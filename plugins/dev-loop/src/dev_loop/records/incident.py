"""Log one incident of the loop itself (a lost build, a wrong target, a full disk) like a lesson.

    dl incident <change> what=... impact=... root_cause=... guard=...

The writer dates it and appends it to the change's incidents.md and records.jsonl; an entry
missing a field is refused (exit 1, nothing written), naming the field. `dl records lessons` and
`trend` count incidents by root cause, as their own class. No customer data or document text.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from ..config import project_root
from . import record

USAGE = "usage: dl incident <change> what=... impact=... root_cause=... guard=..."

TREND_HEADER = "| Week | Incidents | Root causes |\n|---|---|---|"


def incidents(root: Path) -> list[dict[str, Any]]:
    """Every incident record under the changes folder, archived changes' too, with its change."""
    found = [(p.parent, r) for p in sorted(record.changes_dir(root).rglob("records.jsonl"))
             for r in record.read(p.parent) if r["kind"] == "incident"]
    return [r | {"change": folder.name} for folder, r in found]


def counts(root: Path) -> list[tuple[int, int, str, str]]:
    """(changes, incidents, `incident: <root cause>`, latest guard) per root cause."""
    by_cause: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for found in incidents(root):
        by_cause[f"incident: {found['root_cause']}"].append(found)
    return [(len({x["change"] for x in made}), len(made), cause,
             max(made, key=lambda x: x["date"])["guard"]) for cause, made in by_cause.items()]


def trend(root: Path) -> list[str]:
    """Incidents and distinct root causes per ISO week, newest first; none: no lines."""
    weeks: dict[str, list[str]] = defaultdict(list)
    for found in incidents(root):
        year, week, _ = date.fromisoformat(found["date"]).isocalendar()
        weeks[f"{year}-W{week:02d}"].append(found["root_cause"])
    rows = [f"| {w} | {len(weeks[w])} | {len(set(weeks[w]))} |" for w in sorted(weeks)[::-1]]
    return ["", TREND_HEADER, *rows] if rows else []


def main(argv: list[str]) -> int:
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    change, *pairs = argv
    try:
        written = record.write(project_root(), change, record.parse("incident", pairs))
    except ValueError as error:
        print(f"dl incident: refused: {error}", file=sys.stderr)
        return 1
    print(written["line"])
    print("not committed: commit it with the change's records before dl pr or dl review",
          file=sys.stderr)
    return 0
