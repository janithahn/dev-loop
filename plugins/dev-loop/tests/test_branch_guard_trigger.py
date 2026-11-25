"""The branch guard and the PR-loop trigger.

Each hook finds the project from CLAUDE_PROJECT_DIR and the plugin from CLAUDE_PLUGIN_ROOT, so it
runs from outside the repository. Two planted false fires stay silent: a command whose output holds
a create string, and a new folder in a feature worktree whose root checkout is on the integration
branch. A real write on a protected branch is refused with exit 2.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_neutral import check

PLUGIN = Path(__file__).resolve().parents[1]
GUARD = PLUGIN / "hooks" / "branch-guard.sh"
TRIGGER = PLUGIN / "hooks" / "pr-loop-trigger.sh"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@noreply",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@noreply"}

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is absent")


def run(hook: Path, payload: dict[str, object], project: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **GIT_ENV, "CLAUDE_PROJECT_DIR": str(project),
           "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
    env.pop("CLAUDE_PLUGIN_DATA", None)
    return subprocess.run(["sh", str(hook)], input=json.dumps(payload), capture_output=True,  # noqa: S603, S607
                          text=True, check=False, env=env, cwd=project.parent)


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


def write(path: Path) -> dict[str, object]:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path)}}


def bash(command: str, output: str = "") -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command},
            "tool_response": {"stdout": output}}


def silent(result: subprocess.CompletedProcess[str]) -> None:
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def refused(result: subprocess.CompletedProcess[str], text: str) -> None:
    assert result.returncode == 2, result
    assert text in result.stderr


def test_a_write_on_the_integration_branch_is_refused(project: Path) -> None:
    refused(run(GUARD, write(project / "a.py"), project), "protected branch 'main'")


def test_a_new_folder_in_a_feature_worktree_is_judged_by_the_worktree(project: Path) -> None:
    tree = project.parent / "wt"
    git(project, "worktree", "add", "-q", "-b", "feature/x", str(tree))
    silent(run(GUARD, write(tree / "new" / "deeper" / "a.py"), project))


@pytest.mark.parametrize("branch", ["trunk", "stable", "frozen", "hold"])
def test_every_configured_branch_is_protected(project: Path, branch: str) -> None:
    config = "branches: {integration: trunk, release: stable, protected: [frozen, hold]}\n"
    (project / "dev-loop.yaml").write_text(config, encoding="utf-8")
    git(project, "checkout", "-q", "-b", branch)
    refused(run(GUARD, write(project / "a.py"), project), f"protected branch '{branch}'")


def test_a_feature_branch_is_allowed(project: Path) -> None:
    git(project, "checkout", "-q", "-b", "feature/y")
    silent(run(GUARD, write(project / "a.py"), project))


def test_a_create_string_in_the_output_stays_silent(project: Path) -> None:
    payload = bash("grep -rn create hooks/", "dl pr\ndl host pr-create\n/dev-loop:pr\n")
    silent(run(TRIGGER, payload, project))


@pytest.mark.parametrize("command", ["git status", "dl prepush", "grep -n 'dl pr' x.md"])
def test_other_commands_stay_silent(project: Path, command: str) -> None:
    silent(run(TRIGGER, bash(command), project))


@pytest.mark.parametrize(
    "command", ["dl pr my-change", "dl host pr-create --json < body.json", "/dev-loop:pr x"]
)
def test_a_pr_create_nudges_the_review_loop(project: Path, command: str) -> None:
    result = run(TRIGGER, bash(command), project)
    refused(result, "[pr-loop]")
    assert result.stdout == ""


@pytest.mark.parametrize("command", [
    "az repos pr create --title x --source-branch b",
    "gh pr create --fill",
    "curl -s -X POST -H 'Content-Type: application/json' -d @b.json \"$API/pullrequests?v=7\"",
    "python3 -c \"import requests; requests.post(url + '/pullrequests', json=b)\"",
])
def test_other_pr_creates_nudge_the_review_loop(project: Path, command: str) -> None:
    refused(run(TRIGGER, bash(command), project), "[pr-loop]")


@pytest.mark.parametrize("command", [
    "gh pr view 5", "az repos pr list", "curl -s \"$API/pullrequests?v=7\"",
    "grep -rn 'gh pr create' docs/",
])
def test_reading_a_pr_stays_silent(project: Path, command: str) -> None:
    silent(run(TRIGGER, bash(command, "gh pr create\naz repos pr create\n"), project))


def test_an_open_pr_in_the_response_stays_silent(project: Path) -> None:
    payload = bash("dl pr c", "open PR b -> main: 5c\n")
    silent(run(TRIGGER, payload, project))


@pytest.mark.parametrize("command", ["dl pr --help", "dl pr -h"])
def test_a_help_call_stays_silent(project: Path, command: str) -> None:
    silent(run(TRIGGER, bash(command), project))


def test_a_refused_pr_stays_silent(project: Path) -> None:
    silent(run(TRIGGER, bash("dl pr c", "dl pr: not approved: c"), project))


def test_a_bare_pr_still_nudges(project: Path) -> None:
    refused(run(TRIGGER, bash("dl pr my-change"), project), "[pr-loop]")


@pytest.mark.parametrize(
    "command",
    ['dl record c skills skills="dev-loop:pr (dl pr), dev-loop:review"',
     "git commit -m 'x; dl pr c'", 'git commit -m "first line\ndl pr c"'],
)
def test_a_create_inside_a_quoted_argument_stays_silent(project: Path, command: str) -> None:
    silent(run(TRIGGER, bash(command), project))


def test_a_create_after_a_quoted_argument_still_nudges(project: Path) -> None:
    refused(run(TRIGGER, bash('git commit -m "records" && dl pr c'), project), "[pr-loop]")


def test_the_files_are_neutral() -> None:
    assert check([GUARD, TRIGGER, Path(__file__)]) == []


@pytest.mark.parametrize("command", [
    "curl -s -X POST -d @c.json \"$API/pullrequests/12/threads?v=7\"",
    "python3 -c \"import requests; requests.post(url + '/pullrequests/5/threads', json=b)\"",
    "curl -s -X PATCH -d @b.json \"$API/pullrequests/12?v=7\"",
    "curl -s -X PATCH -d @b.json \"$API/pullrequests?v=7\"",
    "curl -s -X PUT -d '{\"vote\": 10}' \"$API/pullrequests/12/reviewers/abc?v=7\"",
    "curl -s -G --data-urlencode 'searchCriteria.status=active' \"$API/pullrequests?v=7\"",
])
def test_a_write_to_one_pr_or_a_read_with_data_stays_silent(project: Path, command: str) -> None:
    silent(run(TRIGGER, bash(command), project))


@pytest.mark.parametrize("command", [
    "set -e\ncurl -s -X POST -d @b.json \"$API/pullrequests?v=7\"",
    "python3 -c \"import urllib.request as u; "
    "u.urlopen(u.Request(url + '/pullrequests?v=7', data=b, method='POST'))\"",
    "gh -R o/r pr create --fill",
    "wsl.exe -d Linux -- bash -c 'cd repo && az repos pr create --title x'",
    "ls -h; gh pr create --fill",
    "gh pr create --title \"add -h flag\"",
])
def test_a_create_in_a_longer_command_still_nudges(project: Path, command: str) -> None:
    refused(run(TRIGGER, bash(command), project), "[pr-loop]")


@pytest.mark.parametrize("command", ["gh pr create -h; ls", "ls && dl pr --help"])
def test_a_help_call_in_a_longer_command_stays_silent(project: Path, command: str) -> None:
    silent(run(TRIGGER, bash(command), project))
