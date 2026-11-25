"""Azure DevOps adapter: credential, request, the pull request and the thread calls.

The token is read once per call, sent in one header and never printed, logged, put in a URL
or carried by an exception. Org, project and repo come from the `host.*` config.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from dev_loop import config
from dev_loop.hosts import HostError

API = "api-version=7.1"
TIMEOUT = 30
SERVER_ERROR = range(500, 600)
DESCRIPTION_MAX = 4000  # the host refuses a longer PR description
CUT = "\n(cut: the whole text is in the change's proposal.md)"
WEB_PAGE = "host answered a web page, not the API: check host.org, host.project and the credential"


def _host() -> dict[str, Any]:
    loaded = config.load(config.project_root())
    return {key: loaded.get(f"host.{key}") for key in ("org", "project", "repo", "token_env")}


def _need(host: dict[str, Any], *keys: str) -> None:
    for key in keys:
        if not host[key]:
            msg = f"host.{key} is not set in dev-loop.yaml"
            raise HostError(msg)


def credential() -> str:
    """Return the token: `git credential fill` for the org's URL, else the `token_env` variable."""
    host = _host()
    _need(host, "org")
    ask = f"url=https://dev.azure.com/{host['org']}\n\n"
    # never prompt: no terminal, no askpass, no Credential Manager dialog
    quiet = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true",
             "GCM_INTERACTIVE": "never"}
    try:
        done = subprocess.run(
            ["git", "credential", "fill"], input=ask, capture_output=True, text=True,
            timeout=TIMEOUT, check=False, env=quiet)
        lines = done.stdout.splitlines() if done.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        lines = []
    for line in lines:
        key, _, value = line.partition("=")
        if key == "password" and value:
            return value
    token = os.environ.get(str(host["token_env"] or ""), "")
    if not token:
        msg = f"no credential: git has none for the org and ${host['token_env']} is empty"
        raise HostError(msg)
    return token


