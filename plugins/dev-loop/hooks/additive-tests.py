"""PostToolUse hook on Edit|Write|MultiEdit: tests stay additive.

A file that matches `fix_proves.tests` is compared with its text at HEAD by
`dev_loop.ci.additive.removed`: an edit that removes a test, a check, or switches a test off
(a marker, a commented-out test, an early return) exits 2 with the reason on stderr; the edit
stands. The text before the edit is the event's `tool_response.originalFile` when it has one,
else the file at HEAD; the globs are matched against the path from the project root, as
`dl ci additive-tests` does. A file new since HEAD or no globs: exit 0. A malformed event or a
crash of its own, SystemExit included, fails open and loud: exit 1, one stderr line and one in
hook-errors.jsonl (dev_loop.hookenv.crashed). A removal no edit tool makes (`git rm`, `sed -i`)
is `dl ci additive-tests`'s to find.
Test: tests/test_additive_tests.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")  # this hook's own plugin code


def git(folder: Path, *args: str) -> str | None:
    done = subprocess.run(  # noqa: S603, S607 - git, fixed arguments
        ["git", "-C", str(folder), *args], capture_output=True, text=True, check=False,
    )
    return done.stdout if done.returncode == 0 else None


def at_head(path: Path) -> str | None:
    """Return the file's text at HEAD; None for a file outside git or new since HEAD."""
    top = git(path.parent, "rev-parse", "--show-toplevel", "--show-prefix")
    if top is None:
        return None
    root, _, prefix = top.partition(chr(10))
    return git(Path(root.strip()), "show", f"HEAD:{prefix.strip() + path.name}")


def main() -> int:
    event = json.loads(sys.stdin.read())
    path = Path(event["tool_input"]["file_path"].replace("\\", "/"))
    if not path.is_file():
        return 0
    sys.path.insert(0, SRC)
    from dev_loop.hookenv import add_venv  # noqa: PLC0415 - only for a file

    add_venv()  # the plugin venv's PyYAML, whichever Python 3 built it
    from dev_loop.ci.additive import MESSAGE, matches, removed  # noqa: PLC0415 - only for a file
    from dev_loop.config import load, project_root  # noqa: PLC0415

    project = project_root().resolve()
    if project not in path.resolve().parents:
        return 0
    globs = load(project).get("fix_proves.tests") or []
    if not matches(path.resolve().relative_to(project).as_posix(), globs):
        return 0
    response = event.get("tool_response")
    original = response.get("originalFile") if isinstance(response, dict) else None
    before = original if isinstance(original, str) else at_head(path)
    if before is None:
        return 0  # new since HEAD: nothing to compare
    said = removed([before], [path.read_text(encoding="utf-8")])
    if said:
        print(f"additive-tests: the edit {' and '.join(said)}; {MESSAGE}", file=sys.stderr)  # noqa: T201
        return 2
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException as error:  # noqa: BLE001 - fail open, loud: SystemExit too
        sys.path.insert(0, SRC)
        from dev_loop.hookenv import crashed

        crashed("additive-tests", error, "edit stands")  # the line, and hook-errors.jsonl
        code = 1
    sys.exit(code)
