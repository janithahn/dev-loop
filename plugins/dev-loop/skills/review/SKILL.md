---
name: review
description: Review a raised pull request with the dev loop's own pipeline, fix its blockers and majors with a test each, defer its minors to the change's follow-ups, and recheck the fixes in incremental rounds until a round files no blocker and no major. Use right after /dev-loop:pr, and when someone says "review and fix my PR", "run the review loop", "fix the review comments on PR <n>", "resolve the review threads" or "make this PR clean".
---

# review

This skill runs in the main context, so it can start the builders that fix findings. Its input is
`$ARGUMENTS`.

Make a raised PR clean before a person reads it. One round is: run the review → read what it
filed → fix the blockers and majors → defer the minors → verify → reply and resolve → log the
lessons → record the round. The loop never votes on the PR, and it never merges it.

Every host call goes through `dl host <verb> --json` (the body on stdin; the adapter holds the
credential, so never load, print or paste one). Every record goes through `dl record` or
`dl lesson`; never edit a record file by hand.

## Before the first round

1. **The inputs.** `$ARGUMENTS` is `<pr> [<change>]`. With no PR id, take the PR of the current
   branch: `echo '{"branch": "<git branch --show-current>"}' | dl host pr-for-branch --json`.
   With no change, take the folder under `dl config get paths.changes` named by the branch's
   last path segment. When neither yields one, stop and return that the input is missing.
2. **The change's paperwork.** Read `proposal.md` and `spec.md` in the change's folder
   (`dl config get paths.changes`). A fix is judged against their requirements and guardrails,
   not against the diff. A guardrail is never undone to silence a finding.
3. **The pitfalls.** Run `dl records pitfalls <the PR's files>`. When a finding
   matches a class there, fix it as that class says and look for the same class elsewhere in
   the diff.
4. **The worktree.** Work from the PR's own branch at its pushed head, with no tracked edits.
   `git status --porcelain --untracked-files=no` prints nothing. Uncommitted records of this
   change (an incident, a task status): commit and push them first. Anything else: stop and say so.
5. **The track.** `dl records fast-track` (the integration branch's `review.fast_track`, as the
   done rule reads it). When it is `false` (the default), skip the next section: the loop runs
   as written below.

## Fast track (only when `review.fast_track` is true)

The project chose to run one round and fix only what is serious. The rest is recorded as debt,
never dropped. Everything below applies, with these changes:

- **One round.** Run round 1 as written, then go to "When the loop ends". No round 2, whatever
  it filed.
- **Fix only the serious classes.** A blocker or major is fixed (with its test, its lesson and
  its reply, as in steps 3 to 6) only when its class is in `dl config get
  review.fast_track_fix`: `law` (a rule the `review.governance` files or the project's
  instructions make), `security` (a hole an attacker or a leaked secret could use), `crash` (an
  unhandled error, data loss or corruption). A finding that fits none of the listed classes is
  not serious here.
- **Record every other blocker and major as debt:**
  `dl record <change> debt pr=<id> thread=<id> file=<path> severity=<blocker|major>
  "finding=<one line>" "why=<class>"` (no `|` in a value; the severity the review filed),
  then reply "Recorded as debt in the change's debt.md (fast track)" on its thread and set it
  `closed`.
- **Minors are deferred as always** (step 3). An item with no PR thread (found while planning)
  is recorded `dl record <change> follow-up pr=none thread=none ...`.
- **Done.** `dl status` counts the round clean when each blocker and major it filed is fixed or
  recorded as debt and no thread is active.
- **The summary thread** starts with `Fast track: N debt items`, N the debt records this PR
  wrote, and the summary says the same. A fast-track PR is never reported as clean: it is
  "fast track, N debt items".

## One round

### 1. Review: the pipeline, never this session

This session starts in the project root. `echo '{"pr": "<id>"}' | dl host pr-get --json` names the
branch (`sourceRefName`); `cd` to its worktree (`git worktree list`) before anything else. The
change is the branch's last path segment. An empty thread list is a new PR, not a missing one.

`dl review` runs the round (`pr-review.sh`; its header lists the environment):

```sh
PR_ID=<pr> REVIEW_BASE=<sha> dl review
```

- Round 1: leave `REVIEW_BASE` unset; the whole PR is reviewed.
- Round 2 and later: `REVIEW_BASE` = the commit the previous round reviewed (the PR head when
  that round ran, before its fixes). The round then reviews only the fixes and rechecks the
  earlier blockers and majors.
