"""`dl clean [--apply] [--json] [--when-full]`: remove the loop's leftovers safely.

A dry run by default: every candidate with its size and the reason, and every kept item with the
reason it was kept. `--apply` removes only the safe candidates. `--when-full` does nothing unless a
worktree root or the system temp dir is at or above `cleanup.threshold_pct` on disk or inodes.
`--json` prints the same as data.

Candidates: merged or branch-gone worktrees under the loop's worktree roots; merged local branches
with `branches.change_prefix`; worker run folders finished over `cleanup.keep_days` days ago; loop
logs over `cleanup.max_log_mb` (rotated, the newest kept); and, opt-in, Docker volumes and images
carrying `cleanup.docker_label` and test caches under the system temp dir (`cleanup.cache_globs`).
"Merged" means an ancestor of the integration ref or a whole patch already there (a squash merge).

Never removed: a worktree that is dirty, has unpushed commits or a live worker run; an unmerged
branch; the current checkout; a run whose process is alive; anything outside the configured roots.
A path that cannot be checked is kept, with the reason "not checked".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from dev_loop import config
from dev_loop.supervise import alive as _alive
from dev_loop.supervise import git as _git
from dev_loop.supervise import integration_ref as _integration_ref
from dev_loop.supervise import live_runs as _live_runs
from dev_loop.supervise import merged as _merged
from dev_loop.supervise import roots as _roots

MB = 1024 * 1024


@dataclass(frozen=True)
class Item:
    """One candidate or kept item: its target, its kind, its size in bytes and the reason."""

    target: str
    kind: str  # worktree | branch | run | log | docker-volume | docker-image | cache
    size: int
    remove: bool
    reason: str


def _tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _older(path: Path, days: int) -> bool:
    return path.stat().st_mtime < time.time() - days * 86400


def _within(parent: Path, child: Path) -> bool:
    return parent == child or parent in child.parents


def worktrees(root: Path, loaded: config.Loaded, live: set[str] | None = None) -> list[Item]:
    remote = str(loaded.get("branches.remote"))
    integration = str(loaded.get("branches.integration"))
    held = live or set()
    out = []
    for wt_root, checkout in _roots(root, loaded):
        if not wt_root.is_dir():
            continue
        ref = _integration_ref(checkout, remote, integration)
        missing = f"no {remote}/{integration} or {integration}"
        out += [_worktree_item(d, integration, ref, missing, held)
                for d in sorted(wt_root.iterdir()) if d.is_dir()]
    return out


def _worktree_item(d: Path, integration: str, ref: str | None, missing: str,
                   live: set[str]) -> Item:
    size = _tree_size(d)

    def keep(reason: str) -> Item:
        return Item(str(d), "worktree", size, False, reason)

    if _within(d, Path.cwd()):
        return keep("the current checkout")
    if d.name in live:
        return keep("a live worker run")
    if _git(d, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        return keep("not checked")
    if _git(d, "status", "--porcelain").stdout.strip():
        return keep("uncommitted changes")
    if _git(d, "rev-list", "HEAD", "--not", "--remotes", "--count").stdout.strip() not in ("0", ""):
        return keep("unpushed commits")
    branch = _git(d, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    if not branch:
        return Item(str(d), "worktree", size, True, "branch gone (detached)")
    if ref is None:
        return keep(f"not checked: {missing}")
    if _merged(d, "HEAD", ref):
        return Item(str(d), "worktree", size, True, f"{branch} merged into {integration}")
    return keep(f"{branch} not merged")


def branches(root: Path, loaded: config.Loaded) -> list[Item]:
    remote = str(loaded.get("branches.remote"))
    integration = str(loaded.get("branches.integration"))
    prefix = str(loaded.get("branches.change_prefix"))
    if _git(root, "rev-parse", "--git-dir").returncode:
        return []
    ref = _integration_ref(root, remote, integration)
    missing = f"no {remote}/{integration} or {integration}"
    current = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    listed = _git(root, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{prefix}*")
    out = []
    for name in listed.stdout.split():
        if name in (current, integration):
            out.append(Item(name, "branch", 0, False, "the current branch or integration"))
        elif ref is None:
            out.append(Item(name, "branch", 0, False, f"not checked: {missing}"))
        else:
            is_merged = _merged(root, name, ref)
            reason = f"merged into {integration}" if is_merged else "not merged"
            out.append(Item(name, "branch", 0, is_merged, reason))
    return out


def run_dirs(home: Path, keep_days: int) -> list[Item]:
    base = home / ".dev-loop" / "worker-run"
    if not base.is_dir():
        return []
    out = []
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        size, pid_file = _tree_size(d), d / "pid"
        text = pid_file.read_text(encoding="utf-8").strip() if pid_file.exists() else ""
        if pid_file.exists() and not text.isdigit():
            out.append(Item(str(d), "run", size, False, "not checked"))
        elif text.isdigit() and _alive(int(text)):
            out.append(Item(str(d), "run", size, False, f"process {text} alive"))
        elif _older(d, keep_days):
            out.append(Item(str(d), "run", size, True, f"finished over {keep_days}d ago"))
        else:
            out.append(Item(str(d), "run", size, False, "finished recently"))
    return out


def logs(home: Path, max_mb: int) -> list[Item]:
    limit, out = max_mb * MB, []
    for base in (home / ".dev-loop", home / ".claude" / "dev-loop"):
        if not base.is_dir():
            continue
        for f in sorted(base.glob("*.log")):
            size = f.stat().st_size
            over = size > limit
            reason = f"over {max_mb} MB — rotated, newest kept" if over else f"under {max_mb} MB"
            out.append(Item(str(f), "log", size, over, reason))
    return out


def _docker(*args: str) -> list[str]:
    done = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    return done.stdout.split() if done.returncode == 0 else []


def docker(label: str) -> list[Item]:
    out = []
    for name in _docker("volume", "ls", "--filter", f"label={label}", "--format", "{{.Name}}"):
        used = bool(_docker("ps", "-a", "--filter", f"volume={name}", "--format", "{{.ID}}"))
        out.append(Item(name, "docker-volume", 0, not used, "in use" if used else f"label {label}"))
    for image in _docker("image", "ls", "--filter", f"label={label}", "--format", "{{.ID}}"):
        out.append(Item(image, "docker-image", 0, True, f"label {label}"))
    return out


def caches(globs: list[str], keep_days: int) -> list[Item]:
    tmp, out = Path(tempfile.gettempdir()), []
    for pattern in globs:
        for d in sorted(tmp.glob(pattern)):
            old = _older(d, keep_days)
            reason = f"older than {keep_days}d" if old else "newer than keep_days"
            out.append(Item(str(d), "cache", _tree_size(d), old, reason))
    return out


def collect(root: Path, loaded: config.Loaded, home: Path) -> list[Item]:
    keep_days = int(loaded.get("cleanup.keep_days"))
    items = worktrees(root, loaded, _live_runs(home)) + branches(root, loaded)
    items += run_dirs(home, keep_days) + logs(home, int(loaded.get("cleanup.max_log_mb")))
    if loaded.get("cleanup.docker") and loaded.get("cleanup.docker_label"):
        items += docker(str(loaded.get("cleanup.docker_label")))
    if loaded.get("cleanup.caches"):
        items += caches(list(loaded.get("cleanup.cache_globs")), keep_days)
    return items


def _rotate(path: Path) -> None:
    """Keep the newest part: the live log becomes `.1`, the one older `.1` (the rest) is removed."""
    rotated = path.with_name(path.name + ".1")
    if rotated.exists():
        rotated.unlink()
    path.rename(rotated)


def _remove(item: Item, root: Path) -> None:
    if item.kind in ("worktree", "run", "cache"):
        shutil.rmtree(item.target, ignore_errors=True)
    elif item.kind == "branch":
        _git(root, "branch", "-D", item.target)
    elif item.kind == "log":
        _rotate(Path(item.target))
    elif item.kind == "docker-volume":
        subprocess.run(["docker", "volume", "rm", item.target], capture_output=True, check=False)
    elif item.kind == "docker-image":
        subprocess.run(["docker", "image", "rm", item.target], capture_output=True, check=False)


def apply(items: list[Item], root: Path, loaded: config.Loaded) -> list[Item]:
    """Remove every safe candidate; prune a worktree's checkout before any branch is deleted."""
    checkouts = {str(wt): ck for wt, ck in _roots(root, loaded)}
    removed = []
    for item in (i for i in items if i.remove):  # worktrees precede branches in the list
        _remove(item, root)
        if item.kind == "worktree" and (ck := checkouts.get(str(Path(item.target).parent))):
            _git(ck, "worktree", "prune")  # free the branch so a later branch -D succeeds
        removed.append(item)
    return removed


