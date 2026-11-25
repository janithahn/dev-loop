"""`dl stack-rebase [<old base>]`: run stack_rebase.sh beside this file and return its exit code."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("stack_rebase.sh")


def main(argv: list[str]) -> int:
    env = {"DL_PYTHON": sys.executable, **os.environ}  # the scripts run the plugin's own python
    return subprocess.call(["bash", str(SCRIPT), *argv], env=env)  # noqa: S603, S607 - our own script
