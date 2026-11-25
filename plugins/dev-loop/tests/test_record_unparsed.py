"""The records-on-file checks for record.py (`record.unparsed`, and the done rule never reading a
record off file), split from test_record.py (that file passed the 400-line
hard stop). Run by test_record.main.
"""

from __future__ import annotations

import json
from pathlib import Path

from dev_loop.records import record
from test_record import LESSON, ROUND, finished, make, migrate, put


def test_on_file(root: Path) -> None:
    """A sidecar record with no Markdown line is refused and never read; a bad
    line (no id, not JSON) is named, never a crash."""
    folder = make(root, "x")
    finished(root, "x")
    dirty = record.parse("round", [f"{k}={v}" for k, v in (ROUND | {"major": "1"}).items()])
    dirty["line"] = record.render(dirty)  # rendered right, but written to the sidecar only
    legacy = {"kind": "lesson", "id": "L9", "when": None, "line": "## [L9] x", "legacy": True}
    no_id = record.parse("lesson", [f"{k}={v}" for k, v in LESSON.items()]) | {"line": "## [L1] x"}
    lines = [json.dumps(r) for r in (dirty, legacy | dict.fromkeys(LESSON), no_id)]
    with (folder / "records.jsonl").open("a", encoding="utf-8") as sidecar:
        sidecar.write("\n".join([*lines, "{not json"]) + "\n")
    migrate(root, "x")
    found = [f.split(": ", 1)[1] for f in record.unparsed(root)]
    assert found[:3] == ["no Markdown line", "no Markdown line", "missing id; missing when"], found
    assert len(found) == 4 and found[3].startswith("unknown kind in not JSON"), found
    assert record.missing(folder) == []  # none of the four is read, and nothing raises
    skills = {"kind": "skills", "skills": None, "legacy": True}  # a legacy line's line
    with (folder / "records.jsonl").open("a", encoding="utf-8") as sidecar:  # status.md has blanks
        for line in ({}, {"line": ["a"]}, {"line": ""}):
            sidecar.write(json.dumps(skills | line) + "\n")
    assert [f.split(": ", 1)[1] for f in record.unparsed(root)[-3:]] == ["bad line"] * 3
    assert record.missing(folder) == []  # read without a crash; the three are not read
    heading = old_round(blocker=0, major=0) | {"line": "## Build"}  # not a record line
    with (folder / "status.md").open("a", encoding="utf-8") as status:
        status.write("\n## Build\n")
    with (folder / "records.jsonl").open("a", encoding="utf-8") as sidecar:
        sidecar.write(json.dumps(heading) + "\n")
    assert record.unparsed(root)[-1].endswith(": no Markdown line"), record.unparsed(root)


def old_round(**counts: int) -> dict[str, object]:
    """A migrated round record, its fields null unless given."""
    keys = ("pr", "round", "base", "blocker", "major", "minor", "fixed", "deferred", "active")
    return {"kind": "round", **dict.fromkeys(keys), **counts, "legacy": True}


def test_no_line(root: Path) -> None:
    """A record with no line is refused, and the done rule never reads it."""
    folder = make(root, "x")
    finished(root, "x")
    round_ = {k: int(v) if v.isdigit() else v for k, v in (ROUND | {"round": "2"}).items()}
    with (folder / "records.jsonl").open("a", encoding="utf-8") as sidecar:
        sidecar.write(json.dumps({"kind": "round", **round_, "major": 1}) + "\n")
    assert record.unparsed(root) == [
        "work/log/x/records.jsonl:7: its fields do not render its line"
    ], record.unparsed(root)
    assert record.missing(folder) == []  # the forged dirty round is not read either way


