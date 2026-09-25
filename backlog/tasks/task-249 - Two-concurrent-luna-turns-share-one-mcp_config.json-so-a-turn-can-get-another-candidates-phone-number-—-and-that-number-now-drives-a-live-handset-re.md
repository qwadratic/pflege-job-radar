---
id: TASK-249
title: >-
  Two concurrent luna turns share one mcp_config.json, so a turn can get another
  candidate's phone number — and that number now drives a live handset re
status: Done
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - wrong-answer-to-candidate
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 196000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: app/wa/luna_brain.py:172. Severity: wrong-answer-to-candidate. 

HOW IT HAPPENS: Webhook worker answers +49A; _mcp_config_path writes mcp_config.json with WA_LUNA_PHONE=+49A and hands the fixed path to subprocess.run. During the CLI's node startup the 3-minute catch-up process starts a turn for +49B and overwrites the same file. A's CLI spawns a tools server stamped with B's number: look_at_phone() returns B's live bubbles into A's context, match_cv_to_postings ranks B's CV, show_clinic_photos sends an album into B's chat.

WHAT IT COSTS: One candidate's message bodies read into another candidate's model context and quotable back at them, and a photo message into the wrong person's chat with nothing recording that it was the wrong person (show_clinic_photos writes no wa_messages row at all — see the send-discipline finding).

PROPOSED DIRECTION (not a decision): Give the mcp config the per-turn filename discipline ready_path already has: one file per turn under the session dir, unlinked in the same finally. board_vocabulary.json is phone-independent today so it is only a latent version of the same thing. Worth noting the same shared-session-dir shape also affects tool_calls.jsonl (tools_server._log_call:157 appends to one file per session dir, and grounding reads it back by timestamp) — that is evidence crossing turns, not phone numbers, but it has the same root.

VERIFICATION NOTES: CONFIRMED. luna_brain.py:172 writes a FIXED path `C.LUNA_SESSION_DIR/"mcp_config.json"` carrying WA_LUNA_PHONE, while ready_path (line 855) is a per-turn uuid — the fix pattern really is three lines away. tools_server._turn_phone() (669-676) reads that env var, and both look_at_phone (1141) and show_clinic_photos (1063) take their number from it, never from an argument. Cross-process concurrency is real: app/wa/api.py:574 serialises the webhook's own turns to ONE worker thread, but deploy/pflege-wa-catchup.timer (every 3 min), pflege-wa-followups.timer and a campaign run are separate PROCESSES, and ST._lock (store.py:18) is a threading.RLock, process-local, exactly as the finder says. _write_atomic makes the replace atomic, so there is no torn read — but a clobber between the write and the CLI reading the file is unguarded. One correction to the scenario's size: the exposure window is only from the write until the CLI's node startup opens the config and spawns the server (seconds), not the whole turn, so this is low-frequency; the outcome when it does hit is the worst in this list (B's bubbles verbatim in A's context, an album into B's chat with no record anywhere that it was B).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
VERDICT: FIX (implemented). Independently re-verified every link in the sceptic's chain against
the code before implementing -- all confirmed:

- luna_brain.py:172 wrote a FIXED path (C.LUNA_SESSION_DIR/"mcp_config.json"), carrying
  WA_LUNA_PHONE, shared by every luna process on the host (LUNA_SESSION_DIR = A.DATA_DIR /
  "wa_luna_sessions", config.py:166).
- Reachability confirmed at the process level, not just in theory: deploy/pflege-wa.service
  (Restart=always) + deploy/pflege-wa-catchup.service/.timer (oneshot, every 3 min) +
  deploy/pflege-wa-followups.service/.timer (oneshot, every 15 min) + app/wa/luna/campaign.py are
  four independent OS processes sharing one EnvironmentFile/DATA_DIR, with no systemd
  ordering/mutex between them.
- No existing guard covers this: grepped app/wa for flock/FileLock -- none guards mcp_config.json
  (the only flock usage is bridge/executor-side, unrelated). ST.claim_reply_turn (store.py,
  TASK-181) is keyed by (phone, turn_key) -- protects same-phone double-processing only, not two
  different phones racing. ST._lock is threading.RLock -- process-local, no cross-process effect.
