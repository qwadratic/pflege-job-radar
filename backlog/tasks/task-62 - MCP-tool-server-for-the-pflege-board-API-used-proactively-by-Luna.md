---
id: TASK-62
title: 'MCP tool server for the pflege-board API, used proactively by Luna'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-12 16:00'
updated_date: '2026-09-12 16:32'
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
- [x] #1 New stdio MCP server (app/wa/luna/tools_server.py) exposes search_postings, list_clinics, get_posting, get_clinic_contact wrapping D.filter_jobs/D.filter_clinics with the same param shape as app/wa/slots.py:filters()
- [x] #2 luna_brain.py Client._live_reply wires the server via --mcp-config/--strict-mcp-config/--allowedTools scoped to exactly those four tools, replacing the blanket --tools ""
- [x] #3 prompts.py gains a TOOLS rule instructing proactive tool use with no code-level retry-without-tools fallback; market_snapshot/requirement_scoreboard remain in every turn's payload regardless of tool availability
- [x] #4 app/wa/config.py:LUNA_EFFORT default is bumped from medium to the CLI's highest supported reasoning-effort tier for the luna brain
- [x] #5 tools_server.py logs every tool invocation (name + args) to a file in the session dir
- [x] #6 Offline unit tests cover the four tool functions directly; an llm-marked test proves a persona message about an unlisted city actually triggers a logged search_postings call before the reply, and a second case proves a question the snapshot already answers does not trigger a needless call
- [x] #7 Full offline suite (pytest -q -m "not network and not completeness and not mutation and not llm") stays green
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented app/wa/luna/tools_server.py (stdio MCP server, 4 tools: search_postings/get_posting/list_clinics/get_clinic_contact wrapping D.filter_jobs/D.filter_clinics; get_clinic_contact delegates to TASK-64's contacts module when present, else null). Wired into luna_brain.py Client._live_reply via --mcp-config/--strict-mcp-config/--allowedTools (tool names confirmed live as mcp__pflege_board__<tool>, not documented anywhere formal). LUNA_EFFORT default bumped medium->high. prompts.py gained a mandatory TOOLS rule (no retry-on-error fallback per plan, and per Ivan's steer: proactive framing, not emergency-only).

Two real bugs found and fixed via live debugging (not just unit tests):
1. Model wrote 'search_postings' as its action value and set no_send=true instead of actually invoking the tool -- it read 'return ONLY a JSON object' as forbidding any intermediate step. Fixed by making OUTPUT_INSTRUCTION explicit that the JSON-only rule governs the FINAL text after any tool calls, not tool use itself.
2. --mcp-config's per-server 'cwd' field is NOT honored by this CLI version for stdio servers -- the spawned server inherited the outer process's cwd (C.LUNA_SESSION_DIR) and failed with ModuleNotFoundError: No module named 'app'. Fixed via env.PYTHONPATH in the generated config. Same subprocess-isolation issue meant a test's monkeypatch on config.SQLITE_PATH/LUNA_SESSION_DIR never reached the server's own fresh import -- fixed by passing WA_SQLITE_PATH/WA_LUNA_SESSION_DIR through as env vars too, read by tools_server.py at call time / import time.

Added mcp>=1.0 to requirements.txt (mcp 2.x API: mcp.server.mcpserver.MCPServer, not the v1 FastMCP import path).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Wired app/wa/luna/tools_server.py (4-tool stdio MCP server over the existing D.filter_jobs/filter_clinics) into luna_brain.py's Client via --mcp-config/--strict-mcp-config/--allowedTools, bumped WA_LUNA_EFFORT medium->high, and added a mandatory (not fallback-only) TOOLS rule to prompts.py. Verified via live debugging (not just mocks): found and fixed a model behavior bug (declaring a tool name as its 'action' instead of calling it, from an ambiguous JSON-only instruction) and a real CLI/MCP integration bug (--mcp-config's per-server cwd is not honored by this CLI version, worked around with env.PYTHONPATH + explicit WA_SQLITE_PATH/WA_LUNA_SESSION_DIR passthrough for subprocess-isolated config). Offline suite: 912 passed, same 6 pre-existing unrelated failures. llm suite: all 10 tests in tests/test_wa_luna_personas.py pass, including two new ones proving proactive tool use (a named unlisted city triggers a real search_postings call; a question the snapshot already answers does not).
<!-- SECTION:FINAL_SUMMARY:END -->
