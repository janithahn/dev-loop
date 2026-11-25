"""`-h`/`--help` on every `dl` entry point exits 0, prints usage, and never acts."""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PLUGIN / "src"), str(Path(__file__).parent)]

from dev_loop import __main__ as entry  # noqa: E402

FLAGS = ("-h", "--help")
# argv after `dl`: each subcommand alone, then the cases the defect report named, then nested
CASES = [[name] for name in sorted(entry.TABLE)] + [
    ["land", "5"], ["propose", "my-slug"], ["review", "context"], ["init", "discover"],
    ["records", "lessons"], ["ci", "flakes"], ["verify", "a-change"], ["pr", "a-change"],
    ["tasks", "show", "5"],
]


def _refuse(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("a help flag made a call")


def _snapshot(root: Path) -> dict[str, int]:
    return {str(path): path.stat().st_mtime_ns for path in root.rglob("*")}


@pytest.fixture
def sealed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty project and home; any process, connection or bind fails the test."""
    for name in ("CLAUDE_PROJECT_DIR", "HOME"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "Popen", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "bind", _refuse)
    (tmp_path / "keep.txt").write_text("x")
    return tmp_path


@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("argv", CASES, ids=" ".join)
def test_help_prints_usage_and_never_acts(
    argv: list[str], flag: str, sealed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _snapshot(sealed)
    try:
        code = entry.main([*argv, flag])
    except SystemExit as stop:  # argparse's own help
        code = stop.code
    out = capsys.readouterr().out
    assert code in (0, None)
    assert out.strip()
    assert _snapshot(sealed) == before


@pytest.mark.parametrize("flag", FLAGS)
def test_bare_help_lists_the_subcommands(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main([flag]) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in entry.TABLE)


def test_a_bad_argument_still_exits_2() -> None:
    assert entry.main(["verify"]) == 2
    assert entry.main(["pr"]) == 2
