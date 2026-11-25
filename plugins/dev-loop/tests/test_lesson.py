"""`dl lesson`: a review lesson is refused without its fields; a good one goes through the writer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_neutral
from dev_loop.records import lesson, record

PITFALLS = "| # | Class | Seen | Example | Guard |\n|---|---|---|---|---|\n"
PITFALLS += "| 3 | Same thing twice | 4 | x | `tests/g.py` |\n"


def project(root: Path) -> Path:
    """A change `c`, a guard and a file on disk, and the pitfalls file the config names."""
    with (root / "dev-loop.yaml").open("a", encoding="utf-8") as config:
        config.write("knowledge:\n  pitfalls: notes/PITFALLS.md\n")
    (root / "notes").mkdir()
    (root / "notes" / "PITFALLS.md").write_text(PITFALLS, encoding="utf-8")
    for path in ("tests/g.py", "src/a.py", "src/b.py"):
        (root / path).parent.mkdir(exist_ok=True)
        (root / path).write_text("", encoding="utf-8")
    folder = record.changes_dir(root) / "c"
    folder.mkdir(parents=True)
    return folder


def args(**over: str) -> list[str]:
    fields = {"Summary": "the cause, said once", "Avoid": "do it right. Self-check: run it.",
              "Class": "3", "Guard": "tests/g.py", "Files": "src/a.py src/b.py", "Round": "1"}
    fields |= over
    return ["c", "high", *(f"{key}: {value}" for key, value in fields.items() if value != "-")]


def test_a_good_lesson_is_written_once_with_its_row_text(root: Path) -> None:
    folder = project(root)
    assert lesson.main(args()) == 0
    [written] = [json.loads(x) for x in (folder / "records.jsonl").read_text().splitlines()]
    assert written["kind"] == "lesson"
    assert written["class"] == "3 Same thing twice"
    assert (written["files"], written["round"], written["id"]) == ("src/a.py src/b.py", 1, "L1")
    assert "Class: 3 Same thing twice" in (folder / "learnings.md").read_text()
    assert record.unparsed(root) == []


def test_a_new_class_is_kept_as_named(root: Path) -> None:
    folder = project(root)
    assert lesson.main(args(Class="new: a fresh cause", Guard="review: no test reaches it")) == 0
    assert "Class: new: a fresh cause" in (folder / "learnings.md").read_text()


@pytest.mark.parametrize("over", [
    {"Class": "-"}, {"Guard": "-"}, {"Files": "-"}, {"Round": "-"},
    {"Class": "9"}, {"Class": "three"}, {"Guard": "tests/none.py"}, {"Guard": "maybe"},
    {"Files": "src/none.py"}, {"Files": " "}, {"Files": "src/*.py"},
    {"Round": "0"}, {"Round": "x"},
])
def test_a_bad_lesson_is_refused_and_nothing_written(
    root: Path, over: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    folder = project(root)
    assert lesson.main(args(**over)) == 1
    assert capsys.readouterr().err.startswith("dl lesson: refused: ")
    assert not (folder / "records.jsonl").exists()
    assert not (folder / "learnings.md").exists()


def test_bad_priority_order_or_change_is_refused(root: Path) -> None:
    project(root)
    assert lesson.main(["c", "urgent", *args()[2:]]) == 1
    assert lesson.main(["c", "high", *reversed(args()[2:])]) == 1
    assert lesson.main(["none", *args()[1:]]) == 1


def test_no_pitfalls_file_refuses_a_numbered_class(root: Path) -> None:
    project(root)
    (root / "notes" / "PITFALLS.md").unlink()
    assert lesson.main(args()) == 1


def test_usage_exits_2(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert lesson.main(["c"]) == 2
    assert capsys.readouterr().err.startswith("usage: dl lesson")


def test_written_files_are_neutral() -> None:
    here = Path(__file__)
    assert test_neutral.check([here, here.parents[1] / "src/dev_loop/records/lesson.py"]) == []


def test_the_missing_file_is_named(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project(root)
    assert lesson.main(args(Files="src/a.py src/none.py")) == 1
    assert "Files: path not found: src/none.py" in capsys.readouterr().err


def test_refused_on_the_local_host_before_a_review(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = project(root)
    with (root / "dev-loop.yaml").open("a", encoding="utf-8") as config:
        config.write("host:\n  provider: local\n")
    with pytest.raises(SystemExit) as stop:
        lesson.main(args())
    assert stop.value.code == 2
    assert "no clean review-run on c" in capsys.readouterr().err
    assert not (folder / "records.jsonl").exists()
    run = {"kind": "review-run", "pr": "c2425df1f07d", "head": "a" * 40, "base": "full", "rc": 0}
    with (folder / "host.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(run) + "\n")
    assert lesson.main(args()) == 0
    assert "L1" in (folder / "records.jsonl").read_text()


def test_files_take_commas_and_are_stored_space_joined(root: Path) -> None:
    folder = project(root)
    assert lesson.main(args(Files="src/a.py, src/b.py")) == 0
    [written] = [json.loads(x) for x in (folder / "records.jsonl").read_text().splitlines()]
    assert written["files"] == "src/a.py src/b.py"


def test_a_missing_comma_path_is_named_without_its_comma(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project(root)
    assert lesson.main(args(Files="src/a.py,src/gone.py")) == 1
    err = capsys.readouterr().err
    assert "spaces or commas" in err
    assert "not found: src/gone.py (" in err and "gone.py," not in err
