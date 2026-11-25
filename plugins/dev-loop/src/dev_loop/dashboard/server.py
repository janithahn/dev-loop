"""The dashboard's HTTP server: state, controls, restart and the config; no enforcing.

Enforcement is `hooks/context-gate.py`'s. Binds 127.0.0.1 only. A
POST that changes anything needs `X-DL: 1`, which a page of another origin cannot send.
Run: dl dashboard
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from datetime import date, datetime
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dev_loop.config import load, project_root
from dev_loop.config_write import ConflictError, view, write

from .gate import STALE_SECONDS, Role, cap_of, role_for, roles
from .scan import CONTEXT_WINDOW, Scanner, first_line, stated_budget
from .spend import Totals, totals
from .store import Store, as_dict, edit, settings_view
from .workers import Workers

if TYPE_CHECKING:
    from .scan import Transcript

CONTROL = Path.home() / ".claude" / "dev-loop" / "control.json"  # beside the hook's files
HOOK_LOGS = ("gate-errors.1.jsonl", "gate-errors.jsonl")  # oldest first: the hook's rotation
SHOWN_ERRORS = 5
PORT = int(os.environ.get("DL_PORT", "8787"))
ROW_KEYS = (
    "session_id agent_id who agent_type description last_tool context unreadable written started"
)
LOCAL = ("127.0.0.1", "localhost")
STALE_PAGE = 30  # the page says its data is stale after this many seconds
GATE = "context-gate.py"  # the command hook whose registration hook_wired reports
PAGE = Path(__file__).parent / "page"
TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}


class Dashboard:
    """The scanner, the controls and the threads that keep them going."""

    def __init__(self, scanner: Scanner, store: Store, root: Path | None = None) -> None:
        self.scanner, self.store = scanner, store
        self.workers = Workers(scanner)
        self.cache: Totals | None = None
        self.root = root or project_root()  # dev-loop.yaml's folder

    def spend(self) -> Totals:
        """Return the totals of the last completed pass, summed once per pass."""
        if self.cache is None or self.cache.at != self.scanner.scanned_at:
            midnight = datetime.combine(date.today(), datetime.min.time())  # noqa: DTZ011 - local
            self.cache = totals(self.scanner, midnight.timestamp())
        return self.cache

    def restart(self) -> dict[str, Any]:
        """Re-read every transcript and revive dead threads."""
        return {"restarted": self.workers.restart(), "reindexing": True}

    def apply(self, body: dict[str, Any]) -> dict[str, Any]:
        """Apply one control request; a refused value raises ValueError and changes nothing."""
        self.store.change(lambda controls: edit(controls, body))
        return as_dict(self.store.controls)

    def save(self, body: dict[str, Any]) -> dict[str, Any]:
        """Write one config key (null: its default) and return the config view."""
        if set(body) != {"key", "value"} or not isinstance(body["key"], str):
            msg = "a body of {key: <dotted key>, value: <a value, or null for its default>}"
            raise ValueError(msg)
        write(self.root, {body["key"]: body["value"]})
        return view(self.root)

    def state(self) -> dict[str, Any]:
        """Return the page's view; its ages and errors say when it is stale, never looking live."""
        now, spent = time.time(), self.spend()
        age = now - spent.at if spent.at else None  # None: no pass has completed yet
        config = load(self.root)  # per request: an edit to dev-loop.yaml applies without a restart
        table, owner = roles(config.values), self.store.controls.context_caps
        with self.scanner.lock:
            found = list(self.scanner.transcripts.values())
            rows = [row(t, now, spent) | caps(t, table, owner) for t in found]
            error, dropped = self.scanner.scan_error, len(self.scanner.dropped)
        for session in (r for r in rows if not r["agent_id"]):  # running while an agent runs
            if any(r["status"] == "running" for r in rows if r["session_id"] == session["id"]):
                session["status"] = "running"
        flags = {
            "stale": age is None or age > STALE_PAGE,
            "pauses_only": age is None or age > STALE_SECONDS,
        }
        scan_view = {"now": now, "scanned_at": spent.at, "scan_age": age, "scan_error": error}
        spend = {"today_usd": spent.today_usd, "burn_usd_h": spent.burn_usd_h, "dropped": dropped}
        threads = {name: thread.is_alive() for name, thread in self.workers.threads.items()}
        extra = {"sessions": rows, "threads": threads, "window": CONTEXT_WINDOW}
        extra["config_error"] = config.error  # None: dev-loop.yaml loaded, else why the defaults
        return {
            **scan_view,
            **flags,
            **spend,
            **extra,
            "control": as_dict(self.store.controls),
            **settings_view(self.root / ".claude" / "settings.json", GATE),  # the project's
            **hook_files(self.store.path.parent),
        }


def row(transcript: Transcript, now: float, spent: Totals) -> dict[str, Any]:
    """Return one transcript's line for the page: ids, names, status, spend and context."""
    ident = transcript.agent_id or transcript.session_id
    kept = {key: getattr(transcript, key) for key in ROW_KEYS.split()}
    return kept | {"id": ident, "status": transcript.status(now), "usd": spent.usd.get(ident, 0.0)}


