"""Self-check for the dev-loop dashboard's served page.

Each check starts a real server on a free local port over fixture transcripts. Run: python tests/test_dev_loop_page.py; test_self_checks.py runs it. The page's own logic is in
tests/test_dev_loop_render.mjs.
"""

from __future__ import annotations

import http.client
import re
from pathlib import Path
from typing import TYPE_CHECKING

from test_dev_loop_fixtures import AGENT, SESSION, Tree, assistant, run, user
from test_dev_loop_serving import call, serving

if TYPE_CHECKING:
    from collections.abc import Callable

PAGE = Path(__file__).resolve().parents[1] / "src" / "dev_loop" / "dashboard" / "page"
LABEL = "API-equivalent, not a bill"
# a hex colour, or a colour function: only tokens.css may hold one
RAW_COLOUR = re.compile(
    r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|\b(?:rgb|hsl|hwb|lab|lch)a?\(",
    re.IGNORECASE,
)


def get(port: int, path: str) -> tuple[int, http.client.HTTPMessage, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    text = response.read().decode("utf-8", "replace")
    connection.close()
    return response.status, response.msg, text


def check_the_page_and_its_files_are_served(tree: Tree) -> None:
    with serving(tree) as (_, port):
        status, headers, body = get(port, "/")
        assert status == 200
        assert headers["Content-Type"] == "text/html; charset=utf-8"
        assert headers["Cache-Control"] == "no-store"
        assert LABEL in body
        status, headers, _ = get(port, "/main.js")
        assert (status, headers["Content-Type"]) == (200, "text/javascript; charset=utf-8")
        assert get(port, "/main.js?x=1")[0] == 200  # a query string is not part of the name
        files = re.findall(r'(?:src|href)="([^"/:]+)"', body)  # every file the page names
        assert {"main.js", "tokens.css", "page.css"} <= set(files)
        assert all(get(port, f"/{name}")[0] == 200 for name in files), files


def check_any_other_path_is_a_404(tree: Tree) -> None:
    with serving(tree) as (_, port):
        for path in ("/../server.py", "/%2e%2e/server.py", "/control.json", "/package.json"):
            assert get(port, path)[0] == 404, path
        for path in ("/eslint.config.js", "/tsconfig.json"):  # the page folder holds config too
            assert get(port, path)[0] == 404, path
        assert get(port, "/nope.js")[0] == 404
        assert get(port, r"/..\server.py")[0] == 404


def raw_colours(files: dict[str, str]) -> list[str]:
    """Return `file: colour` for each raw colour outside tokens.css."""
    return [
        f"{name}: {found.group(0)}"
        for name, text in files.items()
        if name != "tokens.css"
        for found in RAW_COLOUR.finditer(text)
    ]


def check_no_raw_colour_outside_the_tokens(tree: Tree) -> None:
    del tree
    planted = {"page.css": "a{color:#fff;background:rgba(0,0,0,.2)}", "main.js": "x = '#0b1220'"}
    assert len(raw_colours(planted)) == 3  # the matcher itself catches each
    assert raw_colours({"tokens.css": ":root{--bg:#0b1220}", "x.css": "a{color:var(--bad)}"}) == []
    files = {p.name: p.read_text("utf-8") for p in PAGE.iterdir() if p.is_file()}
    assert "tokens.css" in files
    assert raw_colours(files) == []


def check_the_dot_starts_grey_with_its_waiting_title(tree: Tree) -> None:
    del tree
    html = (PAGE / "index.html").read_text("utf-8")
    (tag,) = re.findall(r"<[^>]*\bid=\"live\"[^>]*>", html)
    assert re.search(r'class="dot"', tag), tag  # no green, red or running class
    assert 'title="waiting for the first answer"' in tag


def check_each_row_carries_its_last_write(tree: Tree) -> None:
    session = tree.write([user(), assistant("m1", "end_turn")], age=120)
    agent = tree.write([user(), assistant("m2", "end_turn")], name=SESSION, agent=AGENT, age=300)
    with serving(tree) as (_, port):
        _, state = call(port, ("GET", "/api/state"))
    written = {r["id"]: r["written"] for r in state["sessions"]}
    assert abs(written[SESSION] - session.stat().st_mtime) < 0.01
    assert abs(written[AGENT] - agent.stat().st_mtime) < 0.01


if __name__ == "__main__":
    checks: list[Callable[[Tree], None]] = [
        obj for name, obj in list(globals().items()) if name[:6] == "check_"
    ]
    run("dev-loop page", checks)
