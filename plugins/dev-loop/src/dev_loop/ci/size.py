"""`dl ci size-cap`: the branch's own lines, judged by the base's size section.

The count runs from the merge base of the base and HEAD, so what the base gained after the fork is
not the branch's. A stacked part's base is the parent `dl pr --base` remembered (`stack_base`).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from dev_loop import config, schema
from dev_loop.doctor import LS_REMOTE_TIMEOUT


def plugin_root() -> Path:
    return Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).parents[3])


def script_env() -> dict[str, str]:
    return {**os.environ, "DL": str(plugin_root() / "bin" / "dl"), "DL_PYTHON": sys.executable}


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root,  # noqa: S607
                          capture_output=True, text=True, check=False)
    return "" if done.returncode else done.stdout.strip()


def remembered(root: Path, branch: str) -> str:
    """Return the parent branch `dl pr --base` remembered for branch; "" when none."""
    return git(root, "config", "--get", f"branch.{branch}.dl-base")


def stack_base(root: Path) -> str | None:
    """Return <remote>/<parent> when `dl pr --base` remembered a parent the clone still has.

    None, said in one line, once the remote has deleted it (merged): its tracking ref is stale.
    A remote that cannot be asked within LS_REMOTE_TIMEOUT keeps the parent, said in one line.
    """
    parent = remembered(root, git(root, "branch", "--show-current"))
    remote = config.load(root).values["branches"]["remote"]
    if not parent or not git(root, "rev-parse", "-q", "--verify", f"{remote}/{parent}"):
        return None
    ask = ["git", "ls-remote", "--exit-code", remote, f"refs/heads/{parent}"]
    quiet = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        code = subprocess.run(ask, cwd=root, env=quiet, capture_output=True,  # noqa: S603
                              check=False, timeout=LS_REMOTE_TIMEOUT).returncode
    except subprocess.TimeoutExpired:
        code = -1
    if code == 2:  # ls-remote --exit-code: no such ref
        print(f"{remote} has no branch {parent} now; judged from the integration branch",
              file=sys.stderr)
        return None
    if code:
        print(f"could not reach {remote} within {LS_REMOTE_TIMEOUT}s; judged from "
              f"{remote}/{parent} as last fetched", file=sys.stderr)
    return f"{remote}/{parent}"


def held_section(text: bytes, built_in: dict[str, Any]) -> dict[str, Any] | None:
    """Return a refused config's size section over the built-in one, when that alone is valid."""
    try:
        size = (yaml.safe_load(text) or {}).get("size")
        merged = built_in | size
        schema.validate_section(merged, "size", schema.TEXT["size"])
    except (yaml.YAMLError, AttributeError, TypeError, ValueError):
        return None
    return merged


def held_size(root: Path, base: str, tmp: Path) -> tuple[dict[str, Any], str]:
    """Return the size section the branch is judged by, and where each part came from.

    With a base that has no config, or one whose size section is refused, the globs are the
    branch's own: a branch could then narrow what counts. That is bounded to a missing or broken
    base; a base whose size section loads decides the cap, the advice and the globs alone.
    """
    held = subprocess.run(["git", "show", f"{base}:./{config.PROJECT}"],  # noqa: S607
                          cwd=root, capture_output=True, check=False)
    why = f"{base} has no {config.PROJECT}"
    built_in = config.load(tmp / "none").values["size"]
    if not held.returncode:
        (tmp / config.PROJECT).write_bytes(held.stdout)
        theirs = config.load(tmp)
        if not theirs.error:
            return theirs.values["size"], f"cap, advice and globs from {base}"
        if (size := held_section(held.stdout, built_in)) is not None:
            return size, (
                f"size section from {base} (the rest of its config refused: {theirs.error})")
        why = f"base config refused ({theirs.error})"
    mine = config.load(root).values["size"]
    stricter = {key: min(mine[key], built_in[key]) for key in ("cap", "advise")}
    return mine | stricter, (
        f"{why}; using the stricter of the working tree's and "
        f"the default cap: {stricter['cap']}; globs from the working tree")


def size_cap(root: Path, base: str) -> int:
    """Judge merge-base(base, HEAD)..HEAD by the base's size section: a PR cannot raise its cap.

    Every other key the script reads is the working tree's.
    """
    mine = config.load(root)
    if mine.error:
        print(f"size cap: not run, {mine.error}", file=sys.stderr)
        return 1
    fork = f"{base}...HEAD"  # from the merge base: what the base gained since is not ours
    diff = subprocess.run(["git", "diff", "--numstat", "--no-renames", fork],  # noqa: S607
                          cwd=root, capture_output=True, text=True, check=False)
    if diff.returncode:
        print(f"size cap: not run, git diff {base} failed: {diff.stderr.strip()}", file=sys.stderr)
        return 1
    script = Path(__file__).with_name("size_cap.sh")
    with tempfile.TemporaryDirectory() as tmp:
        size, source = held_size(root, base, Path(tmp))
        print(f"size cap: {source}", flush=True)
        (Path(tmp) / config.PROJECT).write_text(
            yaml.safe_dump(mine.values | {"size": size}), encoding="utf-8")
        env = script_env() | {"CLAUDE_PROJECT_DIR": tmp}
        for override in ("SIZE_CAP", "SIZE_ADVISE"):  # the gate's cap is the config's alone
            env.pop(override, None)
        return subprocess.run(["bash", str(script)], input=diff.stdout, cwd=root,  # noqa: S607
                              env=env, text=True, check=False).returncode