def caps(transcript: Transcript, table: list[Role], owner: dict[str, int]) -> dict[str, Any]:
    """Return an agent row's role and caps as the hook reads them.

    `context` is what the hook applies: the owner's cap, else the stated one clamped, else the
    role's default; `stated`, `default` and `clamped` say how it got there.
    """
    none = {"context": None, "stated": None, "default": None, "clamped": False}
    if not transcript.agent_id:  # a session: the coordinator, never capped
        return {"role": None, "caps": none}
    stated_role, stated = stated_of(transcript.path) if transcript.context else (None, None)
    role = role_for(table, transcript.agent_type, stated_role)
    if role is None or role.context is None:
        return {"role": role and role.name, "caps": none}
    within = cap_of(role, stated, None)
    view = {
        "context": cap_of(role, stated, owner.get(transcript.agent_id)),
        "stated": stated,
        "default": role.context[0],
        "clamped": stated is not None and within != stated,
    }
    return {"role": role.name, "caps": view}


@lru_cache(maxsize=4096)
def stated_of(path: Path) -> tuple[str | None, int | None]:
    """Return the budget a launch stated, read as the hook reads it; once: a prompt never changes.

    Read only after the agent's first call, so the first record is whole. Unreadable: none.
    # ponytail: the cache keeps a path read as unreadable; a Restart re-reads none of it
    """
    try:
        return stated_budget(first_line(path))
    except (OSError, ValueError, AttributeError):  # one bad file: none stated, never a 500
        return None, None


def hook_files(home: Path) -> dict[str, Any]:
    """Return the hook's open wrap-up windows and its errors: ids, counts and classes."""
    windows = {}
    for path in (home / "windows").glob("*"):
        with contextlib.suppress(FileNotFoundError):  # closed by the hook since the listing
            windows[path.name] = path.stat().st_size  # a byte per call judged
    errors = [entry for name in HOOK_LOGS for entry in error_lines(home / name)]
    shown = {"count": len(errors), "last": errors[-SHOWN_ERRORS:]}
    return {"windows": windows, "hook_errors": shown}


def error_lines(path: Path) -> list[dict[str, Any]]:
    """Return the log's well-formed lines, each cut to its time, error class and frame."""
    try:
        lines = path.read_bytes().splitlines()
    except FileNotFoundError:
        return []
    kept = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:  # a half-written or foreign line: skipped
            continue
        if isinstance(entry, dict):  # never any other key: no message, no text
            kept.append({key: entry.get(key) for key in ("at", "error", "frame")})
    return kept


class Handler(BaseHTTPRequestHandler):
    app: Dashboard

    def send_json(self, code: int, data: object) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def send_page(self, path: str) -> bool:
        """Send the page file the path names, by its name in the folder, never a joined path."""
        names = [p for p in PAGE.iterdir() if p.suffix in TYPES and ".config." not in p.name]
        name = "index.html" if path == "/" else path.removeprefix("/")
        found = {p.name: p for p in names}.get(name)
        if not found:
            return False
        body = found.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{TYPES[found.suffix]}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")  # an edited page is never served stale
        self.end_headers()
        self.wfile.write(body)
        return True

    def parse_request(self) -> bool:
        """Refuse a non-local Host: a rebound DNS name must not reach a state-changing POST."""
        if not super().parse_request():
            return False
        if self.headers.get("Host", "").rpartition(":")[0] in LOCAL:
            return True
        self.send_json(403, {"error": "unexpected Host header"})
        return False

    def do_GET(self) -> None:
        try:
            path = self.path.split("?")[0]
            if path == "/api/state":
                self.send_json(200, self.app.state())
            elif path == "/api/config":
                self.send_json(200, view(self.app.root))
            elif not self.send_page(path):
                self.send_json(404, {"error": "not found"})
        except Exception as error:  # noqa: BLE001 - the page shows the reason, not "unreachable"
            self.send_json(500, {"error": f"{type(error).__name__}: {error}"})

    def do_POST(self) -> None:
        app = self.app
        try:
            size = max(0, min(int(self.headers.get("Content-Length", 0)), 1 << 20))
            raw = self.rfile.read(size)
            # anything else, the old hook route too: a 404
            if self.path not in ("/api/restart", "/api/control", "/api/config"):
                self.send_json(404, {"error": "not found"})
                return
            body = dict(json.loads(raw or b"{}"))  # not an object: a 400
            if self.headers.get("X-DL") != "1":
                self.send_json(403, {"error": "missing X-DL header"})
            elif self.path == "/api/restart":
                self.send_json(200, app.restart())
            elif self.path == "/api/config":
                self.send_json(200, app.save(body))
            else:
                self.send_json(200, app.apply(body))
        except (ValueError, TypeError) as error:  # a refused value: the reason, nothing changed
            self.send_json(400, {"error": f"{type(error).__name__}: {error}"})
        except ConflictError as error:  # dev-loop.yaml changed since it was read: nothing written
            self.send_json(409, {"error": str(error)})
        except Exception as error:  # noqa: BLE001 - a failed write is a 500 with its reason
            self.send_json(500, {"error": f"{type(error).__name__}: {error}"})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - nothing is logged
        pass


def bound(app: Dashboard) -> type[Handler]:
    """Return a Handler class that serves this dashboard."""
    return type("BoundHandler", (Handler,), {"app": app})


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001 - `dl dashboard` takes none
    CONTROL.parent.mkdir(parents=True, exist_ok=True)
    root = project_root()
    app = Dashboard(Scanner(root.name), Store(CONTROL), root)  # a bad control.json stops
    app.workers.start()
    ThreadingHTTPServer(("127.0.0.1", PORT), bound(app)).serve_forever()
    return 0


if __name__ == "__main__":
    main()
