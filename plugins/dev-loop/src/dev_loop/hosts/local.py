"""Local host adapter: pull requests and review threads with git and files only (no platform).

There is no hosted pull-request system: `pr_create` pushes the branch to `branches.remote` and
records a `pr` record (id = a short hash of the branch, with its base and head sha) in the
change's folder; `pr_get`/`pr_for_branch` read those records. Review threads live in the change's
`review-threads.jsonl`, keeping the crid text and every status; `set_status` records review-clean
in the change's records. Nothing reaches the network but `git push` and `git fetch`.

Every pr, review-thread and review-clean write is committed on the pull request's branch (only
that record file, one commit per write, the message naming the record kind). `pr_create` pushes
the branch; later record commits go out with the branch's next push and with `dl land`. The
`landed` record is written after the land push and is not committed. A write is refused unless
the project checkout is on that branch.

The change folder is the one `branches.change_prefix<change>` names: the branch with that prefix
stripped (else its last path segment).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from dev_loop import config
from dev_loop.hosts import HostError
from dev_loop.records.record_lock import replace_text

TIMEOUT = 60
STATUSES = ("active", "fixed", "wontFix", "closed", "byDesign", "pending")
STATES = ("success", "failure")
HOST = "host.jsonl"               # the change's pr, review-clean and landed records
THREADS = "review-threads.jsonl"  # the change's review threads, in the shape the loop reads


def _git(root: Path, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HostError(f"git call failed: {type(error).__name__}") from None
    if done.returncode:
        raise HostError(f"git {args[0]} failed")
    return done.stdout.strip()


def _loaded() -> tuple[Path, config.Loaded]:
    root = config.project_root()
    loaded = config.load(root)
    if loaded.error:
        raise HostError(loaded.error)
    return root, loaded


def _change_of(loaded: config.Loaded, branch: str) -> str:
    prefix = str(loaded.get("branches.change_prefix"))
    return branch[len(prefix):] if branch.startswith(prefix) else branch.rpartition("/")[2]


def _changes(root: Path, loaded: config.Loaded) -> Path:
    return root / str(loaded.get("paths.changes"))


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    found: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        try:
            found.append(json.loads(line))
        except json.JSONDecodeError:
            raise HostError(f"{path.name}:{number}: not JSON") from None
    return found


def _append(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write(path: Path, records: list[dict[str, Any]]) -> None:
    replace_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))


def _commit(path: Path, kind: str) -> None:
    """Commit one record file on the branch checked out at the root, one commit per write."""
    root, _ = _loaded()
    name = path.relative_to(root).as_posix()
    _git(root, "add", "--", name)
    if not _git(root, "status", "--porcelain", "--", name):
        return
    _git(root, "commit", "-q", "-m", f"dev-loop: {kind} record", "--", name)


def _on(branch: str) -> None:
    """Refuse a record write unless the project checkout is on the pull request's branch."""
    current = _git(config.project_root(), "branch", "--show-current")
    if current != branch:
        raise HostError(f"not on {branch!r} ({current or 'detached'}): records are committed "
                        "on the pull request's branch; run from its worktree")


def _find(pr_id: Any) -> tuple[Path, config.Loaded, Path, dict[str, Any]]:  # noqa: ANN401 - id
    """Return the root, config, change folder and pr record of a pull request, or raise."""
    root, loaded = _loaded()
    for base in [root, *_worktrees(root)]:
        archive = base / str(loaded.get("paths.archive"))
        for folder in sorted(p for p in _changes(base, loaded).glob("*") if p.is_dir()):
            if folder == archive:
                continue
            for record in reversed(_read(folder / HOST)):
                if record.get("kind") == "pr" and record.get("id") == pr_id:
                    return root, loaded, folder, record
    raise HostError(f"no pull request {pr_id!r}")


def _worktrees(root: Path) -> list[Path]:
    """Return the other worktrees of the repository, in list order (none if git fails)."""
    try:
        out = _git(root, "worktree", "list", "--porcelain")
    except HostError:
        return []
    found = [Path(line[9:]) for line in out.splitlines() if line.startswith("worktree ")]
    return [path for path in found if path != root]


def pr_create(source: str, target: str, title: str, body: str) -> str:
    """Record a pr record, commit it on the branch and push the branch; return its id."""
    root, loaded = _loaded()
    folder = _changes(root, loaded) / _change_of(loaded, source)
    if not folder.is_dir():
        raise HostError(f"no change folder for branch {source!r}")
    _on(source)
    remote = str(loaded.get("branches.remote"))
    _git(root, "fetch", remote, target)
    record = {"kind": "pr", "id": hashlib.sha1(source.encode()).hexdigest()[:12],  # noqa: S324
              "branch": source, "base": target, "base_sha": _git(root, "rev-parse",
              f"{remote}/{target}"), "head": _git(root, "rev-parse", source),
              "title": title, "description": body}
    _append(folder / HOST, record)
    _commit(folder / HOST, "pr")
    _git(root, "push", "-q", "--force-with-lease", remote, source)
    return str(record["id"])


