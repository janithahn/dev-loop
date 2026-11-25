---
description: Raise the pull request for a verified change. `dl pr` rebases, holds the diff to the size cap, pushes the change's own branch and opens the PR into the integration branch or a named target. Never into the release branch.
---

# pr

Raise the PR for the change `<change>` from the project root, on the change's own branch.

1. **Before.** The change is verified: its `verify-report.md` has a green section for this part
   (`/dev-loop:verify`). Everything is committed; the working tree is clean. The change has its
   simplifier record (`dl record <change> simplifier edits=<what|none>`, written by the build
   after the simplifier ran): without one after the last review round, `dl pr` opens no PR.
2. **Run** `dl pr <change> [--target <branch>] [--base <branch>]`. It:
   - refuses a target equal to `branches.release` (a release is opened by a person), and a
     current branch that is the integration, release or a `branches.protected` branch;
   - fetches and rebases on `origin/<target>` (the target defaults to `branches.integration`;
     name it for a stacked part whose base is still open);
   - runs the size cap on the diff from the merge base with `origin/<target>`; over the cap it
     stops before the push (split the change, or record a `Size waiver:` line in its `proposal.md`);
   - with `--base <branch>`, the change branch this part sits on (stacked by merge on an open PR,
     its own PR into the target): fetches it and judges the size cap, the stray-commit check and
     the pre-push hook from `origin/<branch>` and rebases on it (a rebase on the target would
     rewrite the parent's commits); the PR stays on the target. This branch, the release or a
     protected branch is refused as a base. The base
     is remembered (`git config branch.<current>.dl-base`), so later `dl pr` runs, the review
     loop's pushes, the hook and `dl ci` judge from it too; once the remote no longer has it
     (merged and deleted) it is forgotten, said in one line, and the target is used;
   - pushes with `git push --force-with-lease origin <branch>`, never a plain force; the push
     names the same base to the pre-push hook (`PREPUSH_BASE`), so the hook judges this part alone;
   - reports the open PR from the branch if there is one, else opens one: title from
     `proposal.md`'s first heading, body its text, ending with `host.pr_footer` when set; a
     stacked part's title carries its part, and its body opens naming the parent's PR;
   - prints the PR id last.
3. **After.** Report the PR id. When `review.auto_trigger` is true, start `/dev-loop:review` on it.

Rules:
- Never push with a token in the URL or print a credential; the host adapter reads it.
- A rebase conflict stops the command: resolve it, then run `dl pr` again.
- Never approve or merge here; that is a separate step after the review loop.
- Stop point: when `dl pr` fails, fix the cause it names once and run it again; a second failure
  records `dl incident <change> what=... impact=... root_cause=... guard=...` and returns
  `blocked` with its last line.
