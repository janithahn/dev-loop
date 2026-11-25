"""The record grammar: each kind, its text, its headers, and what breaks it (split from record.py)."""

from __future__ import annotations

import re
from typing import Any

from ..schema import SEVERITIES

MIGRATED, LISTED = "migrated-changes.txt", "legacy-changes.txt"  # legacy records; legacy-marks
METRICS_HEADER = "| PR | Change | Rounds | Per round (b/M/m) | Total (b/M/m) | Lessons |"
FOLLOW_UPS_HEADER = "| PR | Thread | File | Finding | Why deferred |"
# B5: a debt row names its severity; one from before B5 has none (unknown).
DEBT_HEADER = "| PR | Thread | File | Severity | Finding | Why deferred |"
OLD_DEBT = "| {pr} | {thread} | {file} | {finding} | {why} |"
LESSON = (  # the lesson block learnings.md has always held
    "\n## [{id}] pattern — {title}\n\n**When:** {when} UTC\n**Category:** pattern\n"
    "**Priority:** {priority}\n**Status:** pending\n\n### Detail\nSummary: {summary}\n"
    "Class: {class}\nGuard: {guard}\nFiles: {files}\nRound: {round}\n\n### Action\n"
    "Avoid: {avoid}\n\n---\n")
# A round's review usage (pr-review.sh's Usage line); `unmetered` counts the calls left out.
USAGE_TEXT = (" · {turns} turns · {seconds} s · {tokens_in} in / {tokens_out} out"
              " · {unmetered} unmetered")
USAGE = {"turns", "seconds", "tokens_in", "tokens_out", "unmetered"}
GRAMMAR = {  # kind: (Markdown file, rendered text); a kind's fields are its text's placeholders
    "round": ("status.md", (
        "Review round {round} (PR {pr}, base {base}): {blocker} blocker, {major} major, "
        "{minor} minor filed → {fixed} fixed, {deferred} deferred, {active} active"
    ) + USAGE_TEXT),
    "metrics": ("status.md", "| {pr} | {change} | {rounds} | {per_round} | {total} | {lessons} |"),
    "follow-up": ("follow-ups.md", "| {pr} | {thread} | {file} | {finding} | {why} |"),
    "debt": ("debt.md", "| {pr} | {thread} | {file} | {severity} | {finding} | {why} |"),  # V4a
    "pause": ("status.md", "Paused: PR {pr} thread {thread}: {cites}; {why}"),  # F1
    "lesson": ("learnings.md", LESSON),
    "lessons-logged": ("status.md", "Lessons logged: {count}"),
    "verify": ("status.md", "Verify: {result} on {head}"),
    "simplifier": ("status.md", "Simplifier: {edits}"),
    "skills": ("status.md", "Skills used: {skills}"),
    "archive": ("status.md", "Archived: {from} → {to}"),  # `dl archive`, before its move
    "budget": ("status.md", "budget_exhausted: {role}; remains: {remains}"),  # a capped agent
    "models": ("status.md", "Models: PR {pr} built on {builder}, reviewed on {reviewer}"),
    "skill": ("status.md", "Skill approved: {skill} for {place} (PR {pr}) by {by}"),
    "handover": (  # a quality cap reached
        "status.md",
        "handover: {role} {agent} at {context} tokens, commit {commit}; done: {done}; next: {next}",
    ),
    "context": ("status.md",  # one per PR, for the calibration
                "Context: PR {pr} peak {peak} tokens · {handovers} hand-offs · {stops} cost stops"),
    "approval": ("status.md", "Approval: {by} at {at}"),  # the hook only
    "recall": ("status.md", "Recall: {change} was shown {shown}"),  # change:L3 ids shown
    "rework": ("status.md", "Rework: PR {pr} fixes PR {cause_pr}: {note}"),  # a fix PR, 7 days
    # B4: the loop's own failure (a lost build, a wrong target), counted like a lesson
    "incident": ("incidents.md", (
        "Incident {date}: {what}; impact: {impact}; root cause: {root_cause}; guard: {guard}")),
}
COUNTS = ("pr round blocker major minor fixed deferred active rounds lessons count thread "
          "context peak handovers stops cause_pr").split() + sorted(USAGE)
