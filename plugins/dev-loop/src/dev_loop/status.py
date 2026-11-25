"""`dl status [change]`: each change's progress, computed from tasks.md and records.jsonl.

No STATUS.md file. A verify that never ran reads "not run", never green. A change folder with
no records.jsonl was last written by another tool and gets a warning.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .config import project_root
from .records import record, round_guard
from .tasklist import done as all_done
from .tasklist import parse_blocks


def gaps(folder: Path) -> list[str]:
    """What the done rule still lacks (the lines the done guard names); empty when done."""
    return record.missing(folder)


def line(folder: Path) -> str:
    records = record.read(folder)
    of = lambda kind: [r for r in records if r["kind"] == kind]  # noqa: E731
    tasks = parse_blocks(record.text_of(folder / "tasks.md"))
    done = sum(t["status"] == "done" for t in tasks)
    verify = of("verify")
    rounds = of("round")
    last = (f"round {rounds[-1]['round']}: {rounds[-1]['blocker']}/{rounds[-1]['major']}/"
            f"{rounds[-1]['minor']}") if rounds else "no round"
    opened = sum(not r.get("closed") for r in of("follow-up"))
    debt = sum(not r.get("closed") for r in of("debt"))  # V4a: what a fast-track review left
    count = f"{done}/{len(tasks)}"
    if any("part" in t for t in tasks):
        names = dict.fromkeys(t.get("part") for t in tasks)
        count = ", ".join(
            f"{n} {sum(t['status'] == 'done' for t in tasks if t.get('part') == n)}/"
            f"{sum(t.get('part') == n for t in tasks)}" for n in names)
    text = (f"{folder.name}: tasks {count}; verify: "
            f"{verify[-1]['result'] if verify else 'not run'}; {last}; follow-ups open: {opened}"
            f"; debt open: {debt}")
    counts = [rounds[-1][k] for k in ("blocker", "major")] if rounds else []
    owing = any(str(n).isdigit() and int(n) > 0 for n in counts)  # a legacy round may say none
    if owing and not round_guard.fast_track(project_root()):  # a recheck round is owed
        number = rounds[-1]["round"]  # a legacy round may carry counts but no number
        nxt = int(number) + 1 if str(number).isdigit() else "next"
        text += f"; owed: review round {nxt} on PR {rounds[-1]['pr']}"
    if not (folder / "records.jsonl").exists():
        text += "; warning: no records.jsonl, last written by another tool"
    elif tasks and all_done(tasks):
        untouched = any(t["status"] == "todo" for t in tasks)
        started = ", ".join(dict.fromkeys(t["part"] for t in tasks if t["status"] != "todo")
                            ) if untouched and tasks[0].get("part") else ""
        label = f" (part {started})" if started else ""
        text += f"; done{label}" if not gaps(folder) else f"; missing: {', '.join(gaps(folder))}"
    return text


def main(argv: list[str]) -> int:
    folders = record.change_folders(project_root())
    folders = [f for f in folders if not argv or f.name in argv]
    for folder in folders:
        print(line(folder))
    if argv and not folders:
        print(f"dl status: no change {argv[0]}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