def disk_pct(path: Path) -> int:
    usage = shutil.disk_usage(path)
    return round(usage.used * 100 / usage.total)


def inode_pct(path: Path) -> int:
    """Inodes used on path's filesystem; 0 where the platform has no statvfs (Windows)."""
    if not hasattr(os, "statvfs"):
        return 0
    stat = os.statvfs(path)
    return round((stat.f_files - stat.f_favail) * 100 / stat.f_files) if stat.f_files else 0


def busiest_gauge(root: Path, loaded: config.Loaded) -> tuple[str, int]:
    """The fullest disk or inode gauge of a worktree root or the temp dir (home as a fallback)."""
    dirs = [wt for wt, _ in _roots(root, loaded) if wt.is_dir()] or [Path.home()]
    gauges = [(f"{path} {kind}", measure(path))
              for path in [*dirs, Path(tempfile.gettempdir())]
              for kind, measure in (("disk", disk_pct), ("inodes", inode_pct))]
    return max(gauges, key=lambda gauge: gauge[1])


def _human(size: int) -> str:
    return f"{size / MB:.1f} MB"


def _report(items: list[Item], removed: list[Item], *, applied: bool, as_json: bool) -> None:
    if as_json:
        shape = [{"target": i.target, "kind": i.kind, "size": i.size, "remove": i.remove,
                  "reason": i.reason} for i in items]
        print(json.dumps({"applied": applied, "items": shape, "removed": len(removed)}))
        return
    for item in items:
        verb = "removed" if applied and item.remove else "would remove" if item.remove else "keep"
        print(f"{verb:<12} {item.kind:<13} {_human(item.size):>9}  {item.target}  ({item.reason})")
    if applied:
        total = sum(i.size for i in removed)
        print(f"clean: removed {len(removed)} item(s), {_human(total)}" if removed
              else "clean: removed nothing")
    else:
        candidates = [i for i in items if i.remove]
        print(f"clean: {len(candidates)} candidate(s), {_human(sum(i.size for i in candidates))}; "
              "run with --apply to remove them")


def main(argv: list[str]) -> int:
    flags = set(argv)
    try:
        root = config.project_root()
    except ValueError as error:
        print(f"dl clean: {error}", file=sys.stderr)
        return 2
    loaded = config.load(root)
    if loaded.error:
        print(f"dl clean: {loaded.error}; the defaults are used", file=sys.stderr)
    threshold = int(loaded.get("cleanup.threshold_pct"))
    if "--when-full" in flags and busiest_gauge(root, loaded)[1] < threshold:
        return 0
    items = collect(root, loaded, Path.home())
    removed = apply(items, root, loaded) if "--apply" in flags else []
    _report(items, removed, applied="--apply" in flags, as_json="--json" in flags)
    remote = str(loaded.get("branches.remote"))
    integration = str(loaded.get("branches.integration"))
    if ("--json" not in flags and not _git(root, "rev-parse", "--git-dir").returncode
            and _integration_ref(root, remote, integration) is None):
        print(f"clean: merge status not checked — no {remote}/{integration} or {integration}")
    return 0
