"""The edit hooks and the done guard: config-driven, rooted at the project, neutral.

A PostToolUse or Stop hook reaches the model only by exiting 2 with its text on stderr; the silent
case is exit 0 with no output. guard-on-edit runs `gates.on_edit` with the edited file; none set
means silent. done-guard's own cases are in test-done-guard.sh, run here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_neutral import check

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="sh is absent")

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"
MINE = ["guard-on-edit.sh", "post-edit-check.sh", "done-guard.sh"]


def run(hook: str, project: Path, file: Path) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(file)}})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project), "DL_PYTHON": sys.executable}
    env.pop("CLAUDE_FILE_PATHS", None)
    return subprocess.run(  # noqa: S603 - our own hook, fixed arguments
        [shutil.which("sh") or "sh", str(HOOKS / hook)],
        input=payload, capture_output=True, text=True, check=False, env=env, cwd=project,
    )


def silent(result: subprocess.CompletedProcess[str]) -> None:
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result


def project(tmp_path: Path, on_edit: str | None) -> Path:
    line = f"gates:\n  on_edit: {json.dumps(on_edit)}\n" if on_edit else ""
    (tmp_path / "dev-loop.yaml").write_text(f"version: 1\n{line}", encoding="utf-8")
    (tmp_path / "check.sh").write_text('echo "bad: $1"\nexit 1\n', encoding="utf-8")
    (tmp_path / "pass.sh").write_text("exit 0\n", encoding="utf-8")
    edited = tmp_path / "a.py"
    edited.write_text("x = 1\n", encoding="utf-8")
    return edited


def test_no_on_edit_runs_nothing(tmp_path: Path) -> None:
    silent(run("guard-on-edit.sh", tmp_path, project(tmp_path, None)))


def test_a_failing_on_edit_reaches_the_model(tmp_path: Path) -> None:
    edited = project(tmp_path, "sh check.sh")
    result = run("guard-on-edit.sh", tmp_path, edited)
    assert result.returncode == 2, result
    assert f"bad: {edited}" in result.stderr
    assert result.stdout == ""


def test_a_passing_on_edit_is_silent(tmp_path: Path) -> None:
    silent(run("guard-on-edit.sh", tmp_path, project(tmp_path, "sh pass.sh")))


def test_post_edit_check_names_an_oversized_file(tmp_path: Path) -> None:
    big = project(tmp_path, None)
    big.write_text("x = 1\n" * 301, encoding="utf-8")
    result = run("post-edit-check.sh", tmp_path, big)
    assert result.returncode == 2, result
    assert "[size]" in result.stderr


def test_post_edit_check_is_silent_on_a_clean_file(tmp_path: Path) -> None:
    silent(run("post-edit-check.sh", tmp_path, project(tmp_path, None)))


def test_done_guard_cases() -> None:
    env = {**os.environ, "DL_PYTHON": sys.executable}
    done = subprocess.run(  # noqa: S603 - our own script
        [shutil.which("sh") or "sh", str(PLUGIN / "tests" / "test-done-guard.sh")],
        capture_output=True, text=True, check=False, env=env,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "done-guard: ok" in done.stdout


def test_the_hooks_and_their_tests_are_neutral() -> None:
    files = [HOOKS / name for name in MINE]
    files += [PLUGIN / "tests" / "test-done-guard.sh", Path(__file__)]
    assert check(files) == []


def test_a_missing_guard_script_is_a_config_problem_said_once(tmp_path: Path) -> None:
    edited = project(tmp_path, "sh nothere.sh")
    first = run("guard-on-edit.sh", tmp_path, edited)
    assert first.returncode == 2, first  # stderr + 2 is the one channel the model reads
    assert "guard script nothere.sh not found; check gates.on_edit" in first.stderr
    assert "failed" not in first.stderr and first.stdout == ""
    silent(run("guard-on-edit.sh", tmp_path, edited))  # said once, then silent
