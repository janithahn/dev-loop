"""`dl records repeat`: the old repeat rate beside the redefined one, on two changes' records.

Old: a round-1 lesson repeats when its class has a row. New: only when the builder was shown a
lesson of its class (a `recall` record) or a guard for the class, landed on the integration
branch before the lesson, covers one of its files; two review passes reporting one problem
(same change, round, class and files) count once. Fixtures are synthetic.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from dev_loop.records.review_records import main
from dev_loop.records.review_repeat import covers, rates
from test_neutral import check
from test_review_records import CHANGES, DOC, PITFALLS, configure, lesson, write

WEEK = "2026-W41"


def plant(root: Path, change: str, *lessons: tuple[str, str, str, int]) -> None:
    """Write a change's lessons: (when, class, files, round) each, ids L1, L2, ..."""
    blocks = []
    for number, (when, class_, files, round_) in enumerate(lessons, start=1):
        detail = ("Summary: q", f"Class: {class_}", f"Files: {files}", f"Round: {round_}")
        blocks.append(lesson(when, *detail).replace("[L1]", f"[L{number}]"))
    write(root / CHANGES / change / "learnings.md", "\n".join(blocks))


def recall(root: Path, change: str, shown: str) -> None:
    made = {"kind": "recall", "change": change, "shown": shown}
    write(root / CHANGES / change / "records.jsonl", json.dumps(made) + "\n")


@pytest.fixture
def two(tree: object) -> Path:
    """Change `a` logs one class-9 lesson; change `b` logs two, one reported by both passes."""
    root: Path = tree.root  # type: ignore[attr-defined]
    configure(root)
    write(root / PITFALLS, DOC)  # class 3 guarded by checks/guard_a.py; class 9 review only
    plant(root, "a", ("2026-10-05 08:00", "9", "lib/x.py", 1))
    twice = ("2026-10-06 08:00", "9", "lib/y.py", 1)
    plant(root, "b", twice, twice, ("2026-10-06 09:00", "9", "lib/z.py", 2))
    return root


def row(table: list[str], week: str = WEEK) -> str:
    return next(line for line in table if line.startswith(f"| {week} "))


def test_a_shown_lesson_that_recurs_counts(two: Path) -> None:
    recall(two, "a", "")  # a's builder was shown nothing: decided, not unknown
    recall(two, "b", "a:L1")  # b's builder saw a's class-9 lesson, and b repeats class 9
    assert row(rates(two)) == f"| {WEEK} | 3 | 3 of 3 (100%) | 2 | 1 of 2 (50%) |"


def test_an_unshown_lesson_does_not_count(two: Path) -> None:
    recall(two, "a", "")
    recall(two, "b", "")  # nothing shown, no guard: class 9 recurring is not a repeat
    assert row(rates(two)) == f"| {WEEK} | 3 | 3 of 3 (100%) | 2 | 0 of 2 (0%) |"


def test_two_passes_reporting_one_finding_count_once(two: Path) -> None:
    recall(two, "a", "")
    recall(two, "b", "a:L1")
    table = rates(two)
    assert table[0].startswith("| Week | Round-1 lessons | Old"), table
    assert "| 2 | 1 of 2 (50%) |" in row(table)  # b's twin lessons are one finding, a repeat


def test_no_recall_record_reads_not_run_never_a_rate(two: Path) -> None:
    recall(two, "a", "")
    assert row(rates(two)).endswith("| 2 | not_run (1 of 2 unknown) |")  # b: no record


def git(root: Path, *args: str, **env: str) -> None:
    run = ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args]
    subprocess.run(run, cwd=root, check=True, capture_output=True, env={**os.environ, **env})


def test_a_guard_on_the_integration_branch_before_the_lesson_counts(two: Path) -> None:
    """Class 3's guard landed on origin/<integration> in 2026-09: a later class-3 lesson repeats."""
    write(two / "checks/guard_a.py", "")
    git(two, "init", "-q", "-b", "trunk")
    git(two, "add", "checks")
    git(two, "commit", "-qm", "guard", GIT_AUTHOR_DATE="2026-09-01T00:00:00Z")
    git(two, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    config = json.loads((two / "dev-loop.yaml").read_text(encoding="utf-8"))
    write(two / "dev-loop.yaml", json.dumps({**config, "branches": {"integration": "trunk"}}))
    plant(two, "c", ("2026-10-07 08:00", "3", "checks/w.py", 1))
    plant(two, "d", ("2026-10-07 09:00", "3", "lib/w.py", 1))  # outside the guard's folder
    for change in ("a", "b", "d"):
        recall(two, change, "")  # shown nothing: only the guard can make a repeat
    assert row(rates(two)) == f"| {WEEK} | 5 | 5 of 5 (100%) | 4 | 1 of 4 (25%) |"  # c only


def test_a_guard_covers_its_folder_or_the_folder_above_its_tests() -> None:
    assert covers("checks/guard_a.py", "checks/w.py")
    assert not covers("checks/guard_a.py", "lib/w.py")
    assert covers("app/tests/guards/test_a.py", "app/src/m.py")
    assert covers("tests/test_a.py", "anything/at/all.py")


def test_the_command_prints_the_table(
    two: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(two))
    assert main(["repeat"]) == 0
    assert f"| {WEEK} | 3 |" in capsys.readouterr().out


def test_this_task_leaves_its_files_neutral() -> None:
    src = Path(__file__).parents[1] / "src/dev_loop/records/review_repeat.py"
    assert check([src, Path(__file__)]) == []
