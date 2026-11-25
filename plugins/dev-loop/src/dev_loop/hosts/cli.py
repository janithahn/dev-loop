"""`dl host <verb> --json`: the adapter for shell callers; the body arrives on stdin."""

from __future__ import annotations

import json
import re
import select
import shutil
import subprocess
import sys
from collections.abc import Callable
from types import ModuleType
from typing import Any

from dev_loop.config import load, project_root
from dev_loop.hosts import HostError, adapter, review_run

def _set_status(host: ModuleType, a: dict[str, Any]) -> Any:  # noqa: ANN401
    """Set the status; on a hosted PR, a records-only push since the reviewed sha gets it."""
    sha, said = a["sha"], a["description"]
    root = project_root()
    hosted = not hasattr(host, "folder_of") and re.fullmatch(r"[0-9a-f]{7,40}", sha)
    if hosted and root.is_dir() and shutil.which("git"):

        def git(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(  # noqa: S603, S607 - git, with a checked hex sha
                ["git", *args], cwd=root, capture_output=True, text=True, check=False)

        head = git("rev-parse", "HEAD").stdout.strip()
        if head and not head.startswith(sha) and git(
                "diff", "--quiet", sha, "HEAD", "--", ".",
                f":(exclude){load(root).get('paths.changes')}").returncode == 0:
            sha, said = head, f"{said} (reviewed {a['sha'][:12]}; records only since)"
    return host.set_status(sha, a["state"], said, a.get("pr"))


VERBS: dict[str, Callable[[ModuleType, dict[str, Any]], Any]] = {
    "pr-create": lambda h, a: {
        "id": h.pr_create(a["source"], a["target"], a["title"], a["body"])},
    "pr-get": lambda h, a: h.pr_get(a["id"] if "id" in a else a["pr"]),
    "pr-for-branch": lambda h, a: {"id": h.pr_for_branch(a["branch"])},
    "threads": lambda h, a: h.threads(a["pr"]),
    "thread-post": lambda h, a: {
        "id": h.thread_post(a["pr"], a.get("file"), a.get("line"), a["text"])},
    "thread-reply": lambda h, a: h.thread_reply(a["pr"], a["thread"], a["text"]),
    "thread-edit": lambda h, a: h.thread_edit(a["pr"], a["thread"], a["text"]),
    "thread-status": lambda h, a: h.thread_status(a["pr"], a["thread"], a["status"]),
    "review-run": lambda h, a: review_run.record(h, a),
    "set-status": lambda h, a: _set_status(h, a),
}


def silent_stdin(seconds: int = 10) -> bool:
    """True when stdin sends nothing for `seconds` (POSIX; Windows has no select on pipes)."""
    # ponytail: Windows keeps the plain blocking read, as select works on sockets only there.
    if sys.platform == "win32" or not hasattr(sys.stdin, "fileno"):
        return False
    try:
        return not select.select([sys.stdin], [], [], seconds)[0]
    except (OSError, ValueError):  # an in-memory stdin has no descriptor to wait on
        return False


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in VERBS or argv[1] != "--json":  # noqa: PLR2004
        print(f"usage: dl host <{'|'.join(VERBS)}> --json  (body on stdin)", file=sys.stderr)
        return 2
    if silent_stdin():
        print(f"dl host {argv[0]}: no body on stdin after 10 s", file=sys.stderr)
        return 2
    try:
        print(json.dumps(VERBS[argv[0]](adapter(), json.loads(sys.stdin.read() or "{}"))))
    except HostError as error:
        print(f"dl host {argv[0]}: {error}", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError) as error:
        print(f"dl host {argv[0]}: bad body ({type(error).__name__})", file=sys.stderr)
        return 2
    return 0
