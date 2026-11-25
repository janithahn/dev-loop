# dev-loop engineering guide

You are working on the dev-loop plugin: a Claude Code plugin and a `dl` command line that run a
small, self-checking development loop (propose → build → verify → pr → review → land) over any git
repository. Read the plugin's own docs under `plugins/dev-loop/` before changing behaviour.

## Project law

Rules no change may break:

1. **One codebase, stdlib + PyYAML only.** No third-party runtime dependency.
2. **Neutral.** No client, project, company, person or machine name anywhere; the neutrality guard
   (`plugins/dev-loop/tests/test_neutral.py`) holds on the whole tree.
3. **Test first, additive only.** Every change ships with a test; a green test is never weakened.
4. **Small, direct code.** Files small, functions short, execution paths direct; no premature
   abstraction.

## Stack

Python 3.12, PyYAML. The command is `dl` (`plugins/dev-loop/bin/dl`); the package is `dev_loop`
under `plugins/dev-loop/src`. Tests run with
`uv run -q --no-project --with pytest --with pyyaml python -m pytest plugins/dev-loop/tests -q`.

## Host

This repository uses the **local** host (`host.provider: local`): no hosted pull-request system,
only git against the `hub` remote. `dl pr`, the review and `dl land` all work with git and files
alone.

Follow the working rules in [.dev-loop/RULES.md](.dev-loop/RULES.md).
