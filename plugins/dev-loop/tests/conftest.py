"""Put the plugin's source on the path and give the config tests a temporary project."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_dev_loop_fixtures import Tree  # noqa: E402

# the hook error log of whoever runs the tests (dev_loop.hookenv.errors_file), read before any test
RUNNER_LOG = Path.home() / ".claude" / "dev-loop" / "hook-errors.jsonl"


@pytest.fixture(autouse=True)
def _own_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Each test has its own HOME: a hook it crashes logs there, never in the runner's own log.

    The log feeds `dl doctor` and the session self-test of every live session, so a test's
    planted crash there turns them red. A test that writes it anyway (an environment built
    without HOME) fails here.
    ponytail: a live session's own crash logged while a test runs fails that test; rerun it.
    """
    before = RUNNER_LOG.stat().st_size if RUNNER_LOG.is_file() else 0
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
    yield
    after = RUNNER_LOG.stat().st_size if RUNNER_LOG.is_file() else 0
    assert after == before, "this test wrote the runner's own hook-errors.jsonl: give it a HOME"


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
    return Tree(tmp_path)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project whose `paths.changes` is not the default, set as the project root."""
    (tmp_path / "dev-loop.yaml").write_text(
        "version: 1\npaths:\n  changes: work/log\n  archive: work/log/archive\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    git = ["git", "-c", "user.name=t", "-c", "user.email=noreply@example.com", "-C", str(tmp_path)]
    subprocess.run([*git, "init", "-q"], check=True)  # the done rule asks git about a verify head
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _own_self_test() -> None:
    """`dl doctor` keeps one hook self-test per run of its rows: no test reads another's."""
    from dev_loop import doctor_hooks  # noqa: PLC0415 - after the path line above

    doctor_hooks.self_test_lines.cache_clear()


@pytest.fixture(autouse=True)
def _no_real_credential_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches the machine's own credential helper (it may open a login dialog)."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GCM_INTERACTIVE", "never")


@pytest.fixture(autouse=True)
def _fresh_packages_cache() -> None:
    """`knowledge.packages` is cached per root; each test starts with an empty cache."""
    from dev_loop.records import review_pitfalls

    review_pitfalls._packages.cache_clear()
