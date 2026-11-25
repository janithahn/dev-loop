"""Grandfather the old-format records of a change named in `<paths.changes>/legacy-changes.txt`.

    dl record legacy <change> reason=<why> <row>...  # mark rows legacy in place, sign the mark
    dl record render <change> reason=<why>           # write each missing line and handover record

`legacy` never deletes, renumbers or reorders a row: it adds `"legacy": true` to the named rows
and appends a legacy-mark (the rows, when, who and why). `render` writes each record's missing
Markdown line and a legacy handover record for an unrecorded handover line.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import record
from .record_lock import locked, replace_text

HANDOVER = re.compile(r"^\W*handover:")
USAGE = ("usage: dl record legacy <change> reason=<why> <row>... "
         "| dl record render <change> reason=<why>")


def folder_of(root: Path, change: str) -> Path:
    folder = record.changes_dir(root) / change
    if not change.strip(".") or Path(change).name != change or not folder.is_dir():
        raise ValueError(f"no change {change!r}")
    if change not in record.listed(record.changes_dir(root)):
        raise ValueError(f"{change} is not in {record.LISTED}")
    return folder


def who(root: Path) -> str:
    git = ["git", "config", "--default", "", "user.name"]  # "" when unset
    try:
        name = subprocess.check_output(git, cwd=root, text=True).strip()  # noqa: S603 - fixed args
    except (OSError, subprocess.CalledProcessError):
        name = ""
    if not name:
        raise ValueError("no git user.name to sign the legacy-mark")
    return name


def stamped(rows: list[int], signed: dict[str, str]) -> str:
    at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")  # noqa: UP017 - 3.10 hosts
    return json.dumps({"kind": record.LEGACY_MARK, "rows": rows, "at": at} | signed)


def unmarked(lines: list[str], row: int) -> dict[str, Any]:
    """Return the record on `row` (1-based); refuse a row that is none or already legacy."""
    found: Any = None
    with suppress(json.JSONDecodeError):
        found = json.loads(lines[row - 1]) if 0 < row <= len(lines) else None
    if not isinstance(found, dict) or found.get("kind") not in record.GRAMMAR:
        raise ValueError(f"row {row}: not a record")
    if found.get("legacy"):
        raise ValueError(f"row {row}: already legacy")
    return found


def legacy(root: Path, change: str, rows: list[int], signed: dict[str, str]) -> list[str]:
    """Mark `rows` legacy in place, append their legacy-mark, write their missing lines."""
    folder = folder_of(root, change)
    path = folder / "records.jsonl"
    with locked(folder):
        lines, marked = record.text_of(path).splitlines(), []
        for row in rows:
            found = unmarked(lines, row) | {"legacy": True}
            lines[row - 1] = json.dumps(found, ensure_ascii=False)
            marked.append(found)
        replace_text(path, "\n".join([*lines, stamped(rows, signed)]) + "\n")
        return fill(folder, change, marked)


def render_missing(root: Path, change: str, signed: dict[str, str]) -> list[str]:
    """Add a legacy record for each handover line with none, then write each missing line."""
    folder = folder_of(root, change)
    path = folder / "records.jsonl"
    with locked(folder):
        known = {r.get("line") for r in record.read(folder)}
        status = record.record_lines(folder / "status.md")
        new = [x for x in dict.fromkeys(status) if HANDOVER.match(x) and x not in known]
        if new:
            lines = record.text_of(path).splitlines()
            added = [{"kind": "handover", "line": x, "legacy": True} for x in new]
            rows = list(range(len(lines) + 1, len(lines) + len(added) + 1))
            added_text = [json.dumps(r, ensure_ascii=False) for r in added]
            replace_text(path, "\n".join([*lines, *added_text, stamped(rows, signed)]) + "\n")
        return fill(folder, change, record.read(folder))


def fill(folder: Path, change: str, records: list[dict[str, Any]]) -> list[str]:
    """Write the Markdown text of each record whose line is on no Markdown file; return those."""
    texts = (record.text_of(folder / name) for name in record.MARKDOWN)
    on_file = {x.rstrip() for body in texts for x in body.splitlines()}
    written = []
    for found in records:
        line = found.get("line")
        if not isinstance(line, str) or not line or line in on_file:
            continue
        record.put_text(folder, change, found, text(found))
        on_file.add(line)
        written.append(line)
    return written


def text(found: dict[str, Any]) -> str:
    """Return the record's rendered text when its fields render its line, else the line."""
    with suppress(KeyError, ValueError, IndexError):
        body = record.render(found).rstrip("\n")
        if body.strip().splitlines()[0] == found["line"]:
            return body
    return str(found["line"])


def main(root: Path, argv: list[str]) -> int:
    command, *rest = argv
    why = [x.removeprefix("reason=").strip() for x in rest[1:] if x.startswith("reason=")]
    numbers = [x for x in rest[1:] if not x.startswith("reason=")]
    usable = len(why) == 1 and why[0] and (not numbers if command == "render" else numbers)
    if not usable or not all(x.isdigit() for x in numbers):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        signed = {"by": who(root), "reason": why[0]}
        if command == "render":
            written = render_missing(root, rest[0], signed)
        else:
            written = legacy(root, rest[0], [int(x) for x in numbers], signed)
    except ValueError as error:
        print(f"dl record: refused: {error}", file=sys.stderr)
        return 1
    print("\n".join(f"wrote: {x[:90]}" for x in written) or "no Markdown line was missing")
    return 0
