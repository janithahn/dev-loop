"""What every Python hook shares: the plugin venv's packages on the path, and its crash said.

The hooks run the system `python3`; `${CLAUDE_PLUGIN_DATA}/venv` (made by bin/dl-bootstrap) holds
PyYAML. The venv may have been built on another Python 3 (`uv venv` picks its own), so every
lib/python3.*/site-packages is added, and a Windows venv's Lib/site-packages. No venv: the
system's site-packages (site.main() when the hook runs as `python -S`). A venv that is there but
holds no PyYAML gets no fallback: the hook fails open and loud.

A hook's own crash, SystemExit included, fails open and loud (crashed): one stderr line and one
line in ~/.claude/dev-loop/hook-errors.jsonl: the time, the hook, the error class and the hook's
innermost own frame. Never prompt, tool or document text. errors_since counts those lines and the
context gate's own log by hook and error class, for `dl doctor` and the session-state hook.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
LOG_MAX = 1 << 20  # past it the log moves to hook-errors.1.jsonl
WORD_MAX = 64  # a hook or error-class name read back from a log is no longer
NO_YAML = "dev-loop config: PyYAML is missing: pip install pyyaml"  # config.py's own exit line


def errors_file() -> Path:
    return Path.home() / ".claude" / "dev-loop" / "hook-errors.jsonl"


def crashed(hook: str, error: BaseException, outcome: str) -> None:
    """Say the hook's own crash in one stderr line and log it; a log that fails only skips it."""
    said = f"{hook}: {type(error).__name__}, {outcome}"
    if isinstance(error, SystemExit) and error.code == NO_YAML:
        said += f" ({NO_YAML})"  # the one exit message echoed: another may quote a config value
    print(said, file=sys.stderr)  # noqa: T201 - a hook's stderr line
    with contextlib.suppress(Exception):  # whatever the log does, the hook ends as it would have
        log(hook, error)


def log(hook: str, error: BaseException) -> None:
    import traceback  # noqa: PLC0415 - only a crash pays for it

    frames = traceback.extract_tb(error.__traceback__)
    ours = [frame for frame in frames if Path(frame.filename).resolve().is_relative_to(PLUGIN)]
    frame = f"{Path(ours[-1].filename).stem}:{ours[-1].name}" if ours else None
    row = {"at": time.time(), "hook": hook, "error": type(error).__name__, "frame": frame}
    path = errors_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > LOG_MAX:
        path.replace(path.with_name("hook-errors.1.jsonl"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def word(value: object) -> str:
    """Return a hook or error-class name as logged, or `unknown`: a log line is never trusted."""
    if isinstance(value, str) and value.isascii() and len(value) <= WORD_MAX:
        return value if value.replace("-", "_").isidentifier() else "unknown"
    return "unknown"


def errors_since(since: float) -> dict[tuple[str, str], int]:
    """Count the errors logged after `since`, by (hook, error class): names and counts only.

    gate-errors.jsonl holds every error of the context gate; its crashes are in hook-errors.jsonl
    too, so the gate's lines there are skipped and each error counts once.
    """
    found: dict[tuple[str, str], int] = {}
    for name in ("hook-errors.jsonl", "gate-errors.jsonl"):
        path = errors_file().with_name(name)
        try:
            lines = path.read_text("utf-8").splitlines() if path.stat().st_mtime > since else []
        except (OSError, ValueError):
            continue
        for line in lines:
            # a cut or odd line is skipped
            with contextlib.suppress(ValueError, TypeError, LookupError, AttributeError):
                row = json.loads(line)
                gate = name == "gate-errors.jsonl"
                if float(row["at"]) <= since or (row.get("hook") == "context-gate" and not gate):
                    continue
                key = ("context-gate" if gate else word(row.get("hook")), word(row.get("error")))
                found[key] = found.get(key, 0) + 1
    return found


def add_venv() -> None:
    """Put the plugin venv's site-packages on sys.path; with no venv, the system's."""
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    venv = Path(data, "venv") if data else None
    if venv is not None and venv.is_dir():
        found = [*sorted(venv.glob("lib/python3.*/site-packages")), venv / "Lib" / "site-packages"]
        sys.path += [str(path) for path in found if path.is_dir()]
    elif sys.flags.no_site:
        __import__("site").main()
