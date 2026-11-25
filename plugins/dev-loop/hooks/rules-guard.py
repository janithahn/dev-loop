"""PreToolUse hook: the rules the plugin states in prose, enforced.

1. A headless run (DEV_LOOP_ROLE set, or permission_mode bypassPermissions) never starts a
   background command: nobody is there to wait for its notification.
2. A subagent (the event has an agent_id) never edits Claude Code's settings files, by an edit
   tool or by a Bash command that writes one; the path is compared in lower case.
3. Records are written by the record writer: no edit of a change's records.jsonl, and no edit
   of its proposal.md that adds a `Size waiver:` line (only a person adds one). A Bash command
   is judged by the files it writes (dev_loop/bash_writes.py); a command that only reads them passes.
4. A Bash command the project names CI-only (`gates.ci_only`) runs only where CI or TF_BUILD
   is set.
5. A Bash command that runs `records approve` (the owner's step) is denied; --help too.
Exit 2 denies with one line on stderr. A malformed event or a crash of its own, SystemExit
included, fails open and loud: exit 1, one stderr line and one in hook-errors.jsonl
(dev_loop.hookenv.crashed). Names only, never the call's text.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shlex
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any


RECORDS = "records go through the record writer (dl record), never a hand edit"
WAIVED = "only a person adds a size waiver: ask the owner to write the line"
CI_ONLY = "this command runs in CI only (gates.ci_only): run the tests for what changed"
APPROVE = "approving a distilled skill is the owner's step, in their own terminal"
APPROVING = re.compile(r"(?:^|[\s/])(?:dl|dev_loop)(?:\s+-\S+)*\s+records\s+approve\b")
ENDS = ";|&()\n"  # the shell operators that end one simple command
WAIVER = re.compile(r"\**Size waiver:")  # the size cap's own opening, bold form included


def deny_background(event: dict[str, Any], call: dict[str, Any]) -> str:
    if not (os.environ.get("DEV_LOOP_ROLE") or event.get("permission_mode") == "bypassPermissions"):
        return ""
    said = "a headless run never starts a background command: run it in the foreground"
    tool, command = event.get("tool_name"), str(call.get("command", ""))
    if tool in {"Bash", "PowerShell", "Agent", "Task"} and call.get("run_in_background") is True:
        return said
    if tool != "Bash" or not re.search(r"&|nohup|setsid|disown", command):  # else no parse
        return ""
    try:
        return said if shell().backgrounds(command) else ""
    except ValueError as error:  # an unclosed quote: not read, the call stands
        name = type(error).__name__
        print(f"rules-guard: {name}, background not checked", file=sys.stderr)  # noqa: T201
        return ""


SRC = str(Path(__file__).resolve().parents[1] / "src")  # this hook's own plugin code


def shell() -> Any:
    """Return the module that reads what a Bash command writes (dev_loop.bash_writes)."""
    sys.path.insert(0, SRC)
    from dev_loop import bash_writes  # noqa: PLC0415 - only for a command that names a file

    return bash_writes


def working(event: dict[str, Any]) -> Path:
    """Return the folder a Bash call's relative paths are read from."""
    return Path(event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or ".")


def deny_settings(event: dict[str, Any], call: dict[str, Any]) -> str:
    """A subagent's write of a settings file: an edit tool's path, or one a Bash command writes."""
    if not event.get("agent_id"):
        return ""
    paths = [str(call.get("file_path") or call.get("notebook_path") or "")]
    command = str(call.get("command", "")) if event.get("tool_name") == "Bash" else ""
    if "settings" in command.lower():  # else no parse
        paths += [str(path) for path in shell().written(command, working(event))]
    settings = (".claude/settings.json", ".claude/settings.local.json")
    if any(path.replace("\\", "/").lower().endswith(settings) for path in paths):
        return "a subagent never edits Claude Code settings: ask the coordinator"
    return ""


@functools.cache
def config() -> tuple[Path, Any]:
    """Return the project and its loaded config, from this hook's own plugin code."""
    sys.path.insert(0, SRC)
    from dev_loop.hookenv import add_venv  # noqa: PLC0415 - only when needed

    add_venv()  # the plugin venv's PyYAML, whichever Python 3 built it
    from dev_loop.config import load, project_root  # noqa: PLC0415 - after the venv is added

    root = project_root()
    return root, load(root)


def changes_parts() -> tuple[str, ...]:
    """Return the parts of `paths.changes`, the way a change folder ends in any worktree."""
    return Path(config()[1].get("paths.changes")).parts


