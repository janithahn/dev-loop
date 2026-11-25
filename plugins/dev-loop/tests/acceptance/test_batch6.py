"""Acceptance batch 6: review rounds against a stub reviewer.

Each case runs `dl review` through test_pr_review's harness (a throwaway origin and clone, a fake
`dl host` that records every write) with F-STUB: a fake `claude` first on a PATH that holds no real
one, its behaviour set by STUB_MODE (ok / sleep / exit1 / empty / echoenv). HOME, the Claude config
dir, the plugin data dir and the global git config are isolated per test; every run is bounded.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest
import test_pr_review as pr
from test_pr_review import Case, crid, git, thread

SYSTEM_PATH = "/usr/bin:/bin"  # no real claude here (checked in the no-claude case)
STUB = """#!/usr/bin/env bash
case "$*" in *"Reply with the single word OK."*)
  echo '{"is_error":false,"result":"OK"}'; exit 0;; esac
env > "$STUB_DIR/env.txt"
cat > /dev/null
case "${STUB_MODE:-ok}" in
  sleep) exec sleep 600;;
  exit1) echo "stub failed" >&2; exit 1;;
  empty) exit 0;;
  echoenv)
    m="leak: $CLAUDE_CODE_OAUTH_TOKEN host=${DEV_LOOP_HOST_TOKEN:-absent}. remove it."
    v="$(jq -nc --arg m "$m" '{verdict:"CHANGES_REQUESTED",summary:"s",files_seen:1,
      findings:[{file:"a.py",line:1,severity:"minor",rule:"leak",message:$m}]}')"
    jq -nc --arg v "$v" '{result:$v}'; exit 0;;
