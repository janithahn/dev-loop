"""Self-check for the context gate's fast path: no YAML under the floor.

Below the floor (the lowest context any cap can trip at under this config, cached by the config
files' stat) the hook answers without YAML; it never lets through a call the slow path denies.
Run: python -m pytest test_dev_loop_context_fast.py -q (from the plugin's tests folder).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dev_loop.dashboard import scan
from test_dev_loop_context_gate import (
    AGENT_RESULT,
    HOOK,
    at_context,
    denied,
    event,
    finish,
    gate,
    hook,
    owner,
)
from test_dev_loop_fixtures import (
    AGENT,
    SESSION,
    Tree,
    assistant,
    line,
    tool_result,
    user,
)

if TYPE_CHECKING:
    from types import ModuleType

# A project file that lowers the builder's cap and the threshold under the shipped defaults.
LOWER = "handoff: {threshold: 0.5}\nroles:\n  builder-backend:\n"
LOWER += "    context: {default: 25000, min: 20000, max: 400000}\n"
# The first case under a new project file is one its cap denies and the shipped floor would pass:
# a floor keyed by the shipped defaults alone lets it through.
CONTEXTS = [29_999, None, 1_000, 9_999, 12_000, 53_999, 54_000, 60_000, 190_000]
RUNS = 20


def loaded_hook(tree: Tree) -> ModuleType:
    """Load the hook in this process with HOME in the tree (its paths are read at import)."""
    home = str(tree.root / "home")
    saved = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    os.environ.update(HOME=home, USERPROFILE=home)  # USERPROFILE: Windows
    try:
        spec = importlib.util.spec_from_file_location("context_gate", HOOK)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, value in saved.items():
            os.environ.pop(name, None) if value is None else os.environ.update({name: value})
    return module


def owner_controls(tree: Tree, agent: str, which: int) -> None:
    """Write control.json: none, a paused session or agent, an owner's cap or a default cap."""
    controls: list[dict[str, Any]] = [{}, {"paused": [SESSION]}, {"paused": [agent]}]
    controls += [{"context_caps": {agent: 25_000}}, {"agent_context": 25_000}]
    folder = tree.root / "home" / ".claude" / "dev-loop"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "control.json").write_text(json.dumps(controls[which]), encoding="utf-8")


def judged(gate: ModuleType, tree: Tree, case: tuple[Any, ...], agent: str) -> tuple[bool, Any]:
    """Return the fast path's yes and the slow path's answer on one call."""
    context, kind, which = case
    if context is None:
        tree.write([user()], agent=agent)
    else:
        at_context(tree, context, agent)
    owner_controls(tree, agent, which)
    call = event(tree, agent) | {"agent_type": kind}
    return gate.below_floor(call, agent, main=False), gate.judge(call, agent, None)[0]


def test_the_fast_path_never_allows_what_the_slow_path_denies(tree: Tree) -> None:
    """On every context, owner control, role and project file, a call the fast path lets
    through is one the slow path answers with silence; a project file below the shipped floor
    moves it. Each case is its own agent: no window carries over."""
    gate, repo = loaded_hook(tree), tree.root / "repo"
    repo.mkdir()
    os.environ["CLAUDE_PROJECT_DIR"] = str(repo)
    fast = denied = 0
    try:
        for number, (text, *case) in enumerate(
            product(["", LOWER], CONTEXTS, ["dev-loop:builder-backend", "general-purpose"], range(5))
        ):
            project = repo / "dev-loop.yaml"
            if not project.exists() or project.read_text("utf-8") != text:  # a rewrite: a miss
                project.write_text(text, encoding="utf-8")
            let_through, slow = judged(gate, tree, tuple(case), f"e{number}")
            fast += let_through
            denied += slow.deny is not None
            assert not let_through or (slow.deny, slow.context) == (None, None), (text, case, slow)
    finally:
        os.environ.pop("CLAUDE_PROJECT_DIR")
    assert fast and denied, (fast, denied)  # both paths were taken


