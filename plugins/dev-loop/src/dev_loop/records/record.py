"""The one writer of a change's review records, in one grammar.

    dl record <change> <kind> key=value ...  # write one record; a bad one is refused
    dl record done [<change>...] [part=<a>]  # each finished change's missing lines
    dl record done --touched-paths -         # the done guard's: git paths on stdin (done_scope)
    dl record <change> done [part=<a>]       # the same, the change first as other kinds
    dl record legacy|render <change> reason=<why> ...  # record_legacy.py

Each record goes to `<paths.changes>/<change>/records.jsonl` (what readers parse) and, rendered,
to the kind's Markdown file. Migrated records carry `"legacy": true`; unstated fields are null.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from contextlib import suppress
from datetime import datetime, timezone
from itertools import takewhile
from pathlib import Path
from typing import Any

from .. import tasklist
from ..config import load, project_root
from . import round_guard
from .record_grammar import (  # noqa: F401  # re-exported: callers and tests read them here
    COUNTS,
    DEBT_HEADER,
    FOLLOW_UPS_HEADER,
    GRAMMAR,
    LEGACY_MARK,
    LESSON,
    LISTED,
    LOCAL_PR,
    MADE,
    MARK,
    MARKDOWN,
    META,
    METRICS_HEADER,
    MIGRATED,
    NEW_FILE,
    OLD_DEBT,
    PRS,
    TABLE,
    USAGE,
    USAGE_TEXT,
    faults,
    render,
)
from .record_lock import locked, replace_text
from .record_quote import quoted


def changes_dir(root: Path) -> Path:
    """Return the folder of the project's changes (`paths.changes`)."""
    return root / str(load(root).get("paths.changes"))


def frozen_dirs(root: Path) -> list[Path]:
    """Return the shared record folders `paths.frozen` names (a glob that is a folder)."""
    globs = load(root).get("paths.frozen")
    return sorted({p for pattern in globs for p in root.glob(pattern) if p.is_dir()})



def severity(record: dict[str, Any]) -> str:
    """Return a debt record's severity; one written before B5 has none and reads as unknown."""
    return str(record.get("severity") or "unknown")


