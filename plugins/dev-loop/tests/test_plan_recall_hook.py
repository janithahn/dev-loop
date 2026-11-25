"""The plan-recall hook: `/dev-loop:plan|build <change>` hands the model the change's recall.

Run from a copy of the plugin outside the project, as an install places it: the readers come from
the hook's own plugin, the project from the event's cwd upwards (a worktree's own change).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from test_neutral import check

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "plan-recall.py"


def installed(tmp_path: Path) -> Path:
    copy = tmp_path / "installed" / "dev-loop"
    for part in ("hooks", "src"):
        shutil.copytree(HOOK.parents[1] / part, copy / part)
    return copy / "hooks" / "plan-recall.py"


def answer(hook: Path, event: dict[str, object]) -> str:
    done = subprocess.run(  # noqa: S603 - this interpreter and our own hook
        [sys.executable, str(hook)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return done.stdout


def project(tmp_path: Path) -> Path:
    root = tmp_path / "work" / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "dev-loop.yaml").write_text("version: 1\n", "utf-8")
    return root


def test_a_typed_plan_and_a_skill_build_recall_the_change(tmp_path: Path) -> None:
    hook, root = installed(tmp_path), project(tmp_path)
    typed = {"hook_event_name": "UserPromptSubmit", "prompt": "/dev-loop:plan --deep gone"}
    skill = {
        "hook_event_name": "PostToolUse",
        "tool_input": {"skill": "dev-loop:build", "args": "gone"},
    }
    for event in (typed, skill):
        out = json.loads(answer(hook, event | {"cwd": str(root / "docs")}))
        decision = out["hookSpecificOutput"]
        assert decision["hookEventName"] == event["hook_event_name"]
        assert "recall unavailable: no change gone" in decision["additionalContext"], decision


def test_a_change_in_its_own_worktree_is_recalled_from_the_parent_checkout(
    tmp_path: Path,
) -> None:
    hook, root = installed(tmp_path), project(tmp_path)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "x"],
                 ["worktree", "add", "-q", "-b", "dev-loop/c1", str(tmp_path / "wt")]):
        subprocess.run([*git, *args], cwd=root, check=True, capture_output=True)  # noqa: S603
    change = tmp_path / "wt" / ".dev-loop" / "changes" / "c1"
    change.mkdir(parents=True)
    (change / "proposal.md").write_text("# c1", "utf-8")
    event = {"hook_event_name": "PostToolUse", "cwd": str(root),
             "tool_input": {"skill": "dev-loop:plan", "args": "c1"}}
    context = json.loads(answer(hook, event))["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("research (ls"), context
    assert "recall unavailable" not in context


def test_any_other_prompt_says_nothing(tmp_path: Path) -> None:
    hook, root = installed(tmp_path), project(tmp_path)
    for prompt in ("please run /dev-loop:plan me later", "/dev-loop:verify me", "/dev-loop:plan"):
        event = {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "cwd": str(root)}
        assert answer(hook, event) == "", prompt


def test_the_hook_and_its_tests_are_neutral() -> None:
    assert check([HOOK, Path(__file__)]) == []
