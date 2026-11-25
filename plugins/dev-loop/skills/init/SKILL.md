---
name: init
description: Set a project up for the dev loop - dev-loop.yaml, the plugin settings, the git hooks, the templates, then the project's docs found and named in knowledge.read_first and knowledge.guides. Use once per project, and again safely at any time.
---

# /dev-loop:init

Run from the project's root. Each step is safe to run again: a second run changes nothing.

1. Run:

   ```
   dl init
   ```

   It writes `dev-loop.yaml` (host and integration branch from `origin`), merges the plugin keys
   into `.claude/settings.json`, writes `.githooks` and copies the templates that are missing.
   Report the `wrote ...` lines. On a non-zero exit, show the one-line error and stop.
2. Run `dl init discover`. It finds the root `CLAUDE.md`, `AGENTS.md`, `ARCHITECTURE.md`,
   `CONTRIBUTING.md`, the conventions files and `docs/adr/` for `knowledge.read_first`, and every
   area `CLAUDE.md` or `AGENTS.md` for `knowledge.guides`. It sets a key only when
   `dev-loop.yaml` does not hold it, so a list the team edited is never replaced. Show the user
   the two lists (`dl config get knowledge.read_first`, `dl config get knowledge.guides`) and ask
   whether to drop or add any; edit `dev-loop.yaml` by hand for that.
3. Run `dl doctor` and report each line. Init is done when it is green; for a red line, name
   the fix it gives and do not claim the project is ready.
4. Tell the user the dev-loop settings pane (`/dev-loop-settings`) opens a Setup section that
   lists anything still missing with its fix, and re-opens by itself while any required row is red.
