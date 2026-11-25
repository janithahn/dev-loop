"""A field value cannot quote more than 40 characters (dl record and dl lesson)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_loop.records import lesson, record
from test_lesson import args, project

LONG, EDGE = "x" * 41, "x" * 40


LONG_WORDS = "x " * 21  # 42 characters with whitespace


@pytest.mark.parametrize("wrap", ['"{}"', "“{}”", "``{}``", "`{}`"])
def test_record_refuses_a_long_quote_and_names_the_field(
    root: Path, wrap: str, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = project(root)
    assert record.main(["c", "budget", "role=builder", "remains=see " + wrap.format(LONG_WORDS)]) == 1
    err = capsys.readouterr().err
    assert err.strip() == ("dl record: refused: remains quotes more than 40 characters: say it in "
                           "your own words (ids, rules and code names only) "
                           "(a long command: name the test or script instead)")
    assert not (folder / "records.jsonl").exists()
    assert not (folder / "status.md").exists()


def test_record_passes_a_quote_of_40(root: Path) -> None:
    project(root)
    assert record.main(["c", "budget", "role=builder", 'remains="' + EDGE + '" and `src/a.py`']) == 0


def test_lesson_refuses_a_long_quote_and_writes_nothing(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = project(root)
    assert lesson.main(args(Summary=f'cause "{LONG}"')) == 1
    err = capsys.readouterr().err
    assert "refused: summary quotes more than 40 characters" in err and LONG not in err
    assert not (folder / "records.jsonl").exists()
    assert not (folder / "learnings.md").exists()


def test_lesson_passes_a_quote_of_40(root: Path) -> None:
    project(root)
    assert lesson.main(args(Summary=f'cause "{EDGE}"')) == 0


@pytest.mark.parametrize("wrap", ['"{}"', "“{}”", "`{}`"])
def test_record_passes_a_span_of_40_in_every_style(root: Path, wrap: str) -> None:
    project(root)
    words = ("x " * 20)[:40]
    assert record.main(["c", "budget", "role=builder", "remains=" + wrap.format(words)]) == 0


@pytest.mark.parametrize("word", ["src/dev_loop/records/some/deep/path/to/a_file_name.py",
                                  "tests/test_a.py::TestSomething::test_a_long_node_id[param-1]"])
def test_a_one_word_backtick_span_passes_at_any_length(root: Path, word: str) -> None:
    project(root)
    assert len(word) > 40
    assert record.main(["c", "budget", "role=builder", f"remains=run `{word}`"]) == 0


def test_a_refused_follow_up_writes_nothing(root: Path) -> None:
    folder = project(root)
    fields = ["pr=1", "thread=2", "file=a.py", f'finding="{LONG_WORDS}"', "why=w"]
    assert record.main(["c", "follow-up", *fields]) == 1
    assert not (folder / "follow-ups.md").exists()
    assert not (folder / "records.jsonl").exists()


def test_a_lesson_class_row_reference_is_not_checked() -> None:
    from dev_loop.records import record_quote
    record_quote.quoted({"class": f'3 Row text "{LONG_WORDS}"'})
