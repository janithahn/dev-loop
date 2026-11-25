"""The block-form tasks.md (`### T1: title` with `- Status:` lines) the plan skill writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_loop.config import route
from dev_loop.records import record


def block(*fields: tuple[str, str, str, str]) -> str:
    """Each field is (id, status, kind, files)."""
    return "\n".join(
        f"### {i}: title\n- Status: {s}\n- Kind: {k}\n- Files: {f}\n" for i, s, k, f in fields
    )


def write(root: Path, text: str) -> Path:
    folder = record.changes_dir(root) / "x"
    folder.mkdir(parents=True)
    (folder / "tasks.md").write_text(text, encoding="utf-8")
    return folder


def test_block_status_decides_done(root: Path) -> None:
    done = ("T1", "done", "backend", "a.py")
    assert record.tasks_done(write(root, block(done, ("T2", "done", "", ""))))
    for status in ("doing", "blocked", "todo"):
        folder = root / "work/log/x"
        (folder / "tasks.md").write_text(block(done, ("T2", status, "", "")), encoding="utf-8")
        assert not record.tasks_done(folder), status


def test_route_reads_block_kind_and_files(root: Path) -> None:
    write(root, block(("T1", "todo", "backend", ""), ("T2", "todo", "", "web/app.js")))
    assert route(root, "x", "T1") == "dev-loop:builder-backend"
    assert route(root, "x", "T2") == "dev-loop:builder-frontend"


def test_route_block_missing_task_raises(root: Path) -> None:
    write(root, block(("T1", "todo", "backend", "")))
    with pytest.raises(ValueError, match="no task T9"):
        route(root, "x", "T9")
    write_no_route = root / "work/log/x/tasks.md"
    write_no_route.write_text(block(("T1", "todo", "", "")), encoding="utf-8")
    with pytest.raises(ValueError, match="no route for T1"):
        route(root, "x", "T1")


def test_checkbox_route_does_not_match_a_longer_id(root: Path) -> None:
    """T1 must not be taken for T10 in the checkbox form."""
    from dev_loop import config
    folder = record.changes_dir(root) / "c"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tasks.md").write_text("- [ ] T10 Kind: frontend\n", encoding="utf-8")
    try:
        config.route(root, "c", "T1")
    except ValueError as error:
        assert "no task T1" in str(error)
    else:
        raise AssertionError("T1 matched T10")
