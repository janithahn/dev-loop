---
description: Show where each change stands (tasks, verify, last review round, open follow-ups), computed from its files. Use to see what is left before a change is done.
---

# status

Run `dl status` (all changes) or `dl status <change>`. It computes the result from each change's
`tasks.md` and `records.jsonl`; there is no STATUS.md to edit.

Report one line per change. A change whose verify never ran shows `verify: not run`, never green.
A change folder with no `records.jsonl` was last written by another tool: say so and offer to start
its records with `dl record <change> ...`. When all tasks are done the line ends `done` or names
what the done rule still lacks.
