"""The SessionEnd clean hook: run `dl clean --apply` at most once per 24h, silent unless it removed.

A stub `dl` on CLAUDE_PLUGIN_ROOT/bin records its calls; the hook is driven through its env.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from test_neutral import check

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "session-end-clean.sh"

STUB = """#!/bin/sh
echo "$1 $2" >> "$CALLS"
case "$1 $2" in
  "config get") echo "${AUTO:-true}" ;;
  "clean --apply") printf '%s\\n' "${CLEAN_OUT:-clean: removed nothing}" ;;
esac
"""


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    (plugin / "bin" / "dl").write_text(STUB)
    (plugin / "bin" / "dl").chmod(0o755)
    project = tmp_path / "project"
    project.mkdir()
    calls, stamp = tmp_path / "calls", tmp_path / "stamp"
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setenv("DEV_LOOP_CLEAN_STAMP", str(stamp))
    monkeypatch.setenv("CALLS", str(calls))
    return {"calls": calls, "stamp": stamp}


def _run() -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sh", str(HOOK)], capture_output=True, text=True, check=False)


def test_runs_once_then_skips_within_24h(world: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLEAN_OUT", "clean: removed 1 item(s), 0.2 MB")
    done = _run()
    assert done.returncode == 0
    assert "removed 1 item" in done.stderr  # it removed something: not silent
    assert world["stamp"].exists()
    assert world["calls"].read_text().count("clean --apply") == 1
    _run()  # a second session-end within 24h
    assert world["calls"].read_text().count("clean --apply") == 1  # stamp held it off


def test_runs_again_after_24h(world: dict[str, Path]) -> None:
    _run()
    old = time.time() - 25 * 3600
    world["stamp"].write_text(str(int(old)))
    os.utime(world["stamp"], (old, old))
    _run()
    assert world["calls"].read_text().count("clean --apply") == 2


def test_silent_when_nothing_removed(world: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLEAN_OUT", "clean: removed nothing")
    done = _run()
    assert done.stderr == ""  # silent
    assert world["calls"].read_text().count("clean --apply") == 1  # but it did run


def test_auto_false_turns_it_off(world: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO", "false")
    _run()
    assert "clean --apply" not in world["calls"].read_text()
    assert not world["stamp"].exists()


def test_hook_is_neutral() -> None:
    assert check([HOOK, Path(__file__)]) == []
