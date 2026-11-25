"""The context gate's saved floor: its file, one per checkout, and its key.

One file shared by every checkout was keyed by each one's own config files, so worktrees evicted
each other's floor and fell to the slow path. os and zlib alone: hashlib loads OpenSSL,
about 6 ms of the fast path; two checkouts whose crc32 clash only share a file, as before.
"""

# ruff: noqa: PTH100, PTH110, PTH116, PTH118 - pathlib costs the fast path its import
from __future__ import annotations

import os
import zlib


def floor_file(home: str, root: str) -> str:
    """Return the floor file of the checkout at root, under the gate's home folder."""
    path = os.path.normcase(os.path.abspath(root))
    return os.path.join(home, "floors", f"{zlib.crc32(path.encode()):08x}.json")


def config_key(plugin: str, root: str) -> list[list[int] | None]:
    """Return the shipped defaults' and the project file's mtime and size: the floor's key."""
    key: list[list[int] | None] = []
    defaults = os.path.join(plugin, "src", "dev_loop", "defaults.yaml")
    for path in (defaults, os.path.join(root, "dev-loop.yaml")):
        found = os.stat(path) if os.path.exists(path) else None
        key.append(None if found is None else [found.st_mtime_ns, found.st_size])
    return key
