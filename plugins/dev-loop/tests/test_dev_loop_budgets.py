"""Self-check for the two budgets: the roles and the window.

The context cap hands off; cost, turns and time are shown, never enforced.
Run: python tests/test_dev_loop_budgets.py; test_self_checks.py runs it.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import yaml
from dev_loop import config
from dev_loop.dashboard.gate import Answer, Caller, Controls, role_for, roles, verdict
from dev_loop.dashboard.spend import Totals, totals
from dev_loop.schema import validate
from test_dev_loop_fixtures import (
    AGENT,
    NOW,
    SECRET,
    SESSION,
    Tree,
    assistant,
    run,
    scanned,
    tool_result,
    user,
)

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"
CONFIG: dict[str, Any] = yaml.safe_load(config.DEFAULTS.read_text(encoding="utf-8"))
EMPTY = Totals(at=NOW, today_usd=0.0, burn_usd_h=0.0, usd={}, context={})
ROLE = next(role for role in roles(CONFIG) if role.name == "builder-backend")  # 200k
# The hand-off names `dl record`, not scripts/record.py
HAND_OFF = (
    "dev-loop gate: agent a0123456789abcdef (builder-backend) is at its quality cap: this "
    "agent's context 200k is at its 200k cap. Hand off within 10 Bash calls: commit your work "
    "in progress on the row's branch, run dl record <change> handover "
    'role=builder-backend agent=a0123456789abcdef context=200000 commit=<sha> "done=..." '
    '"next=...", then stop and report handover. A fresh agent continues from the record.'
)
STOP_NOW = "dev-loop gate: the wrap-up calls are spent: stop now and report to the coordinator."


def left(calls: int) -> str:
    return f"dev-loop gate: {calls} calls left; then stop and report handover."


def ask(
    spent: Totals,
    controls: Controls | None = None,
    window: tuple[str, int] | None = None,
    now: float = NOW,
) -> Answer:
    """Return the gate's answer to one Bash call of the builder-backend agent."""
    return verdict(Caller(SESSION, AGENT, "Bash", ROLE, window), controls or Controls(), spent, now)


def at(**maps: Any) -> Totals:  # noqa: ANN401 - Totals' fields; replace checks each type
    """EMPTY with these maps (or today_usd)."""
    return replace(EMPTY, **maps)


def with_role(name: str, role: dict[str, Any]) -> dict[str, Any]:
    """CONFIG with one role replaced."""
    changed = copy.deepcopy(CONFIG)
    changed["roles"][name] = role
    return changed


def check_a_bad_budgets_block_is_refused(_: Tree) -> None:
    """A context cap that is not a whole number in range, or an empty type, is refused.

    The config's one validator refuses it, naming the role (schema.py).
    """
    assert [role.name for role in roles(CONFIG)][3:5] == ["builder-backend", "builder-frontend"]
    frontend = CONFIG["roles"]["builder-frontend"]
    bad: list[tuple[str, object]] = [("default", 0), ("default", 1_000_001), ("default", 2.5)]
    bad += [("default", True), ("default", ...), ("agent_types", [7]), ("agent_types", [""])]
    bad += [("model", 7)]
    for key, value in bad:  # ...: the key is missing
        block = copy.deepcopy(frontend)
        held = block["context"] if key == "default" else block
        held.pop(key)
        held |= {} if value is ... else {key: value}
        try:
            validate(with_role("builder-frontend", block))
        except ValueError as error:
            said = str(error)
        else:
            raise AssertionError((key, value))
        assert "builder-frontend" in said, (key, value, said)
    most = {"default": 1_000_000, "min": 100_000, "max": 1_000_000}
    widest = with_role("builder-backend", CONFIG["roles"]["builder-backend"] | {"context": most})
    validate(widest)  # the window's edge is a cap
    assert roles(widest)
    twice = with_role("a", frontend | {"agent_types": ["b"]})
    twice["roles"]["b"] = frontend | {"agent_types": ["b"]}
    try:
        validate(twice)
    except ValueError as error:
        assert "'b'" in str(error), error
    else:
        raise AssertionError("a duplicate agent type was accepted")


def check_a_role_is_found_by_agent_type_and_model(_: Tree) -> None:
    """The role is the agent type alone, the model is the role's.

    `builder` is builder-frontend's alias whatever its model; an untyped agent is `general`.
    """
    table = roles(CONFIG)
    backend = role_for(table, "dev-loop:builder-backend")
    frontend = role_for(table, "dev-loop:builder-frontend")
    assert (backend.name, frontend.name) == ("builder-backend", "builder-frontend")
    assert role_for(table, "dev-loop:builder") == frontend  # by type alone
    assert (backend.model, frontend.model) == (OPUS, SONNET)  # the model is the role's, not a key
    assert role_for(table, "general-purpose").name == "general"
    assert role_for(table, None).name == "general"


def check_an_agent_with_no_scanned_call_is_allowed(_: Tree) -> None:
    """Edge case: no scanned call, no model: the type's role, no cap crossed."""
    table = roles(CONFIG)
    assert role_for(table, "dev-loop:builder-backend") == ROLE
    assert EMPTY.model.get(AGENT) is None
    assert EMPTY.peak.get(AGENT) is None
    assert ask(EMPTY) == Answer()


def check_the_context_cap_hands_off_at_its_edge(_: Tree) -> None:
    """One token below the role's quality cap allows; the cap opens the hand-off."""
    assert ask(at(context={AGENT: 199_999})) == Answer()
    assert ask(at(context={AGENT: 200_000})) == Answer(deny=HAND_OFF, kind="context")