def text_of(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read(folder: Path) -> list[dict[str, Any]]:
    """Each sidecar record; a line that is not one is unparsed's to name, never a crash."""
    found = []
    for line in text_of(folder / "records.jsonl").splitlines():
        with suppress(json.JSONDecodeError):
            found.append(json.loads(line))
    return [whole(r) for r in found if isinstance(r, dict) and r.get("kind") in GRAMMAR]


def whole(record: dict[str, Any]) -> dict[str, Any]:
    """Return a legacy record with each field it lacks as null, as migrated ones are."""
    fields = set(re.findall(r"\{(\w+)\}", GRAMMAR[record["kind"]][1])) - USAGE - {"title"}
    lesson = dict(zip(["id"], re.findall(r"^## \[(L\d+)\]", str(record.get("line")))))
    return dict.fromkeys(sorted(fields)) | lesson | record if record.get("legacy") else record


def listed(changes: Path) -> set[str]:
    """Return the folders in the changes folder's LISTED file; `#` starts a comment."""
    return {x.partition("#")[0].strip() for x in text_of(changes / LISTED).splitlines()} - {""}


def change_folders(root: Path) -> list[Path]:
    """Return each change's folder; the archive folder holds finished changes, not one."""
    archive = (root / str(load(root).get("paths.archive"))).resolve()  # `./a`, `a/`, absolute
    return sorted(p for p in changes_dir(root).glob("*") if p.is_dir() and p.resolve() != archive)


def folders(root: Path) -> list[Path]:
    """Return the frozen shared records' folders first, then each change's folder."""
    return [*frozen_dirs(root), *change_folders(root)]


def parse(kind: str, pairs: list[str]) -> dict[str, Any]:
    if kind == "approval":  # written only by the UserPromptSubmit hook
        raise ValueError("approval is written by the hook on a user message, never by dl record")
    record: dict[str, Any] = {"kind": kind}
    for key, sep, value in (pair.partition("=") for pair in pairs):
        if not sep or key in META | MADE:
            raise ValueError(f"{key!r}: give key=value, a field of the kind")
        number = value.isdigit() and not (key in PRS and value[0] == "0" and value != "0")
        record[key] = int(value) if key in COUNTS and number else value  # 0123.. stays text
    return record


def rerun(folder: Path, record: dict[str, Any]) -> dict[str, Any] | None:
    """The sidecar's last record of this kind when its fields are `record`'s, else None."""
    last = next((r for r in reversed(read(folder)) if r["kind"] == record["kind"]), None)
    keep = META - {"kind"} | MADE - {"date"}  # an incident's date is part of what repeats
    same = last and {k: v for k, v in last.items() if k not in keep} == record
    return last if same else None


def write(root: Path, change: str, record: dict[str, Any]) -> dict[str, Any]:
    """Append `record` to the change's sidecar and its Markdown file; refuse it if it is bad."""
    folder = changes_dir(root) / change
    if not change.strip(".") or Path(change).name != change or not folder.is_dir():  # 9110
        raise ValueError(f"no change {change!r}")
    if bad := faults(record, writing=True):
        raise ValueError("; ".join(bad))
    quoted(record)
    with locked(folder):  # the repeat check and both appends are one unit
        return append(folder, change, record)


def append(folder: Path, change: str, record: dict[str, Any]) -> dict[str, Any]:
    # The same step run again is written once (error-handling checklist); two budget stops with
    # the same remains are two real stops, and a simplifier pass after a later round is a new pass.
    if record["kind"] == "incident":
        record["date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")  # noqa: UP017
    if record["kind"] not in {"budget", "simplifier"} and (same := rerun(folder, record)):
        return same
    if record["kind"] == "lesson":
        ids = [int(r["id"][1:]) for r in read(folder) if r["kind"] == "lesson"]
        when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")  # noqa: UP017 - 3.10 hosts
        record |= {"id": f"L{max(ids, default=0) + 1}", "when": when}
    body = render(record).rstrip("\n")
    record["line"] = body.strip().splitlines()[0]  # the record's own line, never a table head
    on_file = text_of(folder / GRAMMAR[record["kind"]][0]).splitlines().count(record["line"])
    if on_file <= sum(r.get("line") == record["line"] for r in read(folder)):  # crash left it
        put_text(folder, change, record, body)
    with (folder / "records.jsonl").open("a", encoding="utf-8", newline="\n") as sidecar:
        sidecar.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def put_text(folder: Path, change: str, record: dict[str, Any], body: str) -> None:
    """Append `body` to the record's Markdown file, under its table head when it is a row."""
    path = folder / GRAMMAR[record["kind"]][0]
    old = text_of(path) or NEW_FILE.get(path.name, "").format(change=change)
    text = ("\n" if old and not old.endswith("\n") else "") + lead(record, old) + body
    replace_text(path, old + text + "\n")


def lead(record: dict[str, Any], old: str) -> str:
    """Return a blank line, or a row's table head unless the file ends in that table."""
    if (kind := record["kind"]) not in TABLE:
        return "" if kind == "lesson" else "\n"
    lines = reversed(old.removesuffix("\n").split("\n"))
    old_debt = kind == "debt" and "severity" not in record
    head = FOLLOW_UPS_HEADER if old_debt else TABLE[kind]
    rows = list(takewhile(lambda x: x.startswith("|"), lines))
    return "" if rows and rows[-1] == head else f"\n{head}\n{re.sub(r'[^|]+', '---', head)}\n"


def part_blocks(folder: Path, part: str | None = None) -> list[dict[str, Any]]:
    """The folder's block tasks; with a part, only the tasks under that  heading."""
    blocks = tasklist.parse_blocks(text_of(folder / "tasks.md"))
    return [b for b in blocks if b.get("part") == part] if part else blocks


def tasks_done(folder: Path, part: str | None = None) -> bool:
    """Every block task done (of one part, if named), else every box ticked (drift 1)."""
    text = text_of(folder / "tasks.md")
    if blocks := part_blocks(folder, part):
        return tasklist.done(blocks)
    if part:
        return False
    boxes = re.findall(r"^\s*[-*] \[(.)\]", text, re.MULTILINE)
    return bool(boxes) and all(box in "xX" for box in boxes)


def missing(folder: Path, part: str | None = None, root: Path | None = None) -> list[str]:
    """Return the done rule's lines the sidecar lacks; `root`: stale()'s.

    Only records on the grammar with their line on file count. A migrated change is history until
    it writes a new record, then it is judged on its new records; with no new round, its loop is
    clean only if its last legacy round states 0 blocker and 0 major. A PR's clean last round files
    0 blocker, 0 major, 0 active, and fixes at most what the round before left active.
    """
    old = folder.name in text_of(folder.parent / MIGRATED).split()
    records = [r for _, r, bad in trusted(folder, MARKDOWN, old) if not bad]
    new = [r for r in records if not r.get("legacy") and r["kind"] != "skill"]  # not its work
    if old and not new:
        return []
    kinds = {kind: [r for r in new if r["kind"] == kind] for kind in GRAMMAR}
    rounds, logged, verify = kinds["round"], kinds["lessons-logged"], kinds["verify"]
    if rounds:
        fast = round_guard.fast_track(root or project_root())
        debt = kinds["debt"] if fast else None  # fast track: no recheck round, the rest is debt
        gaps = round_guard.review_gaps(rounds, part_blocks(folder, part), debt)
    else:  # a migrated loop: clean only if its last legacy round states 0 blocker, 0 major
        past = [r for r in records if r["kind"] == "round"][-1:]
        clean = old and bool(past) and all(r[k] == 0 for r in past for k in ("blocker", "major"))
        gaps = [] if clean else ["a clean last review round"]
    fixed, count = sum(r["fixed"] for r in rounds), logged[-1]["count"] if logged else -1
    short = count < fixed or len({r.get("id") for r in records} - {None}) < count  # each id once
    gaps += [f"'Lessons logged: n' (n >= {fixed} fixed, each a lesson)"] if short else []
    green = verify and verify[-1]["result"] == "green" and (folder / "verify-report.md").exists()
    green = green and not stale(verify[-1]["head"], root)
    gaps += [] if green else ["a green verify record and verify-report.md"]
    gaps += [f"a {kind} line" for kind in ("simplifier", "skills") if not kinds[kind]]
    unmetered = {r["pr"] for r in rounds} - {r["pr"] for r in kinds["metrics"]}
    return gaps + [f"a metrics row for PR {pr}" for pr in sorted(unmetered, key=str)]


def stale(head: str, root: Path | None = None) -> bool:
    """True when code changed since `head`, or git cannot say; the changes folder is none.

    `root` is the checkout to ask; unstated, the project the command runs in.
    """
    root = root or project_root()
    skip = f":(exclude){changes_dir(root).relative_to(root).as_posix()}"
    argv = ["git", "diff", "--quiet", head, "HEAD", "--", ".", skip]
    return subprocess.run(argv, cwd=root, capture_output=True, check=False).returncode != 0


def record_lines(path: Path) -> list[str]:
    """Return the lines a reader would take for a record: lines, lessons, PR rows."""
    found, header, every_row = [], "", path.name.lower() in ("follow-ups.md", "debt.md")
    for line in (line.rstrip() for line in text_of(path).splitlines()):
        if not line.startswith("|"):
            header = ""
            found += [line] if MARK.search(line) else []
        elif not header:
            header = line
        elif not re.fullmatch(r"\|[\s:|-]*", line):
            found += [line] if every_row or re.match(r"\| PR \|.*Round", header) else []
    return found


def trusted(folder: Path, names: tuple[str, ...], old: bool) -> list[tuple[int, Any, list[str]]]:
    """Return each sidecar line, its record and its faults: off the grammar, or not on file."""
    md = {x for name in names for x in record_lines(folder / name)}  # record lines only
    found, parsed = [], []
    for number, text in enumerate(text_of(folder / "records.jsonl").splitlines(), 1):
        try:
            record: Any = json.loads(text)
        except json.JSONDecodeError as error:
            record = f"not JSON: {error}"
        parsed.append((number, record))
    marks = {n: r for n, r in parsed if isinstance(r, dict) and r.get("kind") == LEGACY_MARK}
    bad_marks = {n: mark_faults(r, n, folder) for n, r in marks.items()}
    marked = {row for n, r in marks.items() if not bad_marks[n] for row in r["rows"]}
    found += [(n, marks[n], bad) for n, bad in bad_marks.items() if bad]
    for number, record in ((n, r) for n, r in parsed if n not in marks):
        bad = faults(record, migrated=old or number in marked)
        bad = bad or ([] if record["line"] in md else ["no Markdown line"])
        found.append((number, record if bad else whole(record), bad))
    return sorted(found, key=lambda x: x[0])


def mark_faults(mark: dict[str, Any], number: int, folder: Path) -> list[str]:
    """Return a legacy-mark's faults: in a listed folder, it names earlier rows, when, who, why."""
    rows, signed = mark.get("rows"), ("at", "by", "reason")
    good = isinstance(rows, list) and rows and all(type(x) is int and 0 < x < number for x in rows)
    good = good and set(mark) == {"kind", "rows", *signed}
    good = good and all(isinstance(mark[k], str) and mark[k].strip() for k in signed)
    unlisted = folder.name not in listed(folder.parent)
    where = [f"a legacy-mark in a folder not in {LISTED}"] if unlisted else []
    return where + ([] if good else [f"bad legacy-mark: {str(mark)[:60]}"])


def repeated(checked: list[tuple[int, Any, list[str]]]) -> list[tuple[int, str]]:
    """Each lesson id the writer gave before in the folder; migrated history is as read."""
    ids = [(n, r["id"]) for n, r, bad in checked
           if not bad and r["kind"] == "lesson" and not r.get("legacy")]
    return [(n, id_) for k, (n, id_) in enumerate(ids) if id_ in {x for _, x in ids[:k]}]


def unparsed(root: Path, chosen: list[Path] | None = None) -> list[str]:
    """Each Markdown record line with no sidecar record, and each sidecar line off the grammar."""
    out, old = [], text_of(changes_dir(root) / MIGRATED).split()
    for folder in folders(root) if chosen is None else chosen:
        checked = trusted(folder, MARKDOWN, folder.name in old)
        where = (folder / "records.jsonl").relative_to(root).as_posix()
        out += [f"{where}:{number}: {'; '.join(bad)}" for number, _, bad in checked if bad]
        known = {record["line"] for _, record, bad in checked if not bad}
        out += [f"{where}:{number}: lesson id {id_} repeated" for number, id_ in repeated(checked)]
        for path in (folder / name for name in MARKDOWN if (folder / name).exists()):
            where = path.relative_to(root).as_posix()
            out += [f"{where}: no record: {x[:90]}" for x in record_lines(path) if x not in known]
    return out


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # records carry → and ≤
    if argv[1:2] == ["done"] and argv[0] != "done":  # `dl record <change> done`, as other kinds
        argv = ["done", argv[0], *argv[2:]]
    if argv and argv[0] == "done":
        from . import done_scope  # noqa: PLC0415 - it imports this module
        return done_scope.report_done(argv[1:])
    if argv and argv[0] in ("legacy", "render"):
        from . import record_legacy  # noqa: PLC0415 - it imports this module
        return record_legacy.main(project_root(), argv)
    if len(argv) < 2:  # a misused CLI prints its usage
        print("usage: dl record <change> <kind> key=value ... | dl record done [<change>...] "
              "[part=<a>] | dl record <change> done [part=<a>]", file=sys.stderr)
        return 2
    try:
        asked = round_guard.checked(argv[0], parse(argv[1], argv[2:]))
        written = write(project_root(), argv[0], asked)
        print(written["line"])
        if written is not asked:  # the writer returned the record already on file
            print("record: already on file, not written again", file=sys.stderr)
    except (ValueError, TimeoutError) as error:  # a held lock is one line
        print(f"dl record: refused: {error}", file=sys.stderr)
        return 1
    return 0
