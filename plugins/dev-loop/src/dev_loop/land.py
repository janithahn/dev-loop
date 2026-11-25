"""`dl land <pr>... [--keep-source]`: gate, approve and merge PRs into the integration branch.

Per PR: refuse a target other than `branches.integration`, always `branches.release`, a PR not
active, or an active review-finding (`crid:`) thread; the caller lands only PRs the loop marked
done. Reuse the head's gate build or re-queue the Build policy `gates.policy` names, again on a
cancel or an expiry up to 3 times, never while a build runs; vote 10, set auto-complete (no
fast-forward; source deleted unless --keep-source), await the merge. One lander per machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from dev_loop import config
from dev_loop.hosts import HostError, ado

REQUEUES = 3  # gate runs after the first
POLL, POLLS = 60, 6 * 60  # six hours of one-minute polls for a build or a merge
PREVIEW = "api-version=7.1-preview.1"
NOT_RUN = "not run: dl land supports ado in this release"


class Stop(Exception):  # noqa: N818 - the run stops with this reason
    """Why the run stops."""


def _poll(path: str, done: Callable[[dict[str, Any]], bool], late: str) -> dict[str, Any]:
    for _ in range(POLLS):
        found = ado.request("GET", path)
        if done(found):
            return found
        time.sleep(POLL)
    raise Stop(f"{late} after six hours")


def _build_of(found: dict[str, Any]) -> Any:  # noqa: ANN401 - the host's build id
    return (found.get("context") or {}).get("buildId")


def _gate_path(gate: dict[str, Any]) -> str:
    return f"/_apis/policy/evaluations/{gate['evaluationId']}?{PREVIEW}"


def _evaluation(pr: int, project: str, name: str) -> dict[str, Any]:
    """Return the PR's one Build policy evaluation whose name starts with `name`."""
    pid = ado.org_request("GET", f"/_apis/projects/{project}")["id"]
    artifact = f"vstfs:///CodeReview/CodeReviewId/{pid}/{pr}"
    found = ado.request("GET", f"/_apis/policy/evaluations?artifactId={artifact}&{PREVIEW}")
    builds = [e for e in found["value"] if e["configuration"]["type"]["displayName"] == "Build"
              and e["configuration"]["settings"].get("displayName", "").startswith(name)]
    if len(builds) != 1:
        raise Stop(f"{len(builds)} Build policies match gates.policy={name!r}")
    return dict(builds[0])


def _running(pr: int, gate: dict[str, Any], head: str) -> int | None:
    """Return a gate build already queued or running for the PR's current head, if any."""
    definition = gate["configuration"]["settings"]["buildDefinitionId"]
    found = ado.request("GET", f"/_apis/build/builds?definitions={definition}"
                        f"&branchName=refs/pull/{pr}/merge&statusFilter=inProgress,notStarted")
    ours = [b["id"] for b in found["value"] if b.get("triggerInfo", {}).get("pr.sourceSha") == head]
    return int(ours[0]) if ours else None


def _queue(gate: dict[str, Any]) -> int:
    """Re-queue the policy evaluation; return the new build's id."""
    path = _gate_path(gate)
    old = _build_of(ado.request("GET", path))
    ado.request("PATCH", path, {})
    found = _poll(path, lambda e: _build_of(e) not in (None, old), "the gate started no build")
    return int(_build_of(found))


def _gate_green(pr: int, gate: dict[str, Any], head: str) -> int:
    """Return the build the gate approved for the head; queue again on a cancel or an expiry."""
    build, last, result = _running(pr, gate, head), 0, ""
    for _ in range(REQUEUES + 1):
        last = build or _queue(gate)
        done = _poll(f"/_apis/build/builds/{last}", lambda b: b["status"] == "completed",
                     f"build {last} running")
        result = done["result"]
        policy = _poll(_gate_path(gate), lambda e: e["status"] not in {"queued", "running"}
                       or _build_of(e) != last, f"gate on build {last}")
        if result == "succeeded" and policy["status"] == "approved" and _build_of(policy) == last:
            return last
        if result not in {"succeeded", "canceled"}:
            raise Stop(f"build {last} {result}")
        build = None
    raise Stop(f"gate not approved in {REQUEUES + 1} runs (last: build {last} {result})")


def _no_findings(pr: int) -> None:
    review = [t["id"] for t in ado.threads(pr)
              if t.get("status", "active") == "active"  # a system thread may carry no status
              and "crid:" in ((t.get("comments") or [{}])[0].get("content") or "")]
    if review:
        raise Stop(f"review threads still active: {review}")


