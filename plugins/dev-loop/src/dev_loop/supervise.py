"""Supervision facts for `dl clean`: git merge state, live worker runs, and worktree roots.

Section 4 of the supervision review. A worktree a live worker run holds is never removed, and a
squash-merged branch (its whole patch already in the integration ref, though no longer an ancestor)
counts as merged. Everything here is decided by git, a stat or a pid — never a model.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from dev_loop import config


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def integration_ref(repo: Path, remote: str, integration: str) -> str | None:
    """'merged' is judged against <remote>/<integration>, else local <integration>, else None."""
    for ref in (f"refs/remotes/{remote}/{integration}", f"refs/heads/{integration}"):
        if git(repo, "rev-parse", "--verify", "--quiet", ref).returncode == 0:
            return ref
    return None


def merged(cwd: Path, rev: str, ref: str) -> bool:
    """True when rev is in ref: an ancestor, or its whole patch already there (a squash merge).

    The squash case builds a single commit of rev's tree on their merge base and asks `git cherry`
    whether an equivalent patch (by patch id) is already in ref — the standard squash-merge probe.
    """
    if git(cwd, "merge-base", "--is-ancestor", rev, ref).returncode == 0:
        return True
    base = git(cwd, "merge-base", rev, ref).stdout.strip()
    tree = git(cwd, "rev-parse", f"{rev}^{{tree}}").stdout.strip()
    if not base or not tree:
        return False
    probe = git(cwd, "commit-tree", tree, "-p", base, "-m", "squash-probe").stdout.strip()
    return bool(probe) and git(cwd, "cherry", ref, probe).stdout.strip().startswith("-")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def live_runs(home: Path) -> set[str]:
    """The names of worker run folders whose recorded pid is alive; a worktree of one is kept.

    A worker names a task's worktree and its run folder after the task id, so a worktree is held by
    a live run when a run folder of the same name has a living pid.
    """
    base = home / ".dev-loop" / "worker-run"
    if not base.is_dir():
        return set()
    out = set()
    for folder in base.iterdir():
        pid = folder / "pid"
        text = pid.read_text(encoding="utf-8").strip() if pid.is_file() else ""
        if folder.is_dir() and text.isdigit() and alive(int(text)):
            out.add(folder.name)
    return out


def roots(root: Path, loaded: config.Loaded) -> list[tuple[Path, Path]]:
    """Every (worktree root, its checkout): the propose root, then each configured worker's."""
    pairs = [(root.parent / f"{root.name}-wt", root)]
    for worker in (loaded.get("workers") or {}).values():
        pairs.append((Path(worker["worktrees"]).expanduser(), Path(worker["checkout"]).expanduser()))
    return pairs
