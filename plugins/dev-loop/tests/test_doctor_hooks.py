"""`dl doctor`'s hook rows: red on a hook or gate error of the last 24 h, and the self-test's row.

A hook fails open, so its error is otherwise seen by nobody: the row names the hook, the error
class and the count, never text. Every case has its own HOME (the `proj` fixture's).
Run: python -m pytest test_doctor_hooks.py -q (from the plugin's tests folder).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_doctor import proj  # noqa: F401 - the fixture
from test_neutral import check

from dev_loop import doctor, doctor_hooks

PLUGIN = Path(__file__).resolve().parents[1]
HOUR = 3600
SECRET = "PLANTED DOCTOR HOOK SECRET"
REAL = (doctor_hooks.gate_state, doctor_hooks.self_test_row)  # before `proj` stands in for them


def log(home: Path, name: str, **row: object) -> None:
    folder = home / ".claude" / "dev-loop"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / name).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def test_a_fresh_hook_error_is_red_and_names_hook_class_and_count(
        proj: Path, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: F811
    assert doctor_hooks.errors_row() == ("hook errors", True, "none in the last 24 h")
    for _ in range(2):
        log(proj, "hook-errors.jsonl", at=time.time() - HOUR, hook="brief-contract",
            error="SystemExit", frame="config:<module>")
    name, ok, detail = doctor_hooks.errors_row()
    assert (name, ok) == ("hook errors", False)
    assert "2 in the last 24 h: brief-contract SystemExit x2" in detail, detail
    assert doctor.main([]) == 1
    assert "RED  hook errors" in capsys.readouterr().out


def test_an_error_older_than_a_day_is_green(proj: Path) -> None:  # noqa: F811
    log(proj, "hook-errors.jsonl", at=time.time() - 25 * HOUR, hook="push-guard", error="OSError")
    log(proj, "gate-errors.jsonl", at=time.time() - 25 * HOUR, error="ValueError")
    assert doctor_hooks.errors_row()[1] is True
    assert doctor.main([]) == 0


def test_a_gate_error_is_red_and_its_crash_counts_once(proj: Path) -> None:  # noqa: F811
    now = time.time()
    log(proj, "gate-errors.jsonl", at=now, session_id="s", agent_id="a", error="SystemExit")
    log(proj, "hook-errors.jsonl", at=now, hook="context-gate", error="SystemExit")  # the same
    detail = "1 in the last 24 h: context-gate SystemExit x1 (clears a day after the last)"
    assert doctor_hooks.errors_row()[1:] == (False, detail)


def test_a_log_line_never_puts_text_in_the_row(proj: Path) -> None:  # noqa: F811
    log(proj, "hook-errors.jsonl", at=time.time(), hook=SECRET, error=f"{SECRET}!")
    (proj / ".claude" / "dev-loop" / "hook-errors.jsonl").open("a").write("{cut\n[]\n")
    detail = doctor_hooks.errors_row()[2]
    assert "unknown unknown x1" in detail and SECRET not in detail, detail


def test_the_self_test_row_is_green_then_names_a_broken_hook(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copy = tmp_path / "installed" / "dev-loop"
    for part in ("hooks", "src", "bin"):
        shutil.copytree(PLUGIN / part, copy / part, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    for ambient in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(ambient, raising=False)
    # 7, was 6: the self-test's list grew by design (the rules guard enforces, so it is tested)
    assert doctor_hooks.self_test_row(copy) == ("hook self-test", True, "7 hooks ok")
    (copy / "hooks" / "branch-guard.sh").write_text("exit 1\n", "utf-8")
    doctor_hooks.self_test_lines.cache_clear()  # as each run of doctor does
    assert doctor_hooks.self_test_row(copy) == ("hook self-test", False, "broken: branch-guard")
    (copy / "hooks" / "self-test.py").unlink()
    doctor_hooks.self_test_lines.cache_clear()
    assert doctor_hooks.self_test_row(copy) == ("hook self-test", False, "did not run")


def test_a_venv_without_pyyaml_turns_the_gate_row_red(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real self-test, no stand-in: a gate that cannot judge is never a green row."""
    (tmp_path / "data" / "venv").mkdir(parents=True)  # a venv holding no PyYAML
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # no heartbeat: the gate never ran here
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    for ambient in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(ambient, raising=False)
    name, ok, detail = doctor._context_gate_row()  # noqa: SLF001 - the row under test
    assert (name, ok) == ("context gate", False), detail
    assert "hook self-test names the gate broken" in detail, detail
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "none"))  # no venv: the system's
    doctor_hooks.self_test_lines.cache_clear()  # as each run of doctor does
    row = doctor._context_gate_row()  # noqa: SLF001 - the row under test
    assert row == ("context gate", True, "registered, self-check ok")


def test_one_doctor_run_runs_the_self_test_once(
        proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """The gate row, the self-test row and the JSON's gate row read one run; the next asks anew."""
    runs: list[object] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        runs.append(command)
        return subprocess.CompletedProcess(command, 0, "ok context-gate\n", "")

    monkeypatch.setattr(doctor_hooks, "gate_state", REAL[0])
    monkeypatch.setattr(doctor_hooks, "self_test_row", REAL[1])
    fake = SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)
    monkeypatch.setattr(doctor_hooks, "subprocess", fake)
    listed = {row["name"]: row["status"] for row in doctor.json_rows(proj)}
    assert listed["hook self-test"] == "ok" and len(runs) == 1, (listed, runs)
    doctor.rows(proj)
    assert len(runs) == 2  # noqa: PLR2004 - a second run of doctor, a second self-test


def test_doctor_lists_both_rows(proj: Path) -> None:  # noqa: F811
    names = [row["name"] for row in doctor.json_rows(proj)]
    assert "hook errors" in names and "hook self-test" in names, names


def test_the_files_are_neutral() -> None:
    files = [Path(__file__), PLUGIN / "src" / "dev_loop" / "doctor_hooks.py"]
    assert check(files) == []
