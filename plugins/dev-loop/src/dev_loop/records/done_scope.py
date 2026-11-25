"""`dl record done`: each finished change's missing lines (record.missing), split from record.py.

    dl record done [<change>...] [part=<a>]  # the named changes, or every change
    dl record done --touched-paths -         # the done guard's: git's `-z` paths on stdin

Under `--touched-paths` the scope is the change folders holding a path read: each path is
resolved under git's top level, and its first part below `paths.changes` counts only when
record.change_folders has it (never the archive, never a moved change's old name). The shell
maps no path to a name, so no platform's `realpath`, quoting or argument parsing decides it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..config import project_root
from . import record


def touched(root: Path, raw: bytes) -> set[str]:
    """Return the change folders' names holding a path of `raw` (NUL-separated, from git's top)."""
    # `--show-cdup` prints only `../` parts: no locale decodes a non-ASCII top level (v422j).
    git = ["git", "rev-parse", "--show-cdup"]
    cdup = subprocess.run(git, cwd=root, capture_output=True, text=True, check=True).stdout
    top = (root / cdup.strip()).resolve()
    changes = record.changes_dir(root).resolve()
    names = {folder.name for folder in record.change_folders(root)}
    found: set[str] = set()
    for path in filter(None, raw.split(b"\0")):
        full = (top / os.fsdecode(path)).resolve()
        if full != changes and full.is_relative_to(changes):
            found |= {full.relative_to(changes).parts[0]} & names
    return found


def report_done(args: list[str]) -> int:
    """Print each finished change's missing lines; a named change that is none is refused (2)."""
    root = project_root()
    known = record.change_folders(root)
    part = None
    if args == ["--touched-paths", "-"]:
        wanted = touched(root, sys.stdin.buffer.read())
        if not wanted:  # no path read, or none in a change folder: nothing of ours touched
            return 0
    else:
        part = next((a[5:] for a in args if a.startswith("part=")), None)
        wanted = {a for a in args if not a.startswith("part=")}  # none: every change
        if unknown := sorted(wanted - {f.name for f in known}):  # a typo never reads as clean
            print(f"dl record: no change named {', '.join(unknown)}", file=sys.stderr)
            return 2
    for folder in (f for f in known if f.name in wanted or not wanted):
        if record.tasks_done(folder, part) and (gaps := record.missing(folder, part)):
            print(f"  [done?] {folder.name}: all tasks complete but missing: {', '.join(gaps)}")
    return 0
