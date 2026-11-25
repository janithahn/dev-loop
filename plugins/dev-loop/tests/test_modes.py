"""Every script a user or hook may run directly is executable in git (Windows commits drop the bit)."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _modes() -> dict[str, str]:
    out = subprocess.run(["git", "ls-files", "-s"], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout
    return {line.split("\t")[1]: line.split()[0] for line in out.splitlines()}


def test_scripts_are_executable() -> None:
    modes = _modes()
    scripts = [path for path in modes
               if path.startswith("plugins/dev-loop/bin/") or path.endswith(".sh")
               or "/githooks/" in path]
    assert scripts
    assert [path for path in scripts if modes[path] != "100755"] == []
