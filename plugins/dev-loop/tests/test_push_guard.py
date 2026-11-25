"""G4: the push guard refuses a push to a protected branch and any --no-verify (PreToolUse Bash).

The hook runs from outside the repository: the project is CLAUDE_PROJECT_DIR, the plugin is
CLAUDE_PLUGIN_ROOT, and the protected branch names are read through `dl config get branches`.
A refspec's destination, a bare push on a protected branch, --all, --mirror and --no-verify are
denied; a feature-branch push and quoted text that only looks like a push stay silent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_neutral import check

PLUGIN = Path(__file__).resolve().parents[1]
GUARD = PLUGIN / "hooks" / "push-guard.py"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@noreply",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@noreply"}

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is absent")


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,  # noqa: S603, S607
                   env={**os.environ, **GIT_ENV})


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "commit", "-q", "--allow-empty", "-m", "init")
    return root


def run(command: str, project: Path,
        *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **GIT_ENV, "CLAUDE_PROJECT_DIR": str(project),
           "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
    env.pop("CLAUDE_PLUGIN_DATA", None)
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd or project)}
    argv = [sys.executable, "-S", str(GUARD)]
    return subprocess.run(argv, input=json.dumps(payload), capture_output=True,  # noqa: S603
                          text=True, check=False, env=env)


def denied(result: subprocess.CompletedProcess[str], reason: str) -> None:
    assert result.returncode == 0, result.stderr
    decision = json.loads(result.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny", result.stdout
    assert reason in decision["permissionDecisionReason"]


def allowed(result: subprocess.CompletedProcess[str]) -> None:
    assert (result.returncode, result.stdout.strip()) == (0, ""), result


DENIED = [
    ("git push origin main", "main is protected"),
    ("git push origin HEAD:main", "main is protected"),
    ("git push -f origin main", "main is protected"),
    ("git push origin +main", "main is protected"),
    ("git push origin :main", "main is protected"),
    ("git push origin feat:refs/heads/main", "main is protected"),
    ("cd x && git push origin main", "main is protected"),
    ("git -C repo push origin main", "main is protected"),
    ("GIT_SSH_COMMAND=ssh git push origin main", "main is protected"),
    ("git push --no-verify origin feat", "--no-verify"),
    ("git commit --no-verify -m x", "--no-verify"),
    ("git push --all origin", "protected"),
    ("git push --mirror origin", "protected"),
]

ALLOWED = [
    "git push origin feat/x",
    "git push -u origin feat/x",
    "git push origin main:feat/x",
    'echo "git push origin main"',
    'grep "git push" file',
    "git status",
    "ls && echo done",
]


@pytest.mark.parametrize(("command", "reason"), DENIED)
def test_a_push_to_a_protected_branch_is_denied(project: Path, command: str, reason: str) -> None:
    denied(run(command, project), reason)


@pytest.mark.parametrize("command", ALLOWED)
def test_a_safe_command_stays_silent(project: Path, command: str) -> None:
    allowed(run(command, project))


def test_a_bare_push_on_a_protected_branch_is_denied(project: Path) -> None:
    denied(run("git push", project, cwd=project), "main is protected")


def test_a_bare_push_on_a_feature_branch_is_allowed(project: Path) -> None:
    git(project, "checkout", "-q", "-b", "feature/y")
    allowed(run("git push", project, cwd=project))


def test_the_configured_protected_list_is_honoured(project: Path) -> None:
    config = "branches: {integration: trunk, release: stable, protected: [frozen, release/*]}\n"
    (project / "dev-loop.yaml").write_text(config, encoding="utf-8")
    denied(run("git push origin release/1.0", project), "release/1.0 is protected")
    denied(run("git push origin frozen", project), "frozen is protected")
    denied(run("git push origin stable", project), "stable is protected")
    denied(run("git push origin :trunk", project), "trunk is protected")
    allowed(run("git push origin main", project))  # main is no longer the integration branch


FORCED = [
    ("git push --force origin dev-loop/c1", "--force-with-lease"),
    ("git push -f origin dev-loop/c1", "--force-with-lease"),
    ("git push -fu origin dev-loop/c1", "--force-with-lease"),
    ("git push origin +dev-loop/c1", "--force-with-lease"),
    ("git push --force", "--force-with-lease"),
    ("git push --force-with-lease origin feat/x", "change branch"),
    ("git push --force-with-lease=feat/x:abc origin +feat/x", "change branch"),
    ("git push --force-with-lease origin dev-loop/other", "current branch"),
    ("git push --force-with-lease origin dev-loop/c1:dev-loop/other", "current branch"),
]

LEASED = [
    "git push -u origin dev-loop/c1",
    "git push origin dev-loop/c1",
    "git push --force-with-lease origin dev-loop/c1",
    "git push --force-with-lease origin HEAD",
    "git push --force-with-lease",
    "git push --force-with-lease origin +dev-loop/c1",
    "git push --force-with-lease --force-if-includes origin dev-loop/c1",
]


@pytest.fixture
def on_change_branch(project: Path) -> Path:
    git(project, "checkout", "-q", "-b", "dev-loop/c1")
    return project


@pytest.mark.parametrize(("command", "reason"), FORCED)
def test_a_force_push_off_the_change_branch_is_denied(
        on_change_branch: Path, command: str, reason: str) -> None:
    denied(run(command, on_change_branch, cwd=on_change_branch), reason)


@pytest.mark.parametrize("command", LEASED)
def test_a_push_of_the_change_branch_stays_silent(on_change_branch: Path, command: str) -> None:
    allowed(run(command, on_change_branch, cwd=on_change_branch))


BYPASSES = [
    "git -c core.hooksPath=/dev/null commit -m x",
    "git -c core.hookspath=x push origin feat/x",
    "git -ccore.hooksPath=x commit -m x",
    "git config core.hooksPath /dev/null",
    "git config --local core.hooksPath .nohooks",
    "HUSKY=0 git commit -m x",
    "cd x && HUSKY=0 git push origin feat/x",
]

HOOK_OK = [
    "git -c user.name=x commit -m x",
    "git config user.name x",
    "git config --get core.hooksPath",
    "git commit -m 'document core.hooksPath'",
    "HUSKY=1 git commit -m x",
]


@pytest.mark.parametrize("command", BYPASSES)
def test_a_git_hook_bypass_is_denied(project: Path, command: str) -> None:
    denied(run(command, project), "hooks")


@pytest.mark.parametrize("command", HOOK_OK)
def test_a_command_that_leaves_the_hooks_alone_stays_silent(project: Path, command: str) -> None:
    allowed(run(command, project))



def test_a_lease_push_of_the_current_branch_needs_no_prefix(project: Path) -> None:
    """C8: the review loop folds fixes on a PR branch outside branches.change_prefix."""
    git(project, "checkout", "-q", "-b", "feat/pr-1")
    for command in ("git push --force-with-lease", "git push --force-with-lease origin feat/pr-1",
                    "git push --force-with-lease origin HEAD"):
        allowed(run(command, project, cwd=project))
    for command in ("git push --force-with-lease origin feat/other",
                    "git push --force-with-lease origin dev-loop/c1"):
        denied(run(command, project, cwd=project), "current branch")
    denied(run("git push --force origin feat/pr-1", project, cwd=project), "--force-with-lease")
    git(project, "checkout", "-q", "main")
    denied(run("git push --force-with-lease", project, cwd=project), "main is protected")


def test_an_abbreviated_lease_flag_is_a_lease_flag(on_change_branch: Path) -> None:
    """C10: git takes --force-with-leas; so does the guard, with the same branch rule."""
    here = on_change_branch
    denied(run("git push --force-with-leas origin feat/x", here, cwd=here), "current branch")
    denied(run("git push --force-w origin dev-loop/other", here, cwd=here), "current branch")
    denied(run("git push --forc origin dev-loop/c1", here, cwd=here), "--force-with-lease")
    allowed(run("git push --force-with-leas origin dev-loop/c1", here, cwd=here))
    allowed(run("git push --force-with-leas origin +dev-loop/c1", here, cwd=here))
    allowed(run("git push --follow-tags origin dev-loop/c1", here, cwd=here))


def test_at_means_head_in_a_refspec(project: Path) -> None:
    """C11: `@` is HEAD, so it names the current branch."""
    denied(run("git push origin @", project, cwd=project), "main is protected")
    denied(run("git push origin @:main", project, cwd=project), "main is protected")
    git(project, "checkout", "-q", "-b", "dev-loop/c1")
    allowed(run("git push origin @", project, cwd=project))
    allowed(run("git push --force-with-lease origin @", project, cwd=project))
    allowed(run("git push origin @:feat/x", project, cwd=project))


def test_an_unparsable_segment_is_allowed_and_said_once(project: Path) -> None:
    """C17: one behaviour for every unparsable piece; the parsable pieces are still judged."""
    for command in ('git status "a', "az repos pr list 'a; az repos pr show \"b"):
        result = run(command, project)
        allowed(result)
        assert result.stderr.count("unparsable segment, allowed") == 1, result.stderr
    heredoc = "cat > note.txt <<EOF\nit's late\nEOF\ngit push origin main"
    denied(run(heredoc, project), "main is protected")


def test_the_file_is_neutral() -> None:
    assert check([GUARD, GUARD.parent / "push_guard" / "pr_rules.py",
                  GUARD.parent / "push_guard" / "shell_words.py", Path(__file__)]) == []
