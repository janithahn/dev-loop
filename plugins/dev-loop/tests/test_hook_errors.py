"""Every Python hook's own crash is said and logged: SystemExit too (dev-loop 0.4.18).

A hook fails open (a broken hook never blocks work) but never silently: one stderr line, and one
line in `~/.claude/dev-loop/hook-errors.jsonl` holding the hook, the error class and the hook's
innermost own frame; never prompt, tool or document text. A SystemExit (the config loader's
"PyYAML is missing") had escaped every `except Exception`, so nothing was logged.
Run: python -m pytest test_hook_errors.py -q (from the plugin's tests folder).
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop import hookenv

PLUGIN = Path(__file__).resolve().parents[1]
SECRET = "PLANTED-HOOK-SECRET-41"
FIELDS = {"at", "hook", "error", "frame"}
# hook, interpreter flags as registered, exit code on a crash, what the stderr line says happens
HOOKS = [
    ("context-gate", ["-S"], 1, "call allowed"),
    ("brief-contract", [], 1, "call allowed"),
    ("push-guard", ["-S"], 0, "command allowed"),
    ("approval", [], 1, "nothing written"),
    ("plan-recall", [], 1, "no recall"),
]


def run(tmp_path: Path, name: str, flags: list[str], stdin: str,
        extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"HOME": str(tmp_path / "home"), "CLAUDE_PROJECT_DIR": str(tmp_path)}
    env.pop("CLAUDE_PLUGIN_DATA", None)
    return subprocess.run(  # noqa: S603 - this interpreter and our own hook
        [sys.executable, *flags, str(PLUGIN / "hooks" / f"{name}.py")], input=stdin,
        capture_output=True, text=True, check=False, timeout=60, env=env | (extra or {}))


def logged(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "home" / ".claude" / "dev-loop" / "hook-errors.jsonl"
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


@pytest.mark.parametrize(("name", "flags", "code", "outcome"), HOOKS)
def test_a_crash_is_said_once_and_logged(
        tmp_path: Path, name: str, flags: list[str], code: int, outcome: str) -> None:
    done = run(tmp_path, name, flags, f"{{{SECRET}")
    assert done.returncode == code, done
    assert done.stderr.splitlines() == [f"{name}: JSONDecodeError, {outcome}"], done
    (row,) = logged(tmp_path)
    assert set(row) == FIELDS and (row["hook"], row["error"]) == (name, "JSONDecodeError"), row
    assert row["frame"] == f"{name}:main", row
    log = (tmp_path / "home" / ".claude" / "dev-loop" / "hook-errors.jsonl").read_text("utf-8")
    assert SECRET not in log + done.stdout + done.stderr


def tool_hooks() -> list[tuple[str, list[str]]]:
    """Every Python hook hooks.json runs on a tool call, with its interpreter flags."""
    events = json.loads((PLUGIN / "hooks" / "hooks.json").read_text("utf-8"))["hooks"]
    commands = [hook["command"] for event in ("PreToolUse", "PostToolUse")
                for group in events[event] for hook in group["hooks"]]
    found = (re.search(r'python3((?: -\w+)*) "[^"]*/hooks/([\w-]+)\.py"', c) for c in commands)
    return sorted({(run[2], tuple(run[1].split())) for run in found if run})  # type: ignore[misc]


def test_the_walk_finds_the_enforcing_hooks() -> None:
    names = {name for name, _ in tool_hooks()}
    assert {"rules-guard", "additive-tests", "context-gate", "push-guard"} <= names, names


@pytest.mark.parametrize(("name", "flags"), tool_hooks())
def test_every_tool_hook_routes_its_own_crash_through_the_shared_logger(
        tmp_path: Path, name: str, flags: list[str]) -> None:
    """Whatever hooks.json registers on a tool call: one line, one logged row, never a block."""
    done = run(tmp_path, name, list(flags), f"{{{SECRET}")
    assert done.returncode in (0, 1), done  # a hook's own crash never blocks the call
    (line,) = done.stderr.splitlines()
    assert line.startswith(f"{name}: JSONDecodeError, "), done
    (row,) = logged(tmp_path)
    assert (row["hook"], row["error"], row["frame"]) == (name, "JSONDecodeError", f"{name}:main")
    log = (tmp_path / "home" / ".claude" / "dev-loop" / "hook-errors.jsonl").read_text("utf-8")
    assert SECRET not in log + done.stdout + done.stderr


def test_a_system_exit_is_logged_with_its_reason(tmp_path: Path) -> None:
    """An empty plugin venv and no site-packages (`-S`): the config loader exits for want of
    PyYAML. The hook lets the call through, says why in its one line and logs SystemExit."""
    (tmp_path / "data" / "venv").mkdir(parents=True)
    (tmp_path / "dev-loop.yaml").write_text("version: 1\n", "utf-8")
    event = {"tool_name": "Agent", "tool_input": {"prompt": SECRET, "subagent_type": "x"}}
    done = run(tmp_path, "brief-contract", ["-S"], json.dumps(event),
               {"CLAUDE_PLUGIN_DATA": str(tmp_path / "data")})
    assert (done.returncode, done.stdout) == (1, ""), done
    said = ("brief-contract: SystemExit, call allowed "
            "(dev-loop config: PyYAML is missing: pip install pyyaml)")
    assert done.stderr.splitlines() == [said]
    (row,) = logged(tmp_path)
    assert (row["hook"], row["error"], row["frame"]) == (
        "brief-contract", "SystemExit", "config:<module>"), row


def test_an_unwritable_log_still_says_the_crash(tmp_path: Path) -> None:
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / ".claude").write_text("a file where the folder would go", "utf-8")
    done = run(tmp_path, "push-guard", ["-S"], "{")
    assert done.returncode == 0
    assert done.stderr.splitlines() == ["push-guard: JSONDecodeError, command allowed"], done


def test_a_log_that_fails_any_way_never_stops_the_hook(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def failing(_hook: str, _error: BaseException) -> None:
        raise ValueError

    monkeypatch.setattr(hookenv, "log", failing)
    hookenv.crashed("push-guard", KeyError(SECRET), "command allowed")  # it returns: no raise
    assert capsys.readouterr().err.splitlines() == ["push-guard: KeyError, command allowed"]


def test_only_the_config_loaders_own_exit_message_is_echoed(
        capsys: pytest.CaptureFixture[str]) -> None:
    """Any other exit text may quote a config value: the line then holds the class alone."""
    hookenv.crashed("approval", SystemExit(f"dev-loop.yaml: caps.enabled: {SECRET}"), "said")
    hookenv.crashed("approval", SystemExit(hookenv.NO_YAML), "said")
    assert capsys.readouterr().err.splitlines() == [
        "approval: SystemExit, said", f"approval: SystemExit, said ({hookenv.NO_YAML})"]
    for name in ("src/dev_loop/config.py", "hooks/context-gate.py"):  # one line, three places
        assert f'"{hookenv.NO_YAML}"' in (PLUGIN / name).read_text("utf-8"), name


def test_a_crash_said_in_a_test_is_logged_in_the_tests_own_home() -> None:
    """conftest gives each test a HOME; its teardown fails a test that grows the runner's log."""
    from conftest import RUNNER_LOG  # noqa: PLC0415 - the runner's log, read before any test

    assert hookenv.errors_file() != RUNNER_LOG
    hookenv.crashed("approval", ValueError(), "said")
    assert len(hookenv.errors_file().read_text("utf-8").splitlines()) == 1


