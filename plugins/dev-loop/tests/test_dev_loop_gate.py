"""Self-check for the dev-loop dashboard's spend and gate.

Totals, burn and the gate's pause rules, over fixture transcripts (`test_dev_loop_fixtures`) and
hand-built totals. Run: python tests/test_dev_loop_gate.py; test_self_checks.py runs it.
"""

from __future__ import annotations

import contextlib
import sys
import threading
from typing import TYPE_CHECKING

from dev_loop.dashboard import scan
from dev_loop.dashboard.gate import Answer, Caller, Controls, verdict
from dev_loop.dashboard.scan import Scanner
from dev_loop.dashboard.spend import Totals, totals
from test_dev_loop_fixtures import (
    AGENT,
    DAY,
    MILLION,
    MINUTE,
    NOW,
    SECRET,
    SESSION,
    Record,
    Tree,
    assistant,
    run,
    scanned,
    stamp,
    tool_result,
    user,
)

if TYPE_CHECKING:
    from collections.abc import Callable

OPUS = (MILLION, 0, 0, 0, 0)  # $4 a call
SPENT = Totals(
    at=NOW,
    today_usd=50.0,
    burn_usd_h=0.0,
    usd={SESSION: 20.0, AGENT: 5.0},
    context={SESSION: 400_000, AGENT: 100_000},
)


def check_totals_tree_agent_and_today(tree: Tree) -> None:
    tree.write([assistant("s1", tokens=OPUS, at=stamp(2 * DAY))])
    tree.write([assistant("a1", tokens=OPUS)], agent=AGENT)
    found = totals(scanned(tree), day_start=NOW - 3600)
    assert (round(found.usd[SESSION], 6), round(found.usd[AGENT], 6)) == (8.0, 4.0)
    assert round(found.today_usd, 6) == 4.0
    assert found.context == {SESSION: MILLION, AGENT: MILLION}


def check_burn_is_the_last_15_minutes_spend_times_4(tree: Tree) -> None:
    tree.write([assistant(f"m{age}", tokens=OPUS, at=stamp(age * MINUTE)) for age in (1, 14, 16)])
    tree.write([assistant("a", tokens=OPUS, at=stamp(10 * MINUTE))], agent=AGENT)
    assert round(totals(scanned(tree), day_start=0).burn_usd_h, 6) == 3 * 4.0 * 4


def check_a_message_copied_into_another_session_counts_once(tree: Tree) -> None:
    # Session ids are random: the fork's file may sort before the original's, or after it.
    for original, fork in (("a-original", "b-fork"), ("b-original", "a-fork")):
        each = Tree(tree.root / original)
        each.write([assistant("shared", tokens=OPUS)], original)
        each.write([assistant("shared", tokens=OPUS), assistant("own", tokens=OPUS)], fork)
        found = totals(scanned(each), day_start=0)
        assert round(found.today_usd, 6) == 8.0, original  # two calls, not three
        # Each session holds the copy: the original keeps what it spent.
        assert (round(found.usd[original], 6), round(found.usd[fork], 6)) == (4.0, 8.0), original


def check_the_pass_time_is_read_once_with_the_data(tree: Tree) -> None:
    class Completing:  # a pass completes as the reader takes the lock
        def __enter__(self) -> None:
            scanner.scanned_at = NOW + 3600

        def __exit__(self, *_: object) -> None:
            return None

    tree.write([assistant("m", tokens=OPUS, at=stamp(MINUTE))])
    scanner = scanned(tree)
    scanner.lock = Completing()  # type: ignore[assignment]
    found = totals(scanner, day_start=0)
    assert (found.at, found.burn_usd_h) == (NOW + 3600, 0.0)  # the call is an hour old by then


def check_totals_are_read_whole_while_a_pass_runs(tree: Tree) -> None:
    for n in range(8):  # distinct ids: one id counts once across transcripts
        records: list[Record | str] = [assistant(f"{n}-{k}", tokens=OPUS) for k in range(300)]
        tree.write(records, f"s{n}")
    scanner, done, errors = Scanner("app", 7, tree.root), threading.Event(), list[str]()

    def read() -> None:  # what part b's hook does while the first index runs
        while not done.is_set():
            try:
                totals(scanner, day_start=0)
            except RuntimeError as error:  # "dictionary changed size during iteration"
                errors.append(repr(error))
                return

    reader, chunk, scan.CHUNK = threading.Thread(target=read), scan.CHUNK, 4096
    switch = sys.getswitchinterval()
    sys.setswitchinterval(1e-5)  # switch threads often, so a race shows on every run
    try:
        reader.start()
        scanner.refresh()
    finally:
        done.set()
        reader.join()
        scan.CHUNK = chunk
        sys.setswitchinterval(switch)
    assert errors == []
    found = totals(scanner, day_start=0).usd
    assert {session: round(usd, 6) for session, usd in found.items()} == {
        f"s{n}": 300 * 4.0 for n in range(8)
    }


