"""The dev loop's config: shipped defaults, the project's dev-loop.yaml over them.

Interface:
    project_root() -> Path   # CLAUDE_PROJECT_DIR, else git's top level; never this file's place
    load(root: Path) -> Loaded   # Loaded(values, sources, error, warning); Loaded.get(dotted)
    validate(config) -> None     # ValueError naming the dotted key (schema.py)
    route(root: Path, change: str, task: str) -> str   # ValueError: no route

CLI: dl config get <dotted> | set <dotted> <value> | route <change> <task> | migrate
     (a list prints one item a line; migrate: config_migrate.py, then the old dashboard hook)
Never prints prompt or document text.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from fnmatch import fnmatchcase
from itertools import takewhile
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # one clear line, never a traceback; a reader stops loud with it
    sys.exit("dev-loop config: PyYAML is missing: pip install pyyaml")

if __package__:  # dev_loop.config, as the dashboard imports it
    from . import schema
    from .schema import VersionError, validate
else:  # python scripts/dev_loop/config.py
    import schema  # type: ignore[no-redef]
    from schema import VersionError, validate

__all__ = [
    "DEFAULTS", "Loaded", "leaves", "load", "project_root", "required", "route", "validate",
]

DEFAULTS = Path(__file__).with_name("defaults.yaml")
PROJECT = "dev-loop.yaml"
# The PreToolUse HTTP hook the dashboard's --install-hook wrote; `migrate` removes it.
OLD_HOOK = re.compile(r"http://(127\.0\.0\.1|localhost):\d+/hook/pre-tool-use")


def project_root() -> Path:
    """Return the project: CLAUDE_PROJECT_DIR, else git's top level from the working directory.

    Never taken from `__file__`: the plugin is installed outside the project.
    """
    if env := os.environ.get("CLAUDE_PROJECT_DIR"):
        return Path(env)
    git = ["git", "rev-parse", "--show-toplevel"]
    done = subprocess.run(git, capture_output=True, text=True, check=False)
    if done.returncode:
        msg = "no CLAUDE_PROJECT_DIR and not inside a git repository"
        raise ValueError(msg)
    return Path(done.stdout.strip())


@dataclass(frozen=True)
class Loaded:
    """The merged config, each dotted leaf's source and why the project file was not used.

    A source is `project` or `default`; on an error the defaults were used. A warning:
    the file is at an older version, migrated in memory for this load.
    """

    values: dict[str, Any]
    sources: dict[str, str]
    error: str | None = None
    warning: str | None = None

    def get(self, dotted: str) -> Any:  # noqa: ANN401 - a parsed YAML value
        """Return the value at a dotted key; KeyError naming it when there is none."""
        node: Any = self.values
        for key in dotted.split("."):
            if not isinstance(node, dict) or key not in node:
                msg = f"{dotted}: unknown key"
                raise KeyError(msg)
            node = node[key]
        return node


def leaves(values: Any, prefix: str = "") -> dict[str, Any]:  # noqa: ANN401
    """Return the dotted leaves of a mapping; a list or a scalar is one leaf."""
    if not isinstance(values, dict) or (not values and prefix):
        return {prefix: values}
    out: dict[str, Any] = {}
    for key, value in values.items():
        out |= leaves(value, f"{prefix}.{key}" if prefix else str(key))
    return out


def merge(base: Any, over: Any) -> Any:  # noqa: ANN401
    """Return over on base: mappings merge key by key, anything else replaces."""
    if not (isinstance(base, dict) and isinstance(over, dict)):
        return over
    return base | {
        key: merge(base[key], value) if key in base else value for key, value in over.items()
    }


def loaded(values: dict[str, Any], project: dict[str, Any], *notes: str | None) -> Loaded:
    mine = leaves(project) if project else {}
    sources = {key: "project" if key in mine else "default" for key in leaves(values)}
    return Loaded(values, sources, *notes)


def load(root: Path) -> Loaded:
    """Return the defaults with root's dev-loop.yaml over them.

    A bad project file is reported in `error` and the defaults used, so the caps never go off;
    a newer one in one line. An older one is migrated in memory, with a `warning`.
    A bad defaults file raises.
    """
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    validate(defaults)
    path = root / PROJECT
    if not path.exists():
        return loaded(defaults, {})
    try:
        project = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(project, dict):
            msg = "the file holds no mapping"
            raise ValueError(msg)  # noqa: TRY004, TRY301 - one handler, one message shape
        project, warning = upgraded(project)
        merged = merge(defaults, project)
        validate(merged)
    except VersionError as error:
        return loaded(defaults, {}, str(error))
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        problem = getattr(error, "problem", None) or "cannot parse"
        return loaded(defaults, {}, f"{PROJECT}: bad YAML{where}: {problem}")
    except (OSError, ValueError, TypeError) as error:
        return loaded(defaults, {}, f"{PROJECT}: {type(error).__name__}: {error}")
    return loaded(merged, project, None, warning)


def required(root: Path) -> Loaded:
    """load(), or exit 1 with the one-line config error: no command acts on the defaults."""
    found = load(root)
    if found.error:
        sys.exit(f"dev-loop config: {found.error}")
    return found


def upgraded(project: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Return the project at the latest version; VersionError: newer than this plugin reads.

    config_migrate is imported for an older file only: a copied config.py and schema.py run.
    """
    if schema.version_of(project) == schema.SUPPORTED:
        return project, None
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script, too
    from dev_loop.config_migrate import upgrade  # noqa: PLC0415

    return upgrade(project)


