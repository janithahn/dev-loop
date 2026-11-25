"""`dl land` with the local host: gates, the done rule, rebase, fast-forward, on a bare remote."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop import land
from dev_loop.hosts import local, review_run
from dev_loop.records import record
from test_neutral_guards import GREEN

BRANCH = "loop/add-search"
ENV = {"GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t", "GIT_AUTHOR_EMAIL": "noreply@e.com",
       "GIT_COMMITTER_EMAIL": "noreply@e.com", "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd: Path, gc: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          check=True, env={"PATH": os.environ["PATH"],
                                           "GIT_CONFIG_GLOBAL": str(gc), **ENV})
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A work tree on the change branch with a done, clean PR and a passing gate."""
    gc = tmp_path / ".gc"
    for key, value in {**ENV, "GIT_CONFIG_GLOBAL": str(gc)}.items():
        monkeypatch.setenv(key, value)
    bare, work = tmp_path / "remote.git", tmp_path / "work"
    git(tmp_path, gc, "init", "-q", "--bare", "-b", "main", str(bare))
    git(tmp_path, gc, "clone", "-q", str(bare), str(work))
    (work / "dev-loop.yaml").write_text(
        "version: 1\nbranches: {integration: main, remote: hub, change_prefix: loop/}\n"
        "host: {provider: local}\nverify: {steps: {gate: 'true'}}\n", encoding="utf-8")
    git(work, gc, "remote", "add", "hub", str(bare))
    (work / "README.md").write_text("# app\n", encoding="utf-8")
    git(work, gc, "add", "-A")
    git(work, gc, "commit", "-qm", "base")
    git(work, gc, "push", "-q", "hub", "HEAD:main")
    git(work, gc, "checkout", "-qb", BRANCH)
    folder = work / ".dev-loop" / "changes" / "add-search"
    folder.mkdir(parents=True)
    (folder / "proposal.md").write_text("# Proposal\n", encoding="utf-8")
    (work / "src.py").write_text("x = 1\n", encoding="utf-8")
    git(work, gc, "add", "-A")
    git(work, gc, "commit", "-qm", "work")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(work))
    monkeypatch.setenv("HOME", str(tmp_path))  # the one-per-machine lock lives under ~/.dev-loop
    return work


def done_pr(work: Path, *, records: list[dict[str, object]] = GREEN, ran: bool = True) -> str:
    """Create a pushed PR and record the clean round and the done rule's records."""
    folder = work / ".dev-loop" / "changes" / "add-search"
    (folder / "tasks.md").write_text("- [x] T1\n", encoding="utf-8")
    (folder / "verify-report.md").write_text("# verify\n", encoding="utf-8")
    for item in records:
        record.write(work, "add-search", dict(item))
    git(work, work.parent / ".gc", "add", "-A")
    git(work, work.parent / ".gc", "commit", "-qm", "records")
    pr = local.pr_create(BRANCH, "main", "Add search", "body")
    sha = git(work, work.parent / ".gc", "rev-parse", "HEAD")
    if ran:  # what pr-review.sh writes on exit: the proof a real round ran on this head
        review_run.record(local, {"pr": pr, "head": sha, "base": "full", "rc": 0})
    else:
        review_run.record(local, {"pr": pr, "head": sha, "base": "full", "rc": 1})
    local.set_status(sha, "success", "round 1: 0 blocker, 0 major", pr)
    return pr


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = land.main(argv)
    return code, capsys.readouterr().out.strip()


def test_lands_fast_forwards_and_deletes_the_source(repo: Path, capsys) -> None:
    pr = done_pr(repo)
    head = git(repo, repo.parent / ".gc", "rev-parse", "HEAD")
    code, said = run([pr], capsys)
    assert code == 0, said
    assert said == f"PR {pr}: gates green; main fast-forwarded to {head[:12]}"
    bare = repo.parent / "remote.git"
    assert git(bare, repo.parent / ".gc", "rev-parse", "main") == head
    refs = git(bare, repo.parent / ".gc", "for-each-ref", "--format=%(refname)", "refs/heads")
    assert BRANCH not in refs  # the source branch was deleted
    assert local.pr_get(pr)["status"] == "completed"


