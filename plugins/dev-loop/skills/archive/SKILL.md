---
description: Archive a finished change - move its folder to the archive by git mv once its branch is merged.
---

# archive

Run `dl archive <slug>` from the project. It moves `<paths.changes>/<slug>` to
`<paths.archive>/<yyyy-mm-dd>-<slug>` with `git mv`, so history follows.

1. Check the change is done (`/dev-loop:status`): tasks complete, verify green, no open round.
2. Run `dl archive <slug>`. It refuses when the change's branch (`branches.change_prefix` + slug)
   still exists and is not merged into `branches.integration`; merge first, then run it again.
3. It writes one `archive` record (`Archived: <from> → <to>`) before the move, so the line travels
   with the folder.

After `dl land`, run `dl archive <slug>` from the change's worktree. It commits the move and pushes it to the integration branch itself. Never commit, push, PR or land the move by hand.

Never move a change by hand and never archive one whose branch is unmerged.
