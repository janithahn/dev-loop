"""UserPromptSubmit and PostToolUse(Skill) hook: recall at /dev-loop:plan and /dev-loop:build.

On a typed `/dev-loop:plan <change>` or `/dev-loop:build <change>`, or an agent's Skill call of
either, hands the model the change's recall (docs/research/ first, then the three most similar
past lessons by id) as additionalContext. The project is the nearest folder holding
`dev-loop.yaml` from the event's cwd upwards, then the change's own worktree, if another checkout holds it; else
CLAUDE_PROJECT_DIR. The readers come from this hook's own plugin, wherever it is installed.
Anything else: no output. It never blocks, and a recall that fails says so to the model.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

RUN = re.compile(r"/?dev-loop:(?:plan|build)(?:\s+(.*))?")


def recalled(event: dict, change: str) -> str:
    """Return the change's recall, or the failure said to the model (and logged as a crash)."""
    from dev_loop.hookenv import add_venv, crashed

    cwd = Path(event.get("cwd") or ".").resolve()
    found = (p for p in (cwd, *cwd.parents) if (p / "dev-loop.yaml").is_file())
    root = next(found, Path(os.environ.get("CLAUDE_PROJECT_DIR") or cwd))
    try:  # imported here: every other prompt exits before Python loads the readers
        add_venv()
        from dev_loop.records.review_pitfalls import recall, record_shown
        from dev_loop.worktrees import home

        root = home(root.resolve(), Path(change).name)[0] or root
        lines = recall(root, change, [])
        try:  # impact's recall hit rate needs what was shown; a failure is said, not hidden
            record_shown(root, change, lines)
        except Exception as error:  # noqa: BLE001
            lines.append(f"recall not recorded: {type(error).__name__}")
        return "\n".join(lines)
    except BaseException as error:  # noqa: BLE001 - said to the model, never a silent hook
        crashed("plan-recall", error, "recall unavailable")
        return f"recall unavailable: {type(error).__name__}"


def main() -> None:
    event = json.load(sys.stdin)
    tool = event.get("tool_input") or {}
    text = event.get("prompt") or f"{tool.get('skill', '')} {tool.get('args', '')}"
    run = RUN.fullmatch(text.strip())
    change = next((w for w in ((run and run[1]) or "").split() if not w.startswith("-")), None)
    if change:
        context = recalled(event, change)
        name = event.get("hook_event_name")
        answer = {"hookEventName": name, "additionalContext": context}
        print(json.dumps({"hookSpecificOutput": answer}))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    try:
        main()
    except BaseException as error:  # noqa: BLE001 - fail open, loud: SystemExit too
        from dev_loop.hookenv import crashed

        crashed("plan-recall", error, "no recall")  # the line, and hook-errors.jsonl
        sys.exit(1)
