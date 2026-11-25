"""A change is approved when its records.jsonl holds an `approval` record.

Only the UserPromptSubmit hook (hooks/approval.py) writes one, on a user message
`Approved: <slug>`; `dl record` refuses the kind. `dl tasklist set <c> <T> doing` and `dl pr`
refuse a change without it.
"""

from __future__ import annotations

import json
from pathlib import Path


def refusal(slug: str) -> str:
    """The one sentence every refusal of an unapproved change says."""
    say = f'"Approved: {slug}"'
    return (f"not approved: {slug}: approval is a user message that is exactly {say}; "
            f"in a headless run it is a turn of its own (--resume <session> {say})")


def _records(folder: Path) -> list[dict]:
    """The sidecar's records in write order; a bad JSON line is skipped."""
    path = folder / "records.jsonl"
    found = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            found.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return [one for one in found if isinstance(one, dict)]


def approved(folder: Path) -> bool:
    """True when the change folder's sidecar holds an approval record."""
    return any(one.get("kind") == "approval" for one in _records(folder))


def uncited(folder: Path) -> list[str]:
    """Lesson ids the last plan-time recall showed that design.md does not name.

    Plan time is before the approval record; a recall after it is the build's and does not count.
    """
    found = _records(folder)
    kinds = [one.get("kind") for one in found]
    if "approval" not in kinds:
        return []
    before = found[: kinds.index("approval")]
    recalls = [one for one in before if one.get("kind") == "recall"]
    if not recalls:
        return []
    design = folder / "design.md"
    text = design.read_text(encoding="utf-8") if design.is_file() else ""
    return [lesson for lesson in str(recalls[-1].get("shown", "")).split() if lesson not in text]
