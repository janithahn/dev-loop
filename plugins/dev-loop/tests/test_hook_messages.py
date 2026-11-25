"""Hook messages the other hook tests leave out: the done guard never holds the turn it caused,
the pre-commit hook's silent and failing cases, and the headless reviewer's hook opt-out and
timeouts. A message reaches the model when the hook exits 2 with it on stderr; silent is exit 0
with no output. (The edit hooks are in test_edit_hooks.py, the PR nudge in
test_branch_guard_trigger.py, the done guard's report in test-done-guard.sh.)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="sh is absent")

PLUGIN = Path(__file__).resolve().parents[1]
REVIEW = PLUGIN / "src" / "dev_loop" / "review" / "pr-review.sh"


def run(script: Path, payload: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [shutil.which("sh") or "sh", str(script)],
        input=payload, capture_output=True, text=True, check=False, env={**os.environ, **env},
    )


def test_done_guard_never_holds_the_turn_it_caused(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "tasks.md").write_text("- [x] T1 done\n", "utf-8")
    yaml = "version: 1\npaths:\n  changes: .\n"
    (tmp_path / "dev-loop.yaml").write_text(yaml, "utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(tmp_path)}
    result = run(PLUGIN / "hooks" / "done-guard.sh", '{"stop_hook_active": true}', env)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def pre_commit(tmp_path: Path, staged: str, status: int = 0) -> subprocess.CompletedProcess[str]:
    """Run the pre-commit hook with a stub `git` that lists `staged` and exits with `status`."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "git").write_text(f"#!/bin/sh\nprintf '%s\\n' '{staged}'\nexit {status}\n", "utf-8")
    (stub / "git").chmod(0o755)
    path = f"{stub}{os.pathsep}{os.environ['PATH']}"
    return run(PLUGIN / "hooks" / "githooks" / "pre-commit", "", {"PATH": path})


def test_pre_commit_is_silent_on_other_files(tmp_path: Path) -> None:
    result = pre_commit(tmp_path, "src/app/x.py")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_pre_commit_fails_when_it_cannot_list_the_staged_files(tmp_path: Path) -> None:
    # A failing first stage is never read as "not staged".
    result = pre_commit(tmp_path, ".claude/settings.json", status=128)
    assert result.returncode == 1
    assert "cannot list staged files" in result.stderr


def test_the_reviewer_runs_without_the_project_hooks() -> None:
    # A Stop hook's exit 2 holds a headless `claude -p` for another turn, so the auth probe at
    # --max-turns 1 fails. Each call opts out.
    script = REVIEW.read_text(encoding="utf-8")
    assert "NO_HOOKS='{\"disableAllHooks\": true}'" in script
    probe = script.split("claude -p 'Reply with the single word OK.'")[1].split("\n")[1]
    flags = script.split("CLAUDE_FLAGS=(")[1].split(")")[0]
    assert '--settings "$NO_HOOKS"' in probe
    assert '--settings "$NO_HOOKS"' in flags
    assert script.count("claude -p") == 2  # a new call must opt out as well


def test_every_reviewer_call_is_bounded_by_a_timeout() -> None:
    lines = REVIEW.read_text(encoding="utf-8").splitlines()
    calls = [line for line in lines if "claude -p" in line]
    assert len(calls) == 2
    assert all(re.search(r"\btimeout \S+ claude -p", line) for line in calls)
