"""The doctor's "agent models" row: a configured role model against the agent that runs.

The host obeys an agent file's `model:` frontmatter, so a project's `roles.<role>.model` that
differs from it is drift. A bare type is the project's `.claude/agents/<name>.md` (red: the
project can edit it; an alias or `inherit` there matches its role's family). A `dev-loop:<name>`
type is the plugin's file, which a project cannot edit: drift there is a `warn:` row (green in
the table's exit code, so `dl worker` is not stopped on a valid config).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dev_loop.config_write import agent_files, model_line

if TYPE_CHECKING:
    from pathlib import Path

    from dev_loop import config

Row = tuple[str, bool, str]
WARN = "warn: "


def _model_of(path: Path) -> str | None:
    try:
        line = model_line(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    return line.group(1) if line else None


def _agrees(have: str, want: str) -> bool:
    """Equal, `inherit`, or an alias (`opus`) naming the family of the full id."""
    return have in {want, "inherit"} or f"-{have}-" in f"-{want}-"


def row(root: Path, loaded: config.Loaded, plugin: Path) -> Row:
    red, warn = [], []
    for role in loaded.get("roles").values():
        want = role.get("model")
        if not want:
            continue
        for kind, path in agent_files(root, role):
            have = _model_of(path)
            if have and not _agrees(have, want):
                red.append(f"{kind} runs {have}, config says {want}")
        for agent_type in role["agent_types"]:
            name = agent_type.removeprefix("dev-loop:")
            have = _model_of(plugin / "agents" / f"{name}.md") if name != agent_type else None
            if have and have != want:
                warn.append(f"{name} runs {have}, config says {want}")
    if red:
        return ("agent models", False, "; ".join(red))
    if warn:
        return ("agent models", True, WARN + "; ".join(warn))
    return ("agent models", True, "agents match the configured models")
