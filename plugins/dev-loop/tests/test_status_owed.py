"""`dl status` names a recheck round owed after a round with a blocker or major."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_status import ROUND, TASKS, change  # noqa: E402

from dev_loop import status  # noqa: E402

DONE = "### T1: a\n- Status: done\n"
OWED = "owed: review round 2 on PR 3"
OWING = ROUND | {"minor": 0}  # round 1 on PR 3: 0/1/0


def test_a_round_with_a_major_owes_the_next(root: Path) -> None:
    text = status.line(change(root, "a", DONE, [OWING]))
    assert f"debt open: 0; {OWED}" in text
    assert text.index(OWED) < text.index("; missing:")


def test_owed_while_a_task_is_still_todo(root: Path) -> None:
    assert OWED in status.line(change(root, "a", TASKS, [OWING]))


def test_a_clean_round_owes_nothing(root: Path) -> None:
    clean = ROUND | {"major": 0}  # 0/0/2
    assert "owed" not in status.line(change(root, "a", DONE, [clean]))


def test_fast_track_owes_no_recheck(root: Path) -> None:
    path = root / "dev-loop.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "review: {fast_track: true}\n",
                    encoding="utf-8")
    assert "owed" not in status.line(change(root, "a", DONE, [OWING]))


def test_no_round_owes_nothing(root: Path) -> None:
    assert "owed" not in status.line(change(root, "a", DONE, []))


def test_a_legacy_round_with_no_counts_lists_every_change(
        root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    change(root, "a", DONE, [{"kind": "round", "legacy": True, "round": 1, "pr": 3}])
    change(root, "b", DONE, [OWING])
    assert status.main([]) == 0
    out = capsys.readouterr().out
    assert "a: tasks 1/1" in out and "owed" not in out.splitlines()[0] and OWED in out


def test_a_legacy_round_with_counts_but_no_number_lists_every_change(
        root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    change(root, "a", DONE, [{"kind": "round", "legacy": True, "round": None, "pr": 3,
                              "blocker": 0, "major": 2}])
    assert status.main([]) == 0
    assert "owed: review round next on PR 3" in capsys.readouterr().out
