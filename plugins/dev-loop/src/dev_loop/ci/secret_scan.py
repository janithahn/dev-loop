"""`dl ci secret-scan`: token shapes in tracked files, named by path:line and pattern, never shown.

Stdlib only, so a release script can run it as a plain file. A line carrying the comment
`secret-scan: ignore` is allowed. Exit 1 on any hit.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

LONG = r"[A-Za-z0-9_+/=-]{16,}"
PATTERNS = {
    "github token": re.compile(r"\bghp_[A-Za-z0-9]{36}"),
    "github fine-grained token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    "grafana token": re.compile(r"\bglc_[A-Za-z0-9+/=_-]{20,}"),
    "slack token": re.compile(r"\bxox[bp]-[A-Za-z0-9-]{10,}"),
    "aws access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key block": re.compile(r"-----BEGIN ([A-Z]+ )*PRIVATE KEY-----"),
    "basic auth header": re.compile(r"Authorization:\s*Basic\s+[A-Za-z0-9+/=]{16,}", re.I),
    "password literal": re.compile(rf"\bpassword\s*=\s*[\"']?{LONG}", re.I),
    "anthropic key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "azure devops pat": re.compile(r"(?<![A-Za-z0-9])pat\b\s*[:=]\s*[\"']?[a-z2-7]{52}\b", re.I),
    "key literal": re.compile(
        rf"\b(?:api[_-]?key|secret|token|password)\s*:\s*[\"']?{LONG}", re.I),
    "token literal": re.compile(rf"\btoken\s*=\s*[\"']?{LONG}", re.I),
}
IGNORE = "secret-scan: ignore"


def scan(root: Path) -> int:
    """Print `path:line: pattern` for every hit in root's tracked files; 1 on any hit."""
    files = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True,  # noqa: S607
                           check=True).stdout.decode().split("\0")
    hits = 0
    for name in filter(None, files):
        try:
            data = (root / name).read_bytes()
        except OSError:
            continue  # gone from the working tree
        try:
            lines = data.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            if b"\0" in data and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue  # binary
            print(f"{name}: not scanned (not UTF-8)")
            hits += 1
            continue
        for number, line in enumerate(lines, 1):
            if IGNORE in line:
                continue
            for pattern, regex in PATTERNS.items():
                if regex.search(line):
                    print(f"{name}:{number}: {pattern}")
                    hits += 1
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(scan(Path.cwd()))
