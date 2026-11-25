"""`dl propose <slug>`: a change folder from the templates and a worktree on its own branch."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_neutral  # noqa: E402
from dev_loop import propose  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[1]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", str(origin), str(work))
    (work / "dev-loop.yaml").write_text("version: 1\npaths:\n  changes: log\n", encoding="utf-8")
    git(work, "add", ".")
    git(work, "-c", "user.name=t", "-c", "user.email=noreply@example.org", "commit", "-m", "x")
    git(work, "push", "origin", "HEAD:main")
    git(work, "fetch", "origin")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(work))
    return work


def test_creates_folder_status_and_worktree(repo: Path, capsys: pytest.CaptureFixture) -> None:
    assert propose.main(["add-search", "--title", "Add search"]) == 0
    tree = Path(capsys.readouterr().out.strip())
    assert git(tree, "branch", "--show-current") == "dev-loop/add-search"
    folder = tree / "log" / "add-search"
    assert "# Proposal: Add search" in (folder / "proposal.md").read_text(encoding="utf-8")
    status = (folder / "status.md").read_text(encoding="utf-8")
    assert "**Change:** add-search" in status and "{{" not in status
    assert not (repo / "log" / "add-search").exists()


def test_status_has_no_progress_table(repo: Path, capsys: pytest.CaptureFixture) -> None:
    """The records hold the state; a hand-kept Progress table only drifts from them."""
    assert propose.main(["add-search", "--title", "Add search"]) == 0
    tree = Path(capsys.readouterr().out.strip())
    status = (tree / "log" / "add-search" / "status.md").read_text(encoding="utf-8")
    assert "## Progress" not in status and "| Proposal |" not in status and "{{" not in status
    assert "**Change:** add-search" in status and "## Review rounds" in status


def test_skills_tend_no_progress_rows() -> None:
    def words(skill: str) -> str:
        return " ".join((PLUGIN / "skills" / skill / "SKILL.md").read_text("utf-8").split())

    propose_skill, plan_skill = words("propose"), words("plan")
    assert "Proposal row" not in propose_skill
    assert "Spec, Design and Tasks rows" not in plan_skill
    assert "Never set the Status to Approved yourself" in propose_skill
    assert "`Approved: <slug>`" in propose_skill


@pytest.mark.parametrize("slug", ["Bad Slug", "../x", "-a", ""])
def test_bad_slug_refused(repo: Path, slug: str) -> None:
    assert propose.main([slug]) == 2


def test_existing_branch_refused(repo: Path, capsys: pytest.CaptureFixture) -> None:
    assert propose.main(["one"]) == 0
    assert propose.main(["one"]) == 1
    assert "already" in capsys.readouterr().err


def test_on_own_branch_writes_in_place(repo: Path, capsys: pytest.CaptureFixture) -> None:
    git(repo, "checkout", "-b", "dev-loop/c")
    before = git(repo, "worktree", "list")
    git(repo, "remote", "set-url", "origin", str(repo.parent / "missing.git"))
    assert propose.main(["c"]) == 0
    assert capsys.readouterr().out.strip() == str(repo)
    assert (repo / "log" / "c" / "proposal.md").is_file()
    assert (repo / "log" / "c" / "status.md").is_file()
    assert git(repo, "worktree", "list") == before
    assert propose.main(["c"]) == 1
    assert "dl propose: log/c exists; nothing written" in capsys.readouterr().err


def test_files_are_neutral() -> None:
    files = ["src/dev_loop/propose.py", "skills/propose/SKILL.md", "skills/plan/SKILL.md",
             "tests/test_propose.py"]
    assert test_neutral.check([PLUGIN / f for f in files]) == []


def test_fetches_the_integration_branch_first(repo: Path, capsys: pytest.CaptureFixture) -> None:
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    assert propose.main(["fresh"]) == 0
    tree = Path(capsys.readouterr().out.strip())
    assert git(tree, "branch", "--show-current") == "dev-loop/fresh"


def test_failed_fetch_exits_1_with_one_line(repo: Path, capsys: pytest.CaptureFixture) -> None:
    git(repo, "remote", "set-url", "origin", str(repo.parent / "missing.git"))
    assert propose.main(["offline"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("dl propose: ") and err.count("\n") == 1


def test_continues_an_existing_change_folder(repo: Path, capsys: pytest.CaptureFixture) -> None:
    folder = repo / "log" / "old"
    folder.mkdir(parents=True)
    (folder / "proposal.md").write_text("mine", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=noreply@example.org", "commit", "-m", "p")
    git(repo, "push", "origin", "HEAD:main")
    assert propose.main(["old"]) == 0
    out, err = capsys.readouterr()
    tree = Path(out.strip())
    assert git(tree, "branch", "--show-current") == "dev-loop/old"
    assert (tree / "log" / "old" / "proposal.md").read_text(encoding="utf-8") == "mine"
    assert not (tree / "log" / "old" / "status.md").exists()
    assert len(err.strip().splitlines()) == 1 and "continues an existing change" in err


def test_carries_local_files_into_the_worktree(repo: Path, capsys: pytest.CaptureFixture) -> None:
    (repo / "local.cfg").write_text("a", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=noreply@example.org", "commit", "-m", "c")
    git(repo, "push", "origin", "HEAD:main")
    git(repo, "update-index", "--skip-worktree", "local.cfg")
    (repo / "local.cfg").write_text("mine", encoding="utf-8")
    (repo / ".git" / "info" / "exclude").write_text("secret.env", encoding="utf-8")
    (repo / "secret.env").write_text("s", encoding="utf-8")
    assert propose.main(["carry"]) == 0
    out, err = capsys.readouterr()
    tree = Path(out.strip())
    assert (tree / "local.cfg").read_text(encoding="utf-8") == "mine"
    assert (tree / "secret.env").read_text(encoding="utf-8") == "s"
    assert git(tree, "ls-files", "-v", "local.cfg").startswith("S ")
    assert "local.cfg" in err and "secret.env" in err


def test_resume_reuses_the_worktree(repo: Path, capsys: pytest.CaptureFixture) -> None:
    assert propose.main(["again"]) == 0
    tree = Path(capsys.readouterr().out.strip())
    (tree / "log" / "again" / "proposal.md").write_text("marker", encoding="utf-8")
    assert propose.main(["again", "--resume"]) == 0
    assert Path(capsys.readouterr().out.strip()) == tree
    assert (tree / "log" / "again" / "proposal.md").read_text(encoding="utf-8") == "marker"


def test_resume_without_a_worktree_adds_one(repo: Path, capsys: pytest.CaptureFixture) -> None:
    assert propose.main(["gone"]) == 0
    git(repo, "worktree", "remove", "--force", capsys.readouterr().out.strip())
    assert propose.main(["gone", "--resume"]) == 0
    tree = Path(capsys.readouterr().out.strip())
    assert git(tree, "branch", "--show-current") == "dev-loop/gone"
    assert (tree / "log" / "gone" / "proposal.md").is_file()


def test_refusal_names_the_worktree_and_resume(repo: Path, capsys: pytest.CaptureFixture) -> None:
    assert propose.main(["twice"]) == 0
    tree = capsys.readouterr().out.strip()
    assert propose.main(["twice"]) == 1
    err = capsys.readouterr().err
    assert "already" in err and tree in err and "--resume" in err and err.count("\n") == 1


def test_resume_on_a_new_slug_is_a_plain_propose(
    repo: Path, capsys: pytest.CaptureFixture
) -> None:
    assert propose.main(["new", "--resume", "--title", "New"]) == 0
    tree = Path(capsys.readouterr().out.strip())
    assert git(tree, "branch", "--show-current") == "dev-loop/new"
    assert "# Proposal: New" in (tree / "log" / "new" / "proposal.md").read_text(encoding="utf-8")


def test_status_after_propose_has_no_records_warning(
    repo: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dev_loop import status  # noqa: PLC0415

    assert propose.main(["fresh"]) == 0
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", capsys.readouterr().out.strip())
    assert status.main(["fresh"]) == 0
    out = capsys.readouterr().out
    assert "fresh" in out and "no records.jsonl" not in out


def test_resume_after_the_tree_was_deleted(repo: Path, capsys: pytest.CaptureFixture) -> None:
    import shutil  # noqa: PLC0415

    assert propose.main(["lost"]) == 0
    shutil.rmtree(capsys.readouterr().out.strip())
    assert propose.main(["lost", "--resume"]) == 0
    tree = Path(capsys.readouterr().out.strip())
    assert git(tree, "rev-parse", "--show-toplevel") == str(tree)
    assert git(tree, "branch", "--show-current") == "dev-loop/lost"
    assert (tree / "log" / "lost" / "proposal.md").is_file()


def test_refusal_after_the_tree_was_deleted(repo: Path, capsys: pytest.CaptureFixture) -> None:
    import shutil  # noqa: PLC0415

    assert propose.main(["lost"]) == 0
    shutil.rmtree(capsys.readouterr().out.strip())
    assert propose.main(["lost"]) == 1
    assert "(no worktree)" in capsys.readouterr().err


def test_other_unseen_worktrees_are_not_pruned(repo: Path, capsys: pytest.CaptureFixture) -> None:
    import shutil  # noqa: PLC0415

    assert propose.main(["away"]) == 0
    away = Path(capsys.readouterr().out.strip())
    assert propose.main(["lost"]) == 0
    shutil.rmtree(capsys.readouterr().out.strip())
    aside = away.with_name("aside")
    away.rename(aside)  # a checkout not visible now: unmounted, or seen from another OS
    assert propose.main(["other"]) == 0
    assert propose.main(["lost", "--resume"]) == 0
    aside.rename(away)
    assert str(away) in git(repo, "worktree", "list")
    assert git(away, "branch", "--show-current") == "dev-loop/away"
