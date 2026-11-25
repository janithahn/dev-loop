"""Checks for the legacy gate: only folders listed in legacy-changes.txt are grandfathered, a mark
says who and why, every sidecar reader survives a fieldless legacy row, and the done rule counts
each lesson once.
"""

from __future__ import annotations

import json
from pathlib import Path

from dev_loop.records import record, review_distil, review_metrics, review_pitfalls
from dev_loop.records import review_records, review_repeat
from test_record import DONE, LESSON, METRICS, ROUND, make, migrate, put

OLD_HANDOVER = {"kind": "handover", "change": "x", "next": "plan the row", "heads": "a at b",
                "open": "none", "line": "handover: x; next: plan the row"}
WHY = "reason=old-format rows"
SIGNED = {"kind": record.LEGACY_MARK, "rows": [1], "at": "2026-01-01 10:00", "by": "Tester",
          "reason": "old-format rows"}
OUTSIDE = "a legacy record outside the migration"
UNLISTED = "a legacy-mark in a folder not in legacy-changes.txt"
UNREAD = f"no record: {OLD_HANDOVER['line']}"


def listed(root: Path, *names: str) -> None:
    """Name `names` in legacy-changes.txt; a name in a comment (y) is not listed."""
    body = "# reviewed; y, named in a comment, is not listed\n"
    body += "".join(f"{name}  # old\n" for name in names)
    (record.changes_dir(root) / record.LISTED).write_text(body, encoding="utf-8")


def sidecar(folder: Path) -> list[str]:
    return record.text_of(folder / "records.jsonl").splitlines()


def add(folder: Path, *records: object) -> None:
    with (folder / "records.jsonl").open("a", encoding="utf-8", newline="\n") as file:
        file.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))


def faults(root: Path) -> list[str]:
    return [f.split(": ", 1)[1] for f in record.unparsed(root)]


def test_mark_needed(root: Path) -> None:
    """A legacy row outside the migration with no legacy-mark is refused; a forged mark is named."""
    folder = make(root, "x")
    listed(root, "x")
    add(folder, OLD_HANDOVER | {"legacy": True})
    (folder / "status.md").write_text(f"{OLD_HANDOVER['line']}\n", encoding="utf-8")
    assert faults(root) == [OUTSIDE, UNREAD], faults(root)
    forged = {"kind": record.LEGACY_MARK, "rows": [3, "1"], "at": "2026-01-01"}
    add(folder, forged)
    assert faults(root) == [OUTSIDE, f"bad legacy-mark: {str(forged)[:60]}", UNREAD], faults(root)


def test_mark_signed(root: Path) -> None:
    """A legacy-mark needs who made it and why, each words; the signed one is accepted."""
    folder = make(root, "x")
    listed(root, "x")
    (folder / "status.md").write_text(f"{OLD_HANDOVER['line']}\n", encoding="utf-8")
    row = json.dumps(OLD_HANDOVER | {"legacy": True})
    unsigned = [{k: v for k, v in SIGNED.items() if k != gone} for gone in ("by", "reason")]
    unsigned += [SIGNED | {"by": " "}, SIGNED | {"reason": ""}, SIGNED | {"by": 7}]
    for mark in unsigned:
        (folder / "records.jsonl").write_text(f"{row}\n{json.dumps(mark)}\n", encoding="utf-8")
        assert faults(root) == [OUTSIDE, f"bad legacy-mark: {str(mark)[:60]}", UNREAD], faults(root)
    (folder / "records.jsonl").write_text(f"{row}\n{json.dumps(SIGNED)}\n", encoding="utf-8")
    assert faults(root) == [], faults(root)


def test_mark_unlisted(root: Path) -> None:
    """A signed legacy-mark in a folder not listed is a fault and grandfathers no row."""
    folder = make(root, "y")
    listed(root, "x")
    add(folder, OLD_HANDOVER | {"legacy": True}, SIGNED)
    (folder / "status.md").write_text(f"{OLD_HANDOVER['line']}\n", encoding="utf-8")
    assert faults(root) == [OUTSIDE, UNLISTED, UNREAD], faults(root)


FIELDLESS = {  # kind: a record line on its Markdown file, as an old row may hold nothing else
    "recall": "Recall: x was shown", "round": "Review round 1 (PR 7): old format",
    "metrics": "| 7 | x |", "models": "Models: PR 7 old format", "skill": "Skill approved: old",
    "skills": "Skills used: old", "lesson": "## [L1] old format", "follow-up": "| 7 | 70 |",
}


def test_fieldless_read(root: Path) -> None:
    """A legacy row may lack fields: it renders with dashes and the done rule never crashes."""
    dashes = "handover: — — at — tokens, commit —; done: —; next: plan the row"
    assert record.render(OLD_HANDOVER | {"legacy": True}) == dashes
    folder = make(root, "x")
    line = "Review round 1 (PR 7, base full): no counts"
    add(folder, {"kind": "round", "line": line, "legacy": True})
    (folder / "status.md").write_text(f"{line}\n", encoding="utf-8")
    migrate(root, "x")
    put(root, "x", "skills", {"skills": "tdd"})  # a new record: the folder is judged
    gaps = ["a clean last review round", "'Lessons logged: n' (n >= 0 fixed, each a lesson)"]
    gaps += ["a green verify record and verify-report.md", "a simplifier line"]
    assert record.missing(folder) == gaps, record.missing(folder)
    add(folder, {"kind": "metrics", "line": "| 7 | x |", "legacy": True})  # no pr field
    add(folder, {"kind": "follow-up", "line": "| 7 | 70 |", "legacy": True})
    assert review_records.metric_rows(root) == [], review_records.metric_rows(root)
    shown = "work/log/x/follow-ups.md: | — | — | — | — | — |"
    assert review_records.open_follow_ups(root) == [shown], review_records.open_follow_ups(root)


