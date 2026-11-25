"""The GitHub host adapter through a fake `gh` on PATH that logs argv and stdin."""

from __future__ import annotations

import io
import json
import stat
import sys
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop import hosts
from dev_loop.hosts import HostError, ado, cli, github

TOKEN = "PLANTED-SECRET-77c1"  # secret-scan: ignore (a planted test value)
CRID_TEXT = "**major** — a finding\n\n`crid:ab12cd`"
GH = """#!{python}
import json, os, sys
stdin = sys.stdin.read() if "--input" in sys.argv else ""
folder = os.environ["FAKE_GH"]
with open(os.path.join(folder, "log.jsonl"), "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "stdin": stdin}}) + "\\n")
said = " ".join(sys.argv[1:]) + " " + stdin
for needle, out, code, err in json.load(open(os.path.join(folder, "replies.json"))):
    if needle in said:
        sys.stdout.write(out)
        sys.stderr.write(err)
        sys.exit(code)
sys.stdout.write("{{}}")
"""
REVIEW_THREADS = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
    {"id": "RT_node5", "isResolved": False, "path": "src/a.py", "line": 3,
     "comments": {"nodes": [{"databaseId": 5, "body": CRID_TEXT},
                            {"databaseId": 6, "body": "ok"}]}},
    {"id": "RT_node8", "isResolved": True, "path": "src/b.py", "line": None,
     "comments": {"nodes": [{"databaseId": 8, "body": "done"}]}},
]}}}}}


def _replies(folder: Path, rules: list[list[object]]) -> None:
    (folder / "replies.json").write_text(json.dumps(rules), encoding="utf-8")


