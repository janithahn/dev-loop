"""`dl pr <change> [--target BRANCH] [--base BRANCH]`: rebase, size-cap, push, open the PR.

The target is `branches.integration` unless named; `branches.release` is refused (a release is
the people's step). `--base` names the change branch this part sits on (a part stacked by merge on
an open PR, its own PR into the target): the size cap, the stray-commit span and the pre-push
hook judge from it, and the part rebases on it (a rebase on the target would rewrite the
parent's commits); the PR stays on the target. A base equal to this branch, the release or a
protected branch is refused. It is remembered in
`git config branch.<branch>.dl-base`, so a later `dl pr`, the hook and `dl ci` judge from it too;
once the remote no longer has it (merged and deleted) it is forgotten and the target used.
Only the change's own branch is pushed, with `--force-with-lease`. The title
is proposal.md's first heading less a leading "Proposal: ", the body its text, ending with
`host.pr_footer` when set; a part's title carries its latest started part (the last `## Part`
with a task not todo) and a stacked part's body names the parent's PR (else branch). An open
PR from the branch is reported, never duplicated. Prints the PR id last on stdout; the review-loop
nudge goes to stderr unless `review.auto_trigger` is false, so a person at a terminal sees it too.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from .approval import approved, refusal
from .ci.size import remembered, size_cap
from .config import project_root, required
from .hosts import HostError, adapter
from .records import record
from .review.review_verdict import spec_lines
from .tasklist import parse_blocks

BODY_MAX = 3500  # the host's description limit is 4000 characters
PUSH_TAIL = 20  # lines of the pre-push hook's and git's output shown when a push fails
NUDGE = """[pr-loop] PR {pr_id} is open. Run the review loop on it before a person reads it:
/dev-loop:review {pr_id}. Blockers and majors are fixed and rechecked until a round files none;
minors go to the change's follow-ups.md. The loop never votes on or merges the PR."""


def sh(root: Path, cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=root, input=stdin, capture_output=True, text=True, check=False)


def refuse(text: str, code: int = 2) -> int:
    print(f"dl pr: {text}", file=sys.stderr)
    return code


def title_and_body(proposal: Path, footer: str | None) -> tuple[str, str]:
    text = proposal.read_text(encoding="utf-8") if proposal.exists() else ""
    heads = [line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("# ")]
    title = re.sub(r"^proposal:\s*", "", heads[0], flags=re.IGNORECASE) if heads else ""
    body = text[:BODY_MAX].rstrip()
    return (title or proposal.parent.name)[:128], (
        f"{body}\n\n{footer}" if footer else body)


def started_part(tasks: Path) -> str | None:
    """The latest part with a task begun (not todo), as `tasklist.done` reads parts."""
    try:
        blocks = parse_blocks(tasks.read_text(encoding="utf-8") if tasks.exists() else "")
    except ValueError:  # a bad task head: the plain title, never a traceback after the push
        return None
    letters = [task["part"] for task in blocks if task.get("part") and task["status"] != "todo"]
    return letters[-1] if letters else None


def stacked(tasks: Path, parent: str, host: ModuleType, pair: tuple[str, str]) -> tuple[str, str]:
    """A part's title (its latest started part) and, stacked on a parent, a body naming it."""
    title, body = pair
    if letter := started_part(tasks):
        suffix = f" (part {letter})"
        title = title[:128 - len(suffix)] + suffix
    if not parent:
        return title, body
    on = host.pr_for_branch(parent)
    head = (f"Stacked on PR {on} ({parent}); merge that PR first." if on is not None
            else f"Stacked on {parent}; merge that branch first.")
    return title, f"{head}\n\n{body}"