def _approve(pr: int, title: str, *, keep_source: bool) -> None:
    me = ado.org_request("GET", "/_apis/connectionData?api-version=7.1-preview")
    me = me["authenticatedUser"]
    ado.pr_vote(pr, me["id"], 10)
    options = {"mergeStrategy": "noFastForward", "deleteSourceBranch": not keep_source,
               "mergeCommitMessage": f"Merge PR {pr}: {title}"}
    ado.pr_auto_complete(pr, me["id"], options)


def _not_release(target: str, release: object) -> None:
    """Refuse the configured release branch, whatever the integration setting says."""
    if release and target == release:
        raise Stop(f"targets the release branch {release}: never landed")


def land(pr: int, loaded: config.Loaded, *, keep_source: bool) -> str:
    found = ado.pr_get(pr)
    target = found["targetRefName"].removeprefix("refs/heads/")
    integration, release = loaded.get("branches.integration"), loaded.get("branches.release")
    _not_release(target, release)
    if target != integration or found["status"] != "active":
        raise Stop(f"{found['status']}, targets {target}: lands active PRs into {integration} only")
    _no_findings(pr)
    head = found["lastMergeSourceCommit"]["commitId"]
    gate = _evaluation(pr, loaded.get("host.project"), loaded.get("gates.policy") or "")
    build = _gate_green(pr, gate, head)
    found = ado.pr_get(pr)
    status, now = found.get("mergeStatus"), found["lastMergeSourceCommit"]["commitId"]
    if status != "succeeded" or now != head:
        raise Stop(f"merge status {status}, head {now[:12]}")
    _no_findings(pr)
    _approve(pr, found["title"], keep_source=keep_source)
    found = _poll(f"/_apis/git/repositories/{loaded.get('host.repo')}/pullrequests/{pr}",
                  lambda p: p["status"] != "active" or p.get("mergeStatus") == "conflicts",
                  "approved; not merged")
    if found["status"] != "completed":
        raise Stop(f"approved, then {found['status']} with merge status {found.get('mergeStatus')}")
    return f"PR {pr}: build {build} green; approved; merged"


def _lock(path: Path) -> IO[str]:
    """Hold the lander's lock for the run; the kernel frees it when the process ends."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415 - one platform's lock

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415 - the other platforms' lock

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise Stop(f"another lander holds {path}") from None
    handle.seek(0)
    handle.truncate()
    print(os.getpid(), file=handle, flush=True)  # the holder's pid, for a person
    return handle


def _sh(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=False)


def _tail(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


def _moved(root: Path, one: str, other: str, rel: str) -> bool:
    """True when the two commits differ outside the change's folder (its records move)."""
    return _sh(root, "diff", "--quiet", one, other, "--", ".", f":(exclude){rel}").returncode != 0


def _no_local_findings(pr: str) -> None:
    from dev_loop.hosts import local  # noqa: PLC0415 - imported here to keep ado's path git-free

    notices = ("crid:skip", "crid:summary")  # not findings: a not-run notice and the summary
    active = [t["id"] for t in local.threads(pr)
              if t.get("status", "active") == "active"
              and "crid:" in (text := (t.get("comments") or [{}])[0].get("content") or "")
              and not any(n in text for n in notices)]
    if active:
        raise Stop(f"review threads still active: {active}")


def _rebased(root: Path, remote: str, integration: str, branch: str) -> str:
    """Return the branch head; raise Stop unless <remote>/<integration> is its ancestor."""
    _sh(root, "fetch", remote, integration)
    head = _sh(root, "rev-parse", branch).stdout.strip()
    base = _sh(root, "rev-parse", f"{remote}/{integration}").stdout.strip()
    if not head or not base:
        raise Stop(f"cannot read {branch} or {remote}/{integration}")
    if _sh(root, "merge-base", "--is-ancestor", base, head).returncode:
        raise Stop(f"not rebased on {remote}/{integration}: rebase before landing")
    return head


