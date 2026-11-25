"""The transcript scanner: exact tokens, API-equivalent cost, status and context per agent.

Kept: counts, times, ids, model names, tool
names and subagent descriptions; never prompt, tool input or output, thinking or document text.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import tail

if TYPE_CHECKING:
    from collections.abc import Iterator

PROJECTS = Path.home() / ".claude" / "projects"
CONTEXT_WINDOW = 1_000_000
STALL_SECONDS = 60 * 60  # a pending turn with no write this long is stalled, not running
IDLE_SECONDS = 15 * 60  # a session that answered this recently is idle; later, stopped
# $ per million tokens: input, output, cache read (claude-api skill, cached 2026-10-06). A cache
# write costs 1.25x input (5 minutes) or 2x (1 hour). An unlisted model costs as the dearest.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-fable-5-1": (10.0, 50.0, 0.25),
}
CHUNK = 8 * 1024 * 1024
RESTART = threading.Event()  # set by the page's Restart: the next pass re-reads every transcript
USER, TITLE = b'"type":"user"', b'Title":'  # with tail.USAGE: a line with none is skipped
# Claude Code's own user records: an interrupt, a local command's output. They end the turn; any
# other user record (a prompt, a tool result, a subagent's first prompt) is one the model answers.
ENDS_TURN = ("[Request interrupted by user", "<local-command", "<command-name>")
TAIL_BLOCK, TAIL_LIMIT = tail.TAIL_BLOCK, tail.TAIL_LIMIT  # last_call's, as the hook's
# A launch's budget, its prompt's first line; past nine digits it is none, not a crash.
STATED = re.compile(
    r"dev-loop budget:(?: role=(?P<role>[a-z0-9:_-]+))?"
    r"(?: context=(?P<context>\d{1,9})(?P<k>k)?)?"
)


@dataclass(frozen=True)
class Call:
    """One model call's usage: the last record of its message id."""

    at: float
    model: str
    input: int
    output: int
    write_5m: int
    write_1h: int
    read: int

    @property
    def usd(self) -> float:
        """API-equivalent cost at list price: what the same tokens cost on the API, not a bill."""
        price_in, price_out, price_read = PRICES.get(self.model, max(PRICES.values()))
        prompt = self.input + self.write_5m * 1.25 + self.write_1h * 2
        return (price_in * prompt + price_out * self.output + price_read * self.read) / 1e6

    @property
    def context(self) -> int:
        """The call's prompt: input + cache write + cache read, against CONTEXT_WINDOW."""
        return self.input + self.write_5m + self.write_1h + self.read


def ends_turn(record: dict[str, Any]) -> bool:
    """Return whether a user record is Claude Code's own: its first text is a fixed marker."""
    content = record["message"]["content"]
    first = content if isinstance(content, str) else content[0].get("text") if content else None
    return isinstance(first, str) and first.startswith(ENDS_TURN)


def call_of(record: dict[str, Any]) -> Call:
    """Validate one assistant record's usage; a missing or bad count raises, never defaults."""
    at, model, counts = tail.call_parts(record)
    return Call(at, model, *counts)


def last_call(path: Path | str, tail_bytes: int = TAIL_LIMIT) -> int | None:
    """Return the context of the transcript's last complete assistant call.

    Read back from the end in TAIL_BLOCK blocks, never past `tail_bytes`. The text after the last
    newline is still being written and a line cut by the bound is never parsed. No call yet, or no
    file: None.
    """
    record = tail.last_assistant(path, tail_bytes, TAIL_BLOCK)
    return None if record is None else call_of(record).context


def first_line(path: Path | str) -> str:
    """Return the first line of the agent's prompt, its first record: the hook's and the page's."""
    with Path(path).open("rb") as handle:
        raw = handle.readline(1 << 20)
    record = json.loads(raw) if raw.endswith(b"\n") else {}
    content = record.get("message", {}).get("content") if record.get("type") == "user" else None
    if isinstance(content, list):  # text blocks: the first one's
        content = next((b.get("text") for b in content if isinstance(b, dict)), None)
    return content.partition("\n")[0] if isinstance(content, str) else ""


def stated_budget(first_line: str) -> tuple[str | None, int | None]:
    """Return the role and context a launch states on its prompt's first line.

    The line is `dev-loop budget: role=<r> context=<n>[k]`; nothing else is kept.
    """
    found = STATED.fullmatch(first_line.partition("\n")[0].rstrip("\r"))
    if not found:
        return None, None
    context = None if found["context"] is None else int(found["context"])
    if context is not None and found["k"]:
        context *= 1000
    return found["role"], context