- A non-zero exit (no login, no CLI, over the line cap, not the pushed head, an empty diff) is
  **not a pass**. On a non-zero `dl review` exit, record nothing (no round, no set-status). Return
  `paused` with the FAIL line. Called again, run the same round again. Say which precondition
  failed and stop; never report it as clean. With
  `review.fallback` `never` (the default) the loop then returns "paused" and never reviews by
  subagent; the local `dl land` lands only a head a real `dl review` run reviewed clean (it writes
  a `review-run` record on exit). A verdict
  the harness refuses (`schema:` lines, exit 1) is an incomplete round: run it again.
Stop point: a second non-zero `dl review` exit on the same round in one turn returns `paused`
  with both FAIL lines; never a third run in that turn.

**Fallback, only when the pipeline cannot run and `dl config get review.fallback` is
`subagent`:** a fresh subagent reviews, never this session.
Dispatch the `dev-loop:code-reviewer` agent with the round's diff written to a file, `dl review context <touched files>` as its standard and the change's `proposal.md`,
telling it nothing about the fixes, the earlier rounds or what is believed fixed. Its findings are
posted with `dl host thread-post --json`, the summary says a subagent reviewed that round, and
this skill runs again from step 2.

### 2. Read what it filed

`echo '{"pr": <pr>}' | dl host threads --json`. Work only `active` threads whose first comment
ends in a `crid:` footer: those are findings. A thread without one is a person's comment; leave
it. `crid:skip` is the pipeline's not-run notice, not a finding. Read every finding before
changing anything: two findings are often halves of one defect.

### 3. Fix the blockers and majors; defer the minors

- **Blockers, then majors, each with a test** that pins the failure the finding describes.
  One new test function for each fixed thread whose fix changes source; a thread that repeats
  another's defect still gets its own, on the input that thread names.
  Tests are additive: never weaken, skip or delete a green test to go green.
- **A minor that touches the project's own law counts as a major** and is fixed: anything the
  `review.governance` files make a rule (`dl config get review.governance`). So does unneeded
  code (dead code, an unrequested abstraction, a speculative feature), and a done-when line the
  review marks failed.
- **Every other minor is deferred, not fixed:**
  `dl record <change> follow-up pr=<id> thread=<id> file=<path> "finding=<one line>" "why=<why>"`
  (no `|` in a value), then reply "Deferred to the change's follow-ups.md" on its thread and set
  it `closed`. A deferred minor never buys another round.
- A finding on a record, report or test file (nothing under the source tree changes) is fixed by
  correcting it and running what it claimed. It needs a lesson but no new test.
- **Fixes go through a builder.** Dispatch each blocker or major fix (one per finding, or
  grouped by file) to a builder. Its brief is what
  `echo '{"pr": <id>}' | dl host threads --json | dl review fix-brief <change> <thread>...`
  prints (one call per finding or per file group): the findings, the review's own standard and
  governance for those files, the proposal's limits and the spec bullets naming them. Send it
  unchanged but for the top 3 lessons of `dl records recall <change> <its files>` under their
  own `## Lessons` heading. This skill
  never edits product code, through Bash or otherwise. When a builder cannot be started, return
  `fix needed` with the findings and stop without recording the round.
- Keep the remote at one commit: fold the round's fixes in and `git push --force-with-lease`.
  Run `dl verify <change>` first. Never `--no-verify`.
Stop point: `dl verify <change>` runs at most twice on one head in a turn.

### 4. Verify against the finding

For each fixed thread, state what now happens in the exact scenario it described; where it
gave a concrete input, prove the fix with that input. A green build is not that proof.

### 5. Reply and resolve

On each fixed thread: `dl host thread-reply --json` with what changed and the test that pins it,
exactly `echo '{"pr": "<id>", "thread": <n>, "text": "<reply>"}' | dl host thread-reply --json`,
then `dl host thread-status --json` with `"status": "fixed"`. Resolve only what you fixed. A
finding you think is wrong: pin what the code does on its input with a test, say why, set it
fixed; the next round rechecks it.

### 6. Lessons

