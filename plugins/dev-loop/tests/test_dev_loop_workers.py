"""Self-check for the dashboard's background threads and Restart.

The scan loop outlives a failing pass, a dead thread is revived, and Restart re-reads every
transcript without doubling spend or ending on a partial index. Run: python tests/test_dev_loop_workers.py; test_self_checks.py runs it.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from dev_loop.dashboard import scan
from dev_loop.dashboard.gate import Caller, Controls, verdict
from dev_loop.dashboard.scan import Scanner
from dev_loop.dashboard.spend import totals
from dev_loop.dashboard.workers import Workers
from test_dev_loop_fixtures import NOW, SESSION, Tree, assistant, run, scanned, user

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable, Iterator

DENIED = "denied"
OPUS = (1_000_000, 0, 0, 0, 0)  # $4 a call


def check_the_scan_loop_outlives_a_failing_pass_and_a_dead_thread_is_revived(tree: Tree) -> None:
    scanner, original = Scanner("app", 7, tree.root), Scanner.scan

    def failing(_: Scanner) -> Iterator[scan.Transcript]:
        raise PermissionError(DENIED)

    Scanner.scan = failing  # type: ignore[method-assign,assignment]
    workers = Workers(scanner, interval=0.01)
    try:
        assert workers.start() == ["scanner"]
        time.sleep(0.2)
        assert workers.threads["scanner"].is_alive()
        assert scanner.scan_error is not None
        assert "PermissionError" in scanner.scan_error
        assert workers.start() == []  # alive: nothing started twice
        workers.stopped.set()
        workers.threads["scanner"].join(timeout=10)
        workers.stopped.clear()
        assert workers.start() == ["scanner"]  # the dead thread is revived
    finally:
        workers.stopped.set()
        workers.threads["scanner"].join(timeout=10)
        Scanner.scan = original  # type: ignore[method-assign]


def check_restart_wakes_the_pollers_and_revives_only_the_dead(tree: Tree) -> None:
    workers = Workers(scanned(tree), interval=0.01)
    workers.scanner.refresh = lambda: None  # type: ignore[method-assign]  # keep RESTART set

    def poller() -> None:
        workers.stopped.wait(30)

    workers.targets["poller"] = poller
    try:
        assert workers.restart() == ["scanner", "poller"]
        assert workers.wake.is_set()
        assert scan.RESTART.is_set()
        assert workers.restart() == []
    finally:
        workers.stopped.set()
        for thread in workers.threads.values():
            thread.join(timeout=10)
        scan.RESTART.clear()


def check_a_restart_mid_pass_rebuilds_a_full_index(tree: Tree) -> None:
    sessions = [f"{n:08d}-2f43-4c55-9a51-1d2e3f4a5b6c" for n in range(6)]
    for name in sessions:
        tree.write([user(), assistant(f"m-{name}", "end_turn", tokens=(1000, 10, 0, 0, 0))], name)
    scanner = scanned(tree)
    whole = len(scanner.transcripts)
    spent = sum(c.usd for t in scanner.transcripts.values() for c in t.calls.values())
    reads, original = [], scan.Transcript.read_new

    def pressing(self: scan.Transcript, written: float, lock: threading.RLock) -> None:
        reads.append(1)
        if len(reads) == 2:  # the owner presses Restart again in the middle of the pass
            scan.RESTART.set()
        original(self, written, lock)

    scan.Transcript.read_new = pressing  # type: ignore[method-assign]
    try:
        assert whole == len(sessions)
        scan.RESTART.set()
        scanner.refresh()  # abandoned at the next file: never complete on a partial index
        assert scanner.scanned_at == 0.0
        assert 0 < len(scanner.transcripts) < whole
        scanner.refresh()  # starts over and reads every transcript again
        assert len(scanner.transcripts) == whole
        assert scanner.scanned_at > 0
        again = sum(c.usd for t in scanner.transcripts.values() for c in t.calls.values())
        assert abs(again - spent) < 1e-9  # nothing doubled
    finally:
        scan.Transcript.read_new = original  # type: ignore[method-assign]
        scan.RESTART.clear()


def check_during_a_restart_pass_the_gate_enforces_pauses_only(tree: Tree) -> None:
    tree.write([user(), assistant("m1", "end_turn", tokens=OPUS)])
    scanner, seen = scanned(tree), []
    controls = Controls(paused=frozenset({"paused-one"}), context_caps={SESSION: 1_000})
    callers = [Caller(SESSION, who, "Bash") for who in (SESSION, "paused-one")]
    original = scan.Transcript.read_new

    def watching(self: scan.Transcript, written: float, lock: threading.RLock) -> None:
        spent = totals(scanner, NOW - 3600)
        seen.append([verdict(who, controls, spent, time.time()).deny for who in callers])
        original(self, written, lock)

    spent = totals(scanner, NOW - 3600)
    assert verdict(callers[0], controls, spent, time.time()).deny  # fresh: over the context cap
    scan.RESTART.set()
    scan.Transcript.read_new = watching  # type: ignore[method-assign]
    try:
        scanner.refresh()
    finally:
        scan.Transcript.read_new = original  # type: ignore[method-assign]
        scan.RESTART.clear()
    capped, paused = seen[0]
    assert capped is None  # the context is unknown mid-pass: the cap waits
    assert paused  # the pause holds
    assert verdict(callers[0], controls, totals(scanner, NOW - 3600), time.time()).deny


if __name__ == "__main__":
    checks: list[Callable[[Tree], None]] = [
        obj for name, obj in list(globals().items()) if name[:6] == "check_"
    ]
    run("dev-loop workers", checks)
