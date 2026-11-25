"""The ADO host adapter: credential, request, PR calls; a planted token appears nowhere."""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop.hosts import HostError, ado, cli

TOKEN = "PLANTED-SECRET-9f3a"  # secret-scan: ignore (a planted test value)
SEEN: list[dict] = []
FAILS = {"left": 0}
CRID_TEXT = "**major** \u2014 a finding\n\n`crid:ab12cd`"
THREAD_REPLY = {
    "GET": json.dumps({"value": [{"id": 5, "status": "active",
                                  "comments": [{"id": 1, "content": CRID_TEXT}]}]}),
    "POST": '{"id": 9}',
    "PATCH": '{"id": 5, "status": "fixed"}',
}

HEAD = "c0ffee1234"
ITERATIONS = json.dumps({"value": [
    {"id": 1, "sourceRefCommit": {"commitId": "aaaa111"}},
    {"id": 2, "sourceRefCommit": {"commitId": HEAD}}]})


class Stub(BaseHTTPRequestHandler):
    def _go(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        SEEN.append({"m": self.command, "p": self.path, "b": body,
                     "a": self.headers.get("Authorization", "")})
        if "boom" in self.path:
            self.send_response(403)
            payload = b'{"message":"echo ' + TOKEN.encode() + b'"}'
        elif FAILS["left"] > 0:
            FAILS["left"] -= 1
            self.send_response(503)
            payload = b"{}"
        elif "/threads" in self.path:
            self.send_response(200)
            payload = THREAD_REPLY[self.command].encode()
        elif "/iterations" in self.path:
            self.send_response(200)
            payload = ITERATIONS.encode()
        elif "/statuses" in self.path:
            self.send_response(200)
            payload = b'{"id": 3}'
        elif "searchCriteria" in self.path:
            self.send_response(200)
            payload = b'{"value":[{"pullRequestId":7}]}'
        else:
            self.send_response(200)
            payload = b'{"pullRequestId":42}'
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PATCH = _go

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    SEEN.clear()
    FAILS["left"] = 0
    root = tmp_path / "proj"
    root.mkdir()
    (root / "dev-loop.yaml").write_text(
        "version: 1\nhost: {org: acme, project: proj, repo: repo, token_env: DL_TEST_TOKEN}\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("DEV_LOOP_HOST_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("DL_TEST_TOKEN", raising=False)
    yield tmp_path
    server.shutdown()


def _helper(tmp: Path, monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    config = tmp / "gitconfig"
    config.write_text(f'[credential]\n\thelper = "!f() {{ echo username=u; echo password={token}; }}; f"\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))


def _no_helper(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp / "gitconfig"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))


def test_credential_from_git(host, monkeypatch) -> None:
    _helper(host, monkeypatch, TOKEN)
    assert ado.credential() == TOKEN


def test_credential_falls_back_to_env(host, monkeypatch) -> None:
    _no_helper(host, monkeypatch)
    monkeypatch.setenv("DL_TEST_TOKEN", "from-env")
    assert ado.credential() == "from-env"


def test_missing_credential_fails_loud(host, monkeypatch) -> None:
    _no_helper(host, monkeypatch)
    with pytest.raises(HostError, match="credential"):
        ado.credential()


def test_pr_create_get_and_for_branch(host, monkeypatch) -> None:
    _helper(host, monkeypatch, TOKEN)
    assert ado.pr_create("lp/x", "main", "T", "B") == 42
    sent = json.loads(SEEN[-1]["b"])
    assert sent["sourceRefName"] == "refs/heads/lp/x"
    assert sent["targetRefName"] == "refs/heads/main"
    assert "/acme/proj/_apis/git/repositories/repo/pullrequests" in SEEN[-1]["p"]
    assert ado.pr_get(42)["pullRequestId"] == 42
    assert ado.pr_for_branch("lp/x") == 7
    assert TOKEN not in SEEN[-1]["p"]


def test_retry_once_on_5xx(host, monkeypatch) -> None:
    _helper(host, monkeypatch, TOKEN)
    FAILS["left"] = 1
    assert ado.pr_get(1)["pullRequestId"] == 42
    FAILS["left"] = 2
    with pytest.raises(HostError) as err:
        ado.pr_get(1)
    assert err.value.status == 503


def test_error_carries_status_never_body_or_token(host, monkeypatch) -> None:
    _helper(host, monkeypatch, TOKEN)
    with pytest.raises(HostError) as err:
        ado.request("GET", "/boom")
    assert err.value.status == 403
    assert TOKEN not in str(err.value) + repr(err.value) + repr(err.value.__cause__)


def test_cli_reads_stdin_and_prints_no_token(host, monkeypatch, capsys) -> None:
    _helper(host, monkeypatch, TOKEN)
    body = {"source": "lp/x", "target": "main", "title": "T", "body": "B"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(body)))
    assert cli.main(["pr-create", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"id": 42}
    monkeypatch.setattr("sys.stdin", io.StringIO('{"id": 42}'))
    assert cli.main(["pr-get", "--json"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO('{"branch": "lp/x"}'))
    assert cli.main(["pr-for-branch", "--json"]) == 0
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert json.loads(out.splitlines()[-1]) == {"id": 7}


def test_cli_failure_prints_status_only(host, monkeypatch, capsys) -> None:
    _no_helper(host, monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"id": 1}'))
    assert cli.main(["pr-get", "--json"]) == 1
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
    assert "credential" in out.err


def test_files_are_neutral() -> None:
    root = Path(__file__).resolve().parents[1] / "src/dev_loop/hosts"
    assert check(sorted(root.glob("*.py")) + [Path(__file__)]) == []


def test_credential_lookup_never_opens_a_dialog(host, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs["env"])
        raise OSError

    monkeypatch.setenv("DL_TEST_TOKEN", "from-env")
    monkeypatch.setattr(ado.subprocess, "run", fake_run)
    ado.credential()
    assert seen["GCM_INTERACTIVE"] == "never"
    assert seen["GIT_TERMINAL_PROMPT"] == "0"


def test_threads_list(host, monkeypatch) -> None:
    _helper(host, monkeypatch, TOKEN)
    found = ado.threads(42)
    assert found[0]["id"] == 5
    assert found[0]["comments"][0]["content"] == CRID_TEXT
    assert SEEN[-1]["m"] == "GET"
    assert "/repositories/repo/pullrequests/42/threads?" in SEEN[-1]["p"]


def test_thread_post_keeps_crid_text_and_anchors_line(host, monkeypatch) -> None:
    _helper(host, monkeypatch, TOKEN)
    assert ado.thread_post(42, "src/a.py", 17, CRID_TEXT) == 9
    sent = json.loads(SEEN[-1]["b"])
    assert SEEN[-1]["m"] == "POST"
    assert sent["comments"][0]["content"] == CRID_TEXT
    assert sent["status"] == "active"
    context = sent["threadContext"]
    assert context["filePath"] == "/src/a.py"
    assert context["rightFileStart"] == {"line": 17, "offset": 1}
    assert context["rightFileEnd"] == {"line": 17, "offset": 1}


def test_thread_post_without_file_is_pr_level(host, monkeypatch) -> None:
    _helper(host, monkeypatch, TOKEN)
    ado.thread_post(42, None, None, CRID_TEXT)
    assert "threadContext" not in json.loads(SEEN[-1]["b"])


def test_thread_reply(host, monkeypatch) -> None:
    _helper(host, monkeypatch, TOKEN)
    ado.thread_reply(42, 5, "Deferred.")
    assert SEEN[-1]["m"] == "POST"
    assert "/pullrequests/42/threads/5/comments?" in SEEN[-1]["p"]
    assert json.loads(SEEN[-1]["b"])["content"] == "Deferred."


def test_thread_status(host, monkeypatch) -> None:
    _helper(host, monkeypatch, TOKEN)
    ado.thread_status(42, 5, "fixed")
    assert SEEN[-1]["m"] == "PATCH"
    assert "/pullrequests/42/threads/5?" in SEEN[-1]["p"]
    assert json.loads(SEEN[-1]["b"]) == {"status": "fixed"}


def test_thread_status_refuses_unknown(host, monkeypatch) -> None:
    _helper(host, monkeypatch, TOKEN)
    with pytest.raises(HostError, match="status"):
        ado.thread_status(42, 5, "done")
    assert SEEN == []


def test_cli_thread_verbs(host, monkeypatch, capsys) -> None:
    _helper(host, monkeypatch, TOKEN)
    calls = [
        ("threads", {"pr": 42}),
        ("thread-post", {"pr": 42, "file": "src/a.py", "line": 3, "text": CRID_TEXT}),
        ("thread-reply", {"pr": 42, "thread": 5, "text": "ok"}),
        ("thread-status", {"pr": 42, "thread": 5, "status": "closed"}),
    ]
    for verb, body in calls:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(body)))
        assert cli.main([verb, "--json"]) == 0, verb
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[0])[0]["comments"][0]["content"] == CRID_TEXT
    assert json.loads(lines[1]) == {"id": 9}
    assert TOKEN not in "".join(lines)


def test_thread_edit_rewrites_the_root_comment(host, monkeypatch) -> None:
    _helper(host, monkeypatch, TOKEN)
    ado.thread_edit(42, 5, CRID_TEXT)
    assert SEEN[-1]["m"] == "PATCH"
    assert "/pullrequests/42/threads/5/comments/1?" in SEEN[-1]["p"]
    assert json.loads(SEEN[-1]["b"]) == {"content": CRID_TEXT}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"pr": 42, "thread": 5, "text": "x"})))
    assert cli.main(["thread-edit", "--json"]) == 0


