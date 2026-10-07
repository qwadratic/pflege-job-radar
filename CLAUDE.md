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
3. The mirror is local and git-ignored: `data/mirror/` of the main checkout, one directory shared by every worktree. It holds third-party pages with HR names, e-mails and phone numbers and the repo is public, so mirror content is never committed; a committed test may quote only the redacted slices the `tests/fixtures/board_samples/README.md` allows. After `tools/mirror.py record|add` run `tools/mirror.py push` (private Bunny zone, `docs/deploy.md` "Mirror on Bunny"); CI runs `tools/mirror.py pull`. A test or module that reads a real board carries `pytest.mark.mirror`; `pytest -m "not mirror"` needs no pulled mirror, and a board-reading test without the marker fails there with a MirrorMiss, which is the reminder to add it.

A request the mirror does not hold is a failure that names the board, the URL and the command to record it; nothing falls back to the live site. `tests/conftest.py` refuses every non-local socket and DNS lookup in a test, our own infrastructure (Supabase, the ingest endpoint, Firecrawl, LLM APIs) included: fake those. `MIRROR_RECORD=1 pytest <file>` lifts the guard and records exactly what the mirror lacks, by a person, on purpose (the Google Fonts our own pages load are a mirror board too, `infra__web-fonts`, kept with the registry-proxy snapshot in the committed `tests/fixtures/mirror_infra/` with its headers cut: a web test that meets a font the snapshot lacks fails, `MIRROR_RECORD=1 pytest tests/test_web_<page>.py` records it, and the new file goes into the commit). A mirror's age is information (`status`), never a reason for an automatic refresh. A completeness check that was already red when its board was recorded is an explicit, named xfail (`status` flags it `GAP:<check>`, `pytest --runxfail` shows the finding); the same check going red where it was green fails. `record` replaces a board's whole file: the pages a test recorded under its own scope (`tests/test_verify_pi_loga_live.py`, `test_completeness_dvinci.py`, `test_completeness_helix.py`) go with the old file, so run `MIRROR_RECORD=1 pytest <that file>` once more after re-recording such a board. A recording that ends with the adapter's own error (`ADAPTER-ERROR` in the recorder's line and in `status`) is a finding about the adapter, not a mirror to trust: its checks pass because there is nothing to compare.

</CRITICAL_INSTRUCTION>

## One Playwright

This VM has one Playwright: the Python package in `/home/exedev/repo/.venv` (`requirements.txt`), with its browsers in `~/.cache/ms-playwright`. Run browser work (tests, screenshots, `rag-check`, probes) with `.venv/bin/python`. Do not add a second copy: no venv or `pip install` of Playwright elsewhere (job tmp dirs included), no `npx playwright`, `npm i playwright` or `@playwright/test`, no `playwright install` from any other copy. Another version brings its own browser revision (about 650 MB) into the shared cache, and the disk is 25 GB. If a task needs a newer Playwright, bump it in `requirements.txt`, reinstall in `.venv` once with `.venv/bin/python -m playwright install chromium`, and delete the old revision. Background: on 2026-10-05 a session installed Playwright 1.63.0 in its own venv and left a second 650 MB browser set next to the repo's.

## Backlog task ids

New task ids are born only on `origin/main`, never on a feature branch. Two branches that each create a task get the same next id from the backlog CLI; the file names differ (title slug), so git merges both without a conflict and the backlog holds two tasks under one id. The numbering session `pflege-clawl` creates every new task on `origin/main` and answers with the id. Any other session (the WhatsApp and email lane, `pflege-fe`, a fresh session) sends it title, description, labels and priority and waits for the id. On a feature branch only edit existing tasks (notes, status, acceptance criteria); do not run `backlog task create`, and do not run `backlog doctor --fix` on duplicate ids (it renumbers silently). If `pflege-clawl` is unreachable for more than 2 hours, ask Ivan. `tests/test_backlog_ids.py` fails when two task files share an id or an `id:` line does not match its file name. Background: on 2026-10-05 the WhatsApp fork (PR #1) held 14 ids that named a different task than main. Every lane pushes its own branches to this repository (Ivan, 2026-10-06): no fork, no push to `main` by feature work, no force-push. The history of the retired fork branch holds personal data and a client name: never push it here; start a new branch from `origin/main`.

## Closing tasks

The session that does a task closes it. When it considers the work finished and no re-check is planned, it follows `backlog instructions task-finalization` in the same change set: evidence per acceptance criterion, final summary, status Done. If a re-check is planned, the notes say who re-checks and what, and the task stays open until then. Do not leave a finished task `In Progress` for someone else to close.

## Iterating on the WhatsApp brain

A change to what the Luna brain says or decides (prompt rules, tools, gates, card or context fields) is checked with the brain eval, `evals/wa_brain/` (Ivan, 2026-10-07). It runs by hand when a feature is built or changed: never in CI, never part of `pytest`. Tests only prove the harness itself runs.

- **Phase 1, old vs new (now):** a test point is a real old conversation as it was, the old bot's replies standing in for our own history, with the brain run at chosen turns (`--at-turns`). Several points per candidate, several runs per point. No answer is composed in advance: a judge model works out the best reply for the situation, scores the old reply and the unlabelled new runs side by side, then comments across all points.
- **Phase 2, features (once our bot runs live):** histories hold our own bot's replies, and each new feature gets its own points.
- **The eval collects communication patterns**, so region does not matter: the set keeps Bavarian conversations plus one other-region point; anything else is re-set on the Bavarian fixture.
- **Nothing is sent.** The harness runs on the replay isolation (scratch DB, no rail, sales brain read-only). Case files, results and scratch state hold real conversations and live outside every checkout; the tool refuses paths inside one.
- **Every report states** the git sha, runs per point, failed runs, and the weekly budget before and after.
- **Candidate-facing fixed wording is never composed from a judge's complaint.** A finding about a locked German text goes to Ivan for his verbatim wording.
