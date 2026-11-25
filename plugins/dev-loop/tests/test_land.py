"""`dl land` against a stub Azure DevOps server: gate reuse, retry, refusals, lock, org paths."""

from __future__ import annotations

import fcntl
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from test_neutral import check

from dev_loop import land

HEAD = "a" * 40
PR = "/acme/proj/_apis/git/repositories/repo/pullrequests/7"
CRID = {"id": 9, "status": "active", "comments": [{"content": "major\n\n`crid:abc`"}]}
HUMAN = {"id": 8, "status": "active", "comments": [{"content": "a person's question"}]}


class Ado:
    """One PR (7), one Build policy, builds that finish as `results` says."""

    def __init__(self, results: list[str], target: str = "main") -> None:
        self.results = results
        self.calls: list[tuple[str, str, Any]] = []
        self.pr: dict[str, Any] = {"status": "active", "targetRefName": f"refs/heads/{target}",
                                   "mergeStatus": "succeeded", "title": "t",
                                   "lastMergeSourceCommit": {"commitId": HEAD}}
        self.running: list[dict[str, Any]] = []
        self.builds: dict[int, str] = {}
        self.current, self.next_build = 99, 100
        self.threads: list[dict[str, Any]] = [HUMAN]

    def evaluation(self, method: str) -> dict[str, Any]:
        if method == "PATCH":
            self.current = self.next_build
            self.builds[self.current] = self.results.pop(0)
            self.next_build += 1
        return {"status": "approved", "context": {"buildId": self.current}}

    def answer(self, method: str, path: str, body: Any) -> Any:  # noqa: ANN401, PLR0911
        self.calls.append((method, path, body))
        if path.startswith("/acme/_apis/connectionData"):
            return {"authenticatedUser": {"id": "me"}}
        if path.startswith("/acme/_apis/projects/proj?"):  # projects live at the org level
            return {"id": "pid"}
        if path.startswith("/acme/proj/_apis/policy/evaluations?"):
            assert "CodeReviewId/pid/7" in path, path
            build = {"type": {"displayName": "Build"},
                     "settings": {"displayName": "gates", "buildDefinitionId": 4}}
            other = {"type": {"displayName": "Comment requirements"}, "settings": {}}
            return {"value": [{"evaluationId": "e1", "configuration": build},
                              {"evaluationId": "e2", "configuration": other}]}
        if path.startswith("/acme/proj/_apis/policy/evaluations/e1"):
            return self.evaluation(method)
        if path.startswith("/acme/proj/_apis/build/builds?"):
            assert "definitions=4" in path and "refs/pull/7/merge" in path, path
            return {"value": self.running}
        if match := re.match(r"/acme/proj/_apis/build/builds/(\d+)\?", path):
            return {"status": "completed", "result": self.builds[int(match[1])]}
        if path.startswith(f"{PR}/reviewers/me"):
            return {}
        if path.startswith(f"{PR}/threads"):
            return {"value": self.threads}
        if path.startswith(f"{PR}?"):
            if method == "PATCH":
                self.pr["status"] = "completed"
            return self.pr
        return None

    def sent(self, method: str, fragment: str) -> list[Any]:
        return [body for verb, path, body in self.calls if verb == method and fragment in path]


STATE: dict[str, Ado] = {}


