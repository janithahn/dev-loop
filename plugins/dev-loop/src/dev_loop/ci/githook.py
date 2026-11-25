"""`dl githook pre-commit|pre-push`: run the plugin's hook body and return its exit code.

A project's `.githooks/<name>` (written by `dl init`) finds `dl` and runs `dl githook <name>`; the
bodies live in hooks/githooks/ of the plugin, so a plugin update changes them without touching
the project.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BODIES = Path(__file__).resolve().parents[3] / "hooks" / "githooks"
NAMES = ("pre-commit", "pre-push")


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in NAMES:
        print(f"usage: dl githook {'|'.join(NAMES)}", file=sys.stderr)
        return 2
    env = {"DL_PYTHON": sys.executable, **os.environ}  # the bodies run the plugin's own python
    return subprocess.call(["bash", str(BODIES / argv[0])], env=env)  # noqa: S603, S607 - our own body


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
