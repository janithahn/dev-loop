"""A transcript's last model call, read with json, os and datetime alone.

`scan` loads dataclasses, pathlib and threading; the context gate's fast path imports this instead.
Kept: counts, a time and a model name; never prompt, tool or document text.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Any

USAGE = b'"usage":{'  # a line without it is no model call and is never parsed
TAIL_BLOCK, TAIL_LIMIT = 1 << 20, 64 << 20  # read back in blocks, never past the limit


def last_assistant(
    path: str | os.PathLike[str], tail_bytes: int = TAIL_LIMIT, block: int = TAIL_BLOCK
) -> dict[str, Any] | None:
    """Return the transcript's last complete assistant record that carries a usage.

    Read back from the end in `block` bytes, never past `tail_bytes`. The text after the last
    newline is still being written and a line cut by the bound is never parsed. None: no file.
    """
    if not os.path.exists(path):  # noqa: PTH110 - pathlib costs the fast path its import
        return None
    with open(path, "rb") as handle:  # noqa: PTH123 - as above
        pos = handle.seek(0, 2)  # the end
        floor, carry, tail = max(0, pos - tail_bytes), b"", True  # tail: carry is unfinished
        while pos > floor:
            step = min(block, pos - floor)
            pos -= step
            handle.seek(pos)
            lines = (handle.read(step) + carry).split(b"\n")
            if tail and len(lines) > 1:
                lines.pop()
                tail = False
            carry = lines.pop(0) if pos > 0 or tail else b""  # a line's start may lie further back
            for raw in reversed(lines):
                record = json.loads(raw) if USAGE in raw else None
                if record and record.get("type") == "assistant":
                    return record  # type: ignore[no-any-return]
    return None


def call_parts(record: dict[str, Any]) -> tuple[float, str, list[int]]:
    """Return an assistant record's time, model and counts; a bad count raises, never defaults.

    The counts: input, output, 5-minute and 1-hour cache writes, cache read.
    """
    message, usage = record["message"], record["message"]["usage"]
    written = usage.get("cache_creation_input_tokens") or 0  # null or absent: nothing cached
    hour = (usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0
    counts = [usage["input_tokens"], usage["output_tokens"], written - hour, hour]
    counts.append(usage.get("cache_read_input_tokens") or 0)
    if not isinstance(message["model"], str) or any(type(n) is not int or n < 0 for n in counts):
        msg = "a usage count is not a whole number, or the model is not named"
        raise TypeError(msg)
    return datetime.fromisoformat(record["timestamp"]).timestamp(), message["model"], counts


def context_of(record: dict[str, Any]) -> int:
    """Return the call's prompt: every count but the output (scan.Call.context's sum)."""
    _at, _model, counts = call_parts(record)
    return sum(counts) - counts[1]
