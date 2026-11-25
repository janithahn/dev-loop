"""The tasks.md tools: `dl tasklist parse <change>` and `dl tasklist set <change> <id> <status>`.

Reads the block form (`### T<n>: title`; `T6b` is a task too; then `- Status:`, `- Kind:`,
`- Depends:`, `- Files:`).
parse prints JSON [{id,title,status,kind,depends,files,wave}]; the wave comes from Depends and from a file two tasks share, and a
dependency cycle is refused, naming the ids. set edits that one Status line in place.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

from .approval import approved, refusal, uncited
from .config import load, project_root
from .review.review_verdict import spec_lines

STATUSES = ("todo", "doing", "done", "blocked")
HEAD = re.compile(r"^### (T\d+[a-z]*): (.*?)\s*$")
BAD_HEAD = re.compile(r"^###\s+T\S*:")  # a task heading of any other id form
PART = re.compile(r"^## Part (\S+)")
FIELD = re.compile(r"^- (Status|Kind|Depends|Files):[ \t]*(.*?)\s*$")


def _path(change: str) -> Path:
    root = project_root()
    return root / load(root).values["paths"]["changes"] / Path(change).name / "tasks.md"


def _items(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_blocks(text: str) -> list[dict]:
    tasks: list[dict] = []
    part = None
    for line in text.splitlines():
        if found := PART.match(line):
            part = found[1]
        elif BAD_HEAD.match(line) and not HEAD.match(line):
            msg = f"task id is T<digits> and optional lowercase letters: {line.strip()}"
            raise ValueError(msg)
        elif head := HEAD.match(line):
            tasks.append({"id": head[1], "title": head[2], "status": "todo", "kind": None,
                          "depends": [], "files": [], **({"part": part} if part else {})})
        elif tasks and (field := FIELD.match(line)):
            name, value = field[1].lower(), field[2]
            tasks[-1][name] = _items(value) if name in ("depends", "files") else value or None
    return tasks


def done(tasks: list[dict]) -> bool:
    """Every task done; with `## Part` headings, every task of each started part (one at least)."""
    parts: dict[str | None, list[str]] = {}
    for task in tasks:
        parts.setdefault(task.get("part"), []).append(task["status"])
    started = [held for held in parts.values() if any(s != "todo" for s in held)]
    return bool(started) and all(s == "done" for held in started for s in held)


def add_waves(tasks: list[dict]) -> None:
    """Wave 1 has no dependency; else one more than its latest dependency or file-sharing task.

    ValueError on a cycle.
    """
    known = {task["id"] for task in tasks}
    for task in tasks:
        if missing := [dep for dep in task["depends"] if dep not in known]:
            msg = f"{task['id']} depends on unknown {', '.join(missing)}"
            raise ValueError(msg)
    waves: dict[str, int] = {}
    pending = list(tasks)
    while pending:
        ready = [t for t in pending if all(dep in waves for dep in t["depends"])]
        if not ready:
            ids = ", ".join(t["id"] for t in pending if _in_cycle(t, pending))
            msg = f"dependency cycle among {ids}"
            raise ValueError(msg)
        for task in ready:
            # a file two tasks name is edited by one builder at a time: the later task waits a wave
            # ponytail: equal paths only; a glob or `dir/` entry over another task's file is missed
            shared = [waves[t["id"]] for t in tasks
                      if t["id"] in waves and set(t["files"]) & set(task["files"])]
            waves[task["id"]] = 1 + max([*(waves[d] for d in task["depends"]), *shared], default=0)
        pending = [t for t in pending if t["id"] not in waves]
    for task in tasks:
        task["wave"] = waves[task["id"]]


def _in_cycle(task: dict, pending: list[dict]) -> bool:
    """True when the task can reach itself through the unresolved tasks."""
    by_id = {t["id"]: t for t in pending}
    seen: set[str] = set()
    stack = [d for d in task["depends"] if d in by_id]
    while stack:
        current = stack.pop()
        if current == task["id"]:
            return True
        if current not in seen:
            seen.add(current)
            stack += [d for d in by_id[current]["depends"] if d in by_id]
    return False


def set_status(text: str, task_id: str, status: str) -> str:
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if (h := HEAD.match(ln)) and h[1] == task_id), None)
    if start is None:
        msg = f"no task {task_id}"
        raise ValueError(msg)
    for i in range(start + 1, len(lines)):
        if HEAD.match(lines[i]):
            break
        if re.match(r"- Status:", lines[i]):
            ending = lines[i][len(lines[i].rstrip("\r\n")) :]
            lines[i] = f"- Status: {status}{ending}"
            return "".join(lines)
    msg = f"{task_id} has no Status line"
    raise ValueError(msg)


def _require_commit(root: Path, tasks: list[dict], task_id: str) -> None:
    """`done` needs exactly one commit past the integration branch, titled `T<n>: <title>`."""
    cfg = load(root)
    base = f"{cfg.get('branches.remote')}/{cfg.get('branches.integration')}"
    try:
        # a stacked change counts only its own commits: not those of a branch it sits on
        under = subprocess.run(
            ["git", "branch", "--no-contains", "HEAD", "--format=%(refname)", "--list",
             f"{cfg.get('branches.change_prefix')}*"], cwd=root, capture_output=True, text=True,
            check=False, timeout=30).stdout.split()
        log = subprocess.run(["git", "log", "--format=%H%x09%s", "HEAD", "--not", base, *under],
                             cwd=root, capture_output=True, text=True, check=False, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as error:
        msg = f"git log {base}..HEAD failed: {error}"
        raise ValueError(msg) from None
    if log.returncode:
        msg = f"git log {base}..HEAD failed: {log.stderr.strip()}"
        raise ValueError(msg)
    title = next((t["title"] for t in tasks if t["id"] == task_id), "")
    mine = [x.split("	", 1) for x in log.stdout.splitlines() if "	" in x]
    mine = [(sha, s) for sha, s in mine if s.startswith(f"{task_id}: ")]
    if [s for _, s in mine] != [f"{task_id}: {title}"]:
        msg = f'{task_id}: {len(mine)} commits, need one titled "{task_id}: {title}"'
        raise ValueError(msg)
    _require_scope(root, mine[0][0], next(t["files"] for t in tasks if t["id"] == task_id))


def _require_scope(root: Path, sha: str, files: list[str]) -> None:
    """Production source in the commit must sit in the task's `Files:` (equal, glob, or a dir/)."""
    globs = load(root).get("size.production")
    changed = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha,
         *(["--", *(f":(glob){g}" for g in globs)] if globs else [])],
        cwd=root, capture_output=True, text=True, check=False, timeout=30).stdout.split()
    for path in changed if globs else []:
        if not any(path == f or fnmatch(path, f) or (f.endswith("/") and path.startswith(f))
                   for f in files):
            msg = (f"{path} is outside the task's Files: add it to the task in tasks.md and say "
                   "why in design.md, or have the builder move the edit and amend")
            raise ValueError(msg)


