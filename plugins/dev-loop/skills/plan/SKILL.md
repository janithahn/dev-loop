---
name: plan
description: Turn an approved proposal into spec.md, design.md and tasks.md of atomic tasks. Use after /dev-loop:propose is approved, before building.
context: fork
agent: dev-loop:planner
background: false
---

# /dev-loop:plan `<change>`

This skill runs in a forked `dev-loop:planner` subagent; the caller waits for it. It sees no
conversation, only `$ARGUMENTS`: the change's name, then any notes for the plan. With no change
named, stop and return that the input is missing.

Work in the change's worktree (`git worktree list` names it; its branch ends in the change's name).
The change folder is `<dl config get paths.changes>/<change>/`.

1. Read `proposal.md`. If it is missing or not approved, stop and return that. Approved means
   an `approval` record in the change's `records.jsonl`; `dl tasklist set … doing` and `dl pr`
   refuse without one (`not approved: <slug>`).
2. Read the files `dl config get knowledge.read_first` names, the project's `CLAUDE.md` or
   `AGENTS.md`, and the pitfalls file (`dl config get knowledge.pitfalls`).
3. Run `dl records recall <change> <the files you expect to touch>`. It lists the research notes
   and the three past lessons most like this change, by id. Read what bears on the change.
   The build refuses to start until design.md names every lesson the plan-time recall showed.
4. Read the code the change touches and measure it (counts, files, callers) before you design.
5. Write, in the style of the change folders beside it:
   - `spec.md`: numbered requirements, then acceptance criteria. Write acceptance criteria as a
     bullet list under `## Acceptance criteria`, one `- ` line each, naming a command in backticks
     and its expected output. A table may follow, but `dl review` ticks only bullets;
   - `design.md`: the approach, each recalled lesson by id with how it is addressed, each pitfall
     class with where it is handled;
   - `tasks.md`: blocks in this form, in waves.

```
### T<n>: <title>
- Status: todo
- Kind: backend | frontend         (optional)
- Depends: T1, T2                   (optional)
- Files: path, path
<the brief>
```

## Atomic tasks

Each task is one concern. Its brief names at most 6 files to read, the files it writes, the test,
what it must not break, and a production line estimate against `size.cap` (the estimate counts what the cap counts: every
added or changed line of a production file, docstrings, imports and export lists included;
advise a split from
`size.advise`). A task over the cap is split, at most into two parts, or the proposal records a
size waiver. A split row's tasks sit under `## Part a` and
`## Part b` headings in `tasks.md`; a part is done on its own. A builder gets only its brief, so the brief carries everything it needs.

Check the plan: `dl tasklist parse <change>` must print every task, with no dependency cycle.

## Spec questioning, without a dialogue

A forked planner cannot ask anyone, so it questions the spec itself, section by section:

| Section | Technique |
|---|---|
| Overview | 5 Whys: from the stated solution down to the real need |
| Requirements | Jobs-to-be-done: "When ..., I want ..., so I can ..."; a requirement that will not fit is a design choice, so it moves to design |
| Non-functional | Inversion: what would make this useless or harmful |
| Acceptance criteria | Worked example for each; no example, no criterion |
| Edge cases | Pre-mortem: it broke a week after release; what broke |
| Any addition | MoSCoW: could and won't leave the requirements |

A vague word (fast, easy, secure) gets a number; a criterion no command can observe is rewritten
or dropped. Where a decision belongs to a person, never pick silently: take the default the
proposal and the project's rules point to, and write it under `## Open questions` in `spec.md` as
`<question> - default taken: <default>`. If the references disagree, stop and return that.
An answer the approved proposal already gives, its proposed answer to an open question included,
is decided: the approval covers it. Plan that answer. If you think it is wrong, write no plan:
stop and return `proposal change needed: <the answer, and why>`. Never record a different answer
as a decision of your own.

Once `dl tasklist parse` passes, commit the
change folder's `spec.md design.md tasks.md status.md` by path (`git commit -m "<change>: plan" --
<those paths>`), nothing else, and stop. Never write product code in this step.

## Return

A short result, not logs: the change; the three files written; the task count by wave; the
`dl tasklist parse` outcome; each open question with the default taken; or the reason it stopped.
