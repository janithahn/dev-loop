"""The weekly delivery table: the table on planted and recorded rows, the fetch through the
host adapter on a fake (never the network), the config keys it reads, and its refusals.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
from test_neutral import check
from test_record import METRICS, ROUND, make, put

from dev_loop.hosts import HostError, ado
from dev_loop.records import record, review_metrics, review_records

HERE = Path(__file__).resolve().parent
NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)  # noqa: UP017 - a Tuesday of 2026-W43
EMPTY = "| 2026-W42 | 0 | — | 0 | — | 0 | — | 0 | — | — | — | — |"  # counts 0, the rest unmeasured
TRUNK, MAIN = "refs/heads/trunk", "refs/heads/main"
RECORDED = (TRUNK, MAIN)  # the branch names in the recorded responses
PROJECT = """version: 1
host: {org: acme, project: proj, repo: repo, token_env: DL_TEST_TOKEN}
branches: {integration: trunk, release: main}
metrics: {deploy_pipeline: "7"}
"""


def pull(number: int, opened: str, closed: str, **fields: str) -> dict[str, Any]:
    pr = {"pullRequestId": number, "status": "completed", "title": "x", "creationDate": opened}
    pr |= {"closedDate": closed, "sourceRefName": "refs/heads/feature/x"}
    return pr | {"targetRefName": TRUNK} | fields


def run(result: str, finished: str, version: str = "") -> dict[str, Any]:
    """A run of the deploy pipeline; each planted run its own commit unless `version` names one."""
    built = {"result": result, "finishTime": finished, "sourceVersion": version or finished}
    return built | {"sourceBranch": MAIN}


PRS = [
    pull(1, "2026-10-19T00:00:00.1234567Z", "2026-10-20T00:00:00Z"),  # lead 1 d
    pull(2, "2026-10-17T00:00:00Z", "2026-10-20T00:00:00Z"),  # lead 3 d; opened in W42
    pull(3, "2026-10-19T00:00:00Z", "2026-10-19T01:00:00Z", status="abandoned"),
    pull(4, "2026-10-19T08:00:00Z", "2026-10-19T10:00:00Z", targetRefName=MAIN),
    pull(
        5,
        "2026-10-20T05:00:00Z",
        "2026-10-20T06:00:00Z",
        targetRefName=MAIN,
        title="Hotfix: the deploy",
    ),
    {"pullRequestId": 6, "status": "active", "creationDate": "2026-10-19T00:00:00Z", "title": "x"},
]
RUNS = [
    run("succeeded", "2026-10-19T12:00:00Z"),  # 2 h after PR 4
    run("failed", "2026-10-19T14:00:00Z"),  # restored 19 h later
    run("canceled", "2026-10-19T15:00:00Z"),  # neither a deploy nor a failure
    run("succeeded", "2026-10-20T09:00:00.5Z"),  # 3 h after PR 5
    run("failed", "2026-10-20T10:00:00Z"),  # not restored yet
]


def usage(pr: int, turns: int, tokens: int, unmetered: int) -> dict[str, Any]:
    used = {"turns": turns, "seconds": 1, "tokens_in": tokens, "tokens_out": 100}
    return {"kind": "round", "pr": pr, "unmetered": unmetered} | used


RECORDS = [
    usage(1, 10, 900, 0),  # (900 + 100) // 10
    {"kind": "round", "pr": 1, "legacy": True},  # a legacy round: no usage, never counted
    usage(2, 0, 0, 1),  # a fallback round: nothing metered
    {"kind": "metrics", "pr": 1, "rounds": 2},
    {"kind": "metrics", "pr": 2, "rounds": 4},
    {"kind": "metrics", "pr": 4, "rounds": 3},
    {"kind": "metrics", "pr": 4, "rounds": 5},  # a later copy wins
    {"kind": "metrics", "pr": 999, "rounds": 9},  # a PR outside the window
    {"kind": "models", "pr": 1, "builder": "claude-sonnet-5-5", "reviewer": "claude-opus-5-5"},
    {"kind": "models", "pr": 4, "builder": "claude-sonnet-5-5", "reviewer": "claude-opus-5-5"},
]


def test_planted() -> None:
    """Each column on planted PRs, runs and records; an empty week shows —."""
    lines = review_metrics.table(review_metrics.Ado(PRS, RUNS, TRUNK, MAIN), RECORDS, NOW, 4)
    rounds = "sonnet-5-5/opus-5-5 3.5 (n=2); ?/? 4.0 (n=1)"
    week = f"| 2026-W43 | 2 | 2.0 | 2 | 2.5 | 1 | 25% | 2 | 50% | 19.0 +1 open | 100 | {rounds} |"
    assert lines[2] == week
    w42 = "| 2026-W42 | 0 | — | 0 | — | 0 | — | 0 | — | — | — +1 unmetered | ?/? 4.0 (n=1) |"
    assert lines[3] == w42
    assert lines[4] == EMPTY.replace("W42", "W41")  # no PR of the window opened by then


def test_recorded() -> None:
    """The table on the recorded host responses: header, separator, four weeks, 12 cells."""
    data = json.loads((HERE / "fixtures/ado_metrics.json").read_text(encoding="utf-8"))
    rows = review_metrics.Ado(data["prs"], data["runs"], *RECORDED)
    lines = review_metrics.table(rows, RECORDS, NOW, 4)
    assert len(lines) == 6
    assert [line.count("|") for line in lines] == [13] * 6
    weeks = [line.split(" | ")[0] for line in lines[2:]]
    assert weeks == ["| 2026-W43", "| 2026-W42", "| 2026-W41", "| 2026-W40"]
    assert lines[2].split(" | ")[7:9] == ["3", "0%"]  # three versions shipped; two reruns of them


def test_reruns() -> None:
    """A failed rerun of a deployed version is no failed deploy, a second success no second
    deploy; a retried version restores; a release merge with no deploy after it is named open."""
    runs = [run("succeeded", "2026-10-19T12:00:00Z", "a")]
    runs += [run("failed", "2026-10-19T12:01:00Z", "a")]
    runs += [run("succeeded", "2026-10-19T13:00:00Z", "a")]
    runs += [run("failed", "2026-10-19T14:00:00Z", "b")]
    runs += [run("succeeded", "2026-10-19T16:00:00Z", "b")]  # the retry of b: restored in 2 h
    merges = [
        pull(7, "2026-10-19T09:00:00Z", t, targetRefName=MAIN)
        for t in ("2026-10-19T10:00:00Z", "2026-10-20T11:00:00Z")
    ]  # 2 h to deploy a; nothing after the second
    cells = review_metrics.table(review_metrics.Ado(merges, runs, TRUNK, MAIN), [], NOW, 1)
    cells = cells[2].split(" | ")
    assert cells[3:5] == ["2", "2.0 +1 open"]
    assert cells[7:10] == ["2", "33%", "2.0"]  # a and b deployed; b failed once of three


def test_no_release_branch_deploys_from_integration() -> None:
    """branches.release null: integration merges are the merges a deploy follows."""
    merged = [pull(8, "2026-10-19T09:00:00Z", "2026-10-19T10:00:00Z")]
    shipped = [run("succeeded", "2026-10-19T12:00:00Z")]
    rows = review_metrics.Ado(merged, shipped, TRUNK, TRUNK)
    cells = review_metrics.table(rows, [], NOW, 1)[2].split(" | ")
    assert cells[1:5] == ["1", "0.0", "1", "2.0"]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "dev-loop.yaml").write_text(PROJECT, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


def fake_host(monkeypatch: pytest.MonkeyPatch, answer: dict[str, Any]) -> list[str]:
    asked: list[str] = []

    def request(method: str, path: str, body: object = None) -> dict[str, Any]:
        asked.append(f"{method} {path}")
        return answer

    monkeypatch.setattr(review_metrics.host, "request", request)
    return asked


def test_fetch_through_the_adapter(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The PRs and the deploy pipeline's runs on the release branch, by the config's keys."""
    asked = fake_host(monkeypatch, {"value": PRS})
    got = review_metrics.fetch(NOW, 4)
    assert (got.prs, got.integration, got.release) == (PRS, TRUNK, MAIN)
    assert asked[0].startswith("GET /_apis/git/repositories/repo/pullrequests?")
    assert "searchCriteria.status=all" in asked[0]
    deploys = "GET /_apis/build/builds?definitions=7&branchName=refs%2Fheads%2Fmain"
    assert asked[1].startswith(deploys)


