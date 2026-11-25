"""G1: the brief contract hook checks an Agent call's prompt against the `briefs` config.

Run from a copy of the plugin outside the project, as an install places it. Warn mode allows with
the gap said; block mode exits 2 with it; a bad project config is said and the defaults check the call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml
from dev_loop.schema import validate
from test_neutral import check

PLUGIN = Path(__file__).resolve().parents[1]
HOOK = PLUGIN / "hooks" / "brief-contract.py"

GOOD = """## Goal
Add the widget.

## Read first
- src/widget.py
- docs/widget.md

## Done when
The suite is green.
"""


def defaults() -> dict[str, Any]:
    return yaml.safe_load((PLUGIN / "src/dev_loop/defaults.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def hook(tmp_path: Path) -> Path:
    copy = tmp_path / "installed" / "dev-loop"
    for part in ("hooks", "src"):
        shutil.copytree(PLUGIN / part, copy / part)
    return copy / "hooks" / "brief-contract.py"


def run(
    hook: Path, root: Path, prompt: object, kind: str = "dev-loop:builder"
) -> subprocess.CompletedProcess[str]:
    event = {"tool_name": "Agent", "tool_input": {"prompt": prompt, "subagent_type": kind}}
    # HOME in the test folder: a crash line never lands in the real hook-errors.jsonl
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root), "HOME": str(root.parent / "home")}
    return subprocess.run(  # noqa: S603 - this interpreter and our own hook
        [sys.executable, str(hook)], input=json.dumps(event), env=env,
        capture_output=True, text=True, check=False, timeout=30,
    )


def project(tmp_path: Path, briefs: str = "") -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "dev-loop.yaml").write_text(f"version: 1\n{briefs}", encoding="utf-8")
    return root


def test_the_defaults_carry_the_briefs_section() -> None:
    briefs = defaults()["briefs"]
    assert briefs == {
        "sections": ["Goal", "Read first", "Done when"], "max_read_first": 6, "enforce": "block",
        "agent_types": [
            "dev-loop:builder", "dev-loop:builder-backend", "dev-loop:builder-frontend",
        ],
        "change_line": False,
    }


@pytest.mark.parametrize(("key", "value"), [
    ("sections", "Goal"), ("sections", [""]), ("max_read_first", 0),
    ("max_read_first", True), ("enforce", "deny"),
    ("agent_types", "planner"), ("agent_types", [""]),
])
def test_a_bad_briefs_value_is_refused_with_its_key(key: str, value: object) -> None:
    values = defaults()
    values["briefs"][key] = value
    with pytest.raises(ValueError, match=rf"^briefs\.{key}"):
        validate(values)


def test_a_brief_with_every_section_passes(hook: Path, tmp_path: Path) -> None:
    done = run(hook, project(tmp_path, "briefs: {enforce: block}\n"), GOOD)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


def test_a_missing_section_is_said_and_allowed_in_warn_mode(hook: Path, tmp_path: Path) -> None:
    root = project(tmp_path, "briefs: {enforce: warn, agent_types: []}\n")
    done = run(hook, root, GOOD.replace("## Done when", "Done when"))
    assert done.returncode == 0, done.stderr
    context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "missing Done when" in context
    assert "suite" not in done.stdout  # names only, never the brief's text


def test_a_missing_section_is_blocked_in_block_mode(hook: Path, tmp_path: Path) -> None:
    prompt = GOOD.replace("## Goal\nAdd the widget.", "## Goal\n")  # a heading with no body
    done = run(hook, project(tmp_path, "briefs: {enforce: block}\n"), prompt)
    assert done.returncode == 2
    assert "missing Goal" in done.stderr and done.stdout == ""


def test_more_read_first_files_than_the_cap_is_said(hook: Path, tmp_path: Path) -> None:
    root = project(tmp_path, "briefs: {max_read_first: 1, enforce: block}\n")
    done = run(hook, root, GOOD)
    assert done.returncode == 2
    assert "Read first lists 2 files, over 1" in done.stderr


def test_custom_sections_replace_the_defaults(hook: Path, tmp_path: Path) -> None:
    root = project(tmp_path, "briefs: {sections: [Goal, Rules], enforce: block}\n")
    done = run(hook, root, GOOD)
    assert done.returncode == 2 and "missing Rules" in done.stderr
    assert "Done when" not in done.stderr


LISTED = "briefs: {enforce: block, agent_types: ['dev-loop:planner']}\n"


def test_a_listed_type_is_checked(hook: Path, tmp_path: Path) -> None:
    done = run(hook, project(tmp_path, LISTED), "nothing", kind="dev-loop:planner")
    assert done.returncode == 2 and "missing Goal" in done.stderr


def test_an_unlisted_type_is_not_checked(hook: Path, tmp_path: Path) -> None:
    done = run(hook, project(tmp_path, LISTED), "nothing", kind="Explore")
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


def test_an_empty_list_checks_every_type(hook: Path, tmp_path: Path) -> None:
    root = project(tmp_path, "briefs: {enforce: block, agent_types: []}\n")
    for kind in ("dev-loop:planner", "Explore"):
        assert run(hook, root, "nothing", kind=kind).returncode == 2


def test_default_blocks_a_builder_brief_without_headings(hook: Path, tmp_path: Path) -> None:
    root = project(tmp_path)
    for kind in ("dev-loop:builder", "dev-loop:builder-backend", "dev-loop:builder-frontend"):
        done = run(hook, root, "### T1: mean subcommand", kind=kind)
        assert done.returncode == 2 and "missing Goal" in done.stderr, kind
    other = run(hook, root, "### T1: mean subcommand", kind="dev-loop:planner")
    assert (other.returncode, other.stdout, other.stderr) == (0, "", "")


def test_a_config_error_is_said_and_the_defaults_still_block(hook: Path, tmp_path: Path) -> None:
    root = project(tmp_path, "briefs: {enforce: sometimes}\n")
    done = run(hook, root, "no sections at all")
    assert done.returncode == 2 and done.stdout == ""
    assert "brief-contract: project config not used: " in done.stderr
    assert "briefs.enforce" in done.stderr and "missing Goal" in done.stderr
    assert "no sections at all" not in done.stderr  # names only, never the brief's text


def test_an_unknown_key_still_blocks_a_builder_brief_missing_goal(
    hook: Path, tmp_path: Path
) -> None:
    root = project(tmp_path, "nonsense_key: 1\n")
    done = run(hook, root, GOOD.replace("## Goal\nAdd the widget.\n\n", ""))
    assert done.returncode == 2
    assert "project config not used" in done.stderr and "missing Goal" in done.stderr


def test_the_change_line_is_required_only_when_the_key_is_true(hook: Path, tmp_path: Path) -> None:
    on = project(tmp_path, "briefs: {enforce: block, change_line: true}\n")
    done = run(hook, on, GOOD)
    assert done.returncode == 2
    assert (
        'missing Change line (a line "Change: <change-name>": lower case, digits, . _ -)'
        in done.stderr
    )
    assert run(hook, on, "Change: my-change.2\n" + GOOD).returncode == 0
    crlf = ("Change: my-change"+chr(13)+chr(10)+GOOD).replace(chr(10), chr(13)+chr(10))
    assert run(hook, on, crlf).returncode == 0
    assert run(hook, on, "Change: Bad Name\n" + GOOD).returncode == 2
    (tmp_path / "off").mkdir()
    off = project(tmp_path / "off", "briefs: {enforce: block, change_line: false}\n")
    assert run(hook, off, GOOD).returncode == 0


def test_a_crash_fails_open_and_loud(hook: Path, tmp_path: Path) -> None:
    done = run(hook, project(tmp_path, "briefs: {enforce: warn}\n"), ["not", "text"])
    assert done.returncode == 1 and done.stdout == ""
    assert done.stderr.strip() == "brief-contract: TypeError, call allowed"


def test_the_hook_is_registered_on_agent_calls() -> None:
    groups = json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))
    ours = [g for g in groups["hooks"]["PreToolUse"]
            if any("brief-contract.py" in h["command"] for h in g["hooks"])]
    assert [g["matcher"] for g in ours] == ["Agent|Task"]


def test_the_new_files_are_neutral() -> None:
    assert check([HOOK, Path(__file__)]) == []


def test_a_background_builder_is_refused(hook: Path, tmp_path: Path) -> None:
    root = project(tmp_path)
    # HOME in the test folder: a crash line never lands in the real hook-errors.jsonl
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root), "HOME": str(root.parent / "home")}
    codes = []
    for flag in (True, None):
        call = {"prompt": GOOD, "subagent_type": "dev-loop:builder"}
        if flag:
            call["run_in_background"] = True
        done = subprocess.run(  # noqa: S603 - this interpreter and our own hook
            [sys.executable, str(hook)], input=json.dumps({"tool_input": call}), env=env,
            capture_output=True, text=True, check=False, timeout=30,
        )
        codes.append((done.returncode, "run_in_background" in done.stderr))
    assert codes == [(2, True), (0, False)]


def test_a_brief_made_from_the_build_skills_template_passes(hook: Path, tmp_path: Path) -> None:
    skill = (PLUGIN / "skills/build/SKILL.md").read_text(encoding="utf-8")
    template = textwrap.dedent(skill.split("With the defaults:", 1)[1].split("```")[1])
    assert "## Decisions\nnone\n" in template
    root = project(
        tmp_path, "briefs: {sections: [Goal, Read first, Decisions, Done when], enforce: block}\n")
    done = run(hook, root, template)
    assert (done.returncode, done.stderr) == (0, "")
