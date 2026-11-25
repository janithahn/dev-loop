"""Checks for the record writer (`dl record`) on a temporary project with its own changes folder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dev_loop.records import record

ROUND = {"pr": "7", "round": "1", "base": "full", "blocker": "0", "major": "0", "minor": "3"}
ROUND |= {"fixed": "0", "deferred": "3", "active": "0"}
ROUND |= {"turns": "11", "seconds": "84", "tokens_in": "38200", "tokens_out": "2100"}
ROUND |= {"unmetered": "0"}
LESSON = {"priority": "high", "summary": "the cause, said once in a sentence that runs long enough"}
LESSON |= {"avoid": "do it right. Self-check: run it.", "class": "3 Same thing twice"}
LESSON |= {"guard": "backend/tests/g.py", "files": "backend/a.py", "round": "1"}
FOLLOW_UP = {"pr": "7", "thread": "70", "file": "`a.py`", "finding": "f", "why": "w"}
METRICS = {"pr": "7", "change": "x", "rounds": "1", "per_round": "0/0/3", "total": "0/0/3"}
METRICS |= {"lessons": "0"}
DONE = [("verify", {"result": "green", "head": "HEAD"}), ("simplifier", {"edits": "none"})]
DONE += [("skills", {"skills": "tdd"}), ("lessons-logged", {"count": "0"})]


def make(root: Path, change: str, tasks: str = "- [x] `T1` done\n") -> Path:
    folder = record.changes_dir(root) / change
    folder.mkdir(parents=True)
    (folder / "tasks.md").write_text(tasks, encoding="utf-8")
    (folder / "verify-report.md").write_text("green", encoding="utf-8")
    return folder


def migrate(root: Path, *names: str) -> None:
    """List `names` as migrated: the folders a legacy record may sit in."""
    (record.changes_dir(root) / record.MIGRATED).write_text(" ".join(names), encoding="utf-8")


def put(root: Path, change: str, kind: str, fields: dict[str, str]) -> dict[str, object]:
    pairs = [f"{key}={value}" for key, value in fields.items()]
    return record.write(root, change, record.parse(kind, pairs))


def finished(root: Path, change: str, round_: dict[str, str] = ROUND) -> None:
    """Every line of the done rule, through the writer."""
    put(root, change, "round", round_)
    put(root, change, "metrics", METRICS)
    for kind, fields in DONE:
        put(root, change, kind, fields)


def done_guard(root: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """Run `dl record done` on the project and return what it said."""
    capsys.readouterr()
    assert record.main(["done"]) == 0
    return capsys.readouterr().out


def test_task_styles(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A `T1`, **T1** or `1.` task list is judged (the old regex skipped all three)."""
    for name, tasks in (("a", "- [x] T1 x\n"), ("b", "- [x] **T1** x\n"), ("c", "- [x] 1. x\n")):
        make(root, name, tasks)
    make(root, "open", "- [x] T1 x\n- [ ] T2 y\n")
    said = done_guard(root, capsys)
    assert all(f"[done?] {name}:" in said for name in "abc"), said
    assert "open" not in said, said  # an unticked box: not finished, not judged


def test_round_wording(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Drift 3: the guard reads the sidecar's fields, never the wording of a round line.

    The hand line is vessel-watch-recorder's clean last round (no "active"), which the old regex
    took for a dirty one; verify-report.md is there so the old guard had nothing else to name.
    """
    folder = make(root, "x")
    finished(root, "x")
    (folder / "verify-report.md").write_text("green\n", encoding="utf-8")
    hand = "Review round 2 (base abc): 0 blocker, 0 major, 3 minor filed → 0 fixed, 3 deferred"
    with (folder / "status.md").open("a", encoding="utf-8") as status:
        status.write(f"\n{hand}\n")
    assert "[done?] x" not in done_guard(root, capsys)  # the sidecar's last round is clean
    put(root, "x", "round", ROUND | {"round": "2", "major": "1", "fixed": "1"})
    assert "a clean last review round" in done_guard(root, capsys)
    found = record.unparsed(root)
    assert found == [f"work/log/x/status.md: no record: {hand[:90]}"], found




def test_debt_writes_both_files(root: Path) -> None:
    """V4a: a debt record goes to debt.md under its table head and to records.jsonl."""
    folder = make(root, "x")
    put(root, "x", "debt", FOLLOW_UP)
    put(root, "x", "debt", FOLLOW_UP | {"thread": "71"})
    text = (folder / "debt.md").read_text(encoding="utf-8")
    assert text.startswith("# x debt\n"), text
    assert text.count(record.FOLLOW_UPS_HEADER) == 1, text
    assert "| 7 | 71 | `a.py` | f | w |" in text, text
    assert [r["kind"] for r in record.read(folder)] == ["debt", "debt"]
    assert record.unparsed(root) == []
    with pytest.raises(ValueError, match="missing why"):
        put(root, "x", "debt", {k: v for k, v in FOLLOW_UP.items() if k != "why"})


def test_a_follow_up_with_no_thread_takes_thread_none(root: Path) -> None:
    """v0.4.20 D: a follow-up found while planning has no PR thread: `thread=none`."""
    folder = make(root, "x")
    pairs = ["pr=none", "thread=none", "file=a.py", "finding=x", "why=y"]
    assert record.main(["x", "follow-up", *pairs]) == 0
    assert "| none | none | a.py | x | y |" in (folder / "follow-ups.md").read_text("utf-8")
    with pytest.raises(ValueError, match="bad thread"):
        put(root, "x", "follow-up", FOLLOW_UP | {"thread": "planner"})
