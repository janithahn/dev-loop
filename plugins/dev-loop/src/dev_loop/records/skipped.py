"""The sidecar lines the readers skip (a line off the grammar, or with no Markdown line on file).

`dl records --skipped` lists them as file:line; recall prints `note` on stderr, so a skip is said.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .record import MARKDOWN, MIGRATED, change_folders, changes_dir, text_of, trusted


def lines(root: Path) -> list[str]:
    """Return `file:line` of every sidecar line `record.trusted` finds a fault in."""
    old, found = text_of(changes_dir(root) / MIGRATED).split(), []
    for folder in change_folders(root):
        try:
            checked = trusted(folder, MARKDOWN, folder.name in old)
        except UnicodeDecodeError:  # the whole file is skipped
            found.append((folder / "records.jsonl").relative_to(root).as_posix() + ":1")
            continue
        where = (folder / "records.jsonl").relative_to(root).as_posix()
        found += [f"{where}:{number}" for number, _, bad in checked if bad]
    return found


def note(root: Path) -> None:
    """Say on stderr how many record lines were skipped and in which files; silent at none."""
    found = lines(root)
    files = sorted({x.rpartition(":")[0] for x in found})
    if found:
        print(f"skipped {len(found)} unparsed record lines in {', '.join(files)}", file=sys.stderr)