esac
cat "$STUB_REPLY"
"""
BLOCKER = {"file": "a.py", "line": 1, "severity": "blocker", "rule": "b-rule", "message": "b. fix."}
MINOR = {"file": "pkg/b.py", "line": 1, "severity": "minor", "rule": "m-rule", "message": "m. fix."}
TWO = {"verdict": "CHANGES_REQUESTED", "summary": "s", "files_seen": 2,
       "findings": [BLOCKER, MINOR]}
NONE = {"verdict": "APPROVE", "summary": "s", "files_seen": 1, "findings": []}
USAGE = {"input_tokens": 100, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 20,
         "output_tokens": 7}
BLOCKER_CRID, MINOR_CRID = crid("a.py|b-rule"), crid("pkg/b.py|m-rule")  # line 1: file|rule


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The harness preamble, and every review run under a 150 s bound (stdout joined to stderr)."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (tmp_path / "data").mkdir()
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\temail = t@t\n\tname = tester\n"
                         "[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    for name, value in (("HOME", home), ("CLAUDE_CONFIG_DIR", home / ".claude"),
                        ("CLAUDE_PLUGIN_DATA", tmp_path / "data"), ("GIT_CONFIG_GLOBAL", gitconfig),
                        ("GIT_TERMINAL_PROMPT", "0")):
        monkeypatch.setenv(name, str(value))
    bounded = tmp_path / "bounded-bash"
    bounded.write_text('#!/bin/sh\nexec timeout -k 5 150 bash "$@" 1>&2\n', encoding="utf-8")
    bounded.chmod(0o755)
    monkeypatch.setattr(pr, "BASH", str(bounded))


@pytest.fixture
def stub(tmp_path: Path) -> Path:
    folder = tmp_path / "stub"
    folder.mkdir()
    (folder / "claude").write_text(STUB, encoding="utf-8", newline="\n")
    (folder / "claude").chmod(0o755)
    return folder


def stub_case(stub: Path, mode: str = "ok", reply: object = NONE, **kw: object) -> Case:
    """F-STUB: a round whose reviewer is the stub, with no spec unless one is passed."""
    envelope = {"result": json.dumps(reply), "num_turns": 3, "duration_ms": 1500, "usage": USAGE}
    if kw.pop("metered", True) is False:
        envelope = {"result": json.dumps(reply)}
    (stub / "reply.json").write_text(json.dumps(envelope), encoding="utf-8")
    env = (("PATH", f"{stub}:{SYSTEM_PATH}"), ("STUB_MODE", mode), ("STUB_DIR", str(stub)),
           ("STUB_REPLY", str(stub / "reply.json")))
    extra: tuple[tuple[str, str], ...] = kw.pop("env", ())  # type: ignore[assignment]
    return Case(**{"spec": None, "env": env + extra, **kw})  # type: ignore[arg-type]


def writes(posted: str) -> list[tuple[str, dict[str, object]]]:
    return [(verb, json.loads(body)) for verb, body in
            (line.split("\t", 1) for line in posted.splitlines())]


def thread_writes(posted: str) -> list[tuple[str, dict[str, object]]]:
    return [(verb, body) for verb, body in writes(posted) if verb != "review-run"]


def ours(tid: int, sev: str, path: str, code: str, status: str = "active") -> dict[str, object]:
    item = thread(tid, f"**{sev}** — earlier.\n\n`crid:{code}`")
    return {**item, "status": status, "threadContext": {"filePath": f"/{path}"}}


def test_v1_a_round_posts_once_and_is_idempotent(stub: Path) -> None:
    code, err, posted, _ = pr.review({}, stub_case(stub, reply=TWO))
    assert code == 0, err
    assert [b["rc"] for v, b in writes(posted) if v == "review-run"] == [0], posted
    new = [body for verb, body in writes(posted) if verb == "thread-post"]
    assert len(new) == 3, posted  # the blocker, the minor and the summary
    assert sum("`crid:summary`" in str(body["text"]) for body in new) == 1, posted
    threads = tuple(
        {"id": n, "status": "closed" if "file" not in body else "active",
         "threadContext": {"filePath": f"/{body.get('file', '')}"},
         "comments": [{"content": body["text"]}]} for n, body in enumerate(new, 1))
    code, err, posted, _ = pr.review({}, stub_case(stub, reply=TWO, threads=threads))
    assert code == 0, err
    # the summary, in place
    assert [v for v, _ in thread_writes(posted)] == ["thread-edit"], posted
    assert [b["rc"] for v, b in writes(posted) if v == "review-run"] == [0], posted


def test_v2_a_follow_up_round_reviews_only_the_fixes(stub: Path) -> None:
    threads = (ours(11, "blocker", "a.py", BLOCKER_CRID), ours(12, "minor", "pkg/b.py", MINOR_CRID))
    code, err, posted, _ = pr.review({}, stub_case(stub, base="HEAD~1", threads=threads))
    assert code == 0, err
    assert '"thread":"11","status":"fixed"' in posted, posted
    assert '"thread":"12"' not in posted, posted  # pkg/b.py is outside the fix diff


def test_v3_a_human_dismissal_stays_and_our_resolve_reopens(stub: Path) -> None:
    ours_resolved = ours(22, "minor", "pkg/b.py", MINOR_CRID, "fixed")
    marker = {"content": "✅ No longer flagged by Claude — resolved."}
    comments = [*ours_resolved["comments"], marker]  # type: ignore[misc]
    resolved = {**ours_resolved, "comments": comments}
    threads = (ours(21, "blocker", "a.py", BLOCKER_CRID, "wontFix"), resolved)
    code, err, posted, _ = pr.review({}, stub_case(stub, reply=TWO, threads=threads))
    assert code == 0, err
    assert '"thread":"21"' not in posted, posted
    assert '"thread":"22","status":"active"' in posted, posted
    assert '"thread":"22","text":"↩ Re-flagged' in posted, posted
    assert not [b for v, b in writes(posted) if v == "thread-post" and "file" in b], posted


def reviewer_tools(tmp: Path) -> tuple[tuple[str, str], ...]:
    (tmp / "dev-loop.yaml").write_text(
        "roles: {reviewer: {tools: [Read, Grep, Glob, Bash]}}\n", encoding="utf-8")
    return (("REVIEW_CONFIG_ROOT", str(tmp)),)


@pytest.mark.parametrize("path", ["no-claude", "exit1", "empty", "over-cap", "extra-tools"])
def test_v4_every_could_not_run_path_fails_and_says_so(
    path: str, stub: Path, tmp_path: Path
) -> None:
    kw: dict[str, object] = {"threads": (ours(31, "blocker", "a.py", BLOCKER_CRID),)}
    mode, skip = "ok", path in ("no-claude", "extra-tools")
    if path == "no-claude":
        assert shutil.which("claude", path=SYSTEM_PATH) is None
        kw["env"] = (("PATH", SYSTEM_PATH),)  # the later PATH wins: the stub is not on it
    elif path == "over-cap":
        kw["fix"] = "x = 2\n" * 20_001
    elif path == "extra-tools":
        kw["env"] = reviewer_tools(tmp_path)
    else:
        mode = path
    case = stub_case(stub, mode, **kw)
    code, err, posted, _ = pr.review({}, case)
    assert code == 1, err
    assert '"status":"fixed"' not in posted, posted  # nothing auto-resolved
    if skip:
        assert "SKIPPED" in posted and "`crid:skip`" in posted, posted
        assert "Claude PR review (advisory)" not in posted, posted
    else:  # the round ran but could not finish: its summary says so beside any count
        assert "INCOMPLETE" in posted or "NOT reviewed" in posted, posted


def test_v5_a_lost_thread_write_fails_the_round(stub: Path) -> None:
    case = stub_case(stub, reply=TWO, env=(("FAKE_FAIL_WRITES", "1"),))
    code, err, _, _ = pr.review({}, case)
    assert code == 1, err
    assert "host write(s) failed" in err, err


def test_v6_the_review_never_hangs(stub: Path) -> None:
    case = stub_case(stub, "sleep", env=(("REVIEW_CLAUDE_TIMEOUT", "5"),))
    start = time.monotonic()
    code, err, posted, _ = pr.review({}, case)
    assert time.monotonic() - start < 120, err
    assert code == 1, err
    assert "exit=124" in err, err  # the pass was cut at its bound
    assert "INCOMPLETE" in posted, posted


def test_v7_the_review_keeps_the_full_history(
    stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts: dict[str, str] = {}
    make = pr.make_repo

    def deep(root: Path, case: Case) -> Path:  # 300+ commits on the integration branch
        repo, origin = make(root, case), root / "origin"
        for n in range(310):
            git(origin, "commit", "-q", "--allow-empty", "-m", f"c{n}")
        git(repo, "remote", "set-url", "origin", f"file://{origin}")
        git(repo, "fetch", "-q", "origin")
        counts["before"] = git(repo, "rev-list", "--all", "--count")
        return repo

    monkeypatch.setattr(pr, "make_repo", deep)
    code, err, _, fakes = pr.review({}, stub_case(stub))
    repo = fakes.parent / "repo"
    assert code == 0, err
    assert int(counts["before"]) > 300
    assert not (repo / ".git" / "shallow").exists()
    assert git(repo, "rev-list", "--all", "--count") == counts["before"]


def test_v9_secrets_never_reach_the_reviewer_or_the_comments(stub: Path) -> None:
    code, err, posted, _ = pr.review({}, stub_case(stub, "echoenv"))
    assert code == 0, err
    assert pr.HOST_TOKEN not in (stub / "env.txt").read_text("utf-8")
    assert "zz-test-token-zz" not in posted, posted
    assert "leak: ***REDACTED*** host=absent" in posted, posted


def test_v11_the_round_prints_its_usage(stub: Path) -> None:
    code, err, _, _ = pr.review({}, stub_case(stub))
    assert code == 0, err
    assert "Usage: turns=6 seconds=3 tokens_in=260 tokens_out=14 unmetered=0" in err, err
    code, err, _, _ = pr.review({}, stub_case(stub, metered=False))
    assert code == 0, err
    assert "Usage: turns=0 seconds=0 tokens_in=0 tokens_out=0 unmetered=2" in err, err
