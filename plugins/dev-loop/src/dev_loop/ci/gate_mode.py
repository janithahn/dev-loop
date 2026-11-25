"""Which sides of the pipeline a change runs, from its changed paths on stdin (one per line).

Feed it `git diff --name-only --no-renames <base> HEAD`, so a moved file lists its old path too.
`gates.sides` maps a side to its globs, space-separated (`fnmatch`: `*` crosses `/`). Prints each
side to run, one a line, in config order. A side runs when a path matches one of its globs.
Everything runs when the list is empty, when a path matches an `always` glob, or when a path
matches no side: an unclaimed path is never read as "nothing to run". A bad project file is
refused (exit 2), never read as no sides.
"""

from __future__ import annotations

import sys
from fnmatch import fnmatchcase

from dev_loop import config


def to_run(sides: dict[str, str], paths: list[str]) -> list[str]:
    """Return the sides the paths need, in config order; every side but `always` when unsure."""
    globs = {side: text.split() for side, text in sides.items()}
    every = [side for side in globs if side != "always"]
    claimed = {
        path: [side for side, patterns in globs.items()
               if any(fnmatchcase(path, pattern) for pattern in patterns)]
        for path in paths
    }
    if not paths or any(not hit or "always" in hit for hit in claimed.values()):
        return every
    return [side for side in every if any(side in hit for hit in claimed.values())]


def main(argv: list[str]) -> int:
    del argv  # no options: the paths come on stdin
    loaded = config.load(config.project_root())
    if loaded.error:
        print(f"dl gate-mode: {loaded.error}", file=sys.stderr)
        return 2
    paths = [line.strip() for line in sys.stdin if line.strip()]
    for side in to_run(loaded.get("gates.sides"), paths):
        print(side)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
