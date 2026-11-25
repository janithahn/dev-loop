"""Fixture transcripts for the dev-loop dashboard's self-checks.

Synthetic JSONL in Claude Code's shapes, written to a temporary projects folder: never a real
transcript. The self-checks `test_dev_loop_*.py` import it.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from dev_loop.dashboard.scan import Scanner, Transcript

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

SESSION = "0b6c7c1e-2f43-4c55-9a51-1d2e3f4a5b6c"
AGENT = "a0123456789abcdef"
PROJECT = "C--Projects-app"
BRANCH = "feat/page-ui"  # the main checkout's branch when the session started
# Text the user's files and prompts hold: none of it may ever come out.
SECRET = "SECRET-PARTY-EXAMPLE-4471"
NOW = time.time()
UUIDS = itertools.count()
MINUTE, DAY, MILLION = 60, 86400, 1_000_000
Record = dict[str, object]
Tokens = tuple[int, int, int, int, int]  # input, output, 5-minute write, 1-hour write, read


def stamp(seconds_ago: float = 0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(NOW - seconds_ago))


def usage(tokens: Tokens) -> Record:
    """Return the usage block as Claude Code writes it."""
    tin, out, w5, w1, read = tokens
    split = {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}
    return {
        "input_tokens": tin,
        "output_tokens": out,
        "cache_creation_input_tokens": w5 + w1,
        "cache_read_input_tokens": read,
        "cache_creation": split,
    }


def assistant(
    mid: str,
    stop: str | None = "tool_use",
    *,
    tool: str = "",
    model: str = "",
    tokens: Tokens = (1, 10, 0, 0, 0),
    at: str = "",
) -> Record:
    block: Record = {"type": "text", "text": f"model text {SECRET}"}
    if stop == "end_turn":
        block = {"type": "thinking", "thinking": f"about {SECRET}"}
    if tool:
        block = {
            "type": "tool_use",
            "id": "toolu_1",
            "name": tool,
            "input": {"command": f"cat {SECRET}.pdf"},
        }
    message = {
        "id": mid,
        "model": model or "claude-opus-5-5",
        "role": "assistant",
        "stop_reason": stop,
        "content": [block],
        "usage": usage(tokens),
    }
    return {
        "type": "assistant",
        "timestamp": at or stamp(),
        "message": message,
        "uuid": f"u-{next(UUIDS)}",
        "gitBranch": BRANCH,
        "sessionId": SESSION,
    }


def user(content: object = None) -> Record:
    """Return a prompt the owner typed: the model answers it."""
    body = content if content is not None else f"prompt: screen {SECRET}"
    message = {"role": "user", "content": body}
    return {"type": "user", "timestamp": stamp(), "message": message, "promptSource": "user"}


def tool_result() -> Record:
    block = {"type": "tool_result", "tool_use_id": "toolu_1", "content": f"doc {SECRET}"}
    message = {"role": "user", "content": [block]}
    return {"type": "user", "message": message, "toolUseResult": {"stdout": f"doc {SECRET}"}}


def bare_user(text: str) -> Record:
    """Return a user record with only its promptId: an interrupt, a local command or a prompt."""
    return {"type": "user", "message": {"role": "user", "content": text}, "promptId": "p-1"}


def line(record: Record) -> str:
    """Return one record as Claude Code writes it: compact JSON."""
    return json.dumps(record, separators=(",", ":"))


class Tree:
    """A temporary projects folder holding fixture transcripts."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(
        self,
        records: Sequence[Record | str],
        name: str = SESSION,
        *,
        age: float = 0,
        agent: str = "",
        project: str = PROJECT,
    ) -> Path:
        folder = self.root / project
        if agent:
            folder, name = folder / name / "subagents", f"agent-{agent}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.writelines((r if isinstance(r, str) else line(r)) + "\n" for r in records)
        os.utime(path, (NOW - age, NOW - age))
        return path

    def scan(self, days: float = 7, scanner: Scanner | None = None) -> list[Transcript]:
        return list((scanner or Scanner("app", days, self.root)).scan())

    def one(
        self,
        records: Sequence[Record | str],
        name: str = SESSION,
        *,
        age: float = 0,
        agent: str = "",
    ) -> Transcript:
        path = self.write(records, name, age=age, agent=agent)
        (found,) = (t for t in self.scan() if t.path == path)
        return found

    def status(
        self,
        records: Sequence[Record | str],
        name: str = SESSION,
        *,
        age: float = 0,
        agent: str = "",
    ) -> str:
        return self.one(records, name, age=age, agent=agent).status(NOW)


def scanned(tree: Tree) -> Scanner:
    """Return a scanner after one whole pass over the tree."""
    scanner = Scanner("app", 7, tree.root)
    scanner.refresh()
    return scanner


def run(label: str, checks: Sequence[Callable[[Tree], None]]) -> None:
    """Run each check in its own temporary tree; print the verdict pr-gates reads."""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    for check in checks:
        with tempfile.TemporaryDirectory() as tmp:
            check(Tree(Path(tmp)))
    print(f"{label}: {len(checks)} checks, ok")  # noqa: T201 - the self-check's verdict
