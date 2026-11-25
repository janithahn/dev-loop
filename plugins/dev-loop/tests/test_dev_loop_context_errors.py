"""Self-check for the context gate's error boundary: fail open and loud, and no text out.

Any crash lets the call through (exit 1), prints one stderr line and appends one line to
`~/.claude/dev-loop/gate-errors.jsonl`; numbers and ids only. Run: python tests/test_dev_loop_context_errors.py; test_self_checks.py runs it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from test_dev_loop_context_gate import at_context, denied, event, finish, gate, hook
from test_dev_loop_fixtures import AGENT, SECRET, SESSION, Tree, run

if TYPE_CHECKING:
    from pathlib import Path

FIELDS = {"at", "session_id", "agent_id", "error", "frame"}


def home(tree: Tree) -> Path:
    return tree.root / "home" / ".claude" / "dev-loop"


def crash(tree: Tree, stdin: str, error: str) -> None:
    """Run the hook on a planted fault: it lets the call through with one stderr line."""
    code, stdout, stderr = finish(hook(tree, stdin))
    assert (code, stdout) == (1, ""), (code, stdout, stderr)
    assert stderr.splitlines() == [f"context-gate: {error}, call allowed"], stderr


def logged(tree: Tree) -> list[dict[str, Any]]:
    path = home(tree) / "gate-errors.jsonl"
    return [json.loads(row) for row in path.read_text("utf-8").splitlines()]


def check_a_crash_lets_the_call_through_and_is_logged(tree: Tree) -> None:
    """A TypeError, an AttributeError and bad JSON each pass and leave one log line."""
    at_context(tree, 190_000)
    crash(tree, json.dumps(event(tree) | {"transcript_path": None}), "TypeError")
    crash(tree, json.dumps([SECRET]), "AttributeError")
    crash(tree, f"{{{SECRET}", "JSONDecodeError")
    rows = logged(tree)
    assert [row["error"] for row in rows] == ["TypeError", "AttributeError", "JSONDecodeError"]
    assert all(set(row) == FIELDS for row in rows), rows
    assert (rows[0]["session_id"], rows[0]["agent_id"]) == (SESSION, AGENT)
    assert (rows[1]["session_id"], rows[1]["agent_id"]) == (None, None)
    assert rows[0]["frame"] == "context-gate:judge", rows[0]
    assert rows[1]["frame"] == "context-gate:answer", rows[1]
    control = home(tree) / "control.json"
    for agent, bad in (("d0", "{not json"), ("d1", '{"paused": "a0"}')):  # no pauses, logged
        control.write_text(bad, encoding="utf-8")
        at_context(tree, 190_000, agent)
        assert "Hand off" in denied(gate(tree, event(tree, agent)))  # the cap still holds
    assert [row["error"] for row in logged(tree)][3:] == ["JSONDecodeError", "TypeError"]


def check_a_bad_project_file_runs_on_defaults_and_says_so(tree: Tree) -> None:
    """A project file that fails to load is judged on the defaults, with a systemMessage."""
    (tree.root / "repo").mkdir()
    (tree.root / "repo" / "dev-loop.yaml").write_text("caps: [enabled\n", "utf-8")
    at_context(tree, 100_000)
    note = gate(tree, event(tree))
    assert note is not None, "the bad file was not announced"
    assert set(note) == {"systemMessage"}, note
    message = str(note["systemMessage"])
    assert message.startswith("dev-loop.yaml: "), message
    assert message.endswith("; running on the defaults"), message
    at_context(tree, 190_000)  # caps stay on: the default 200k cap at 0.9
    crossing = gate(tree, event(tree))
    assert "Hand off" in denied(crossing)
    assert crossing is not None
    assert crossing["systemMessage"] == message


def check_the_error_log_rotates(tree: Tree) -> None:
    """Past 1 MiB the log moves to gate-errors.1.jsonl; the new one starts empty."""
    home(tree).mkdir(parents=True)
    full = home(tree) / "gate-errors.jsonl"
    full.write_bytes(b"{}\n" * ((1 << 20) // 3 + 1))
    size = full.stat().st_size
    crash(tree, "[]", "AttributeError")
    assert (home(tree) / "gate-errors.1.jsonl").stat().st_size == size
    assert [row["error"] for row in logged(tree)] == ["AttributeError"]
    crash(tree, "[]", "AttributeError")  # under the bound: appended, not moved
    assert len(logged(tree)) == 2  # noqa: PLR2004 - two crashes


def check_no_prompt_text_leaves_the_hook(tree: Tree) -> None:
    """The prompt and tool input carry SECRET; no output, log or window file holds it."""
    at_context(tree, 190_000, prompt=f"dev-loop budget: context=200k {SECRET}\nscreen {SECRET}")
    planted = event(tree) | {"cwd": SECRET}
    denied(gate(tree, planted))
    gate(tree, planted)  # the countdown
    (tree.root / "repo").mkdir()
    (tree.root / "repo" / "dev-loop.yaml").write_text("caps: [enabled\n", "utf-8")
    gate(tree, event(tree))  # the systemMessage names the YAML error, never the prompt
    crash(tree, json.dumps(event(tree) | {"tool_name": SECRET, "transcript_path": 5}), "TypeError")
    crash(tree, json.dumps(event(tree) | {"agent_id": f"{SECRET}.pdf"}), "ValueError")
    (home(tree) / "control.json").write_text(json.dumps({SECRET: 1}), encoding="utf-8")
    gate(tree, event(tree))  # an unknown key is ignored, never named: exit 0, no SECRET
    for path in home(tree).rglob("*"):  # what the hook wrote: control.json is the test's input
        if path.name == "control.json":
            continue
        assert SECRET not in path.name, path
        assert not path.is_file() or SECRET.encode() not in path.read_bytes(), path


if __name__ == "__main__":
    run("dev-loop context errors", [v for k, v in list(globals().items()) if k[:6] == "check_"])
