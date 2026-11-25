"""Guard moves (G5): a gone Guard a `records.guard_moves` key satisfies is not stale.

Fixtures come from test_review_records (a non-default layout). Synthetic data only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_review_records import CHANGES, DOC, FROZEN, PITFALLS, T, lesson, root, write  # noqa: F401, I001
from dev_loop.records.review_lessons import current, moved  # noqa: I001


def with_moves(root: Path, moves: dict[str, str]) -> None:
    config = {
        "paths": {"changes": CHANGES, "frozen": [FROZEN]},
        "knowledge": {"pitfalls": PITFALLS},
        "records": {"guard_moves": moves},
    }
    write(root / "dev-loop.yaml", json.dumps(config))


def plant(changes: Path, name: str, guard: str) -> None:
    write(changes / f"{name}/learnings.md", lesson(T, "Summary: q", "Class: 3", f"Guard: {guard}"))


def setup(root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A planted project and a fake plugin with one test and one hook; returns the changes dir."""
    write(root / PITFALLS, DOC)
    write(root / "checks/guard_a.py", "")  # the DOC's own guard cell must exist
    plugin = tmp_path / "plugin"
    write(plugin / "tests/test_moved.py", "")
    write(plugin / "hooks/moved-guard.sh", "")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    return root / CHANGES


def test_a_moved_guard_to_a_project_path_that_exists_is_satisfied(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    changes = setup(root, monkeypatch, tmp_path)
    write(root / "checks/here.py", "")
    plant(changes, "c1", "old/gone.py")
    with_moves(root, {"old/gone.py": "checks/here.py"})
    assert not any("old/gone.py" in f for f in current(root)), current(root)
    assert ["c1 L1: Guard: old/gone.py moved -> checks/here.py"] == moved(root)


def test_a_moved_guard_to_a_project_path_that_is_absent_is_stale(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    changes = setup(root, monkeypatch, tmp_path)
    plant(changes, "c1", "old/gone.py")
    with_moves(root, {"old/gone.py": "checks/absent.py"})
    assert any("old/gone.py no longer exists" in f for f in current(root))
    assert moved(root) == []  # an unsatisfied move is not reported as moved


def test_a_plugin_name_that_exists_is_satisfied(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    changes = setup(root, monkeypatch, tmp_path)
    plant(changes, "c1", "old/test_x.py")
    plant(changes, "c2", "old/hook.sh")
    with_moves(root, {"old/test_x.py": "plugin:test_moved.py",
                      "old/hook.sh": "plugin:moved-guard.sh"})
    assert current(root) == []
    assert "c1 L1: Guard: old/test_x.py moved -> plugin:test_moved.py" in moved(root)
    assert "c2 L1: Guard: old/hook.sh moved -> plugin:moved-guard.sh" in moved(root)


def test_a_plugin_name_that_does_not_exist_is_stale(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    changes = setup(root, monkeypatch, tmp_path)
    plant(changes, "c1", "old/gone.py")
    with_moves(root, {"old/gone.py": "plugin:test_absent.py"})
    assert any("old/gone.py no longer exists" in f for f in current(root))
    assert moved(root) == []


def test_an_unmapped_missing_path_is_stale(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    changes = setup(root, monkeypatch, tmp_path)
    plant(changes, "c1", "old/gone.py")
    with_moves(root, {"other/path.py": "checks/here.py"})
    assert any("old/gone.py no longer exists" in f for f in current(root))


def test_a_glob_key_matches_and_satisfies(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    changes = setup(root, monkeypatch, tmp_path)
    plant(changes, "c1", "web/one.test.ts")
    plant(changes, "c2", "web/two.test.ts")
    with_moves(root, {"web/*.test.ts": "plugin:moved-guard.sh"})
    assert current(root) == []
    assert len(moved(root)) == 2


def test_no_moves_leaves_a_gone_guard_stale(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    changes = setup(root, monkeypatch, tmp_path)
    plant(changes, "c1", "old/gone.py")  # default guard_moves is {}
    assert any("old/gone.py no longer exists" in f for f in current(root))
    assert moved(root) == []