def test_unparsed(root: Path) -> None:
    """Hand-written record lines and broken sidecar lines are counted; prose is not."""
    folder = make(root, "x")
    put(root, "x", "skills", {"skills": "tdd"})
    lines = [
        "Lessons logged: 2",  # by hand: counted
        "review round 1 was a fresh subagent.",  # prose: not a record
        "| Phase | Status |", "|---|---|", "| Verify | ✅ |",  # not a PR table
        "", "| PR | Rounds | Findings (b/M/m) | Lessons |", "|---|---|---|---|", "| 9 | 1 | 0/0/1 | 0 |",
    ]
    with (folder / "status.md").open("a", encoding="utf-8") as status:
        status.write("\n".join(lines) + "\n")
    (folder / "follow-ups.md").write_text("| PR | Thread |\n|---|---|\n| 9 | 90 |\n", "utf-8")
    with (folder / "records.jsonl").open("a", encoding="utf-8") as sidecar:
        sidecar.write('{"kind": "round"\n{"kind": "verify", "result": "", "head": "a", "line": "x"}\n')
    legacy = {"kind": "verify", "result": None, "head": None, "line": "Verify: old", "legacy": True}
    forged = put(root, "x", "round", ROUND | {"major": "1"}) | {"major": 0}  # edited after writing
    with (folder / "records.jsonl").open("a", encoding="utf-8") as sidecar:
        sidecar.write(json.dumps(legacy) + "\n" + json.dumps(forged) + "\n")
    found = record.unparsed(root)
    assert [f.split(": ", 1)[0] for f in found] == [
        "work/log/x/records.jsonl:2",
        "work/log/x/records.jsonl:3",
        "work/log/x/records.jsonl:5",
        "work/log/x/records.jsonl:6",
        "work/log/x/status.md",
        "work/log/x/status.md",
        "work/log/x/follow-ups.md",
    ], found
    assert "bad result: ''" in found[1], found
    assert found[2].endswith("a legacy record outside the migration"), found  # 9100
    assert found[3].endswith("its fields do not render its line"), found  # 9125
    assert found[4].endswith("no record: Lessons logged: 2"), found
    assert found[5].endswith("no record: | 9 | 1 | 0/0/1 | 0 |"), found
    migrate(root, "x")
    assert "a legacy record outside" not in " ".join(record.unparsed(root))  # migrated: allowed


def test_bad_line_read(root: Path) -> None:
    """A broken sidecar line is named by unparsed, and no reader or the writer raises on it."""
    folder = make(root, "x")
    put(root, "x", "follow-up", {"pr": "7", "thread": "70", "file": "a", "finding": "f", "why": "w"})
    with (folder / "records.jsonl").open("a", encoding="utf-8") as sidecar:
        sidecar.write('{not json\n[1]\n{"no": "kind"}\n')
    put(root, "x", "lesson", LESSON)  # the writer reads the lesson ids past the broken lines
    assert [r["kind"] for r in record.read(folder)] == ["follow-up", "lesson"]
    found = [f.split(": ", 1)[0] for f in record.unparsed(root)]
    assert found == [f"work/log/x/records.jsonl:{n}" for n in (2, 3, 4)], found


def test_repeated_id(root: Path) -> None:
    """A lesson id the writer gave twice in one folder is named; migrated history is not."""
    folder = make(root, "x")
    first = put(root, "x", "lesson", LESSON)
    with (folder / "records.jsonl").open("a", encoding="utf-8") as sidecar:
        sidecar.write(json.dumps(first) + "\n")
    found = record.unparsed(root)
    assert found == ["work/log/x/records.jsonl:2: lesson id L1 repeated"], found
    old = make(root, "old")
    legacy = {"kind": "lesson", "id": "L6", "line": "## [L6] pattern — a", "legacy": True}
    legacy |= dict.fromkeys(("when", "priority", "summary", "avoid", "class", "guard", "files"))
    legacy |= {"round": None}
    again = legacy | {"line": "## [L6] pattern — b"}  # c5-examiner-pack's migrated L6 twice
    (old / "records.jsonl").write_text(f"{json.dumps(legacy)}\n{json.dumps(again)}\n", "utf-8")
    (old / "learnings.md").write_text(f"{legacy['line']}\n\n{again['line']}\n", encoding="utf-8")
    migrate(root, "old")
    assert record.unparsed(root, [old]) == [], record.unparsed(root, [old])


def test_heading_not_record(root: Path) -> None:
    """A legacy record whose line is a Markdown heading is not on file; only record lines are."""
    folder = make(root, "x")
    (folder / "status.md").write_text("# Status: x\n\n## Build\n", encoding="utf-8")
    legacy = {"kind": "verify", "result": None, "head": None, "line": "## Build", "legacy": True}
    (folder / "records.jsonl").write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    migrate(root, "x")
    found = record.unparsed(root)
    assert found == ["work/log/x/records.jsonl:1: no Markdown line"], found
