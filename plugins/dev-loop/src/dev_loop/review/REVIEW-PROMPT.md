# Paste-in prompt — Claude PR review (any PR)

Copy everything below the line, replace `<PR>`, paste into a fresh Claude Code session
opened in the project's repo root.

---

Review PR `<PR>` on the project's git host (host, org, project and repo from the project's config) using this repo's own review pipeline, and post the findings to the PR.

**Read these first, and follow them as the standard — they are the review's definition of
done, not background reading:**
- `standards.base.txt` (this plugin's `review` folder): severity ladder, error-handling checklist
  and what counts as a finding, plus the project's own standards file when the config's
  `review.standards` names one
- `pr-review.sh`: how findings are anchored, deduped and posted
- the project's law and guides: the files the config's `knowledge.read_first` and
  `review.governance` name, and the root `CLAUDE.md` or `AGENTS.md`
- the project's recurring-defect file (config `knowledge.pitfalls`); check the diff against every class
- the "Pitfalls for this PR's files" block `pr-review.sh` puts in the system prompt
  (`dl records pitfalls <changed files>`): the classes most often logged
  against these packages; a block that says "pitfalls unavailable" is stated, never skipped

**Setup**
- The host credential comes from the environment variable the config's `host.token_env` names;
  never print it.
- Get the PR's source branch from the host (`dl host pr-get <PR>`, its source ref), fetch it,
  and pin it to a local branch named `pr<PR>`. Review the
  branch head, not `refs/pull/<PR>/merge`.
- Diff against `origin/<integration branch>` — specifically compute the merge base against `origin/<integration branch>`,
  not against whatever `FETCH_HEAD` happens to be, or the added/removed test counts come
  out wrong.

**How to review**

Two passes over the full diff. The first pass is for what the change does; the second is
for what it does when something else is true — a concurrent worker, an input
arriving out of order, a second replica, a missing input.

Verify claims rather than accepting them. When the code or a comment asserts something
("this record is append-only", "this value is the one that was pinned", "these two
values are the same string"), go read the other end and confirm it. Most real findings come
from two pieces of code that each look correct alone and disagree about a value's shape,
key space, lifecycle status, or casing. Trace the value end to end.

Give particular weight to failures that fail SAFE, because nobody will notice them: a
filter that silently matches no rows, a guard that never fires, a cache that never hits.
The code keeps working, the feature quietly stops, and no alert exists. These are usually
the most valuable findings in a PR and are routinely more severe than they first look.

Check the tests as evidence, not as decoration: does the new test actually distinguish the
implemented rule from the alternative rule, or would it pass under both? Does it exercise
the code that broke, or does it hand-build the input that code produces? Verify tests are
additive — 0 removed — and report the counts.

State the blast radius honestly. If something is limited today by a config value rather
than by design (a feature switched off in a dev config, a check not yet enabled), say so, and say
that it is an accident of configuration rather than a guarantee.

**How to post**

Post each finding as its own PR thread anchored to the file and line, severity-prefixed
(🔴 blocker / 🟠 major / 🟡 minor), with a `crid:<hash>` footer computed exactly as
`crid_of()` in `pr-review.sh` does it: `sha1("<path>|<whitespace-normalized source line at
that line number>")[:12]`, falling back to `sha1("<path>|<rule>")` when there is no line.
Then post one summary thread (`status: 4`, closed) with the verdict —
APPROVE / APPROVE-with-notes / CHANGES_REQUESTED — a short account of what you verified
rather than assumed, and the test delta.

Findings should be written to be acted on: state the defect, give the concrete failure
scenario (inputs/state → wrong behaviour), and name the fix. Quote the real code. If the
fix is one line because some field already exists, say which field.

**Traps that have actually bitten**

- Never put a literal `crid:` string inside a finding's body text. The pipeline takes the
  FIRST match in a comment, so a thread would inherit another thread's identity. Refer to
  other threads as `` thread `abc123` `` instead.
- Write poster scripts with the Write tool, not a bash heredoc. Heredocs mangle `\n` and
  f-strings here and produce `SyntaxError: unterminated f-string literal`.
- If you modify anything in the working tree to test a hypothesis (swapping a config,
  running a linter against the PR's rules), restore it and confirm `git status` is clean.
  `cd` persists between Bash calls — account for it.
- When testing a tool's config-driven behaviour, make sure you are running the PR's config
  and not `main`'s, or the result is meaningless.

**Re-reviews** (when asked to "review again with the fixes made")

Re-fetch the branch and diff only what changed since the commit you last reviewed. For each
existing thread you raised, verify the fix against the code — do not take the commit
message's word for it — then reply on the thread with the specific evidence (what the fix
now does, why that shape holds, which test pins it) and PATCH it to `status: "fixed"`.
Resolve only threads you actually verified.

Then review the fix itself as new code. Fixes introduce findings at a high rate, and a fix
that is correct in isolation can still be wrong where it is wired in — check the caller,
not just the changed function. Post any new finding as its own thread and end with a
round-N summary.

If you were wrong in an earlier round, say so plainly on the thread and move on.
