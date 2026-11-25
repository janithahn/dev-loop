"""dev-loop push guard: a PreToolUse(Bash) hook that refuses a push to a protected branch (G4).

The branch guard stops edits on a protected branch; this stops the push that would land them
anyway. It denies a `git push` whose destination is protected, a bare push while the current
branch is protected, `--all`, `--mirror`, any `--no-verify` on a push or commit (`-nm` too), the
other ways round the git hooks (`-c core.hooksPath`, a `git config` write to core.hooksPath other
than `.githooks`, `HUSKY=0` anywhere), `--force` / `-f` / `+refspec` without `--force-with-lease`,
a force-with-lease to any branch but the current one (whatever its prefix), and an agent's PR
approve, vote, merge or complete (push_guard/pr_rules.py: `az repos pr`, `gh pr`, a write to the
PR API; `dl land` calls the API from Python, not through the Bash tool, so it is not caught) so the
rule holds whether or not the agent remembers it. Every simple command of the line is judged
(push_guard/shell_words.py), inside `$( )`, `bash -c` and behind `sudo` too.

Protected = `branches.integration` + `branches.release` + `branches.protected`, read through
`dl config get branches` (the project is CLAUDE_PROJECT_DIR, else git's top level), matched as
globs so `release/*` is honoured. The config is read only once a push is seen (the fast path: a
non-git command returns at once, with no YAML). The answer is the PreToolUse deny JSON on stdout;
any other command passes silently. A config that cannot be read denies every push. A crash
fails open and loud (one stderr line), so a broken guard never blocks unrelated work.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from fnmatch import fnmatchcase

PLUGIN = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(PLUGIN, "hooks", "push_guard"))
from pr_rules import PR_RULE, decides  # noqa: E402 - the hook's own folder
from shell_words import ENV, segments, tool  # noqa: E402

WATCH = re.compile(r"git|husky|pull|\b(az|gh)\b", re.IGNORECASE)  # else: nothing to judge
VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
              "--config-env", "--super-prefix"}  # git global options that take a separate value
PUSH_VALUE = {"-o", "--push-option", "--receive-pack", "--exec", "--repo"}  # push value options
CONFIG_VALUE = {"-f", "--file", "--blob", "-t", "--type", "--default"}  # config value options
CONFIG_READ = ("--get", "--list", "-l", "--show-origin")
CONFIG_SCOPE = ("--local", "--global", "--system", "--worktree", "--file", "-f", "--blob", "--null",
                "-z", "--type", "-t", "--show-scope")  # flags a plain read or set may carry
CONFIG_VERBS = {"get", "set", "unset", "list", "edit", "rename-section", "remove-section"}
HOOK_RULE = "changing or skipping git hooks is refused: let the hooks run"
WAY_OUT = "push a feature branch and open a PR"


def git_command(tokens: list[str]) -> tuple[str, list[str], str | None, list[str]] | None:
    """Return (subcommand, args after it, the -C directory, tokens before it) for a git call.

    A leading VAR=value env prefix is skipped and git's global options are consumed, so the
    subcommand of `FOO=bar git -C repo push ...` is found.
    """
    index = 0
    while index < len(tokens) and ENV.match(tokens[index]):
        index += 1
    if index >= len(tokens) or tool(tokens[index]) != "git":
        return None
    index += 1
    directory = None
    while index < len(tokens):
        token = tokens[index]
        if token == "-C" and index + 1 < len(tokens):
            directory = tokens[index + 1]
            index += 2
        elif token in VALUE_OPTS and index + 1 < len(tokens):
            index += 2
        elif token.startswith("-"):
            index += 1
        else:
            return token, tokens[index + 1:], directory, tokens[:index]
    return None


def push_targets(args: list[str]) -> tuple[set[str], list[str]]:
    """Return the flags present and the positional arguments of a `git push`."""
    flags: set[str] = set()
    positionals: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token in PUSH_VALUE and index + 1 < len(args):
            index += 2
        elif token.startswith("-"):
            flags.add(token.split("=")[0])
            index += 1
        else:
            positionals.append(token)
            index += 1
    return flags, positionals


def destination(refspec: str) -> str:
    """The branch a refspec writes to: `+a:b` -> b, `:b` -> b, `refs/heads/b` -> b."""
    spec = refspec.lstrip("+")
    branch = spec.split(":", 1)[1] if ":" in spec else spec
    return branch.removeprefix("refs/heads/")


def protected(name: str, patterns: list[str]) -> bool:
    return any(fnmatchcase(name, pattern) for pattern in patterns)


def current_branch(directory: str | None, cwd: str | None) -> str | None:
    """The checked-out branch of the push's target repository, or None if it cannot be read."""
    command = ["git", "-C", directory or ".", "rev-parse", "--abbrev-ref", "HEAD"]
    done = subprocess.run(command, cwd=cwd or None, capture_output=True,  # noqa: S603, S607
                          text=True, check=False)
    return done.stdout.strip() if done.returncode == 0 else None


def check_force(flags: set[str], targets: list[str | None], branch: str | None) -> str | None:
    """The deny reason for a forced push: leased, and of the checked-out branch only.

    `flags` holds "+" when a refspec starts with one.
    """
    short = any(f.startswith("-") and not f.startswith("--") and "f" in f[1:] for f in flags)
    lease = any(f.startswith("--force-w") and "--force-with-lease".startswith(f) for f in flags)
    force = any(f.startswith("--fo") and "--force".startswith(f) for f in flags)  # git abbreviates
    if force or short or ("+" in flags and not lease):
        return "a force push must use --force-with-lease, never --force, -f or +refspec"
    if not lease:
        return None
    for name in targets:
        if not name or name != branch:
            return ("force-with-lease only on the current branch (your change branch): "
                    f"{name} is not {branch}")
    return None


