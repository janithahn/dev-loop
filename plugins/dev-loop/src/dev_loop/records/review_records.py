"""Print the review records combined across every change, on demand.

    dl records follow-ups                   # open deferred minors
    dl records metrics                      # one row per reviewed PR
    dl records metrics --weeks 4            # the DORA five, tokens, rounds per model
    dl records lessons --min 3              # lesson classes seen in 3+ changes
    dl records current                      # the pitfalls file's faults (exit 1)
    dl records current --verbose            # also list each moved guard (records.guard_moves)
    dl records current --base origin/<branches.integration>  # rule 1: this tree's lessons
    dl records trend                        # lessons, incidents and flakes per week, newest first
    dl records pitfalls <path>...           # the lessons logged for those packages
    dl records recall <change> [path...]    # research notes, 3 similar lessons
    dl records rules [--write]              # area guides' "Do not repeat" blocks
    dl records rules --base HEAD^1          # only the guides this tree changes
    dl records evaluate                     # repeat rate per class, weak guards
    dl records repeat                       # repeat rate per week, old and new side by side
    dl records unparsed [--base REF]        # records the sidecars miss (exit 1)
    dl records distil <pr>                  # a skill drafted from a clean PR
    dl records approve <pr> --by X          # the owner's step; distil: the tally
    dl records examples [--limit N]         # draft worked review examples from the records
    dl records fast-track                   # true/false: the integration branch's review track

Each change keeps its own records in `<paths.changes>/<change>/`: `records.jsonl`, written by
`dl record` with the Markdown beside it (`follow-ups.md`, `status.md`, `learnings.md`), so no PR
appends to a shared file and two PRs never conflict on one. `metrics` and `follow-ups` read the
sidecars, the frozen shared ones first. The lesson readers parse `learnings.md`; `recall` reads
the sidecars' lessons. Reads files only; `rules --write` writes the guides, `distil <pr>` a draft
in `skills-proposed/` beside the changes folder and `approve` moves it to `.claude/skills/` with
its record (`review_distil.py`). The project is `config.project_root()`.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import flakes
from . import record
from .. import config
from . import review_distil
from . import review_evaluate
from . import review_examples
from . import review_lessons
from . import review_metrics
from . import review_pitfalls
from . import review_repeat
from . import round_guard
from . import review_rules
from . import skipped
from .review_classes import changes_dir, pitfalls_path

# A rendered metrics row: PR, change, rounds, per round, total (b/M/m); review-trend.py reads it.
METRIC_ROW = re.compile(r"^\| (\d+) \| .*? \| (\d+) \| (.*?) \| (\d+)/(\d+)/(\d+) \|")


def sidecars(kind: str, root: Path) -> list[tuple[Path, dict[str, object]]]:
    """Each open record of `kind` with its folder: the frozen shared records, then each change."""
    found = [(f, r) for f in record.folders(root) for r in record.read(f) if r["kind"] == kind]
    return [(f, r) for f, r in found if not r.get("closed")]  # closed: a struck follow-up row


def open_follow_ups(root: Path) -> list[str]:
    """Every deferred minor not struck through, prefixed by the file that holds it."""
    named = dict.fromkeys(record.frozen_dirs(root), "FOLLOW-UPS.md")  # a change's is follow-ups.md
    found = [(f / named.get(f, "follow-ups.md"), r) for f, r in sidecars("follow-up", root)]
    return [f"{p.relative_to(root).as_posix()}: {record.render(r)}" for p, r in found]


def metric_rows(root: Path) -> list[str]:
    """Every PR's metrics row, frozen and per change, ordered by PR id (a later copy wins).

    Numeric ids first, in number order, then local hex ids.
    """
    rows = {r["pr"]: record.render(r) for _, r in sidecars("metrics", root) if r.get("pr") is not None}
    order = sorted(rows, key=lambda pr: (isinstance(pr, str), str(pr).zfill(20)))
    return [rows[pr] for pr in order]


def unparsed(base: str | None, root: Path) -> int:
    """Print each record the sidecars miss or break; with `base`, the folders changed since it."""
    chosen = None
    if base:
        where = changes_dir(root).as_posix()
        frozen = [d.relative_to(root).as_posix() for d in record.frozen_dirs(root)]
        names = review_rules.git(root, "diff", "--name-only", base, "--", where, *frozen)
        names += review_rules.git(root, "ls-files", "--others", "--exclude-standard", where)
        chosen = [f for f in record.folders(root) if f.relative_to(root).as_posix() + "/" in names]
    faults = record.unparsed(root, chosen)
    print("\n".join(faults) or "every record is in its sidecar")
    return 1 if faults else 0


def metrics(weeks: int | None, root: Path) -> int:
    """Print every PR's row; with `weeks`, the weekly table, exit 1 without ADO or at a cap."""
    if not weeks:
        print("\n".join([record.METRICS_HEADER, "|---|---|---|---|---|---|", *metric_rows(root)]))
        return 0
    now = datetime.now(timezone.utc)  # noqa: UP017 - 3.10 hosts
    try:
        ado_rows = review_metrics.fetch(now, weeks)
    except ValueError as error:
        print(f"metrics: {error}", file=sys.stderr)
        return 1
    every = [r for folder in record.folders(root) for r in record.read(folder)]
    print("\n".join(review_metrics.table(ado_rows, every, now, weeks)))
    return 0


