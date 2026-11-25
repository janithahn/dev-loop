# dev-loop

A Claude Code plugin that runs a software change through one loop: **spec → plan → build → review →
verify**, done by Claude Code agents and kept honest by records, lessons, a size cap and gates.

- **Records.** Every change has a folder (`proposal.md`, `spec.md`, `design.md`, `tasks.md`,
  `status.md`, `follow-ups.md`, `learnings.md`) and a `records.jsonl` written by one writer. A
  hand-written record is refused.
- **Lessons.** Every blocker and major a review round fixes leaves a lesson (root cause, how to
  avoid it, a self-check). Lessons seen across changes become the project's pitfalls list, which
  planners and reviewers read before they work. A record or lesson field that quotes more than
  40 characters is refused, so document text cannot slip in.
- **Size cap.** A pull request carries one concern and at most `size.cap` production lines.
- **Gates.** Hooks guard protected branches, nudge the review loop when a PR is raised, and stop a
  session from calling a change done while its records are missing.
- **Agents.** Planner, builders, reviewer and researcher agent types (`dev-loop:<name>`), each with
  its model and context cap set by the project's config.

Agents recommend; a person approves and merges. The loop never approves or merges a PR.

## Install (Azure DevOps)

Needs Claude Code 2.1.325 or later, `git` and Python 3.12 or later. In the project's repository:

```
/plugin marketplace add janithahn/dev-loop
/plugin install dev-loop@dev-loop
/dev-loop:init
```

The first session start builds the plugin's own Python environment under `${CLAUDE_PLUGIN_DATA}`.
`/dev-loop:init` (`dl init`) writes `dev-loop.yaml`, the knowledge files and the git hooks, and
makes the plugin's working rules load in every session: it writes `.dev-loop/RULES.md` (owned by
the plugin, refreshed by the next `dl init`) and points both instruction files at it.
`AGENTS.md` is canonical when present: Codex, Cursor and the other harnesses read it, so the
project law lives there, and it gains one plain line pointing at `.dev-loop/RULES.md`.
`CLAUDE.md` gains the line `@.dev-loop/RULES.md`; when missing it is created as `@AGENTS.md`
plus that line, and a missing `AGENTS.md` is created from the template with the project-law
placeholders. An existing file only gains the line it lacks and keeps every other byte. Running
it again changes nothing. Then set `host.org`, `host.project` and `host.repo`, and run `dl doctor`:
it prints one table of what the loop needs and exits 1 while any row is red.

The host credential comes from `git credential fill` for `https://dev.azure.com/<org>`, else from
the variable `host.token_env` names. It is never printed and never put in a URL.

## Upgrade

Update the marketplace, then the plugin, then refresh the project's files:

```
/plugin marketplace update dev-loop
/plugin update dev-loop@dev-loop
/dev-loop:init
```

`dl init` again changes only what the new version added, and `dl config migrate` rewrites an
older `dev-loop.yaml` to the current schema.

## Skills

| Skill | What it does |
|---|---|
| `/dev-loop:init` | runs `dl init`: the project's files, safe to run again |
| `/dev-loop:propose` | drafts a change: its folder, a worktree on its own branch, `proposal.md` |
| `/dev-loop:plan` | turns an approved proposal into `spec.md`, `design.md` and `tasks.md` |
| `/dev-loop:build` | builds the tasks in waves, one builder agent per task, test first |
| `/dev-loop:verify` | checks the acceptance criteria and runs the configured verify steps |
| `/dev-loop:pr` | rebases, holds the diff to the size cap, pushes and opens the PR |
| `/dev-loop:review` | the review loop: rounds until one files no blocker and no major |
| `/dev-loop:land` | gates, approves and merges PRs the loop marked done; never into the release branch |
| `/dev-loop:done` | runs the simplifier and checks the done rule, naming what is missing |
| `/dev-loop:status` | where each change stands, computed from its files |
| `/dev-loop:archive` | moves a merged change to the archive |
| `/dev-loop:research` | a background agent reads primary sources and writes a findings file |

