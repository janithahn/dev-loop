"""GitHub Issues as the task board: the calls agent_tasks makes on ado_work, in the same shapes.

An open issue labelled `tasks.tag` is a task: To Do, or Doing while it carries the `doing`
label; a closed issue is Done. Comments are issue comments. Items and comments come back under
the work-item field names agent_tasks reads, as github.pr_get does for pull requests.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from dev_loop.hosts import HostError, github

DOING = "doing"
NOT_FOUND = 404
STATES = ("To Do", "Doing", "Done")


def _issue(number: int, rest: str = "") -> str:
    return github._path(f"issues/{int(number)}{rest}")  # noqa: SLF001 - one host, two modules


def _call(method: str, path: str, body: object = None) -> Any:  # noqa: ANN401 - parsed JSON
    return github._call(method, path, body)  # noqa: SLF001


def _item(issue: dict[str, Any]) -> dict[str, Any]:
    labels = [label["name"] for label in issue.get("labels", [])]
    state = "Done" if issue["state"] == "closed" else "Doing" if DOING in labels else "To Do"
    return {"id": issue["number"], "fields": {
        "System.CreatedBy": {"uniqueName": issue["user"]["login"]}, "System.State": state,
        "System.Title": issue["title"], "System.Tags": "; ".join(labels),
        "System.Description": issue.get("body") or ""}}


def open_tasks(tag: str, worker: str | None) -> list[dict[str, Any]]:
    """Return the open issues labelled `tag` and `worker`, or `tag` and assigned to the caller."""
    labels = urllib.parse.quote(",".join([tag, worker] if worker else [tag]), safe="")
    mine = "" if worker else f"&assignee={_call('GET', 'user')['login']}"
    query = f"issues?state=open&labels={labels}{mine}&sort=created&direction=asc&per_page=100"
    found = _call("GET", github._path(query))  # noqa: SLF001
    return [_item(issue) for issue in found if "pull_request" not in issue]


def get_item(item_id: int) -> dict[str, Any]:
    """Return one issue as a work item."""
    return _item(_call("GET", _issue(item_id)))


def comments(item_id: int) -> list[dict[str, Any]]:
    """Return an issue's comments, oldest first."""
    found = _call("GET", _issue(item_id, "/comments?per_page=100"))
    notes = [{"id": note["id"], "createdBy": {"uniqueName": note["user"]["login"]},
              "text": note["body"], "createdDate": note["created_at"]} for note in found]
    return sorted(notes, key=lambda note: note["id"])


def add_comment(item_id: int, html_text: str) -> None:
    """Add an issue comment (already escaped HTML, which GitHub renders)."""
    _call("POST", _issue(item_id, "/comments"), {"body": html_text})


def set_state(item_id: int, state: str) -> None:
    """Move an issue: To Do and Doing keep it open (Doing labelled), Done closes it."""
    if state not in STATES:
        msg = f"task state {state!r} is not one of {', '.join(STATES)}"
        raise HostError(msg)
    _call("PATCH", _issue(item_id), {"state": "closed" if state == "Done" else "open"})
    if state == "Doing":
        _call("POST", _issue(item_id, "/labels"), {"labels": [DOING]})
    if state == "To Do":
        try:
            _call("DELETE", _issue(item_id, f"/labels/{DOING}"))
        except HostError as error:
            if error.status != NOT_FOUND:  # the label was not there: already To Do
                raise


def add_tag(item_id: int, tag: str) -> None:
    """Add a label to an issue (idempotent: GitHub keeps the label set)."""
    _call("POST", _issue(item_id, "/labels"), {"labels": [tag]})


def create(title: str, description: str, tags: str) -> int:
    """Open an issue labelled with the `;`-separated tags; return its number."""
    labels = [name.strip() for name in tags.split(";") if name.strip()]
    payload = {"title": title, "body": description, "labels": labels}
    return int(_call("POST", github._path("issues"), payload)["number"])  # noqa: SLF001