def rules(base: str | None, root: Path, *, write: bool) -> int:
    """Print the guides whose block is stale; `--write` rewrites them, else a stale one exits 1.

    With `base` (a git ref) only the guides this tree changes are judged (`review_rules.touched`).
    """
    if (status := review_rules.generate(root, write=write)) is not None:
        return status
    try:
        names = review_rules.write(root) if write else review_rules.stale(root, base)
    except ValueError as error:
        print(f"dl records rules: {error}", file=sys.stderr)
        return 1
    what = "wrote" if write else "stale (run dl records rules --write):"
    print("\n".join(f"{what} {n}" for n in names) or "every guide's block is current")
    return 1 if names and not write else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    records = (
        "follow-ups", "metrics", "lessons", "current", "trend", "pitfalls", "rules", "evaluate",
        "unparsed", "recall", "distil", "approve", "repeat", "examples", "fast-track",
    )
    parser.add_argument("record", nargs="?", choices=records)
    parser.add_argument("--skipped", action="store_true", help="list the sidecar lines skipped")
    paths = "pitfalls: the files to be changed; recall: the change, then those; distil: a PR id"
    parser.add_argument("paths", nargs="*", help=paths)
    parser.add_argument("--min", type=int, default=1, help="lessons: at least this many changes")
    parser.add_argument("--limit", type=int, help="examples: at most N candidates per source")
    parser.add_argument("--base", help="current, rules, unparsed: a git ref; judge what this adds")
    parser.add_argument("--no-rule-1", action="store_true", help="current: rules 2 and 3 only")
    parser.add_argument("--verbose", action="store_true", help="current: also list moved guards")
    parser.add_argument("--write", action="store_true", help="rules: rewrite the stale blocks")
    parser.add_argument("--weeks", type=int, choices=range(1, 27), metavar="N", help="metrics")
    parser.add_argument("--by", help="approve: the owner who approved the skill")
    args = parser.parse_args(argv)
    try:
        root = config.project_root()
    except ValueError as error:
        print(f"records: {error}", file=sys.stderr)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")  # rows carry ≤, → and the like
    if args.skipped:
        print(chr(10).join(skipped.lines(root)) or "no record line is skipped")
        return 0
    if args.record is None:
        parser.error("a record kind is required")
    if args.record in ("rules", "unparsed"):  # the gates: they answer with an exit code
        write = args.write
        if args.record == "unparsed":
            return unparsed(args.base, root)
        return rules(args.base, root, write=write)
    if args.record in ("distil", "approve"):  # a refusal says why and exits 1
        try:
            print("\n".join(review_distil.run(root, args.record, args.paths, args.by)))
        except ValueError as error:
            print(f"{args.record}: refused: {error}", file=sys.stderr)
            return 1
    elif args.record == "follow-ups":
        print("\n".join(open_follow_ups(root)))
    elif args.record == "metrics":
        return metrics(args.weeks, root)
    elif args.record == "lessons":
        for count, total, name, guard in review_lessons.lesson_counts(root):
            if count >= args.min:
                print(f"{count:3} changes {total:4} lessons  {name}  guard: {guard}")
    elif args.record == "current":
        faults = review_lessons.current(root, args.base, rule_one=not args.no_rule_1)
        if args.verbose:
            print("\n".join(review_lessons.moved(root)))
        print("\n".join(faults) or f"{pitfalls_path(root).name} is current")
        return 1 if faults else 0
    elif args.record == "pitfalls":
        print("\n".join(review_pitfalls.pitfalls(root, args.paths)))
        print("\n".join(review_pitfalls.do_not_repeat(root, args.paths)))
    elif args.record == "recall":  # no change given: its "recall unavailable" line
        change, *paths = args.paths or [""]
        lines = review_pitfalls.recall(root, change, paths)
        review_pitfalls.record_shown(root, change, lines)
        print("\n".join(lines))
    elif args.record == "evaluate":
        print("\n".join(review_evaluate.evaluate(root)))
    elif args.record == "repeat":
        print("\n".join(review_repeat.rates(root)))
    elif args.record == "examples":
        print(review_examples.draft(root, args.limit))
    elif args.record == "fast-track":  # what the done rule reads, so the skill agrees with it
        print(str(round_guard.fast_track(root)).lower())
    else:
        print("\n".join(review_lessons.trend(root) + flakes.trend(root)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
