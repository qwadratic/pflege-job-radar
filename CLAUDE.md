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

## One Playwright

This VM has one Playwright: the Python package in `/home/exedev/repo/.venv` (`requirements.txt`), with its browsers in `~/.cache/ms-playwright`. Run browser work (tests, screenshots, `rag-check`, probes) with `.venv/bin/python`. Do not add a second copy: no venv or `pip install` of Playwright elsewhere (job tmp dirs included), no `npx playwright`, `npm i playwright` or `@playwright/test`, no `playwright install` from any other copy. Another version brings its own browser revision (about 650 MB) into the shared cache, and the disk is 25 GB. If a task needs a newer Playwright, bump it in `requirements.txt`, reinstall in `.venv` once with `.venv/bin/python -m playwright install chromium`, and delete the old revision. Background: on 2026-10-05 a session installed Playwright 1.63.0 in its own venv and left a second 650 MB browser set next to the repo's.

## Backlog task ids

New task ids are born only on `origin/main`, never on a feature branch. Two branches that each create a task get the same next id from the backlog CLI; the file names differ (title slug), so git merges both without a conflict and the backlog holds two tasks under one id. The numbering session `pflege-clawl` creates every new task on `origin/main` and answers with the id. Any other session (the WhatsApp and email lane on the fork, `pflege-fe`, a fresh session) sends it title, description, labels and priority and waits for the id. On a feature branch only edit existing tasks (notes, status, acceptance criteria); do not run `backlog task create`, and do not run `backlog doctor --fix` on duplicate ids (it renumbers silently). If `pflege-clawl` is unreachable for more than 2 hours, ask Ivan. `tests/test_backlog_ids.py` fails when two task files share an id or an `id:` line does not match its file name. Background: on 2026-10-05 the WhatsApp fork (PR #1) held 14 ids that named a different task than main.

## Closing tasks

The session that does a task closes it. When it considers the work finished and no re-check is planned, it follows `backlog instructions task-finalization` in the same change set: evidence per acceptance criterion, final summary, status Done. If a re-check is planned, the notes say who re-checks and what, and the task stays open until then. Do not leave a finished task `In Progress` for someone else to close.