def stack_parent(root: Path, branch: str, base: str | None) -> str | None:
    """Return the parent branch the part is judged from: the named one, else the remembered one.

    "" when there is none or the remote no longer has the remembered one (it is then forgotten);
    None when it is refused: this branch, the release or a protected branch, or unfetchable.
    """
    cfg = required(root)
    remote = str(cfg.get("branches.remote"))
    key = f"branch.{branch}.dl-base"
    parent = base or remembered(root, branch)
    if not parent:
        return ""
    if parent in {branch, cfg.get("branches.release"), *cfg.get("branches.protected")}:
        refuse(f"--base {parent} is this branch, the release or a protected branch; name the "
               "change branch this part sits on", 1)
        return None
    gone = sh(root, ["git", "ls-remote", "--exit-code", remote, f"refs/heads/{parent}"])
    if not base and gone.returncode == 2:  # ls-remote --exit-code: no such ref
        sh(root, ["git", "config", "--unset", key])
        print(f"dl pr: {remote} has no branch {parent} now; forgot it, judged from the target",
              file=sys.stderr)
        return ""
    if (done := sh(root, ["git", "fetch", remote, parent])).returncode:
        refuse(f"git fetch {remote} {parent} failed: {done.stderr.strip()}", 1)
        return None
    sh(root, ["git", "config", key, parent])
    return parent


def publish(root: Path, branch: str, target: str, base: str | None = None) -> int:
    """Rebase on and size-cap from the parent, else the target, then push; first failure's exit.

    base: the parent branch the part sits on; None takes the remembered one, else the target.
    A stacked part rebases on its parent: a rebase on the target would rewrite the parent's
    commits, and the part would then count them and all the target gained since.
    """
    remote = str(required(root).get("branches.remote"))
    fetch = ["git", "fetch", remote, target]
    if (done := sh(root, fetch)).returncode:
        return refuse(f"{' '.join(fetch)} failed: {done.stderr.strip()}", 1)
    if (parent := stack_parent(root, branch, base)) is None:
        return 1
    judged = f"{remote}/{parent or target}"
    status = sh(root, ["git", "status", "--porcelain", "--untracked-files=no"]).stdout
    if tracked := [line for line in status.splitlines() if line.strip()]:
        return refuse(f"uncommitted changes to tracked files ({tracked[0][3:]}, "
                      f"{len(tracked)} in all); commit or stash them: "
                      "nothing rebased, nothing pushed", 1)
    if (rebase := sh(root, ["git", "rebase", "--rebase-merges", judged])).returncode:
        sh(root, ["git", "rebase", "--abort"])
        why = [x for x in rebase.stderr.splitlines() if x.startswith(("error:", "CONFLICT"))]
        return refuse(f"rebase on {judged} stopped; aborted, nothing pushed: "
                      f"{(why or rebase.stderr.strip().splitlines() or [''])[0]}", 1)
    sys.stdout.flush()
    if size_cap(root, judged):  # judged as `dl ci size-cap` judges
        return refuse("over the size cap; split the change or record a waiver", 1)
    here = sh(root, ["git", "rev-parse", "HEAD"]).stdout.strip()
    there = sh(root, ["git", "rev-parse", "--verify", "-q", f"refs/remotes/{remote}/{branch}"])
    if here and there.returncode == 0 and there.stdout.strip() == here:
        print(f"push: {remote}/{branch} is at HEAD")
        return 0
    return push(root, remote, branch, judged)


def push(root: Path, remote: str, branch: str, base: str) -> int:
    """Push the change's branch; on a failure show what the pre-push hook and git said.

    The hook is told the base `dl pr` judged (PREPUSH_BASE), for this one push only.
    """
    old = os.environ.get("PREPUSH_BASE")
    os.environ["PREPUSH_BASE"] = base
    try:
        done = sh(root, ["git", "push", "--force-with-lease", remote, branch])
    finally:
        if old is None:
            os.environ.pop("PREPUSH_BASE", None)
        else:
            os.environ["PREPUSH_BASE"] = old
    if not done.returncode:
        return 0
    # git gives a pre-push hook its own stdout: the hook's lines are there, git's on stderr
    text = re.sub(r"://[^/@\s]+@", "://", done.stdout + done.stderr)
    said = [x for x in text.splitlines() if x.strip()] or [f"git push exited {done.returncode}"]
    if said[:-1]:
        print("\n".join(said[:-1][-PUSH_TAIL:]), file=sys.stderr)
    return refuse(f"push failed: {said[-1]}", 1)