def test_two_checkouts_keep_their_own_floor(tree: Tree) -> None:
    """Two checkouts called in turn each keep their own floor and the fast path.

    The project roots' config files differ in mtime; each saves its floor once, and neither
    rewrites the other's (one shared file did: each call evicted the other's floor).
    """
    gate, roots = loaded_hook(tree), [tree.root / "wt-a", tree.root / "wt-b"]
    floors = tree.root / "home" / ".claude" / "dev-loop" / "floors"
    for number, root in enumerate(roots):
        root.mkdir()
        (root / "dev-loop.yaml").write_text("", encoding="utf-8")
        os.utime(root / "dev-loop.yaml", ns=(number * 10**9, number * 10**9))
    at_context(tree, 1_000)
    call = event(tree) | {"agent_type": "dev-loop:builder-backend"}
    try:
        for root in roots:  # the slow path saves each checkout's floor
            os.environ["CLAUDE_PROJECT_DIR"] = str(root)
            assert not gate.judge(call, AGENT, None)[0].kind
        saved = {path: path.stat().st_mtime_ns for path in floors.glob("*.json")}
        for root in roots * 3:
            os.environ["CLAUDE_PROJECT_DIR"] = str(root)
            assert gate.below_floor(call, AGENT, main=False), f"{root.name} lost its floor"
    finally:
        os.environ.pop("CLAUDE_PROJECT_DIR")
    assert len(saved) == len(roots), sorted(saved)  # one file per checkout
    assert {path: path.stat().st_mtime_ns for path in floors.glob("*.json")} == saved