Four skills run forked (`context: fork` in their frontmatter): `plan` (`dev-loop:planner`), `research` (`dev-loop:researcher`),
`verify` and `land` (a general-purpose agent). They read and write a lot, so each runs in its own
subagent and only its short result reaches the conversation; `plan` and `verify` make the caller
wait, the other two run in the background. `review` runs in the main context, so it can start the
builders that fix its findings. A forked skill sees no conversation history and
cannot ask a question, so it takes its inputs from its arguments and records any default it took.
`propose` and `init` ask the user, `build` dispatches the builders itself, and `status`, `done`,
`archive` and `pr` are short, so they stay inline.

## The `dl` command

| Command | What it does |
|---|---|
| `dl init` | the project's files (config, knowledge, git hooks, rules import); idempotent |
| `dl doctor` | what the loop needs, one row each; exit 1 when any is red. The **agent models** row compares each `roles.<role>.model` with the agent that runs: red for a project agent (`.claude/agents/`) that differs, a warning for a plugin agent, which a project cannot edit |
| `dl config get <key>` · `set <key> <value>` · `route <change> <task>` · `migrate` | read and write `dev-loop.yaml` |
| `dl propose <slug> [--title T]` | a change folder and a worktree on its own branch |
| `dl tasklist parse <change>` · `set <change> <id> <status>` | read and update `tasks.md` |
| `dl verify <change> [--part NAME] [--skip STEP]` | run the verify steps and record the result |
| `dl pr <change> [--target BRANCH]` | rebase, size cap, push, open the PR |
| `dl land <pr>… [--keep-source]` | gate, approve and merge PRs into the integration branch, one lander per machine (ado; github: "not run") |
| `dl review` · `dl review context` | one review round; the reviewer's context from the config |
| `dl host <verb> --json` | the host adapter for shell callers (PRs, threads); body on stdin |
| `dl record <change> <kind> key=value …` | write one record through the one writer |
| `dl record legacy <change> reason=… <row>…` · `dl record render <change> reason=…` | grandfather named old-format rows of a change listed in `<paths.changes>/legacy-changes.txt`; write missing Markdown lines |
| `dl lesson <change> …` | log one review lesson |
| `dl incident <change> what=… impact=… root_cause=… guard=…` | log one incident of the loop itself |
| `dl records <view>` | records combined across changes: follow-ups, metrics, lessons, trend, pitfalls, rules, … |
| `dl status [change]` | each change's progress |
| `dl archive <slug>` | move a merged change to `paths.archive` by `git mv` |
| `dl clean [--apply] [--json] [--when-full]` | remove the loop's leftovers safely (dry run by default); see Cleanup below |
| `dl ci <step>` | a CI step: size-cap, records-current, rules-current, unparsed, the self-tests, pin-check, fix-proves, secret-scan, licences (each direct dependency's licence vs `licences.allow`), release-gate (each open debt item and its severity; red on `release_gate.block_on`) |
| `dl stack-rebase [<old base>]` | rebase a stacked part onto its merged base |
| `dl githook pre-commit\|pre-push` | the git hooks' bodies |
| `dl tasks …` · `dl worker …` | workers (below) |
| `dl dashboard` | a local page of sessions, agents, spend and context; it serves until stopped, so it blocks |
| `dl fleet-export [--delivery]` | push the fleet's load and delivery figures as OTLP gauges |

## Configuration

The plugin's agents pin full model ids (`claude-opus-5-5`, `claude-sonnet-5-5`). On a host where a
first-party id may not resolve, set `CLAUDE_CODE_SUBAGENT_MODEL=<id the host serves>` and
`CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` in the environment: it overrides every agent's `model:`.

`dev-loop.yaml` at the repository root holds only the project's differences from the plugin's
defaults: mappings merge key by key, a list replaces the default whole, an unknown key is refused.

| Key | What it sets |
|---|---|
| `version` | the config schema version (none: 1); a newer file is refused, an older one is read migrated until `dl config migrate` rewrites it |
| `paths` | `changes` (where change folders live), `archive` (where merged ones move); `frozen` files no change edits |
| `branches` | `integration` (where changes merge), `release`, `protected`, `change_prefix`, `remote` (the git remote every command uses; default `origin`) |
| `host` | `provider` (`ado` or `github`; github runs through a logged-in `gh`, owner `org`), `org`, `project`, `repo`, `token_env`, `pr_footer` |
| `knowledge` | files agents read first: `read_first`, `pitfalls`, `decisions`, `guides`, `packages`, `session_checklist` |
| `rules` | the area guides' Do-not-repeat block: `generator`, a command that writes it in place of the built-in writer; `check`, its staleness check when a generator is set (both default unset) |
| `size` | `cap` and `advise` (production lines per PR); `production`, `exclude`, `generated` globs |
| `review` | `governance` globs, `extra` standards, `standards` overlay, `examples` (a project file of worked examples), `auto_trigger`, `fast_track` (true: one review round), `fallback` (`never` | `subagent`, default `never`: what a failed reviewer precondition does), `fast_track_fix` (the finding classes that round still fixes), `runner` (default `claude`), `model`, `runner_command` (another CLI: `{model}`, `{prompt}`, `{system}`; prints the verdict JSON) |
| `verify` | `runner`, `steps`, `affected`, `prepush`, `host_steps` (step names run with no runner prefix; each must be in `steps`), `covers` (step name: the globs it checks, space-separated; each must be in `steps`; a changed `size.production` path no step covers makes the run red; default `{}`: not checked), `step_timeout_minutes` (default 20: a step running longer is killed with its children and failed, never passed) |
| `fix_proves` | `src` and `tests` globs and the test `command` of `dl ci fix-proves`: a fix's changed test must fail on the base's source; empty: "not run" |
| `briefs` | the `sections` every Agent brief must have, `max_read_first` files, `enforce` warn or block, `agent_types` the subagent types it applies to (empty: all), `change_line` true to require a `Change: <name>` line (default false) |
| `release_gate` | `block_on`: the debt severities (`blocker`, `major`, `minor`, `unknown`: a debt written without one) that make `dl ci release-gate` red; default `[blocker]` |
| `licences` | `allow`: the SPDX ids a direct dependency may carry; `dl ci licences` is red on any other or unreadable one |
| `gates` | `sides` of the pipeline by glob; `on_edit`, the guard run after an edit; `policy`, the Build policy `dl land` gates on (name prefix; null: the only one); `ci_only`, regular expressions of commands only CI runs: a Bash call matching one is denied unless `CI` or `TF_BUILD` is set |
| `metrics` | `deploy_pipeline` the delivery figures count |
| `cleanup` | `dl clean`: `auto`, `keep_days`, `max_log_mb`, `threshold_pct`, and the opt-in `docker`/`docker_label` and `caches`/`cache_globs` (see Cleanup) |
| `records` | `guard_moves`: a map from a lesson's old `Guard` path or glob to its new home — a project path, or `plugin:<test or hook name>` — so a guard moved onto the plugin counts as satisfied, not stale |
| `tasks` | the work-item `tag` workers take; the task `cache` path |
| `build` | `parallel`: how many tasks a build runs at once (1 or more) |
| `managers` | names allowed to give tasks and start workers |
| `workers` | per worker: `checkout`, `worktrees`, `never` globs; optional `may_run` (routing kinds it takes, default all; only the worker's brief (a prompt, not code) tells it to answer other kinds on the work item), `reach` and `accounts` (display only) |
| `routing` | which builder agent type a task goes to, by `Kind:` or by path |
| `roles` | per role: its agent types, model and context cap |
| `caps` | `enabled`: the context cap's hand-off on or off |
| `handoff` | `threshold`: the share of the context cap at which the hand-off opens |
| `warnings` | `running_hours`: when the dashboard marks a long run |

## Switching a project onto the plugin

A project that ran its own copy of the loop and then switches to the plugin deletes its own tests
and hooks, but its historical lessons still name those files in their `Guard` field, so
`dl ci records-current` would report every one as stale. History is never rewritten; instead map
each old guard to its new home in `records.guard_moves` — an old path or glob to a project path
that still exists, or to `plugin:<test or hook name>` when the guard now lives inside the plugin. A
mapped guard whose target exists counts as satisfied; `dl records current --verbose` lists each as
`moved -> <value>`. An unmapped missing path, or a mapped one whose target is absent, stays stale.

## Worked review examples

The reviewer's base prompt (`standards.base.txt`) carries four worked examples — a blocker, a
major, a minor and a borderline case that is deliberately **not** filed — each with the reasoning
behind its verdict, so findings come out concrete and a false positive is recognised rather than
filed. A project adds its own with `review.examples`, a path to a file appended after the base
examples (a named file that is missing is said in the prompt, never a silent skip).
`dl records examples [--limit N]` drafts candidates from the project's own lessons (fixed blockers
and majors) and deferred minors for a person to edit into that file; it never writes the file and
never invents a case.

## Settings pane

`/dev-loop-settings` opens a pane that shows and edits the main keys of `dev-loop.yaml` (the same
writes as `dl config set`). **Telemetry is off by default**, and the pane's "Send telemetry" toggle
is the only way to turn it on; it applies from the next session. The exporter's auth header
(`OTEL_EXPORTER_OTLP_HEADERS`) is set by you in your own Claude Code user settings; the pane only
says whether it is present and never shows it.

## Workers

A worker is a Claude Code session on another machine that takes tasks from the host's work items
(on GitHub: issues labelled `tasks.tag`; a `doing` label marks one taken, closing it marks it
done). `dl tasks` gives, reads and closes tasks (`new`, `show`, `start`, `comment`, `done`, `watch`,
`log`); only a name in `managers` can give one, and only a manager's comments count as
instructions. `dl worker` runs one machine worker from its `workers.<name>` entry.

## Cleanup

The loop cleans up after itself so a worker server or a laptop does not fill. `dl clean` is a **dry
run** unless `--apply` is given: it lists every candidate with its size and reason, and every item
it keeps with the reason it was kept (`--json` prints the same as data). It removes, and only when
each is safe:

- git worktrees under the loop's worktree roots (`workers.<name>.worktrees` and the propose root)
  whose branch is merged into `origin/<branches.integration>`, or whose branch is gone, then
  `git worktree prune`;
- local branches with `branches.change_prefix` that are merged into the integration branch;
- worker run folders whose task finished more than `cleanup.keep_days` days ago (default 3);
- the loop's own logs over `cleanup.max_log_mb` (default 50), rotated with the newest part kept.

Opt-in: with `cleanup.docker: true` it removes Docker volumes and images carrying
`cleanup.docker_label` (a volume in use is kept); with `cleanup.caches: true` it removes test
caches older than `keep_days` under the system temp dir matching `cleanup.cache_globs` (default
`[vitest-*, pytest-of-*]`).

It never removes a worktree with uncommitted changes or unpushed commits, an unmerged branch, the
current checkout, a run folder whose process is alive, or anything outside the configured roots; a
path it cannot check is kept, with the reason "not checked".

Automatic runs (off when `cleanup.auto: false`): a worker runs `dl clean --apply` after each task,
and `dl clean --apply --when-full` before a task when the worktree root's filesystem is at or above
`cleanup.threshold_pct` (default 80); a SessionEnd hook runs it at most once per 24 hours, silent
unless it removed something. `dl doctor` shows a **disk** row, red at or above `threshold_pct`.

## Releasing

Two scripts at the repository root, not shipped in the plugin:

- `tools/merge.sh <branch>...` merges task branches from `MERGE_REMOTE` (default `origin`) into
  `main`, prunes the neutrality guard's `KNOWN` list, runs the base suite plus every test file the
  branches touched, then fast-forwards `main` and pushes it. A conflicting branch is skipped and
  named.
- `RELEASE_REMOTE=<repository> tools/release.sh [--push]` builds the release from `main` as one
  commit tagged `v<version>` (the version in `plugin.json`) on top of the release remote's `main`.
  It refuses an existing tag, a missing private name list (`DEV_LOOP_PRIVATE_NAMES`, else
  `~/.config/dev-loop/private-names.txt`, kept outside the repository), any neutrality offender
  (`KNOWN` must be empty) and any hit of `dl ci secret-scan`. Without `--push` it is a dry run.

`dl ci secret-scan` names a token-shaped line by path and line, never its text; a line marked
`secret-scan: ignore` is allowed.

## More

- `ARCHITECTURE.md`: the package map and the hook map.
- `CHANGELOG.md`: what each release changed.
