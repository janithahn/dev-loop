---
name: propose
description: Draft a change proposal. Use whenever the user wants to add, build or change something. Makes the change folder and a worktree on its own branch, then fills in proposal.md.
---

# /dev-loop:propose

If the idea or the problem it solves is missing, ask once for both, then go on.

1. Slugify the idea into `<slug>` (lowercase, digits, hyphens).
2. Run `dl propose <slug> --title "<title>"`. It adds a worktree on `branches.change_prefix<slug>`
   from `origin/<branches.integration>` and writes `proposal.md` and `status.md` from the templates
   into `paths.changes/<slug>/` there. It prints the worktree path; work in that path from now on.
3. Fill `proposal.md`: problem, proposed solution, scope (in and out), impact with a production
   line estimate against `size.cap`, open questions, and each class in the pitfalls file
   (`knowledge.pitfalls`) that applies, with where the spec will handle it.
4. If the estimate is over `size.cap`, say so now: the row splits into at most two parts, or the
   proposal records `Size waiver: <who> <yyyy-mm-dd> - <reason>`.
5. Show the proposal and end the turn. An
   instruction to deliver a whole brief is not approval. Approval arrives as a new user message
   `Approved: <slug>`, which a hook turns into the change's approval record. Never set the
   Status to Approved yourself, and never write an approval record.

Do not start `/dev-loop:plan` until the user approves the proposal. When `Approved: <slug>`
arrives, run `/dev-loop:plan <slug>`, report its result and end the turn. The build starts only on
its own request (`/dev-loop:build <slug>`), never in the approve turn.