def route(root: Path, change: str, task: str) -> str:
    """Return the agent type for one task of a change's tasks.md.

    Its Kind:, else the first glob matching a path the task names. ValueError: no route.
    """
    values = required(root).values
    config = values["routing"]
    tasks = root / values["paths"]["changes"] / change / "tasks.md"
    text = tasks.read_text(encoding="utf-8")
    try:
        from .tasklist import parse_blocks  # noqa: PLC0415 - tasklist imports this module
    except ImportError:  # loaded as a plain script (a hook), not as the package
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from dev_loop.tasklist import parse_blocks  # noqa: PLC0415

    if block := next((t for t in parse_blocks(text) if t["id"] == task), None):
        if block["kind"] in config["kinds"]:
            return str(config["kinds"][block["kind"]])
        return _by_glob(config, block["files"], task)
    lines = text.splitlines()
    head = re.compile(rf"- \[[ xX]\] {re.escape(task)}\b")
    start = next((i for i, line in enumerate(lines) if head.match(line)), None)
    if start is None:
        msg = f"no task {task} in {change}/tasks.md"
        raise ValueError(msg)
    if (kind := re.search(r"\bKind: (\w+)", lines[start])) and kind.group(1) in config["kinds"]:
        return str(config["kinds"][kind.group(1)])
    rest = takewhile(lambda line: line.startswith("  "), lines[start + 1 :])
    text = " ".join([lines[start], *rest])
    return _by_glob(config, re.findall(r"`([^`\s]+/[^`\s]*|[^`\s]+\.\w+)`", text), task)


def _by_glob(config: dict[str, Any], paths: list[str], task: str) -> str:
    for rule in config["paths"] if paths else []:
        glob = rule["glob"]
        tail = glob[3:] if glob.startswith("**/") else None
        if any(fnmatchcase(p, glob) or (tail and fnmatchcase(p, tail)) for p in paths):
            return str(config["kinds"][rule["kind"]])
    msg = f"no route for {task}: add Kind: backend|frontend"
    raise ValueError(msg)


