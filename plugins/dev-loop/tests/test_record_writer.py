"""The writer's checks for record.py (`record.write`, `record.main`), split from test_record.py by
concern (writer, done rule, unparsed). Run by test_record.main.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from dev_loop.records import record
from test_record import FOLLOW_UP, LESSON, METRICS, ROUND, finished, make, migrate, put


def test_refusals(root: Path) -> None:
    """A bad record is refused, and nothing is written."""
    folder = make(root, "x")
    bad: list[tuple[str, str, dict[str, str]]] = [
        ("round", "missing major", {k: v for k, v in ROUND.items() if k != "major"}),
        ("round", "bad major: 'x'", ROUND | {"major": "x"}),
        ("round", "bad major: '-1'", ROUND | {"major": "-1"}),
        ("round", "unknown field colour", ROUND | {"colour": "red"}),
        ("follow-up", "bad finding: 'a | b'", FOLLOW_UP | {"finding": "a | b"}),
        ("follow-up", "bad why: ''", FOLLOW_UP | {"why": ""}),
        ("lesson", "bad summary: 'a\\nb'", LESSON | {"summary": "a\nb"}),
        ("colour", "unknown kind", {}),
    ]
    for kind, message, fields in bad:
        try:
            put(root, "x", kind, fields)
        except ValueError as error:
            assert message in str(error), (message, error)
        else:
            raise AssertionError(f"{kind} {message}: written")
    for pairs in (["line=forged"], ["legacy=true"], ["major"]):
        try:
            record.parse("round", pairs)
        except ValueError:
            continue
        raise AssertionError(f"{pairs} accepted")
    for change in ("nowhere", "..", ""):
        try:
            put(root, change, "skills", {"skills": "tdd"})
        except ValueError as error:
            assert "no change" in str(error), error
        else:
            raise AssertionError(f"change {change!r}: written")
    assert sorted(p.name for p in folder.iterdir()) == ["tasks.md", "verify-report.md"]  # no record


def test_writer(root: Path) -> None:
    """The sidecar line and the Markdown line together, in the one grammar."""
    folder = make(root, "x")
    legacy = {"kind": "lesson", "id": "L5", "line": "## [L5] pattern — old", "legacy": True}
    legacy |= dict.fromkeys(("when", "priority", "summary", "avoid", "class", "guard", "files"))
    (folder / "records.jsonl").write_text(json.dumps(legacy | {"round": None}) + "\n", "utf-8")
    (folder / "learnings.md").write_text(f"# Learnings: x\n\n{legacy['line']}\n", encoding="utf-8")
    migrate(root, "x")
    written = put(root, "x", "lesson", LESSON)
    assert written["id"] == "L6", written  # after the highest id, legacy included
    text = (folder / "learnings.md").read_text(encoding="utf-8")
    title = "## [L6] pattern — Summary: the cause, said once in a sentence that runs lon..."
    assert text.startswith(f"# Learnings: x\n\n{legacy['line']}\n\n{title}\n\n**When:** "), text
    assert "Class: 3 Same thing twice\nGuard: backend/tests/g.py\n" in text, text
    assert text.endswith("### Action\nAvoid: do it right. Self-check: run it.\n\n---\n"), text
    put(root, "x", "follow-up", FOLLOW_UP)
    put(root, "x", "follow-up", FOLLOW_UP | {"thread": "71"})
    follow_ups = (folder / "follow-ups.md").read_text(encoding="utf-8").splitlines()
    assert follow_ups[2] == "| PR | Thread | File | Finding | Why deferred |", follow_ups
    assert follow_ups[4:] == ["| 7 | 70 | `a.py` | f | w |", "| 7 | 71 | `a.py` | f | w |"]
    (folder / "status.md").write_text("# Status: x", encoding="utf-8")  # no final newline
    finished(root, "x")
    status = (folder / "status.md").read_text(encoding="utf-8")
    assert status.startswith("# Status: x\n\nReview round 1 (PR 7, base full): 0 blocker, "), status
    assert f"\n\n{record.METRICS_HEADER}\n|---|---|---|---|---|---|\n| 7 | x | 1 |" in status
    sidecar = record.read(folder)
    assert [r["kind"] for r in sidecar[:4]] == ["lesson", "lesson", "follow-up", "follow-up"]
    usage = "0 active · 11 turns · 84 s · 38200 in / 2100 out · 0 unmetered"
    assert sidecar[-6]["line"].endswith(usage), sidecar[-6]
    assert sidecar[-6]["major"] == 0
    assert record.unparsed(root) == [], record.unparsed(root)


def test_change_names(root: Path) -> None:
    """A change name is one path segment; one that climbs out is refused."""
    make(root, "x")
    outside = [root / "docs" / "plans", root / "scripts"]  # beside the changes folder
    for folder in outside:
        folder.mkdir(parents=True)
    for change in ("../../../docs/plans", "../../../scripts", "x/../x", "x/", "x/."):
        try:
            put(root, change, "skills", {"skills": "tdd"})
        except ValueError as error:
            assert "no change" in str(error), error
        else:
            raise AssertionError(f"change {change!r}: written")
    assert not [p for folder in outside for p in folder.iterdir()], "written outside the changes"
    assert not (root / record.changes_dir(root) / "x" / "records.jsonl").exists()


def test_table_head(root: Path) -> None:
    """A table's head is written once; a row under prose or another table gets its own."""
    folder = make(root, "x")
    put(root, "x", "metrics", METRICS)
    put(root, "x", "metrics", METRICS | {"pr": "8"})
    status = (folder / "status.md").read_text(encoding="utf-8")
    assert status.count(record.METRICS_HEADER) == 1, status
    assert status.endswith("|\n| 7 | x | 1 | 0/0/3 | 0/0/3 | 0 |\n| 8 | x | 1 | 0/0/3 |"
                           " 0/0/3 | 0 |\n"), status
    head = "| PR | Thread | File | Finding | Why deferred |\n|---|---|---|---|---|\n"
    (folder / "follow-ups.md").write_text("# x follow-ups\n\nSome prose.\n", encoding="utf-8")
    put(root, "x", "follow-up", FOLLOW_UP)
    put(root, "x", "follow-up", FOLLOW_UP | {"thread": "71"})
    rows = "| 7 | 70 | `a.py` | f | w |\n| 7 | 71 | `a.py` | f | w |\n"
    text = (folder / "follow-ups.md").read_text(encoding="utf-8")
    assert text == f"# x follow-ups\n\nSome prose.\n\n{head}{rows}", text
    assert record.unparsed(root) == [], record.unparsed(root)
    old = "| PR | Thread | Finding |\n|---|---|---|\n| 1 | 2 | b |\n"  # a legacy table's columns
    (folder / "follow-ups.md").write_text(old, encoding="utf-8")
    put(root, "x", "follow-up", FOLLOW_UP | {"thread": "72"})
    text = (folder / "follow-ups.md").read_text(encoding="utf-8")
    assert text == f"{old}\n{head}| 7 | 72 | `a.py` | f | w |\n", text


def test_usage() -> None:
    """Fewer than two arguments prints the usage and exits 2, as a CLI's misuse does."""
    for argv in ([], ["x"]):
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            code = record.main(argv)
        assert code == 2, (argv, code)
        assert said.getvalue().startswith("usage: dl record <change> <kind>"), said




def test_rerun_said(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The same record written twice is written once, and the second run says so."""
    folder = make(root, "x")
    runs = []
    for _ in range(2):
        assert record.main(["x", "skills", "skills=tdd"]) == 0
        runs.append(capsys.readouterr())
    assert [run.out for run in runs] == ["Skills used: tdd"+chr(10)] * 2, runs
    assert runs[0].err == "", runs[0].err
    assert "already on file" in runs[1].err, runs[1].err
    assert len(record.read(folder)) == 1


def test_paths_come_from_config(root: Path) -> None:
    """Nothing is written under the default folder: the project's `paths.changes` rules."""
    make(root, "x")
    put(root, "x", "skills", {"skills": "tdd"})
    assert (root / "work" / "log" / "x" / "records.jsonl").exists()
    assert not (root / ".dev-loop").exists()