# A PR id is a number (ADO, GitHub) or, on the local host, 12 lowercase hex characters
LOCAL_PR = re.compile(r"[0-9a-f]{12}")
PRS = ("pr", "cause_pr")
# made by the writer: a lesson's id, when and title, an incident's date; closed: struck, legacy
MADE, META = {"id", "when", "title", "date"}, {"kind", "line", "legacy", "closed"}
NEW_FILE = {"learnings.md": "# Learnings: {change}\n", "follow-ups.md": "# {change} follow-ups\n",
            "debt.md": "# {change} debt\n", "incidents.md": "# {change} incidents\n"}
# A row kind: its table's head. Debt (V4a) is a follow-up a fast-track review chose not to fix.
TABLE = {"metrics": METRICS_HEADER, "follow-up": FOLLOW_UPS_HEADER, "debt": DEBT_HEADER}
# A line a reader would take for a record: unparsed checks each has its sidecar line.
MARK = re.compile(r"^\W*(Review round \d|Skills used|Skill approved|Simplifier:|Verify:"
                  r"|budget_exhausted:|Models: PR [0-9a-f]|handover:|Context: PR [0-9a-f]"
                  r"|Recall: \S+ was shown|Rework: PR [0-9a-f]|Archived:|Approval:"
                  r"|Incident \d|Paused: PR [0-9a-f])|Lessons logged:|^## \[L")
MARKDOWN = tuple("status.md metrics.md follow-ups.md learnings.md debt.md incidents.md".split())
LEGACY_MARK = "legacy-mark"  # the audit row of dl record legacy|render: the rows it grandfathered


def faults(record: object, *, migrated: bool = False, writing: bool = False) -> list[str]:
    """Return what breaks the grammar: kind, fields, counts, text, a line its fields do not render.

    A legacy record (nulls allowed) is valid only in a migrated folder. Any other
    record needs the line its fields render, except while the writer is making it.
    """
    if not isinstance(record, dict) or (kind := record.get("kind")) not in GRAMMAR:
        return [f"unknown kind in {str(record)[:60]}"]
    # A lesson's id and when are the writer's to fill; its title is drawn from the summary.
    need = set(re.findall(r"\{(\w+)\}", GRAMMAR[kind][1])) - (MADE if writing else {"title"})
    if not writing and not USAGE & set(record):  # a legacy round; a new one needs usage
        need -= USAGE
    need -= {"severity"} if kind == "debt" and "severity" not in record else set()  # B5: old debt
    given = set(record) - META
    found = [f"missing {k}" for k in sorted(need - given)]
    found += [f"unknown field {k}" for k in sorted(given - need)]
    found = [] if record.get("legacy") else found  # a legacy row: its fields as first written
    banned = set("|\n\r" if kind in TABLE else "\n\r")
    for name, value in sorted((k, record[k]) for k in need & given):
        good = type(value) is int and value >= 0 if name in COUNTS else (
            isinstance(value, str) and bool(value.strip()) and not banned & set(value))
        good = good or name in PRS and LOCAL_PR.fullmatch(str(value)) is not None
        good = good or kind == "follow-up" and name in ("pr", "thread") and value == "none"
        if not good and not (value is None and record.get("legacy")):
            found.append(f"bad {name}: {value!r}")
    if kind == "debt" and record.get("severity", "unknown") not in SEVERITIES:
        found.append(f"bad severity: {record['severity']!r}")
    if record.get("legacy"):  # its line is what the migration read; it must be a line
        found += [] if isinstance(record.get("line"), str) and record["line"] else ["bad line"]
        return found + ([] if migrated else ["a legacy record outside the migration"])
    ok = writing or found or render(record).lstrip().split("\n")[0] == record.get("line")
    return found + ([] if ok else ["its fields do not render its line"])


def render(record: dict[str, Any]) -> str:
    """Return the record's Markdown text; a legacy null shows as —."""
    title = f"Summary: {record.get('summary')}"
    title = title if len(title) <= 60 else title[:57] + "..."  # the title cut
    values = {k: "—" if v is None else v for k, v in record.items()} | {"title": title}
    text = GRAMMAR[record["kind"]][1]
    text = OLD_DEBT if record["kind"] == "debt" and "severity" not in record else text
    values = dict.fromkeys(re.findall(r"\{(\w+)\}", text), "—") | values  # fieldless legacy
    return (text if USAGE & set(record) else text.removesuffix(USAGE_TEXT)).format_map(values)
