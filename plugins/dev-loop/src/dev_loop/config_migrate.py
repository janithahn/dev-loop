"""Config versions: the migrations from each version to the next, and `dl config migrate`.

Interface:
    MIGRATIONS: dict[int, Migration]   # from_version -> turns that version's mapping into the next
    upgrade(project) -> (project, warning | None)   # in memory, for the load; VersionError: newer
    run(root: Path) -> int   # `dl config migrate`: rewrite dev-loop.yaml at the latest version

None is registered while 1 is the latest (schema.SUPPORTED). Within a version an additive optional
key with a default goes into the schema maps and the snapshot together; a rename, removal or kind
change raises SUPPORTED, adds MIGRATIONS[old] and snapshots the new schema.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from . import schema

Migration = Callable[[dict[str, Any]], dict[str, Any]]
MIGRATIONS: dict[int, Migration] = {}
PROJECT = "dev-loop.yaml"


def upgrade(project: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Return the project mapping at schema.SUPPORTED and a warning when it was older."""
    version = schema.version_of(project)
    for step in range(version, schema.SUPPORTED):
        project = MIGRATIONS[step](project) | {"version": step + 1}
    if version == schema.SUPPORTED:
        return project, None
    return project, (
        f"{PROJECT} is version {version}; read as version {schema.SUPPORTED} for this run: "
        "`dl config migrate` rewrites it"
    )


def rewrite(root: Path) -> str:
    """Rewrite root's dev-loop.yaml at the latest version through the Settings writer.

    The writer validates, keeps the old text as .bak and refuses a file changed meanwhile; it
    writes YAML afresh, so comments do not survive a rewrite (a file at the latest is not touched).
    """
    from .config_write import write  # noqa: PLC0415 - the writer and its store: a rewrite only

    path = root / PROJECT
    if not path.exists():
        return f"migrate: no {PROJECT}; nothing to migrate"
    project = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    version = schema.version_of(project) if isinstance(project, dict) else None
    if version == schema.SUPPORTED:
        return f"migrate: {PROJECT} is at version {version}, the latest; nothing changed"
    write(root, {})
    return (
        f"migrate: {PROJECT} rewritten from version {version} to {schema.SUPPORTED} "
        f"(old file: {PROJECT}.bak)"
    )


def run(root: Path) -> int:
    """`dl config migrate` for the project: 0 done or nothing to do, 1 refused (one line)."""
    try:
        print(rewrite(root))
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as error:
        print(f"dev-loop config: {error}", file=sys.stderr)
        return 1
    return 0