def transcript(tree: Tree, agent: str, context: int) -> None:
    """Write a 27 MB transcript for the agent: its prompt, tool results, then one model call."""
    filler = line(tool_result()) + "\n"
    path = tree.write([user()], agent=agent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(filler * (27_000_000 // len(filler)))
    at_context(tree, context, agent)
    assert path.stat().st_size > 27_000_000, path.stat().st_size  # noqa: PLR2004 - 27 MB


def imported(tree: Tree, agent: str) -> str:
    """Return the import trace of one hook call, after one that saves the floor."""
    stdin = json.dumps(event(tree, agent))
    finish(hook(tree, stdin))
    code, _, stderr = finish(hook(tree, stdin, extra={"PYTHONPROFILEIMPORTTIME": "1"}))
    assert code == 0, stderr
    return stderr


def test_the_hook_is_fast(tree: Tree) -> None:
    """On a 27 MB transcript, a call under the floor never loads the config's YAML; one
    over it does (the slow path). Run as registered, `python -S` (hook). The modules loaded are
    compared, not the milliseconds: wall-clock bounds flake on a loaded machine."""
    transcript(tree, AGENT, 1_000)
    transcript(tree, "slow0123456789", 100_000)
    fast, slow = imported(tree, AGENT), imported(tree, "slow0123456789")
    loads_yaml = re.compile(r"\| +yaml$", re.MULTILINE)
    assert not loads_yaml.search(fast), fast
    assert loads_yaml.search(slow), slow


def test_the_tail_reader_runs_without_site_packages(tree: Tree) -> None:
    """tail.py, which the hook's fast path imports, finds the same last call as the
    scanner under `python -S`, loading no module outside json, os and datetime's own."""
    last = assistant("m2", tokens=(2, 10, 3, 4, 90_000))
    path = tree.write([user(), last, AGENT_RESULT], agent=AGENT)
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); before = set(sys.modules); "
        "import dev_loop.dashboard.tail as t; "
        "print(t.context_of(t.last_assistant(sys.argv[2]))); "
        "print(sorted(m for m in set(sys.modules) - before if m.split('.')[0] in "
        "{'pathlib', 'dataclasses', 'threading', 'typing', 'yaml'}))"
    )
    source = str(Path(__file__).resolve().parents[1] / "src")
    argv = [sys.executable, "-S", "-c", code, source, str(path)]
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split("\n")[:2] == [str(scan.last_call(path)), "[]"], done.stdout


def test_under_dash_s_the_slow_path_finds_pyyaml_and_denies(tree: Tree) -> None:
    """As registered (`python -S`), the slow path finds PyYAML and denies the crossing call.

    PyYAML is on no path until the slow path runs site.main(); it is judged, never failed open.
    """
    bare = subprocess.run(
        [sys.executable, "-S", "-c", "import yaml"], capture_output=True, check=False
    )
    assert bare.returncode, "PyYAML found without site: the check proves nothing"
    at_context(tree, 180_000)  # 90% of builder-backend's 200k cap
    assert "180k is at 90% of its 200k cap" in denied(gate(tree, event(tree)))


def test_a_plain_id_is_what_the_old_pattern_matched(tree: Tree) -> None:
    """The hook's re-free plain() keeps `[A-Za-z0-9_-]{1,64}`: never a path, dot or space."""
    pattern, plain = re.compile(r"[A-Za-z0-9_-]{1,64}"), loaded_hook(tree).plain
    samples: list[object] = ["a", "A-z_9", "___", "-", "x" * 64, "x" * 65, "", "a/b", ".."]
    samples += ["a b", "a\n", "a.jsonl", "a\nb", chr(0xE9), chr(0x661), chr(0xFF21), None, 5]
    for value in samples:
        expected = value if isinstance(value, str) and pattern.fullmatch(value) else None
        assert plain(value) == expected, value


def test_an_old_control_file_never_crashes_the_hook(tree: Tree) -> None:
    """An old control.json's dollar and default caps are ignored; its pause still holds.

    A crash would fail open: the paused agent would run.
    """
    at_context(tree, 1_000)
    owner(tree, paused=[SESSION], daily_usd=5, session_usd=3, agent_context=100_000)
    for agent in (AGENT, None):
        code, stdout, stderr = finish(hook(tree, json.dumps(event(tree, agent))))
        assert (code, stderr) == (0, ""), (agent, code, stderr)
        assert "this session is paused" in denied(json.loads(stdout)), agent


def test_the_floor_key_sees_the_plugins_shipped_defaults(tree: Tree) -> None:
    """A new plugin's defaults move the key: the floor saved under the old ones is a miss."""
    from dev_loop.dashboard.floor import config_key

    plugin = str(HOOK.parents[1])
    assert config_key(plugin, str(tree.root))[0] is not None


def test_the_fast_path_records_no_telemetry(tree: Tree) -> None:
    """A slow-path call records one subagent line; a later call the fast path answers
    (under the cached floor) adds none — the telemetry rides the slow path only."""
    gate, repo = loaded_hook(tree), tree.root / "repo"
    repo.mkdir()
    os.environ["CLAUDE_PROJECT_DIR"] = str(repo)
    log = tree.root / "home" / ".claude" / "dev-loop" / "agents.jsonl"
    try:
        at_context(tree, 150_000)  # above the floor: slow path, which caches the floor and records
        assert not gate.below_floor(event(tree), AGENT, main=False)
        gate.judge(event(tree), AGENT, None)
        assert len(log.read_text("utf-8").splitlines()) == 1
        other = "b0123456789abcdef"
        at_context(tree, 1_000, other)  # under the floor: the fast path answers it
        assert gate.below_floor(event(tree, other), other, main=False)
        assert len(log.read_text("utf-8").splitlines()) == 1  # no second line
    finally:
        os.environ.pop("CLAUDE_PROJECT_DIR", None)


def test_a_non_plain_session_id_is_recorded_as_null(tree: Tree) -> None:
    """The telemetry session_id goes through plain(), as the error log does — a value
    that is not a plain id (never a real session) is written null, never shipped as a label."""
    gate = loaded_hook(tree)
    log = tree.root / "home" / ".claude" / "dev-loop" / "agents.jsonl"
    gate.record_agent(event(tree) | {"session_id": "s/../x"}, AGENT, 120_000)
    [row] = [json.loads(one) for one in log.read_text("utf-8").splitlines()]
    assert (row["session_id"], row["agent_id"]) == (None, AGENT), row


def test_the_telemetry_log_rotates(tree: Tree) -> None:
    """Past AGENTS_MAX the telemetry log rolls to agents.1.jsonl; a write never raises."""
    gate = loaded_hook(tree)
    folder = tree.root / "home" / ".claude" / "dev-loop"
    saved, gate.AGENTS_MAX = gate.AGENTS_MAX, 50
    try:
        for context in (100_000, 110_000, 120_000):
            gate.record_agent(event(tree), AGENT, context)
    finally:
        gate.AGENTS_MAX = saved
    assert (folder / "agents.1.jsonl").exists()
    assert len((folder / "agents.jsonl").read_text("utf-8").splitlines()) == 1
