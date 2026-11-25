"""The local host adapter on a temporary git repository with a bare remote (git and files only)."""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop import hosts
from dev_loop.hosts import HostError, cli, local, review_run

BRANCH = "loop/add-search"
ENV = {"GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t", "GIT_AUTHOR_EMAIL": "noreply@e.com",
       "GIT_COMMITTER_EMAIL": "noreply@e.com", "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          check=True, env={"PATH": os.environ["PATH"],
                                           "GIT_CONFIG_GLOBAL": str(cwd / ".gc"), **ENV})
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A work tree on the change branch, a bare remote, a change folder, provider local."""
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / ".gc"))
    bare, work = tmp_path / "remote.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    git(tmp_path, "clone", "-q", str(bare), str(work))
    (work / "dev-loop.yaml").write_text(
        f"version: 1\nbranches: {{integration: main, remote: hub, change_prefix: loop/}}\n"
        f"host: {{provider: local}}\n", encoding="utf-8")
    git(work, "remote", "add", "hub", str(bare))
    (work / "README.md").write_text("# app\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "base")
    git(work, "push", "-q", "hub", "HEAD:main")
    git(work, "checkout", "-qb", BRANCH)
    folder = work / ".dev-loop" / "changes" / "add-search"
    folder.mkdir(parents=True)
    (folder / "proposal.md").write_text("# Proposal: add search\n", encoding="utf-8")
    (work / "src.py").write_text("x = 1\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "work")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(work))
    return work


def test_pr_create_pushes_and_records(repo: Path) -> None:
    pr = local.pr_create(BRANCH, "main", "Add search", "the body")
    assert len(pr) == 12
    # the branch reached the bare remote
    assert git(repo.parent / "remote.git", "rev-parse", BRANCH) == git(repo, "rev-parse", "HEAD")
    record = local.records(repo / ".dev-loop" / "changes" / "add-search")[0]
    assert record["kind"] == "pr" and record["branch"] == BRANCH and record["base"] == "main"
    # head is the work's tip; the pr record's own commit sits on top of it
    assert record["head"] == git(repo, "rev-parse", "HEAD~1")


def test_pr_get_reads_the_record_with_the_live_head(repo: Path) -> None:
    pr = local.pr_create(BRANCH, "main", "Add search", "b")
    got = local.pr_get(pr)
    assert got["pullRequestId"] == pr and got["status"] == "active"
    assert got["sourceRefName"] == f"refs/heads/{BRANCH}"
    assert got["targetRefName"] == "refs/heads/main"
    assert got["lastMergeSourceCommit"]["commitId"] == git(repo, "rev-parse", "HEAD")
    # a new commit moves the head pr_get reports, with no new record
    (repo / "src.py").write_text("x = 2\n", encoding="utf-8")
    git(repo, "commit", "-aqm", "more")
    assert local.pr_get(pr)["lastMergeSourceCommit"]["commitId"] == git(repo, "rev-parse", "HEAD")


def test_pr_get_takes_the_id_as_pr_or_id(repo: Path) -> None:
    from dev_loop.hosts import cli

    pr = local.pr_create(BRANCH, "main", "Add search", "b")
    for key in ("pr", "id"):
        assert cli.VERBS["pr-get"](local, {key: pr})["pullRequestId"] == pr


def test_pr_for_branch_finds_the_pr_or_none(repo: Path) -> None:
    assert local.pr_for_branch(BRANCH) is None
    pr = local.pr_create(BRANCH, "main", "t", "b")
    assert local.pr_for_branch(BRANCH) == pr
    assert local.pr_for_branch("loop/other") is None


def test_threads_keep_crid_text_and_statuses(repo: Path) -> None:
    pr = local.pr_create(BRANCH, "main", "t", "b")
    tid = local.thread_post(pr, "/src.py", 1, "**major** — a finding\n\n`crid:ab12cd`")
    assert local.thread_post(pr, None, None, "a pr-level note") == tid + 1
    found = local.threads(pr)
    assert [t["id"] for t in found] == [tid, tid + 1]
    assert found[0]["comments"][0]["content"].endswith("`crid:ab12cd`")
    assert found[0]["threadContext"]["filePath"] == "/src.py"
    assert "threadContext" not in found[1]
    local.thread_reply(pr, tid, "a reply")
    local.thread_edit(pr, tid, "**major** — reworded\n\n`crid:ab12cd`")
    assert local.thread_status(pr, tid, "fixed") == {"id": tid, "status": "fixed"}
    found = local.threads(pr)
    assert found[0]["status"] == "fixed"
    assert [c["content"] for c in found[0]["comments"]] == [
        "**major** — reworded\n\n`crid:ab12cd`", "a reply"]


def test_thread_status_refuses_unknown_and_missing(repo: Path) -> None:
    pr = local.pr_create(BRANCH, "main", "t", "b")
    with pytest.raises(HostError, match="status"):
        local.thread_status(pr, 1, "done")
    with pytest.raises(HostError, match="no thread"):
        local.thread_reply(pr, 99, "x")


def _reviewed(pr: str, head: str) -> None:
    review_run.record(local, {"pr": pr, "head": head, "base": "full", "rc": 0})


def test_set_status_refuses_a_head_that_was_not_reviewed(repo: Path) -> None:
    pr = local.pr_create(BRANCH, "main", "t", "b")
    head = git(repo, "rev-parse", "HEAD")
    _reviewed(pr, "a1b2c3d")
    with pytest.raises(HostError, match="sha is not the reviewed head a1b2c3d"):
        local.set_status(head, "success", "d", pr)
    assert local.set_status("a1b2c3d", "success", "d", pr)["sha"] == "a1b2c3d"


def test_set_status_records_review_clean(repo: Path) -> None:
    pr = local.pr_create(BRANCH, "main", "t", "b")
    _reviewed(pr, "a1b2c3d")
    local.set_status("a1b2c3d", "success", "round 1: 0 blocker, 0 major", pr)
    clean = [r for r in local.records(repo / ".dev-loop" / "changes" / "add-search")
             if r["kind"] == "review-clean"]
    assert clean == [{"kind": "review-clean", "pr": pr, "sha": "a1b2c3d", "state": "success",
                      "description": "round 1: 0 blocker, 0 major"}]
    with pytest.raises(HostError, match="state"):
        local.set_status("a1b2c3d", "pending", "d", pr)
    with pytest.raises(HostError, match="sha"):
        local.set_status("../x", "success", "d", pr)


def test_the_same_status_twice_is_one_record_and_no_commit(repo: Path) -> None:
    pr = local.pr_create(BRANCH, "main", "Add search", "the body")
    sha = git(repo, "rev-parse", "HEAD")
    review_run.record(local, {"pr": pr, "head": sha, "base": "full", "rc": 0})
    first = local.set_status(sha, "failure", "round 1: 0 blockers, 1 majors", pr)
    before = git(repo, "rev-parse", "HEAD")
    again = local.set_status(sha, "failure", "round 1: 0 blockers, 1 majors", pr)
    assert again == first
    assert git(repo, "rev-parse", "HEAD") == before

    def clean() -> list[dict]:
        return [r for r in local.records(local.folder_of(pr)) if r["kind"] == "review-clean"]
    assert len(clean()) == 1
    local.set_status(sha, "success", "round 1: 0 blockers, 0 majors", pr)
    assert len(clean()) == 2


def _subjects(repo: Path, count: int) -> list[str]:
    return git(repo, "log", f"-{count}", "--format=%s").splitlines()[::-1]


def test_each_record_write_is_committed_on_the_branch(repo: Path) -> None:
    folder = ".dev-loop/changes/add-search"
    (repo / "notes.txt").write_text("stray\n", encoding="utf-8")   # untracked, never committed
    (repo / "src.py").write_text("x = 3\n", encoding="utf-8")
    git(repo, "add", "src.py")                                     # staged, never committed
    before = git(repo, "rev-parse", "HEAD")
    pr = local.pr_create(BRANCH, "main", "t", "b")
    tid = local.thread_post(pr, "/src.py", 1, "**major** — x\n\n`crid:ab12cd`")
    local.thread_status(pr, tid, "fixed")
    local.thread_status(pr, tid, "fixed")                          # no change: no commit
    _reviewed(pr, "a1b2c3d")
    local.set_status("a1b2c3d", "success", "round 1", pr)
    assert git(repo, "rev-list", "--count", f"{before}..HEAD") == "5"
    subjects = _subjects(repo, 5)
    assert [s.split()[1] for s in subjects] == ["pr", "review-thread", "review-thread",
                                                 "review-run", "review-clean"]
    changed = git(repo, "diff", "--name-only", before, "HEAD").splitlines()
    assert sorted(changed) == [f"{folder}/host.jsonl", f"{folder}/review-threads.jsonl"]
    assert git(repo, "status", "--porcelain").splitlines() == ["M  src.py", "?? notes.txt"]
    bare = repo.parent / "remote.git"
    assert git(bare, "rev-parse", BRANCH) == git(repo, "rev-parse", "HEAD~4")


def test_a_second_clone_finds_the_pr(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pr = local.pr_create(BRANCH, "main", "t", "b")
    tid = local.thread_post(pr, None, None, "a note")
    git(repo, "push", "-q", "hub", BRANCH)
    other = repo.parent / "other"
    git(repo.parent, "clone", "-q", "-b", BRANCH, str(repo.parent / "remote.git"), str(other))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(other))
    assert local.pr_for_branch(BRANCH) == pr
    assert local.pr_get(pr)["pullRequestId"] == pr
    assert [t["id"] for t in local.threads(pr)] == [tid]


def test_a_pr_recorded_in_a_worktree_is_found_from_the_root(repo: Path) -> None:
    pr = local.pr_create(BRANCH, "main", "t", "b")
    tid = local.thread_post(pr, None, None, "a note")
    git(repo, "checkout", "-q", "main")
    git(repo, "worktree", "add", "-q", str(repo.parent / "wt"), BRANCH)
    assert local.pr_get(pr)["pullRequestId"] == pr
    assert [t["id"] for t in local.threads(pr)] == [tid]


def test_a_torn_line_is_refused(repo: Path) -> None:
    pr = local.pr_create(BRANCH, "main", "t", "b")
    local.thread_post(pr, None, None, "a note")
    path = repo / ".dev-loop" / "changes" / "add-search" / "review-threads.jsonl"
    with path.open("a", encoding="utf-8") as file:
        file.write('{"id": 2, "sta')
    with pytest.raises(HostError, match=r"review-threads\.jsonl:2"):
        local.threads(pr)


def test_a_write_off_the_pr_branch_is_refused(repo: Path) -> None:
    pr = local.pr_create(BRANCH, "main", "t", "b")
    git(repo, "checkout", "-q", "--detach")
    with pytest.raises(HostError, match="not on"):
        local.thread_post(pr, None, None, "a note")
    assert local.threads(pr) == []


def test_unknown_pr_fails(repo: Path) -> None:
    with pytest.raises(HostError, match="no pull request"):
        local.pr_get("deadbeef0000")


def test_the_provider_picks_the_module(repo: Path) -> None:
    assert hosts.adapter() is local


def test_cli_uses_the_local_host(repo: Path, monkeypatch: pytest.MonkeyPatch,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    pr = local.pr_create(BRANCH, "main", "t", "b")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"pr": pr, "file": "/src.py", "line": 1, "text": "**m** — x\n\n`crid:z1`"})))
    assert cli.main(["thread-post", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"id": 1}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"pr": pr})))
    assert cli.main(["threads", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["comments"][0]["content"].endswith("`crid:z1`")


def test_files_are_neutral() -> None:
    plugin = Path(__file__).resolve().parents[1]
    assert check([plugin / "src/dev_loop/hosts/local.py", Path(__file__)]) == []
