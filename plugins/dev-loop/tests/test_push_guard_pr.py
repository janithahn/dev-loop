"""G4: the push guard on PR decisions and review findings (split from test_push_guard.py)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from test_push_guard import allowed, denied, project, run  # noqa: F401

# ruff: noqa: F811 - the imported project fixture is each test's argument
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is absent")


PR_DENIED = [
    "az repos pr update --id 7 --auto-complete true",
    "az repos pr update --id 7 --status completed",
    "az repos pr update --id 7 --status=completed",
    "az repos pr set-vote --id 7 --vote approve",
    "cd x && az repos pr set-vote --id 7 --vote 10",
    "gh pr merge 7 --squash",
    "gh pr review 7 --approve",
    "curl -X PUT https://h/o/p/_apis/git/repositories/r/pullrequests/7/reviewers/me -d '{}'",
    "wget --method=POST https://h/_apis/git/repositories/r/pullrequests/7/reviewers/me",
    "curl -X PATCH https://h/_apis/git/repositories/r/pullrequests/7 -d '{\"autoCompleteSetBy\": 1}'",
    "python3 -c \"import requests; requests.patch(u + '/pullrequests/7', "
    "json={'status': 'completed'})\"",
    "python3 -c \"requests.patch('https://h/pullrequests/7', json={\\\"status\\\": \\\"completed\\\"})\"",
]

PR_ALLOWED = [
    "az repos pr create --title x",
    "az repos pr list",
    "az repos pr update --id 7 --title new",
    "gh pr create --fill",
    "gh pr review 7 --comment -b ok",
    "gh pr view 7",
    "curl -s https://h/_apis/git/repositories/r/pullrequests/7",
    "dl land 7",
    "git commit -m 'refuse gh pr merge'",
    "echo az repos pr set-vote",
]


@pytest.mark.parametrize("command", PR_DENIED)
def test_an_agent_cannot_approve_vote_or_complete_a_pr(project: Path, command: str) -> None:
    denied(run(command, project), "lander or a person")


@pytest.mark.parametrize("command", PR_ALLOWED)
def test_pr_commands_that_do_not_decide_stay_silent(project: Path, command: str) -> None:
    allowed(run(command, project))


VOTE = "https://h/_apis/git/repositories/r/pullrequests/7/reviewers/me"
PR_URL = "https://h/_apis/git/repositories/r/pullrequests/7"
PR, HOOKS = "lander or a person", "hooks"

# Review findings: each denied command has an allowed twin below (ordinary work must pass).
FOUND_DENIED = [
    # C4: the REST check runs per simple command; only an echo/grep/git segment is exempt
    (f"echo go; curl -X PUT {VOTE} -d '{{\"vote\": 10}}'", PR),
    (f"git fetch && curl -X PATCH {PR_URL} -d '{{\"status\":\"completed\"}}'", PR),
    ("git status & az repos pr set-vote --id 7 --vote 10", PR),
    (f"curl -X PUT '{VOTE}?api-version=7.1&x=1' -d '{{\"vote\": 10}}'", PR),
    # C6: a write method or a data flag in the same simple command makes it a decision
    (f"curl --request PATCH {PR_URL} --data-binary '{{\"autoCompleteSetBy\": {{\"id\": 1}}}}'", PR),
    ("python3 -c \"requests.patch(u + '/pullrequests/' + pr, json={'autoCompleteSetBy': me})\"",
     PR),
    # C5: variable ids, a body kept in a file, GitHub's merge and review routes, az devops invoke
    ("curl -X PUT \"https://h/_apis/git/repositories/r/pullrequests/$PR_ID/reviewers/$ME\" "
     "-d '{\"vote\": 10}'", PR),
    (f"curl -X PATCH {PR_URL} -d @body.json", PR),
    (f"az rest --method patch --url {PR_URL} --body @body.json", PR),
    ("curl -X PUT https://api.github.com/repos/o/r/pulls/7/merge", PR),
    ("gh api -X PUT repos/o/r/pulls/7/merge", PR),
    ("gh api repos/o/r/pulls/7/reviews -f event=APPROVE", PR),
    ("curl https://api.github.com/repos/o/r/pulls/7/reviews -d '{\"event\":\"APPROVE\"}'", PR),
    ("az devops invoke --area git --resource pullRequestReviewers --http-method PUT "
     "--in-file vote.json", PR),
    # C7 (and C12): az / gh inside $( ), ( ), `bash -c`, behind sudo or command, as .cmd / .exe
    ("x=$(az repos pr set-vote --id 7 --vote 10)", PR),
    ("echo $(az repos pr set-vote --id 7 --vote 10)", PR),
    ("(gh pr merge 7 --squash)", PR),
    ("echo `gh pr merge 7`", PR),
    ("bash -c \"az repos pr set-vote --id 7 --vote 10\"", PR),
    ("sh -lc 'cd x && gh pr merge 7'", PR),
    ("sudo -E az repos pr set-vote --id 7 --vote 10", PR),
    ("command gh pr merge 7", PR),
    ("az.cmd repos pr set-vote --id 7 --vote 10", PR),
    ("/usr/bin/gh.exe pr merge 7", PR),
    ("xargs gh pr merge", PR),
    ("gh -R o/r pr merge 7", PR),
    ("gh --repo o/r pr review 7 --approve", PR),
    ("az repos pr update --id 7 --status Completed", PR),
    ("az repos pr update --id 7 --status=COMPLETED", PR),
    ("bash -c \"git push origin main\"", "main is protected"),
    ("sudo git push \\\n origin main", "main is protected"),
    # C13: asking for changes is a vote too
    ("gh pr review 7 --request-changes -b 'fix it'", PR),
    ("gh pr review 7 -r -b 'fix it'", PR),
    # C1: every way to unset, edit or drop core.hooksPath
    ("git config --unset core.hooksPath", HOOKS),
    ("git config --unset-all core.hooksPath", HOOKS),
    ("git config unset core.hooksPath", HOOKS),
    ("git config --local --unset core.hookspath", HOOKS),
    ("git config --edit", HOOKS),
    ("git config edit", HOOKS),
    ("git config --remove-section core", HOOKS),
    ("git config remove-section core", HOOKS),
    ("git config --rename-section core spare", HOOKS),
    ("git config --replace-all core.hooksPath /dev/null", HOOKS),
    ("git config --file .git/config core.hooksPath /dev/null", HOOKS),
    # C2: only the plugin's own value may be written
    ("git config core.hooksPath .githooks-off", HOOKS),
    ("git config set core.hooksPath /dev/null", HOOKS),
    ("git config core.hooksPath ''", HOOKS),
    ("git config --add core.hooksPath .githooks", HOOKS),
    # C3: HUSKY=0 anywhere it takes effect, not only right before git
    ("export HUSKY=0", HOOKS),
    ("export HUSKY=0 && git commit -m x", HOOKS),
    ("env HUSKY=0 npm run release", HOOKS),
    ("HUSKY=0 npx semantic-release", HOOKS),
    ("CI=1 HUSKY=\"0\" make commit", HOOKS),
    ("bash -c 'HUSKY=0 npm version patch'", HOOKS),
    # C9: n inside a short-flag cluster of git commit, and the abbreviated long flag
    ("git commit -nm x", "--no-verify"),
    ("git commit -anm x", "--no-verify"),
    ("git commit -an -m x", "--no-verify"),
    ("git commit --no-verif -m x", "--no-verify"),
    ("git push --no-verif origin feat/x", "--no-verify"),
]

FOUND_ALLOWED = [
    f"echo 'curl -X PUT {VOTE} -d x'; ls",
    f"git commit -m 'curl -X PUT {VOTE} -d x' && ls",
    "git log --grep 'az repos pr set-vote' & ls",
    f"curl -s '{PR_URL}/threads?api-version=7.1&x=1'",
    # C6: a read of the PR API is not a decision
    f"curl -s {VOTE}",
    f"curl -sf {PR_URL} | python3 -c \"import json,sys; print(json.load(sys.stdin)['autoCompleteSetBy'])\"",
    "rg autoCompleteSetBy src && python3 -m pytest tests/test_pullrequests.py",
    "python3 -c \"print(requests.get(u + '/pullrequests/7').json()['autoCompleteSetBy'])\"",
    "python3 -m pytest tests/test_pullrequests.py -k autoCompleteSetBy -d",
    # C5: a comment thread, a new PR, reads and a comment review are ordinary work
    "curl -X POST \"https://h/_apis/git/repositories/r/pullrequests/$PR_ID/threads\" -d @c.json",
    "curl -X POST https://h/_apis/git/repositories/r/pullrequests -d @new.json",
    "curl -s https://api.github.com/repos/o/r/pulls/7/merge",
    "gh api repos/o/r/pulls/7/reviews",
    "gh api repos/o/r/pulls/7/reviews -f event=COMMENT -f body=ok",
    "gh api repos/o/r/pulls/7/comments -f body=ok",
    "az devops invoke --area git --resource pullRequestReviewers --route-parameters pullRequestId=7",
    # C7 (and C12): the same wrappers round calls that decide nothing
    "x=$(gh pr view 7 --json state)",
    "echo \"$(date)\" && (gh pr create --fill)",
    "bash -c \"az repos pr list\"",
    "sh -lc 'git push origin feat/x'",
    "sudo -E az repos pr show --id 7",
    "az.cmd repos pr create --title x",
    "gh -R o/r pr view 7",
    "gh --repo o/r pr review 7 --comment -b ok",
    "az repos pr update --id 7 --title completed --status active",
    "az repos pr update --id 7 --status abandoned --description 'not completed'",
    # C13: a comment that mentions the flag is still a comment
    "gh pr review 7 --comment -b 'use --request-changes sparingly'",
    # C1: the same verbs on other keys and sections
    "git config --unset user.signingkey",
    "git config unset user.name",
    "git config --remove-section branch.old",
    "git config --rename-section remote.a remote.b",
    "git config --get-all core.hooksPath",
    "git config --list",
    # C2: the plugin's own enable command (dl init and dl doctor print it) and the read forms
    "git config core.hooksPath .githooks",
    "git config --local core.hooksPath .githooks",
    "git config set core.hooksPath .githooks",
    "git config core.hooksPath",
    "git config get core.hooksPath",
    "git config --show-origin core.hooksPath",
    "git -C repo config --global core.hooksPath",
    # C3: a mention, another value, another variable
    "echo HUSKY=0",
    "grep -r HUSKY=0 .",
    "git commit -m 'never set HUSKY=0'",
    "export HUSKY=1",
    "HUSKY_DEBUG=0 npm test",
    "npm run check -- HUSKY=0",
    # C9: clusters without n, n as a flag's value, and push -n (a dry run)
    "git commit -am x",
    "git commit -mn",
    "git commit -uno -m x",
    "git commit --amend --no-edit",
    "git commit -m x --no-status",
    "git push -n origin feat/x",
]


@pytest.mark.parametrize(("command", "reason"), FOUND_DENIED)
def test_a_review_finding_is_denied(project: Path, command: str, reason: str) -> None:
    denied(run(command, project), reason)


@pytest.mark.parametrize("command", FOUND_ALLOWED)
def test_the_twin_of_a_review_finding_stays_silent(project: Path, command: str) -> None:
    allowed(run(command, project))
