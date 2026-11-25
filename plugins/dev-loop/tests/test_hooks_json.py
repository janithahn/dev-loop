"""hooks/hooks.json registers every hook through ${CLAUDE_PLUGIN_ROOT}; the session guard."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from test_neutral import check

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks" / "hooks.json"
GUARD = PLUGIN / "hooks" / "session-guard.sh"
ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"


def commands() -> dict[str, list[str]]:
    """Return each event's commands."""
    events = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"]
    return {
        event: [hook["command"] for group in groups for hook in group["hooks"]]
        for event, groups in events.items()
    }


def test_every_command_resolves_to_an_existing_file() -> None:
    every = [command for listed in commands().values() for command in listed]
    assert every
    for command in every:
        paths = re.findall(r'"\$\{CLAUDE_PLUGIN_ROOT\}/([^"]+)"', command)
        assert paths, command
        assert all((PLUGIN / path).is_file() for path in paths), command


def test_every_hook_file_is_registered() -> None:
    registered = " ".join(command for listed in commands().values() for command in listed)
    for hook in (PLUGIN / "hooks").glob("*.*"):
        if hook.suffix in {".sh", ".py"}:
            assert f"{ROOT_VAR}/hooks/{hook.name}" in registered, hook.name
    assert f"{ROOT_VAR}/bin/dl-bootstrap" in " ".join(commands()["SessionStart"])


def test_events_carry_their_hooks() -> None:
    by_event = {event: " ".join(listed) for event, listed in commands().items()}
    assert "session-guard.sh" in by_event["SessionStart"]
    assert "branch-guard.sh" in by_event["PreToolUse"]
    assert "context-gate.py" in by_event["PreToolUse"]
    for name in ("post-edit-check.sh", "guard-on-edit.sh", "pr-loop-trigger.sh", "plan-recall.py"):
        assert name in by_event["PostToolUse"], name
    assert "done-guard.sh" in by_event["Stop"]
    for event in ("SessionStart", "UserPromptSubmit", "Stop", "PreCompact", "SessionEnd"):
        assert "session-state.py" in by_event[event], event


def guard(project: Path) -> str:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project), "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
    env.pop("CLAUDE_PLUGIN_DATA", None)
    done = subprocess.run(["sh", str(GUARD)], cwd=project, env=env, input="{}",
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_session_guard_prints_the_overlays_checklist(tmp_path: Path) -> None:
    (tmp_path / "dev-loop.yaml").write_text("knowledge: {session_checklist: docs/check.md}\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "check.md").write_text("- [ ] the project's own first step\n")
    assert "the project's own first step" in guard(tmp_path)


def test_session_guard_prints_a_neutral_default_without_one(tmp_path: Path) -> None:
    out = guard(tmp_path)
    assert len([line for line in out.splitlines() if line.startswith("- [ ]")]) == 3


def test_session_guard_warns_on_the_integration_branch(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(tmp_path)], check=True)
    (tmp_path / "dev-loop.yaml").write_text("branches: {integration: trunk}\n")
    assert "protected branch 'trunk'" in guard(tmp_path)


def test_session_guard_warns_on_a_second_context_gate(tmp_path: Path) -> None:
    call = {"command": "python .claude/hooks/context-gate.py"}
    settings = {"hooks": {"PreToolUse": [{"hooks": [call]}]}}
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(settings))
    assert "two context gates: remove the project copy" in guard(tmp_path)


def test_session_guard_is_silent_without_a_second_gate(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text('{"hooks": {}}')
    assert "two context gates" not in guard(tmp_path)


def test_session_guard_says_caps_are_disabled(tmp_path: Path) -> None:
    (tmp_path / "dev-loop.yaml").write_text("caps: {enabled: false}\n")
    out = guard(tmp_path)
    assert "caps.enabled: false" in out
    (tmp_path / "dev-loop.yaml").write_text("caps: {enabled: true}\n")
    assert "caps.enabled" not in guard(tmp_path)


def test_files_are_neutral() -> None:
    assert check([HOOKS, GUARD, Path(__file__)]) == []
