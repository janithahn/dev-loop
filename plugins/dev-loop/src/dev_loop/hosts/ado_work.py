"""Azure DevOps work items: WIQL, reads, comments and state, on `ado.request`."""

from __future__ import annotations

from typing import Any

from dev_loop.hosts import ado

PATCH = "application/json-patch+json"
COMMENTS = "?api-version=7.1-preview.4"


def _patch(path: str, fields: dict[str, str], method: str) -> dict[str, Any]:
    ops = [{"op": "add", "path": f"/fields/{key}", "value": value} for key, value in fields.items()]
    return ado.request(method, path, ops, PATCH)


def wiql(query: str) -> list[int]:
    """Return the ids a WIQL query finds."""
    found = ado.request("POST", "/_apis/wit/wiql", {"query": query})
    return [int(item["id"]) for item in found["workItems"]]


def open_tasks(tag: str, worker: str | None) -> list[dict[str, Any]]:
    """Return the open items tagged `tag` and `worker`, or `tag` and assigned to the caller."""
    mine = f"[System.Tags] CONTAINS '{worker}'" if worker else "[System.AssignedTo] = @Me"
    query = (
        "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project"
        f" AND [System.Tags] CONTAINS '{tag}' AND {mine}"
        " AND [System.State] <> 'Done' ORDER BY [System.Id]"
    )
    return get_items(wiql(query))


def get_items(ids: list[int]) -> list[dict[str, Any]]:
    """Return the work items with these ids."""
    if not ids:
        return []
    return ado.request("GET", f"/_apis/wit/workitems?ids={','.join(map(str, ids))}")["value"]


def get_item(item_id: int) -> dict[str, Any]:
    """Return one work item."""
    return ado.request("GET", f"/_apis/wit/workitems/{int(item_id)}")


def comments(item_id: int) -> list[dict[str, Any]]:
    """Return a work item's comments, oldest first."""
    found = ado.request("GET", f"/_apis/wit/workItems/{int(item_id)}/comments{COMMENTS}")
    return sorted(found["comments"], key=lambda note: note["id"])


def add_comment(item_id: int, html_text: str) -> None:
    """Add a comment (already escaped HTML)."""
    ado.request("POST", f"/_apis/wit/workItems/{int(item_id)}/comments{COMMENTS}",
                {"text": html_text})


def set_state(item_id: int, state: str) -> None:
    """Move a work item to a state."""
    _patch(f"/_apis/wit/workitems/{int(item_id)}", {"System.State": state}, "PATCH")


def add_tag(item_id: int, tag: str) -> None:
    """Add a tag to a work item, keeping the ones it already carries."""
    tags = get_item(item_id)["fields"].get("System.Tags", "")
    if tag in tags:
        return
    value = f"{tags}; {tag}" if tags else tag
    _patch(f"/_apis/wit/workitems/{int(item_id)}", {"System.Tags": value}, "PATCH")


def create(title: str, description: str, tags: str) -> int:
    """Create a Task and return its id."""
    fields = {"System.Title": title, "System.Description": description, "System.Tags": tags}
    return int(_patch("/_apis/wit/workitems/$Task", fields, "POST")["id"])
