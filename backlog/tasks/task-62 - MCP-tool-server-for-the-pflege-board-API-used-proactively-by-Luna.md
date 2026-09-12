---
id: TASK-62
title: 'MCP tool server for the pflege-board API, used proactively by Luna'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-12 16:00'
updated_date: '2026-09-12 16:02'
labels: []
dependencies: []
ordinal: 62000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Luna (app/wa/luna_brain.py, WA_BRAIN=luna) only ever sees a pre-computed market_snapshot/requirement_scoreboard every turn -- it cannot query the board itself mid-conversation. Ivan asked for the pflege-board read API to be wrapped as real tools the model can call, used proactively whenever the conversation raises something the snapshot did not already cover (an unlisted city, a clinic contact question), not only as a last resort. Ivan also asked for a higher reasoning-effort tier since deciding when/which/how to call a tool is real planning work, and for the model to stay Sonnet 5. See /home/claude/.claude/plans/wise-enchanting-crayon.md section 1 for the full design (an earlier draft included an automatic retry-without-tools on any CLI RuntimeError; that was reviewed and rejected as an invented safety net indistinguishable from masking a real outage -- do not add it back without a distinct, verified MCP-failure signal).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 New stdio MCP server (app/wa/luna/tools_server.py) exposes search_postings, list_clinics, get_posting, get_clinic_contact wrapping D.filter_jobs/D.filter_clinics with the same param shape as app/wa/slots.py:filters()
- [ ] #2 luna_brain.py Client._live_reply wires the server via --mcp-config/--strict-mcp-config/--allowedTools scoped to exactly those four tools, replacing the blanket --tools ""
- [ ] #3 prompts.py gains a TOOLS rule instructing proactive tool use with no code-level retry-without-tools fallback; market_snapshot/requirement_scoreboard remain in every turn's payload regardless of tool availability
- [ ] #4 app/wa/config.py:LUNA_EFFORT default is bumped from medium to the CLI's highest supported reasoning-effort tier for the luna brain
- [ ] #5 tools_server.py logs every tool invocation (name + args) to a file in the session dir
- [ ] #6 Offline unit tests cover the four tool functions directly; an llm-marked test proves a persona message about an unlisted city actually triggers a logged search_postings call before the reply, and a second case proves a question the snapshot already answers does not trigger a needless call
- [ ] #7 Full offline suite (pytest -q -m "not network and not completeness and not mutation and not llm") stays green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New app/wa/luna/tools_server.py: stdio MCP server exposing search_postings/list_clinics/get_posting/get_clinic_contact wrapping D.filter_jobs/D.filter_clinics, mirroring app/wa/slots.py:filters() param shape. get_clinic_contact reads a clinic_contacts table if present, else returns null (TASK-64 populates it independently).
2. Instrument every tool call with an append-only JSONL log line (tool name, args, ts) into C.LUNA_SESSION_DIR so tests can assert real invocation.
3. Wire luna_brain.py Client._live_reply: generate an mcp-config JSON pointing at tools_server.py, pass --mcp-config/--strict-mcp-config/--allowedTools scoped to the 4 tools, drop the old --tools "" blanket-disable for this path.
4. app/wa/config.py: bump LUNA_EFFORT default from medium to the CLI's highest effort tier (verify accepted values first with a quick claude -p --help / --effort probe).
5. prompts.py: add a short TOOLS rule -- what each tool is for, call proactively when the conversation raises something market_snapshot/consult didn't cover, reason from the snapshot alone if a call errors, no retry machinery.
6. Offline tests for the 4 tool functions; llm-marked tests for proactive-call and no-needless-call cases reading the JSONL log.
7. Run offline suite, then the new llm tests, update docs/whatsapp.md, backlog notes/finalize.
<!-- SECTION:PLAN:END -->
