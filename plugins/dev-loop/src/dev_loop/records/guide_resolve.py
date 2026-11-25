"""guide_resolve.py <CLAUDE.md>: resolve a rebase conflict in an area guide (stack_rebase.sh).

Only when every conflict hunk sits inside the generated do-not-repeat block (`dl records rules
--write` regenerates it): takes the upstream side there. Exits 1 with the file untouched when a
hunk touches the guide's own prose or crosses a block marker.
"""

from __future__ import annotations

import pathlib
import sys

START, END = "<!-- do-not-repeat:start -->", "<!-- do-not-repeat:end -->"


def resolved(lines: list[str], path: pathlib.Path) -> list[str]:
    """Return the lines with the block's conflicts settled; SystemExit on any other conflict."""
    out, inside, mode = [], False, None  # mode: None, "ours", "base", "theirs"
    for line in lines:
        if line.startswith("<<<<<<< "):
            if not inside:
                sys.exit(f"prose conflict in {path}")
            mode = "ours"
        elif mode and line.startswith("||||||| "):
            mode = "base"
        elif mode and line.rstrip("\r\n") == "=======":
            mode = "theirs"
        elif mode and line.startswith(">>>>>>> "):
            mode = None
        elif mode and line.strip() in (START, END):
            sys.exit(f"conflict across the block marker in {path}")
        elif mode not in ("base", "theirs"):
            if mode is None:
                inside = {START: True, END: False}.get(line.strip(), inside)
            out.append(line)
    return out


def main(argv: list[str]) -> int:
    path = pathlib.Path(argv[0])
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(resolved(lines, path)), encoding="utf-8", newline="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
