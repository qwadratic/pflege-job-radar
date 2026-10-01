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

## Tests never touch a live site

A test reads a local mirror of the clinic sites, never the site itself (Ivan, 2026-10-01, TASK-197). Three steps, in this order:

1. A new page shape, a new board, or a posting an adapter misreads: re-record it. `.venv/bin/python tools/mirror.py record <board_id | host | clinic_id>` (one page: `tools/mirror.py add <board_id> <url>`; what the site changed since: `tools/mirror.py diff <board_id>`; what is mirrored: `tools/mirror.py status`).
2. Write the red test on the mirror (`tests/adapter_harness.py`, `tests/mirror.py` `mirror_board(board_id)`), watch it fail, fix the adapter, watch it pass.
3. The mirror is local and git-ignored: `data/mirror/` of the main checkout, one directory shared by every worktree. It holds third-party pages with HR names, e-mails and phone numbers and the repo is public, so mirror content is never committed; a committed test may quote only the redacted slices the `tests/fixtures/board_samples/README.md` allows.

A request the mirror does not hold is a failure that names the board, the URL and the command to record it; nothing falls back to the live site. `tests/conftest.py` refuses every non-local socket and DNS lookup in a test, our own infrastructure (Supabase, the ingest endpoint, Firecrawl, LLM APIs) included: fake those. `MIRROR_RECORD=1 pytest <file>` lifts the guard and records exactly what the mirror lacks, by a person, on purpose. A mirror's age is information (`status`), never a reason for an automatic refresh.

</CRITICAL_INSTRUCTION>
