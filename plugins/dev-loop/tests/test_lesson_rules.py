"""`dl lesson` rewrites only the guides its lesson changes; `rules.generator` replaces the writer."""

from __future__ import annotations

from pathlib import Path

import pytest
from dev_loop.records import lesson, review_records
from dev_loop.records.review_rules import write
from test_review_pitfalls import root  # noqa: F401 - the project fixture
from test_review_rules import AREA, CASE, FRONT, PITFALLS, plant, write_text

ARGS = ["c", "high", "Summary: s", "Avoid: x. Self-check: y.", "Class: 3", "Guard: tests/g.py",
        f"Files: {AREA}/x.py", "Round: 1"]


def logged(root: Path, monkeypatch: pytest.MonkeyPatch, extra: str = "") -> None:
    plant(root)
    config = root / "dev-loop.yaml"
    text = config.read_text().replace("knowledge:\n", f"knowledge:\n  pitfalls: {PITFALLS}\n")
    config.write_text(text + extra, encoding="utf-8")
    for path in (f"{AREA}/x.py", "tests/g.py"):
        write_text(root / path, "")
    write_text(root / ".dev-loop/changes/c/proposal.md", "p")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))


def test_a_lesson_rewrites_only_its_own_guide(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logged(root, monkeypatch)
    write(root)
    other = root / FRONT / "CLAUDE.md"
    edited = other.read_text().replace("- None in the window.", "- Hand edited.")
    other.write_text(edited, encoding="utf-8")
    before = {p: (root / p / "CLAUDE.md").read_bytes() for p in (FRONT, CASE)}
    mine = (root / AREA / "CLAUDE.md").read_bytes()
    assert lesson.main(ARGS) == 0
    assert (root / AREA / "CLAUDE.md").read_bytes() != mine
    assert {p: (root / p / "CLAUDE.md").read_bytes() for p in (FRONT, CASE)} == before


def test_a_generator_command_replaces_the_writer(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    logged(root, monkeypatch, 'rules:\n  generator: "echo ran > gen.txt; exit 3"\n')
    before = (root / AREA / "CLAUDE.md").read_bytes()
    assert review_records.main(["rules", "--write"]) == 3
    assert (root / "gen.txt").read_text().strip() == "ran"
    assert "rules.generator exited 3" in capsys.readouterr().err
    assert (root / AREA / "CLAUDE.md").read_bytes() == before, "the built-in writer did not run"
    (root / "gen.txt").unlink()
    assert lesson.main(ARGS) == 0
    assert (root / "gen.txt").exists(), "dl lesson runs the generator too"
    assert (root / AREA / "CLAUDE.md").read_bytes() == before


def test_the_check_command_is_the_staleness_check_under_a_generator(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    logged(root, monkeypatch, 'rules:\n  generator: "true"\n  check: "echo seen > chk.txt; exit 4"\n')
    assert review_records.main(["rules"]) == 4
    assert (root / "chk.txt").exists()
    assert "rules.check exited 4" in capsys.readouterr().err


def test_a_generator_without_a_check_says_so_and_passes(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    logged(root, monkeypatch, 'rules:\n  generator: "true"\n')
    assert review_records.main(["rules"]) == 0
    assert capsys.readouterr().out == (
        "rules: blocks are written by rules.generator; no built-in staleness check\n")


def test_no_generator_keeps_the_built_in_check(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    logged(root, monkeypatch)
    assert review_records.main(["rules"]) == 1
    assert "stale (run dl records rules --write)" in capsys.readouterr().out
