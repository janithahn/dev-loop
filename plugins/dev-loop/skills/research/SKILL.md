---
name: research
description: Investigate a question against high-trust primary sources and capture the findings as a Markdown file in the repo. Use when the user wants a topic researched, docs or API facts gathered, or reading legwork delegated to a background agent.
context: fork
agent: dev-loop:researcher
background: true
---

This skill runs in a forked `dev-loop:researcher` subagent, in the background, so the caller
keeps working while it reads. It sees no conversation, only `$ARGUMENTS`: the question, and
optionally the findings file's path. With no question, stop and return that the input is missing.

1. Investigate the question against **primary sources** (official docs, source code, specs,
   first-party APIs), not a secondary write-up of them. Follow every claim back to the source that
   owns it.
2. Write the findings to a single Markdown file, citing each claim's source.
3. Save it at the path the arguments name. With none, save it where the repo already keeps such
   notes (look for a `research` folder of Markdown notes); with none of those, at
   `research/<a short slug of the question>.md` in the project root. Say which in the result.

## Return

A short result, not the findings: the file's path, three to five lines of the main answers, and
any part of the question the sources did not settle.
