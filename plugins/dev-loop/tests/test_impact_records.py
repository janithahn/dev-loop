"""Self-check for the record kinds `impact` reads: `recall` and `rework`.

A recall that showed lessons is written once (a rerun is no second
record), a skill line is no lesson id, a rework needs a numeric cause, and both kinds are on file.
Two writers at once lose no line, nor do two waiters on a dead writer's lock; a held lock times
out rather than writing and is never released by another writer; a replace Windows refuses once
is retried.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest


from dev_loop.records import record
from dev_loop.records import record_lock
from dev_loop.records.review_pitfalls import record_shown  # noqa: E402
from test_record import make, put  # noqa: E402


@pytest.fixture
def root(tree: object) -> Path:
    return tree.root  # type: ignore[attr-defined, no-any-return]


def test_recall_record(root: Path) -> None:
    folder = make(root, "built")
    shown = ["research (ls docs/research/): a", "- skill gate-fast-path for backend/x", "- p:L1 · s · a"]
    record_shown(root, "built", shown + ["- q.r-s:L12 · t · avoid: u"])
    record_shown(root, "built", shown + ["- q.r-s:L12 · t · avoid: u"])  # a rerun: not twice
    found = [r for r in record.read(folder) if r["kind"] == "recall"]
    assert [r["shown"] for r in found] == ["p:L1 q.r-s:L12"], found
    record_shown(root, "built", shown[:2])  # no lesson shown: nothing written
    assert len([r for r in record.read(folder) if r["kind"] == "recall"]) == 1
    assert record.unparsed(root, [folder]) == [], record.unparsed(root, [folder])


def test_rework(root: Path) -> None:
    folder = make(root, "fix")
    put(root, "fix", "rework", {"pr": "9", "cause_pr": "7", "note": "a null guard missing"})
    assert record.unparsed(root, [folder]) == []
    with pytest.raises(ValueError, match="bad cause_pr"):
        put(root, "fix", "rework", {"pr": "9", "cause_pr": "seven", "note": "x"})


def race(root: Path, change: str) -> None:
    """Two writers of one change at once: every sidecar record has its status.md line."""
    folder = record.changes_dir(root) / change

    def writer(name: str) -> None:
        for n in range(15):
            record_shown(root, change, [f"- {name}:L{n} · s · a"])

    threads = [threading.Thread(target=writer, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    found = [r for r in record.read(folder) if r["kind"] == "recall"]
    lines = (folder / "status.md").read_text(encoding="utf-8").count(f"Recall: {change} was shown")
    assert len(found) == lines == 30, (len(found), lines)
    assert record.unparsed(root, [folder]) == [], record.unparsed(root, [folder])
    assert sorted(p.name for p in folder.glob(".*")) == [], list(folder.glob(".*"))


def test_two_writers(root: Path) -> None:
    make(root, "race")
    race(root, "race")


def test_stale_lock_two_waiters(root: Path) -> None:
    """Two waiters meet one dead writer's lock; it is broken once."""
    lock = make(root, "stale") / ".record.lock"
    lock.write_text("1 dead", encoding="utf-8")
    old = time.time() - record_lock.LOCK_STALE - 1
    os.utime(lock, (old, old))
    breaker = lock.with_name(".record.lock.breaker")  # waiter A is mid-takeover
    breaker.write_text("1 a", encoding="utf-8")
    record_lock.break_stale(lock, "2 b")  # so B never decides: it might unlink A's new lock
    assert lock.read_text(encoding="utf-8") == "1 dead"
    assert breaker.read_text(encoding="utf-8") == "1 a"
    breaker.unlink()
    race(root, "stale")


def test_lock_left_behind(root: Path) -> None:
    """A fresh lock is another writer's: wait, then fail; its release never removes another's."""
    folder = make(root, "held")
    lock = folder / ".record.lock"
    lock.write_text("1 other", encoding="utf-8")
    with pytest.raises(TimeoutError), record_lock.locked(folder, wait=0.2):
        pytest.fail("a write went past a held lock")
    record_lock.release(lock, "2 mine")
    assert lock.read_text(encoding="utf-8") == "1 other"
    lock.unlink()
    record_lock.release(lock, "2 mine")  # already gone: no error


def test_replace_refused_once(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows refuses a replace while a reader has the file; retried."""
    folder = make(root, "busy")
    real, calls = os.replace, []

    def refuse_once(source: str, target: str) -> None:
        calls.append(target)
        if len(calls) == 1:
            raise PermissionError(target)
        real(source, target)

    monkeypatch.setattr(os, "replace", refuse_once)
    record_shown(root, "busy", ["- p:L1 · s · a"])
    assert len(calls) == 2, calls
    assert [r["shown"] for r in record.read(folder) if r["kind"] == "recall"] == ["p:L1"]
    assert record.unparsed(root, [folder]) == [], record.unparsed(root, [folder])
