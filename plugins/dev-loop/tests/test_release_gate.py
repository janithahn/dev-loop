"""B5: `dl ci release-gate` lists each open debt item with its severity, red on `block_on`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dev_loop import config
from dev_loop.ci import steps
from dev_loop.records import record

DEBT = {"pr": "7", "thread": "70", "file": "a.py", "finding": "f", "why": "w"}


def change(root: Path, name: str) -> Path:
    folder = record.changes_dir(root) / name
    folder.mkdir(parents=True)
    return folder


def put(root: Path, name: str, fields: dict[str, str]) -> dict[str, object]:
    pairs = [f"{key}={value}" for key, value in fields.items()]
    return record.write(root, name, record.parse("debt", pairs))


def gate(capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    capsys.readouterr()
    code = steps.main(["release-gate"])
    return code, capsys.readouterr().out


def test_the_default_blocks_on_blocker_only(root: Path) -> None:
    assert config.load(root).get("release_gate.block_on") == ["blocker"]


def test_no_debt_is_green(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    change(root, "x")
    assert gate(capsys) == (0, "no open debt\n")


def test_minor_only_is_green(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    change(root, "x")
    put(root, "x", DEBT | {"severity": "minor"})
    code, out = gate(capsys)
    assert code == 0, out
    assert "x: minor PR 7 thread 70 a.py: f" in out, out


def test_a_blocker_is_red(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    change(root, "x")
    change(root, "y")
    put(root, "x", DEBT | {"severity": "minor"})
    put(root, "y", DEBT | {"severity": "blocker", "thread": "71"})
    code, out = gate(capsys)
    assert code == 1, out
    assert "x: minor PR 7 thread 70" in out, out
    assert "y: blocker PR 7 thread 71" in out, out
    assert "1 of 2 open debt items block the release (block_on: blocker)" in out, out


def test_block_on_is_the_config(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (root / "dev-loop.yaml").write_text(
        "version: 1\npaths: {changes: work/log, archive: work/log/archive}\n"
        "release_gate: {block_on: [blocker, minor]}\n", encoding="utf-8")
    change(root, "x")
    put(root, "x", DEBT | {"severity": "minor"})
    assert gate(capsys)[0] == 1


def test_an_old_line_without_severity_reads_unknown(
        root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = change(root, "x")
    old = DEBT | {"kind": "debt", "pr": 7, "thread": 70, "line": "| 7 | 70 | a.py | f | w |"}
    (folder / "records.jsonl").write_text(json.dumps(old) + "\n", encoding="utf-8")
    (folder / "debt.md").write_text(
        f"# x debt\n\n{record.FOLLOW_UPS_HEADER}\n|---|---|---|---|---|\n{old['line']}\n",
        encoding="utf-8")
    assert record.unparsed(root) == []  # the old line still parses
    code, out = gate(capsys)
    assert code == 0, out
    assert "x: unknown PR 7 thread 70" in out, out


def test_closed_and_archived_debt_is_not_open(
        root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = change(root, "x")
    shut = DEBT | {"kind": "debt", "severity": "blocker", "closed": True}
    (folder / "records.jsonl").write_text(json.dumps(shut) + "\n", encoding="utf-8")
    archived = change(root, "archive/2026-01-01-old")
    (archived / "records.jsonl").write_text(json.dumps(shut | {"closed": False}) + "\n",
                                            encoding="utf-8")
    assert gate(capsys) == (0, "no open debt\n")


def test_a_severity_is_written_and_checked(root: Path) -> None:
    folder = change(root, "x")
    put(root, "x", DEBT | {"severity": "major"})
    text = (folder / "debt.md").read_text(encoding="utf-8")
    assert record.DEBT_HEADER in text, text
    assert "| 7 | 70 | a.py | major | f | w |" in text, text
    assert record.unparsed(root) == []
    with pytest.raises(ValueError, match="bad severity"):
        put(root, "x", DEBT | {"severity": "huge"})


def test_a_bad_block_on_is_red(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (root / "dev-loop.yaml").write_text(
        "version: 1\nrelease_gate: {block_on: [huge]}\n", encoding="utf-8")
    assert gate(capsys)[0] == 1
    with pytest.raises(ValueError, match="release_gate.block_on"):
        config.validate(config.load(root).values | {"release_gate": {"block_on": ["huge"]}})


CORRUPT = '{"kind": "debt", "severity": "blocker", "pr": "8", "thread": "80"'  # cut short


def test_a_corrupt_line_is_named_beside_a_blocker(
        root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = change(root, "x")
    put(root, "x", DEBT | {"severity": "blocker"})
    with (folder / "records.jsonl").open("a", encoding="utf-8") as sidecar:
        sidecar.write(CORRUPT + "\n")
    code, out = gate(capsys)
    assert code == 1, out
    assert "records.jsonl:2" in out, out


def test_a_corrupt_line_alone_is_red(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = change(root, "x")
    (folder / "records.jsonl").write_text(CORRUPT + "\n", encoding="utf-8")
    code, out = gate(capsys)
    assert code == 1, out
