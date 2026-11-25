"""Helpers for the dashboard server self-checks: a real server on a free local port."""

from __future__ import annotations

import contextlib
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

from dev_loop.dashboard import scan
from dev_loop.dashboard.server import Dashboard, bound
from dev_loop.dashboard.store import Store
from test_dev_loop_fixtures import scanned

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dev_loop.dashboard.scan import Scanner
    from test_dev_loop_fixtures import Tree

DL = {"X-DL": "1"}


@contextlib.contextmanager
def serving(
    tree: Tree, *, scanner: Scanner | None = None
) -> Iterator[tuple[Dashboard, int]]:
    """Run a server over the tree with its own control.json and settings.json."""
    scan.RESTART.clear()
    owner = tree.root / "owner"
    owner.mkdir(exist_ok=True)
    store = Store(owner / "control.json")
    app = Dashboard(scanner or scanned(tree), store)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), bound(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield app, httpd.server_address[1]
    finally:
        app.workers.stopped.set()  # a scan thread must not outlive its check: it would take RESTART
        app.workers.wake.set()  # a poller in `targets` waits on it
        for thread in app.workers.threads.values():
            thread.join(timeout=10)
        httpd.shutdown()
        httpd.server_close()


def call(
    port: int, request: tuple[str, str], body: object = None, headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    """Send (method, path) with a JSON body (or raw bytes); return the status and the JSON."""
    method, path = request
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    raw = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
    connection.request(method, path, raw if method == "POST" else None, headers or {})
    response = connection.getresponse()
    data = json.loads(response.read() or b"null")
    connection.close()
    return response.status, data


def pause(port: int, ident: str) -> tuple[int, Any]:
    body = {"action": "pause", "id": ident}
    return call(port, ("POST", "/api/control"), body, DL)
