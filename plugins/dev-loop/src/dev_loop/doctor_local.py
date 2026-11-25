"""`dl doctor`'s local rows: plugin version, rules imported, guides, githooks, verify steps."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dev_loop import config
from dev_loop.init import IMPORT, POINTER, RULES, git

Row = tuple[str, bool, str]
GITHOOKS_FIX = "git config core.hooksPath .githooks"
STEPS_FIX = "add a command under verify.steps in dev-loop.yaml"


def _plugin_root() -> Path:
    return Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).parents[2])


def _plugin_version() -> str:
    manifest = _plugin_root() / ".claude-plugin" / "plugin.json"
    return str(json.loads(manifest.read_text(encoding="utf-8")).get("version", ""))


def _pin(root: Path) -> str | None:
    """The version `dl init` pinned: the marketplace source's `ref`, less a leading `v`."""
    path = root / ".claude" / "settings.json"
    try:
        ref = json.loads(path.read_text(encoding="utf-8"))["extraKnownMarketplaces"]["dev-loop"][
            "source"
        ]["ref"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return ref.removeprefix("v") if isinstance(ref, str) else None


def _version_row(root: Path) -> Row:
    try:
        have = _plugin_version()
    except (OSError, ValueError):
        return ("plugin version", False, "plugin.json is unreadable")
    pin = _pin(root)
    if pin is None:
        return ("plugin version", True, f"{have} (no pin)")
    return ("plugin version", pin == have, f"{have}, project pins {pin}")


def _rules_row(root: Path) -> Row:
    lines: list[str] = []
    for name in ("CLAUDE.md", "AGENTS.md"):
        try:
            lines += (root / name).read_text(encoding="utf-8").splitlines()
        except (OSError, ValueError):
            continue
    found = {line.strip() for line in lines} & {IMPORT, POINTER}
    ok = bool(found) and (root / RULES).is_file()
    return ("rules imported", ok, "ok" if ok else "run dl init")


def _guides_row(root: Path) -> Row:
    """Red when guides are set but no package can place a lesson in one (`dl records rules`)."""
    from dev_loop.records import review_rules  # noqa: PLC0415 - imported here, as _disk_row does

    try:
        review_rules.expected(root)
    except ValueError as error:
        return ("guides", False, f"dl records rules: {error}")
    return ("guides", True, "ok")


def _hooks_row(root: Path) -> Row:
    path = git(root, "config", "core.hooksPath")
    if path == ".githooks":
        return ("githooks", True, f"core.hooksPath {path}")
    state = f"is {path}, not .githooks" if path else "unset"
    detail = f"core.hooksPath {state}, the githooks never run: {GITHOOKS_FIX}"
    return ("githooks", False, detail)


def _steps_row(loaded: config.Loaded) -> Row:
    """Green when some verify.steps entry has a command: `dl verify` is red with none."""
    steps = loaded.get("verify.steps") or {}
    named = [name for name, command in steps.items() if command]
    if not named:
        detail = f"none with a command, dl verify is red: {STEPS_FIX}"
        return ("verify steps", False, detail)
    noun = "step" if len(named) == 1 else "steps"
    return ("verify steps", True, f"{len(named)} {noun}: {', '.join(named)}")
