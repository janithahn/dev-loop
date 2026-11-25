"""`dl doctor`: one table of what the loop needs, and exit 1 when any row is red.

Rows: Python, PyYAML, plugin version against the project's pin (the marketplace source's
`ref` that `dl init` writes to `.claude/settings.json`; no pin is green), dev-loop.yaml valid,
rules imported (CLAUDE.md imports .dev-loop/RULES.md, or AGENTS.md imports or points at it, and
it exists), githooks (core.hooksPath is .githooks, else they never run), verify steps (at least one
`verify.steps` entry with a command, else `dl verify` is red), host reachable, credential present or absent, and the
checkout no more than
`STALE_DAYS` behind the remote integration branch. On GitHub both host rows ask
`gh auth status`: logged in or not. The credential's value is never read into the output.

`dl doctor --json` prints the same checks (plus a telemetry and a context-gate row) as a JSON
array, each row {name, status: ok|red|optional, detail, fix}, where `fix` is one instruction and
no field ever holds a secret. The settings pane builds its Setup section from it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from dev_loop import agent_models, config, doctor_hooks
from dev_loop.doctor_local import (  # noqa: F401  # re-exported: tests call them here
    GITHOOKS_FIX,
    STEPS_FIX,
    _guides_row,
    _hooks_row,
    _pin,
    _plugin_root,
    _plugin_version,
    _rules_row,
    _steps_row,
    _version_row,
)
from dev_loop.hosts import HostError, ado, github
from dev_loop.init import git

MIN_PYTHON = (3, 12)
GATE_STALE_DAYS = 7  # a heartbeat older than this, with the gate once seen, is reported
LS_REMOTE_TIMEOUT = 30  # seconds; a remote that never answers is a red row
STALE_DAYS = 7  # a checkout more than this behind the remote integration tip is red
Row = tuple[str, bool, str]

# One concrete instruction per red row, by name; the credential row is provider-aware below.
_FIX = {
    "dev-loop.yaml": "run dl init",
    "rules imported": "run dl init",
    "plugin version": "run dl init",
    "host reachable": "check host.org, host.project and the credential",
    "disk": "run dl clean",
    "Python": "install Python 3.12 or newer",
    "PyYAML": "pip install pyyaml",
    "githooks": GITHOOKS_FIX,
    "verify steps": STEPS_FIX,
    "agent models": "set the role model back, or copy the agent into .claude/agents/ under a bare type and set its model:",
}
_ADO_CRED_FIX = (
    "log in once with Git Credential Manager (`git credential-manager configure`), "
    "or set the env var named by host.token_env"
)
_GH_CRED_FIX = "gh auth login"
_TELEMETRY_FIX = "open the Telemetry section below"
_CONTEXT_FIX = "restart Claude Code; check the plugin is enabled"


def _gate_registered(root: Path) -> bool:
    """A PreToolUse group matching every tool (`*`) runs context-gate.py (G6)."""
    try:
        hooks = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    groups = hooks.get("PreToolUse", []) if isinstance(hooks, dict) else []
    return any(
        group.get("matcher", "*") == "*"
        and any("context-gate.py" in hook.get("command", "") for hook in group.get("hooks", []))
        for group in groups
    )


def _heartbeat_note(home: Path) -> str:
    """'gate not seen running since <date>' when it ran once but not in GATE_STALE_DAYS; else ''."""
    beat = home / ".claude" / "dev-loop" / "gate-heartbeat"
    if not beat.exists():  # never fired here: a new install, or caught by the registration check
        return ""
    seen = beat.stat().st_mtime
    if (time.time() - seen) / 86400 <= GATE_STALE_DAYS:
        return ""
    return f"gate not seen running since {time.strftime('%Y-%m-%d', time.localtime(seen))}"


def _context_gate_row() -> Row:
    """Green when the gate is registered (matcher `*`), its file exists and its self-check passes.

    Red whenever the hook self-test names the gate broken: it cannot judge an agent's call.
    A stale heartbeat (the gate ran once but not in GATE_STALE_DAYS) turns the row red with
    'gate not seen running since <date>': a cap nobody can see is off is not a cap (G6).
    """
    from dev_loop.dashboard import gate  # noqa: PLC0415 - imported here, like _disk_row

    root = _plugin_root()
    registered = _gate_registered(root)
    exists = (root / "hooks" / "context-gate.py").is_file()
    passed = gate.self_check() if exists else False
    note = _heartbeat_note(Path.home())
    state = doctor_hooks.gate_state(root)
    if not registered:
        return ("context gate", False, "context-gate not registered on PreToolUse: run dl init")
    if not exists:
        return ("context gate", False, "hooks/context-gate.py is missing: run dl init")
    if not passed:
        return ("context gate", False, "the gate's self-check failed")
    if state == "broken":
        return ("context gate", False, "the hook self-test names the gate broken: run dl doctor")
    note = "" if state == "ok" else note  # a quiet gate the self-test runs ok is healthy
    return ("context gate", not note, note or "registered, self-check ok")


def _host_row() -> Row:
    try:
        ado.request("GET", "/_apis/git/repositories")
    except (HostError, ValueError) as error:
        detail = str(error)
        return ("host reachable", False, ado.WEB_PAGE if ado.WEB_PAGE in detail else detail)
    return ("host reachable", True, "answered")


def _credential_row() -> Row:
    try:
        ado.credential()
    except (HostError, ValueError):
        return ("credential", False, "absent")
    return ("credential", True, "present")


# The guards a project may have copied into its own settings: kind, script name.
GUARDS = {"context gate": "context-gate.py", "done guard": "done-guard.sh",
          "branch guard": "branch-guard.sh", "push guard": "push-guard.py",
          "pr loop": "pr-loop-trigger.sh"}


def _tools_row() -> Row:
    """The review, runner and hooks need jq, flock and claude; only some setups use each."""
    need = {"jq": "hooks and review", "flock": "the runner's lock", "claude": "agents and review"}
    missing = [f"{tool} ({why})" for tool, why in need.items() if not shutil.which(tool)]
    if missing:
        return ("tools", False, "missing: " + ", ".join(missing))
    return ("tools", True, "jq, flock, claude found")


def duplicate_hooks(root: Path) -> list[tuple[str, str]]:
    """(kind, scope) of each guard also registered in project, local or user settings."""
    user = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    places = (("project", root / ".claude" / "settings.json"),
              ("project-local", root / ".claude" / "settings.local.json"),
              ("user", user / "settings.json"))
    found = []
    for scope, path in places:
        text = json.dumps(_read_settings(path).get("hooks", {}))
        found += [(kind, scope) for kind, script in GUARDS.items() if script in text]
    return found


def _duplicates_row(root: Path) -> Row:
    found = duplicate_hooks(root)
    if not found:
        return ("duplicate hooks", True, "none")
    named = ", ".join(f"{kind} ({scope})" for kind, scope in found)
    return ("duplicate hooks", False, f"duplicate hooks, each also in the plugin: {named}")


def _local_rows(root: Path, loaded: config.Loaded) -> list[Row]:
    """The local host needs no credential; its one check is that git reaches the remote."""
    remote = str(loaded.get("branches.remote"))
    try:
        done = subprocess.run(["git", "ls-remote", remote], cwd=root, capture_output=True,
                              text=True, check=False, timeout=LS_REMOTE_TIMEOUT)
        ok, note = done.returncode == 0, "reachable with git"
        note = note if ok else "unreachable with git"
    except subprocess.TimeoutExpired:
        ok, note = False, f"timed out after {LS_REMOTE_TIMEOUT}s"
    return [("host reachable", ok, f"{remote} {note}"),
            ("credential", True, "git only; none needed")]


def _github_rows() -> list[Row]:
    try:
        ok = github.logged_in()
    except HostError as error:
        return [("host reachable", False, str(error)), ("credential", False, str(error))]
    if ok:
        return [("host reachable", True, "gh logged in"), ("credential", True, "gh logged in")]
    hint = "gh not logged in: run gh auth login"
    return [("host reachable", False, hint), ("credential", False, "gh not logged in")]


def rows(root: Path) -> list[Row]:
    """Run every check; each one is a (name, ok, detail) row, never raising."""
    doctor_hooks.self_test_lines.cache_clear()  # this run's own self-test, read by two rows
    try:
        import yaml  # noqa: F401, PLC0415
        yaml_row: Row = ("PyYAML", True, "installed")
    except ModuleNotFoundError:
        yaml_row = ("PyYAML", False, "missing: pip install pyyaml")
    loaded = config.load(root)
    if loaded.error:  # nothing below can be judged without a valid config: show that row alone
        return [("dev-loop.yaml", False, loaded.error)]
    provider = loaded.get("host.provider")
    return [
        ("Python", sys.version_info >= MIN_PYTHON, sys.version.split()[0]),
        yaml_row,
        _version_row(root),
        ("dev-loop.yaml", True, "valid"),
        _tools_row(),
        _duplicates_row(root),
        _rules_row(root),
        _guides_row(root),
        _hooks_row(root),
        _steps_row(loaded),
        _context_gate_row(),
        *doctor_hooks.rows(_plugin_root()),
        agent_models.row(root, loaded, _plugin_root()),
        _disk_row(root, loaded),
        _staleness_row(root, loaded),
        *(_github_rows() if provider == "github" else
          _local_rows(root, loaded) if provider == "local" else
          [_host_row(), _credential_row()]),
    ]


def _disk_row(root: Path, loaded: config.Loaded) -> Row:
    """Red at or above `cleanup.threshold_pct` of a worktree root or the temp dir; names dl clean."""
    from dev_loop import clean  # noqa: PLC0415 - imported here to avoid a load-time cycle

    threshold = int(loaded.get("cleanup.threshold_pct"))
    name, pct = clean.busiest_gauge(root, loaded)
    tail = "used" if pct < threshold else f"(>= {threshold}%): run dl clean"
    return ("disk", pct < threshold, f"{name} {pct}% {tail}")


def _row_fix(name: str, ok: bool, detail: str, provider: str) -> str:
    if ok:
        return ""
    if name == "dev-loop.yaml":
        return detail  # the config error itself says what to change
    if name == "host reachable" and provider == "local":
        return "check `git remote get-url <branches.remote>`"
    if name == "credential":
        return _GH_CRED_FIX if provider == "github" else _ADO_CRED_FIX
    return _FIX.get(name, detail)


def _read_settings(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _settings(root: Path, name: str) -> dict:
    return _read_settings(root / ".claude" / name)


def _telemetry_json(root: Path) -> dict[str, str]:
    env = _settings(root, "settings.local.json").get("env")
    on = isinstance(env, dict) and env.get("DEV_LOOP_TELEMETRY") == "1"
    return {
        "name": "telemetry", "status": "ok" if on else "optional",
        "detail": "on" if on else "off", "fix": "" if on else _TELEMETRY_FIX,
    }


def _names_dev_loop(doc: dict) -> bool:
    """A settings doc enables dev-loop: a known marketplace, or an enabledPlugins entry names it."""
    markets = doc.get("extraKnownMarketplaces")
    if isinstance(markets, dict) and "dev-loop" in markets:
        return True
    enabled = doc.get("enabledPlugins")
    return isinstance(enabled, (dict, list)) and any("dev-loop" in str(key) for key in enabled)


def _gate_enabled(root: Path) -> bool:
    """Enabled when the session loaded the plugin, or a project, local or user setting names it."""
    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return True
    docs = (
        _settings(root, "settings.json"),
        _settings(root, "settings.local.json"),
        _read_settings(Path.home() / ".claude" / "settings.json"),
    )
    return any(_names_dev_loop(doc) for doc in docs)


def _context_gate_json(root: Path) -> dict[str, str]:
    """One gate row: green only when enabled, registered and self-checking (G6 heartbeat kept)."""
    if not _gate_enabled(root):
        return {"name": "context gate", "status": "red", "detail": "not enabled",
                "fix": _CONTEXT_FIX}
    _name, ok, detail = _context_gate_row()
    return {"name": "context gate", "status": "ok" if ok else "red",
            "detail": detail, "fix": "" if ok else detail}


def _status(ok: bool, detail: str) -> str:
    if not ok:
        return "red"
    return "optional" if detail.startswith(agent_models.WARN) else "ok"


def json_rows(root: Path) -> list[dict[str, str]]:
    """The same checks as the table, plus telemetry and the gate, each as a {name,...,fix} row."""
    loaded = config.load(root)
    try:
        provider = str(loaded.get("host.provider")) if loaded.error is None else "ado"
    except KeyError:
        provider = "ado"
    out = [
        {"name": name, "detail": detail, "status": _status(ok, detail),
         "fix": _row_fix(name, ok and not detail.startswith(agent_models.WARN), detail, provider)}
        for name, ok, detail in rows(root)
        if name != "context gate"  # the unified gate row is appended below, never twice
    ]
    return [*out, _telemetry_json(root), _context_gate_json(root)]


def _staleness_row(root: Path, loaded: config.Loaded) -> Row:
    """Red when the checkout is more than STALE_DAYS behind the remote integration tip."""
    ref = f"{loaded.get('branches.remote')}/{loaded.get('branches.integration')}"
    days = _days_behind(root, ref)
    if days is None:
        return ("checkout", True, f"no {ref} to compare")
    detail = f"{days}d behind {ref}"
    ok = days <= STALE_DAYS
    return ("checkout", ok, detail if ok else f"{detail} (> {STALE_DAYS}d): git pull")


def _days_behind(root: Path, ref: str) -> int | None:
    """Whole days from the HEAD/ref merge-base to ref's tip; None when either is unknown."""
    tip = git(root, "rev-parse", "--verify", "-q", ref)
    base = git(root, "merge-base", "HEAD", ref)
    if not tip or not base:
        return None
    if base == tip:
        return 0
    stamps = [git(root, "show", "-s", "--format=%ct", sha) for sha in (base, tip)]
    if not all(stamps):
        return None
    return (int(stamps[1]) - int(stamps[0])) // 86400


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    if "--duplicates" in argv:  # session-guard.sh's one check: a line per copy, always exit 0
        try:
            copies = duplicate_hooks(config.project_root())
        except ValueError:
            return 0
        for kind, scope in copies:
            print(f"⚠ two {kind}s: remove the {scope} copy and keep the plugin's.")
        return 0
    try:
        root = config.project_root()
    except ValueError as error:
        if as_json:
            print("[]")
            return 1
        print(f"dl doctor: {error}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(json_rows(root)))
        return 0
    table = rows(root)
    for name, ok, detail in table:
        mark = "RED" if not ok else "WARN" if detail.startswith(agent_models.WARN) else "ok "
        print(f"{mark}  {name:<16} {detail}")
    return 0 if all(ok for _, ok, _ in table) else 1