def _send(req: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:  # noqa: S310
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise HostError(f"host answered HTTP {error.code}{_said(error, req)}", error.code) from None
    except json.JSONDecodeError:
        raise HostError(f"host call failed: {WEB_PAGE}") from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise HostError(f"host call failed: {type(error).__name__}") from None


def _said(error: urllib.error.HTTPError, req: urllib.request.Request) -> str:
    """`: <message>` from a JSON error body (one line, 200 characters, the token masked)."""
    try:
        message = json.loads(error.read()).get("message")
    except (OSError, ValueError, AttributeError):
        return ""
    if not isinstance(message, str):
        return ""
    for secret in sorted(_secrets(req), key=len, reverse=True):
        message = message.replace(secret, "***")
    return f": {' '.join(message.split())[:200]}"


def _secrets(req: urllib.request.Request) -> set[str]:
    """Every Authorization value in the request, its credential and a Basic one's token."""
    found: set[str] = set()
    for name, value in req.header_items():
        if name.lower() != "authorization":
            continue
        scheme, _, credential = value.partition(" ")
        found |= {value, credential}
        if scheme.lower() == "basic":
            try:
                found.add(base64.b64decode(credential).decode().partition(":")[2])
            except ValueError:  # binascii.Error and UnicodeDecodeError are ValueErrors
                pass
    return found - {""}


def request(
    method: str, path: str, body: object = None, kind: str = "application/json"
) -> dict[str, Any]:
    """Call the host; `path` is under `/<org>/<project>`. One retry on a 5xx.

    A path that names its own `api-version` keeps it (preview calls).
    """
    host = _host()
    _need(host, "org", "project")
    return _call(method, f"/{host['org']}/{host['project']}{path}", body, kind)


def org_request(method: str, path: str, body: object = None) -> dict[str, Any]:
    """Call an organisation-level API (projects, connection data): `path` is under `/<org>`."""
    host = _host()
    _need(host, "org")
    return _call(method, f"/{host['org']}{path}", body, "application/json")


def _call(method: str, path: str, body: object, kind: str) -> dict[str, Any]:
    base = os.environ.get("DEV_LOOP_HOST_URL", "https://dev.azure.com")
    sep = "&" if "?" in path else "?"
    version = "" if "api-version=" in path else f"{sep}{API}"
    url = f"{base}{path}{version}"
    auth = base64.b64encode(f":{credential()}".encode()).decode()
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": kind}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310
    req.add_unredirected_header("Authorization", f"Basic {auth}")  # never sent on a redirect
    try:
        return _send(req)
    except HostError as error:
        if error.status not in SERVER_ERROR:
            raise
    return _send(req)


def _repo() -> str:
    host = _host()
    _need(host, "repo")
    return f"/_apis/git/repositories/{host['repo']}/pullrequests"


def pr_create(source: str, target: str, title: str, body: str) -> int:
    """Open a pull request and return its id; a description over the host's limit is cut."""
    units = body.encode("utf-16-le")  # the host counts UTF-16 units; a half pair is dropped
    if len(units) // 2 > DESCRIPTION_MAX:
        body = units[:2 * (DESCRIPTION_MAX - len(CUT))].decode("utf-16-le", "ignore") + CUT
    payload = {"sourceRefName": f"refs/heads/{source}", "targetRefName": f"refs/heads/{target}",
               "title": title, "description": body}
    return int(request("POST", _repo(), payload)["pullRequestId"])


def pr_get(pr_id: int) -> dict[str, Any]:
    """Return one pull request."""
    return request("GET", f"{_repo()}/{int(pr_id)}")


def pr_vote(pr_id: int, reviewer: str, vote: int) -> dict[str, Any]:
    """Cast a reviewer's vote (10: approved)."""
    return request("PUT", f"{_repo()}/{int(pr_id)}/reviewers/{reviewer}", {"vote": vote})


def pr_auto_complete(pr_id: int, by: str, options: dict[str, Any]) -> dict[str, Any]:
    """Set auto-complete on a pull request with these completion options."""
    body = {"autoCompleteSetBy": {"id": by}, "completionOptions": options}
    return request("PATCH", f"{_repo()}/{int(pr_id)}", body)


def pr_for_branch(branch: str) -> int | None:
    """Return the id of the active pull request from a branch, or None."""
    query = urllib.parse.urlencode(
        {"searchCriteria.sourceRefName": f"refs/heads/{branch}", "searchCriteria.status": "active"})
    found = request("GET", f"{_repo()}?{query}").get("value") or []
    return int(found[0]["pullRequestId"]) if found else None


THREAD_STATUSES = ("active", "fixed", "wontFix", "closed", "byDesign", "pending")


def _threads(pr_id: int) -> str:
    return f"{_repo()}/{int(pr_id)}/threads"


def threads(pr_id: int) -> list[dict[str, Any]]:
    """Return every thread on a pull request, comments included, text as the host holds it."""
    return list(request("GET", _threads(pr_id)).get("value") or [])


def thread_post(pr_id: int, file: str | None, line: int | None, text: str) -> int:
    """Open an active thread (on a file's right-side line when given) and return its id."""
    payload: dict[str, Any] = {
        "comments": [{"parentCommentId": 0, "content": text, "commentType": 1}],
        "status": "active"}
    if file:
        at = {"line": int(line or 1), "offset": 1}
        payload["threadContext"] = {"filePath": "/" + file.lstrip("/"),
                                    "rightFileStart": at, "rightFileEnd": at}
    return int(request("POST", _threads(pr_id), payload)["id"])


def thread_reply(pr_id: int, thread_id: int, text: str) -> dict[str, Any]:
    """Add a comment to a thread; return the comment."""
    payload = {"parentCommentId": 0, "content": text, "commentType": 1}
    return request("POST", f"{_threads(pr_id)}/{int(thread_id)}/comments", payload)


def thread_edit(pr_id: int, thread_id: int, text: str) -> dict[str, Any]:
    """Rewrite a thread's first comment (a status kept in one place); return the comment."""
    return request("PATCH", f"{_threads(pr_id)}/{int(thread_id)}/comments/1", {"content": text})


def thread_status(pr_id: int, thread_id: int, status: str) -> dict[str, Any]:
    """Set a thread's status and return the thread; refuses a status the host does not know."""
    if status not in THREAD_STATUSES:
        msg = f"thread status {status!r} is not one of {', '.join(THREAD_STATUSES)}"
        raise HostError(msg)
    return request("PATCH", f"{_threads(pr_id)}/{int(thread_id)}", {"status": status})


STATUS_CONTEXT = {"genre": "dev-loop", "name": "review-clean"}
STATUS_STATES = {"success": "succeeded", "failure": "failed"}


def set_status(sha: str, state: str, description: str, pr: int | None = None) -> dict[str, Any]:
    """Post the `dev-loop/review-clean` PR status on the PR's last iteration: succeeded when a
    round filed no blocker and no major, else failed. Refuses when the head moved past `sha`."""
    if state not in STATUS_STATES:
        msg = f"status state {state!r} is not one of {', '.join(STATUS_STATES)}"
        raise HostError(msg)
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        msg = "sha must be 7 to 40 lowercase hex digits"
        raise HostError(msg)
    if pr is None:
        msg = "pr is needed: an ado status sits on a pull request"
        raise HostError(msg)
    last = (request("GET", f"{_repo()}/{int(pr)}/iterations").get("value") or [{}])[-1]
    if not str(last.get("sourceRefCommit", {}).get("commitId", "")).startswith(sha):
        msg = "the PR's head is not the reviewed sha: no status set"
        raise HostError(msg)
    payload = {"state": STATUS_STATES[state], "description": description,
               "context": STATUS_CONTEXT, "iterationId": int(last["id"])}
    return request("POST", f"{_repo()}/{int(pr)}/statuses", payload)