def _status_body() -> dict:
    posted = [call for call in SEEN if "/statuses" in call["p"]]
    assert len(posted) == 1
    assert posted[0]["m"] == "POST"
    assert "/pullrequests/42/statuses?" in posted[0]["p"]
    return json.loads(posted[0]["b"])


@pytest.mark.parametrize(("state", "posted"), [("success", "succeeded"), ("failure", "failed")])
def test_set_status_posts_review_clean_on_the_last_iteration(host, monkeypatch, state, posted):
    _helper(host, monkeypatch, TOKEN)
    ado.set_status(HEAD, state, "round 2: 0 blockers, 0 majors", 42)
    assert any("/pullrequests/42/iterations?" in call["p"] for call in SEEN)
    assert _status_body() == {
        "state": posted, "description": "round 2: 0 blockers, 0 majors",
        "context": {"genre": "dev-loop", "name": "review-clean"}, "iterationId": 2}


def test_set_status_refuses_a_head_that_moved(host, monkeypatch) -> None:
    _helper(host, monkeypatch, TOKEN)
    with pytest.raises(HostError, match="head"):
        ado.set_status("aaaa111", "success", "round 1: clean", 42)
    assert not [call for call in SEEN if "/statuses" in call["p"]]


def test_set_status_refuses_bad_input_before_any_call(host, monkeypatch) -> None:
    _helper(host, monkeypatch, TOKEN)
    with pytest.raises(HostError, match="state"):
        ado.set_status(HEAD, "succeeded", "d", 42)
    with pytest.raises(HostError, match="sha"):
        ado.set_status("../x", "success", "d", 42)
    with pytest.raises(HostError, match="pr"):
        ado.set_status(HEAD, "success", "d", None)
    assert SEEN == []


def test_cli_set_status_on_ado_prints_no_token(host, monkeypatch, capsys) -> None:
    _helper(host, monkeypatch, TOKEN)
    body = {"pr": 42, "sha": HEAD, "state": "failure", "description": "round 1: 1 major"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(body)))
    assert cli.main(["set-status", "--json"]) == 0
    out = capsys.readouterr()
    assert _status_body()["state"] == "failed"
    assert TOKEN not in out.out + out.err
    assert all(TOKEN not in call["p"] + call["b"] for call in SEEN)
