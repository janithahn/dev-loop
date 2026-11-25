"""GitHub adapter: pull requests and review threads through the `gh` CLI.

gh holds the login; this code handles no token and never repeats gh's output in an error.
Owner and repo come from `host.org` and `host.repo`. A thread's id is its first comment's id: a
file-anchored thread is a review comment thread, a PR-level one an issue comment.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.parse
from typing import Any

from dev_loop import config
from dev_loop.hosts import HostError

TIMEOUT = 60
LOGGED_OUT = 4  # gh's exit code when it needs `gh auth login`
NOT_FOUND = 404
RESOLVED = {"active": False, "fixed": True, "closed": True, "wontFix": True}
THREADS = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) {
    reviewThreads(first: 100) { nodes { id isResolved path line
      comments(first: 100) { nodes { databaseId body } } } } } } }"""
STATUS_CONTEXT = "dev-loop/review-clean"
STATUS_STATES = ("success", "failure")
DESCRIPTION_MAX = 140  # GitHub refuses a longer status description
MUTATION = "mutation($id: ID!) {{ {verb}(input: {{threadId: $id}}) {{ thread {{ id }} }} }}"


def _gh(args: list[str], body: object = None) -> Any:  # noqa: ANN401 - parsed JSON
    if shutil.which("gh") is None:
        msg = "gh not found"
        raise HostError(msg)
    send = None if body is None else json.dumps(body)
    command = ["gh", "api", *args, *([] if send is None else ["--input", "-"])]
    try:
        done = subprocess.run(command, input=send, capture_output=True, text=True,
                              timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HostError(f"gh call failed: {type(error).__name__}") from None
    if done.returncode == LOGGED_OUT or "gh auth login" in done.stderr:
        msg = "gh is not logged in"
        raise HostError(msg)
    if done.returncode:
        code = re.search(r"HTTP (\d{3})", done.stderr)
        status = int(code.group(1)) if code else 0
        raise HostError(f"gh api answered {status or f'exit {done.returncode}'}", status)
    try:
        return json.loads(done.stdout or "{}")
    except json.JSONDecodeError:
        raise HostError("gh api answered something other than JSON") from None


def logged_in() -> bool:
    """Whether `gh auth status` says gh is logged in; its output (may show a token) is dropped."""
    if shutil.which("gh") is None:
        msg = "gh not found"
        raise HostError(msg)
    try:
        done = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True,
                              timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HostError(f"gh call failed: {type(error).__name__}") from None
    return done.returncode == 0


def _call(method: str, path: str, body: object = None) -> Any:  # noqa: ANN401 - parsed JSON
    return _gh([path, "--method", method], body)


def _repo() -> tuple[str, str]:
    loaded = config.load(config.project_root())
    owner, name = loaded.get("host.org"), loaded.get("host.repo")
    for key, value in (("org", owner), ("repo", name)):
        if not value:
            msg = f"host.{key} is not set in dev-loop.yaml"
            raise HostError(msg)
    return str(owner), str(name)


def _path(rest: str) -> str:
    owner, name = _repo()
    return f"repos/{owner}/{name}/{rest}"


def pr_create(source: str, target: str, title: str, body: str) -> int:
    """Open a pull request and return its number."""
    payload = {"head": source, "base": target, "title": title, "body": body}
    return int(_call("POST", _path("pulls"), payload)["number"])


def pr_get(pr_id: int) -> dict[str, Any]:
    """Return one pull request, with the fields the loop reads under the names it reads."""
    found = _call("GET", _path(f"pulls/{int(pr_id)}"))
    return {**found, "pullRequestId": found["number"], "description": found.get("body") or "",
            "sourceRefName": f"refs/heads/{found['head']['ref']}",
            "targetRefName": f"refs/heads/{found['base']['ref']}",
            "lastMergeSourceCommit": {"commitId": found["head"]["sha"]},
            "lastMergeCommit": {"commitId": found.get("merge_commit_sha") or ""}}


def pr_for_branch(branch: str) -> int | None:
    """Return the number of the open pull request from a branch, or None."""
    owner, _ = _repo()
    head = urllib.parse.quote(f"{owner}:{branch}", safe=":/")
    found = _call("GET", _path(f"pulls?state=open&head={head}")) or []
    return int(found[0]["number"]) if found else None


def _review_threads(pr_id: int) -> list[dict[str, Any]]:
    owner, name = _repo()
    variables = {"owner": owner, "name": name, "number": int(pr_id)}
    found = _gh(["graphql"], {"query": THREADS, "variables": variables})
    return found["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]


def threads(pr_id: int) -> list[dict[str, Any]]:
    """Return every thread, review threads first, in the shape the loop reads; text verbatim."""
    out = []
    for node in _review_threads(pr_id):
        notes = [{"id": note["databaseId"], "content": note["body"]}
                 for note in node["comments"]["nodes"]]
        out.append({"id": notes[0]["id"], "status": "fixed" if node["isResolved"] else "active",
                    "threadContext": {"filePath": "/" + node["path"],
                                      "rightFileStart": {"line": node["line"]}},
                    "comments": notes})
    for note in _call("GET", _path(f"issues/{int(pr_id)}/comments?per_page=100")):
        out.append({"id": note["id"], "status": "active",
                    "comments": [{"id": note["id"], "content": note["body"]}]})
    return out


def thread_post(pr_id: int, file: str | None, line: int | None, text: str) -> int:
    """Open a thread (a review comment on a file's right-side line when given); return its id."""
    if not file:
        return int(_call("POST", _path(f"issues/{int(pr_id)}/comments"), {"body": text})["id"])
    head = pr_get(pr_id)["lastMergeSourceCommit"]["commitId"]
    payload = {"body": text, "commit_id": head, "path": file.lstrip("/"),
               "line": int(line or 1), "side": "RIGHT"}
    return int(_call("POST", _path(f"pulls/{int(pr_id)}/comments"), payload)["id"])


def thread_reply(pr_id: int, thread_id: int, text: str) -> dict[str, Any]:
    """Reply in a review thread; a PR-level thread gets a new PR comment. Return the comment."""
    try:
        path = _path(f"pulls/{int(pr_id)}/comments/{int(thread_id)}/replies")
        return _call("POST", path, {"body": text})
    except HostError as error:
        if error.status != NOT_FOUND:
            raise
    return _call("POST", _path(f"issues/{int(pr_id)}/comments"), {"body": text})


def thread_edit(pr_id: int, thread_id: int, text: str) -> dict[str, Any]:
    """Rewrite a thread's first comment; return the comment."""
    try:
        return _call("PATCH", _path(f"pulls/comments/{int(thread_id)}"), {"body": text})
    except HostError as error:
        if error.status != NOT_FOUND:
            raise
    return _call("PATCH", _path(f"issues/comments/{int(thread_id)}"), {"body": text})


def thread_status(pr_id: int, thread_id: int, status: str) -> dict[str, Any]:
    """Resolve (fixed, closed, wontFix) or unresolve (active) a review thread."""
    if status not in RESOLVED:
        msg = f"thread status {status!r} is not one of {', '.join(RESOLVED)}"
        raise HostError(msg)
    nodes = [node["id"] for node in _review_threads(pr_id)
             if node["comments"]["nodes"][0]["databaseId"] == int(thread_id)]
    if not nodes:
        msg = f"no review thread {int(thread_id)} on PR {int(pr_id)}; a PR comment has no status"
        raise HostError(msg)
    verb = "resolveReviewThread" if RESOLVED[status] else "unresolveReviewThread"
    _gh(["graphql"], {"query": MUTATION.format(verb=verb), "variables": {"id": nodes[0]}})
    return {"id": int(thread_id), "status": status}


def set_status(
    sha: str, state: str, description: str, pr: int | None = None,  # noqa: ARG001 - ado's need
) -> dict[str, Any]:
    """Set the `dev-loop/review-clean` commit status: success when a round filed no blocker
    and no major, else failure."""
    if state not in STATUS_STATES:
        msg = f"status state {state!r} is not one of {', '.join(STATUS_STATES)}"
        raise HostError(msg)
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        msg = "sha must be 7 to 40 lowercase hex digits"
        raise HostError(msg)
    payload = {"state": state, "context": STATUS_CONTEXT,
               "description": description[:DESCRIPTION_MAX]}
    return _call("POST", _path(f"statuses/{sha}"), payload)
