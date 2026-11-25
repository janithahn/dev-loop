# Changelog

Keep-a-Changelog form, with "Config" and "Migration" sections per release.

## [Unreleased]

## [0.4.23]

Ready for a public repository.

- Name guard: the list of private names a project must never publish is read from a file outside the repository (`DEV_LOOP_PRIVATE_NAMES`, else `~/.config/dev-loop/private-names.txt`); the repository keeps only generic checks (email addresses, IP addresses, internal review and planning references). `tools/release.sh` refuses to publish without that list.
- Comments, docstrings and test data state the rule or reason itself instead of citing internal review threads, planning ids or dates.

- Neutrality guard: the private names it denies are read from a list outside the repository (`DEV_LOOP_PRIVATE_NAMES`, else `~/.config/dev-loop/private-names.txt`); `tools/release.sh` refuses without it. Shipped code and docs state each rule's reason instead of citing where it came from.

## [0.4.22]

Fixes for what a real two-part row ran into, so a row reaches done with no hand edits of its records.

- Records: `dl record <change> done [part=<a>]` works, the same order as every other `dl record` call; a done check on a change that does not exist is refused instead of passing silently.
- Stacked parts: a part's PR title names its part (from the latest started part), and a stacked part's description opens with the PR it sits on. A malformed tasks list gives the plain title instead of a crash after the push.
- Status: new change folders no longer carry the hand-kept Progress table that went stale; the records and `dl status` hold the state.
- Worker: a runner that updates itself keeps its lock across the restart, so a leftover child no longer makes the new runner refuse to start; an inherited lock is trusted only when it is the run folder's own.
- Done guard: the Stop check hands git's raw path list to `dl record done --touched-paths -`, and Python alone decides which change folders were touched. A finished change with no green verify is now held whatever the spelling of `paths.changes` or `paths.archive`, with archived or moved changes, odd folder names, a project in a subfolder, `diff.relative`, Git Bash on Windows, macOS or BusyBox, and non-ASCII project paths.
- Fix briefs: spec lines are matched to a finding's file by path, so `a.py` no longer pulls in a line about `data.py`.

## [0.4.21]

Fixes for the two gaps a real-row comparison found: review fixes that add new majors, and turns that run to the time limit.

- Simplify before the PR: `dl pr` refuses to open a new PR until a simplifier record follows the change's last review round, so the simplifier's edits are reviewed in the same round instead of needing another. The build skill runs the simplifier at the end of the build. An open PR is never refused for this.
- Fix briefs: new `dl review fix-brief <change> <thread>...` reads the round's threads on stdin and writes the fixer's brief with the reviewer's own standard, the spec lines for the touched files, the decisions and the rules, so a fix keeps to what the reviewer checked.
- Stop points: the build, pr, review and verify skills and the builder agents say where each step ends and what to do when it cannot finish, instead of retrying.
- Worker: `workers.max_turns` (default 150) caps a worker's turns.
- Status: `dl status` names a review round that is still owed, and survives legacy rounds with missing counts or numbers.
- Fast track is re-read when the integration branch moves.
- Verify: coverage on a stacked part is judged from its parent; the report section and the record are written together (the report is restored when the record write fails).
- Records: a repeated simplifier record is kept, so a re-raised PR or a second part can open.

## [0.4.20]

Fixes from the incidents real rows recorded on 0.4.16-0.4.19.

- Size cap: a change is measured from its fork point (`base...HEAD`), and the base's size section is read on its own. A stacked part is measured from, and rebased on, its parent; `dl pr --base <parent>` remembers the parent, refuses this branch, the release branch or a protected branch as a base, and forgets the parent only when the remote says it is gone. The remote check has a time limit and never prompts.
- Verify: new `verify.covers` maps each step to the paths it checks; a changed production path no step covers turns verify red, and covers with an empty `size.production` is "not run", never green. A failed step keeps more of its output.
- Propose: `dl propose --resume` continues a change whose branch or folder already exists, re-adding a deleted worktree; propose never prunes other worktrees. Propose writes an empty `records.jsonl`.
- Approval: the approval record names the session that approved. The approval turn ends after the plan; `/dev-loop:build` starts the build. The planner commits its plan files.
- Done rule: a fast-track round counts as clean with no recheck round when every open blocker and major has a debt record of the same severity (one per thread). Fast track is read from the integration branch's config, so a change cannot relax its own done rule; `dl records fast-track` prints it for the review skill. A hosted round 1 skips only the cut-assertion check; a fix still needs a test. Follow-ups accept `thread=none`.
- Lessons: `Files:` accepts commas as well as spaces. `dl --version`.
- Azure DevOps: a PR description is cut to 4000 UTF-16 units; a 400 names the host's reason, with every credential in it masked.

## [0.4.19]

Recall gives the planner only lessons that bear on the change, and the build checks the plan used them.

- `dl records recall` shows a past lesson only when it shares a place with the change's paths, or its rarity-weighted word score clears a floor (ln N of the lessons ranked; twice that for a lesson from another place when the paths name places). Fewer than three is normal; none prints `recall: no past lesson near this change`. Before, word overlap alone always filled three slots, so an unrelated area's lesson could lead the plan.
- A lesson whose summary or avoid holds another field's label (a legacy one-line lesson, or two lessons run together) is read, cut at the first embedded label, instead of being skipped whole as "fields ran together". Distil (`review_distil`) is unchanged.
- `dl tasklist set <change> <T> doing` refuses (exit 2) while `design.md` does not name every lesson id the last recall before approval showed; the refusal names the missing ids. The plan skill and the planner agent say so.
- Tests: two recall expectations changed with the floor (a lesson sharing only a rare word with no place, and a lesson with no place and no shared word, are no longer shown).

## [0.4.18]

Parity with the reference loop the plugin came from: the context gate can no longer fail silently (shared venv lookup, every hook logs its crash, a session self-test, doctor red on hook errors, gate errors in telemetry); PR, push, records, headless and additive-test rules enforced by hooks; fix-proves, size cap, brief contract and verify scope as strict as the reference; delivery telemetry for rounds, stages and gate runs.