def check_cost_turns_and_time_never_refuse(_: Tree) -> None:
    """Crossing the old usd, turns, seconds or daily caps allows the call."""
    huge = at(turns={AGENT: 10**6}, usd={AGENT: 10**4, SESSION: 10**4})
    assert ask(replace(huge, today_usd=10**5)) == Answer()
    assert verdict(Caller(SESSION, AGENT, "Bash"), Controls(), huge, NOW) == Answer()


def check_a_rows_own_cap_overrides_the_role(_: Tree) -> None:
    """The owner's context cap on this agent's row beats its role's."""
    assert ask(at(context={AGENT: 200_000}), Controls(context_caps={AGENT: 300_000})) == Answer()
    assert ask(at(context={AGENT: 150_000}), Controls(context_caps={AGENT: 150_000})).kind


def check_the_crossing_call_is_denied_with_the_instruction(_: Tree) -> None:
    """The crossing call, whatever its tool, is denied with the hand-off instruction."""
    for tool in ("Bash", "Read"):
        caller = Caller(SESSION, AGENT, tool, ROLE)
        assert verdict(caller, Controls(), at(context={AGENT: 200_000}), NOW).deny == HAND_OFF


def window(spent: Totals, tools: list[str]) -> list[Answer]:
    """Run calls through the gate as the server does: each answer moves the count on."""
    answers, held = [], None
    for tool in tools:
        answer = verdict(Caller(SESSION, AGENT, tool, ROLE, held), Controls(), spent, NOW)
        held = (answer.kind, (held[1] if held else 0) + 1) if answer.kind else None
        answers.append(answer)
    return answers


def check_ten_bash_calls_then_a_stop_and_other_tools_denied(_: Tree) -> None:
    """After the crossing, 10 calls count down, a non-Bash one denied; the 11th stops."""
    answers = window(at(context={AGENT: 250_000}), ["Bash"] * 5 + ["Edit"] + ["Bash"] * 6)
    assert (answers[0].deny or "").startswith("dev-loop gate: agent a0123"), answers[0]
    for count in (1, 2, 3, 4, 6, 7, 8, 9, 10):
        assert answers[count] == Answer(context=left(10 - count), kind="context"), count
    only = "dev-loop gate: only Bash calls in the window: tests, git add/commit, record.py. "
    assert answers[5] == Answer(deny=only + (answers[0].deny or ""), kind="context"), answers[5]
    assert answers[11] == Answer(deny=STOP_NOW, kind="context"), answers[11]


def check_the_hook_never_answers_allow(_: Tree) -> None:
    """Every call in and past a window is a deny or a countdown, never an allow."""
    for answer in window(at(context={AGENT: 250_000}), ["Bash", "Read"] * 8):
        assert bool(answer.deny) != bool(answer.context), answer  # one, never neither or both


def check_a_pause_beats_a_window(_: Tree) -> None:
    """A pause is a hard deny before any window; the open window still counts the call."""
    paused = Controls(paused=frozenset({AGENT}))
    answer = ask(at(context={AGENT: 250_000}), paused, ("context", 3))
    assert answer.kind == "context", answer
    assert (answer.deny or "").startswith("dev-loop gate: this agent is paused by the owner. Stop")
    assert ask(EMPTY, paused).kind is None
    fresh = at(context={AGENT: 250_000})  # crossed while paused: no window opens, the pause denies
    assert ask(fresh, paused).kind is None
    first = ask(fresh)  # the first unpaused call is the crossing call, with the full instruction
    assert first.kind == "context" and (first.deny or "").startswith("dev-loop gate: agent a0123")
    assert ask(EMPTY, Controls(paused=frozenset({SESSION}))).deny


def check_the_window_closes_when_no_cap_is_crossed(_: Tree) -> None:
    """The owner raised the cap, or compaction cut the context: the window closes."""
    assert ask(at(context={AGENT: 120_000}), None, ("context", 4)) == Answer()
    raised = Controls(context_caps={AGENT: 400_000})
    assert ask(at(context={AGENT: 250_000}), raised, ("context", 11)) == Answer()


def check_a_stale_scan_opens_no_window_and_an_open_one_runs_on(_: Tree) -> None:
    """Spend older than 60 s opens no window; an open window counts on and stops."""
    over, stale = at(context={AGENT: 250_000}), NOW + 61
    assert ask(over, now=NOW + 60).kind == "context"
    assert ask(over, now=stale) == Answer()
    assert ask(over, None, ("context", 4), stale) == Answer(context=left(6), kind="context")
    assert ask(EMPTY, None, ("context", 4), stale).kind == "context"  # never closed on stale data
    assert ask(over, None, ("context", 11), stale) == Answer(deny=STOP_NOW, kind="context")


def check_no_text_in_an_instruction(tree: Tree) -> None:
    """No prompt, tool input or document text in any deny or countdown."""
    calls = [user(), assistant("m1", tool="Bash", tokens=(250_000, 0, 0, 0, 0)), tool_result()]
    tree.write(calls, agent=AGENT)
    spent = totals(scanned(tree), NOW - 3600)
    answers = window(spent, ["Bash", "Read"] * 7)
    controls = Controls(paused=frozenset({AGENT}))
    answers.append(verdict(Caller(SESSION, AGENT, "Bash", ROLE), controls, spent, NOW))
    assert all(answer.deny or answer.context for answer in answers)
    assert SECRET not in repr(answers)


if __name__ == "__main__":
    run("dev-loop budgets", [obj for name, obj in list(globals().items()) if name[:6] == "check_"])
