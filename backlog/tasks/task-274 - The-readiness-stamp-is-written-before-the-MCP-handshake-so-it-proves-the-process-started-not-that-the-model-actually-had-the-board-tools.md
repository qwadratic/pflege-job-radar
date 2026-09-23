---
id: TASK-274
title: >-
  The readiness stamp is written before the MCP handshake, so it proves the
  process started, not that the model actually had the board tools
status: In Progress
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-23 14:04'
labels:
  - rail-critique
  - degraded
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 221000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: app/wa/luna/tools_server.py:1181. Severity: degraded. 

HOW IT HAPPENS: The tools server stamps, then mcp.run raises immediately or the CLI drops the connection; claude -p exits 0, the stamp exists, _live_reply is satisfied, and the turn is answered with no board tools available.

WHAT IT COSTS: The only guard against a board-less answer can pass in exactly the failure it was built for. Grounding catches most of what would follow, so the realistic outcome is a degraded/holding reply rather than an invented clinic — but the turn is silently indistinguishable from a good one.

PROPOSED DIRECTION (not a decision): Stamp on evidence the model actually reached the server — the first tool listing or the first tool call would both do. If neither is observable from inside the MCP server, say in the docstring that the stamp proves the process launched and nothing more, so the next reader does not lean on it harder than it holds.

VERIFICATION NOTES: CONFIRMED mechanically, DOWNGRADED on impact. serve() (tools_server.py:1178-1182) runs apply_board_vocabulary(), then _stamp_ready(), then mcp.run(transport="stdio") — the stamp is on disk before a byte of the handshake, and _live_reply (luna_brain.py:878-884) treats its existence as proof the turn had the tools, in an error message that says exactly that. So the guard can pass in its own failure mode: server stamps, then the stdio loop dies or the CLI drops it for missing the connect deadline, and the turn answers with no board under it. Two things reduce the severity below what the finder claims. First, the window between the stamp and the handshake is now small precisely because TASK-213 moved the expensive work out of the child. Second, there IS a backstop: grounding.check_reply derives its evidence from the tool-call log the tools server writes (tools_server._log_call:144-158, grounding TOOL_LOG_NAME:225), so a turn with no tool calls has no evidence and NO INVENTION blocks a named clinic or an unsupported figure — the board-less answer mostly turns into a correction or a holding reply rather than a confident lie. What is left is a guard that does not guard, on a rare path.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
VERDICT: fix, agreeing with the sceptic. Independently re-verified, not just re-read:

- Reachability confirmed by reading the code: serve() = _prime_board_snapshot(); apply_board_vocabulary();
  _stamp_ready(); mcp.run(transport="stdio") (old order). Both priming functions are local file
  reads/JSON parses, no network -- the window between the stamp and the actual stdio handshake is real,
  just small.
- Went one step further than the sceptic: wrote a throwaway in-memory MCP client/server script
  (mcp.shared.memory.create_client_server_memory_streams + a real ClientSession) against this module's
  own `mcp` object. Empirically confirmed two things the sceptic only argued from reading source: (1)
  session.initialize() completing does NOT by itself trigger MCPServer.list_tools -- so "handshake done"
  and "CLI has the schemas" are genuinely different moments; (2) overriding the `mcp.list_tools` instance
  attribute IS seen by the lowlevel Server's on_list_tools callback (it calls self.list_tools()
  dynamically), so the proposed hook point actually works, not just in theory.
- Backstop confirmed by reading grounding.py directly: calls_since/turn_evidence read tool_calls.jsonl,
  which a board-less turn never wrote to, so NO INVENTION (grounding.py:1548-1556) does block a
  confident fabricated clinic/figure. Caps damage, does not make the stamp true.
- Cost confirmed low and confined: grepped every reference to WA_LUNA_TOOLS_READY/_stamp_ready/mcp.run
  in app/ and tests/ -- only tools_server.py (the fix site) and two mcp.run-mocking tests in
  tests/test_wa_luna_tools.py touch this. luna_brain.py's ready_path.exists() check, the send path, the
  ledger and bridge/dispatcher are untouched, matching the sceptic's blast-radius claim exactly.

IMPLEMENTED (app/wa/luna/tools_server.py): serve() no longer calls _stamp_ready() unconditionally.
_arm_ready_stamp() now wraps mcp.list_tools so the stamp fires on the server's first real response to a
tools/list request (proof the CLI actually received the tool schemas), then delegates to the original.
Tied to listing, not the first tool call, per the task's own reasoning -- most turns legitimately call
zero tools, and a call-based trigger would trip _live_reply's RuntimeError on every one of those.
Updated _stamp_ready()'s docstring and the module's own top docstring to say precisely what the stamp
now proves.

TESTS (tests/test_wa_luna_tools.py): updated test_serving_stamps_the_readiness_file_the_parent_checks so
its mcp.run mock also calls TS.mcp.list_tools() (stands in for the CLI's real first move), keeping its
existing assertions on the real path. Added
test_a_server_the_cli_never_actually_lists_tools_from_leaves_no_readiness_stamp: mcp.run mocked to a
no-op that never lists tools; asserts the readiness file does not exist. Verified by hand that this new
test FAILS on the pre-fix code (git stash on just tools_server.py) and PASSES with the fix restored.

Ran only tests/test_wa_luna_tools.py (136 passed) per instructions -- did not run the full suite.

Not done: did not touch luna_brain.py, the send path, the ledger, or bridge/dispatcher -- none of them
needed to change. Left status at In Progress and acceptance criteria unchecked for the verification pass.
<!-- SECTION:NOTES:END -->
