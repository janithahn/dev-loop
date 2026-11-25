"""`dl status`: computed per change, a verify that never ran is "not run", never green."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from dev_loop import status  # noqa: E402
from test_neutral import check  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[1]
TASKS = "### T1: a\n- Status: done\n### T2: b\n- Status: todo\n"
ROUND = {"kind": "round", "round": 1, "pr": 3, "base": "x", "blocker": 0, "major": 1, "minor": 2,
         "fixed": 1, "deferred": 2, "active": 0}


def change(root: Path, name: str, tasks: str = TASKS, records: list[dict] | None = None) -> Path:
    folder = root / "work/log" / name
    folder.mkdir(parents=True)
    (folder / "tasks.md").write_text(tasks, encoding="utf-8")
    if records is not None:
        lines = "".join(json.dumps(r) + "\n" for r in records)
        (folder / "records.jsonl").write_text(lines, encoding="utf-8")
    return folder


def test_counts_and_not_run(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    follow = {"kind": "follow-up", "pr": 3, "thread": 1, "file": "f", "finding": "m", "why": "w"}
    change(root, "alpha", records=[ROUND, follow])
    assert status.main([]) == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "tasks 1/2" in out
    assert "verify: not run" in out and "green" not in out
    assert "round 1: 0/1/2" in out and "follow-ups open: 1" in out


def test_verify_result_and_done(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = change(root, "beta", "### T1: a\n- Status: done\n", [
        {"kind": "verify", "result": "green", "head": "abc"}])
    status.main([])
    assert "verify: green" in capsys.readouterr().out
    assert status.gaps(folder)  # lines still missing: not done


def test_status_counts_parts(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from test_record import finished, make  # noqa: PLC0415

    tasks = ("## Part a\n### T1: a\n- Status: done\n### T2: b\n- Status: done\n"
             "## Part b\n### T3: c\n- Status: todo\n")
    make(root, "delta", tasks)
    finished(root, "delta")
    status.main(["delta"])
    out = capsys.readouterr().out
    assert "tasks a 2/2, b 0/1" in out and "done (part a)" in out, out


def test_foreign_folder_warns(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    change(root, "gamma")
    status.main([])
    assert "no records.jsonl" in capsys.readouterr().out


def test_files_neutral() -> None:
    files = ["src/dev_loop/status.py", "skills/status/SKILL.md", "skills/done/SKILL.md"]
    assert check([PLUGIN / f for f in files]) == []


def test_debt_counted(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """V4a: the debt a fast-track review left shows next to the follow-ups."""
    debt = {"kind": "debt", "pr": 3, "thread": 2, "file": "f", "finding": "m", "why": "w"}
    change(root, "gamma", records=[ROUND, debt, debt | {"thread": 4}])
    change(root, "delta", records=[ROUND])
    assert status.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "follow-ups open: 0; debt open: 2" in out[1], out
    assert "debt open: 0" in out[0], out
