"""Delivery series: `dl fleet-export --delivery`.

Per repository in `~/.dev-loop/repos.json` (or `DEV_LOOP_REPOS`): PRs touched in 30 days and the
week's throughput, each series labelled `repo`; gate runs in `gates.py`. Ids, counts and error classes only: no PR title,
description, branch or author is ever a label. The token comes from the row's `token_env`.
"""

from __future__ import annotations

import base64
import json
import os
import re
import statistics
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import load
from ..records import record
from . import gates, progress
from .transport import fetch

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

HOME = Path.home() / ".dev-loop"
REPOS = Path(os.environ.get("DEV_LOOP_REPOS") or HOME / "repos.json")
BROKEN = (OSError, ValueError, LookupError, TypeError, AttributeError)  # unreachable or malformed
FAILED = (*BROKEN, re.error)  # or a worker_pattern that is not a regex
KEYS = ("state", "opened_by", "target")  # dev_loop_prs' labels, in its key's order
TOKEN_ENV = "DEV_LOOP_HOST_TOKEN"
Point = tuple[str, dict[str, str], float]
Row = dict[str, Any]


def seconds(stamp: str) -> float:
    return datetime.fromisoformat(stamp[:19]).replace(tzinfo=UTC).timestamp()


def ado(repo: Row, path: str) -> Any:  # noqa: ANN401 - JSON
    """GET one ADO API of the repo's project with the token its row names (KeyError if unset)."""
    token = os.environ[repo.get("token_env", TOKEN_ENV)]
    url = f"https://dev.azure.com/{repo['org']}/{repo['project']}/_apis/{path}&api-version=7.1"
    auth = base64.b64encode(f":{token}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})  # noqa: S310
    return fetch(request)


def pulls(repo: Row, since: str) -> list[Row]:
    """Every active PR and every PR closed since `since`, one row per PR."""
    path = f"git/repositories/{urllib.parse.quote(repo['repo'])}/pullrequests?$top=1000"
    closed = f"all&searchCriteria.queryTimeRangeType=closed&searchCriteria.minTime={since}"
    reads = [ado(repo, f"{path}&searchCriteria.status={s}") for s in ("active", closed)]
    return list({row["pullRequestId"]: row for read in reads for row in read["value"]}.values())


def change_of(branch: str, changes: list[str]) -> str:
    """The longest change the branch's last segment is, or starts with followed by `-`."""
    tail = branch.rsplit("/", 1)[-1]
    hits = [name for name in changes if tail == name or tail.startswith(name + "-")]
    return max(hits, key=len, default="")


def opened_by(repo: Row, pr: Row) -> str:
    """worker when the repo's `worker_pattern` matches the PR description, else by its author."""
    marks = repo.get("worker_pattern")  # the repo's own convention
    if marks and re.search(marks, pr.get("description") or "", re.MULTILINE):
        return "worker"
    who, coordinator = pr["createdBy"].get("uniqueName", ""), repo.get("coordinator", "")
    return "coordinator" if coordinator and who.lower() == coordinator.lower() else "teammate"


def pr_points(repo: Row, prs: list[Row], changes: list[str], now: float) -> list[Point]:
    """One age series per PR and the counts by state, opener and target; tags each change."""
    points: list[Point] = []
    counts: dict[tuple[str, str, str], int] = {}
    for pr in prs:
        branch = pr["sourceRefName"].removeprefix("refs/heads/")
        pr["change"] = change_of(branch, changes)
        target = pr["targetRefName"].removeprefix("refs/heads/")
        key = (pr["status"], opened_by(repo, pr), target)
        counts[key] = counts.get(key, 0) + 1
        labels = dict(zip(KEYS, key, strict=True))
        pr["ended"] = seconds(pr["closedDate"]) if pr.get("closedDate") else now
        labels |= {"pr": str(pr["pullRequestId"]), "change": pr["change"]}
        points.append(("pr", labels, (pr["ended"] - seconds(pr["creationDate"])) / 3600))
    return points + [("prs", dict(zip(KEYS, key, strict=True)), n) for key, n in counts.items()]


def week_of(prs: list[Row], now: float) -> list[Row]:
    return [pr for pr in prs if pr["status"] == "completed" and pr["ended"] >= now - 7 * 86400]


def throughput(prs: list[Row], now: float) -> list[Point]:
    """PRs completed in the last 7 days and their p50 lead time; none, no p50."""
    week = week_of(prs, now)
    lead = [(pr["ended"] - seconds(pr["creationDate"])) / 3600 for pr in week]
    points: list[Point] = [("merged_week", {}, len(week))]
    return points + ([("lead_time_hours_p50", {}, statistics.median(lead))] if lead else [])


def change_folders(repo: Row) -> list[Path]:
    """The checkout's change folders, never its archive: none when it has no such folder.

    The checkout's own dev-loop.yaml names both; a row's `changes` names another changes folder.
    """
    root = Path(repo["checkout"])
    if "changes" not in repo:
        return record.change_folders(root)
    archive = root / str(load(root).get("paths.archive"))
    return sorted(p for p in (root / repo["changes"]).glob("*") if p.is_dir() and p != archive)


def repo_points(repo: Row, now: float, seen: Row) -> list[Point]:
    """One repository's PR series; its PRs and change folders stay in `seen` for the records."""
    since = datetime.fromtimestamp(now - 30 * 86400, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    folders = change_folders(repo)
    prs = pulls(repo, since)
    points = pr_points(repo, prs, [p.name for p in folders], now)
    seen |= {"prs": prs, "folders": folders}
    return points + throughput(prs, now)


def record_points(repo: Row, now: float, seen: Row) -> list[Point]:
    """Rounds, findings, stages and the week's p50 review rounds, from the checkout's records."""
    if "prs" not in seen:  # the PR source failed and was said: a stage without its PRs is wrong
        return []
    prs, folders = seen["prs"], seen["folders"]
    rounds = progress.rounds_of(prs, folders)  # read once, for the p50 and for each PR's series
    p50 = progress.p50_rounds(week_of(prs, now), rounds)
    points: list[Point] = [("review_rounds_p50", {}, p50)] if p50 is not None else []
    return points + progress.points(prs, folders, rounds, Path(repo["checkout"]))


def sources(
    repo: Row, now: float, say: Callable[[str], None]
) -> Iterator[tuple[str, Callable[[], list[Point]]]]:
    """One repository's sources, each read on its own: one that fails keeps the others' series."""
    seen: Row = {}
    yield "", lambda: repo_points(repo, now, seen)
    yield " records", lambda: record_points(repo, now, seen)
    yield " gates", lambda: gates.points(repo, ado, now, say)


def gather(now: float, say: Callable[[str], None]) -> tuple[list[Point], bool]:
    """Every listed repository's points, and False when one's source failed (said on stderr)."""
    if not REPOS.exists():
        say(f"delivery: skipped, no repo list at {REPOS}")
        return [], True
    points: list[Point] = []
    ok = True
    for repo in json.loads(REPOS.read_text(encoding="utf-8"))["repos"]:
        for part, read in sources(repo, now, say):
            try:
                points += [(m, {"repo": repo["repo"]} | lab, v) for m, lab, v in read()]
            except FAILED as error:
                ok = False
                say(f"delivery: {repo.get('repo')}{part} failed ({type(error).__name__})")
    return points, ok
