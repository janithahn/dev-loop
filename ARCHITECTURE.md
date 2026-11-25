# Architecture

One repository, one marketplace (`.claude-plugin/marketplace.json`), one plugin
(`plugins/dev-loop`). The plugin has two halves: the Claude Code parts (skills, agents, hooks,
the settings pane) and a Python package, `dev_loop`, run as `dl`. The Claude Code parts never
hold logic a test cannot reach: a skill or hook calls `dl`, or a script beside it.

## Layout

```
.claude-plugin/marketplace.json   the marketplace
pyproject.toml                    package `dev-loop`, import package `dev_loop`, console script `dl`
plugins/dev-loop/
  .claude-plugin/plugin.json      the plugin manifest
  bin/dl, bin/dl-bootstrap        the launcher and the venv bootstrap
  skills/<name>/SKILL.md          /dev-loop:<name>
  agents/<name>.md                agent types dev-loop:<name>
  hooks/                          hooks.json, the hook scripts, githooks/, the settings pane
  src/dev_loop/                   the package (below)
  templates/                      what `dl init` writes: dev-loop.yaml, AGENTS.md, CLAUDE.md,
                                  knowledge/, change/ (the change folder's files), ci/
  tests/
```

## The project and the user

Every part finds the project from `CLAUDE_PROJECT_DIR`, else `git rev-parse --show-toplevel` in
the working directory, never from its own file's place. The project holds `dev-loop.yaml` and the
change folders (`paths.changes`). Per-user state lives under `~/.claude/dev-loop/` (the context
gate's controls) and `~/.dev-loop/` (the task cache). The plugin's venv lives in
`${CLAUDE_PLUGIN_DATA}`.

## Package map (`src/dev_loop`)

`__main__.py` holds one table from subcommand to module; each module exposes
`main(argv) -> int`, imported on demand. A subcommand whose module is missing answers
"not built yet" and exits 2.

| Module | Owns |
|---|---|
| `config.py`, `schema.py`, `defaults.yaml` | the merged config (defaults under the project's file), its one validator, `dl config` |
| `config_write.py` | the settings pane's edits: view with sources, write, drift |
| `init.py`, `doctor.py` | `dl init` (idempotent project files) and `dl doctor` (what the loop needs) |
| `propose.py`, `tasklist.py`, `verify.py` (`verify.host_steps` run with no runner), `pr.py`, `status.py`, `archive.py` | one loop step each |
| `records/` | the one record writer, lessons, and the combined views (follow-ups, metrics, lessons, trend, pitfalls, rules, distil) |
| `review/` | one review round (`pr-review.sh`), the reviewer's prompts and base standards, the verdict contract |
| `ci/` | the CI steps, the size cap, the gate mode, the stacked-part rebase, the git hook bodies, the pre-push check |
| `hosts/` | the host adapters, picked by `host.provider` in `__init__.py`: `ado.py` (credential, request, PRs, threads, the review-clean PR status), `github.py` (PRs, threads, the review-clean commit status through `gh`), `ado_work.py` (work items), `github_work.py` (Issues as the task board), `cli.py` (`dl host`) |
| `tasks/` | workers: `dl tasks` over host work items or GitHub Issues, `dl worker` |
| `dashboard/` | the transcript scanner, the budget gate, spend, the controls store, the local HTTP server |
| `fleet/` | the OTLP exporter of worker load, account use and delivery series |

Only `hosts/` talks to the host, over the standard library's `urllib`; everything else calls it.
Only `records/record.py` writes a change's records.

## Hook map (`hooks/hooks.json`)

Every command reaches its file through `${CLAUDE_PLUGIN_ROOT}`.

| Event | Matcher | Hook | What it does |
|---|---|---|---|
| `SessionStart` | | `dl-bootstrap` | builds the venv under `${CLAUDE_PLUGIN_DATA}`; idempotent |
| `SessionStart` | | `self-test.py` | in the bootstrap's command, after it (never beside a venv half built): runs the gate and the guards (the rules guard too) once on harmless events; names a broken one and `dl doctor` |
| `SessionStart` | | `session-guard.sh` | warns on a protected branch; prints the session checklist |
| `UserPromptSubmit` | | `approval.py` | on a user message `Approved: <slug>`, writes the change's approval record (the only writer) |
| `UserPromptSubmit` | | `plan-recall.py` | on `/dev-loop:plan` or `/dev-loop:build`, hands the model the change's recall |
| `PreToolUse` | Edit, Write, MultiEdit, NotebookEdit | `branch-guard.sh` | refuses an edit on the integration, release or a protected branch |
| `PreToolUse` | Bash, PowerShell, Agent, Task, Edit, Write, MultiEdit, NotebookEdit | `rules-guard.py` | denies a background command in a headless run, a subagent's edit of Claude Code settings, a hand edit of `records.jsonl` and a size waiver added to a `proposal.md` |
| `PreToolUse` | Agent, Task | `brief-contract.py` | checks a brief's sections and its Read first count against `briefs`; warns or blocks |
| `PreToolUse` | Bash | `push-guard.py` | refuses a `git push` to a protected branch, `--all`, `--mirror` and any `--no-verify` |
| `PreToolUse` | any | `context-gate.py` | caps a subagent's context by its role; opens the hand-off near the cap |
| `PostToolUse` | Edit, Write, MultiEdit | `post-edit-check.sh` | an advisory lint on the edited file only |
| `PostToolUse` | Edit, Write, MultiEdit | `additive-tests.py` | reports an edit to a `fix_proves.tests` file that removes a test or an assert, or adds skip, xfail or `.only` |
| `PostToolUse` | Edit, Write, MultiEdit | `guard-on-edit.sh` | runs `gates.on_edit` on the edited file, in its own checkout |
| `PostToolUse` | Bash | `pr-loop-trigger.sh` | a raised PR nudges the review loop (`review.auto_trigger`) |
| `PostToolUse` | Skill | `plan-recall.py` | the same recall when an agent calls the plan or build skill |
| `Stop` | | `done-guard.sh` | names what the done rule still misses; never holds the turn it caused |
| `SessionEnd` | | `session-end-clean.sh` | runs `dl clean --apply` at most once per 24h (`cleanup.auto`); silent unless it removed something |
| `SessionStart`, `UserPromptSubmit`, `Stop`, `Notification`, `PreCompact`, `SessionEnd` | | `session-state.py` | sends the session's state and context to the dashboard |

`hooks.json` also loads the settings pane module, `settings.tsx` (`/dev-loop-settings`).

The git hooks (`hooks/githooks/`) run as `dl githook <name>`: `pre-commit` warns when the shared
settings file is staged; `pre-push` runs the pipeline's cheap steps for what the branch changed.
