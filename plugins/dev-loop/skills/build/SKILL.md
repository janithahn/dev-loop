---
name: build
description: Build a change's tasks from its tasks.md in parallel waves, one builder agent per task, test first, one commit per task. Use after /dev-loop:plan, when someone says "build the change", "run the tasks" or "build wave 1".
---

# build

Build the tasks of one change. You coordinate; builders write the code.

## Steps

1. **Waves.** `dl tasklist parse <change>` prints the tasks as JSON with a `wave` each. Skip
   tasks already `done`. Dispatch a wave's builders in one message, in the foreground, never `run_in_background`: a
   turn can end before a background builder commits. Run a wave's tasks side by side; start the next wave when every task of
   the one before is `done`. At most N at once: `dl config get build.parallel` if it prints a
   number, else 4. Two tasks that name the same file never share a wave: `dl tasklist parse`
   puts the later one a wave on. A wave's builders share this worktree and its index, so each
   commits only its own files, by path.
2. **Atomic brief, per task.** One builder, one job. The brief holds:
   - the task block from `tasks.md` (title, concern, the interface it must keep);
   - at most 6 files to read, taken from the task's `Files:`;
   - its test: the test file that must fail first and pass at the end;
   - the top 3 lessons of `dl records recall <change> <the task's files>`, pasted in under their
     own `## Lessons` heading, never under Read first. Never paste the whole lessons file.

   `dl config get briefs.sections` prints the headings, one a line. The brief has each as
   `## <heading>` with a body; the plugin refuses or warns on a builder brief without them.
   When `dl config get briefs.change_line` is true, the brief starts with `Change: <change>`.
   With the defaults:

   ```
   ## Goal
   <the task's title and one-line aim>

   ## Read first
   - <file; at most 6 bullets>

   ## Decisions
   none

   ## Lessons
   <the top 3 lessons, one bullet each>

   ## Done when
   - <its test fails first, passes at the end>
   ```
3. **Refs checked.** For every branch or sha the brief names, run
   `git rev-parse --verify <ref>`. A missing ref stops that dispatch with the line
   `build: T<n>: ref <ref> not found`; do not guess a replacement.
4. **Agent type.** `dl config route <change> T<n>` prints the role (for example
   `dev-loop:builder-backend`). Dispatch that agent type and no other.
5. **Status.** `dl tasklist set <change> T<n> doing` on dispatch, `done` when the builder's
   tests pass and its commit exists, `blocked` (with the reason) when it cannot finish.
6. **Test first, one commit.** The builder writes the failing test, then the code, then runs
   the project's verify scope. One commit per task; the subject is exactly `T<n>: <title>`. Never
   `--no-verify`.
7. **Build notes.** A defect found while building (yours or a builder's) goes in the change's
   `design.md`: root cause, how to avoid it, a self-check. No customer data. A defect of the loop
   itself is logged with `dl incident <change> what=... impact=... root_cause=... guard=...`.
   `dl lesson` is only for a fixed review finding; before the change's first clean review run it
   is refused.
8. **Simplify, then verify.** When every task is `done`, dispatch the simplifier to a builder
   (the routed type, as in step 4) with a brief holding the contract headings and the change's
   diff. It makes one commit, subject `<change>: simplify` (nothing to simplify: no commit).
   Record it: `dl record <change> simplifier edits=<what|none>`. Then run `dl verify <change>`.
   `dl pr` opens no PR without that record, so the simplifier's commit is reviewed like any other.
9. **Report.** Per task: status, commit sha, tests run, anything the task text got wrong.

## Stop points

- Stop point: never wait in a shell loop or poll (`sleep`, `tail -f`, a `seq` loop over
  `git log`): builders run in the foreground and return.
- Stop point: a builder that returns with no commit for its task is dispatched once more with its
  own report; a second return with no commit sets the task `blocked` with the reason.
- Stop point: `dl verify` runs at most twice on one head in a turn; a second red on the same head
  goes to a builder or into the report, never a third unchanged run.

## Size, as it is built

After each wave run `dl ci size-cap` (on a stacked part: `--base <the part's base>`). When a part
will not fit `size.cap`:

1. Re-split first. Move whole tasks between the two parts in `tasks.md` (the `## Part` headings,
   two parts at most) until each part fits, and write the new split and the measured sizes in
   `design.md`. This needs no person.
2. A task moved to the later part, whose commit leaves this branch, goes back to `todo`. A part
   with any task not `todo` counts as started, and the done rule then asks for all of it.
3. Ask for a `Size waiver:` line only when no arrangement of whole tasks into two parts fits. A
   waiver names a person: never write one yourself, and say which splits were tried.

## Never

- Never use `--no-verify`.
- Never write source or tests yourself, by Edit or by Bash (`sed -i`, a redirect, a script). A gap
  you see in a builder's work goes back to a builder of that task, which amends the task's commit
  (`git commit --amend --no-edit`); else leave it to the review.
- Never push to the integration or the release branch (`branches.integration`,
  `branches.release`); builders push their own task branch only.
- Never approve or merge a PR.
- Never give a builder more than one task, or a brief naming more than 6 files.
