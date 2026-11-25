"""`dl verify`: coverage judged from a stacked part's parent; the report first, then the record."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_verify import _env, changed, git, project, run  # noqa: F401 - _env is the fixture

from dev_loop import verify

Err = pytest.CaptureFixture[str]


def stacked(root: Path, *, remember: bool) -> Path:
    """origin has main and `parent` (adding web/a.ts); HEAD is `child` off it, adding src/b.py."""
    folder = changed(root, {"lint": "-c pass"}, {"lint": "src/**"}, "web/a.ts")
    git(root, "checkout", "-qb", "parent")
    git(root, "push", "-q", "origin", "parent")
    git(root, "checkout", "-qb", "child")
    (root / "src").mkdir()
    (root / "src" / "b.py").write_text("x\n", encoding="utf-8")
    git(root, "add", "src/b.py")
    git(root, "commit", "-qm", "child")
    if remember:
        git(root, "config", "branch.child.dl-base", "parent")
    return folder


def test_a_stacked_part_is_judged_from_its_remembered_parent(tmp_path: Path) -> None:
    folder = stacked(tmp_path, remember=True)
    code, report, records = run(folder)
    assert code == 0 and records[-1]["result"] == "green"
    assert "| coverage | passed |" in report


def test_with_no_remembered_parent_the_integration_branch_rules(tmp_path: Path) -> None:
    folder = stacked(tmp_path, remember=False)
    code, report, records = run(folder)
    assert code == 1 and records[-1]["result"] == "red"
    assert "| coverage | not run: no step covers web/a.ts |" in report


def test_an_unrelated_integration_history_is_not_run_never_green(tmp_path: Path) -> None:
    folder = changed(tmp_path, {"lint": "-c pass"}, {"lint": "src/**"}, "src/b.py")
    git(tmp_path, "checkout", "-q", "--orphan", "other")
    git(tmp_path, "commit", "-qm", "orphan")
    git(tmp_path, "push", "-qf", "origin", "other:main")
    git(tmp_path, "checkout", "-q", "main")
    code, report, records = run(folder)
    assert code == 1 and records[-1]["result"] == "red"
    assert "| coverage | not run: no merge base with origin/main |" in report


def test_a_report_that_cannot_be_written_writes_no_record(tmp_path: Path, capsys: Err) -> None:
    folder = project(tmp_path, {"unit": "-c pass"})
    (folder / "verify-report.md").unlink()
    (folder / "verify-report.md").mkdir()
    assert verify.main(["c1"]) == 1
    assert f"dl verify: cannot write {folder / 'verify-report.md'}: " in capsys.readouterr().err
    records = folder / "records.jsonl"
    assert not records.exists() or '"verify"' not in records.read_text(encoding="utf-8")


def test_a_held_record_lock_leaves_no_new_report(tmp_path: Path) -> None:
    folder = project(tmp_path, {"unit": "-c pass"})
    (folder / "verify-report.md").unlink()
    (folder / ".record.lock").write_text(f"{os.getpid()} held", encoding="utf-8")
    assert verify.main(["c1"]) == 1
    assert not (folder / "verify-report.md").exists()