def _checked_out(root: Path, folder: Path, branch: str, head: str) -> None:
    """Raise Stop unless this checkout is the branch head, with the change's records committed."""
    here = _sh(root, "rev-parse", "HEAD").stdout.strip()
    edits = _sh(root, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    loose = _sh(root, "status", "--porcelain", "--", str(folder)).stdout.strip()
    if here != head or edits or loose:
        raise Stop(f"this checkout is not {branch} at {head[:12]} with everything committed: "
                   "land from the pull request's worktree")


def _gates(root: Path) -> None:
    from dev_loop import verify  # noqa: PLC0415 - the gates are verify.steps, run locally

    results = verify.run_all(root, set(), full=True)  # a gate never runs a step narrowed
    failed = [name for name, outcome, _ in results if outcome != "passed"]
    if not results or failed:
        raise Stop(f"gate failed: {', '.join(failed) or 'no verify steps configured'}")


def _done(folder: Path) -> None:
    """Raise Stop unless the change meets the done rule `dl ci done-rule` judges."""
    from dev_loop.records import record  # noqa: PLC0415 - only the local lander needs it

    if not record.tasks_done(folder):
        raise Stop(f"not done: {folder.name}: tasks open")
    if gaps := record.missing(folder):
        raise Stop(f"not done: {folder.name}: missing {'; '.join(gaps)}")


def land_local(pr: str, loaded: config.Loaded, *, keep_source: bool) -> str:
    """Land one local PR: the loop marked it clean, the change meets the done rule and the gates
    pass, then fast-forward and push.

    Never merges a branch <remote>/<integration> is not an ancestor of (an un-rebased branch).
    """
    from dev_loop.hosts import local  # noqa: PLC0415

    root = config.project_root()
    remote, integration = str(loaded.get("branches.remote")), str(loaded.get("branches.integration"))
    release = loaded.get("branches.release")
    found = local.pr_get(pr)
    target = found["targetRefName"].removeprefix("refs/heads/")
    branch = found["sourceRefName"].removeprefix("refs/heads/")
    _not_release(target, release)
    if target != integration:
        raise Stop(f"targets {target}: lands into {integration} only")
    folder = local.folder_of(pr)
    held = local.records(folder)
    rounds = [r for r in held if r.get("kind") == "review-clean"]
    if not rounds or rounds[-1].get("state") != "success":
        raise Stop("not done: the last review round is not clean")
    _done(folder)
    _no_local_findings(pr)
    head = _rebased(root, remote, integration, branch)
    sha, rel = str(rounds[-1].get("sha", "")), folder.relative_to(root).as_posix()
    if _moved(root, sha, head, rel):
        raise Stop(f"not done: the clean round is for {sha[:12]}, not the head {head[:12]}: "
                   "review again")
    if loaded.get("review.fallback") != "subagent" and not any(
            r.get("kind") == "review-run" and r.get("rc") == 0 and not _moved(
                root, str(r.get("head")), sha, rel) for r in held):
        raise Stop(f"not done: no review-run record for {sha[:12]}: run `dl review` on this head")
    _checked_out(root, folder, branch, head)
    _gates(root)
    if (push := _sh(root, "push", remote, f"{head}:refs/heads/{integration}")).returncode:
        raise Stop(f"push to {integration} failed: {_tail(push.stderr)}")
    local.mark_landed(pr)
    if not keep_source:
        _sh(root, "push", remote, "--delete", branch)
    return f"PR {pr}: gates green; {integration} fast-forwarded to {head[:12]}"


def _run(prs: list[Any], loaded: config.Loaded, lander: Callable[..., str],
         *, keep_source: bool) -> int:
    """Hold the one-per-machine lock and land each PR; a Stop ends the run at that PR."""
    if not prs:
        print("usage: dl land <pr>... [--keep-source]", file=sys.stderr)
        return 2
    pr: Any = ""
    try:
        with _lock(Path.home() / ".dev-loop" / "land.lock"):
            for pr in prs:
                print(lander(pr, loaded, keep_source=keep_source), flush=True)
    except (Stop, HostError) as stop:
        print(f"PR {pr}: {stop} - stopped" if pr != "" else f"{stop} - stopped", flush=True)
        return 1
    return 0


def main(argv: list[str]) -> int:
    prs = [arg for arg in argv if arg != "--keep-source"]
    loaded = config.load(config.project_root())
    if loaded.error:
        print(f"dl land: {loaded.error}", file=sys.stderr)
        return 2
    keep = "--keep-source" in argv
    provider = loaded.get("host.provider")
    if provider == "local":
        return _run(prs, loaded, land_local, keep_source=keep)
    if provider != "ado":
        print(NOT_RUN)
        return 2
    if not prs or not all(pr.isdigit() for pr in prs):
        print("usage: dl land <pr>... [--keep-source]", file=sys.stderr)
        return 2
    return _run([int(pr) for pr in prs], loaded, land, keep_source=keep)
