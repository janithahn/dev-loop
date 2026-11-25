---
name: land
description: Land pull requests the review loop marked done. `dl land` gates, approves and merges them into the integration branch, one lander per machine. Never into the release branch.
context: fork
agent: general-purpose
background: true
---

# land

This skill runs in a forked subagent, in the background. It sees no conversation, only
`$ARGUMENTS`: `<pr>... [--keep-source]`. With no PR named, stop and return that the input is
missing.

Land the named pull requests from the project root, after the loop marked each one done.

1. **Before.** Land a PR only when the loop marked it done: its change is done, its last review
   round filed no blocker and no major, and every minor is deferred with its thread closed.
   The lander does not judge that; you do. For each PR read
   `echo '{"pr": <pr>}' | dl host threads --json`: a PR with an active thread, or without the
   loop's closed summary thread, is not done. Leave it out and name it in the result.
2. **Run** `dl land <pr>... [--keep-source]` on the PRs that are done. Per PR it:
   - refuses a target other than `branches.integration`, and always one equal to
     `branches.release` (a release is merged by a person);
   - refuses a PR with an active review-finding (`crid:`) thread;
   - reuses the gate build already running for the PR's head, or re-queues the Build policy
     `gates.policy` names; a cancelled or expired build is retried up to 3 times, and it never
     re-queues while a build is in progress;
   - approves (vote 10), sets auto-complete (no fast-forward; the source branch is deleted
     unless `--keep-source`) and waits for the merge.
   On a local host it instead refuses a change that fails the done rule (`dl ci done-rule`:
   open tasks or a missing record, each named), runs the verify steps as the gates and
   fast-forwards the integration branch. It runs from the PR's worktree, at the branch head,
   with everything committed.
   One lander runs per machine (a lock under `~/.dev-loop/`); a second one stops at once.
3. **After.** A stopped PR names its reason; it is reported, not fixed here.

Rules:
- Never into the release branch, and never a PR the loop has not marked done.
- On a github host it prints "not run" and exits 2: land by hand there.

## Return

A short result, not logs: one line per PR (merged, stopped with its reason, or left out as not
done and why).
