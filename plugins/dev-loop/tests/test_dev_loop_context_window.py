"""Self-check for the context gate's wrap-up window: the countdown after a crossing.

A crossing opens a window: one byte per judged call in `~/.claude/dev-loop/windows/<agent_id>`,
so the file's size is the count. Run: python tests/test_dev_loop_context_window.py; test_self_checks.py runs it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from dev_loop.dashboard.gate import WRAP_CALLS
from test_dev_loop_context_gate import at_context, denied, event, finish, gate, hook
from test_dev_loop_fixtures import AGENT, SECRET, Tree, run

if TYPE_CHECKING:
    from pathlib import Path


def windows(tree: Tree) -> Path:
    return tree.root / "home" / ".claude" / "dev-loop" / "windows"


def countdown(answer: dict[str, Any] | None) -> str:
    """Return a window call's note; a deny or silence fails."""
    assert answer is not None, "no countdown"
    decision = answer["hookSpecificOutput"]
    assert "permissionDecision" not in decision, answer
    return str(decision["additionalContext"])


def check_ten_bash_calls_then_a_stop(tree: Tree) -> None:
    """The crossing call is denied; 10 Bash calls go through, counting down; then a stop."""
    at_context(tree, 190_000)
    assert "Hand off within 10 Bash calls" in denied(gate(tree, event(tree)))
    assert WRAP_CALLS == 10  # noqa: PLR2004 - the window the hand-off names
    for left in range(WRAP_CALLS - 1, -1, -1):
        assert f"{left} calls left" in countdown(gate(tree, event(tree))), left
    stop = denied(gate(tree, event(tree)))
    assert "the wrap-up calls are spent" in stop, stop
    assert (windows(tree) / AGENT).stat().st_size == WRAP_CALLS + 2


def check_parallel_calls_each_count(tree: Tree) -> None:
    """Two processes at once count 2; an append needs no lock."""
    at_context(tree, 190_000)
    denied(gate(tree, event(tree)))
    started = [hook(tree, json.dumps(event(tree))) for _ in range(2)]
    for one in started:  # both judged in the window
        countdown(json.loads(finish(one)[1]))
    assert (windows(tree) / AGENT).stat().st_size == 1 + 2
    assert f"{WRAP_CALLS - 3} calls left" in countdown(gate(tree, event(tree)))


def check_the_window_closes_when_under_the_cap(tree: Tree) -> None:
    """A call under the trip (compacted) deletes the file; the next crossing starts anew."""
    at_context(tree, 190_000)
    denied(gate(tree, event(tree)))
    for _ in range(3):
        countdown(gate(tree, event(tree)))
    at_context(tree, 60_000)
    assert gate(tree, event(tree)) is None
    assert not (windows(tree) / AGENT).exists()
    at_context(tree, 190_000)
    assert "Hand off within 10 Bash calls" in denied(gate(tree, event(tree)))
    assert (windows(tree) / AGENT).stat().st_size == 1


def check_an_event_over_a_megabyte_is_judged_whole(tree: Tree) -> None:
    """A Write's event carries its whole content: read in full, the crossing is still denied."""
    at_context(tree, 190_000)
    big = event(tree) | {"tool_name": "Write", "tool_input": {"content": "x" * (3 << 19)}}
    assert "Hand off within 10 Bash calls" in denied(gate(tree, big))
    assert (windows(tree) / AGENT).stat().st_size == 1


def check_a_bad_agent_id_fails_open(tree: Tree) -> None:
    """An id outside [A-Za-z0-9_-]{1,64} is a hook error: the call proceeds."""
    for agent in ("../escape", "a" * 65, "", f"{SECRET}/x", 7):
        code, stdout, stderr = finish(hook(tree, json.dumps(event(tree) | {"agent_id": agent})))
        assert (code, stdout) == (1, ""), (agent, code, stdout)
        assert stderr.strip() == "context-gate: ValueError, call allowed", stderr
    assert not windows(tree).exists() or not list(windows(tree).iterdir())
    assert not (tree.root / "home" / ".claude" / "escape").exists()


if __name__ == "__main__":
    run("dev-loop context window", [v for k, v in list(globals().items()) if k[:6] == "check_"])
