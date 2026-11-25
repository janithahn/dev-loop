"""PreToolUse hook on Agent|Task: the brief contract (gap G1).

The call's prompt must carry every heading of `briefs.sections`, each with a non-blank body, and
list at most `briefs.max_read_first` files (bullet lines) under "Read first". `briefs.enforce`:
warn says the gap to the model and allows; block exits 2 with the gap on stderr. A bad project
dev-loop.yaml is said on stderr and the shipped defaults check the call; a crash of its own
fails open and loud: exit 1, one stderr line. The project is
CLAUDE_PROJECT_DIR, else git's top level; the code is this hook's own plugin, wherever installed.
`briefs.agent_types`: when not empty, only a call whose `subagent_type` is listed is checked.
`briefs.change_line`: when true, a `Change: <name>` line is required.
Names and counts only, never the prompt's text.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

HEADING = re.compile(r"#{1,6}[ \t]+(.*?)[ \t#]*")
BULLET = re.compile(r"[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S")
READ_FIRST = "read first"
MISSING_CHANGE = 'missing Change line (a line "Change: <change-name>": lower case, digits, . _ -)'
CHANGE = re.compile(r"^Change:[ \t]*[a-z0-9][a-z0-9._-]{0,63}[ \t\r]*$", re.MULTILINE)


def sections(prompt: str) -> dict[str, list[str]]:
    """Return each lower-case heading's non-blank body lines."""
    found: dict[str, list[str]] = {}
    current = None
    for line in prompt.splitlines():
        if heading := HEADING.fullmatch(line):
            current = heading.group(1).lower()
            found.setdefault(current, [])
        elif current is not None and line.strip():
            found[current].append(line)
    return found


def gaps(prompt: str, wanted: list[str], cap: int) -> list[str]:
    """Return what the brief lacks: missing sections, then a Read first over the cap."""
    found = sections(prompt)
    said = [f"missing {name}" for name in wanted if not found.get(name.lower())]
    files = sum(1 for line in found.get(READ_FIRST, []) if BULLET.match(line))
    if files > cap:
        said.append(f"Read first lists {files} files, over {cap}")
    return said


def load_config() -> Any:  # noqa: ANN401 - a Loaded; dev_loop is imported late
    """Return the merged config from this hook's own plugin (and its venv's PyYAML, if any)."""
    plugin = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(plugin / "src"))
    from dev_loop.hookenv import add_venv  # noqa: PLC0415 - after the path is set

    add_venv()
    from dev_loop.config import load, project_root  # noqa: PLC0415 - after the venv is added

    return load(project_root())


def main() -> int:
    call = json.loads(sys.stdin.read())["tool_input"]
    prompt = call.get("prompt", "")
    if not isinstance(prompt, str):
        msg = "prompt is not text"
        raise TypeError(msg)
    config = load_config()
    if config.error:
        # the loaded values are the shipped defaults: check against them, never allow
        unused = f"brief-contract: project config not used: {config.error}"
        print(unused, file=sys.stderr)  # noqa: T201
    get = config.get
    only = get("briefs.agent_types")
    if only and call.get("subagent_type") not in only:
        return 0  # a type the contract does not apply to: not checked, nothing said
    said = gaps(prompt, get("briefs.sections"), get("briefs.max_read_first"))
    if get("briefs.change_line") and not CHANGE.search(prompt):
        said.append(MISSING_CHANGE)
    if call.get("run_in_background"):
        said.append("run_in_background is set: a builder runs in the foreground")
    if not said:
        return 0
    line = "brief contract: " + "; ".join(said)
    if get("briefs.enforce") == "block":
        print(line, file=sys.stderr)  # noqa: T201
        return 2
    context = {"hookEventName": "PreToolUse", "additionalContext": line}
    print(json.dumps({"hookSpecificOutput": context}))  # noqa: T201 - stdout is the answer
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException as error:  # noqa: BLE001 - fail open, loud: SystemExit too, never block
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from dev_loop.hookenv import crashed

        crashed("brief-contract", error, "call allowed")  # the line, and hook-errors.jsonl
        code = 1
    sys.exit(code)