def test_readers(root: Path) -> None:
    """Every sidecar reader survives a fieldless legacy row of the kinds it reads."""
    folder = make(root, "x")
    listed(root, "x")
    rows = [{"kind": kind, "line": line, "legacy": True} for kind, line in FIELDLESS.items()]
    add(folder, *rows, SIGNED | {"rows": list(range(1, len(rows) + 1))})
    status = [*(FIELDLESS[k] for k in ("recall", "round", "models", "skill", "skills")), ""]
    status += [record.METRICS_HEADER, "|---|", FIELDLESS["metrics"]]
    (folder / "status.md").write_text("\n".join(status) + "\n", encoding="utf-8")
    (folder / "learnings.md").write_text(FIELDLESS["lesson"] + "\n", encoding="utf-8")
    table = f"{record.FOLLOW_UPS_HEADER}\n|---|\n{FIELDLESS['follow-up']}\n"
    (folder / "follow-ups.md").write_text(table, encoding="utf-8")
    assert faults(root) == [], faults(root)  # the checked readers meet them too
    found, bad = review_pitfalls.past_lessons(root, "", review_distil.KINDS)
    assert (len(found), bad) == (5, 0), found
    assert [review_pitfalls.line_for(c, r) for c, r in found if r["kind"] == "lesson"]
    assert review_distil.approved(found, {"backend"}) == []
    assert review_distil.tally(root)
    try:
        review_distil.source(found, 7)
    except ValueError as error:
        assert "no records of PR 7" in str(error), error
    assert review_repeat.shown_classes(root, []) is not None
    every = record.read(folder)
    assert review_metrics.tokens_cell(every, {7: "w"}, "w") == "—"
    assert review_metrics.rounds_cell(every, {7: "w"}, ["w"]) == "—"
    assert review_records.metric_rows(root) == []
    assert record.missing(folder)
    assert put(root, "x", "lesson", LESSON)["id"] == "L2"  # the writer reads them too


def test_migrated_lessons(root: Path) -> None:
    """A migrated change's legacy lessons count toward its cumulative 'Lessons logged'."""
    folder = make(root, "x")
    migrate(root, "x")
    old = [{"kind": "lesson", "id": f"L{n}", "line": f"## [L{n}] old format", "legacy": True}
           for n in (1, 2, 3)]
    add(folder, *old)
    lines = "".join(f"{row['line']}\n" for row in old)
    (folder / "learnings.md").write_text(lines, encoding="utf-8")
    put(root, "x", "round", ROUND | {"major": "1", "fixed": "1"})
    put(root, "x", "round", ROUND | {"round": "2"})
    put(root, "x", "metrics", METRICS | {"rounds": "2"})
    for kind, fields in DONE[:3]:
        put(root, "x", kind, fields)
    assert put(root, "x", "lesson", LESSON)["id"] == "L4"
    put(root, "x", "lessons-logged", {"count": "4"})
    assert record.missing(folder) == [], record.missing(folder)
    put(root, "x", "lessons-logged", {"count": "5"})
    assert record.missing(folder) == ["'Lessons logged: n' (n >= 1 fixed, each a lesson)"]


def test_acceptance(root: Path) -> None:
    """Old-format rows, a repeated lesson id and fieldless rows: one signed mark clears them."""
    folder = make(root, "a")
    listed(root, "a")
    put(root, "a", "lesson", LESSON)
    second = "## [L1] pattern — a second block"
    handovers = ["handover: a; next: plan the row", "handover: a; next: write the spec"]
    add(folder,
        {"kind": "lesson", "line": second, "legacy": True},
        OLD_HANDOVER | {"change": "a", "line": handovers[0], "legacy": True},
        {"kind": "handover", "line": handovers[1], "legacy": True},
        {"kind": "verify", "result": "green", "head": "part b2",
         "line": "Verify: green part b2", "legacy": True})
    (folder / "status.md").write_text("\n".join([*handovers, "Verify: green part b2"]) + "\n",
                                      encoding="utf-8")
    with (folder / "learnings.md").open("a", encoding="utf-8") as learnings:
        learnings.write(f"\n{second}\n")
    assert faults(root), "marks are what clears them"
    add(folder, SIGNED | {"rows": [2, 3, 4, 5]})
    assert faults(root) == [], faults(root)
    assert record.read(folder)[1]["id"] == "L1"
    add(folder, OLD_HANDOVER | {"legacy": True})  # the mark names rows, not the folder
    with (folder / "status.md").open("a", encoding="utf-8") as status:
        status.write(f"{OLD_HANDOVER['line']}\n")
    assert faults(root) == [OUTSIDE, UNREAD], faults(root)
    (record.changes_dir(root) / record.LISTED).unlink()
    assert {UNLISTED, OUTSIDE} <= set(faults(root)), faults(root)
