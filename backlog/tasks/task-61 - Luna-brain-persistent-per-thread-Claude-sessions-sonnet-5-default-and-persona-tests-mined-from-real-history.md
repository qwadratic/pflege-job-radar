---
id: TASK-61
title: >-
  Luna brain: persistent per-thread Claude sessions, sonnet-5 default, and
  persona tests mined from real history
status: Done
assignee:
  - '@claude'
created_date: '2026-09-12 13:31'
updated_date: '2026-09-12 13:31'
labels:
  - whatsapp
  - luna
  - claude
dependencies: []
references:
  - TASK-60
  - app/wa/luna/VENDORED.md
ordinal: 61000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Follow-up to TASK-60. Three changes: (1) the luna brain now keeps one resumed Claude Code session per WhatsApp thread (--session-id on first contact, --resume on every later turn, from a pinned working directory) instead of replaying the whole conversation as a JSON blob on every stateless call -- only per-turn ground truth (card, market snapshot, requirement scoreboard) is resent, the conversation itself lives in the session. (2) default model changed from Opus to Sonnet 5 (cost/latency for a chat-shaped workload), with Haiku noted as a cheaper option. (3) a real read of two months of the reference implementation's WhatsApp history (anonymized, aggregated into archetype groups -- no real identifiers kept) produced six synthetic personas, scripted as tests that actually call the claude CLI (tests/test_wa_luna_personas.py, marked llm). That real-CLI run found two genuine bugs a fake-reply test could not have: a salary figure invented with no rule against it, and a crash when the model returned empty bubbles without also setting no_send. Both fixed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each WhatsApp thread resumes the same Claude Code session across turns (verified against the real CLI: qualification stated in turn 1 is correctly recalled in turn 3 without being resent)
- [x] #2 The default model is claude-sonnet-5; claude-haiku-4-5 and claude-opus-5 remain available via WA_LUNA_MODEL
- [x] #3 tests/test_wa_luna_personas.py exercises six synthetic personas (and cross-cutting patterns: salary deferral, family headcount not re-asked, graceful withdrawal, disqualified decline, duplicate reopen) against the real claude CLI, marked llm and excluded from the default test run
- [x] #4 The salary-invention and empty-bubbles-crash bugs found by that run are fixed, each with an offline regression test
- [x] #5 --tools "" disables every built-in CLI tool (--restricted alone left file-reading tools available, which produced a stray tool-use narration ahead of the JSON reply once); parsing tolerates that shape as a fallback without silently accepting genuinely malformed output
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Research CLI flags: --session-id/--resume for session persistence (verified: turn 2 in the same session recalls turn 1's stated name without it being resent; same cwd required for resume to find the session), --mcp-config/--strict-mcp-config/--allowedTools for custom tools (not wired in, answered as a question -- no specific tool was requested).
2. Redesign app/wa/luna_brain.py: Client.reply(system, user, session_id) -> (out, next_session_id); _live_reply starts fresh with --session-id <generated uuid> when session_id is None, else --resume <session_id>, always from a pinned cwd (config.LUNA_SESSION_DIR) since resume is cwd-scoped. Drop the thread-history serialization from the user payload -- the resumed session already has it.
3. config.py: WA_LUNA_MODEL default claude-opus-5 -> claude-sonnet-5. Add LUNA_SESSION_DIR.
4. Mine two months of real WhatsApp history for candidates with a CV on file and a resolved qualification verdict (subagent, strict anonymization rules): classify into 6 archetype groups, get one synthetic (fictional) persona + short script per group plus cross-cutting patterns, with zero real names/phones/quotes in the output.
5. tests/test_wa_luna_personas.py: script the six personas plus cross-cutting-pattern tests, marked llm, run against the real CLI.
6. Fix what that run found: no salary-figure rule (added), and a crash when bubbles=[] without no_send=true (empty array now authoritative on its own) -- both with offline regression tests in test_wa_luna_brain.py.
7. Harden the CLI call: --tools "" (--restricted alone leaves file-reading tools enabled), and a fallback JSON-extraction parse for stray prose around the reply.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified live: 3-turn session-resume conversation correctly recalled a stated qualification without resending it. Full persona suite (8 tests, tests/test_wa_luna_personas.py) passed against the real CLI after fixing both bugs it found. Offline suite: 867 passed / 122 skipped, same 6 pre-existing failures as before this task (unrelated Supabase-network dependencies and 2 tests that predate this work).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/wa/luna_brain.py now keeps one resumed Claude Code session per WhatsApp thread instead of replaying the whole conversation every call; default model is now Sonnet 5; --tools "" plus a fallback JSON-extraction parse harden the CLI call; six anonymized-history-derived synthetic personas in tests/test_wa_luna_personas.py (marked llm) exercise the real CLI and already found and drove the fix of two real bugs (an invented salary figure, a crash on bubbles=[] without no_send).
<!-- SECTION:FINAL_SUMMARY:END -->
