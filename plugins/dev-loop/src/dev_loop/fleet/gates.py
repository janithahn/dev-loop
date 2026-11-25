"""Gate series for `dl fleet-export --delivery`: a repository's finished PR pipeline runs.

The ADO runs are merged into a kept history, one file per repository under `~/.dev-loop`, written
under record_lock's lock (no fcntl: a Windows host has none); an entry older than 400 days is
dropped. Labels are ids, results and counts only.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ..records.record_lock import locked, replace_text

if TYPE_CHECKING:
    from collections.abc import Callable

HOME = Path.home() / ".dev-loop"
KEEP = 400 * 86400
RESULTS = ("succeeded", "partiallySucceeded", "failed", "canceled")  # a finished run's results
Row = dict[str, Any]
Point = tuple[str, dict[str, str], float]


def seconds(stamp: str) -> float:
    return datetime.fromisoformat(stamp[:19]).replace(tzinfo=UTC).timestamp()


def history(repo: Row) -> Path:
    """One file per org, project and repository: a repository's name alone is not unique."""
    name = "/".join(str(repo[key]) for key in ("org", "project", "repo"))
    return HOME / f"gate-runs-{quote(name, safe='')}.jsonl"  # percent-coded: one name, one file


def new_runs(builds: list[Row], known: set[int]) -> list[Row]:
    """The finished PR-merge runs not yet kept, as id, pr, result, finish time and duration."""
    found = []
    for build in builds:
        pr = re.fullmatch(r"refs/pull/(\d+)/merge", build.get("sourceBranch", ""))
        if pr and build.get("result") in RESULTS and build["id"] not in known:
            end, began = seconds(build["finishTime"]), build.get("startTime")
            took = end - seconds(began) if began else None  # canceled while queued: never began
            found.append({"build": build["id"], "pr": int(pr[1]), "result": build["result"],
                          "finished": end, "duration": took})
    return found


def merged(repo: Row, builds: list[Row], now: float) -> list[Row]:
    """Merge `builds` into the repository's kept history under a lock; return what is kept."""
    path = history(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    # overlapping crons: one writer at a time. ponytail: one lock for every repository's history
    # (the write is a few ms); a lock per history if the list grows past what 10 s of waiting fits
    with locked(path.parent):
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        kept = [run for run in map(json.loads, lines) if run["finished"] >= now - KEEP]
        kept += new_runs(builds, {run["build"] for run in kept})
        replace_text(path, "".join(json.dumps(run) + "\n" for run in kept))
    return kept


def points(
    repo: Row, get: Callable[[Row, str], Any], now: float, say: Callable[[str], None]
) -> list[Point]:
    """Run count by result and one duration (minutes) per run since the 30-day window start."""
    if repo.get("pipeline") is None:  # unstated or null: the row names no gate
        return []
    if repo.get("provider", "ado") != "ado":
        say(f"delivery: {repo['repo']} gate series not run (provider is not ado)")
        return []
    start = now - 30 * 86400
    since = datetime.fromtimestamp(start, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = f"build/builds?definitions={int(repo['pipeline'])}&minTime={since}&$top=1000"
    kept = merged(repo, get(repo, path)["value"], now)
    window = [run for run in kept if run["finished"] >= start]
    counts = dict.fromkeys(RESULTS, 0)  # a result with no run is a stated 0, not a stale series
    out: list[Point] = []
    for run in window:
        counts[run["result"]] += 1
        # `build` keeps a rerun with the same result its own series (bounded: 30 days of runs)
        labels = {"pr": str(run["pr"]), "build": str(run["build"]), "result": run["result"]}
        if run["duration"] is not None:  # a run that never began is counted, with no minutes
            out.append(("gate_run_minutes", labels, run["duration"] / 60))
    return out + [("gate_runs", {"result": r}, n) for r, n in counts.items()]
