---
description: Verify a change before it is called done. The agent checks the spec's acceptance criteria; `dl verify` runs the project's configured commands and records the result. Reports pass, fail or not run per step.
context: fork
agent: general-purpose
background: false
---

# verify

This skill runs in a forked subagent; the caller waits for it. It sees no conversation, only
`$ARGUMENTS`: `<change> [--part <name>] [--skip <step>]`. With no change named, stop and return
that the input is missing.

Verify the change from the project root. Two parts, both reported.

1. **Acceptance criteria (the agent).** Read `spec.md` in the change's folder
   (`<dl config get paths.changes>/<change>/`). For each criterion, find the test, file or output
   that shows it holds, and write pass or fail with that evidence. A criterion with no evidence
   is **not run**, never passed.
2. **Commands (`dl verify`).** Run `dl verify <change> [--part <name>] [--skip <step>]` with the
   arguments given. It runs each `verify.steps` entry through `verify.runner`
   (none for a `verify.host_steps` name) in the project root,
   appends a section to the change's `verify-report.md` (a section per part), and writes the
   `verify` record (`result=green|red`, `head=<sha>`).
   - A step with no command, or named by `--skip`, is written **not run**.
   - The result is green only when every step passed. A failed, not-run or missing step is red.
   - With `verify.covers` set (step name: the globs it checks), a `coverage` row says whether
     every changed production path (`size.production`, less `size.exclude` and `size.generated`)
     is matched by a step's globs. A change no step covers is **red**, never green: the row
     reads `not run: no step covers <path>`. On a part stacked by `dl pr --base`, the row judges
     the change from the remembered parent, not the integration branch.

Then add the acceptance-criteria results under that section's "Acceptance criteria" heading.

Rules:
- Tests are additive: a suite with fewer tests than before is a failure to investigate.
- A flaky test is fixed at its cause, never retried or skipped.
- Do not commit. Stop on a red step.
- Stop point: run `dl verify` at most twice on one head in a turn. A second red on an unchanged
  head is returned red with the failing step, never run a third time (a flaky step is fixed at
  its cause).
- A step that could not run (a tool missing) is reported as **not run**, never as passed.

## Return

A short result, not logs: the per-step table (step, pass, fail or not run), the acceptance
criteria counted as pass, fail and not run with each failing one named, the overall result
(green or red) and the head sha. For a red step, its last few lines of output only.