def test_keep_source_leaves_the_branch(repo: Path, capsys) -> None:
    pr = done_pr(repo)
    assert run([pr, "--keep-source"], capsys)[0] == 0
    bare = repo.parent / "remote.git"
    refs = git(bare, repo.parent / ".gc", "for-each-ref", "--format=%(refname)", "refs/heads")
    assert f"refs/heads/{BRANCH}" in refs


def test_refused_on_a_failing_gate(repo: Path, capsys) -> None:
    (repo / "dev-loop.yaml").write_text(
        "version: 1\nbranches: {integration: main, remote: hub, change_prefix: loop/}\n"
        "host: {provider: local}\nverify: {steps: {gate: 'false'}}\n", encoding="utf-8")
    pr = done_pr(repo)
    code, said = run([pr], capsys)
    assert code == 1 and "gate failed: gate" in said, said
    bare = repo.parent / "remote.git"
    # main did not move: a failing gate never merges
    assert git(bare, repo.parent / ".gc", "rev-parse", "main") != git(
        repo, repo.parent / ".gc", "rev-parse", "HEAD")
    assert local.pr_get(pr)["status"] == "active"


def test_refused_when_not_rebased(repo: Path, capsys) -> None:
    pr = done_pr(repo)
    gc = repo.parent / ".gc"
    bare = repo.parent / "remote.git"
    # move main on the remote past the branch's base, so main is not its ancestor
    clone = repo.parent / "other"
    git(repo.parent, gc, "clone", "-q", str(bare), str(clone))
    (clone / "other.py").write_text("y = 1\n", encoding="utf-8")
    git(clone, gc, "add", "-A")
    git(clone, gc, "commit", "-qm", "ahead")
    git(clone, gc, "push", "-q", "origin", "HEAD:main")
    code, said = run([pr], capsys)
    assert code == 1 and "not rebased" in said, said


def test_refused_when_no_clean_round(repo: Path, capsys) -> None:
    pr = local.pr_create(BRANCH, "main", "Add search", "body")  # no review-clean record
    code, said = run([pr], capsys)
    assert code == 1 and "not done" in said, said


def test_refused_when_the_done_rule_fails(repo: Path, capsys) -> None:
    """A clean round is not enough; the change's done-rule records must be on file."""
    pr = done_pr(repo, records=GREEN[:3])  # no verify, simplifier or skills record
    code, said = run([pr], capsys)
    assert code == 1 and "not done: add-search: missing" in said, said
    assert "a green verify record and verify-report.md" in said and "a simplifier line" in said
    bare = repo.parent / "remote.git"
    assert git(bare, repo.parent / ".gc", "rev-parse", "main") != git(
        repo, repo.parent / ".gc", "rev-parse", "HEAD")  # main did not move
    assert local.pr_get(pr)["status"] == "active"


def test_refused_when_a_later_round_failed(repo: Path, capsys) -> None:
    pr = done_pr(repo)
    head = git(repo, repo.parent / ".gc", "rev-parse", "HEAD")
    review_run.record(local, {"pr": pr, "head": head, "base": "full", "rc": 0})
    local.set_status(head, "failure", "round 2: 1 major", pr)
    code, said = run([pr], capsys)
    assert code == 1 and "the last review round is not clean" in said, said


def test_refused_when_the_checkout_has_edits(repo: Path, capsys) -> None:
    pr = done_pr(repo)
    (repo / "src.py").write_text("x = 9\n", encoding="utf-8")
    code, said = run([pr], capsys)
    assert code == 1 and "this checkout is not" in said, said
    bare = repo.parent / "remote.git"
    assert git(bare, repo.parent / ".gc", "rev-parse", "main") != git(
        repo, repo.parent / ".gc", "rev-parse", "HEAD")


