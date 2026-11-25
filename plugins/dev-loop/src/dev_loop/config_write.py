"""The Settings page's config edits: view with sources, write and drift.

Interface:
    view(root: Path) -> dict      # path, values, defaults, sources, ranges, error, drift,
                                  # read_only (the model keys of roles with no agent file)
    write(root: Path, changes: dict[str, Any]) -> list[str]   # the keys the file holds
    drift(root: Path, loaded: Loaded) -> list[dict]           # agent files off their role's model

Beside config.py (the loader), which stays the one reader; schema.validate stays the one validator.
Keeps and returns keys, model names and config values only.
"""

from __future__ import annotations

import copy
import re
import threading
from typing import TYPE_CHECKING, Any

import yaml

from .config import DEFAULTS, PROJECT, leaves, load
from .dashboard import store
from .schema import IMPLICIT, bounds, validate

if TYPE_CHECKING:
    from pathlib import Path

    from .config import Loaded

MODEL_KEY = re.compile(r"roles\.([^.]+)\.model")
MODEL_LINE = re.compile(r"^model:[ \t]*([^\r\n]*?)[ \t]*\r?$", re.MULTILINE)
AGENT_NAME = re.compile(r"[\w-]+")  # never a path: `*` and plugin types have no file here
LOCK = threading.Lock()


class ConflictError(RuntimeError):
    """dev-loop.yaml changed since it was read: nothing written (a 409)."""


def shipped() -> dict[str, Any]:
    return dict(yaml.safe_load(DEFAULTS.read_text(encoding="utf-8")))


def view(root: Path) -> dict[str, Any]:
    """Return each dotted key's value, default, source and range, the error and the drift."""
    loaded = load(root)
    values = leaves(loaded.values)
    return {
        "path": str(root / PROJECT),
        "values": values,
        "defaults": leaves(shipped()),
        "sources": loaded.sources,
        "ranges": {key: span for key in values if (span := bounds(key))},
        "error": loaded.error,
        "drift": drift(root, loaded),
        "read_only": [
            f"roles.{name}.model"
            for name in sorted(loaded.values["roles"])
            if not agent_files(root, loaded.values["roles"][name])
        ],
    }


def agent_files(root: Path, role: dict[str, Any]) -> list[tuple[str, Path]]:
    """Return the role's agent types with a file under .claude/agents, and the file."""
    folder = root / ".claude" / "agents"
    named = [(kind, folder / f"{kind}.md") for kind in role["agent_types"]]
    return [(kind, path) for kind, path in named if AGENT_NAME.fullmatch(kind) and path.is_file()]


def model_line(text: str) -> re.Match[str] | None:
    """Return the frontmatter's `model:` line; never a line of the body."""
    end = text.find("\n---", 3) if text.startswith("---") else -1
    return MODEL_LINE.search(text, 0, end) if end > 0 else None


def drift(root: Path, loaded: Loaded) -> list[dict[str, Any]]:
    """Return each agent file whose `model:` differs from its role's model."""
    found = []
    for name, role in loaded.values["roles"].items():
        for kind, path in agent_files(root, role):
            line = model_line(path.read_bytes().decode("utf-8"))
            model = line and line.group(1)
            if model != role["model"]:
                found.append(
                    {"role": name, "agent_type": kind, "file": model, "config": role["model"]}
                )
    return found


def diff(values: Any, base: Any) -> Any:  # noqa: ANN401 - parsed YAML
    """Return the parts of values that differ from base, nested: the project file's shape."""
    if not (isinstance(values, dict) and isinstance(base, dict)):
        return values
    return {
        key: diff(value, base[key]) if key in base else value
        for key, value in values.items()
        if key not in base or value != base[key]
    }


def put(values: dict[str, Any], defaults: dict[str, Any], dotted: str, value: object) -> None:
    """Set one dotted key; None returns it to its default. ValueError: an unknown key."""
    *parents, last = dotted.split(".")
    node: Any = values
    base: Any = defaults
    for key in parents:
        node = node.get(key) if isinstance(node, dict) else None
        base = base.get(key) if isinstance(base, dict) else None
    if not isinstance(node, dict) or last not in node:
        msg = f"{dotted}: unknown key"
        raise ValueError(msg)
    if value is not None:
        node[last] = value
    elif isinstance(base, dict) and last in base:
        node[last] = copy.deepcopy(base[last])
    else:  # a key only the project file holds
        del node[last]


def retargets(root: Path, values: dict[str, Any], dotted: str) -> list[tuple[Path, str]]:
    """Return the agent files a model change rewrites; ValueError: a role with none."""
    found = MODEL_KEY.fullmatch(dotted)
    if not found:
        return []
    role = values["roles"][found.group(1)]
    files = agent_files(root, role)
    if not files:
        msg = f"{dotted}: read-only, the role has no agent file"
        raise ValueError(msg)
    if role["model"] is None:
        msg = f"{dotted}: a model name, the role's agent files need one"
        raise ValueError(msg)
    return [(path, role["model"]) for _, path in files]


def rewrite(path: Path, model: str) -> None:
    """Rewrite the frontmatter's model: line, every other byte kept; OSError names the file."""
    text = path.read_bytes().decode("utf-8")
    line = model_line(text)
    if line is None:
        msg = f"{path.name}: no model: line in its frontmatter"
        raise ValueError(msg)
    if line.group(1) == model:
        return
    try:
        store.write_durable(path, text[: line.start(1)] + model + text[line.end(1) :])
    except OSError as error:  # a 500 naming the file; drift shows it until fixed
        msg = f"{path}: {error}"
        raise OSError(msg) from error


def text_of(path: Path) -> str | None:
    return path.read_bytes().decode("utf-8") if path.exists() else None


def write(root: Path, changes: dict[str, Any]) -> list[str]:
    """Set each dotted key (None: its default), validate the whole, then write.

    The agent files a model change names first, then dev-loop.yaml with only the keys that differ
    from the defaults, each durably, the old text kept as .bak. ValueError: a refused key or
    value, nothing written; ConflictError: the file changed since it was read.
    Returns the dotted keys the file now holds.
    """
    path = root / PROJECT
    with LOCK:  # one write at a time; the guard below catches any other writer
        raw, loaded, defaults = text_of(path), load(root), shipped()
        if loaded.error:
            raise ValueError(loaded.error)
        values = copy.deepcopy(loaded.values)
        for dotted, value in changes.items():
            put(values, defaults, dotted, value)
        validate(values)
        targets = [target for dotted in changes for target in retargets(root, values, dotted)]

        def unchanged() -> None:
            if text_of(path) != raw:
                msg = f"{PROJECT} changed since it was read; nothing written, reload and try again"
                raise ConflictError(msg)

        for agent, model in targets:
            rewrite(agent, model)
        project = diff(values, defaults)
        if values["version"] != IMPLICIT:  # a file with no version reads as version 1
            project = {"version": values["version"]} | project
        if raw is not None:
            store.write_durable(path.with_name(f"{PROJECT}.bak"), raw)
        store.write_durable(path, yaml.safe_dump(project, sort_keys=False), unchanged)
    return sorted(leaves(project))
