"""Checks for `dl record legacy` and `dl record render`: they grandfather old-format fault shapes
and leave every non-legacy fault caught.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dev_loop.__main__ import main as dl_main
from dev_loop.records import record, record_legacy
from test_record import LESSON, METRICS, ROUND, make, put
from test_record_legacy_gate import OLD_HANDOVER, SIGNED, WHY, add, faults, listed, sidecar

REAL_WHO = record_legacy.who


@pytest.fixture(autouse=True)
def _signed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The marker's name, without the host's git config."""
    monkeypatch.setattr(record_legacy, "who", lambda _root: "Tester")


def run(root: Path, *argv: str) -> int:
    return record_legacy.main(root, list(argv))


def test_old_handover(root: Path) -> None:
    """An old-schema handover row is grandfathered by `legacy`; a hand-written line beside it is
    still caught, and the other rows are byte for byte the same."""
    folder = make(root, "x")
    listed(root, "x")
    put(root, "x", "skills", {"skills": "tdd"})
    add(folder, OLD_HANDOVER)
    with (folder / "status.md").open("a", encoding="utf-8") as status:
        status.write(f"\n{OLD_HANDOVER['line']}\nLessons logged: 2\n")  # the second by hand
    old = "missing agent; missing commit; missing context; missing done; missing role; "
    old += "unknown field change; unknown field heads; unknown field open"
    unread = [f"no record: {OLD_HANDOVER['line']}", "no record: Lessons logged: 2"]
    assert faults(root) == [old, *unread]
    before = sidecar(folder)
    assert run(root, "legacy", "x", WHY, "2") == 0
    after = sidecar(folder)
    assert after[0] == before[0] and len(after) == 3, after  # nothing renumbered or reordered
    assert json.loads(after[1]) == OLD_HANDOVER | {"legacy": True}, after[1]
    mark = json.loads(after[2])
    assert mark == SIGNED | {"rows": [2], "at": mark["at"]}, mark  # who and why
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", mark["at"]), mark
    assert faults(root) == ["no record: Lessons logged: 2"], faults(root)  # non-legacy: caught
    assert run(root, "legacy", "x", WHY, "2") == 1  # already legacy: refused
    assert run(root, "legacy", "x", WHY, "9") == 1  # no such row: refused
    assert sidecar(folder) == after


def test_repeated_lessons(root: Path) -> None:
    """A lesson id given twice is caught; marked legacy, the copy leaves the repeat check."""
    folder = make(root, "x")
    listed(root, "x")
    first = put(root, "x", "lesson", LESSON)
    add(folder, first)
    assert faults(root) == ["lesson id L1 repeated"], faults(root)
    assert run(root, "legacy", "x", WHY, "2") == 0
    assert faults(root) == [], faults(root)
    assert record.read(folder)[0] == first  # the first is untouched and still judged strictly


def assert_idempotent(folder: Path, root: Path) -> None:
    names = ("records.jsonl", *record.MARKDOWN)
    before = [record.text_of(folder / name) for name in names]
    assert run(root, "render", "x", WHY) == 0
    assert [record.text_of(folder / name) for name in names] == before


def test_render_handover(root: Path) -> None:
    """A status.md handover line with no record gains a legacy record and its mark; a second
    run changes nothing."""
    folder = make(root, "x")
    listed(root, "x")
    line = "handover: x; next: write the spec"
    (folder / "status.md").write_text(f"# Status: x\n\n{line}\n", encoding="utf-8")
    assert faults(root) == [f"no record: {line}"], faults(root)
    assert run(root, "render", "x", WHY) == 0
    assert faults(root) == [], faults(root)
    added, mark = (json.loads(x) for x in sidecar(folder))
    assert added == {"kind": "handover", "line": line, "legacy": True}, added
    assert mark == SIGNED | {"at": mark["at"]}, mark
    assert_idempotent(folder, root)