- `DEV_LOOP_SELF_TEST_TIMEOUT` raises the session self-test's per-hook timeout (default 5 s) on a host known to be busy; its own tests run load-proof.
- Tests only: every test runs with its own HOME (a conftest fixture), and a test that still grows the runner's own `~/.claude/dev-loop/hook-errors.jsonl` fails. Tests that crashed a hook on purpose had written the real log, which turned `dl doctor`'s `hook errors` row and the end-to-end tests red for a day.
- The hook self-test also runs `rules-guard.py` (it denies calls, so a broken one is named at session start); `self-test.py --list` and `dl doctor`'s `hook self-test` row count 7 hooks, not 6.
- `rules-guard.py` and `additive-tests.py` find PyYAML through the shared venv lookup (`hookenv.add_venv`) before they load the config. With a plugin venv built on another Python 3 than the hooks' `python3`, the config loader had exited and both hooks let the call through: a hand edit of `records.jsonl`, a size waiver, a `gates.ci_only` command, a removed test.
- `rules-guard.py` and `additive-tests.py` say and log their own crash like every other hook (`hookenv.crashed`): SystemExit included, one stderr line (unchanged) and one line in `hook-errors.jsonl`, so `dl doctor`'s `hook errors` row shows it. They still fail open (exit 1).
- Tests only: one test walks `hooks.json` and holds every Python hook under PreToolUse and PostToolUse to both rules (the venv added before the config is imported; its own crash said once and logged), so a new hook is covered when it is registered.
- Known, not in this change: the hook review's minors m8, m9 and m11 are open; nothing here changes what they describe.
- `dl doctor` runs the hook self-test once per run: the context-gate row, the self-test row and the JSON output's gate row read the same result (it had run up to three times).
- Tests only: a Windows venv's `Lib/site-packages` is pinned by a test of the shared PyYAML lookup.
- Tests only: the self-test's test file names the 5 s budget (its text still said 2 s), and the "every Python hook catches BaseException" test checks the handler at each hook's boundary, not the word anywhere in the file.
- A hook's crash line and the gate's "not enforcing" message echo a SystemExit's text only when it is the plugin's own "PyYAML is missing" line; any other exit text (it may quote a config value) is reduced to the error class.
- A hook whose crash cannot be logged for any reason (not only an unwritable folder) still says its one stderr line and ends as it would have; the log's own failure no longer replaces the hook's exit.
- The hook self-test marks a hook broken from the shared error log only for the hooks it started itself; a line another session logged for some other hook no longer adds a "broken" name.
- The hook self-test takes exit 2 as a pass only from the guards (their "blocked"); `dl config` exiting 2 (the launcher's "no Python with PyYAML") is named broken.
- The gate reads every common form of an agent definition's `tools:` to tell whether the agent can run the hand-off's record command: a block list (`- Bash`), a flow list (`[Read, Bash]`), quoted names and scoped tools (`Bash(git:*)` is Bash), less an unscoped `disallowedTools` name, in the frontmatter only. Read-only now means no Bash: an agent with Bash and no Write or Edit (a reviewer) gets the record command, not the reply hand-off. A line that cannot be read means the agent can record.
- `dl-bootstrap` builds a venv beside the old one (`venv.new`) and moves it in whole, so a rebuild that fails (no network, no `python3-venv`) leaves a ready venv as it was instead of deleting it first; a venv that was never finished is still removed.
- A first session raises no false "broken hooks" alarm: `dl-bootstrap` and the hook self-test are one SessionStart command, in that order (a session's commands start side by side, so the self-test could judge a venv the bootstrap was still building). The bootstrap's own lines go to stderr; a venv it could not build is said by the self-test when a hook needs it.
- The hook self-test also loads what the context gate needs to judge an agent's call (the config, the gate and the scanner, under `python3 -S` with the plugin venv's packages) and names `context-gate` broken when that fails; `dl doctor`'s context-gate row asks the self-test every time and is red when it does. A plugin venv with no PyYAML had passed both, since the gate's main-session event needs none of it.
- The brief contract no longer allows a call when the project config is bad: it says `project config not used: <error>` on stderr and checks the call against the shipped defaults.
- `dl doctor`'s context-gate row is green when the hook self-test runs the gate ok, even with no heartbeat for a week (a quiet gate writes none), and red when the self-test names it broken; "not seen running" now shows only when the self-test did not answer either.
- The hook self-test gives each hook 5 s, not 2, so a loaded machine no longer reports a healthy hook as broken.
- The context gate finds PyYAML in a plugin venv built on another Python 3 than the hooks' `python3` (as `uv venv` may build it); it had failed open on every call past the floor, so no capped agent was handed off.
- A bare agent type (a project's own copy, e.g. `planner`) gets the role of the namespaced type of that name (`dev-loop:planner`), not the fallback role's smaller cap.
- An agent that cannot run the hand-off's record command (a read-only built-in, or a definition granting no Bash) is told to stop and put the hand-off in its final reply; any other agent still gets the record command.
- Every hook that reads the config finds PyYAML through one shared lookup (`dev_loop.hookenv`): each `lib/python3.*/site-packages` of the plugin venv, or a Windows venv's `Lib/site-packages`, else the system's. The brief contract had looked under its own Python version only and let every brief through; approval and plan recall had not looked in the venv at all.
- `dl-bootstrap` builds the plugin venv on the hooks' own `python3` (`uv venv --python` too) and rebuilds a ready venv whose `pyvenv.cfg` names another Python version.
- Every Python hook catches its own crash at its boundary, SystemExit included (the config loader's "PyYAML is missing" had escaped every `except Exception`, unlogged), still lets the call through, says it in one stderr line (with the plugin's own exit reason, if any) and appends the hook, error class and frame to `~/.claude/dev-loop/hook-errors.jsonl`.
- The context gate writes its heartbeat only after it has judged a call (it had written it first, so a gate that crashed on every call still looked live), and a gate that cannot judge at all (an import fails, or the config loader exits for want of PyYAML) says `dev-loop gate is not enforcing: <reason>; run dl doctor` in a `systemMessage` and exits 0: still open, now seen. It still logs the crash and still takes no PyYAML from the system when a plugin venv exists.
- A new SessionStart hook, `self-test.py`, runs the context gate, the brief contract and the push, branch and done guards once each on a harmless event, as `hooks.json` registers them, plus `dl config get version`, side by side within 2 s. A hook that exits other than 0 or 2, runs past the 2 s or logs a crash is named in one `systemMessage` with "run dl doctor"; nothing is said when all pass, and the session is never blocked. `self-test.py --list` prints `ok <hook>` or `broken <hook>` a line each.
- `dl doctor` has two new rows: `hook self-test` (red naming each hook the self-test finds broken) and `hook errors` (red while `hook-errors.jsonl` or `gate-errors.jsonl` holds a line of the last 24 h, naming each hook, error class and count).
- The session-state hook sends the hooks' own errors to the telemetry endpoint: one `dev_loop.gate_error` record per hook and error class (the two names and a count, never text) for the log lines newer than `~/.claude/dev-loop/errors-sent.mark`, once per new batch; a send that fails is retried with the next event and is not logged as a hook error.

- The push guard refuses an agent's PR approve, vote, merge or auto-complete (`az repos pr`, `gh pr`, curl or python calls to the PR API); the lander or a person does it.
- The push guard judges every simple command of a line on its own (split on `;`, `&&`, `||`, `|`, `&`, newlines and backticks outside quotes; inside `$( )`, `( )` and `bash -c '..'`; behind `sudo`, `command`, `env`, `export`; `az.cmd`, `gh.exe`), so `echo go; curl -X PUT ..` no longer passes. Only a command led by echo, printf, grep, rg or git is taken as a mention.
- A PR API call is a decision only when it writes (PUT, PATCH, POST or a data flag in the same command): a GET of `pullrequests/<id>/reviewers`, a read piped into python and a test run that names `autoCompleteSetBy` pass. Refused writes: `pullrequests/<id>/reviewers` (any id, `$PR` too), `pullrequests/<id>` itself (the body may sit in a file, so a title edit by curl is refused too: use `az repos pr update`), GitHub `pulls/<id>/merge` and `pulls/<id>/reviews` unless `event=COMMENT` (curl and `gh api`), and `az devops invoke --resource pullRequestReviewers`.
- `gh pr review --request-changes` is a vote; `gh -R o/r pr merge` is found past the global flag; `az repos pr update --status Completed` is matched in any case, and only the value after `--status` counts.

- The push guard refuses `--force`, `-f` and a `+refspec` push without `--force-with-lease`, and a force-with-lease to any branch but the current one.
- A lease push of the current branch is allowed whatever its prefix (the review loop folds its fixes on PR branches that may sit outside `branches.change_prefix`); a protected branch is still refused, and `git push --force-with-lease origin <another branch>` stays refused, so the earlier "any feature branch" allowance is not restored. `--force-with-leas` counts as the lease flag and `@` as HEAD.

- The push guard also refuses the ways round git hooks: `git -c core.hooksPath=..`, a `git config` write to core.hooksPath (set, `--unset`, `--unset-all`, `--edit`, `--remove-section core`, `--rename-section core`) and `HUSKY=0` wherever it is set (inline before any tool, `export`, `env`). `git config core.hooksPath .githooks` (what `dl init` and `dl doctor` tell you to run) and the read forms pass. `git commit -nm` and an abbreviated `--no-verif` are no-verify.
- An unparsable piece of a command is allowed and said once on stderr, whichever tool it names.

- `dl land` never merges into the configured `branches.release`, whatever `branches.integration` says.
- `dl ci fix-proves` reads the run's junit report: a fix is proved only when a changed test ran and failed on an assertion; a fixture error, an empty run or a command that wrote no report stops with exit 3 and says so, where any exit other than 0, 126 and 127 passed before. The test command must accept `--junitxml`.
- `dl ci fix-proves` runs only for a fix PR (a `fix/` branch or a changed status.md holding `fixes-defect`, aimed at the integration branch; `FIX_PROVES_BRANCH` and `FIX_PROVES_TARGET` override the detected values) and says "not a fix PR" otherwise. Its verdict table: pytest exit 5 skipped; no test in the report exit 3; an assertion failure or a collection error proved; any other error exit 3; every test passing exit 1.
- The PR-loop nudge also fires on `az repos pr create`, `gh pr create` and a curl or python POST to a `/pullrequests` endpoint (the command only, never its output).
- `verify.affected` is read: its command's output fills a `{affected}` placeholder in a `verify.steps` command, so a step can run only the tests a change affects while a step without the placeholder (a guard) runs in full; unset, failed or empty, the placeholder is empty and the step runs in full.
- `dl ci fix-proves` stops with "not run, git diff failed" (exit 1) when git cannot diff the base, where it said "skipped" and passed.
- `dl ci fix-proves` strips `refs/heads/` from `FIX_PROVES_TARGET`, as it does from the branch, so a host that gives the full ref is not skipped.
- `dl ci fix-proves` on a detached HEAD (a CI checkout) reads the PR's branch and target from the host (`GITHUB_HEAD_REF` and `GITHUB_BASE_REF`, `SYSTEM_PULLREQUEST_SOURCEBRANCH` and `SYSTEM_PULLREQUEST_TARGETBRANCH`) when `FIX_PROVES_BRANCH` and `FIX_PROVES_TARGET` are unset; with no branch at all it stops with "not run, cannot tell the branch" (exit 1), where a fix PR was skipped as "not a fix PR".
- `fix_proves.command` may name `{report}`, the path its junit report is written to; `--junitxml=<path>` is added only when the placeholder is absent, so a command that is not pytest is no longer broken by the option. A run with no report says the command must write one.
- `dl ci fix-proves` removes a junit report already in the tree before the run, so an earlier run's report is never read as this one's.
- `dl ci fix-proves` reads the junit report with the Python `dl` runs on and stops with "not run, <python> not found" (exit 1) when there is none, where it said no test ran; its globs and `paths.changes` are matched from git's top level, where the test command also runs, so a project below the top is no longer a pathspec error.
- The PR-loop nudge fires only on a POST to the `/pullrequests` collection: a thread or reviewer write to one PR (`/pullrequests/<id>/...`), a PATCH, a PUT and a `-G` read that carries data stay silent.
- The PR-loop nudge also fires on a create inside a multi-line command, inside a script given to `sh -c` or `bash -c`, on `gh` with options before `pr create` and on a urllib `method='POST'`; a `-h` counts as help only in the create's own segment, never elsewhere in the command or inside a quoted title.
- `verify.affected` runs after `verify.runner` and under `verify.step_timeout_minutes`, as a step does; a lookup past the timeout is killed with its children and the step runs in full. Migration: a `verify.affected` that named the runner itself drops it.
- `dl land` runs its gate with `{affected}` empty, so every step runs in full and a narrowed run never gates a merge; `dl verify` writes a narrowed step's outcome as `passed (affected: N)` in the report and on screen.
- The pre-push check leaves a step's `{affected}` placeholder empty, so the step runs in full, where it ran with the literal text `{affected}`.
- rules-guard denies `dl records approve` in every spelling: approving a distilled skill is the owner's step.
- New key `gates.ci_only` (a list of regular expressions, default empty): a hook denies a Bash call whose simple command matches one unless `CI` or `TF_BUILD` is set, so whole suites stay in CI; words inside a quoted argument do not count, and an invalid expression is refused naming its index.
- A hook denies `run_in_background` on Bash, Agent and Task in a headless run (`DEV_LOOP_ROLE` set or bypass-permissions mode); the land skill's `background: true` frontmatter is not a tool call and is not blocked.
- A hook denies a subagent's edit of `.claude/settings.json` and `.claude/settings.local.json`.
- A hook denies a hand edit of a change's `records.jsonl` and an edit that adds a `Size waiver:` line to its `proposal.md`; the bold form `**Size waiver:**` counts, as in the size cap. A Bash command that writes `records.jsonl` or appends a waiver to `proposal.md` is denied too, matched on the command text only; known gap: a write built from variables or run by a script is not seen.
- The records and waiver rule judges a Bash command by the file it writes (a redirect's target, tee, the last argument of cp, dd's `of=`, an in-place sed, a path an interpreter opens for writing), inside the changes folder only: a command that reads `records.jsonl` or `proposal.md` (`cat ... 2>/dev/null`, `grep ... > /tmp/x`, `jq 'select(.n > 3)'`, `cp` out of the folder), a commit that names them and the writer with its output redirected all pass. A command the hook cannot parse is allowed, with one line on stderr.
- A hook reports an edit to a test file (`fix_proves.tests`) that removes a test or an assert, or adds skip, xfail or `.only`: tests are additive only.
- The same rule also denies a Bash command that removes or rewrites a change's `records.jsonl` another way: `rm`, `mv` (either side), `truncate`, `git checkout|restore|rm|mv` of the path, an in-place `perl` or `sed` however the flag is written (`-E -i`, `-e ... -i`, `-Ei.bak`, `--in-place`), a literal path a `python`, `node` or `ruby` program appends to, writes, renames or removes (`appendFileSync`, `Path(...).open('a')`, `os.remove`), and a glob of the name (`records.json*`) after a `cd` into the folder. Known gap, as before: a path built from a variable, a script file, `find -exec`, `xargs`, or a git command that names no path (`git reset --hard`, `git stash`).
- A size waiver brought into a change's `proposal.md` from another file is denied too: `mv`, `cp`, `cat file >>` or `tee -a` whose source file holds a `Size waiver:` line the proposal does not have, and a glob of the name (`proposal.m*`). The size cap now prints the waiver line it accepted (`size cap: a person confirms this waiver in review: <line>`). Known gap, stated rather than closed: no local check can tell who wrote a line, since a shell can write any file, any record and any git author, so a waiver assembled from pieces (`printf '%s waiver:' Size`), from a variable or by a script still reaches the file; the person who reviews the PR is the check, and the gate log now shows them the line.
- The waiver rule applies an Edit, Write or MultiEdit to the proposal's text in memory and compares the waiver lines before and after, so a waiver formed from a fragment (old `e**`, new `e waiver:** ...`), in two MultiEdit steps or by `replace_all` is denied; an edit of a proposal that forms no new waiver line passes, one that already holds a waiver included.
- The settings rule also denies a subagent's Bash command that writes `.claude/settings.json` or `.claude/settings.local.json` (`jq ... > t && mv t .claude/settings.json`, a redirect, `cp`, `sed -i`, `rm`, a program that opens it for writing), and compares the path in lower case (`C:\Repo\.Claude\Settings.json`); a subagent's read of the file and the coordinator's own write pass.
- The additive-tests hook no longer reports a renamed test as removed: it reports only when more test names are gone than new ones appeared.
- The additive-tests hook matches the markers that switch a test off (`@pytest.mark.skip|skipif|xfail`, `pytest.skip(`, `unittest.skip`, `expectedFailure`, `.skip(`, `.only(`, `.todo(`, `xit(`, `xdescribe(`, `t.Skip`), not the bare word: a test named `test_it_skips_blank_lines` or a comment that says skip is not reported.
- The additive-tests hook drops whole-line comments before it counts, so a commented-out test is reported as removed and a comment that says `assert` is not counted; a check is now any of `assert...` (`self.assertEqual`, `mock.assert_called_once`), `expect(`, `raises(` and `.should`; an added bare `return` or `if False` is reported. New `dl ci additive-tests [--base REF]` makes the same comparison over every `fix_proves.tests` file that differs between the merge base and HEAD, so a test removed by `git rm`, `rm` or `sed -i` (which no edit hook sees) is found; tests moved to another file are not a removal. It is not a step of the CI templates or of the built-in pre-push list (a red step would block a deliberate removal): a project opts in with `verify.steps.additive: dl ci additive-tests` and `verify.prepush: [additive]`.
- The headless background rule also covers PowerShell (`run_in_background`, and the hook matcher names it) and a Bash command that backgrounds itself: a lone `&`, `nohup`, `setsid` or `disown` is denied in a headless run, while `a && b`, a quoted `&` and `2>&1` pass; a command that cannot be parsed is allowed with one stderr line.
- A change folder is found by the end of `paths.changes` in any parent of the path, so the records and waiver rule also covers the change folder of a linked worktree; the settings rule also reads `notebook_path` for NotebookEdit.
- The additive-tests hook matches a test file's path from the project root with the same matcher as `dl ci additive-tests` and `fix_proves` (a leading `**/` also matches the top level), not from the git top level.
- The additive-tests hook compares an edit with the event's `tool_response.originalFile` when it has one, so an edit after an earlier edit in the same session is judged on its own; it falls back to the text at HEAD.
- The additive-tests check counts the rows of a literal `@pytest.mark.parametrize` list: a removed row is reported like a removed test (an added, changed or moved row is not).
- `verify.host_steps` lists steps run on the host with no `verify.runner` prefix, in `dl verify` and the pre-push check.
- A worker entry takes optional `may_run` (routing kinds it may run, default all; named in its brief, a task of another kind is answered on the work item), and display-only `reach` and `accounts`. A may_run name that is no routing kind is refused.
- `dl ci size-cap` reads only the size section from the base (every other key is the working tree's), says where the cap and the globs came from (base, working tree, defaults) and exits 1 on a base it cannot diff.
- `dl ci size-cap`: a base with no `dev-loop.yaml` is judged like a refused one (the working tree's globs, the stricter of its cap and the default), so an adoption PR that bundles code is counted.
- `dl ci size-cap` ignores `SIZE_CAP` and `SIZE_ADVISE`: the environment cannot raise the gate's cap.
- `dl pr` judges size as `dl ci size-cap` does: by the base's size section, never by the branch's own cap or `SIZE_CAP`.
- Agents pin full model ids from the role defaults; `dl doctor` gains an "agent models" row that is red when a configured role model differs from the agent that runs.
- Delivery export: a repository's change records are their own source; when they cannot be read the run says `<repo> records failed (<error class>)` and exits non-zero, and the PR and gate series still go. `dev_loop_changes` states 0 for a stage no change is at.
- Delivery export: a change whose PR closed before the 30-day window (every task done, a review round on record, no PR in the window) stays at stage `merged` instead of falling back to `building`.
- Delivery export: the archive folder is never exported as a change; the change folders are the ones the checkout's own `dev-loop.yaml` names (`paths.changes`, `paths.archive`), unless the repo row states `changes`.
- Delivery export: a checkout with no changes folder still exports its PR series.
- Delivery export: the gate history file is named by org, project and repository, and a row with `"pipeline": null` has no gate source.
- Delivery export: `dev_loop_gate_runs` counts `partiallySucceeded` runs and states 0 for a result with no run in the window.
- Delivery export: a gate run canceled while queued (no start time) is counted in `dev_loop_gate_runs` with no duration point, instead of failing the repository's gate source.
- Delivery export: `dev_loop_gate_run_minutes` carries a `build` label, so a PR's rerun with the same result is its own series.
- Delivery export: `dl fleet-export` imports on Windows again; the gate history is written under the record lock, not `fcntl`.
- Delivery export: a change's done rule is judged against its own checkout (`record.missing` and `stale` take a `root`), so a run from cron neither fails nor asks another repository's git.
- Delivery export: the gate pipeline's finished PR runs, count by result and run duration, kept per repository under `~/.dev-loop` (`fleet/gates.py`); a failed source is said and exits non-zero.
- Delivery export: review rounds and findings per PR, each change's stage and the p50 review rounds of the week's merged PRs (`fleet/progress.py`).
- Builder agents carry the area "Do not repeat" rules, the guard step, "Read beyond the brief" and "the spec wins" lines.
- `dl record` and `dl lesson` refuse a field that quotes more than 40 characters, so document text cannot enter the records.

### Config
- `briefs.change_line` (default `false`): true requires a `Change: <name>` line in a brief.

## [0.4.17]

From the plugin's first real rows on a large repository that keeps its own loop tools:

- Every child `dl` starts (a repo's git hook included) gets the user's PYTHONPATH, not the plugin's.
- An open PR is judged from its own target, so a stacked part is not counted with its parent.
- `dl ci size-cap`: when the base's config is refused, the cap is the stricter of the working tree's and the built-in default, never HEAD's alone.
- `dl propose` carries the files kept local in the main checkout (skip-worktree and existing `.git/info/exclude` paths) into the change's worktree and prints which.

- `dl propose` continues a change folder that already exists on the integration branch: it makes the branch and worktree, writes nothing over the folder and says so.

- A `dev-loop.yaml` that exists but fails to load stops every `dl` command (exit 1, one line naming the file and the first error); `doctor`, `config`, `init` and `dashboard` still run so it can be repaired. A missing file still means defaults.
- `dl lesson` rewrites only the guides of the areas its lesson names; `dl records rules --write` still rewrites every stale one.
- `rules.generator` (default unset) names a shell command that writes the guides' Do-not-repeat blocks; `dl lesson` and `dl records rules --write` run it instead of the built-in writer and report its exit status.
- `rules.check` (default unset) is the staleness check when `rules.generator` is set: `dl records rules` runs it and returns its status; with none it says so and exits 0.
- Recall prints `skipped <n> unparsed record lines in <files>` on stderr when it skips record lines; `dl records --skipped` lists them as file:line.
- `dl tasklist` reads `T6b`-style ids as their own task; any other task id form is an error naming the line.
- `dl record <change> follow-up pr=none` is accepted for a follow-up found before a PR exists.
- The build skill's brief template carries the `## Decisions` heading (body `none`).
- `dl incident` repeats are told apart by date: the same incident on another day is recorded.
- A missing `gates.on_edit` guard script is reported once as a configuration problem, not as a failing guard.
- The review skill runs in the main context, so it dispatches the builders that fix its findings.
- The reviewer's context holds the nearest area guide (CLAUDE.md or AGENTS.md) of each changed file, cut per guide with a marker.
- `dl record done part=<a>` judges one part of a split change on its own tasks, so part a closes while part b is under way.
- The context gate's speed test compares the modules each path loads, not wall-clock milliseconds, so it no longer flakes under load.

## [0.4.16]

From the plugin trial's third run on a large repository:

- `dl review` prints the head it reviewed; a hosted host takes the status sha from that line.
- Two tasks that name the same file never share a wave; builders commit their own files by path.
- Over the cap during a build, the row is re-split inside its two parts before a waiver is asked.
- The rules file names the configured decision log and pitfalls file.
- A builder's report tells the tests that failed first from the pins.

## [0.4.15]

From the plugin trial's second run on a large repository:

- A stacked part's push is judged from its own base (the hook gets `dl pr`'s target).
- The done rule needs a reviewed PR for each started part.
- A round that fixes a carried thread counts as clean.
- The approval hook marks the status row.
- The PR nudge ignores quoted text.
- The review skill names only `success` and `failure`.

## [0.4.14]

From the tenth batch of autonomous whole-project runs:

- `dl pr` keeps merge commits when it rebases, so a task done by a merge survives.
- Builders compare whole output where it holds a temp path or other random name, and the
  reviewer files a chance-failing assertion as a major.

## [0.4.13]

From the ninth batch of autonomous whole-project runs:

- A thread waits for a person only behind written words copied from the proposal or a governance
  file; a case the proposal does not answer is the loop's to decide.
- A stacked change's tasks and review are measured from its own base, not the integration branch.
- `pr-get` accepts the pull request as `pr` or `id`.
- A brief's lessons get their own `## Lessons` heading, never under Read first.

## [0.4.12]

From the eighth batch of autonomous whole-project runs:

- The local host records the same clean status once: a repeated `dl host set-status` writes
  nothing and makes no commit. The review skill repeats the call only on a hosted pull request.
- The plan skill builds the answer an approved proposal gives, or returns
  `proposal change needed`.
- The review skill: one new test function per fixed source thread; the fixer never edits a thread.

## [0.4.11]

From the seventh batch and the trial rerun in an existing repository:

- The recall hook finds a change in its own worktree when the session started in another checkout.
- `dl host set-status` follows a records-only push on a hosted PR.
- A split row's part is done on its own (`## Part <name>` headings in `tasks.md`).
- `dl tasklist set <c> T<n> done` refuses production source outside the task's `Files:`.
- Recall no longer skips a lesson whose text names a Markdown heading.
- A background builder is refused; `dl incident` says its record is not committed.
- Builders and the review-fixer read only their files' pitfalls; the reviewer warns over 400 KB.

## [0.4.10]

From the sixth batch of autonomous whole-project runs and the side-by-side trial in an existing repository:

- The approval hook writes to the worktree on the change's own branch (`branches.change_prefix` + slug), then to the session's root; when several other worktrees hold a same-named change folder and none is on that branch, it writes nothing and says so.
- `dl record <change> round` refuses a fix that cut an assertion the PR had when first reviewed, or that is on the integration branch, and a fix that changed `size.production` source without adding a test. The fix is read from the reviewed head (local host) or the round's `base` to the working tree; a record-only fix needs no test, and a fix round may correct a test an earlier fix round added.
- `dl lesson` and `dl record <change> lesson` refuse, on the local host, a lesson logged before the change has a clean review run: a lesson is one fixed review finding.
- `dl pr` shows why a push failed: the last 20 lines the pre-push hook and git printed (the hook's stdout was dropped before), then the refusal; a credential in a remote URL is cut from them.
- The build skill sends a defect found while building to the change's `design.md`, and a defect of the loop itself to `dl incident`; `dl lesson` is only for a fixed review finding.

## [0.4.9]

From the third and fourth batches of autonomous whole-project runs and from adopting the plugin in an existing repository:
- `dl pr` refuses, on a first raise, an own commit that changes source and is no task's (`T<n>: <title>` or `<change>: simplify`).
- `dl pr` on an open PR prints no review nudge, and the nudge hook stays silent for `--help` and for a refusal; the review loop records `lessons-logged` also when it is 0.
- Every acceptance bullet must name a command in backticks (`dl tasklist parse`).
- `dl records recall` reads the config once (it read it per file) and bounds its git history read.
- `dl records rules` refuses to write guides when no lesson can be placed (`knowledge.packages` empty), so existing guidance is never stripped.
- `dl propose` writes in place on the change's own branch.
- `dl pr` tells uncommitted changes from a rebase conflict and names git's error; approval refusals say how approval arrives in a headless run; the build skill's brief follows `briefs.sections`.
- The review fork starts by moving to the PR's worktree.

## [0.4.8]

From the first batch of autonomous whole-project runs:
- `dl archive` runs from the change's worktree, commits the move and pushes it to the integration branch itself (never forced; refused if the diff leaves the changes folder).
- `dl tasklist set <c> T<n> done` needs exactly one commit titled `T<n>: <title>`.
- The local host's `set_status` takes only the head of the last review-run.
- `dl record <c> lessons-logged` refuses a count that differs from the change's lesson records.
- A fix to a record, report or test file needs a lesson but no new test.
- One review thread per crid per run, keeping the higher severity.
- The review skill shows the exact thread-reply command; a lesson's missing file is named.

## [0.4.7]

From the second autonomous whole-project run:
- `dl pr` refuses a spec with no acceptance bullet: `dl review` ticks only bullets, and the plan skill now asks for them.
- A failed `dl review` records nothing; `dl record <c> round` refuses unless the PR's last review-run is clean (or `review.fallback: subagent`).
- A forked review records its round after the fixes, with a true fixed count and one lesson per fixed thread.
- The approval hook also marks proposal.md Approved.
- `dl archive` compares against `<remote>/<integration>` after a fetch, so it works right after a land.

## [0.4.6]

From the first autonomous whole-project run:
- Approval gate: only a hook that sees the user's literal `Approved: <slug>` writes the approval record; `tasklist set ... doing` and `dl pr` refuse without it; the CLI refuses the kind.
- A clean round is provable: `dl review` writes a review-run record; local land needs one with rc 0 for the clean head unless `review.fallback: subagent`; the review-skipped notice never blocks land.
- Builder briefs: the build skill gives the literal Goal / Read first / Done when template; the contract blocks builder agents by default; task commits are titled `T<n>: <title>`.
- The simplifier and review fixes go through a builder; the main thread never edits product code.
- Local host: `dl host` from the main checkout finds a PR recorded in a worktree.
- size-cap reads HEAD's config when the base's dev-loop.yaml is refused by the schema.

### Fixed
- `dl ci size-cap` reads the size config from HEAD, saying so, when the base ref's `dev-loop.yaml` is refused by the schema, as it does when the base has none; a switch PR over a pre-plugin config is no longer stuck.

## [0.4.5]

### Fixed
- A change planned in the plan skill's own block form (`### T1: title` with `- Status:`, `- Kind:`, `- Files:`) passes the done rule and `dl land`: `tasks_done` and `dl config route` read the block form first and keep the checkbox form as a fallback. Before this, a change planned exactly as the plugin says could never land.

## [0.4.4]

### Fixed
Acceptance-tested: 13 acceptance batches (1,370 tests) found about 90 defects; this release fixes them.
- `dl` runs outside a Claude session; bootstrap never hangs on the network.
- No command acts on the defaults when dev-loop.yaml is refused; config errors are one line.
- `dl init` never takes over a project's git hooks or marketplace entry; no owner handle in code.
- Local land refuses code pushed after the clean round.
- Secret scan finds Anthropic keys, ADO PATs and key literals; never skips a text file silently.
- Pre-push steps time out; an unconfigured fix-proves is not run, never green.
- The runner honours branches.remote, refuses hosts with no task board, never starts a run for a #number in text.
- Unknown flags are refused; `dl host` never waits forever on stdin.
- done-rule, release-gate and size-cap never pass on nothing; size-cap reads the base's config.
- Records: leading-zero PR ids, dead-lock recovery, no duplicate line after a crash, a stale or unknowable verify head is stale.
- Doctor: remote timeout, tools row, duplicate-hook check across project, local and user settings.
- Hooks: push guard fails closed on a config error; the PR trigger fires only on a real `dl pr`.
- `briefs.agent_types` limits the brief contract to listed subagent types.

### Fixed
- `dl ci`: done-rule and release-gate never pass on nothing (no change, no tasks.md, an unreadable record line); size-cap exits 1 on a config error and judges a PR by the base ref's size config.

### Config
- `briefs.agent_types` (default `[]`).

## [0.4.3]

### Added
- `dl record legacy` and `dl record render` grandfather old-format records of a change listed in `legacy-changes.txt`, signed by a legacy-mark; readers accept the marks and fieldless legacy rows.

## [0.4.2]

### Fixed
- Local host: `dl record` accepts a local pull-request id (12 hex characters), and Models, Context
  and Rework lines with such an id are read as records.
- Local host: the pull-request and review records are committed on the change branch, so they
  travel with it and land with it; `pr_create` pushes the branch once. Host record files are
  replaced atomically, and a torn line is refused with its file and line instead of skipped.
- Local `dl land` runs the done rule, refuses unless the last review round is clean, and runs
  only from the pull request's worktree at its head with everything committed.
- The done guard judges committed changes against `<branches.remote>/<integration>`, not
  `origin`.
- A fresh `dl init` passes `dl ci records-current`: the REVIEW-PITFALLS template is the class
  table the checker reads.
- `dl init discover` no longer lists the shipped templates as guides.
- `-h`/`--help` on any `dl` subcommand prints its usage and never acts.
- `dl doctor` has rows for git hooks that never run (core.hooksPath unset or not `.githooks`) and
  for empty `verify.steps`.
- `dl pr` drops a leading "Proposal: " from proposal.md's heading in the PR title.
- The status.md template and the review prompts name commands `dl <cmd>`, not the old
  `loop <cmd>`.

### Changed
- A verify step that runs past `verify.step_timeout_minutes` is killed, with every process it
  started (POSIX and Windows), and reported failed with the timeout named; a hung step no longer
  hangs `dl verify` or `dl land`. This repository's own gate runs the suite in parallel.

### Config
- `verify.step_timeout_minutes` (default `20`): a whole number of 1 or more. Added to schema
  version 1; a file without it reads the default.

### Migration
- A local-host project lands each pull request from its own worktree with the change's records
  committed.

## [0.4.1]

### Added
- Subagent telemetry. The context gate records one line per subagent slow-path call — the session
  and agent ids, the agent type and its context size, ids and counts and the type only — rotated
  past 5 MB and written best-effort so it never changes the gate's verdict. The fleet exporter
  emits a `dev_loop_subagent_context` metric from the latest line per agent in the last 30 minutes,
  labelled by session, agent, agent type, host and repository.
- `dl doctor` gains a "checkout" row: it goes red when the loop's checkout is more than seven days
  behind the remote integration tip, so a stale checkout no longer runs the loop silently without
  the current gates.

### Changed
- The task runner updates itself when idle: it fast-forwards its checkout to the remote integration
  branch and re-execs, so a long-lived runner never drifts behind. It never resets, merges, or drops
  dirty or unpushed work.

### Config
- None.

### Migration
- None.

## [0.4.0]

### Added
- A git-only host (`host.provider: local`): the whole loop runs with no hosted pull-request system.
  Pull requests and review threads are recorded in the change folder; `dl pr` pushes the branch and
  records it, `dl land` fast-forwards and pushes the integration branch once a review round is clean
  and the local gates pass (never merging an un-rebased branch), `dl doctor`'s host row needs only a
  reachable git remote and no credential, and `dl init --host local` writes no hosted CI.
- `dl clean`: lists and, with `--apply`, removes the loop's leftovers — merged or branch-gone
  worktrees, merged change branches, old worker run folders and oversized logs, with opt-in Docker
  and cache cleanup. It is a dry run by default and never removes uncommitted or unpushed work, an
  unmerged branch, the current checkout, a worktree a live run holds, or anything it cannot check; a
  squash-merged branch counts as merged. A worker cleans after each task and before one when the
  disk is full, and a session-end hook cleans at most once a day. `dl doctor` gains a disk row that
  weighs free space, inodes and the temp directory.
- Worked review examples in the review standards — a blocker, a major, a minor and a borderline case
  left deliberately unfiled, each with the reasoning behind its verdict, plus a "one problem, one
  finding" rule so two passes do not file the same problem twice. `dl records examples` drafts
  candidate examples from the project's own fixed findings and deferred minors; it never writes the
  examples file or invents a case.
- A Setup section in the settings pane, built from `dl doctor --json` (each check as name, status,
  detail and one concrete fix, with no secret value): red rows carry their fix, optional rows are
  greyed, and a Re-check button and a "Setup complete" line show the state. The pane opens on its
  own when a required check is red, once per session unless the set of reds changes.
- The context gate proves it is live: it touches a heartbeat file on every subagent call, `dl
  doctor` has a "context gate" row with a self-check of the decision engine, and the row goes red if
  the gate stops running after it once ran.
- `records.guard_moves`: a lesson's guard path that has moved — to a new project path or into the
  plugin — counts as current instead of stale, without rewriting history; `dl records current
  --verbose` names each moved guard.

### Changed
- `branches.remote` replaces the hard-coded `origin` everywhere a `<remote>/<integration>` ref is
  built: `dl clean`, `dl propose`, `dl pr`, `dl ci` step bases, the review loop and the records'
  upstream ref, and the `prepush`, `stack_rebase` and `pr-review` scripts.
- The task runner keeps itself live: it writes a heartbeat each poll, sweeps dead locks during
  polling rather than only at start-up so they can no longer fill every slot and block it, runs each
  task in its own process group and kills that group when the run ends, and creates each task's
  worktree so no run starts in the shared checkout.

- The task runner supervises its own open tasks on each poll, with no model call: a Doing task
  whose run died and left unpushed work is re-run once and then tagged `needs-coordinator`; an
  unanswered question or a queued task older than `stale_hours` (24) is tagged at once. A Done task
  reopens when the coordinator comments on it, and a run that comments but leaves its worktree
  dirty or unpushed counts as a stall. Host adapters gain `add_tag`. Nothing is ever removed.

### Fixed
- `dl clean` judged "merged" against the wrong remote. The first real run, on a repo whose remote
  is not `origin`, called every branch "not merged" because `origin/<integration>` did not exist and
  every merge check failed (it deleted nothing — safe, but silently wrong). `dl clean` now judges
  against `<remote>/<integration>`, falls back to the local `<integration>` branch when that ref is
  absent, and when neither exists keeps every candidate as "not checked" with a summary line naming
  the missing ref — a missing integration ref never reports "not merged".
- Headless worker runs keep every command in the foreground. A run used to exit the moment the agent
  ended its turn to wait for a background command's notification, so it finished with no report; the
  brief now tells the run it is headless and the background-task path is turned off at the source.
- A stalled worker run — one that ends without finishing and leaves the task untouched — is now
  detected, commented and re-run once with a note; a second stall hands the task to the coordinator
  instead of leaving it silent. There is no third run.
- A headless worker run gets its role's context cap and hand-off: it is judged as that role rather
  than as an uncapped main session, and at the hand-off threshold it writes a short done/next note
  that its single re-run picks up (counted as a hand-off, not a stall). Without a role set, a main
  session stays uncapped as before.
- `dl init` fills every placeholder and leaves the shared git config untouched. It no longer writes
  `AGENTS.md` or the knowledge files with unfilled placeholders, names the project from the git
  remote (else the main worktree's folder, never a linked worktree's folder), and sets
  `core.hooksPath` only from the main worktree — from a linked worktree it prints the one command
  instead of silently writing the config every worktree shares. Existing files are still never
  overwritten.

### Config
- New keys, all optional (schema version stays 1):
  - `branches.remote` (default `origin`).
  - `cleanup.*`: `auto` (default `true`), `keep_days` (`3`), `max_log_mb` (`50`), `threshold_pct`
    (`80`), `docker` (`false`), `caches` (`false`), `cache_globs` (default `vitest-*`, `pytest-of-*`)
    and `docker_label` (default none).
  - `review.examples` (default none).
  - `records.guard_moves` (default empty: an old guard path or glob mapped to its new home).
  - `workers.<name>.default_role` (default `builder-backend`).
  - `host.provider` accepts `local`.

### Migration
- None. Every new key is optional and keeps the previous behaviour; a project switches to the
  git-only host by setting `host.provider: local` and re-running `dl init`.

## [0.3.2]

### Added
- `dl ci licences`: each direct dependency's licence against `licences.allow`; unknown counts as
  red, never a silent pass; "not run" with no manifest.
- `dl ci flakes --junit <file>...`: a test that failed then passed on a rerun is recorded as a
  flake and the build stays red (`--no-fail-on-flake` to only record); `dl records trend` shows
  flakes per week and the five flakiest tests.

### Config
- New key: `licences.allow`. Optional; schema version stays 1.

## [0.3.1]

### Added
- A PreToolUse(Bash) push guard (`hooks/push-guard.py`): it denies a `git push` whose destination
  is a protected branch (`branches.integration` + `branches.release` + `branches.protected`,
  matched as globs), a bare push while the current branch is protected, `--all`, `--mirror`, and
  any `--no-verify` on a push or commit. The rule holds whether or not the agent remembers it;
  every other command passes, and the guard fails open and loud on its own error.

### Config
- None. The guard reads the existing `branches.*` keys.

### Migration
- None.

## [0.3.0]

### Added
- Heavy skills run in a forked subagent (`context: fork`), so only their result reaches the main
  conversation: review (new `dev-loop:review-fixer` agent, background), plan (`dev-loop:planner`),
  research (`dev-loop:researcher`, background), verify and land. propose and init stay inline (they
  ask the user), build stays inline (it dispatches the builders).
- The pre-commit hook refuses a commit on a protected branch; `dl ci done-rule` checks the done rule
  in CI; `dl pr` prints the review-loop nudge itself.
- AGENTS.md: `dl init` makes the rules reach other agent tools too; CLAUDE.md imports it.
- The `dev-loop/review-clean` status on Azure DevOps pull requests (GitHub had it in 0.2.0).
- Incident records (`dl incident`), folded into lessons and trend.
- `dl ci release-gate`: open debt by severity, blocking only on `release_gate.block_on`.
- A review runner seam (`review.runner`, `review.model`, `review.runner_command`); `claude` stays
  the default and runs exactly as before.

### Config
- New keys: `release_gate.block_on`, `review.runner`, `review.model`, `review.runner_command`;
  the `debt` record gains `severity`. All optional; schema version stays 1.

### Migration
- None. Re-run `dl init` to add AGENTS.md.

## [0.2.0]

### Added
- GitHub as a host: pull requests, review threads and their resolution through `gh`; a
  `dev-loop/review-clean` commit status; GitHub Issues as the workers' task board; `dl init` reads
  GitHub origins and writes `.github/workflows/dev-loop.yml`; `dl doctor` checks the `gh` login.
- `dl land`: gate, approve and merge into the integration branch with one lock (Azure DevOps).
- Fast track (`review.fast_track`, off by default): one review round, serious findings fixed, the
  rest recorded as `debt`; `dl status` shows open debt.
- The brief contract: a PreToolUse hook checks every agent brief against `briefs.*` (warn or block).
- `dl records repeat`: the repeat-mistake rate, old and new definitions side by side.
- `dl ci fix-proves` (a fix's new test fails without the fix) and `dl ci secret-scan`.
- `dl config migrate`, a refusal of newer config versions, and a frozen per-version schema.
- Worker limits in `workers.<name>` (slots, poll, timeout, Docker slots, base); the runner tags
  each run for telemetry (only when telemetry is on), re-runs a task once after a mid-run comment,
  and refuses a settings file that would tag every run the same.
- The settings pane edits fast track and the brief enforcement, and shows the host and open debt.
- `tools/release.sh` and `tools/merge.sh` in the repository.

### Config
- New keys: `review.fast_track`, `review.fast_track_fix`, `briefs.*`, `fix_proves.*`,
  `gates.policy`, `workers.<name>.{slots,poll_seconds,timeout_hours,docker_slots,default_base}`;
  `host.provider` accepts `github`. All optional with defaults; schema version stays 1.

### Migration
- None needed. Re-run `dl init` to add the GitHub workflow on GitHub projects.

## [0.1.4]

- The git hooks `dl init` writes (`.githooks/pre-commit`, `.githooks/pre-push`) find `dl` outside
  Claude Code: `dl` on PATH first, else the newest copy in the plugin cache
  (`${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache/dev-loop/dev-loop/*/bin/dl`, highest version).
  With neither, the hook prints `dev-loop: dl not found, <hook> checks NOT run ...` to stderr and
  lets the commit or push go on; a failing check still blocks.

### Migration
- Run `dl init` once: it rewrites the two hooks it wrote before; commit them.

## [0.1.3]

- `dl init` writes the plugin's generic working rules to `.dev-loop/RULES.md` (plugin-owned,
  refreshed on the next `dl init` after a plugin update) and imports them from `CLAUDE.md`: a
  missing `CLAUDE.md` is written from the template, an existing one gains only the line
  `@.dev-loop/RULES.md`. The rules name the commands `dl <sub>` and add an "Agents" section.
- `dl doctor` has a "rules imported" row, and the host row names a web page answered in place of
  the API (check `host.org`, `host.project` and the credential).

### Migration
- Run `dl init` once: it adds `.dev-loop/RULES.md` and the import line; nothing else in
  `CLAUDE.md` changes.

## [0.1.2]

- `dl` runs from an installed plugin: the source always runs from the plugin folder, and
  `dl-bootstrap` makes a venv holding only PyYAML (python3-venv, else uv), marked ready only when
  complete, so a half-made venv is never used.

## [0.1.1]

- `bin/dl`, `bin/dl-bootstrap` and the shell scripts are executable again (the bit was lost on
  commits made on Windows); `test_modes.py` pins it.

## [0.1.0]

The first release: the dev loop as a Claude Code plugin, for projects hosted on Azure DevOps.

### Added
- Marketplace `dev-loop` and plugin `dev-loop`: skills `/dev-loop:*`, agent types
  `dev-loop:<name>`, the console script `dl` (see `README.md` for every command).
- Change records: a folder per change and `records.jsonl` written by one writer (`dl record`,
  `dl lesson`); combined views across changes (`dl records`).
- The review loop: incremental rounds, fix-or-defer, one lesson per fixed blocker and major
  (`dl review`, `/dev-loop:review`). The loop never approves or merges.
- The size cap, the stacked-part rebase and the CI steps (`dl ci`, `dl stack-rebase`).
- Session hooks: branch guard, context gate, post-edit check, project guard on edit, review-loop
  trigger, done guard, plan recall, session state (see `ARCHITECTURE.md`).
- The Azure DevOps host adapter (`dl host`): pull requests, threads and work items; the credential
  comes from git's credential helper, else `host.token_env`, and is never printed.
- Workers (`dl tasks`, `dl worker`), the dashboard (`dl dashboard`) and the fleet exporter
  (`dl fleet-export`).
- The settings pane `/dev-loop-settings`. Telemetry is off by default; its toggle is the only way
  to turn it on.

### Config
- `dev-loop.yaml`, schema `version: 1`, holding only a project's differences from the shipped
  defaults. Keys: see `README.md`, "Configuration".

### Migration
- None: the first release. `dl config migrate` removes a dashboard hook an older, pre-plugin
  install wrote into the project's settings.
