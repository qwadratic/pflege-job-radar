<CRITICAL_INSTRUCTION>

## Backlog.md Workflow

This project uses Backlog.md for task and project management.

**At the beginning of each conversation in this project, run `backlog instructions overview` before answering or taking action. Re-read it only if you have not read it yet in the current conversation.**

Use the overview to decide whether to search, read, create, or update Backlog tasks.

Before task lifecycle actions, read the matching detailed guide:
- `backlog instructions task-creation` before creating or splitting tasks
- `backlog instructions task-execution` before planning, changing status or assignee, adding a plan or implementation notes, or implementing task work
- `backlog instructions task-finalization` before checking acceptance criteria, writing final summaries, or moving tasks to terminal statuses

Use `backlog <command> --help` before running unfamiliar commands. Help shows options, fields, and examples.

Do not edit Backlog task, draft, document, decision, or milestone markdown files directly. Use the `backlog` CLI so metadata, relationships, and history stay consistent.

## No safety nets

There is no self-invented insurance logic in this project unless Ivan explicitly asked for it. No caps (page, item or row ceilings), no silent fallbacks, no defensive narrowing, no guards added to protect production from a requested change. The only stop conditions are the source's own end signal or a budget recorded as `truncated`, never as success. Failures fail loudly and get recorded. If a limit or guard seems necessary, ask first. Background: every crawl ceiling found on 2026-09-09 had been invented by an AI session as a default and silently truncated boards.

</CRITICAL_INSTRUCTION>