def drop_old_hook(settings: dict[str, Any]) -> list[str]:
    """Remove the dashboard's HTTP hooks from PreToolUse and return their URLs.

    A group left empty by it goes too; every other hook and group stays as it was.
    """
    groups = settings.get("hooks", {}).get("PreToolUse", [])
    gone: list[str] = []
    kept = []
    for group in groups:
        hooks = group.get("hooks", [])
        old = [h for h in hooks if h.get("type") == "http" and OLD_HOOK.fullmatch(h.get("url", ""))]
        gone += [hook["url"] for hook in old]
        if old:
            group["hooks"] = [hook for hook in hooks if hook not in old]
        if not old or group["hooks"]:
            kept.append(group)
    groups[:] = kept
    return gone


def migrate(path: Path) -> str:
    """Drop the old hook from the user's settings; say what was done.

    Through store.merge_settings: a .bak kept, a file changed meanwhile refused. A second run
    writes nothing and says so.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script, too
    from dev_loop.dashboard.store import merge_settings, read_settings  # noqa: PLC0415

    if not drop_old_hook(read_settings(path)[1]):
        return f"migrate: no dashboard hook in {path}; nothing changed"
    gone: list[str] = []
    merge_settings(path, lambda settings: gone.extend(drop_old_hook(settings)))
    return f"migrate: removed the dashboard hook {', '.join(gone)} from {path} (old file: .bak)"


def migrated(path: Path) -> int:
    """Run `migrate` for the CLI: 0 done or nothing to do, 1 a file it would not touch."""
    try:
        say(migrate(path), error=False)
    except (OSError, ValueError, RuntimeError) as error:  # unparsable, or changed meanwhile
        say(f"dev-loop config: {error}")
        return 1
    return 0


def set_key(root: Path, dotted: str, text: str) -> int:
    """Write one key: `text` read as YAML (empty: the default), through the Settings writer.

    The writer validates the whole config; a refusal is one line on stderr, exit 1.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script, too
    from dev_loop.config_write import write  # noqa: PLC0415 - it imports this module

    try:
        write(root, {dotted: yaml.safe_load(text)})
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as error:
        say(f"dev-loop config: {error}")
        return 1
    say(f"{dotted} set", error=False)
    return 0


def say(line: str, *, error: bool = True) -> None:
    """Write one line: a value to stdout, a refusal to stderr (the CLI's only output)."""
    print(line, file=sys.stderr if error else sys.stdout)


def render(value: Any) -> str:  # noqa: ANN401
    """A list one item a line, a text bare, anything else as JSON, for shell callers."""
    if isinstance(value, list):
        return "\n".join(render(item) for item in value)
    return value if isinstance(value, str) else json.dumps(value)


def told(root: Path) -> Loaded:
    """Load the config or exit 1 on an error; say on stderr when it was read migrated."""
    config = required(root)
    if config.warning:
        say(f"dev-loop config: {config.warning}")
    return config


def main(argv: list[str]) -> int:
    try:
        root = project_root()
    except ValueError as error:
        say(f"dev-loop config: {error}")
        return 2
    if len(argv) == 2 and argv[0] == "get":  # noqa: PLR2004 - the command's arity
        config = told(root)
        try:
            if out := render(config.get(argv[1])):
                say(out, error=False)
        except KeyError as error:
            say(f"dev-loop config: {error.args[0]}")
            return 1
        return 0
    if len(argv) == 3 and argv[0] == "route":  # noqa: PLR2004 - the command's arity
        told(root)
        try:
            say(route(root, argv[1], argv[2]), error=False)
        except (OSError, ValueError) as error:
            say(f"dev-loop config: {error}")
            return 2
        return 0
    if len(argv) == 3 and argv[0] == "set":  # noqa: PLR2004 - the command's arity
        return set_key(root, argv[1], argv[2])
    if argv == ["migrate"]:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script, too
        from dev_loop.config_migrate import run  # noqa: PLC0415 - it imports config_write

        project = run(root)
        return max(project, migrated(
            Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
            / "settings.json"
        ))
    say("usage: dl config get <dotted> | set <dotted> <value> | route <change> <task> | migrate")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