One lesson per fixed blocker or major, one lesson per thread set fixed, including a minor fixed
as a major (a thread repeating another's defect still gets one, naming it):

```sh
dl lesson <change> <high|medium> "Summary: <root cause>" \
  "Avoid: <how to write it right>. Self-check: <one grep, assertion or test>." \
  "Class: <a pitfalls row number | new: <short name>>" \
  "Guard: <a test path | review: <reason> | open>" "Files: <paths>" "Round: <n>"
```

`high` for a blocker, `medium` for a major. The root cause is why it was written that way, not
the finding restated. Ids, rules and code only: never customer data or document text. A field that quotes more than 40
characters (double quotes, or backticks around words) is refused: say it in your own words, and
name a long command's test or script instead. A one-word path or test id in backticks passes. Then
`dl record <change> lessons-logged count=<n>`; count is the change's total lessons so far.

### 7. Record the round, then decide

```sh
dl record <change> round pr=<id> round=<n> base=<sha|full> blocker=<b> major=<m> minor=<k> \
  fixed=<f> deferred=<c> active=<a> turns=<t> seconds=<s> tokens_in=<i> tokens_out=<o> \
  unmetered=<u>
```

The usage fields come from the `Usage:` line `dl review` prints last. A fallback subagent round
records `turns=0 seconds=0 tokens_in=0 tokens_out=0 unmetered=1`.

Then set the clean-review status on the commit the round reviewed (`sha` is the `head` of the
last `review-run` record, never `git rev-parse HEAD`; a hosted host writes no such record: there `sha` is the `Reviewed:` line `dl review` printed; the local host refuses any other): `success` when the round
filed no blocker and no major, else `failure`:

```sh
echo '{"pr": <id>, "sha": "<the reviewed sha>", "state": "<success|failure>", "description": "round <n>: <b> blockers, <m> majors"}' \
  | dl host set-status --json
```

On GitHub it is the commit status `dev-loop/review-clean`. On Azure DevOps it is the pull
request status of genre `dev-loop`, name `review-clean`, set on the
PR's last iteration; when that iteration's head is not the reviewed sha (a push came after the
review) it sets nothing and fails: say so, never report it as a pass. Only the round that ends
the loop can post `success`, and only on the head it reviewed. In short: send `success` or `failure` on
every host; the adapter maps it.

On GitHub or Azure DevOps, after each later push that adds only records (the round's, then the
done step's), run the same `set-status` again with the same reviewed sha: the PR's newest push
gets the status.
On the local host (`dl config get host.provider` prints `local`) run it once, before the records
push.

- **Go again** when the round filed any blocker or major: push once, then the next round with
  `REVIEW_BASE` = the commit this round reviewed. There is no round cap.
Stop point: after two rounds in one turn, push the records and return
  `continuing: round <n> on PR <id>` (n the next round); the next turn runs it.
- **Stop** at the first round with no blocker and no major. That is the loop's success.
  Commits that only add records after the clean round need no round: `dl land` accepts them.
- **The same major survives two consecutive rounds:** the fix is not working. Try a different
  approach and report it in the summary with what was tried; the loop does not end while it is open.
- **A finding on a case the approved proposal does not answer is yours to settle**, inside its
  scope: take the answer the proposal, the spec and the rules point to. Record that answer in
  the decision log (`knowledge.decisions`), then dispatch the builder. A decision the loop recorded this way is settled: never hold a thread for a person to
  confirm it. Where code or plan departs from an answer the proposal gives, the fix is to build
  that answer. Pause only when the fix would break written words:
  `dl record <change> pause pr=<id> thread=<n> "cites=<copied words>" "why=<line>"`, leave the
  thread active, report paused.
  A paused thread fixed later is counted in the next round's `fixed`. That round rechecks it;
  never run a round only to move a count.

## When the loop ends

1. Post one summary as a **closed** thread (`dl host thread-post --json`, then
   `dl host thread-status --json` with `"status": "closed"`): rounds run, what was fixed, what
   stays active and why, and whether any round was a subagent. An active summary can block a
   merge policy. Return the same, honestly: "clean after 3 rounds, 5 minors deferred"
   and "paused with a major open" are different outcomes.
2. `dl record <change> lessons-logged count=<total>`, also when it is 0.
3. Record the PR: `dl record <change> metrics pr=<id> change=<label> rounds=<n>
   "per_round=<b/m/k, ...>" total=<b/m/k> lessons=<n>` and `dl record <change> models pr=<id>
   builder=<model id> reviewer=<the model on the review's "Model:" line>`.
4. `dl records lessons --min 3`: a root cause seen in three or more changes is named in the
   summary for the pitfalls file. This PR never edits that file.

## Never

- Never review your own fixes in this session; never skip a round because the last was clean.
- Never approve, vote on or complete a PR here, and never merge one. Approval, if the project
  allows it, is a separate step after a clean round, by the project's own rule.
- Never fix on the integration, release or a protected branch; the PR's source branch only.
- Never touch another PR's threads, or reopen a thread a person settled.
- Never `dl host thread-edit`: only the review edits a thread. You reply (`thread-reply`) and set
  a status; a wrong reply is corrected by another reply.
- Never silence a check that did not run: a skipped check is recorded as not run, never removed
  or caught.

## Return

A short summary, not logs: the PR id and change; one line per round (`round <n>: <b> blockers,
<m> majors, <k> minors; fixed <f>; deferred <c>`); what stays active and why; any root cause
`dl records lessons --min 3` named; and the outcome in one line: clean after N rounds, paused
with a finding open, a precondition that failed, or fast track with N debt items.
