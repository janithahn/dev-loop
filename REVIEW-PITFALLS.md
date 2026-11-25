# Review pitfalls

The defects reviews keep finding. Read this before writing code; a spec and design address every
class that applies. When a root cause is seen in three changes, add a row here. Keep this file
short: at most 15 classes.

Each row is a class: the rule, and the check that would fail if it were broken. `dl records
current` reads this table. The guard cell names the test or hook that guards the class as a
backticked path that exists, or says `review only: <reason>` until one does.

| # | Class | Rule | Check | Automated guard |
|---|---|---|---|---|
| 1 | Docs out of step with code | A change to behaviour updates the doc, comment and example that describe it, in the same PR. | A test or script that reads the doc against the code. | review only: no guard yet |
| 2 | Not run shown as passed | A step that did not run is reported as not run, never green. | A test where the step is skipped and the report must say so. | review only: no guard yet |
| 3 | Input not range-checked | Every value from outside a trust boundary is validated for type, range and length before use. | A test with an out-of-range value. | review only: no guard yet |
| 4 | A test that passes with the bug present | Every fix has a test that fails without it; run it against the broken code once. | Revert the fix and watch the test fail. | review only: no guard yet |
| 5 | Unbounded work | Loops, queries, retries, queues and responses have a limit and a timeout. | A test with an input over the limit. | review only: no guard yet |
| 6 | Stale data read as current | Cached or copied data carries its date and source, and is refused or refreshed when too old. | A test with an old value. | review only: no guard yet |
| 7 | Races and re-runs | A job run twice, or two at once, gives the same result as once. | A test that runs it twice and concurrently. | review only: no guard yet |
| 8 | Results that cannot be reproduced | Inputs, versions and parameters are pinned in the record, so a result can be re-derived later. | A test that re-derives from the record alone. | review only: no guard yet |
| 9 | A finding with no source | Every finding names where it came from: a file and line, a record, or a data row. | A test that a finding without a source is refused. | review only: no guard yet |
| 10 | A failure swallowed | No bare catch that hides an error; a failure is logged, surfaced and recorded as a failure. | A test that forces the failure and expects it to show. | review only: no guard yet |
| 11 | A write with no audit | Every change to stored state leaves a record of who, what and when. | A test that the write adds an audit row. | review only: no guard yet |
| 12 | Size limits | File, function, parameter, complexity and line limits in CLAUDE.md hold. Split by responsibility, never at an arbitrary line. | The linter. | review only: the linter, not a test |
| 13 | Unneeded code | Code with no caller, a helper used once, a config for a value that never changes, an abstraction with one user. Delete it, or do not write it. | The reviewer's unneeded-code pass. | review only: the reviewer's unneeded-code pass |
