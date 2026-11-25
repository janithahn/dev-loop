"""`dl host review-run`: the proof a real review round ran, which the local lander asks for."""

from __future__ import annotations

from types import ModuleType
from typing import Any


def record(host: ModuleType, body: dict[str, Any]) -> dict[str, Any]:
    """Append a review-run record (pr, head, base, rc) to the local host's records, committed.

    Another host has its own status for the round: nothing is written there.
    """
    if not hasattr(host, "folder_of"):
        return {}
    pr = body["pr"]
    host._on(host.pr_get(pr)["sourceRefName"].removeprefix("refs/heads/"))  # noqa: SLF001
    folder = host.folder_of(pr)
    item = {"kind": "review-run", "pr": pr, "head": body["head"], "base": body["base"],
            "rc": int(body["rc"])}
    host._append(folder / host.HOST, item)  # noqa: SLF001
    host._commit(folder / host.HOST, "review-run")  # noqa: SLF001
    return item