def check_totals_carry_turns_model_and_peak(tree: Tree) -> None:
    """Per agent, its calls once by id, its latest model and its peak."""
    sonnet = "claude-sonnet-5-5"
    first, peak = assistant("a1", at=stamp(300)), assistant("a2", tokens=(10, 0, 0, 0, 9_000))
    last = assistant("a3", model=sonnet, tokens=(10, 0, 0, 0, 2_000))
    tree.write([first, peak, peak, last], agent=AGENT)
    tree.write([assistant("s1", tokens=(50_000, 0, 0, 0, 0), at=stamp(900))])  # a session: no row
    found = totals(scanned(tree), day_start=0)
    assert found.turns == {AGENT: 3}, found.turns
    assert found.model == {AGENT: sonnet}, found.model
    assert found.peak == {AGENT: 9_010}, found.peak  # the largest call, not the latest (2,010)


def deny(controls: Controls, now: float = NOW) -> str:
    """Return the deny reason of the agent's call, or "" when the gate allows."""
    return verdict(Caller(SESSION, AGENT, "Bash"), controls, SPENT, now).deny or ""


def check_no_control_allows(_: Tree) -> None:
    assert verdict(Caller(SESSION, AGENT, "Bash"), Controls(), SPENT, NOW) == Answer()


def check_a_stale_scan_still_enforces_pauses(_: Tree) -> None:
    reason = deny(Controls(paused=frozenset({AGENT})), now=NOW + 61)
    assert "agent is paused" in reason


def check_pause(_: Tree) -> None:
    assert "session is paused" in deny(Controls(paused=frozenset({SESSION})))
    assert "agent is paused" in deny(Controls(paused=frozenset({AGENT})))
    assert not deny(Controls(paused=frozenset({"another-agent"})))


def check_a_deny_names_its_reason_and_says_stop(_: Tree) -> None:
    reason = deny(Controls(paused=frozenset({SESSION})))
    assert "paused" in reason
    assert "budget_exhausted" in reason
    assert "Do not retry" in reason


def check_controls_refuse_a_cap_out_of_range(_: Tree) -> None:
    bad: list[Callable[[], Controls]] = [
        lambda: Controls(context_caps={AGENT: 0}),
        lambda: Controls(context_caps={AGENT: -5}),
        lambda: Controls(context_caps={AGENT: 1_000_001}),
        lambda: Controls(context_caps={AGENT: 2_000_000}),
        # json.loads reads NaN and Infinity: a cap silently off
        lambda: Controls(context_caps={AGENT: float("nan")}),  # type: ignore[dict-item]
        lambda: Controls(context_caps={AGENT: float("inf")}),  # type: ignore[dict-item]
        lambda: Controls(context_caps={AGENT: True}),
    ]
    for number, make in enumerate(bad):
        with contextlib.suppress(ValueError):
            make()
            raise AssertionError(number)


def check_no_text_in_a_reason(tree: Tree) -> None:
    tree.write([user(), assistant("m1", tool="Bash"), tool_result()])
    tree.write([user(), assistant("m2", tool="Read"), tool_result()], agent=AGENT)
    scanner = scanned(tree)
    spent = totals(scanner, NOW - 3600)
    ids = [t.agent_id or t.session_id for t in scanner.transcripts.values()]
    callers = [Caller(SESSION, agent, "Bash") for agent in ids]
    capped = Controls(paused=frozenset({SESSION}), context_caps=dict.fromkeys(ids, 1))
    reasons = [verdict(caller, capped, spent, NOW).deny for caller in callers]
    assert all(reasons)
    assert SECRET not in repr(reasons)


if __name__ == "__main__":
    run("dev-loop gate", [obj for name, obj in list(globals().items()) if name[:6] == "check_"])
