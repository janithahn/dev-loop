"""Every hook that loads the config finds PyYAML in the plugin venv, whichever Python built it.

`uv venv` may build `${CLAUDE_PLUGIN_DATA}/venv` on another Python 3 than the hooks' `python3`
(lib/python3.12 under a 3.14 hook). A lookup of the running version's folder alone found no
PyYAML, the config loader exited and the hook let every call through. `-S` stands in for a
`python3` with no PyYAML of its own; lib/python3.0 is never the running version.
Run: python -m pytest test_hook_venv.py -q (from the plugin's tests folder).
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from test_hook_errors import tool_hooks
from test_neutral import check

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.fixture
def installed(tmp_path: Path) -> Path:
    """The plugin's hooks and source copied outside any project, and a venv for another Python."""
    copy = tmp_path / "installed" / "dev-loop"
    for part in ("hooks", "src"):
        shutil.copytree(PLUGIN / part, copy / part)
    packages = tmp_path / "data" / "venv" / "lib" / "python3.0" / "site-packages"
    shutil.copytree(Path(yaml.__file__).parent, packages / "yaml")
    return copy


def run(installed: Path, name: str, event: dict[str, object],
        config: str = "briefs: {enforce: block}") -> subprocess.CompletedProcess[str]:
    root = installed.parents[1] / "repo"
    root.mkdir(exist_ok=True)
    (root / "dev-loop.yaml").write_text(f"version: 1\n{config}\n", "utf-8")
    env = os.environ | {"CLAUDE_PROJECT_DIR": str(root), "PYTHONNOUSERSITE": "1",
                        "CLAUDE_PLUGIN_DATA": str(installed.parents[1] / "data"),
                        "HOME": str(installed.parents[1] / "home")}
    return subprocess.run(  # noqa: S603 - this interpreter and our own hook
        [sys.executable, "-S", str(installed / "hooks" / name)], input=json.dumps(event | {
            "cwd": str(root)}), capture_output=True, text=True, check=False, timeout=60, env=env)


def test_brief_contract_blocks_with_the_venvs_pyyaml(installed: Path) -> None:
    brief = {"prompt": "## Goal\nx\n", "subagent_type": "dev-loop:builder"}
    event = {"tool_name": "Agent", "tool_input": brief}
    done = run(installed, "brief-contract.py", event)
    assert done.returncode == 2 and "missing" in done.stderr, done


def test_a_windows_venvs_packages_are_found(installed: Path) -> None:
    """A venv made on Windows keeps its packages under Lib/site-packages, with no version."""
    venv = installed.parents[1] / "data" / "venv"
    shutil.move(venv / "lib" / "python3.0" / "site-packages", venv / "packages")
    shutil.rmtree(venv / "lib")  # before Lib is made: one folder where case does not count
    (venv / "Lib").mkdir()
    shutil.move(venv / "packages", venv / "Lib" / "site-packages")
    brief = {"prompt": "## Goal", "subagent_type": "dev-loop:builder"}
    done = run(installed, "brief-contract.py", {"tool_name": "Agent", "tool_input": brief})
    assert done.returncode == 2 and "missing" in done.stderr, done


def test_approval_reads_the_config_with_the_venvs_pyyaml(installed: Path) -> None:
    done = run(installed, "approval.py", {"prompt": "Approved: nope"})
    assert done.returncode == 0, done
    assert done.stderr.strip() == "approval: no change named nope; nothing written", done


def test_plan_recall_recalls_with_the_venvs_pyyaml(installed: Path) -> None:
    event = {"hook_event_name": "UserPromptSubmit", "prompt": "/dev-loop:plan demo"}
    done = run(installed, "plan-recall.py", event)
    assert done.returncode == 0, done
    context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("research"), context  # the recall ran: it read the config


def test_rules_guard_denies_a_records_edit_with_the_venvs_pyyaml(installed: Path) -> None:
    """It had failed open: no PyYAML found, the config loader exited and the edit went through."""
    records = installed.parents[1] / "repo" / ".dev-loop" / "changes" / "c1" / "records.jsonl"
    event = {"tool_name": "Edit", "tool_input": {"file_path": str(records)}}
    done = run(installed, "rules-guard.py", event)
    assert done.returncode == 2 and "record writer" in done.stderr, done  # noqa: PLR2004


def test_additive_tests_reports_a_removed_test_with_the_venvs_pyyaml(installed: Path) -> None:
    test = installed.parents[1] / "repo" / "tests" / "test_x.py"
    test.parent.mkdir(parents=True)
    test.write_text("def test_a():\n    assert 1\n", "utf-8")
    before = test.read_text("utf-8") + "\n\ndef test_b():\n    assert 2\n"
    event = {"tool_name": "Edit", "tool_input": {"file_path": str(test)},
             "tool_response": {"originalFile": before}}
    done = run(installed, "additive-tests.py", event, "fix_proves: {tests: ['tests/*']}")
    assert done.returncode == 2 and "additive" in done.stderr, done  # noqa: PLR2004


def adds_venv_first(source: str) -> bool:
    """Whether each import of dev_loop.config has add_venv() called above it in its function.

    A function that never calls it leaves the call to another that does: the gate adds the venv
    once, on its slow path, before it judges. An import outside any function has no call above.
    """
    tree = ast.parse(source)
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]

    def lines(function: ast.AST, *, calls: bool) -> list[int]:
        return [node.lineno for node in ast.walk(function)
                if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "add_venv"
                    if calls else
                    isinstance(node, ast.ImportFrom) and node.module == "dev_loop.config")]

    inside = 0
    for function in functions:
        imports, added = lines(function, calls=False), lines(function, calls=True)
        inside += len(imports)
        if imports and (min(added) >= imports[0] if added else not lines(tree, calls=True)):
            return False
    return inside == len(lines(tree, calls=False))


def test_every_tool_hook_adds_the_venv_before_it_imports_the_config() -> None:
    late = ("def main():\n    from dev_loop.config import load\n"
            "    from dev_loop.hookenv import add_venv\n    add_venv()\n")
    first = ("def config():\n    from dev_loop.hookenv import add_venv\n    add_venv()\n"
             "    from dev_loop.config import load\n")
    assert not adds_venv_first(late)
    assert not adds_venv_first("from dev_loop.config import load\n")
    assert not adds_venv_first("def config():\n    from dev_loop.config import load\n")
    assert adds_venv_first(first)
    assert adds_venv_first(first + "def read():\n    from dev_loop.config import project_root\n")
    for name, _ in tool_hooks():
        assert adds_venv_first((PLUGIN / "hooks" / f"{name}.py").read_text("utf-8")), name


def test_no_hook_keeps_its_own_venv_lookup() -> None:
    """One shared lookup: no hook builds a site-packages path itself."""
    for hook in (PLUGIN / "hooks").glob("*.py"):
        assert "site-packages" not in hook.read_text("utf-8"), hook.name


def test_the_file_is_neutral() -> None:
    assert check([Path(__file__)]) == []
