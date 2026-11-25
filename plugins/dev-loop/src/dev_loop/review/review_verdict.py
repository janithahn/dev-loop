"""The reviewer's verdict contract, checked by the harness.

pr-review.sh calls it; an off-schema reply makes the round incomplete, never "no findings":
    review_verdict.py lines <spec.md>...   # S<n><TAB><text> per done-when line; exit 1 when a spec
                                           # has none (named on stderr)
    review_verdict.py check <n>            # reply on stdin; errors to stderr, exit 1 on any
    review_verdict.py ticks <lines file>   # pass A's checked reply on stdin; the summary's block
Standard library only: it runs on the build agent and in the dev container.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

VERDICTS = ("APPROVE", "CHANGES_REQUESTED")
SEVERITIES = ("blocker", "major", "minor")
TICKS = {"met": "✅", "failed": "❌", "not-applicable": "➖"}
HEADING = re.compile(r"^(#{1,6}) ")
# `## Acceptance…`, `## Done when` or `## Definition of done`, at the `##` or `###` level;
# a heading no shallower than it ends the section, a deeper one stays inside.
DONE_WHEN = re.compile(r"^#{2,3} (acceptance|done when|definition of done)", re.IGNORECASE)
BULLET = re.compile(r"^(?:[-*]|\d+\.)\s+(.*)")


def spec_lines(text: str) -> list[str]:
    """Return the top-level bullets under a done-when heading, continuations joined."""
    lines: list[str] = []
    depth = 0  # the open section's heading level; 0 = outside one
    for raw in text.splitlines():
        if heading := HEADING.match(raw):
            level = len(heading[1])
            if DONE_WHEN.match(raw):
                depth = level  # open (or reopen) the section at this heading's level
            elif level <= depth:
                depth = 0  # a heading no shallower than the open one ends it
        elif depth and (bullet := BULLET.match(raw)):
            lines.append(bullet[1])
        elif depth and lines and raw[:1] in (" ", "\t") and raw.strip():
            lines[-1] += " " + raw.strip()
    return [" ".join(line.split()) for line in lines]


def is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def finding_errors(at: str, finding: object) -> list[str]:
    if not isinstance(finding, dict):
        return [f"{at}: not an object"]
    found = [f"{at}.{key}: not a non-empty string" for key in ("file", "rule", "message")
             if not is_text(finding.get(key))]
    if not (is_int(finding.get("line")) and finding["line"] >= 1):
        found.append(f"{at}.line: not an integer >= 1")
    if finding.get("severity") not in SEVERITIES:
        found.append(f"{at}.severity: not one of {', '.join(SEVERITIES)}")
    return found


def tick_errors(spec: object, count: int) -> list[str]:
    if not isinstance(spec, list):
        return [f"spec: not a list of {count} ticks"]
    found, ids = [], []
    for n, tick in enumerate(spec):
        if not isinstance(tick, dict):
            found.append(f"spec[{n}]: not an object")
            continue
        ids.append(tick.get("id"))
        if tick.get("status") not in TICKS:
            found.append(f"spec[{n}].status: not one of {', '.join(TICKS)}")
        if not is_text(tick.get("evidence")):
            found.append(f"spec[{n}].evidence: not a non-empty string")
    if sorted(map(str, ids)) != sorted(f"S{k}" for k in range(1, count + 1)):
        found.append(f"spec: ids {ids} are not S1..S{count}, each once")
    return found


def filed_ids(findings: list[object]) -> set[str]:
    """The S-ids a spec-line-failed blocker/major files: its `spec_id`, else the message's lead.

    Matched by the leading id or the `spec_id` field, never by the id appearing anywhere in the
    text, so naming one line inside another line's failure does not file it.
    """
    ids: set[str] = set()
    for finding in findings:
        if not (isinstance(finding, dict) and finding.get("rule") == "spec-line-failed"
                and finding.get("severity") in SEVERITIES[:2]):
            continue
        if is_text(finding.get("spec_id")):
            ids.add(str(finding["spec_id"]).strip())
        elif lead := re.match(r"\s*(S\d+)\b", str(finding.get("message"))):
            ids.add(lead[1])
    return ids


def unfiled_failures(spec: object, findings: object) -> list[str]:
    """A tick `failed` needs a blocker or major, rule spec-line-failed, naming its id."""
    if not (isinstance(spec, list) and isinstance(findings, list)):
        return []  # tick_errors and errors() already name the broken shape
    filed = filed_ids(findings)
    failed = [t.get("id") for t in spec if isinstance(t, dict) and t.get("status") == "failed"]
    return [f"spec: {tick} ticked failed with no spec-line-failed major naming it"
            for tick in failed if str(tick) not in filed]


def approve_without_failures(verdict: object, spec: object) -> list[str]:
    """Refuse an APPROVE verdict while a done-when line is ticked failed.

    A round never reads clean while a line failed (Law 1: not executed is never clear).
    """
    if verdict != "APPROVE" or not isinstance(spec, list):
        return []
    failed = [t.get("id") for t in spec if isinstance(t, dict) and t.get("status") == "failed"]
    return [f"verdict: APPROVE while {tick} ticked failed" for tick in failed]


def errors(reply: object, spec_count: int) -> list[str]:
    """Return every way the reply breaks the output contract (standards.base.txt)."""
    if not isinstance(reply, dict):
        return ["the reply is not a JSON object"]
    found = []
    if reply.get("verdict") not in VERDICTS:
        found.append(f"verdict: not one of {', '.join(VERDICTS)}")
    if not isinstance(reply.get("summary"), str):
        found.append("summary: not a string")
    if "files_seen" in reply and not is_int(reply["files_seen"]):
        found.append("files_seen: not an integer")
    findings = reply.get("findings")
    if isinstance(findings, list):
        for n, finding in enumerate(findings):
            found += finding_errors(f"findings[{n}]", finding)
    else:
        found.append("findings: not a list")
    if spec_count:
        found += tick_errors(reply.get("spec"), spec_count)
        found += unfiled_failures(reply.get("spec"), findings)
        found += approve_without_failures(reply.get("verdict"), reply.get("spec"))
    return found


def ticks(reply: dict[str, list[dict[str, str]]], lines: list[str]) -> str:
    """Render a checked reply's ticks: one line per S-id, in order, with its evidence."""
    by_id = {tick["id"]: tick for tick in reply["spec"]}
    met = sum(tick["status"] == "met" for tick in by_id.values())
    rows = [f"Spec: {met}/{len(lines)} met"]
    for k, line in enumerate(lines, 1):
        tick = by_id[f"S{k}"]
        evidence = " ".join(tick["evidence"].split())[:200]  # one line, cut
        rows.append(f"- {TICKS[tick['status']]} S{k} {tick['status']}: {line[:120]} — {evidence}")
    return "\n".join(rows)


def main(command: str, args: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # ticks carry ✅
    if command == "lines":
        per_spec = {path: spec_lines(Path(path).read_text("utf-8")) for path in args}
        found = [line for lines in per_spec.values() for line in lines]
        sys.stdout.write("".join(f"S{k}\t{line}\n" for k, line in enumerate(found, 1)))
        # A spec with no line the parser reads is not_run, never "nothing to tick" (audit M3).
        unread = [path for path, lines in per_spec.items() if not lines]
        sys.stderr.write(f"no done-when line (a bullet under `## Acceptance...` or `## Done when`)"
                         f" in {', '.join(unread)}\n" if unread else "")
        return 1 if unread else 0
    try:
        reply = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except ValueError:
        reply = None
    if command == "check":
        found = errors(reply, int(args[0]))
        sys.stderr.write("".join(f"  schema: {error}\n" for error in found))
        return 1 if found else 0
    rows = Path(args[0]).read_text("utf-8").splitlines()
    print(ticks(reply, [row.split("\t", 1)[1] for row in rows]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