def _require_commands(spec: Path) -> None:
    if not spec.is_file():
        return
    for bullet in spec_lines(spec.read_text(encoding="utf-8")):
        if not re.search(r"`[^`]+`", bullet):
            msg = f"spec.md: acceptance bullet names no command in backticks: {bullet[:60]}"
            raise ValueError(msg)


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 2 and argv[0] == "parse":
            path = _path(argv[1])
            _require_commands(path.with_name("spec.md"))
            tasks = parse_blocks(path.read_text(encoding="utf-8"))
            add_waves(tasks)
            print(json.dumps(tasks))
            return 0
        if len(argv) == 4 and argv[0] == "set":
            if argv[3] not in STATUSES:
                msg = f"unknown status {argv[3]!r}; one of {', '.join(STATUSES)}"
                raise ValueError(msg)
            path = _path(argv[1])
            if argv[3] == "doing" and not approved(path.parent):
                msg = refusal(argv[1])
                raise ValueError(msg)
            if argv[3] == "doing" and (missing := uncited(path.parent)):
                msg = (f"design.md does not address recalled lesson(s) {' '.join(missing)}: "
                       "name each by id with how the design handles it (plan step 3)")
                raise ValueError(msg)
            text = path.read_text(encoding="utf-8")
            new = set_status(text, argv[2], argv[3])
            if argv[3] == "done":
                _require_commit(project_root(), parse_blocks(text), argv[2])
            path.write_text(new, encoding="utf-8", newline="")
            return 0
    except (ValueError, OSError) as error:
        print(f"dl tasklist: {error}", file=sys.stderr)
        return 2
    print("usage: dl tasklist parse <change> | set <change> <id> <status>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