def _log(folder: Path) -> list[dict]:
    lines = (folder / "log.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


@pytest.fixture
def gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "gh"
    folder.mkdir()
    script = folder / "gh"
    script.write_text(GH.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "dev-loop.yaml").write_text(
        "version: 1\nhost: {provider: github, org: octo, repo: widgets}\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("FAKE_GH", str(folder))
    monkeypatch.setenv("PATH", str(folder))
    _replies(folder, [
        ["graphql --input - {\"query\": \"query", json.dumps(REVIEW_THREADS), 0, ""],
        ["graphql", '{"data": {}}', 0, ""],
        ["issues/42/comments?per_page=100 --method GET",
         json.dumps([{"id": 11, "body": "PR-level note"}]), 0, ""],
        ["issues/42/comments --method POST", '{"id": 12}', 0, ""],
        ["head=octo:dev-loop/c1", '[{"number": 7}]', 0, ""],
        ["head=octo:", "[]", 0, ""],
        ["pulls/42 --method GET", json.dumps({
            "number": 42, "title": "t", "body": "b", "state": "open", "merge_commit_sha": "m1",
            "head": {"ref": "dev-loop/c1", "sha": "h1"}, "base": {"ref": "trunk"}}), 0, ""],
        ["pulls --method POST", '{"number": 42}', 0, ""],
        ["pulls/42/comments --method POST", '{"id": 5}', 0, ""],
        ["replies", '{"id": 6, "body": "ok"}', 0, ""],
    ])
    return folder


def test_pr_create_sends_head_and_base(gh: Path) -> None:
    assert github.pr_create("dev-loop/c1", "trunk", "Title", "Body") == 42
    call = _log(gh)[-1]
    assert call["argv"][:4] == ["api", "repos/octo/widgets/pulls", "--method", "POST"]
    assert json.loads(call["stdin"]) == {
        "head": "dev-loop/c1", "base": "trunk", "title": "Title", "body": "Body"}


def test_pr_get_carries_the_fields_the_loop_reads(gh: Path) -> None:
    found = github.pr_get(42)
    assert found["pullRequestId"] == 42
    assert found["sourceRefName"] == "refs/heads/dev-loop/c1"
    assert found["targetRefName"] == "refs/heads/trunk"
    assert found["lastMergeSourceCommit"] == {"commitId": "h1"}
    assert found["lastMergeCommit"] == {"commitId": "m1"}
    assert found["description"] == "b"


def test_pr_for_branch_finds_the_open_pr_or_none(gh: Path) -> None:
    assert github.pr_for_branch("dev-loop/c1") == 7
    assert "state=open" in _log(gh)[-1]["argv"][1]
    assert github.pr_for_branch("dev-loop/none") is None


def test_threads_merge_review_threads_and_pr_comments(gh: Path) -> None:
    found = github.threads(42)
    assert [item["id"] for item in found] == [5, 8, 11]
    assert found[0]["status"] == "active"
    assert found[0]["comments"][0]["content"] == CRID_TEXT
    assert [note["id"] for note in found[0]["comments"]] == [5, 6]
    assert found[0]["threadContext"]["filePath"] == "/src/a.py"
    assert found[1]["status"] == "fixed"
    assert "threadContext" not in found[2]
    query = json.loads(_log(gh)[0]["stdin"])
    assert query["variables"] == {"owner": "octo", "name": "widgets", "number": 42}


def test_thread_post_on_a_file_is_a_review_comment(gh: Path) -> None:
    assert github.thread_post(42, "/src/a.py", 3, CRID_TEXT) == 5
    sent = json.loads(_log(gh)[-1]["stdin"])
    assert sent == {"body": CRID_TEXT, "commit_id": "h1", "path": "src/a.py",
                    "line": 3, "side": "RIGHT"}


def test_thread_post_without_a_file_is_a_pr_comment(gh: Path) -> None:
    assert github.thread_post(42, None, None, CRID_TEXT) == 12
    call = _log(gh)[-1]
    assert call["argv"][1] == "repos/octo/widgets/issues/42/comments"
    assert json.loads(call["stdin"]) == {"body": CRID_TEXT}


def test_thread_reply_answers_the_review_thread(gh: Path) -> None:
    assert github.thread_reply(42, 5, "ok") == {"id": 6, "body": "ok"}
    assert _log(gh)[-1]["argv"][1] == "repos/octo/widgets/pulls/42/comments/5/replies"


def test_thread_reply_on_a_pr_comment_posts_a_pr_comment(gh: Path) -> None:
    _replies(gh, [["replies", "", 1, "gh: Not Found (HTTP 404)"],
                  ["issues/42/comments", '{"id": 13}', 0, ""]])
    assert github.thread_reply(42, 11, "ok") == {"id": 13}
    assert json.loads(_log(gh)[-1]["stdin"]) == {"body": "ok"}


def test_thread_edit_falls_back_to_the_pr_comment(gh: Path) -> None:
    _replies(gh, [["pulls/comments/11", "", 1, "gh: Not Found (HTTP 404)"],
                  ["issues/comments/11", '{"id": 11}', 0, ""]])
    assert github.thread_edit(42, 11, CRID_TEXT) == {"id": 11}
    assert _log(gh)[-1]["argv"][1:4] == ["repos/octo/widgets/issues/comments/11",
                                         "--method", "PATCH"]


@pytest.mark.parametrize(("status", "mutation"), [
    ("active", "unresolveReviewThread"), ("fixed", "resolveReviewThread"),
    ("closed", "resolveReviewThread"), ("wontFix", "resolveReviewThread")])
def test_thread_status_resolves_through_graphql(gh: Path, status: str, mutation: str) -> None:
    assert github.thread_status(42, 5, status) == {"id": 5, "status": status}
    sent = json.loads(_log(gh)[-1]["stdin"])
    assert sent["query"].startswith(f"mutation($id: ID!) {{ {mutation}(")
    assert sent["variables"] == {"id": "RT_node5"}


def test_thread_status_refuses_unknown_status_and_pr_comments(gh: Path) -> None:
    with pytest.raises(HostError, match="status"):
        github.thread_status(42, 5, "done")
    with pytest.raises(HostError, match="no review thread"):
        github.thread_status(42, 11, "closed")


def test_gh_missing_fails_in_one_line(gh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(gh.parent / "empty"))
    with pytest.raises(HostError, match=r"^gh not found$"):
        github.pr_get(42)


def test_gh_logged_out_fails_without_leaking(gh: Path) -> None:
    _replies(gh, [["pulls", "", 4, f"To get started run gh auth login {TOKEN}"]])
    with pytest.raises(HostError, match=r"^gh is not logged in$"):
        github.pr_get(42)
    _replies(gh, [["pulls", "", 1, f"gh: Forbidden {TOKEN} (HTTP 403)"]])
    with pytest.raises(HostError) as caught:
        github.pr_get(42)
    assert caught.value.status == 403
    assert TOKEN not in str(caught.value)


def test_the_provider_picks_the_module(gh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert hosts.adapter() is github
    root = gh.parent / "proj"
    (root / "dev-loop.yaml").write_text("version: 1\nhost: {provider: ado}\n", encoding="utf-8")
    assert hosts.adapter() is ado
    (root / "dev-loop.yaml").write_text("version: 1\nhost: {provider: gitlab}\n", encoding="utf-8")
    with pytest.raises(HostError, match="host.provider"):
        hosts.adapter()


def test_cli_uses_the_github_host(gh: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"pr": 42})))
    assert cli.main(["threads", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["comments"][0]["content"] == CRID_TEXT


@pytest.mark.parametrize("state", ["success", "failure"])
def test_set_status_posts_the_review_clean_context(gh: Path, state: str) -> None:
    github.set_status("a1b2c3d", state, "round 2: 0 blockers, 1 major " + "x" * 200)
    call = _log(gh)[-1]
    assert call["argv"][1:4] == ["repos/octo/widgets/statuses/a1b2c3d", "--method", "POST"]
    sent = json.loads(call["stdin"])
    assert sent["state"] == state
    assert sent["context"] == "dev-loop/review-clean"
    assert sent["description"].startswith("round 2: 0 blockers, 1 major")
    assert len(sent["description"]) <= 140


def test_set_status_refuses_a_bad_state_or_sha(gh: Path) -> None:
    with pytest.raises(HostError, match="state"):
        github.set_status("a1b2c3d", "pending", "d")
    with pytest.raises(HostError, match="sha"):
        github.set_status("../pulls", "success", "d")
    assert not (gh / "log.jsonl").exists()


def test_cli_set_status(gh: Path, monkeypatch, capsys) -> None:
    body = {"pr": 42, "sha": "a1b2c3d", "state": "success", "description": "clean"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(body)))
    assert cli.main(["set-status", "--json"]) == 0
    assert _log(gh)[-1]["argv"][1] == "repos/octo/widgets/statuses/a1b2c3d"

def test_this_file_is_neutral() -> None:
    assert check([Path(__file__)]) == []
