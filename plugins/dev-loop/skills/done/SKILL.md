---
description: Close a change. Check the done rule and name what is missing; the simplifier already ran in the build, before the PR. Use when the last task is complete.
---

# done

1. The simplifier ran in the build, before the PR (`dl pr` opens none without its record).
   Never dispatch it after a review round: its commit would reach the PR unreviewed. Only when
   `dl status <change>` names the simplifier line missing: dispatch it to a builder (the routed
   type, as in the build skill) with a brief that has the contract headings and the change's
   diff; one commit, subject `<slug>: simplify`, then `dl verify <change>`; record it with
   `dl record <change> simplifier edits=<what|none>`, and run one more review round on it before
   done. The main thread never edits code.
2. Run `dl status <change>` (`dl record done <change> part=<a>` judges one part alone). The done rule is what the done guard checks:
   - every task of each started part `done` in tasks.md (a part with any task not `todo` is
  started; the status line then ends `done (part a)`);
   - a green verify record and verify-report.md (`dl verify <change>` writes it; never `dl record ... verify` by hand);
   - the last review round of each part's PR clean (0 blocker, 0 major, 0 active; `fixed` only for
     a thread the round before left active);
   - a reviewed PR for each started part;
   - `Lessons logged: <n>` covering every fix, each a lesson (`dl lesson`);
   - a skills-used line (`dl record <change> skills skills=<names>`);
   - a metrics row for each reviewed PR.
3. Anything missing: do it, never write a record for a step that did not run. Done only when the
   status line ends `done`. Then offer `dl archive <change>`.
4. After `dl land`, run `dl archive <slug>` from the change's worktree. It commits the move and pushes it to the integration branch itself. Never commit, push, PR or land the move by hand.