def stray_commit(root: Path, change: str, target: str) -> str | None:
    """The first own commit touching source whose subject is no task's: a first raise only."""
    cfg = required(root)
    globs = cfg.get("size.production") or []
    if not globs:
        return None
    me = sh(root, ["git", "config", "user.email"]).stdout.strip()
    span = f"{cfg.get('branches.remote')}/{target}..HEAD"
    fmt = "--format=%h%x09%ae%x09%s"
    log = sh(root, ["git", "log", "--first-parent", "--no-merges", fmt, span, "--",
                    *[f":(glob){g}" for g in globs]])
    tasks = root / cfg.get("paths.changes") / change / "tasks.md"
    text = tasks.read_text(encoding="utf-8") if tasks.exists() else ""
    ok = {f"{t['id']}: {t['title']}" for t in parse_blocks(text)} | {f"{change}: simplify"}
    for row in log.stdout.splitlines():
        sha, author, subject = row.split("	", 2)
        if author == me and subject not in ok:
            return (f'{sha} "{subject}" changes source outside a task commit: drop it '
                    f"(git rebase --onto {sha}^ {sha}) and brief a builder, who amends "
                    "its task's commit")
    return None


def unsimplified(folder: Path, change: str) -> str | None:
    """Why a first PR is refused: no simplifier record after the last round (file order)."""
    kinds = [r["kind"] for r in record.read(folder) if r["kind"] in {"round", "simplifier"}]
    if kinds and kinds[-1] == "simplifier":
        return None
    return ("no simplifier record since the last review round: dispatch the simplifier to a "
            f"builder (one commit `{change}: simplify`, none when nothing changes), run "
            f"`dl record {change} simplifier edits=<what|none>`, then `dl verify {change}`; "
            "nothing pushed")


def arguments(argv: list[str]) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(prog="dl pr")
    parser.add_argument("change")
    parser.add_argument("--target")
    parser.add_argument("--base", help="the change branch this part sits on; remembered")
    try:
        return parser.parse_args(argv)
    except SystemExit:
        return None


def main(argv: list[str]) -> int:
    if (args := arguments(argv)) is None:
        return 2
    root = project_root()
    cfg = required(root)
    if not approved(root / cfg.get("paths.changes") / args.change):
        return refuse(refusal(args.change))
    release, integration = cfg.get("branches.release"), cfg.get("branches.integration")
    target = args.target or integration
    if release and target == release:
        return refuse(f"{target} is the release branch; a release is opened by a person")
    branch = sh(root, ["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if branch in {integration, release, "HEAD", "", *cfg.get("branches.protected")}:
        return refuse(f"not on a change branch ({branch or 'none'}); nothing pushed")
    spec = root / cfg.get("paths.changes") / args.change / "spec.md"
    if not spec_lines(spec.read_text(encoding="utf-8") if spec.exists() else ""):
        return refuse("spec.md has no done-when bullet")
    try:
        host = adapter()
        found = host.pr_for_branch(branch)
        if found is None and (why := unsimplified(spec.parent, args.change)):
            return refuse(why)
        if found is not None and not args.target:  # a stacked part's PR targets its parent
            target = host.pr_get(found)["targetRefName"].removeprefix("refs/heads/")
        if (parent := stack_parent(root, branch, args.base)) is None:
            return 1
        if found is None and (stray := stray_commit(root, args.change, parent or target)):
            return refuse(stray)
        if code := publish(root, branch, target, parent or None):
            return code
        title, body = title_and_body(spec.parent / "proposal.md", cfg.get("host.pr_footer"))
        if found is None:
            title, body = stacked(spec.parent / "tasks.md", parent, host, (title, body))
        pr_id = found if found is not None else host.pr_create(branch, target, title, body)
    except HostError as error:
        return refuse(str(error), 1)
    if found is None and cfg.get("review.auto_trigger"):
        print(NUDGE.format(pr_id=pr_id), file=sys.stderr)
    print(f"{'open PR' if found is not None else 'PR'} {branch} -> {target}: {pr_id}")
    return 0