class Transcript:
    """One transcript file (a session, or a subagent under `subagents/`), read incrementally."""

    def __init__(self, path: Path) -> None:
        self.path, subagent = path, path.parent.name == "subagents"
        self.session_id = path.parent.parent.name if subagent else path.stem
        self.agent_id = path.stem.removeprefix("agent-") if subagent else None
        meta_file = path.with_suffix(".meta.json")
        meta = json.loads(meta_file.read_text("utf-8")) if meta_file.exists() else {}
        self.agent_type: str | None = meta.get("agentType")  # a meta not an object raises
        self.description: str | None = meta.get("description")
        self.calls: dict[str, Call] = {}  # by message id
        self.turn = "none"  # the last turn: a stop reason, "streaming", "user" or "ended"
        self.last_tool: str | None = None
        self.titles: dict[str, str] = {}  # by record type: custom-title, ai-title (latest)
        self.branch: str | None = None  # gitBranch: the checkout's branch, a last resort
        self.context = self.unreadable = self.offset = 0  # unreadable: bad records, counted
        self.written = 0.0  # the file's mtime at the last read
        self.started: float | None = None  # epoch seconds of the first timestamped record

    def read_new(self, written: float, lock: threading.RLock) -> None:
        """Read the complete lines appended since the last read; a half line waits."""
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            pending = b""
            while chunk := handle.read(CHUNK):
                pending += chunk
                end = pending.rfind(b"\n") + 1
                with lock:  # a reader sees a chunk whole or not at all: no half dedupe
                    for raw in pending[:end].split(b"\n")[:-1]:
                        self.line(raw)
                    self.offset, self.written = self.offset + end, written
                pending = pending[end:]

    def line(self, raw: bytes) -> None:
        """Apply one line; a record that is not well formed is counted, never guessed at."""
        if tail.USAGE in raw or USER in raw or TITLE in raw:
            try:
                record = json.loads(raw)
                at = record.get("timestamp") if self.started is None else None
                started = self.started if at is None else datetime.fromisoformat(at).timestamp()
                self.apply(record)  # a bad stamp raises first: the record changes nothing
                self.started = started
            except Exception:  # noqa: BLE001 - one bad record is counted, never its file
                self.unreadable += 1

    def apply(self, record: dict[str, Any]) -> None:
        """Apply one record by its top-level type; it raises before changing anything."""
        kind = record.get("type")
        if kind in ("custom-title", "ai-title"):
            name = record["customTitle" if kind == "custom-title" else "aiTitle"]
            if not isinstance(name, str) or not name:
                msg = "a session title is not a name"
                raise TypeError(msg)
            self.titles[kind] = name
        if kind == "user" and not (record.get("isMeta") or record.get("isVisibleInTranscriptOnly")):
            self.turn = "ended" if ends_turn(record) else "user"
        if kind != "assistant":
            return
        call, message = call_of(record), record["message"]
        blocks = message.get("content") if isinstance(message.get("content"), list) else []
        tools = [b["name"] for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
        self.calls[message.get("id") or record["uuid"]] = call
        self.branch = record.get("gitBranch") or self.branch
        self.context = call.context or self.context
        self.turn = message.get("stop_reason") or "streaming"
        self.last_tool = tools[-1] if tools else self.last_tool

    @property
    def who(self) -> str | None:
        """Return the session's name: its custom title, else its latest AI title, else branch."""
        return self.titles.get("custom-title") or self.titles.get("ai-title") or self.branch

    def status(self, now: float) -> str:
        """Return running, stalled, done (subagent), idle or stopped (session), from state."""
        quiet = now - self.written
        if self.turn in {"tool_use", "streaming", "user"}:  # a turn still being worked on
            return "stalled" if quiet > STALL_SECONDS else "running"
        if self.agent_id:
            return "done"
        return "idle" if quiet < IDLE_SECONDS else "stopped"


class Scanner:
    """Every transcript of one project written in the last `days` days."""

    def __init__(self, project: str, days: float = 7, root: Path = PROJECTS) -> None:
        self.project, self.days, self.root = project.lower(), days, root
        self.transcripts: dict[Path, Transcript] = {}
        self.lock = threading.RLock()  # held to change the transcripts and to read them whole
        self.passing = threading.Lock()  # one pass at a time: two would double an offset
        self.dropped: dict[Path, str] = {}  # this pass's unreadable files: path, error type
        self.scanned_at = 0.0  # when the last pass completed; 0: none has
        self.scan_error: str | None = None  # why the last pass failed; None when it did not

    def refresh(self) -> None:
        """Run one whole pass. It never raises: a failure is recorded and the next pass runs."""
        try:
            with self.passing:
                for _ in self.scan():
                    pass
                if RESTART.is_set():  # abandoned at a file: never "complete" on a partial index
                    return
        except Exception as error:  # noqa: BLE001 - a failed pass must never end the scan loop
            self.scan_error = f"{type(error).__name__}: {error}"
            return
        self.scanned_at, self.scan_error = time.time(), None

    def scan(self) -> Iterator[Transcript]:
        """Read each file's new bytes and yield it, newest first; a file out of the window drops."""
        if RESTART.is_set():
            RESTART.clear()
            with self.lock:  # spend is unknown until a whole pass: the gate enforces pauses only
                self.transcripts, self.scanned_at = {}, 0.0
        found: list[tuple[float, int, Path]] = []
        dropped: dict[Path, str] = {}
        for folder in self.root.iterdir() if self.root.is_dir() else []:
            if self.project in folder.name.lower():
                for path in [*folder.glob("*.jsonl"), *folder.glob("*/subagents/agent-*.jsonl")]:
                    try:
                        found.append(((stat := path.stat()).st_mtime, stat.st_size, path))
                    except OSError as error:  # removed since the listing: dropped, recorded
                        dropped[path] = type(error).__name__
        found = sorted((f for f in found if f[0] >= time.time() - self.days * 86400), reverse=True)
        kept = {f[2] for f in found}  # new maps, swapped in whole: no reader sees them change
        self.transcripts = {p: t for p, t in self.transcripts.items() if p in kept}
        self.dropped = dropped
        for written, size, path in found:
            if RESTART.is_set():
                return  # the next pass starts over
            known = self.transcripts.get(path)
            try:
                if known is None or size < known.offset:  # new, or rewritten shorter: whole
                    known = Transcript(path)
                    with self.lock:
                        self.transcripts[path] = known
                known.read_new(written, self.lock)
            except Exception as error:  # noqa: BLE001 - one bad file drops out, never the pass
                with self.lock:
                    self.transcripts.pop(path, None)
                    self.dropped[path] = type(error).__name__
                continue
            yield known
