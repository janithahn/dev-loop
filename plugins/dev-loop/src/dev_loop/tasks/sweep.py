"""`dl tasks sweep <run_dir> <worktrees_root> <stale_hours>`: supervise this worker's open tasks.

Every verdict is a pid, git or the task query — never a model. One automatic retry, then a
person: a stalled task (Doing, no live run, its worktree still holding unpushed work) is re-run
once, its id printed for the runner, and a `.retried-<id>` stamp dropped; on the next sweep it is
tagged `needs-coordinator` instead (no third retry). A question or a queued task older than
`stale_hours` is tagged straight away. Work is never removed: the sweep only comments, tags and
asks the runner to re-run.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dev_loop.tasks import agent_tasks as at

NEEDS = "needs-coordinator"
QUESTION = "QUESTION:"


def _alive(pid: str) -> bool:
    if not pid.isdigit():
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def live_run(run: Path, item_id: str) -> bool:
    """True when this machine runs the task: a lock dir with a live pid or a queued re-run."""
    lock = run / item_id
    if not lock.is_dir():
        return False
    if (lock / "again").exists():
        return True
    pid = lock / "pid"
    return _alive(pid.read_text(encoding="utf-8").strip()) if pid.is_file() else False


def _git(tree: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=tree, capture_output=True,
                          text=True, check=False).stdout.strip()


def worktree_unfinished(tree: Path) -> bool:
    """True when the worktree has uncommitted changes or commits on no remote (work left behind)."""
    if not (tree / ".git").exists():
        return False
    if _git(tree, "status", "--porcelain"):
        return True
    return _git(tree, "rev-list", "HEAD", "--not", "--remotes", "--count") not in ("", "0")


def _hours_since(iso: str) -> float:
    if not iso:
        return 0.0
    return (datetime.now(UTC) - datetime.fromisoformat(iso)).total_seconds() / 3600


def _question(notes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The latest comment when it is this worker's own unanswered QUESTION, else None."""
    if not notes:
        return None
    last = notes[-1]
    text = at.plain(last["text"])
    return last if text.startswith(at.MARK) and QUESTION in text else None


def escalate(item_id: str, why: str) -> None:
    """Comment and tag `needs-coordinator`; both idempotent, so a re-sweep is a no-op."""
    at.comment(item_id, f"needs the coordinator: {why}")
    at.work().add_tag(int(item_id), NEEDS)


def _stall(item_id: str, run: Path, tree: Path, stale: float) -> None:
    """A Doing task with no live run: hold a stale question, re-run a worktree once, then escalate."""
    question = _question(at.work().comments(int(item_id)))
    if question is not None:
        if _hours_since(question["createdDate"]) > stale:
            escalate(item_id, "a question has waited past the limit")
        return  # waiting for the coordinator; never retried
    if not worktree_unfinished(tree):
        return  # clean or pushed: not a stall (the work awaits a merge)
    stamp = run / f".retried-{item_id}"
    if stamp.exists():
        escalate(item_id, "stalled with unpushed work after one retry")
    else:
        stamp.write_text("1", encoding="utf-8")
        print(f"#{item_id}", flush=True)  # the runner re-runs it once


def sweep(run_dir: str, worktrees_root: str, stale_hours: float) -> None:
    """Supervise each open task; print #id for every task the runner should (re-)run."""
    run, roots = Path(run_dir), Path(worktrees_root)
    for item in at.tasks():
        item_id, fields = str(item["id"]), item["fields"]
        if NEEDS in fields.get("System.Tags", ""):
            continue  # already handed over: no third retry
        state = fields["System.State"]
        if state == "Doing" and not live_run(run, item_id):
            _stall(item_id, run, roots / item_id, stale_hours)
        elif state == "To Do" and _hours_since(fields.get("System.CreatedDate", "")) > stale_hours:
            escalate(item_id, "queued without a run past the limit")
