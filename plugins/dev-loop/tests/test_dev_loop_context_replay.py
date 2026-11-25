"""Replay of a real planner run the gate let through (dev-loop 0.4.18, task A).

R27H holds the context (input + cache write + cache read) of each model request of a planner
subagent that ran to 278,548 tokens with no hand-off: numbers only, no transcript text. Its
planner cap is 250k at a 0.9 threshold, so the first call at or over 225,000 must be denied.
Run: python -m pytest test_dev_loop_context_replay.py -q (from the plugin's tests folder).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import yaml
from dev_loop.dashboard.gate import (
    Caller,
    Controls,
    cap_of,
    hand_off,
    read_only,
    role_for,
    roles,
    verdict,
)
from dev_loop.dashboard.spend import Totals
from test_dev_loop_config import defaults
from test_dev_loop_context_gate import at_context, denied, event, finish, gate, hook
from test_dev_loop_fixtures import Tree

R27H = (
    23918, 26406, 30928, 32125, 32668, 39798, 43546, 48802, 52831, 54223, 59261, 59969, 60653,
    66864, 71995, 72655, 79943, 81045, 83896, 88687, 97051, 98889, 100353, 104775, 106826,
    118631, 121710, 126082, 127625, 130955, 132202, 134599, 135738, 138016, 139881, 141661,
    143705, 146701, 149449, 152521, 153238, 155703, 156819, 158357, 159570, 160632, 162846,
    163991, 164415, 168397, 169180, 170752, 173070, 174842, 177041, 177772, 178177, 179765,
    180213, 182179, 183679, 184856, 185487, 186696, 187249, 187904, 188659, 189617, 190474,
    193867, 195136, 202673, 209760, 211828, 213354, 214368, 218549, 220445, 224894, 225740,
    236562, 236841, 238005, 240191, 243430, 244775, 245512, 252689, 258800, 272025, 273893,
    276024, 276601, 277510, 278548,
)  # fmt: skip
TRIP = next(index for index, context in enumerate(R27H) if context >= 225_000)


def replay(tree: Tree, agent_type: str, extra: dict[str, str] | None = None) -> int | None:
    """Run the hook once per request, as Claude Code does; return the first denied index."""
    for index, context in enumerate(R27H):
        at_context(tree, context)
        called = json.dumps(event(tree) | {"agent_type": agent_type})
        code, out, err = finish(hook(tree, called, extra=extra))
        assert code == 0, (index, context, err)
        if out.strip():
            assert "(planner)" in denied(json.loads(out)), out
            return index
    return None


def test_a_venv_built_for_another_python_still_gives_pyyaml(tree: Tree) -> None:
    """The real case: `uv venv` built the plugin venv on another Python (lib/python3.12) than the
    hooks' python3; the gate looked only under its own version, found no PyYAML and failed open
    on every call past the floor, so the planner ran to 278k. It must deny at the first >= 225k."""
    data = tree.root / "data"
    packages = data / "venv" / "lib" / "python3.0" / "site-packages"  # never the running version
    shutil.copytree(Path(yaml.__file__).parent, packages / "yaml")
    extra = {"CLAUDE_PLUGIN_DATA": str(data), "PYTHONNOUSERSITE": "1"}
    assert R27H.index(225_740) == TRIP
    assert replay(tree, "dev-loop:planner", extra) == TRIP


def tripped(agent_type: str) -> int | None:
    """Replay R27H through the gate's verdict under the default config; the first denied index."""
    config = defaults()
    role = role_for(roles(config), agent_type)
    assert role is not None, agent_type
    cap, threshold = cap_of(role, None, None), config["handoff"]["threshold"]
    for index, context in enumerate(R27H):
        caller = Caller(
            "s", "a", "Bash", role, cap=cap, threshold=threshold, on_change_branch=False
        )
        if verdict(caller, Controls(), Totals(0.0, 0.0, 0.0, {}, {"a": context}), 0.0).deny:
            return index
    return None


def test_a_bare_planner_type_is_judged_as_the_planner() -> None:
    """A project's own copy of the agent runs as bare `planner`: it is the planner role (250k),
    not the 200k fallback that tripped it at 180k. Another plugin's `x:planner` is not."""
    assert tripped("dev-loop:planner") == TRIP
    assert tripped("planner") == TRIP
    table = roles(defaults())
    for bare, name in (("builder", "builder-frontend"), ("code-simplifier", "simplifier")):
        assert getattr(role_for(table, bare), "name", None) == name, bare
    assert getattr(role_for(table, "other:planner"), "name", None) == "general"


PLUGIN = Path(__file__).resolve().parents[1]
REPLY = "put the hand-off in your final reply"


def test_the_hand_off_gives_a_read_only_agent_a_way_to_comply(tree: Tree) -> None:
    """An agent that cannot run the record command (no Bash) hands off
    in its final reply; one that can keeps the record command and is not offered the reply."""
    repo = tree.root / "repo"
    assert read_only("Explore", PLUGIN, repo)  # a read-only built-in
    assert read_only("dev-loop:researcher", PLUGIN, repo)  # its definition grants no Bash
    for writer in ("dev-loop:planner", "dev-loop:builder", "general-purpose", "other:researcher"):
        assert not read_only(writer, PLUGIN, repo), writer
    role = role_for(roles(defaults()), "Explore")
    caller = Caller("s", "a", "Read", role, threshold=0.9, on_change_branch=False)
    for agent, gets, never in ((True, REPLY, "dl record"), (False, "dl record", REPLY)):
        text = hand_off(replace(caller, read_only=agent), 230_000, 250_000)
        assert gets in text, text
        assert never not in text, text
    assert "what is left" in hand_off(replace(caller, read_only=True), 230_000, 250_000)
    agents = repo / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "scout.md").write_text("---\nname: scout\ntools: Read, Grep, Glob\n---\n", "utf-8")
    at_context(tree, 190_000)  # past the fallback role's 180k trip
    assert REPLY in denied(gate(tree, event(tree) | {"agent_type": "scout"}))