class Stub(BaseHTTPRequestHandler):
    def _go(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or "null")
        found = STATE["ado"].answer(self.command, self.path, body)
        payload = json.dumps(found).encode()
        self.send_response(404 if found is None else 200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PATCH = do_PUT = _go

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = tmp_path / "proj"
    root.mkdir()
    (root / "dev-loop.yaml").write_text(
        "version: 1\nbranches: {integration: main, release: prod}\ngates: {policy: gates}\n"
        "host: {org: acme, project: proj, repo: repo, token_env: DL_TEST_TOKEN}\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("DEV_LOOP_HOST_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("DL_TEST_TOKEN", "t")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(land, "POLL", 0)
    yield tmp_path
    server.shutdown()


def run(ado: Ado, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    STATE["ado"] = ado
    code = land.main(list(argv))
    return code, capsys.readouterr().out.strip()


def test_reuses_a_running_build_never_requeues(home, capsys) -> None:
    ado = Ado([])
    ado.builds[55], ado.current = "succeeded", 55
    ado.running = [{"id": 54, "triggerInfo": {"pr.sourceSha": "b" * 40}},
                   {"id": 55, "triggerInfo": {"pr.sourceSha": HEAD}}]
    code, said = run(ado, capsys, "7")
    assert (code, said) == (0, "PR 7: build 55 green; approved; merged")
    assert not ado.sent("PATCH", "policy/evaluations")
    assert ado.sent("PUT", "/reviewers/me") == [{"vote": 10}]
    options = ado.sent("PATCH", f"{PR}?")[0]
    assert options["autoCompleteSetBy"] == {"id": "me"}
    assert options["completionOptions"]["mergeStrategy"] == "noFastForward"
    assert options["completionOptions"]["deleteSourceBranch"] is True


def test_retries_after_a_cancel(home, capsys) -> None:
    ado = Ado(["canceled", "succeeded"])
    code, said = run(ado, capsys, "7", "--keep-source")
    assert (code, said) == (0, "PR 7: build 101 green; approved; merged")
    assert len(ado.sent("PATCH", "policy/evaluations/e1")) == 2
    assert ado.sent("PATCH", f"{PR}?")[0]["completionOptions"]["deleteSourceBranch"] is False


def test_gives_up_after_three_retries(home, capsys) -> None:
    code, said = run(Ado(["canceled"] * 4), capsys, "7")
    assert code == 1
    assert said.startswith("PR 7: gate not approved in 4 runs"), said


@pytest.mark.parametrize(("target", "why"), [
    ("feature-x", "active, targets feature-x: lands active PRs into main only"),
    ("prod", "targets the release branch prod: never landed")])
def test_wrong_target_refused(home, capsys, target, why) -> None:
    ado = Ado(["succeeded"], target=target)
    code, said = run(ado, capsys, "7")
    assert (code, said) == (1, f"PR 7: {why} - stopped")
    assert not ado.sent("PATCH", "") and not ado.sent("PUT", "")


def test_release_refused_even_when_it_is_the_integration(home, capsys) -> None:
    (home / "proj" / "dev-loop.yaml").write_text(
        "version: 1\nbranches: {integration: main, release: main}\n"
        "host: {org: acme, project: proj, repo: repo, token_env: DL_TEST_TOKEN}\n")
    ado = Ado(["succeeded"])
    code, said = run(ado, capsys, "7")
    assert code == 1 and "release" in said, said
    assert not ado.sent("PUT", "")


def test_active_crid_thread_refused(home, capsys) -> None:
    ado = Ado(["succeeded"])
    ado.threads = [HUMAN, CRID, {**CRID, "id": 10, "status": "fixed"}]
    code, said = run(ado, capsys, "7")
    assert (code, said) == (1, "PR 7: review threads still active: [9] - stopped")
    assert not ado.sent("PUT", "") and not ado.sent("PATCH", "")


def test_lock_held_refuses_a_second_lander(home, capsys) -> None:
    path = home / ".dev-loop" / "land.lock"
    path.parent.mkdir()
    with path.open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ado = Ado(["succeeded"])
        code, said = run(ado, capsys, "7")
    assert code == 1
    assert said == f"another lander holds {path} - stopped", said
    assert ado.calls == []


def test_projects_api_is_called_at_org_level(home, capsys) -> None:
    ado = Ado(["succeeded"])
    assert run(ado, capsys, "7")[0] == 0
    projects = [path for _, path, _ in ado.calls if "/_apis/projects/" in path]
    assert projects and all(path.startswith("/acme/_apis/projects/proj?") for path in projects)


def test_github_host_is_not_run(home, capsys) -> None:
    (home / "proj" / "dev-loop.yaml").write_text("version: 1\nhost: {provider: github}\n")
    ado = Ado([])
    code, said = run(ado, capsys, "7")
    assert (code, said) == (2, "not run: dl land supports ado in this release")
    assert ado.calls == []


def test_usage_without_a_pr(home, capsys) -> None:
    assert land.main(["--keep-source"]) == 2


def test_files_are_neutral() -> None:
    plugin = Path(__file__).resolve().parents[1]
    files = [plugin / "src/dev_loop/land.py", plugin / "skills/land/SKILL.md", Path(__file__)]
    assert check(files) == []



def test_release_main_refused_when_integration_differs(home, capsys) -> None:
    (home / "proj" / "dev-loop.yaml").write_text(
        "version: 1\nbranches: {integration: trunk, release: main}\n"
        "host: {org: acme, project: proj, repo: repo, token_env: DL_TEST_TOKEN}\n")
    ado = Ado(["succeeded"], target="main")
    code, said = run(ado, capsys, "7")
    assert (code, said) == (1, "PR 7: targets the release branch main: never landed - stopped")
    assert not ado.sent("PUT", "")
