# Paste-in prompt — fix the open Claude review threads on a PR

**Prefer the plugin's review-loop skill.** It is surfaced to every session
automatically, it runs the review as well as the fix, and it repeats until clean. This
file is the manual fallback: a one-shot fix with
no re-review wanted, or driving it by hand.

Companion to `REVIEW-PROMPT.md`. That one reviews a PR; this one FIXES what a review
found. Copy everything below the line, replace `<PR>`, paste into a Claude Code session
opened in the repo root.

Why a prompt is needed at all: a Claude session has no ambient access to the git host.
It reads files, not pull-request threads. "Fix the review comments" on its own gets you
a session guessing which comments, or asking. The prompt is what points it at them.

---

Fix the open Claude review threads on PR `<PR>` (host, org, project and repo from the project's config).

**Read the threads first.** The credential comes from the environment
variable the config's `host.token_env` names; never print it. List the threads with
`dl host threads <PR>`.
Only `status: active` threads need work — `fixed`/`closed` ones are already done, and a
thread whose first comment has no `crid:` footer is a human's comment, not a review
finding. Each finding names the file, the line, the concrete failure scenario, and the
fix. Read all of them before changing anything: findings on one PR are often two halves
of one defect, and fixing them separately produces a worse shape than fixing them
together.

**Read the change's own paperwork before writing code.** Find the change folder under
the config's `paths.changes` and read `proposal.md` and `spec.md`. If they carry
requirements, acceptance criteria or guardrails — especially any section marked
"verbatim" from a review thread or from the owner — those are the standard the
fix is judged against, not the diff. **Anything in a Guardrails section must not be
undone**: a review finding can usually be silenced in more than one way, and the cheap
way is often the one a guardrail forbids. Satisfying the compiler is not satisfying the
finding.

**Run `dl records pitfalls <the files the findings name>`** and keep its
lines in view while you fix: they are the classes most often logged against those packages, and
the same block sits in the reviewer's system prompt as "Pitfalls for this PR's files".

**Then fix, in this order:**

1. The blocker(s), then majors, then minors. A blocker that changes a contract may
   dissolve a minor beneath it — re-read the minors after.
2. Add the test each finding asks for. Most findings name one explicitly, and they name
   it because the defect shipped past a green suite. A fix with no test is half a fix.
3. Tests are **additive only** — never weaken, skip or delete an existing test to go
   green. If an existing test genuinely encodes the old, wrong behaviour, say so
   explicitly in the commit message and on the thread; do not quietly invert its
   assertions.
4. Run what the repo runs: `dl verify` (the steps the config's `verify` section names for the
   files you changed).

**Verify against the finding, not against the build.** For each thread, be able to state
what now happens in the exact failure scenario the finding described. A green build is
evidence the code compiles, not evidence the defect is gone. Where a finding was proved
with a concrete input, prove the fix with the same input.

**Then report on each thread.** Reply on the thread (`dl host thread-reply <PR> <thread>`) saying what changed and
which test pins it, then set it to `fixed` (`dl host thread-status`). Reply only on threads you
actually fixed; leave the rest active. If a finding is wrong, say why on the thread and
leave it open for the reviewer — do not resolve a thread by disagreeing with it silently.

**Do not vote on or complete the pull request.** Push the fixes; the owner
reviews and merges.

**Traps that have bitten fixes**

- **Silencing a failure is never the fix.** Record the failure (or the check as not run); never
  drop the check from the plan or swallow the exception.
- **A silent fail-safe is still a defect.** "It doesn't crash" is not the fix: the condition is
  either impossible or stated in a record.
- Write poster/helper scripts with the Write tool, not a bash heredoc: heredocs mangle
  `\n` and f-strings and produce `SyntaxError: unterminated f-string literal`.
- If you change anything in the working tree to test a hypothesis, restore it and confirm
  `git status` is clean. `cd` persists between Bash calls.
