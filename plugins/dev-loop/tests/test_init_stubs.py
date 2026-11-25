"""dl init writes no unfilled placeholder and no shared git config from a linked worktree (#260).

A new user should never get a `{{placeholder}}` in a written file, a project named after a linked
worktree's folder, or a `core.hooksPath` silently set in the config every worktree shares.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from dev_loop import init
from dev_loop.records import review_classes, review_lessons
from test_neutral import check

URL = "https://org1" + "@dev.azure.com/org1/My%20Proj/_git/repo1"
IDENT = ["-c", "user.name=t", "-c", "user.email=noreply@example.com"]
BARE = re.compile(r"(?<!\$)\{\{")  # a template placeholder, not a `${{ }}` pipeline expression


def run(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    run(tmp_path, "init", "-q")
    run(tmp_path, "remote", "add", "origin", URL)
    return tmp_path


def placeholders(root: Path) -> list[str]:
    """Return the written files that still carry an unfilled {{placeholder}}."""
    bad = []
    for path in root.rglob("*"):
        if not path.is_file() or ".git/" in path.as_posix():
            continue
        if BARE.search(path.read_text(encoding="utf-8", errors="replace")):
            bad.append(path.relative_to(root).as_posix())
    return bad


def test_no_written_file_keeps_a_placeholder(repo: Path) -> None:
    init.init(repo)
    assert placeholders(repo) == []
    assert "{{" not in (repo / "DECISIONS.md").read_text()  # date and owner filled too
    assert "{{" not in (repo / "AGENTS.md").read_text()


def test_filled_agents_md_is_neutral(repo: Path) -> None:
    init.init(repo)
    assert check([repo / "AGENTS.md", repo / "DECISIONS.md"], root=repo) == []


def linked(root: Path, where: Path) -> Path:
    run(root, *IDENT, "commit", "-q", "--allow-empty", "-m", "base")
    run(root, "worktree", "add", "-q", str(where), "HEAD")
    return where


def test_linked_worktree_leaves_shared_config_and_names_the_repo(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work = linked(repo, tmp_path / "linked")
    changed = init.init(work)
    # the shared .git/config is not touched from a linked worktree; init prints the one command
    assert init.git(repo, "config", "core.hooksPath") == ""
    assert "core.hooksPath" not in changed
    err = capsys.readouterr().err
    assert "core.hooksPath" in err and ".githooks" in err
    # the project is named from the repository, never the linked worktree's folder
    assert (work / "AGENTS.md").read_text().startswith("# repo1 engineering guide")
    assert placeholders(work) == []


def test_main_worktree_still_sets_the_hook_path(repo: Path, tmp_path: Path) -> None:
    linked(repo, tmp_path / "linked")  # a sibling worktree exists, but we init the main one
    changed = init.init(repo)
    assert "core.hooksPath" in changed
    assert init.git(repo, "config", "core.hooksPath") == ".githooks"


def test_existing_knowledge_files_survive(repo: Path) -> None:
    for name in ("DECISIONS.md", "REVIEW-PITFALLS.md"):
        (repo / name).write_text("mine\n")
    init.init(repo)
    for name in ("DECISIONS.md", "REVIEW-PITFALLS.md"):
        assert (repo / name).read_text() == "mine\n"


def test_name_falls_back_to_the_main_worktree_folder(tmp_path: Path) -> None:
    main = tmp_path / "myproject"
    main.mkdir()
    run(main, "init", "-q")  # no origin remote: the name is the main worktree's folder
    work = linked(main, tmp_path / "wt")
    init.init(work)
    assert (work / "AGENTS.md").read_text().startswith("# myproject engineering guide")


def test_a_fresh_init_passes_records_current(repo: Path) -> None:
    """The template is the class table `review_classes.CLASS_ROW` reads."""
    init.init(repo)
    assert review_lessons.current(repo) == []
    assert len(review_classes.class_rows(repo)) == 13


def test_the_plugin_repo_pitfalls_pass_records_current() -> None:
    """The plugin repo's own pitfalls file is the same table, so `dl ci` is green on it."""
    root = Path(__file__).resolve().parents[3]
    assert review_lessons.current(root, rule_one=False) == []
