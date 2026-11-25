"""Recall says on stderr which record lines it skipped; `dl records --skipped` lists them."""

from __future__ import annotations

from pathlib import Path

import pytest
from dev_loop.records import review_records
from dev_loop.records.review_pitfalls import recall
from test_recall import CHANGES, plant
from test_review_pitfalls import root, write  # noqa: F401 - the project fixture


def broken(root: Path) -> None:
    plant(root)
    write(root / CHANGES / "broken/records.jsonl", '{"kind": "lesson"}\nnot json\n')


def test_recall_says_what_it_skipped(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broken(root)
    recall(root, "me", [])
    err = capsys.readouterr().err
    assert err == f"skipped 2 unparsed record lines in {CHANGES}/broken/records.jsonl\n"


def test_recall_is_silent_when_nothing_is_skipped(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plant(root)
    recall(root, "me", [])
    assert capsys.readouterr().err == ""


def test_skipped_lists_each_line(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    broken(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    assert review_records.main(["--skipped"]) == 0
    assert capsys.readouterr().out.split() == [f"{CHANGES}/broken/records.jsonl:{n}" for n in (1, 2)]
