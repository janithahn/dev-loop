"""`dl archive <slug>`: move a merged change to `paths.archive/<yyyy-mm-dd>-<slug>` by `git mv`.

Refused when the change's branch (`branches.change_prefix` + slug) exists and is not merged into
`branches.integration`. One `archive` record (from, to) is written first, so it travels with the
folder.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import Loaded, load, project_root
from .records.record import write


def _git(root: Path, *args: str, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False, timeout=timeout
    )


def unmerged(root: Path, slug: str) -> bool:
    """True when the change's branch exists and its tip is not in the integration branch."""
    cfg = load(root)
    branch = f"{cfg.get('branches.change_prefix')}{slug}"
    integration, remote = str(cfg.get("branches.integration")), str(cfg.get("branches.remote"))
    if _git(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}").returncode:
        return False  # no branch left: deleted after its merge
    try:  # local main may lag a land: compare against the remote's integration branch
        fetched = _git(root, "fetch", remote, integration, timeout=60)
    except subprocess.TimeoutExpired:
        raise ValueError(f"git fetch {remote} {integration} timed out: not run") from None
    if fetched.returncode:
        raise ValueError(f"git fetch {remote} {integration} failed: not run")
    base = f"{remote}/{integration}"
    return _git(root, "merge-base", "--is-ancestor", branch, base).returncode != 0


def archive(root: Path, slug: str) -> Path:
    """Move the change; ValueError names why not. Returns the new folder."""
    cfg = load(root)
    source = root / str(cfg.get("paths.changes")) / slug
    if not slug.strip(".") or Path(slug).name != slug or not source.is_dir():
        raise ValueError(f"no change {slug!r}")
    prefix = str(cfg.get("branches.change_prefix"))
    if _git(root, "branch", "--show-current", timeout=30).stdout.strip() != f"{prefix}{slug}":
        raise ValueError("run dl archive from the change's worktree")
    if unmerged(root, slug):
        raise ValueError(f"{slug}: its branch is not merged into {cfg.get('branches.integration')}")
    base = f"{cfg.get('branches.remote')}/{cfg.get('branches.integration')}"
    if _git(root, "merge", "--ff-only", base, timeout=60).returncode:
        raise ValueError(f"cannot fast-forward to {base}: not run")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")  # noqa: UP017 - 3.10 hosts
    target = root / str(cfg.get("paths.archive")) / f"{today}-{slug}"
    if target.exists():
        raise ValueError(f"{target.name}: already archived")
    moved = {"from": source.relative_to(root).as_posix(), "to": target.relative_to(root).as_posix()}
    write(root, slug, {"kind": "archive", **moved})
    target.parent.mkdir(parents=True, exist_ok=True)
    done = _git(root, "mv", str(source), str(target))
    if done.returncode:
        raise ValueError(f"git mv failed: {done.stderr.strip()}")
    _land(root, cfg, slug)
    return target


def _land(root: Path, cfg: Loaded, slug: str) -> None:
    """Commit the move, check it touches only paths.changes, push it to integration."""
    changes = str(cfg.get("paths.changes")).strip("/")
    remote, integration = str(cfg.get("branches.remote")), str(cfg.get("branches.integration"))
    for step in (("add", "-A", changes), ("commit", "-q", "-m", f"archive {slug}")):
        done = _git(root, *step, timeout=60)
        if done.returncode:
            raise ValueError(f"git {step[0]} failed: {done.stderr.strip()}")
    diff = _git(root, "diff", "--name-only", f"{remote}/{integration}", "HEAD", timeout=60)
    outside = [n for n in diff.stdout.split() if not n.startswith(f"{changes}/")]
    if diff.returncode or outside:
        raise ValueError(f"the move touches paths outside {changes}: {outside[:3]}")
    try:
        pushed = _git(root, "push", remote, f"HEAD:refs/heads/{integration}", timeout=120)
    except subprocess.TimeoutExpired:
        raise ValueError("git push timed out: not run") from None
    if pushed.returncode:
        raise ValueError("integration moved: run dl archive again")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: dl archive <slug>", file=sys.stderr)
        return 2
    try:
        print(archive(project_root(), argv[0]))
    except ValueError as error:
        print(f"dl archive: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
