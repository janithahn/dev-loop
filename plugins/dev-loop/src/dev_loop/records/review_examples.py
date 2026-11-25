"""`dl records examples`: draft candidate worked review examples from this project's records.

    dl records examples [--limit N]

Reads the change sidecars only, drafts Markdown for a person to pick from, and writes nothing. It
never invents a case:
  - each fixed blocker or major (a logged lesson) as a finding object, its root cause the Why;
  - each open deferred minor (a follow-up) as a minor finding object, its why the Why;
  - findings closed as not-a-problem are not recorded by the loop, so none are drafted (stated).
The examples that teach a judgement go, edited, into the file `review.examples` names.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import record

INTRO = (
    "Drafted from this project's own records for review.examples. Pick the ones that teach a\n"
    "judgement, set the severity and the exact fix, then paste them into the examples file.\n"
    "Nothing is written to that file and no case is invented."
)
L_HEAD = "## Fixed blockers and majors (one per lesson; set blocker or major from the PR)"
M_HEAD = "## Deferred minors (one per open follow-up)"
N_HEAD = "## Closed as not-a-problem (wontFix or invalid)"
NONE_RECORDED = (
    "- (none recorded: the loop does not record findings closed as not-a-problem; "
    "add a borderline case by hand)"
)


def obj(file: str, severity: str, rule: str, message: str) -> str:
    """One finding object in the verdict JSON shape (line 1: a draft the person anchors)."""
    fields = {"file": file, "line": 1, "severity": severity, "rule": rule, "message": message}
    return json.dumps(fields, ensure_ascii=False)


def slug(label: str) -> str:
    """A kebab rule slug from a lesson's class label (a leading row number dropped)."""
    text = re.sub(r"^\d+\s+", "", label.strip())
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "general"


def records_of(root: Path, kind: str, limit: int | None) -> list[tuple[str, dict[str, object]]]:
    """Each open record of `kind` with its change name; `limit`: the most recent N kept."""
    found = [
        (folder.name, r)
        for folder in record.folders(root)
        for r in record.read(folder)
        if r["kind"] == kind and not r.get("closed")
    ]
    return found[-limit:] if limit else found


def lessons(root: Path, limit: int | None) -> list[str]:
    lines: list[str] = []
    for change, r in records_of(root, "lesson", limit):
        files = str(r.get("files") or "").replace("`", "").split()
        summary = str(r.get("summary", "")).strip()
        avoid = str(r.get("avoid", "")).strip().rstrip(".")
        message = f"{summary}. {avoid}." if avoid else f"{summary}."
        rule = slug(str(r.get("class", "")))
        finding = obj(files[0] if files else "FILE", "major", rule, message)
        lines.append(f"- change `{change}` {r.get('id', '?')}: {finding}")
        lines.append(f"  Why: {summary}")
    return lines


def minors(root: Path, limit: int | None) -> list[str]:
    lines: list[str] = []
    for change, r in records_of(root, "follow-up", limit):
        file = str(r.get("file", "")).replace("`", "").strip() or "FILE"
        finding = obj(file, "minor", "deferred-minor", f"{str(r.get('finding', '')).strip()}.")
        lines.append(f"- change `{change}` PR {r.get('pr', '?')}: {finding}")
        lines.append(f"  Why: {r.get('why', '')}")
    return lines


def draft(root: Path, limit: int | None = None) -> str:
    """Return the Markdown draft: fixed blockers/majors, deferred minors, the not-filed note."""
    parts = ["# Candidate review examples", "", INTRO, "", L_HEAD]
    parts += lessons(root, limit) or ["- (none in the sidecars)"]
    parts += ["", M_HEAD] + (minors(root, limit) or ["- (none in the sidecars)"])
    parts += ["", N_HEAD, NONE_RECORDED]
    return "\n".join(parts)
