"""`dl worker`: run this machine's task worker (`worker.sh`) from the project's config.

    DEV_LOOP_WORKER=<name> [SLOTS=5] dl worker      (run it inside tmux)

The worker's checkout and worktree root come from `workers.<name>`, the base branch from
`branches.integration`, the brief's never list from `workers.<name>.never`, the kinds it takes
from `workers.<name>.may_run` (absent: every kind; the brief tells the run, nothing else enforces
it), and the model from `DEV_LOOP_MODEL`, else `roles.coordinator.model` (null: the CLI's own).
The machine limits
(`slots`, `poll_seconds`, `timeout_hours`, `max_turns`, `docker_slots`, `default_base`) come from
`workers.<name>`, each overridden by its upper-case environment variable. It refuses to start
while `dl doctor` has a red row, when the config has an error or no base branch (fail closed),
and when ~/.claude/settings.json's env sets OTEL_RESOURCE_ATTRIBUTES: the runner tags each run
itself (DEV_LOOP_TELEMETRY=1 or CLAUDE_CODE_ENABLE_TELEMETRY=1), and a settings value would tag
every run the same.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

from dev_loop import config, doctor, schema
from dev_loop.tasks import agent_tasks

SCRIPT = Path(__file__).with_name("worker.sh")
BIN = Path(__file__).resolve().parents[3] / "bin"


def environment(root: Path, name: str) -> dict[str, str]:
    """Return the environment `worker.sh` runs with; KeyError when `name` is not configured."""
    loaded = config.load(root)
    if loaded.error:
        raise ValueError(loaded.error)
    mine = loaded.get(f"workers.{name}")
    if not loaded.get("branches.integration"):
        msg = "branches.integration: empty"
        raise ValueError(msg)
    model = os.environ.get("DEV_LOOP_MODEL") or loaded.get("roles.coordinator.model") or ""
    return os.environ | {
        "DEV_LOOP_WORKER": name,
        "DEV_LOOP_CHECKOUT": str(Path(mine["checkout"]).expanduser()),
        "DEV_LOOP_WORKTREES": str(Path(mine["worktrees"]).expanduser()),
        "DEV_LOOP_BASE": str(loaded.get("branches.integration")),
        "DEV_LOOP_REMOTE": str(loaded.get("branches.remote")),
        "DEV_LOOP_PROVIDER": str(loaded.get("host.provider")),
        "DEV_LOOP_NEVER": ", ".join(mine["never"]) or "nothing beyond the brief",
        "DEV_LOOP_MODEL": str(model),
        "DEV_LOOP_MAY_RUN": ", ".join(mine.get("may_run", [])),  # empty: every kind
        "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",  # a headless run has nothing to wake it
        "STALE_HOURS": os.environ.get("STALE_HOURS", "24"),  # the sweep's question/queue limit
        "PATH": f"{BIN}{os.pathsep}{os.environ.get('PATH', '')}",
    } | limits(name, mine, str(loaded.get("branches.integration")))


def limits(name: str, mine: dict[str, object], integration: str) -> dict[str, str]:
    """Return a worker's limits, each env variable of its upper-case name over its config value.

    ValueError names the dotted key of a bad value, from the config or the environment.
    """
    values = schema.WORKER_DEFAULTS | {k: v for k, v in mine.items() if k in schema.WORKER_KEYS}
    for key in schema.WORKER_DEFAULTS:
        if (raw := os.environ.get(key.upper())) is not None:
            whole = raw.isdigit() and schema.WORKER_KEYS[key] == "positive"
            values[key] = int(raw) if whole else raw
    schema.validate_section(values, f"workers.{name}", schema.WORKER_KEYS)
    values["default_base"] = values["default_base"] or integration
    return {key.upper(): str(values[key]) for key in schema.WORKER_DEFAULTS}


def settings_tag_every_run(home: Path) -> str | None:
    """Return why ~/.claude/settings.json stops the runner, None when it does not."""
    path = home / ".claude" / "settings.json"
    if not path.exists():
        return None
    try:
        env = json.loads(path.read_text(encoding="utf-8")).get("env", {})
    except (OSError, ValueError, AttributeError) as error:
        return f"{path} unreadable ({error})"
    if "OTEL_RESOURCE_ATTRIBUTES" in env:
        return f"{path} sets env OTEL_RESOURCE_ATTRIBUTES; remove it: the runner tags each run"
    return None


def main(argv: list[str]) -> int:
    del argv
    name = os.environ.get("DEV_LOOP_WORKER", "")
    try:
        root = config.project_root()
        env = environment(root, name)
    except KeyError as error:
        print(f"dl worker: set DEV_LOOP_WORKER to a name under workers ({error})", file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"dl worker: {error}", file=sys.stderr)
        return 2
    provider = env.pop("DEV_LOOP_PROVIDER")
    if importlib.util.find_spec(f"dev_loop.hosts.{provider}_work") is None:
        print(agent_tasks.refusal(provider), file=sys.stderr)
        return 1
    red = [f"{row} ({detail})" for row, ok, detail in doctor.rows(root)
           if not ok and row != "agent models"  # advisory: a model choice is the project's
           and not detail.startswith("gate not seen running")]  # an idle week is fine
    if red:
        print(f"dl worker: dl doctor is red: {'; '.join(red)}", file=sys.stderr)
        return 1
    refused = settings_tag_every_run(Path.home())
    if refused:
        print(f"dl worker: {refused}", file=sys.stderr)
        return 1
    os.execvpe("bash", ["bash", str(SCRIPT)], env)
    return 0
