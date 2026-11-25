# Working rules

Managed by the dev-loop plugin; `dl init` refreshes it. Put project rules in AGENTS.md.

## Core guidelines

1. **Read the whole picture before changing anything.** Read the design, the architecture notes and
   the decision log together. If they disagree, stop and say so; never pick one silently.
2. **Think before coding.** Surface trade-offs, ask when ambiguous, push back when a simpler
   approach exists. For a new area, ask about data scale, target times and environment limits first.
3. **Simplicity first.** The minimum code that solves the problem. No speculative features.
4. **No premature abstraction.** Three similar lines beat an unneeded helper. A reader follows a
   request without jumping through layers.
5. **Surgical changes.** Touch only what the task needs. No drive-by refactors or reformatting.
6. **Reuse before you write.** Search for an existing helper first; extend rather than parallel.
   A new dependency needs a recorded reason.

## Size limits

- A file at most 300 lines (hard stop 400). A function at most 40 lines.
- Cyclomatic complexity at most 10. At most 4 parameters per function. Lines at most 100 characters.
- Over the limit: split by responsibility, never by cutting a function at an arbitrary line.

## Workflow and testing

- **Non-trivial work** goes propose, plan, build, verify. Trivial fixes may go direct.
- **Tests are only ever added.** Never weaken a green test to make it pass. A fix has a test that
  fails without it.
- **Recurring defects first.** Read `REVIEW-PITFALLS.md` before writing code; the spec and design
  address every class that applies.
- **Small PRs.** One concern and at most 200 changed production lines (advice to split from 100).
  Tests, docs and generated code do not count. A larger PR needs a recorded waiver.
- **The review loop.** Every PR is reviewed and fixed before a human reads it. Fix every blocker and
  major, each with a test; defer minors to the change's follow-ups file. The loop ends at the first
  round that files no blocker and no major.
- **Lessons logged.** Every blocker and major fixed leaves a lesson (root cause, how to avoid it, a
  self-check) through `dl lesson`. A root cause seen in three changes joins `REVIEW-PITFALLS.md`.
- **Verify before done.** Run `dl verify`; quote its last lines. A change is done only when its
  tasks are complete, verify is green, and its records are written.
- **Decisions recorded in the repository.** Append each to `DECISIONS.md`, not to chat or memory.
- **Records** are written with `dl record`, never by hand.

## Agents

- **Atomic tasks.** Work is split into atomic tasks, one job per agent.
- **A brief** names at most 6 files, one concern, the interface it keeps and its test.
- **Agent types.** Builders run as `dev-loop:builder-backend` or `dev-loop:builder-frontend`,
  plans as `dev-loop:planner`, reviews as `dev-loop:code-reviewer`.