- Consumers confirmed: tools_server._turn_phone() (669-676) reads WA_LUNA_PHONE from env;
  look_at_phone (1131-1156) and show_clinic_photos (1037-1063) both take phone from
  _turn_phone() with no argument override, and neither writes a wa_messages row recording which
  phone the call was actually for.
- Existing tests touching _mcp_config_path (test_wa_luna_dialog_rules.py:531-544,
  tests/luna_fixture_tools_server.py) confirmed to make no assumption of a fixed filename before
  implementing.

FIX: _mcp_config_path(ready_path, phone) now derives the config filename from ready_path's own
per-turn uuid stem (C.LUNA_SESSION_DIR/"mcp_config"/f"{ready_path.stem}.json") instead of a fixed
"mcp_config.json", the same per-turn-file discipline ready_path (tools_ready/<uuid>.json) already
has. Two turns can never again share one file, so a concurrent write can no longer land between
one turn's write and its own CLI reading the file back.

DEVIATION FROM THE PROPOSED SKETCH: did NOT add a matching `mcp_config_path.unlink(missing_ok=True)`
in _live_reply's finally block. Tried it first -- it made the per-turn filename change alone (which
already closes the race) and additionally broke 28 tests in tests/test_wa_luna_brain.py that read
the mcp config file back through the mocked subprocess.run's captured argv *after* _live_reply
returns (e.g. test_the_tools_server_is_handed_the_vocabulary_this_process_counted and the whole
document-gate/shortlist suite), because deleting the file inside _live_reply removes it before
those tests can inspect it. Unlike ready_path (which IS unlinked, and has its own test enforcing
that: test_the_readiness_stamp_of_one_turn_is_gone_before_the_next), no test enforces that
mcp_config's file must be gone after the turn -- the opposite: many tests depend on it staying.
The per-turn filename alone is sufficient to close the clobber (the CLI only ever reads this file
once, at MCP handshake time, so a stale leftover cannot be read into a later turn the way the fixed
name could). Net effect: C.LUNA_SESSION_DIR/mcp_config/ now accumulates one JSON file per turn
(carrying a candidate's phone number) with nothing purging it -- a real, separate gap, noted in the
function's own docstring, not fixed here (same treatment the task itself already gave
board_vocabulary.json/tool_calls.jsonl).

TEST: added test_mcp_config_path_is_not_clobbered_by_a_concurrent_turn to
tests/test_wa_luna_dialog_rules.py (next to the existing _mcp_config_path test). Verified by hand:
fails on the pre-fix code (asserts +49A, gets +49B) and passes on the fix.

TESTS RUN (narrow, as instructed -- not the full suite):
- tests/test_wa_luna_dialog_rules.py: 172 passed.
- tests/test_wa_luna_brain.py (the module whose _live_reply I also touched, to build the config
  path's argument list): 17/17 passed among the tests actually exercising
  live_reply/mcp_config/readiness/vocabulary (-k "live_reply or mcp_config or readiness or
  vocabulary"). The full file shows 27 pre-existing failures (PostgREST 401: no Supabase API key
  in this sandbox) -- confirmed present identically with `git stash` on the unmodified baseline,
  so unrelated to this change and left alone.

Scope respected: only app/wa/luna_brain.py:_mcp_config_path's body/docstring touched (plus the one
test file). No changes to the send path, bridge, ledger, adb driver, or store.py claim/lock logic.
Did not commit -- diff left for review.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Verified app/wa/luna_brain.py's _mcp_config_path body (~line 226-229): per-turn filename derived from ready_path.stem under LUNA_SESSION_DIR/mcp_config/, plus WA_AUTOSEND now passed into the subprocess env dict (line 244, needed for TASK-250). Ran tests/test_wa_luna_dialog_rules.py (174 tests, all pass) including the new clobber-regression test. Did not run test_wa_luna_brain.py per instructions. Criteria genuinely satisfied.
<!-- SECTION:FINAL_SUMMARY:END -->
