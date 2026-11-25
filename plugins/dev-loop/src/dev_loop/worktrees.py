"""Which checkout of the project holds a change folder (shared by the approval and recall hooks)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from dev_loop.config import load
from dev_loop.records.record import changes_dir


def home(root: Path, slug: str) -> tuple[Path | None, int]:
    """The checkout that holds <paths.changes>/<slug>, and how many hold it.

    The first of: the one on branch <branches.change_prefix><slug>; the root; the only one.
    """
    listing = subprocess.run(  # noqa: S603, S607 - git, to find the worktrees
        ["git", "worktree", "list", "--porcelain"], cwd=root, capture_output=True, text=True,
        check=False)
    trees: list[Path] = []
    branch: dict[Path, str] = {}
    for x in listing.stdout.splitlines():
        if x.startswith("worktree "):
            trees.append(Path(x[9:]).resolve())
        elif x.startswith("branch ") and trees:
            branch[trees[-1]] = x[7:]
    want = f"refs/heads/{load(root).get('branches.change_prefix')}{slug}"
    have = [r for r in dict.fromkeys((root, *trees)) if (changes_dir(r) / slug).is_dir()]
    on = [r for r in have if branch.get(r) == want]
    chosen = on[0] if on else root if root in have else have[0] if len(have) == 1 else None
    return chosen, len(have)
