"""UserPromptSubmit hook: a user message `Approved: <slug>` writes the change's approval record.

The only writer of the `approval` kind (`dl record` refuses it), so the agent cannot approve its
own proposal. The change folder is looked for in the project root and each worktree (see
`dev_loop.worktrees.home`); the record goes to the first of: the one on branch
`<branches.change_prefix><slug>`; the root; the only one holding the folder. Two or more with
none of those write nothing and say so, as does a slug that matches no folder.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RUN = re.compile(r"Approved: (\S+)")
SESSION = re.compile(r"[A-Za-z0-9_-]{1,64}")  # a plain id names the approving session
SRC = Path(__file__).resolve().parents[1] / "src"


def approve(event: dict, slug: str) -> None:
    """Write the approval record to the change's home, and show it on its pages."""
    from dev_loop.config import load
    from dev_loop.records.record import changes_dir, write
    from dev_loop.worktrees import home as find_home

    cwd = Path(event.get("cwd") or ".").resolve()
    found = (p for p in (cwd, *cwd.parents) if (p / "dev-loop.yaml").is_file())
    root = next(found, Path(os.environ.get("CLAUDE_PROJECT_DIR") or cwd).resolve())
    prefix = load(root).get("branches.change_prefix")
    home, held = find_home(root, slug)
    if home is None and held:
        print(f"approval: {slug} is in {held} worktrees and none is on {prefix}{slug}; "
              f"nothing written: remove the stale ones (git worktree remove <path>) and say "
              f"it again", file=sys.stderr)
    elif home is None:
        print(f"approval: no change named {slug}; nothing written", file=sys.stderr)
    if home is not None:
        at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")  # noqa: UP017 - 3.10 hosts
        session = str(event.get("session_id") or "")
        by = f"user message in session {session}" if SESSION.fullmatch(session) else "user message"
        write(home, slug, {"kind": "approval", "by": by, "at": f"{at} UTC"})
        for name in ("proposal.md", "status.md"):  # show it on both pages; other bytes stay
            page = changes_dir(home) / slug / name
            if page.is_file():
                text = page.read_bytes().decode("utf-8")
                text = text.replace("**Status:** Draft", "**Status:** Approved")
                text = re.sub(r"(\| Proposal \| )Draft( \|)", r"\1Approved\2", text)
                page.write_bytes(text.encode("utf-8"))


def main() -> None:
    event = json.load(sys.stdin)
    run = RUN.fullmatch((event.get("prompt") or "").strip())
    if run:
        from dev_loop.hookenv import add_venv

        add_venv()
        approve(event, run[1])


if __name__ == "__main__":
    sys.path.insert(0, str(SRC))
    try:
        main()
    except BaseException as error:  # noqa: BLE001 - fail open, loud: SystemExit too
        from dev_loop.hookenv import crashed

        crashed("approval", error, "nothing written")  # the line, and hook-errors.jsonl
        sys.exit(1)