def check_push(args: list[str], directory: str | None, cwd: str | None,
               branches: dict) -> str | None:
    """The deny reason for one `git push`, else None."""
    patterns = protected_names(branches)
    flags, positionals = push_targets(args)
    if "--all" in flags:
        return f"git push --all would touch a protected branch: {WAY_OUT}"
    if "--mirror" in flags:
        return f"git push --mirror would touch a protected branch: {WAY_OUT}"
    specs = positionals[1:]  # positionals[0] is the remote
    branch = current_branch(directory, cwd)  # a bare push uses the current branch
    names = [branch if (n := destination(spec)) in ("HEAD", "@") else n for spec in specs]
    for name in names or [branch]:
        if name and protected(name, patterns):
            return f"{name} is protected: {WAY_OUT}"
    flags |= {"+"} if any(spec.startswith("+") for spec in specs) else set()
    return check_force(flags, names or [branch], branch)


def branches_for(cwd: str | None) -> dict | None:
    """The `branches` config; None when it cannot be read."""
    command = ["bash", os.path.join(PLUGIN, "bin", "dl"), "config", "get", "branches"]
    try:
        done = subprocess.run(command, cwd=cwd or None, capture_output=True,  # noqa: S603, S607
                              text=True, check=False)
        if done.returncode != 0:  # a refused config exits 1
            return None
        return json.loads(done.stdout) if done.stdout.strip() else {}
    except (OSError, ValueError):
        return None


def protected_names(branches: dict) -> list[str]:
    """The protected branch names and globs."""
    names = [branches.get("integration") or "main", branches.get("release"),
             *(branches.get("protected") or [])]
    return [name for name in names if name]


def config_bypass(args: list[str]) -> bool:
    """True for a `git config` call that could change core.hooksPath; reads, `.githooks` pass."""
    flags = [arg for arg in args if arg.startswith("-")]
    words = [arg.lower() for index, arg in enumerate(args)
             if not arg.startswith("-") and (not index or args[index - 1] not in CONFIG_VALUE)]
    verb = words.pop(0) if words and words[0] in CONFIG_VERBS else ""
    if verb == "edit" or {"--edit", "-e"} & set(flags):
        return True
    if "section" in verb or any("-section" in flag for flag in flags):
        return "core" in words
    if not any("core.hookspath" in word for word in words):
        return False
    if verb in ("get", "list") or any(flag.startswith(CONFIG_READ) for flag in flags):
        return False
    if verb not in ("", "set") or not all(flag.startswith(CONFIG_SCOPE) for flag in flags):
        return True
    return not (words[1:] == [".githooks"] or (not verb and len(words) == 1))


def husky_off(tokens: list[str]) -> bool:
    """True for a leading `HUSKY=0` assignment: inline before any tool, or after export / env."""
    for token in tokens:
        if not ENV.match(token):
            return False
        if token.lower() == "husky=0":
            return True
    return False


def hook_bypass(sub: str, args: list[str], head: list[str]) -> bool:
    """True for `git -c core.hooksPath=..` and a `git config` write to core.hooksPath."""
    if any("core.hookspath" in token.lower() for token in head):
        return True
    return sub == "config" and config_bypass(args)


def no_verify(sub: str, args: list[str]) -> bool:
    """True for --no-verify (abbreviated too) and, on a commit, `n` in a short cluster (-nm)."""
    if any(arg.startswith("--no-v") and "--no-verify".startswith(arg) for arg in args):
        return True
    # push -n is a dry run; a letter that takes a value (-m, -uno) ends a cluster's flags
    return sub == "commit" and any(re.match(r"-[^-mFCctuS]*n", arg) for arg in args)


def inspect(command: str, cwd: str | None) -> str | None:
    """The deny reason for the first offending git call in the command, else None."""
    if not WATCH.search(command):
        return None
    parts = segments(command)
    if any(decides(tokens) for tokens in parts):
        return PR_RULE
    if any(husky_off(tokens) for tokens in parts):
        return HOOK_RULE
    calls = [found for tokens in parts if (found := git_command(tokens))]
    for sub, args, _, head in calls:
        if hook_bypass(sub, args, head):
            return HOOK_RULE
        if sub in ("push", "commit") and no_verify(sub, args):
            return "--no-verify is refused on a push or commit: let the hooks run"
    pushes = [(args, directory) for sub, args, directory, _ in calls if sub == "push"]
    if not pushes:
        return None
    branches = branches_for(cwd)
    if branches is None:
        return f"the protected branches cannot be read from the config, push refused: {WAY_OUT}"
    for args, directory in pushes:
        if reason := check_push(args, directory, cwd, branches):
            return reason
    return None


def main() -> int:
    """Deny on stdout, else let the call through; fail open and loud on the hook's own error."""
    try:
        event = json.loads(sys.stdin.buffer.read())
        command = (event.get("tool_input") or {}).get("command", "")
        reason = inspect(command, event.get("cwd")) if isinstance(command, str) else None
        if reason:
            print(json.dumps({"hookSpecificOutput": {  # noqa: T201 - stdout is the hook's answer
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": reason}}))
    except BaseException as error:  # noqa: BLE001 - the hook's one boundary, fails open
        sys.path.insert(0, os.path.join(PLUGIN, "src"))
        from dev_loop.hookenv import crashed  # noqa: PLC0415 - only a crash pays for it

        crashed("push-guard", error, "command allowed")  # the line, and hook-errors.jsonl
    return 0


if __name__ == "__main__":
    sys.exit(main())
