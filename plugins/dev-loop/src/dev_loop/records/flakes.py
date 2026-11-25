"""Flake records from JUnit XML (dev-loop FL1): a flake is recorded, the build stays red.

A testcase that failed and then passed — a pytest-rerunfailures rerun element, or a second
testcase entry with the same id — is a flake; a rerun never turns a failed build green.
`dl ci flakes --junit <file>...` writes each flake as a record (test, file, build, date) to
`<paths.changes>/_flakes/flakes.jsonl` and exits 1 when a test failed in its final attempt, or a
flake was seen unless --no-fail-on-flake. `dl records trend` counts flakes per week and lists the
top five flaky tests. Reads the XML and the log only; no network, no document text.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from contextlib import suppress
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree  # noqa: S405 - our own CI's junit output, not hostile input

from . import record

FOLDER = "_flakes"  # under paths.changes: the flake log, not a change
LOG = "flakes.jsonl"
FAILED = {"failure", "error"}
WEEK_HEADER = "| Week | Flakes | Distinct tests |\n|---|---|---|"
TOP_HEADER = "| Flaky test | Flakes |\n|---|---|"


def folder(root: Path) -> Path:
    return record.changes_dir(root) / FOLDER


def key(flake: dict[str, Any]) -> tuple[str, str, str, str]:
    return flake["test"], flake["file"], flake["build"], flake["date"]


def read(root: Path) -> list[dict[str, Any]]:
    """Each flake record in the log; a line that is not one is skipped, never a crash."""
    found = []
    for line in record.text_of(folder(root) / LOG).splitlines():
        with suppress(json.JSONDecodeError):
            flake = json.loads(line)
            if isinstance(flake, dict) and flake.get("kind") == "flake":
                found.append(flake)
    return found


def testid(case: ElementTree.Element) -> str:
    cls, name = case.get("classname", ""), case.get("name", "")
    return f"{cls}::{name}" if cls else name


def outcome(case: ElementTree.Element) -> bool | None:
    """True failed, False passed, None skipped (a skip is neither a pass nor a fail)."""
    tags = {child.tag for child in case}
    if FAILED & tags:
        return True
    return None if "skipped" in tags else False


def attempts(case: ElementTree.Element) -> list[bool]:
    """A rerun child is an earlier failed attempt; the testcase itself is the final one."""
    earlier = [True for child in case if child.tag.lower().startswith("rerun")]
    final = outcome(case)
    return earlier if final is None else [*earlier, final]


def suite_date(root: ElementTree.Element) -> str:
    """The first testsuite's timestamp date, else today (UTC); a bad timestamp reads as today."""
    for suite in root.iter("testsuite"):
        with suppress(ValueError):
            return date.fromisoformat((suite.get("timestamp") or "")[:10]).isoformat()
    return datetime.now(timezone.utc).date().isoformat()  # noqa: UP017 - 3.10 hosts


def scan(path: Path) -> tuple[list[dict[str, str]], bool]:
    """One report's flakes and whether any test failed in its final attempt."""
    root = ElementTree.parse(path).getroot()  # noqa: S314 - our CI's own output
    when, runs, where = suite_date(root), defaultdict(list), {}
    for case in root.iter("testcase"):
        cid = testid(case)
        where.setdefault(cid, case.get("file") or "")
        runs[cid].extend(attempts(case))
    flakes, failed = [], False
    for cid, seq in runs.items():
        if seq and seq[-1]:
            failed = True
        elif seq and any(seq):  # a failed attempt, then a pass
            flakes.append({"test": cid, "file": where[cid], "date": when})
    return flakes, failed


def detect(files: list[str]) -> tuple[list[dict[str, str]], bool]:
    flakes, failed = [], False
    for name in files:
        found, red = scan(Path(name))
        flakes += found
        failed = failed or red
    return flakes, failed


def write(root: Path, flakes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append each new flake to the log; one already there is written once (idempotent)."""
    folder(root).mkdir(parents=True, exist_ok=True)
    seen, new = {key(f) for f in read(root)}, []
    for flake in flakes:
        if key(flake) not in seen:
            seen.add(key(flake))
            new.append(flake)
    with (folder(root) / LOG).open("a", encoding="utf-8", newline="\n") as log:
        for flake in new:
            log.write(json.dumps(flake, ensure_ascii=False) + "\n")
    return new


def run(root: Path, files: list[str], build: str | None, *, fail_on_flake: bool) -> int:
    """Detect, log and report flakes; exit 1 on a final failure or a flake kept red."""
    try:
        flakes, failed = detect(files)
    except (OSError, ElementTree.ParseError) as error:
        print(f"flakes: not run, {error}", file=sys.stderr)
        return 1
    new = write(root, [{"kind": "flake", **flake, "build": build or "local"} for flake in flakes])
    print(f"{len(flakes)} flake(s), {len(new)} new; a final attempt failed: {failed}")
    return 1 if failed or (flakes and fail_on_flake) else 0


def trend(root: Path) -> list[str]:
    """Flakes per ISO week (newest first) and the top five flaky tests; none: no lines."""
    weeks: dict[str, list[str]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for flake in read(root):
        with suppress(ValueError):
            year, number, _ = date.fromisoformat(flake["date"]).isocalendar()
            weeks[f"{year}-W{number:02d}"].append(flake["test"])
            counts[flake["test"]] += 1
    if not counts:
        return []
    rows = [f"| {w} | {len(weeks[w])} | {len(set(weeks[w]))} |" for w in sorted(weeks, reverse=True)]
    top = [f"| {test} | {n} |" for test, n in counts.most_common(5)]
    return ["", WEEK_HEADER, *rows, "", TOP_HEADER, *top]