def test_refused_with_no_round_or_follow_up_records(repo: Path, capsys) -> None:
    """As found: review-clean on file, the change's own records absent."""
    pr = done_pr(repo, records=[])
    code, said = run([pr], capsys)
    assert code == 1 and "not done: add-search: missing" in said, said


def test_refused_with_tasks_open(repo: Path, capsys) -> None:
    pr = done_pr(repo)
    tasks = repo / ".dev-loop" / "changes" / "add-search" / "tasks.md"
    tasks.write_text("- [x] T1\n- [ ] T2\n", encoding="utf-8")
    code, said = run([pr], capsys)
    assert code == 1 and "not done: add-search: tasks open" in said, said


def test_refused_with_an_active_crid_thread(repo: Path, capsys) -> None:
    pr = done_pr(repo)
    local.thread_post(pr, "/src.py", 1, "**major** — a finding\n\n`crid:ab12`")
    code, said = run([pr], capsys)
    assert code == 1 and "review threads still active" in said, said


def test_refused_when_no_review_run_is_on_file(repo: Path, capsys) -> None:
    """A hand-set success is not a review: only a `dl review` run's record proves one."""
    pr = done_pr(repo, ran=False)
    code, said = run([pr], capsys)
    assert code == 1 and "no review-run record" in said and "dl review" in said, said
    assert local.pr_get(pr)["status"] == "active"


def test_refused_when_the_review_run_failed(repo: Path, capsys) -> None:
    pr = done_pr(repo, ran=False)
    head = git(repo, repo.parent / ".gc", "rev-parse", "HEAD")
    review_run.record(local, {"pr": pr, "head": head, "base": "full", "rc": 1})
    code, said = run([pr], capsys)
    assert code == 1 and "no review-run record" in said, said


def test_subagent_fallback_lands_without_a_review_run(repo: Path, capsys) -> None:
    cfg = repo / "dev-loop.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "review: {fallback: subagent}\n",
                   encoding="utf-8")
    git(repo, repo.parent / ".gc", "commit", "-qam", "config")
    pr = done_pr(repo, ran=False)
    code, said = run([pr], capsys)
    assert code == 0 and "fast-forwarded" in said, said


def test_a_skip_or_summary_thread_does_not_block(repo: Path, capsys) -> None:
    """The not-run notice and the summary are not findings."""
    pr = done_pr(repo)
    local.thread_post(pr, None, None, "review skipped\n\n`crid:skip`")
    local.thread_post(pr, None, None, "summary\n\n`crid:summary`")
    code, said = run([pr], capsys)
    assert code == 0 and "fast-forwarded" in said, said


def test_refused_targeting_the_release_branch(repo: Path, capsys) -> None:
    (repo / "dev-loop.yaml").write_text(
        "version: 1\nbranches: {integration: main, release: main, remote: hub, "
        "change_prefix: loop/}\nhost: {provider: local}\nverify: {steps: {gate: 'true'}}\n",
        encoding="utf-8")
    pr = done_pr(repo)
    code, said = run([pr], capsys)
    assert code == 1 and "release branch" in said, said


def test_usage_without_a_pr(repo: Path) -> None:
    assert land.main([]) == 2


def test_files_are_neutral() -> None:
    plugin = Path(__file__).resolve().parents[1]
    files = [plugin / "src/dev_loop/land.py", plugin / "skills/land/SKILL.md", Path(__file__)]
    assert check(files) == []



def test_refused_release_main_when_integration_differs(repo: Path, capsys) -> None:
    (repo / "dev-loop.yaml").write_text(
        "version: 1\nbranches: {integration: trunk, release: main, remote: hub, "
        "change_prefix: loop/}\nhost: {provider: local}\nverify: {steps: {gate: 'true'}}\n",
        encoding="utf-8")
    pr = done_pr(repo)
    code, said = run([pr], capsys)
    assert code == 1 and "release branch main" in said, said
