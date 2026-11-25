"""What the scan spent: today's spend, the burn rate, and spend and context per session and agent.

The hook and page read it in memory,
never from a file. It is summed under the scanner's lock, so it never sees a torn chunk;
taken during a pass, it holds what the pass has read so far.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scan import Scanner

BURN_SECONDS = 15 * 60  # the burn rate is this window's spend, per hour


@dataclass(frozen=True)
class Totals:
    """What the gate reads. Session and agent ids share the maps: they never collide."""

    at: float  # the last completed pass: the gate trusts spend for 60 s after it
    today_usd: float
    burn_usd_h: float  # the last 15 minutes' spend, times 4
    usd: dict[str, float]  # a session's whole tree, subagents included; an agent's own
    context: dict[str, int]  # a session's main transcript; an agent's own
    turns: dict[str, int] = field(default_factory=dict)  # per agent: its calls, once by message id
    model: dict[str, str] = field(default_factory=dict)  # its latest call's model
    peak: dict[str, int] = field(default_factory=dict)  # its largest call context


def totals(scanner: Scanner, day_start: float) -> Totals:
    """Sum the scan under its lock; a message id counts once in today's spend and the burn.

    A forked session copies earlier messages, ids and all. Each session that holds a copy counts it
    in its own spend, so the original keeps what it spent and a cap never under-counts.
    """
    today, burn, usd, context = 0.0, 0.0, dict[str, float](), dict[str, int]()
    turns, model, peak = dict[str, int](), dict[str, str](), dict[str, int]()
    seen: set[str] = set()
    with scanner.lock:
        at = scanner.scanned_at  # read once, with the data it describes
        for transcript in scanner.transcripts.values():
            new = [call for key, call in transcript.calls.items() if key not in seen]
            seen.update(transcript.calls)
            today += sum(call.usd for call in new if call.at >= day_start)
            burn += sum(call.usd for call in new if call.at >= at - BURN_SECONDS)
            spent = sum(call.usd for call in transcript.calls.values())
            usd[transcript.session_id] = usd.get(transcript.session_id, 0.0) + spent
            if transcript.agent_id:
                usd[transcript.agent_id] = spent
            context[transcript.agent_id or transcript.session_id] = transcript.context
            if (agent := transcript.agent_id) and (calls := list(transcript.calls.values())):
                turns[agent] = len(calls)
                latest = max(reversed(calls), key=lambda call: call.at)  # a tie: the last
                model[agent] = latest.model
                peak[agent] = max(call.context for call in calls)
    burn_h = burn * 3600 / BURN_SECONDS
    return Totals(at, today, burn_h, usd, context, turns, model, peak)
