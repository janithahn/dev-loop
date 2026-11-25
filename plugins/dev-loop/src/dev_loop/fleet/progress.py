"""Delivery series from a checkout's change records: review rounds and findings per PR, and each
change at its stage. Called by delivery.py; reads files only. Labels: ids, counts, stage names.
"""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Any

from ..records import record

if TYPE_CHECKING:
    from pathlib import Path

SEVERITIES = ("blocker", "major", "minor")
STAGES = ("planned", "building", "in_review", "merged", "done")
Point = tuple[str, dict[str, str], float]
Row = dict[str, Any]


def rounds_of(prs: list[Row], folders: list[Path]) -> dict[int, list[Row]]:
    """The review rounds of each reported PR, from any change folder (a PR id is text in a record)."""
    wanted = {str(pr["pullRequestId"]): pr["pullRequestId"] for pr in prs}
    rounds: dict[int, list[Row]] = {}
    for folder in folders:
        for found in record.read(folder):
            if found["kind"] == "round" and str(found.get("pr")) in wanted:
                rounds.setdefault(wanted[str(found["pr"])], []).append(found)
    return rounds


def p50_rounds(prs: list[Row], rounds: dict[int, list[Row]]) -> float | None:
    """The median review rounds of these PRs; None when none has a round."""
    counts = [len(rounds[pr["pullRequestId"]]) for pr in prs if pr["pullRequestId"] in rounds]
    return statistics.median(counts) if counts else None


def stage(folder: Path, states: set[str], root: Path) -> str:
    """The change's stage; `root` is its checkout, since cron's working directory is not.

    `states` holds the change's PRs of the last 30 days only. With none of them, a change whose
    tasks are all done and whose records hold a review round had its PR closed before the window:
    it stays `merged`, so a stage never steps back to `building` as the window moves on.
    ponytail: a PR abandoned that long ago with every task ticked reads as merged too; ask the
    host for the recorded PR's state by id if that is ever seen.
    """
    if "active" in states:
        return "in_review"
    if not record.missing(folder, root=root):
        return "done"
    if "completed" in states or (not states and reviewed_and_built(folder)):
        return "merged"
    return "building" if (folder / "tasks.md").exists() else "planned"


def reviewed_and_built(folder: Path) -> bool:
    """Every task done and a review round on record: the change had a PR, and nothing is left."""
    reviewed = any(found["kind"] == "round" for found in record.read(folder))
    return reviewed and record.tasks_done(folder)


def points(
    prs: list[Row], folders: list[Path], rounds: dict[int, list[Row]], root: Path
) -> list[Point]:
    """Rounds and findings by severity per PR (`rounds`: rounds_of's), each change's stage and the
    counts by stage."""
    found: list[Point] = []
    for pr, done in rounds.items():
        found.append(("pr_rounds", {"pr": str(pr)}, len(done)))
        for sev in SEVERITIES:
            total = sum(int(one.get(sev) or 0) for one in done)
            found.append(("pr_findings", {"pr": str(pr), "severity": sev}, total))
    counts = dict.fromkeys(STAGES, 0) if folders else {}  # a stage with no change: a stated 0
    for folder in folders:
        states = {pr["status"] for pr in prs if pr.get("change") == folder.name}
        at = stage(folder, states, root)
        counts[at] += 1
        found.append(("change", {"change": folder.name, "stage": at}, 1))
    return found + [("changes", {"stage": at}, n) for at, n in counts.items()]
