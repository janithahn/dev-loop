"""E8: `dl ci additive-tests` compares the branch's test files with the base, so a test taken away
by `git rm`, `rm` or `sed -i` (which no edit hook sees) is reported."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_loop.ci import additive, steps

ONE = "def test_a():\n    assert 1\n"
TWO = "def test_b():\n    assert 2\n\n\ndef test_c():\n    assert 3\n"


def git(root: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603, S607 - git, fixed arguments
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=noreply@example.invalid",
         *args], check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A project with two committed test files; returns the base commit."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "dev-loop.yaml").write_text("version: 1\nfix_proves: {tests: ['tests/*']}\n")
    (tmp_path / "tests" / "test_one.py").write_text(ONE)
    (tmp_path / "tests" / "test_two.py").write_text(TWO)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return git(tmp_path, "rev-parse", "HEAD")


def commit(root: Path) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-qm", "work")


def test_a_test_file_removed_with_git_rm_is_reported(
    base: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(tmp_path, "rm", "-q", "tests/test_two.py")
    commit(tmp_path)
    assert steps.main(["additive-tests", "--base", base]) == 1
    out = capsys.readouterr().out
    assert "removes a test and removes an assert" in out
    assert "tests are additive only" in out


def test_an_assert_removed_outside_the_edit_tools_is_reported(
    base: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "tests" / "test_one.py").write_text("def test_a():\n    pass\n")
    commit(tmp_path)
    assert steps.main(["additive-tests", "--base", base]) == 1
    assert "removes an assert;" in capsys.readouterr().out


def test_tests_moved_to_another_file_are_additive(
    base: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(tmp_path, "rm", "-q", "tests/test_two.py")
    (tmp_path / "tests" / "test_one.py").write_text(ONE + "\n\n" + TWO)
    commit(tmp_path)
    assert steps.main(["additive-tests", "--base", base]) == 0
    assert capsys.readouterr().out.strip() == "additive-tests: additive (2 changed test files)"


def test_an_added_test_and_a_change_outside_the_globs_are_additive(
    base: str, tmp_path: Path
) -> None:
    (tmp_path / "tests" / "test_three.py").write_text("def test_d():\n    assert 4\n")
    (tmp_path / "dev-loop.yaml").write_text("version: 1\nfix_proves: {tests: ['tests/*']}\n# x\n")
    commit(tmp_path)
    assert steps.main(["additive-tests", "--base", base]) == 0


def test_without_test_globs_or_a_base_the_step_is_not_run(
    base: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert steps.main(["additive-tests", "--base", "no-such-ref"]) == 1
    assert "not run" in capsys.readouterr().err
    (tmp_path / "dev-loop.yaml").write_text("version: 1\n")
    assert steps.main(["additive-tests", "--base", base]) == 1
    assert "not run" in capsys.readouterr().err


def test_the_step_is_not_one_the_ci_templates_must_run() -> None:
    """A project opts in (`verify.steps` + `verify.prepush`); a red step blocks a deliberate removal."""
    assert "additive-tests" in steps.CHOICES
    assert "additive-tests" not in steps.STEPS


@pytest.mark.parametrize(("rel", "glob", "found"), [
    ("tests/test_x.py", "tests/*", True),
    ("tests/unit/test_x.py", "tests/*", True),  # `*` crosses folders, as in fix_proves.sh
    ("test_x.py", "**/test_*.py", True),  # a leading `**/` also matches at the top level
    ("a/b/test_x.py", "**/test_*.py", True),
    ("src/x.py", "tests/*", False),
    ("Tests/test_x.py", "tests/*", False),
])
def test_a_glob_matches_as_fix_proves_matches_it(rel: str, glob: str, found: bool) -> None:
    assert additive.matches(rel, [glob]) is found