def pr_get(pr_id: Any) -> dict[str, Any]:  # noqa: ANN401 - the id is the host's own
    """Return one pull request in the shape the loop reads; head is the branch's live tip."""
    root, _, folder, record = _find(pr_id)
    try:
        head = _git(root, "rev-parse", record["branch"])
    except HostError:
        head = record["head"]
    landed = any(r.get("kind") == "landed" for r in _read(folder / HOST))
    return {"pullRequestId": pr_id, "status": "completed" if landed else "active",
            "title": record.get("title", ""), "description": record.get("description", ""),
            "mergeStatus": "succeeded", "sourceRefName": f"refs/heads/{record['branch']}",
            "targetRefName": f"refs/heads/{record['base']}",
            "lastMergeSourceCommit": {"commitId": head}, "lastMergeCommit": {"commitId": ""}}


def pr_for_branch(branch: str) -> str | None:
    """Return the id of the pull request recorded for a branch, or None."""
    root, loaded = _loaded()
    folder = _changes(root, loaded) / _change_of(loaded, branch)
    for record in reversed(_read(folder / HOST)):
        if record.get("kind") == "pr" and record.get("branch") == branch:
            return str(record["id"])
    return None


def threads(pr_id: Any) -> list[dict[str, Any]]:  # noqa: ANN401 - the host's own id
    """Return every review thread, as held, in the shape the loop reads."""
    return _read(_find(pr_id)[2] / THREADS)


def thread_post(pr_id: Any, file: str | None, line: int | None, text: str) -> int:  # noqa: ANN401
    """Open a review thread (anchored at a file's right-side line when given); return its id."""
    _, _, folder, record = _find(pr_id)
    _on(record["branch"])
    path = folder / THREADS
    items = _read(path)
    tid = max((int(t["id"]) for t in items), default=0) + 1
    thread: dict[str, Any] = {"id": tid, "status": "active",
                              "comments": [{"id": tid, "content": text}]}
    if file:
        thread["threadContext"] = {"filePath": "/" + file.lstrip("/"),
                                   "rightFileStart": {"line": int(line or 1)}}
    _append(path, thread)
    _commit(path, "review-thread")
    return tid


def _update(pr_id: Any, thread_id: Any,  # noqa: ANN401 - the host's own ids
            change: Any) -> dict[str, Any]:  # noqa: ANN401 - a callback on the thread
    _, _, folder, record = _find(pr_id)
    _on(record["branch"])
    path = folder / THREADS
    items = _read(path)
    for thread in items:
        if int(thread["id"]) == int(thread_id):
            result = change(thread)
            _write(path, items)
            _commit(path, "review-thread")
            return result
    raise HostError(f"no thread {thread_id} on pull request {pr_id!r}")


def thread_reply(pr_id: Any, thread_id: Any, text: str) -> dict[str, Any]:  # noqa: ANN401
    """Add a comment to a thread; return the comment."""
    def add(thread: dict[str, Any]) -> dict[str, Any]:
        cid = max(int(c["id"]) for c in thread["comments"]) + 1
        comment = {"id": cid, "content": text}
        thread["comments"].append(comment)
        return comment
    return _update(pr_id, thread_id, add)


def thread_edit(pr_id: Any, thread_id: Any, text: str) -> dict[str, Any]:  # noqa: ANN401
    """Rewrite a thread's first comment; return the comment."""
    def edit(thread: dict[str, Any]) -> dict[str, Any]:
        thread["comments"][0]["content"] = text
        return dict(thread["comments"][0])
    return _update(pr_id, thread_id, edit)


def thread_status(pr_id: Any, thread_id: Any, status: str) -> dict[str, Any]:  # noqa: ANN401
    """Set a thread's status and return it; refuses a status the loop does not use."""
    if status not in STATUSES:
        raise HostError(f"thread status {status!r} is not one of {', '.join(STATUSES)}")

    def set_status_(thread: dict[str, Any]) -> dict[str, Any]:
        thread["status"] = status
        return {"id": int(thread_id), "status": status}
    return _update(pr_id, thread_id, set_status_)


def set_status(sha: str, state: str, description: str, pr: Any = None) -> dict[str, Any]:  # noqa: ANN401
    """Record review-clean in the change's records: success when a round filed no blocker/major."""
    if state not in STATES:
        raise HostError(f"status state {state!r} is not one of {', '.join(STATES)}")
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        raise HostError("sha must be 7 to 40 lowercase hex digits")
    if pr is None:
        raise HostError("pr is needed: a local status sits on a pull request")
    _, _, folder, found = _find(pr)
    _on(found["branch"])
    held = _read(folder / HOST)
    runs = [r for r in held if r.get("kind") == "review-run" and r.get("pr") == pr]
    if not runs or runs[-1].get("head") != sha:
        head = runs[-1].get("head") if runs else "none"
        raise HostError(f"sha is not the reviewed head {head}")
    record = {"kind": "review-clean", "pr": pr, "sha": sha, "state": state,
              "description": description}
    if record in held:
        return record  # the same status again: one record, no commit
    _append(folder / HOST, record)
    _commit(folder / HOST, "review-clean")
    return record


def folder_of(pr_id: Any) -> Path:  # noqa: ANN401 - the host's own id
    """Return the change folder a pull request belongs to (for the lander's done rule)."""
    return _find(pr_id)[2]


def records(folder: Path) -> list[dict[str, Any]]:
    """Return the host records of a change folder."""
    return _read(folder / HOST)


def mark_landed(pr_id: Any) -> None:  # noqa: ANN401 - the host's own id
    """Record that a pull request was landed (its pr_get then reads completed)."""
    _, _, folder, record = _find(pr_id)
    _append(folder / HOST, {"kind": "landed", "id": pr_id, "head": record["head"]})
