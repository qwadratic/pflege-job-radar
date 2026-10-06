---
id: TASK-345.12.2
title: Daria answers questions and harder corrections with her own toolset
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-01 18:10'
updated_date: '2026-10-01 18:54'
labels:
  - email
dependencies: []
parent_task_id: TASK-345.12
priority: high
ordinal: 280000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-01: any question or harder correction from him or Valentyn that needs no code is answered by Daria, not with "operator needed". Toolset by his choice: sales_brain read-only, the board tools the WA harness gives Luna (app/wa/luna/tools_server.py, board tools only, nothing that touches the phone rail), the mailing state, and her own backlog project "daria" as a task pipeline. No server shell.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A question gets a Russian answer built from her tools; German wording she proposes is marked as a draft for Ivan
- [ ] #2 A correction she cannot do by mail becomes a task in backlog project daria, and the answer names the task
- [ ] #3 An answer may give a candidate's name, phone or email, because it goes only to Ivan and Valentyn; a pipeline task names a candidate by number only
- [ ] #4 The answering run has no Bash and no file write outside her backlog project, verified by a test that inspects the CLI arguments
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. tools/daria_tools.py: stdio MCP server daria with brain_tables, brain_query (URI mode=ro, query_only, ATTACH denied, 150 KB result budget reported as truncated), mailing_state, read_doc, pipeline_list/view/create (backlog CLI, project daria only).
2. Desk answerer: claude -p as the claude user with --tools "" (no built-in tools), --strict-mcp-config, two servers (jobs = app.wa.luna.tools_server without WA env, daria), --allowedTools = board read tools + daria tools, --disallowedTools = phone-rail/WA-data tools; MCP_TIMEOUT for the cold board build; a missing board readiness stamp fails the answer loudly.
3. System prompt: Russian, terse, sources named, candidates by number, German as draft, corrections into the pipeline with the task id.
4. Failed answers are mailed to the operators as failures.
5. Tests of the CLI args, brain read-only and the pipeline project gate; live smoke test with ask.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-01: implemented. Live smoke tests via 'python3 tools/daria_desk.py ask' (no mail sent): answer 1 used mailing_state + 12 brain_query calls + count_postings and named sources; answer 2 listed the Nürnberg Intensiv clinics from the board and created TASK-402 in project daria (painless). Fixes after the smoke tests: feminine self-reference, one more lookup instead of 'I did not check', English task text, no promises of when a pipeline task is done, phone-rail tools moved to --disallowedTools (the board server registers them; the model no longer sees them). Daria cannot read Valentyn's mailbox; TASK-402 needs a source she has or Ivan.

2026-10-01, Ivan: "она может писать нам PII". The answer prompt now lets an answer name a candidate (name, phone, email, address) when the question needs it; pipeline tasks stay by number, because the backlog is in git. Answers are logged in data/email-analysis/desk/desk.jsonl (gitignored, server only) and sit in daria's Sent Items.
<!-- SECTION:NOTES:END -->
