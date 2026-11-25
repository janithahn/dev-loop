"""E: the rules guard denies a background command in a headless run (rule 1), a subagent's edit of
Claude Code settings (rule 2) and a hand-edited record or size waiver (rule 3)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOK = PLUGIN / "hooks" / "rules-guard.py"


def run(
    event: dict[str, object], root: Path | None = None, **env: str
) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k != "DEV_LOOP_ROLE"}
    if root:
        base["CLAUDE_PROJECT_DIR"] = str(root)
    return subprocess.run(  # noqa: S603 - this interpreter and our own hook
        [sys.executable, str(HOOK)], input=json.dumps(event), env={**base, **env},
        capture_output=True, text=True, check=False, timeout=30,
    )


@pytest.mark.parametrize("tool", ["Bash", "Agent", "Task"])
def test_a_background_call_is_denied_for_a_headless_role(tool: str) -> None:
    event = {"tool_name": tool, "tool_input": {"run_in_background": True}}
    done = run(event, DEV_LOOP_ROLE="builder")
    assert done.returncode == 2
    assert "background" in done.stderr


def test_a_background_call_is_denied_in_bypass_permissions_mode() -> None:
    event = {"tool_name": "Bash", "permission_mode": "bypassPermissions",
             "tool_input": {"command": "sleep 9", "run_in_background": True}}
    assert run(event).returncode == 2


def test_an_interactive_background_command_is_allowed() -> None:
    event = {"tool_name": "Bash", "permission_mode": "default",
             "tool_input": {"command": "sleep 9", "run_in_background": True}}
    assert run(event).returncode == 0


def test_a_powershell_background_call_is_denied_for_a_headless_role() -> None:
    call = {"command": "sleep 9", "run_in_background": True}
    event = {"tool_name": "PowerShell", "tool_input": call}
    done = run(event, DEV_LOOP_ROLE="builder")
    assert done.returncode == 2 and "background" in done.stderr
    assert run(event).returncode == 0  # interactive twin


def test_the_hook_matcher_names_powershell() -> None:
    groups = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
    ours = [g["matcher"] for g in groups if "rules-guard" in g["hooks"][0]["command"]]
    assert "PowerShell" in ours[0].split("|")


@pytest.mark.parametrize("command", [
    "sleep 9 &", "make build & make test", "nohup make &>/dev/null", "nohup make",
    "setsid make", "make & disown", "sudo setsid make", "FOO=1 nohup make",
])
def test_a_shell_background_command_is_denied_for_a_headless_role(command: str) -> None:
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    done = run(event, DEV_LOOP_ROLE="builder")
    assert done.returncode == 2 and "background" in done.stderr
    assert run(event).returncode == 0  # interactive twin


@pytest.mark.parametrize("command", [
    "make a && make b", "echo 'a && b'", 'echo "run & wait"', "make 2>&1 | tail", "make &> out.txt",
    "echo nohup", "grep -r setsid .",
])
def test_a_command_that_only_quotes_or_chains_is_allowed_for_a_headless_role(command: str) -> None:
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert run(event, DEV_LOOP_ROLE="builder").returncode == 0


def test_an_unparsable_headless_command_fails_open_and_loud() -> None:
    event = {"tool_name": "Bash", "tool_input": {"command": "echo 'unclosed &"}}
    done = run(event, DEV_LOOP_ROLE="builder")
    logged = "rules-guard: ValueError, background not checked" + chr(10)
    assert (done.returncode, done.stderr) == (0, logged)


def test_a_foreground_call_is_allowed_for_a_headless_role() -> None:
    event = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert run(event, DEV_LOOP_ROLE="builder").returncode == 0


def test_a_malformed_event_fails_open_and_loud() -> None:
    done = subprocess.run(  # noqa: S603
        [sys.executable, str(HOOK)], input="not json", capture_output=True, text=True,
        check=False, timeout=30,
    )
    assert done.returncode == 1 and "rules-guard" in done.stderr


@pytest.mark.parametrize("name", ["settings.json", "settings.local.json"])
@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
def test_a_subagent_edit_of_claude_settings_is_denied(tool: str, name: str) -> None:
    event = {"tool_name": tool, "agent_id": "a1",
             "tool_input": {"file_path": rf"C:\repo\.claude\{name}"}}
    done = run(event)
    assert done.returncode == 2 and "settings" in done.stderr


def test_a_settings_path_in_another_letter_case_is_denied_for_a_subagent() -> None:
    event = {"tool_name": "Edit", "agent_id": "a1",
             "tool_input": {"file_path": r"C:|Repo|.Claude|Settings.json".replace("|", chr(92))}}
    done = run(event)
    assert done.returncode == 2 and "settings" in done.stderr


def test_a_subagent_notebook_edit_of_claude_settings_is_denied_by_its_notebook_path() -> None:
    event = {"tool_name": "NotebookEdit", "agent_id": "a1",
             "tool_input": {"notebook_path": "/repo/.claude/settings.json"}}
    done = run(event)
    assert done.returncode == 2 and "settings" in done.stderr
    other = {**event, "tool_input": {"notebook_path": "/repo/docs/a.ipynb"}}
    assert run(other).returncode == 0


def test_the_coordinator_may_edit_claude_settings() -> None:
    event = {"tool_name": "Edit", "tool_input": {"file_path": "/repo/.claude/settings.json"}}
    assert run(event).returncode == 0


def test_a_subagent_may_edit_other_files() -> None:
    event = {"tool_name": "Edit", "agent_id": "a1",
             "tool_input": {"file_path": "/repo/.claude/agents/x.md"}}
    assert run(event).returncode == 0


def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".dev-loop" / "changes" / "c1").mkdir(parents=True)
    (root / "dev-loop.yaml").write_text("version: 1\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_a_hand_edit_of_a_change_record_file_is_denied(tool: str, tmp_path: Path) -> None:
    root = project(tmp_path)
    path = root / ".dev-loop" / "changes" / "c1" / "records.jsonl"
    done = run({"tool_name": tool, "tool_input": {"file_path": str(path)}}, root)
    assert done.returncode == 2 and "record writer" in done.stderr


def test_a_linked_worktrees_change_folder_is_covered(tmp_path: Path) -> None:
    root = project(tmp_path)
    other = tmp_path / "linked" / ".dev-loop" / "changes" / "c1"
    other.mkdir(parents=True)
    event = {"tool_name": "Write", "tool_input": {"file_path": str(other / "records.jsonl")}}
    done = run(event, root)
    assert done.returncode == 2 and "record writer" in done.stderr
    elsewhere = tmp_path / "linked" / ".dev-loop" / "records.jsonl"
    assert run({**event, "tool_input": {"file_path": str(elsewhere)}}, root).returncode == 0


def test_a_records_file_outside_the_changes_folder_is_allowed(tmp_path: Path) -> None:
    root = project(tmp_path)
    path = str(root / "fixtures/records.jsonl")
    event = {"tool_name": "Write", "tool_input": {"file_path": path}}
    assert run(event, root).returncode == 0


def test_the_record_writer_may_write_records_through_bash(tmp_path: Path) -> None:
    root = project(tmp_path)
    command = "dl record c1 note x=1"
    assert run({"tool_name": "Bash", "tool_input": {"command": command}}, root).returncode == 0


WAIVER = "Size waiver: lead 2026-01-01 - reason"


def proposal(root: Path) -> str:
    return str(root / ".dev-loop" / "changes" / "c1" / "proposal.md")


def test_an_edit_adding_a_size_waiver_is_denied(tmp_path: Path) -> None:
    root = project(tmp_path)
    call = {"file_path": proposal(root), "old_string": "# P", "new_string": f"# P\n{WAIVER}\n"}
    done = run({"tool_name": "Edit", "tool_input": call}, root)
    assert done.returncode == 2 and "waiver" in done.stderr


def test_a_write_adding_a_size_waiver_is_denied(tmp_path: Path) -> None:
    root = project(tmp_path)
    call = {"file_path": proposal(root), "content": f"# P\n{WAIVER}\n"}
    assert run({"tool_name": "Write", "tool_input": call}, root).returncode == 2


def test_a_multiedit_adding_a_size_waiver_is_denied(tmp_path: Path) -> None:
    root = project(tmp_path)
    edits = [{"old_string": "a", "new_string": "b"}, {"old_string": "", "new_string": WAIVER}]
    call = {"file_path": proposal(root), "edits": edits}
    assert run({"tool_name": "MultiEdit", "tool_input": call}, root).returncode == 2


def test_an_edit_that_keeps_an_existing_waiver_is_allowed(tmp_path: Path) -> None:
    root = project(tmp_path)
    call = {"file_path": proposal(root), "old_string": WAIVER, "new_string": f"{WAIVER}\nmore"}
    assert run({"tool_name": "Edit", "tool_input": call}, root).returncode == 0


def test_an_edit_to_a_proposal_without_a_waiver_is_allowed(tmp_path: Path) -> None:
    root = project(tmp_path)
    call = {"file_path": proposal(root), "old_string": "a", "new_string": "b\nSize is small."}
    assert run({"tool_name": "Edit", "tool_input": call}, root).returncode == 0


@pytest.mark.parametrize(
    "line", ["**Size waiver:** lead 2026-01-01 - r", "*Size waiver:* x", "**Size waiver:"]
)
def test_a_bold_size_waiver_is_denied_like_the_size_cap_reads_it(line: str, tmp_path: Path) -> None:
    root = project(tmp_path)
    call = {"file_path": proposal(root), "old_string": "# P", "new_string": f"# P\n{line}\n"}
    assert run({"tool_name": "Edit", "tool_input": call}, root).returncode == 2


def edit_of(root: Path, text: str, **call: object) -> subprocess.CompletedProcess[str]:
    """Run the hook on an Edit or MultiEdit of a proposal that holds `text`."""
    Path(proposal(root)).write_text(text, encoding="utf-8")
    tool = "MultiEdit" if "edits" in call else "Edit"
    return run({"tool_name": tool, "tool_input": {"file_path": proposal(root), **call}}, root)


def test_an_edit_forming_a_waiver_from_a_fragment_is_denied(tmp_path: Path) -> None:
    root = project(tmp_path)
    done = edit_of(root, "# P|**Size**|".replace("|", chr(10)),
                   old_string="e**", new_string="e waiver:** lead 2026-01-01 - r")
    assert done.returncode == 2
    said = "rules-guard: only a person adds a size waiver: ask the owner to write the line"
    assert done.stderr.strip() == said


def test_a_multiedit_forming_a_waiver_in_two_steps_is_denied(tmp_path: Path) -> None:
    edits = [{"old_string": "Sz note", "new_string": "Size wai"},
             {"old_string": "wai", "new_string": "waiver: lead 2026-01-01 - r"}]
    done = edit_of(project(tmp_path), "# P|Sz note|".replace("|", chr(10)), edits=edits)
    assert done.returncode == 2 and "waiver" in done.stderr


def test_a_replace_all_forming_a_waiver_on_a_later_line_is_denied(tmp_path: Path) -> None:
    done = edit_of(project(tmp_path), "Sz in prose|Sz: lead 2026-01-01 - r|".replace("|", chr(10)),
                   old_string="Sz", new_string="Size waiver", replace_all=True)
    assert done.returncode == 2 and "waiver" in done.stderr


@pytest.mark.parametrize("call", [
    {"old_string": "e**", "new_string": "e matters**"},  # the same fragment, no waiver formed
    {"old_string": "# P", "new_string": "# Proposal"},  # another line of a file that holds one
    {"old_string": WAIVER + chr(10), "new_string": ""},  # a waiver taken away
    {"edits": [{"old_string": "# P", "new_string": "# Q"},
               {"old_string": "**Size**", "new_string": "**Size:** small"}]},
])
def test_an_edit_that_forms_no_new_waiver_is_allowed(
    call: dict[str, object], tmp_path: Path
) -> None:
    text = f"# P|**Size**|{WAIVER}|".replace("|", chr(10))
    done = edit_of(project(tmp_path), text, **call)
    assert (done.returncode, done.stderr) == (0, "")


def test_the_land_skills_background_frontmatter_is_not_blocked(tmp_path: Path) -> None:
    skill = (PLUGIN / "skills" / "land" / "SKILL.md").read_text(encoding="utf-8")
    assert "\nbackground: true\n" in skill.split("---")[1]
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    matchers = [group.get("matcher", "") for group in hooks["PreToolUse"]]
    assert not any("Skill" in matcher for matcher in matchers)
    event = {"tool_name": "Skill", "tool_input": {"skill": "land", "run_in_background": True}}
    assert run(event, DEV_LOOP_ROLE="builder").returncode == 0


def ci_project(tmp_path: Path, patterns: str = r"['\bpytest( -q)?$', '^make golden']") -> Path:
    root = project(tmp_path)
    text = f"version: 1\ngates:\n  ci_only: {patterns}\n"
    (root / "dev-loop.yaml").write_text(text, encoding="utf-8")
    return root


def local(command: str, root: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run the hook on a Bash call with CI and TF_BUILD unset, then `env` over that."""
    base = {k: v for k, v in os.environ.items() if k not in {"CI", "TF_BUILD", "DEV_LOOP_ROLE"}}
    return subprocess.run(  # noqa: S603 - this interpreter and our own hook
        [sys.executable, str(HOOK)], capture_output=True, text=True, check=False, timeout=30,
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        env={**base, "CLAUDE_PROJECT_DIR": str(root), **env},
    )


