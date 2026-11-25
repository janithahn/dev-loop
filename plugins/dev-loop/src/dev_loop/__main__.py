"""The `dl` command: one table from subcommand name to the module that runs it."""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
from pathlib import Path

from .config import project_root, required

TABLE = {
    "record": "records.record", "records": "records.review_records", "lesson": "records.lesson",
    "incident": "records.incident",
    "review": "review.run", "host": "hosts.cli",
    "ci": "ci.steps", "stack-rebase": "ci.stack_rebase", "githook": "ci.githook",
    "init": "init", "doctor": "doctor", "config": "config",
    "propose": "propose", "tasklist": "tasklist", "verify": "verify", "pr": "pr",
    "status": "status", "archive": "archive", "land": "land", "clean": "clean",
    "tasks": "tasks.agent_tasks", "worker": "tasks.worker",
    "dashboard": "dashboard.server", "fleet-export": "fleet.fleet_export",
}
HELP = {"-h", "--help"}
# These read or repair a broken dev-loop.yaml themselves; every other command stops on it.
TOLERANT = {"doctor", "config", "init", "dashboard"}
SHELL_SIBLING = {"review": "pr-review.sh", "stack-rebase": "stack_rebase.sh"}


def unknown_flag(command: str, module: object, args: list[str]) -> str | None:
    """The first `--flag` in args that the module's source (and shell sibling) never names."""
    if not getattr(module, "__file__", None):
        return None
    path = Path(module.__file__)  # type: ignore[attr-defined]
    source = path.read_text(encoding="utf-8")
    if command in SHELL_SIBLING:
        source += path.with_name(SHELL_SIBLING[command]).read_text(encoding="utf-8")
    for arg in args:
        if arg == "--":
            break
        found = re.match(r"--[a-z][a-z0-9-]*", arg)
        if found and found[0] not in source:
            return found[0]
    return None


def shed_plugin_path() -> None:
    """Take the plugin's own `src` out of PYTHONPATH (the shim put it there) for every child.

    A git hook or a repo's own Python started from here must import its own packages, not ours;
    this process has its imports already, since sys.path was read at start-up.
    """
    own = str(Path(__file__).resolve().parent.parent)
    kept = [x for x in os.environ.get("PYTHONPATH", "").split(os.pathsep)
            if x and x != own]
    if kept:
        os.environ["PYTHONPATH"] = os.pathsep.join(kept)
    else:
        os.environ.pop("PYTHONPATH", None)


def version() -> int:
    """Print `dl <version>` from the plugin's plugin.json."""
    plugin = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).parents[2])
    try:
        found = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text("utf-8"))
        print(f"dl {found['version']}")
    except (OSError, ValueError, KeyError, TypeError):
        print(f"dl: no version in {plugin}/.claude-plugin/plugin.json", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    shed_plugin_path()
    args = sys.argv[1:] if argv is None else argv
    if args[:1] and args[0] in HELP:
        print(f"usage: dl <subcommand> [-h] | dl --version; one of {', '.join(sorted(TABLE))}")
        return 0
    if args[:1] == ["--version"]:
        return version()
    if not args or args[0] not in TABLE:
        print(f"dl: unknown subcommand; one of {', '.join(sorted(TABLE))}", file=sys.stderr)
        return 2
    name = f"dev_loop.{TABLE[args[0]]}"
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as error:
        if error.name not in (name, name.rpartition(".")[0]):
            raise
        module = None
    if module is None or not hasattr(module, "main"):
        print(f"dl {args[0]}: not built yet", file=sys.stderr)
        return 2
    if HELP & set(args[1:]):  # a help flag never acts
        print((module.__doc__ or f"dl {args[0]}").strip())
        return 0
    if (flag := unknown_flag(args[0], module, args[1:])) is not None:
        usage = (module.__doc__ or f"dl {args[0]}").strip().splitlines()[0]
        print(f"dl {args[0]}: unknown option {flag}\nusage: {usage}", file=sys.stderr)
        return 2
    if args[0] not in TOLERANT:
        try:
            required(project_root())  # a file that exists and fails: one line, exit 1
        except ValueError:
            pass  # outside a project: the command says so itself
    return int(module.main(args[1:]))


if __name__ == "__main__":
    sys.exit(main())
