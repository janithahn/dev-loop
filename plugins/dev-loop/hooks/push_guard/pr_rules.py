"""The push guard's PR-decision rules: is one simple command an approve, vote, merge or complete?

`decides(tokens)` judges one simple command (the push guard splits the command line first), so
a harmless first command never excuses a later one. A segment led by echo, printf, grep, rg or
git only mentions such a call and passes.
"""

from __future__ import annotations

import re

from shell_words import ENV, tool

QUIET = {"echo", "printf", "grep", "rg", "git"}  # these only mention a call, they do not make it
VOTES = re.compile(  # a write here is a vote, a merge or (the body may sit in a file) a completion
    r"pullrequests/[^/\s]+/reviewers|pulls/[^/\s]+/merge|--resource[ =]pullrequestreviewers\b"
    r"|pullrequests/[^/\s?\"')]+(?=$|[\s?\"')])", re.IGNORECASE)
GH_REVIEW = re.compile(r"pulls/[^/\s]+/reviews", re.IGNORECASE)  # a decision unless a comment
COMMENT = re.compile(r"event[\\\"']*\s*[=:]\s*[\\\"']*COMMENT", re.IGNORECASE)
KEYWORD = re.compile(r"autoCompleteSetBy|status[\\\"']+\s*:\s*[\\\"']+completed", re.IGNORECASE)
GH_FIELD = re.compile(r"(?:^|\s)(?:-[fF]|--field|--raw-field|--input)(?=[\s=]|$)")  # gh api: POST
METHOD = r"(?i:put|patch|post)\b"
WRITE = re.compile(  # a write method or a data flag: a read of the PR API decides nothing
    r"(?:^|\s)(?:-[A-Za-z]*X\s*|--request[ =]|--(?:http-)?method[ =]|(?i:-method)\s+)" + METHOD
    + r"|(?:^|\s)(?:-[A-Za-z]*[dFT]|--data[\w-]*|--json|--form|--upload-file|--in-file"
    r"|--body[\w-]*|--post-\w+)(?=[\s=@]|$)"
    r"|\.(?i:put|patch|post)\s*\(|(?i:method)[\"']?\s*[=:]\s*[\"']?" + METHOD)
PR_RULE = "approving, voting on or completing a PR is for the lander or a person, not an agent"


def gh_decides(args: list[str]) -> bool:
    """True for `gh pr merge` and a voting `gh pr review`, global flags such as -R skipped."""
    words, flags, skip = [], set(), False
    for arg in args:
        if skip or arg in ("-R", "--repo"):
            skip = not skip
        elif arg.startswith("-"):
            flags.add(arg.split("=")[0])
        else:
            words.append(arg)
    return words[:2] == ["pr", "merge"] or (
        words[:2] == ["pr", "review"]
        and bool(flags & {"--approve", "-a", "--request-changes", "-r"}))


def az_decides(args: list[str]) -> bool:
    """True for `az repos pr set-vote` and an update that completes or sets auto-complete."""
    if "repos" not in args or "pr" not in args:
        return False
    status = ""
    for index, arg in enumerate(args):
        if arg.split("=")[0] == "--status":  # its own value, not any later word
            status = arg.partition("=")[2] or "".join(args[index + 1:index + 2])
    auto = any(arg.startswith("--auto-complete") for arg in args)
    return "set-vote" in args or ("update" in args and (status.lower() == "completed" or auto))


def cli_decides(tokens: list[str]) -> bool:
    """True for an az / gh call, anywhere in the simple command, that decides a PR."""
    names = [tool(token) for token in tokens]
    if "gh" in names:
        return gh_decides(tokens[names.index("gh") + 1:])
    return "az" in names and az_decides(tokens[names.index("az") + 1:])


def rest_decides(tokens: list[str]) -> bool:
    """True for a write to the PR API (any tool) that votes on or completes a PR."""
    text = " ".join(tokens)
    gh_post = any(tool(token) == "gh" for token in tokens) and GH_FIELD.search(text)
    if not (WRITE.search(text) or gh_post):
        return False
    if VOTES.search(text):
        return True
    if GH_REVIEW.search(text):
        return not COMMENT.search(text)
    return bool(KEYWORD.search(text)) and "pullrequests/" in text.lower()


def decides(tokens: list[str]) -> bool:
    """True when this simple command approves, votes on, merges or completes a PR."""
    while tokens and ENV.match(tokens[0]):
        tokens = tokens[1:]
    if not tokens or tool(tokens[0]) in QUIET:
        return False
    return cli_decides(tokens) or rest_decides(tokens)
