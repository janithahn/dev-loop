"""Recompute the review Trend over every PR's metrics row, re-runnable.

    python -m dev_loop.records.review_trend [--ref origin/main] [--skip 215,220] 203-210

For each window of PR ids: the merged PRs with a metrics row and at least one review round (the
frozen metrics tables and each change's own records, via review_records).
Per PR: round-1 blocker+major (the first item of "Per round"), all-round blocker+major (the
"Total" column), rounds, and changed lines: added plus deleted in the PR's merge commit
("Merged PR <id>:" on the first-parent history of --ref, by default origin/<branches.integration>)
against its first parent, generated files left out (lockfiles, *.gen.ts, `size.generated` and
`size.exclude`).
Prints one line per PR, then the medians per window (and the pooled rate: all round-1 blocker+major
over all changed lines, which one small PR cannot swing). Reads git and the rows only; no network.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import statistics
import subprocess
import sys
from pathlib import Path

from ..config import load, project_root
from .review_records import METRIC_ROW, metric_rows

GENERATED = ("*.lock", "*package-lock.json", "*schema.d.ts", "*.gen.ts")
COUNTS = re.compile(r"(\d+)/(\d+)/(\d+)")
Table = dict[int, tuple[int, int, int]]  # PR -> (rounds, round-1 b+M, all-round b+M)


def rows(root: Path) -> Table:
    """Return PR -> (rounds, round-1 blocker+major, all-round blocker+major) from the rows."""
    found: Table = {}
    for line in metric_rows(root):
        match = METRIC_ROW.match(line)
        if not match:
            continue
        pr, rounds, per_round = int(match[1]), int(match[2]), match[3]
        first = COUNTS.search(per_round)
        round_one = int(first[1]) + int(first[2]) if first else 0
        found[pr] = (rounds, round_one, int(match[4]) + int(match[5]))
    return found


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout


def changed_lines(root: Path, pr: int, ref: str) -> int | None:
    """Added plus deleted lines of the PR's merge commit, generated files left out."""
    commit = git(root, "log", "--first-parent", ref, "--format=%H", f"--grep=^Merged PR {pr}:")
    if not commit.split():
        return None  # not merged into ref
    size, total = load(root).get("size"), 0
    skipped = (*GENERATED, *size["generated"], *size["exclude"])
    first = commit.split()[0]
    for line in git(root, "diff", "--numstat", f"{first}^1", first).splitlines():
        added, deleted, path = line.split("\t", 2)
        if added == "-" or any(fnmatch.fnmatch(path, pattern) for pattern in skipped):
            continue  # a binary file, or a generated one
        total += int(added) + int(deleted)
    return total


def window(root: Path, spec: str, table: Table, args: tuple[str, set[int]]) -> None:
    """Print each PR of the window, then its medians."""
    ref, skip = args
    low, high = (int(part) for part in spec.split("-"))
    per_k, round_one, all_rounds, rounds, sizes, included = [], [], [], [], [], []
    for pr in range(low, high + 1):
        if pr not in table or table[pr][0] == 0 or pr in skip:
            continue  # no row yet, a PR that ran no review, or one left out (--skip)
        lines = changed_lines(root, pr, ref)
        if not lines:
            continue
        count, first, total = table[pr]
        print(f"  PR {pr}: {lines} lines, rounds {count}, round-1 b+M {first} "
              f"({1000 * first / lines:.2f} per 1k), all rounds b+M {total}")
        per_k.append(1000 * first / lines)
        round_one.append(first)
        all_rounds.append(total)
        rounds.append(count)
        sizes.append(lines)
        included.append(pr)
    if not included:
        print(f"{spec}: no merged reviewed PR")
        return
    print(
        f"{spec}: {len(included)} PRs ({', '.join(map(str, included))}); medians: round-1 b+M "
        f"per 1k lines {statistics.median(per_k):.2f} "
        f"(pooled {1000 * sum(round_one) / sum(sizes):.2f}), "
        f"per PR {statistics.median(round_one):g}; "
        f"all-round b+M per PR {statistics.median(all_rounds):g}; "
        f"rounds per PR {statistics.median(rounds):g}; "
        f"changed lines per PR {statistics.median(sizes):g}"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("windows", nargs="+", help="PR id ranges, e.g. 203-210")
    parser.add_argument("--ref", help="default: <branches.remote>/<branches.integration>")
    parser.add_argument("--skip", default="", help="PR ids to leave out, e.g. 215,220")
    args = parser.parse_args(argv)
    root = project_root()
    loaded = load(root)
    ref = args.ref or f"{loaded.get('branches.remote')}/{loaded.get('branches.integration')}"
    table = rows(root)
    skip = {int(pr) for pr in args.skip.split(",") if pr}
    for spec in args.windows:
        window(root, spec, table, (ref, skip))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
