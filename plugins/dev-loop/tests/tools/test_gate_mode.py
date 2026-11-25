"""ci/gate_mode.py decides which sides of the pipeline a change runs, from `gates.sides`.

A wrong answer skips a suite a change needs, so every rule is pinned here, including a moved file
(the gate is fed `--no-renames`, so a move lists its old path as a deletion), a path no side
claims (it runs everything) and a bad project file (refused, never read as "run nothing").
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
TESTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS))

from test_neutral import check  # noqa: E402

# The fixture project: a server, a web app whose own sources need only the server's guards,
# the change folders, and the pipeline paths that run everything.
CONFIG = """\
version: 1
gates:
  sides:
    always: "pipeline.yml tools/ci/* tools/board/page/* tools/test_render.mjs"
    server: "server/* docs/* design/* web/app/nginx.conf web/app/openapi.json tools/*"
    web: "web/* design/*"
    records: ".dev-loop/changes/*"
"""
ALL = ("records", "server", "web")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "dev-loop.yaml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


def gate(project: Path, *paths: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project), "PYTHONPATH": str(SRC)}
    return subprocess.run(  # noqa: S603 - our own module, fixed arguments
        [sys.executable, "-m", "dev_loop.ci.gate_mode"],
        input="\n".join(paths), capture_output=True, text=True, check=False, env=env,
    )


def sides(project: Path, *paths: str) -> tuple[str, ...]:
    done = gate(project, *paths)
    assert done.returncode == 0, done.stderr
    return tuple(sorted(done.stdout.split()))


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (("web/app/src/features/queue/Queue.tsx", ".dev-loop/changes/x/status.md"),
         ("records", "web")),
        (("web/app/vite.config.ts", "web/app/package.json"), ("web",)),
        (("web/app/src/a.tsx", "web/app/nginx.conf"), ("server", "web")),
        (("web/app/src/a.tsx", "web/app/openapi.json"), ("server", "web")),
        (("server/agents/x.dsl.yaml", "server/tests/a.py"), ("server",)),
        (("server/src/core/x.py", "web/app/src/api/types.gen.ts"), ("server", "web")),
        (("docs/adr/0004-approved-libraries.md",), ("server",)),
        (("design/mock/app.part.html",), ("server", "web")),
        (("pipeline.yml",), ALL),
        (("tools/ci/gate_mode.py",), ALL),
        (("tools/board/page/main.js",), ALL),
        (("tools/test_render.mjs",), ALL),
        ((), ALL),
    ],
)
def test_the_gate_runs_what_the_paths_need(
    project: Path, paths: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    assert sides(project, *paths) == expected


def test_a_path_no_side_claims_runs_everything(project: Path) -> None:
    assert sides(project, "web/app/src/a.tsx", "README.md") == ALL


def test_a_list_bigger_than_the_pipe_buffer_never_fails_open(project: Path) -> None:
    # 5,000 web paths (~250 KB) then one server path: the gate must still see the server.
    many = [f"web/app/src/features/f{n}/component{n}.tsx" for n in range(5000)]
    assert sides(project, *many, "server/src/core/x.py") == ("server", "web")


def test_a_source_moved_out_of_the_web_app_still_runs_the_web_side(project: Path) -> None:
    # --no-renames lists the deletion of the old path, so the move is seen from both ends.
    assert sides(project, "web/app/src/x.ts", "docs/x.ts") == ("server", "web")


def test_a_server_file_moved_into_a_change_folder_runs_the_server(project: Path) -> None:
    assert sides(project, "server/src/core/x.py", ".dev-loop/changes/y/x.py") == (
        "records", "server",
    )


def test_a_bad_project_file_is_refused_never_read_as_no_sides(project: Path) -> None:
    (project / "dev-loop.yaml").write_text("version: 1\ngates:\n  sides: [web]\n", "utf-8")
    done = gate(project, "web/app/src/a.tsx")
    assert done.returncode == 2
    assert done.stdout == ""
    assert "gates.sides" in done.stderr


def test_the_files_this_task_wrote_are_neutral() -> None:
    assert check([SRC / "dev_loop" / "ci" / "gate_mode.py", Path(__file__)]) == []
