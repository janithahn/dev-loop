"""The weekly delivery table: `review_records.py metrics --weeks N`.

Per ISO week: a PR counts when it closed, a run of the
`metrics.deploy_pipeline` when it finished, a review round when its PR opened. A median or rate with
nothing under it shows —, never 0. Lead time is of PRs into `branches.integration`; deploys are runs
on `branches.release` (null: the integration branch is what deploys). Transcript tokens per role are
from the dashboard's scanner. The host is reached only through the adapter (`hosts/ado.py`).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any, NamedTuple
from urllib.parse import urlencode

from .. import config
from ..hosts import HostError
from ..hosts import ado as host
from .review_lessons import iso_week

NEEDED = ("host.repo", "metrics.deploy_pipeline")
TOP = 1000  # rows per request: a full page is refused, never cut
HOTFIX = re.compile(r"hotfix|revert|rollback", re.IGNORECASE)
HEADER = (
    "| Week | Merged | Lead (d) | Release merges | Merge→deploy (h) | Hotfix | Rework | Deploys "
    "| Fail rate | Restore (h) | Review tok/call | Rounds to clean (4 wk) |"
)
Row = dict[str, Any]


class Ado(NamedTuple):
    """The host's PRs and deploy runs, and the refs they are judged by."""

    prs: list[Row]
    runs: list[Row]
    integration: str  # refs/heads/<branches.integration>: lead time
    release: str  # refs/heads/<branches.release>, else the integration ref: deploys


def listing(path: str, query: Mapping[str, object]) -> list[Row]:
    """GET one host list through the adapter; refuse a page that fills the request cap."""
    try:
        answer = host.request("GET", f"/_apis/{path}?{urlencode({**query, '$top': TOP})}")
    except HostError as error:
        raise ValueError(f"{path}: {error}") from None
    rows: list[Row] = answer.get("value") or []
    if len(rows) >= TOP:
        raise ValueError(f"{path}: {TOP} rows, the request cap; ask for fewer --weeks")
    return rows


