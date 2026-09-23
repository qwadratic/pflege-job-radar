---
id: TASK-239
title: >-
  A thread whose Claude Code session transcript has aged out can never be
  answered again: --resume fails, and nothing ever clears _session_id
status: In Progress
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-23 09:25'
labels:
  - rail-critique
  - loses-messages
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 186000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: app/wa/luna_brain.py:851. Severity: loses-messages. 

HOW IT HAPPENS: A campaign re-engages leads months old, or an ordinary candidate goes quiet for weeks. They reply; --resume finds no session; every attempt from then on fails identically.

WHAT IT COSTS: A returning candidate is never answered and the thread is permanently dead. The operator is not fully blind (a wa_send_failures row and stuck_reply on GET /wa/threads), but the signal reads as a CLI error rather than "this thread lost its memory", which points the reader at the wrong thing.

PROPOSED DIRECTION (not a decision): Treat a resume failure as recoverable: on the specific "no such session" failure, clear _session_id and run the turn once as first contact. The card, the scoreboard and the market snapshot already carry everything the gates need; only conversational wording memory is lost, which the FUNNEL CONTINUITY rule already covers. Pinning the CLI's cleanup period is a separate, smaller decision.

VERIFICATION NOTES: CONFIRMED. luna_brain.py:851 uses --resume for any non-None session id; card["_session_id"] is written at 1203/1273 and grepping app/ shows the only code that removes it is shadow_run's in-memory strip (shadow_run.py:111) and purge_test_history for test threads. There is no "no such session" recovery anywhere: a non-zero CLI exit raises at luna_brain.py:872, api.process_owed_turn marks skipped_error (api.py:927-929), nothing is sent, the pending row survives, and catch-up re-drives the same inbound every 3 minutes into the identical failure, forever, because the card is never saved with a cleared id. LUNA_MAX_CALLS_PER_HOUR=20 (config.py:180) only caps the burn. The one thing I cannot verify from inside this repo is the CLI's own transcript cleanup period (config.py:172 knows where the store is, nothing pins the period) — but transcript loss does not need a 30-day default: a disk cleanup, a CLAUDE_CONFIG_DIR change or a host move does it too, and the code has no path back from any of them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Sceptic review verdict: fix (not skip) -- their reasoning held up on independent re-check, no edits made in that pass. Implemented here.

Fix, app/wa/luna_brain.py:
- New `SessionNotFound(RuntimeError)` (carries the stale id), raised by `Client._live_reply` only when a --resume attempt (`not fresh`) exits non-zero AND stderr matches the CLI's own signature -- verified against the live CLI (2.1.270) in this sandbox: `claude -p --resume <bogus-uuid> ...` prints exactly `No conversation found with session ID: <id>` to stderr, exit 1. Every other non-zero exit (auth, network, a real bug, or the same text on a *fresh* --session-id attempt) still raises the plain RuntimeError unchanged.
- `turn()` now wraps the `cl.reply(...)` call: on `SessionNotFound`, clears `card["_session_id"]`, rebuilds the user payload (so `fresh_session` reads true) and retries `cl.reply(..., None)` once. A second failure raises normally -- no unbounded retry loop. This is the same one-shot corrective-retry shape `_checked_reply` already uses elsewhere in this file.

Test added, tests/test_wa_luna_brain.py:
- test_live_reply_raises_session_not_found_only_on_a_resumes_own_signature -- the narrow-catch discipline point: SessionNotFound only on (not fresh + matching stderr); a fresh attempt with the same text, or a --resume failing for any other reason, both still raise plain RuntimeError.
- test_turn_recovers_from_a_stale_session_id_by_retrying_once_as_a_fresh_contact -- LB.turn() with a card carrying a stale _session_id, mocked subprocess.run failing on --resume then succeeding on --session-id; asserts no raise, a reply goes out, exactly 2 subprocess calls (no loop), and slots._session_id is the NEW id, not the stale one. Fails on pre-fix code (bare RuntimeError propagates, card never updated) and passes after the fix -- satisfies AC#2.

Ran narrowly: .venv/bin/python -m pytest tests/test_wa_luna_brain.py -q -> 179 passed relevant to this change; the pre-existing 27 failures (test_shortlist_*, test_market_snapshot_matches_only_once_fully_ready_to_close) are unrelated -- confirmed identical on the pre-change file too (PostgREST 401, a live-network/Supabase-auth dependency, nothing to do with this diff).

Left at In Progress, AC unchecked, not committed, per the batch-verification workflow -- the owner reviews the diff and does one full-suite pass at the end.
<!-- SECTION:NOTES:END -->