def catches_everything(source: str) -> bool:
    """Return whether the source's last statement, or its main(), handles BaseException."""
    tree = ast.parse(source)
    mains = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == "main"]
    return any(
        isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name)
        and node.type.id == "BaseException"
        for boundary in [tree.body[-1], *mains] for node in ast.walk(boundary))


def test_no_python_hook_catches_less_than_base_exception() -> None:
    """The handler itself, at the hook's boundary: the word in a comment or a helper is none."""
    word_only = "try:\n    pass\nexcept Exception:  # not BaseException\n    pass\n"
    helper_only = ("def helper():\n    try:\n        pass\n"
                   "    except BaseException:\n        pass\n")
    assert not catches_everything(word_only)
    assert not catches_everything(helper_only + "helper()\n")
    assert catches_everything(helper_only.replace("helper", "main") + "main()\n")
    for hook in (PLUGIN / "hooks").glob("*.py"):
        assert catches_everything(hook.read_text("utf-8")), hook.name


def test_the_file_is_neutral() -> None:
    assert check([Path(__file__), PLUGIN / "src" / "dev_loop" / "hookenv.py"]) == []


def test_the_log_rotates_past_a_mebibyte(tmp_path: Path) -> None:
    folder = tmp_path / "home" / ".claude" / "dev-loop"
    folder.mkdir(parents=True)
    (folder / "hook-errors.jsonl").write_bytes(b"x" * ((1 << 20) + 1))
    run(tmp_path, "push-guard", ["-S"], "{")
    assert (folder / "hook-errors.1.jsonl").stat().st_size == (1 << 20) + 1
    assert len(logged(tmp_path)) == 1
    shutil.rmtree(folder)