def fetch(now: datetime, weeks: int) -> Ado:
    """Return the PRs opened from three weeks before the first week shown, and the deploy runs."""
    loaded = config.load(config.project_root())
    gone = [key for key in NEEDED if not loaded.get(key)]
    if gone:
        raise ValueError(f"{', '.join(gone)} not set in dev-loop.yaml")
    integration = f"refs/heads/{loaded.get('branches.integration')}"
    release = f"refs/heads/{loaded.get('branches.release') or loaded.get('branches.integration')}"
    monday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(now.weekday())
    since = (monday - timedelta(weeks=weeks + 2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    created = {"searchCriteria.status": "all", "searchCriteria.queryTimeRangeType": "created"}
    path = f"git/repositories/{loaded.get('host.repo')}/pullrequests"
    prs = listing(path, created | {"searchCriteria.minTime": since})
    deploy = {"definitions": loaded.get("metrics.deploy_pipeline"), "branchName": release}
    runs = listing("build/builds", deploy | {"minTime": since})
    return Ado(prs, runs, integration, release)


def when(stamp: str) -> datetime:
    """Parse an ADO timestamp (up to 7 fraction digits, Z) as UTC, to the second."""
    return datetime.fromisoformat(stamp[:19]).replace(tzinfo=timezone.utc)  # noqa: UP017 - 3.10


def hours(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600


def cell(values: list[float]) -> str:
    return f"{median(values):.1f}" if values else "—"


def share(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "—"


def deploys(runs: list[Row]) -> tuple[list[datetime], list[datetime]]:
    """Return each version's first deploy, and failures of versions not yet shipped (no reruns)."""
    ended = [r for r in runs if r.get("result") in ("succeeded", "failed")]
    shipped: dict[str, datetime] = {}
    failures = []
    for run in sorted(ended, key=lambda r: when(r["finishTime"])):
        if run["result"] == "succeeded":
            shipped.setdefault(run["sourceVersion"], when(run["finishTime"]))
        elif run["sourceVersion"] not in shipped:
            failures.append(when(run["finishTime"]))
    return sorted(shipped.values()), failures


def after(moments: list[datetime], deployed: list[datetime]) -> tuple[list[float], str]:
    """Return the hours from each moment to the next deploy, and `+k open` for those with none."""
    nexts = [(m, min((d for d in deployed if d > m), default=None)) for m in moments]
    waits = [hours(moment, deploy) for moment, deploy in nexts if deploy]
    still = len(moments) - len(waits)
    return waits, f" +{still} open" if still else ""


def pull_cells(ado_rows: Ado, deployed: list[datetime], week: str) -> list[str]:
    """Return merged, lead (d), release merges, merge→deploy (h), hotfix and rework of `week`."""
    done = [p for p in ado_rows.prs if p["status"] == "completed"]
    done = [p for p in done if iso_week(when(p["closedDate"])) == week]
    merged = [p for p in done if p["targetRefName"] == ado_rows.integration]
    lead = [hours(when(p["creationDate"]), when(p["closedDate"])) / 24 for p in merged]
    merges = [when(p["closedDate"]) for p in done if p["targetRefName"] == ado_rows.release]
    lag, pending = after(merges, deployed)
    hot = [p for p in done if HOTFIX.search(f"{p['title']} {p['sourceRefName']}")]
    counts = [str(len(merged)), cell(lead), str(len(merges)), cell(lag) + pending, str(len(hot))]
    return [*counts, share(len(hot), len(done))]


def run_cells(deployed: list[datetime], failures: list[datetime], week: str) -> list[str]:
    """Return deploys, fail rate, restore (h; `+k open`: failures with no deploy after them)."""
    shipped = [t for t in deployed if iso_week(t) == week]
    lost = [t for t in failures if iso_week(t) == week]
    restore, still = after(lost, deployed)
    return [str(len(shipped)), share(len(lost), len(shipped) + len(lost)), cell(restore) + still]


def tokens_cell(records: list[Row], opened: dict[int, str], week: str) -> str:
    """Return (Σ tokens in + out) ÷ Σ turns of the metered rounds whose PR opened in `week`."""
    rounds = [r for r in records if r["kind"] == "round" and isinstance(r.get("turns"), int)]
    mine = [r for r in rounds if opened.get(r["pr"]) == week]
    turns, unmetered = sum(r["turns"] for r in mine), sum(r["unmetered"] for r in mine)
    tokens = sum(r["tokens_in"] + r["tokens_out"] for r in mine)
    note = f" +{unmetered} unmetered" if unmetered else ""
    return (f"{tokens // turns}" if turns else "—") + note


def rounds_cell(records: list[Row], opened: dict[int, str], window: list[str]) -> str:
    """Return mean rounds to clean per builder/reviewer model of the PRs opened in `window`."""
    rounds = {r["pr"]: r["rounds"] for r in records if r["kind"] == "metrics"}  # a later copy wins
    models = {r["pr"]: f"{r['builder']}/{r['reviewer']}" for r in records if r["kind"] == "models"}
    groups: dict[str, list[int]] = defaultdict(list)
    for pr, count in rounds.items():
        if opened.get(pr) in window and count is not None:
            groups[models.get(pr, "?/?").replace("claude-", "")].append(count)
    ranked = sorted(groups.items(), key=lambda group: -len(group[1]))
    return "; ".join(f"{name} {mean(c):.1f} (n={len(c)})" for name, c in ranked) or "—"


def table(ado_rows: Ado, records: list[Row], now: datetime, weeks: int) -> list[str]:
    """Return the header, its separator and a row per week, the `weeks` to `now`, newest first."""
    labels = [iso_week(now - timedelta(weeks=n)) for n in range(weeks + 3)]
    opened = {p["pullRequestId"]: iso_week(when(p["creationDate"])) for p in ado_rows.prs}
    deployed, failures = deploys(ado_rows.runs)
    lines = [HEADER, "|---" * HEADER.count(" | ") + "|---|"]
    for n, week in enumerate(labels[:weeks]):
        cells = [week, *pull_cells(ado_rows, deployed, week), *run_cells(deployed, failures, week)]
        review = [
            tokens_cell(records, opened, week),
            rounds_cell(records, opened, labels[n : n + 4]),
        ]
        lines.append(f"| {' | '.join(cells + review)} |")
    return lines