def test_render_lines(root: Path) -> None:
    """Records whose Markdown lines were lost get them back, a lesson its whole block, a metrics
    row its table head; a forged record stays caught."""
    folder = make(root, "x")
    listed(root, "x")
    put(root, "x", "verify", {"result": "green", "head": "HEAD"})
    lesson = put(root, "x", "lesson", LESSON)
    put(root, "x", "metrics", METRICS)
    forged = put(root, "x", "round", ROUND) | {"major": 1}  # edited after writing
    add(folder, forged)
    (folder / "status.md").unlink()
    (folder / "learnings.md").unlink()
    forged_fault = "its fields do not render its line"
    assert faults(root) == ["no Markdown line"] * 4 + [forged_fault], faults(root)
    assert run(root, "render", "x", WHY) == 0
    assert faults(root) == [forged_fault], faults(root)  # still caught
    learnings = record.text_of(folder / "learnings.md")
    assert learnings == f"# Learnings: x\n{record.render(lesson)}", learnings  # the whole block
    status = record.text_of(folder / "status.md").splitlines()
    head = status.index(record.METRICS_HEADER)
    assert status[head + 2] == record.render(record.read(folder)[2]), status  # under its head
    assert_idempotent(folder, root)


def test_usage(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A misused command is refused in one line, never a crash; no change folder is refused."""
    make(root, "x")
    listed(root, "x", "nope")
    usage = [["legacy", "x", WHY], ["legacy", "x", WHY, "a"], ["render"], ["render", "x", WHY, "1"]]
    usage += [["legacy", "x", "1"], ["render", "x"], ["render", "x", "reason= "]]  # no reason
    usage += [["render", "x", WHY, WHY]]
    for argv in usage:
        capsys.readouterr()
        assert run(root, *argv) == 2, argv
        assert len(capsys.readouterr().err.splitlines()) == 1, argv
    assert run(root, "render", "nope", WHY) == 1
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_unlisted(root: Path) -> None:
    """`legacy` and `render` refuse a folder not in legacy-changes.txt and change nothing; a name
    in a comment is not listed."""
    folder = make(root, "y")
    listed(root, "x")
    add(folder, OLD_HANDOVER)
    status = f"{OLD_HANDOVER['line']}\nhandover: y; next: plan the row\n"
    (folder / "status.md").write_text(status, encoding="utf-8")
    names = ("records.jsonl", *record.MARKDOWN)
    before = [record.text_of(folder / name) for name in names]
    assert run(root, "legacy", "y", WHY, "1") == 1
    assert run(root, "render", "y", WHY) == 1
    (record.changes_dir(root) / record.LISTED).unlink()  # no list: nothing is listed
    assert run(root, "render", "y", WHY) == 1
    assert [record.text_of(folder / name) for name in names] == before


def test_lesson_count(root: Path) -> None:
    """'Lessons logged' counts each lesson id once and never a legacy copy."""
    folder = make(root, "x")
    listed(root, "x")
    put(root, "x", "round", ROUND | {"major": "2", "fixed": "2"})
    put(root, "x", "round", ROUND | {"round": "2"})
    put(root, "x", "metrics", METRICS | {"rounds": "2"})
    for kind, fields in (("verify", {"result": "green", "head": "HEAD"}),
                         ("simplifier", {"edits": "none"}), ("skills", {"skills": "tdd"})):
        put(root, "x", kind, fields)
    put(root, "x", "lessons-logged", {"count": "2"})
    add(folder, put(root, "x", "lesson", LESSON))  # L1 twice
    short = "'Lessons logged: n' (n >= 2 fixed, each a lesson)"
    assert record.missing(folder) == [short], record.missing(folder)
    assert run(root, "legacy", "x", WHY, str(len(sidecar(folder)))) == 0
    assert record.missing(folder) == [short], record.missing(folder)
    put(root, "x", "lesson", LESSON | {"summary": "a second cause, said once, long enough to read"})
    assert record.missing(folder) == [], record.missing(folder)


@pytest.mark.skipif(shutil.which("git") is None, reason="no git")
def test_who(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`by` is git's user.name; with none the command refuses, never writing an unsigned mark."""
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "A Marker"], check=True)
    assert REAL_WHO(root) == "A Marker"
    subprocess.run(["git", "-C", str(root), "config", "--unset", "user.name"], check=True)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(root / "none"))
    with pytest.raises(ValueError, match="user.name"):
        REAL_WHO(root)


def test_cli(root: Path) -> None:
    """`dl record render` and `dl record legacy` run through the CLI."""
    folder = make(root, "x")
    listed(root, "x")
    (folder / "status.md").write_text(f"{OLD_HANDOVER['line']}\n", encoding="utf-8")
    assert dl_main(["record", "render", "x", WHY]) == 0
    assert faults(root) == [], faults(root)
    assert dl_main(["record", "legacy", "x", WHY, "9"]) == 1
