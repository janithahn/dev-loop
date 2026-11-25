"""Host adapters: where pull requests live. `adapter()` is the one place that picks one."""

from __future__ import annotations

import importlib
from types import ModuleType


class HostError(Exception):
    """A host call failed. Carries the HTTP status (0: none), never the response body."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def adapter() -> ModuleType:
    """Return the host module `host.provider` names (ado or github); refuse a bad config."""
    from dev_loop import config  # noqa: PLC0415 - config is not needed to raise a HostError

    loaded = config.load(config.project_root())
    if loaded.error:
        raise HostError(loaded.error)
    return importlib.import_module(f"dev_loop.hosts.{loaded.get('host.provider')}")