@pytest.mark.parametrize("command", [
    "uv run pytest -q",
    "cd backend && uv run pytest -q; echo done",
    "ls\nmake golden | tail -3",
    "(pytest)",
])
def test_a_ci_only_command_is_denied_outside_ci(command: str, tmp_path: Path) -> None:
    done = local(command, ci_project(tmp_path))
    assert done.returncode == 2
    assert done.stderr.strip() == ("rules-guard: this command runs in CI only (gates.ci_only): "
                                   "run the tests for what changed")


@pytest.mark.parametrize("name", ["CI", "TF_BUILD"])
def test_a_ci_only_command_passes_in_ci(name: str, tmp_path: Path) -> None:
    assert local("uv run pytest -q", ci_project(tmp_path), **{name: "1"}).returncode == 0


@pytest.mark.parametrize("command", [
    "git commit -m 'run pytest -q; then make golden'",
    'git commit -m "ci only: uv run pytest -q"',
    "uv run pytest -q tests/test_one.py",
    "echo make golden",
])
def test_a_command_that_only_quotes_or_scopes_the_words_passes(
    command: str, tmp_path: Path
) -> None:
    done = local(command, ci_project(tmp_path))
    assert (done.returncode, done.stderr) == (0, "")


def test_an_empty_ci_only_list_passes(tmp_path: Path) -> None:
    assert local("uv run pytest -q", ci_project(tmp_path, "[]")).returncode == 0
    assert local("uv run pytest -q", project(tmp_path / "bare")).returncode == 0


def test_a_config_ci_only_cannot_be_read_from_fails_open_and_loud(tmp_path: Path) -> None:
    done = local("uv run pytest -q", ci_project(tmp_path, "['(']"))
    assert done.returncode == 1 and "rules-guard" in done.stderr and "allowed" in done.stderr
    quote = local("echo 'unclosed", ci_project(tmp_path / "q"))
    assert quote.returncode == 1 and "allowed" in quote.stderr


def test_a_project_without_the_key_costs_no_yaml_parse(tmp_path: Path) -> None:
    """Under -S there is no PyYAML: loading the config would exit 1."""
    event = {"tool_name": "Bash", "tool_input": {"command": "uv run pytest -q"}}
    env = {"PATH": os.environ["PATH"], "CLAUDE_PROJECT_DIR": str(project(tmp_path))}
    done = subprocess.run(  # noqa: S603 - this interpreter and our own hook
        [sys.executable, "-S", str(HOOK)], input=json.dumps(event), env=env,
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert (done.returncode, done.stderr) == (0, "")
