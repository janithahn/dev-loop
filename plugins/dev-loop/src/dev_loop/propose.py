"""`dl propose <slug> [--title T] [--resume]`: a change folder and a worktree on its own branch.

`<branches.remote>/<branches.integration>` is fetched first. Then the worktree is created, on
`branches.change_prefix<slug>` from that ref, so the change folder is written on the change's
own branch.
Prints the worktree path. Already on that branch: the folder is written in place, no fetch,
no worktree, and the root is printed. An existing branch is refused unless `--resume`: then the
worktree on it is used, or one added on it, and its folder continued.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from .config import project_root, required

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PLUGIN = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parents[2])


def fill(text: str, values: dict[str, str]) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda found: values.get(found[1], ""), text)


def write_folder(folder: Path, slug: str, title: str) -> None:
    today = date.today().isoformat()
    values = {"title": title, "change_name": slug, "date": today, "updated": today,
              "problem": "", "solution": "", "questions": ""}
    folder.mkdir(parents=True)
    for name in ("proposal.md", "status.md"):
        template = (PLUGIN / "templates" / "change" / name).read_text(encoding="utf-8")
        (folder / name).write_text(fill(template, values), encoding="utf-8", newline="\n")
    (folder / "records.jsonl").touch()


def fetch(root: Path, remote: str, integration: str) -> None:
    done = subprocess.run(["git", "fetch", remote, integration], cwd=root,
                          capture_output=True, text=True, check=False)
    if done.returncode:
        raise ValueError(done.stderr.strip().splitlines()[-1] if done.stderr else "git failed")


def worktree(root: Path, branch: str, base: str | None, slug: str) -> Path:
    """Add a worktree on a new branch from base, or on the existing branch when base is None.

    The existing branch is added with --force: its only entry is a prunable one (see `held`).
    """
    path = root.parent / f"{root.name}-wt" / slug
    start = ["-b", branch, str(path), base] if base else ["--force", str(path), branch]
    done = subprocess.run(["git", "worktree", "add", *start], cwd=root,
                          capture_output=True, text=True, check=False)
    if done.returncode:
        error = done.stderr.strip().splitlines()[-1] if done.stderr else "git failed"
        raise ValueError(f"{branch} cannot be made: {error}")
    return path


def git_lines(root: Path, *args: str) -> list[str]:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    return done.stdout.splitlines()


def held(root: Path, branch: str) -> Path | None:
    """The worktree checked out on branch, if any; a `prunable` entry (folder not seen) is none.

    Never pruned here: an entry not seen now may be a live checkout on an unmounted drive.
    """
    listing = "\n".join(git_lines(root, "worktree", "list", "--porcelain"))
    for entry in listing.split("\n\n"):
        lines = entry.splitlines()
        if f"branch refs/heads/{branch}" in lines and not any(
                x.startswith("prunable") for x in lines):
            return Path(lines[0].removeprefix("worktree "))
    return None


def change_tree(root: Path, branch: str, branches: dict[str, str], slug: str) -> Path:
    """The worktree on an existing branch (found or added), else a new branch fetched first."""
    if git_lines(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
        return held(root, branch) or worktree(root, branch, None, slug)
    remote, integration = branches["remote"], branches["integration"]
    try:
        fetch(root, remote, integration)
    except ValueError as error:
        raise ValueError(f"cannot fetch {remote}/{integration}: {error}") from None
    return worktree(root, branch, f"{remote}/{integration}", slug)


def carry(root: Path, tree: Path) -> list[str]:
    """Copy the files kept local in root (skip-worktree, info/exclude) into the new worktree."""
    skipped = [x[2:] for x in git_lines(root, "ls-files", "-v") if x.startswith("S ")]
    exclude = Path(root, git_lines(root, "rev-parse", "--git-path", "info/exclude")[0])
    excluded = git_lines(root, "ls-files", "-o", "-i", "--directory",
                         f"--exclude-from={exclude}") if exclude.is_file() else []
    carried = []
    for name in [*skipped, *excluded]:
        source, target = root / name, tree / name
        if not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
        carried.append(name)
    if skipped:
        subprocess.run(["git", "update-index", "--skip-worktree", *skipped], cwd=tree,
                       capture_output=True, check=False)
    return carried


def main(argv: list[str]) -> int:
    title = argv[argv.index("--title") + 1] if "--title" in argv[:-1] else None
    slug = argv[0] if argv and not argv[0].startswith("--") else ""
    if not SLUG.match(slug):
        print("dl propose: give a slug of a-z, 0-9 and '-'", file=sys.stderr)
        return 2
    root = project_root()
    values = required(root).values
    branch = values["branches"]["change_prefix"] + slug
    current = subprocess.run(["git", "branch", "--show-current"], cwd=root,
                             capture_output=True, text=True, check=False).stdout.strip()
    if current == branch:
        folder = root / values["paths"]["changes"] / slug
        if folder.exists():
            print(f"dl propose: {values['paths']['changes']}/{slug} exists; nothing written",
                  file=sys.stderr)
            return 1
        write_folder(folder, slug, title or slug)
        print(root)
        return 0
    if "--resume" not in argv and git_lines(root, "rev-parse", "--verify", "--quiet",
                                            f"refs/heads/{branch}"):
        tree = held(root, branch)
        where = f"worktree {tree}" if tree else "no worktree"
        print(f"dl propose: {branch} already exists ({where}); dl propose {slug} --resume "
              "continues it", file=sys.stderr)
        return 1
    try:
        path = change_tree(root, branch, values["branches"], slug)
    except ValueError as error:
        print(f"dl propose: {error}", file=sys.stderr)
        return 1
    folder = path / values["paths"]["changes"] / slug
    if folder.exists():
        print(f"dl propose: continues an existing change: {values['paths']['changes']}/{slug}",
              file=sys.stderr)
    else:
        write_folder(folder, slug, title or slug)
    if carried := carry(root, path):
        print(f"dl propose: carried local files: {', '.join(carried)}", file=sys.stderr)
    print(path)
    return 0