def test_fetch_without_release_uses_integration(project: Path, monkeypatch) -> None:
    text = PROJECT.replace("release: main", "release: null")
    (project / "dev-loop.yaml").write_text(text, encoding="utf-8")
    asked = fake_host(monkeypatch, {"value": []})
    assert review_metrics.fetch(NOW, 4).release == TRUNK
    assert "branchName=refs%2Fheads%2Ftrunk" in asked[1]


def test_unset_keys_refused_before_any_request(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No deploy pipeline or repo: exit 1 naming the keys, nothing asked of the host, no .env."""
    (project / "dev-loop.yaml").write_text("version: 1\n", encoding="utf-8")
    asked = fake_host(monkeypatch, {"value": []})
    assert review_records.metrics(4, project) == 1
    assert "host.repo, metrics.deploy_pipeline not set in dev-loop.yaml" in capsys.readouterr().err
    assert asked == []


def test_host_error_named_never_a_traceback(project: Path, monkeypatch, capsys) -> None:
    def refuse(*_: object) -> dict[str, Any]:
        raise HostError("host answered HTTP 401", 401)

    monkeypatch.setattr(review_metrics.host, "request", refuse)
    assert review_records.metrics(4, project) == 1
    assert "HTTP 401" in capsys.readouterr().err


def test_full_page_refused(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A response that fills the request cap is refused, never cut."""
    fake_host(monkeypatch, {"value": [{}] * review_metrics.TOP})
    with pytest.raises(ValueError, match="the request cap"):
        review_metrics.fetch(NOW, 4)


def test_sign_in_page_refused_token_never_redirected(project: Path, monkeypatch) -> None:
    """A non-JSON answer (a sign-in page) is refused, never parsed; the token never rides a
    redirect (the adapter sends it as an unredirected header)."""
    sent: list[urllib.request.Request] = []

    class Page:
        headers = Message()

        def __enter__(self) -> Page:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self, *_: object) -> bytes:
            return b"<html>sign in</html>"

    def answer(request: urllib.request.Request, **_: object) -> Page:
        sent.append(request)
        return Page()

    monkeypatch.setenv("DL_TEST_TOKEN", "from-env")
    monkeypatch.setattr(ado.subprocess, "run", lambda *_, **__: (_ for _ in ()).throw(OSError))
    monkeypatch.setattr(ado.urllib.request, "urlopen", answer)
    with pytest.raises(ValueError, match="host call failed"):
        review_metrics.fetch(NOW, 4)
    assert "Authorization" in sent[0].unredirected_hdrs
    assert "Authorization" not in sent[0].headers  # urllib copies these onto a redirect


def test_models_record(root: Path) -> None:
    """The models record goes through the writer; a missing field is refused."""
    folder = make(root, "x")
    models = {"pr": "7", "builder": "claude-opus-5-5", "reviewer": "claude-opus-5-5"}
    put(root, "x", "models", models)
    line = (folder / "status.md").read_text(encoding="utf-8").strip()
    assert line == "Models: PR 7 built on claude-opus-5-5, reviewed on claude-opus-5-5"
    with pytest.raises(ValueError, match="missing reviewer"):
        put(root, "x", "models", {"pr": "7", "builder": "claude-opus-5-5"})
    assert record.unparsed(root) == []
    put(root, "x", "round", ROUND)
    put(root, "x", "metrics", METRICS)
    assert record.missing(folder)  # the models record is not (yet) part of the done rule


def test_files_are_neutral() -> None:
    assert check([Path(review_metrics.__file__), Path(__file__)]) == []
