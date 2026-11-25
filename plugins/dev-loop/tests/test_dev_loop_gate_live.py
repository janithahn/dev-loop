"""G6 (prove the context gate is live): the gate's heartbeat on a subagent call.

The gate touches ~/.claude/dev-loop/gate-heartbeat (an mtime, no content) each time it has judged
a subagent call, never on a main-session call and never when it could not judge (a crash, a
missing PyYAML): a gate that cannot judge says so in a systemMessage. `dl doctor` reads the
heartbeat (test_doctor.py).
Run: python -m pytest test_dev_loop_gate_live.py -q (from the plugin's tests folder).
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING

from test_dev_loop_context_gate import assistant, event, finish, gate, hook, plugin_copy, user
from test_dev_loop_fixtures import Tree

if TYPE_CHECKING:
    from pathlib import Path

from test_dev_loop_context_gate import at_context


def heartbeat(tree: Tree) -> Path:
    return tree.root / "home" / ".claude" / "dev-loop" / "gate-heartbeat"


def test_the_heartbeat_marks_a_subagent_call(tree: Tree) -> None:
    """The gate touches gate-heartbeat on a subagent call, an mtime and no content."""
    at_context(tree, 1_000)
    assert gate(tree, event(tree)) is None
    beat = heartbeat(tree)
    assert beat.is_file() and beat.read_bytes() == b""


def test_no_heartbeat_on_a_main_session_call(tree: Tree) -> None:
    """A main session (no agent_id) is not a subagent call; it leaves no heartbeat."""
    tree.write([user(), assistant("m1", tokens=(1_000, 10, 0, 0, 0))])
    assert gate(tree, event(tree, None)) is None
    assert not heartbeat(tree).exists()


def test_a_gate_without_pyyaml_says_so_and_leaves_no_heartbeat(tree: Tree) -> None:
    """A plugin venv holding no PyYAML: the config loader exits inside judge(). The call goes
    through (exit 0) with a systemMessage, the crash is logged, and no heartbeat is written."""
    (tree.root / "data" / "venv").mkdir(parents=True)
    at_context(tree, 190_000)
    extra = {"CLAUDE_PLUGIN_DATA": str(tree.root / "data")}
    code, out, err = finish(hook(tree, json.dumps(event(tree)), extra=extra))
    assert code == 0, err
    assert json.loads(out) == {"systemMessage": "dev-loop gate is not enforcing: dev-loop config: "
                               "PyYAML is missing: pip install pyyaml; run dl doctor"}
    assert not heartbeat(tree).exists()
    for log in ("hook-errors.jsonl", "gate-errors.jsonl"):
        (row,) = heartbeat(tree).with_name(log).read_text("utf-8").splitlines()
        assert json.loads(row)["error"] == "SystemExit", row


def test_a_gate_with_a_missing_module_says_so_and_leaves_no_heartbeat(tree: Tree) -> None:
    """An install that lost one of the gate's own modules: the same, naming the import error."""
    copy = plugin_copy(tree)
    (copy / "src" / "dev_loop" / "dashboard" / "gate.py").unlink()
    shutil.rmtree(copy / "src" / "dev_loop" / "dashboard" / "__pycache__", ignore_errors=True)
    at_context(tree, 190_000)
    started = hook(tree, json.dumps(event(tree)), copy / "hooks" / "context-gate.py")
    code, out, err = finish(started)
    assert code == 0, err
    assert json.loads(out) == {"systemMessage": "dev-loop gate is not enforcing: "
                               "ModuleNotFoundError dev_loop.dashboard.gate; run dl doctor"}
    assert not heartbeat(tree).exists()


def test_a_gate_stopped_by_any_other_exit_never_echoes_its_text(tree: Tree) -> None:
    """Only the PyYAML line is echoed: another exit's text may quote a config value."""
    copy = plugin_copy(tree)
    module = copy / "src" / "dev_loop" / "dashboard" / "gate.py"
    stopped = module.read_text("utf-8") + 'raise SystemExit("PLANTED-EXIT-17")\n'
    module.write_text(stopped, "utf-8")  # the module exits as it is imported
    shutil.rmtree(copy / "src" / "dev_loop" / "dashboard" / "__pycache__", ignore_errors=True)
    at_context(tree, 190_000)
    started = hook(tree, json.dumps(event(tree)), copy / "hooks" / "context-gate.py")
    code, out, err = finish(started)
    assert code == 0, err
    assert json.loads(out) == {"systemMessage": "dev-loop gate is not enforcing: SystemExit; "
                               "run dl doctor"}
    assert "PLANTED" not in out + err


def test_the_gate_self_check_denies_a_crossing_call() -> None:
    """self_check proves the decision engine denies an agent at its cap (dl doctor reads it)."""
    from dev_loop.dashboard import gate as engine

    assert engine.self_check() is True