def simple_commands(command: str) -> list[str]:
    """Each simple command's words; an argument with a space in it (a quoted text) is left out.

    ponytail: a `#` comment swallows its newline, so the next line joins that command; a
    command inside `bash -c "..."` or a script is not seen. A real shell parser if it matters.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=ENDS)
    lexer.whitespace, lexer.whitespace_split = " \t\r", True
    found: list[list[str]] = [[]]
    for token in lexer:
        if not token.strip(ENDS):
            found.append([])
        elif not re.search(r"\s", token):
            found[-1].append(token)
    return [" ".join(words) for words in found if words]


def deny_ci_only(call: dict[str, Any]) -> str:
    """A simple command matching a `gates.ci_only` pattern, outside CI."""
    command = str(call.get("command", ""))
    if not command or os.environ.get("CI") or os.environ.get("TF_BUILD"):
        return ""
    mine = Path(os.environ.get("CLAUDE_PROJECT_DIR", ""), "dev-loop.yaml")  # a hook always has it
    if not mine.is_file() or "ci_only" not in mine.read_text(encoding="utf-8"):
        return ""  # the fast path: the key is not set, no YAML parse
    loaded = config()[1]
    if loaded.error:  # a refused file: the list cannot be trusted, fail open and loud
        raise ValueError(loaded.error)
    patterns = [re.compile(pattern) for pattern in loaded.get("gates.ci_only")]
    if patterns and any(p.search(c) for c in simple_commands(command) for p in patterns):
        return CI_ONLY
    return ""


def deny_approve(call: dict[str, Any]) -> str:
    """A simple command running `records approve`; a quoted text or a mention is not one."""
    command = str(call.get("command", ""))
    if "approve" not in command:  # else no parse
        return ""
    return APPROVE if any(APPROVING.search(c) for c in simple_commands(command)) else ""


def applied(call: dict[str, Any], text: str) -> tuple[str, str]:
    """Return the file's text before and after the call; an old text it lacks counts as there."""
    if "content" in call:
        return text, str(call["content"])
    edits = call.get("edits") or [call]
    olds = [str(edit.get("old_string", "")) for edit in edits]
    before = after = text + "".join(chr(10) + old for old in olds if old not in text)
    for edit, old in zip(edits, olds):
        count = -1 if edit.get("replace_all") else 1
        after = after.replace(old, str(edit.get("new_string", "")), count)
    return before, after


def waiver_lines(text: str) -> set[str]:
    return {line for line in text.splitlines() if WAIVER.match(line)}


MENTIONS = re.compile(r"records\.j|proposal\.m")  # the fast path: no parse, no config read


def named(path: Path) -> str:
    """Return the guarded name the path's last part is or, as a glob, matches; else ""."""
    return next((n for n in ("records.jsonl", "proposal.md") if fnmatchcase(n, path.name)), "")


def ours(path: Path) -> bool:
    """True for a records.jsonl or a proposal.md under a changes folder, a linked worktree's too."""
    if not named(path):
        return False
    parts = changes_parts()
    return any(folder.parts[-len(parts):] == parts for folder in path.resolve().parents)


def deny_bash(event: dict[str, Any], call: dict[str, Any]) -> str:
    """A shell write of a change's records file or of a size waiver, by what the command writes."""
    command = str(call.get("command", ""))
    if not MENTIONS.search(command):
        return ""
    cwd = working(event)
    hit = [path for path in shell().written(command, cwd) if ours(path)]
    proposals = [path for path in hit if named(path) == "proposal.md"]
    if len(proposals) < len(hit):
        return RECORDS
    if not proposals:
        return ""
    if WAIVER.search(command):  # ponytail: anywhere in the command, a commit message included
        return WAIVED
    words = [s.cwd / arg for s in shell().commands(command, cwd) for arg in s.args]
    brought = set().union(*map(waivers, words)) - set().union(*map(waivers, proposals))
    return WAIVED if brought else ""  # a waiver another file carries in: mv, cp, cat >>


def waivers(path: Path) -> set[str]:
    """Return the waiver lines of a readable text file under 1 MB; of anything else, none."""
    try:
        if not path.is_file() or path.stat().st_size > 1_000_000:
            return set()
        return waiver_lines(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # unreadable, not text, or a word no path can hold
        return set()


def deny_records(event: dict[str, Any], call: dict[str, Any]) -> str:
    path = Path(str(call.get("file_path", "")))
    if event.get("tool_name") not in {"Edit", "Write", "MultiEdit"} or not ours(path):
        return ""
    if path.name == "records.jsonl":
        return RECORDS
    before, after = applied(call, path.read_text(encoding="utf-8") if path.is_file() else "")
    return WAIVED if waiver_lines(after) - waiver_lines(before) else ""


def main() -> int:
    event = json.loads(sys.stdin.read())
    call = event["tool_input"]
    bash = call if event.get("tool_name") == "Bash" else {}
    said = (deny_background(event, call) or deny_settings(event, call)
            or deny_records(event, call) or deny_bash(event, bash) or deny_ci_only(bash)
            or deny_approve(bash))
    if said:
        print(f"rules-guard: {said}", file=sys.stderr)  # noqa: T201
        return 2
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException as error:  # noqa: BLE001 - fail open, loud: SystemExit too, never block
        sys.path.insert(0, SRC)
        from dev_loop.hookenv import crashed

        crashed("rules-guard", error, "call allowed")  # the line, and hook-errors.jsonl
        code = 1
    sys.exit(code)
