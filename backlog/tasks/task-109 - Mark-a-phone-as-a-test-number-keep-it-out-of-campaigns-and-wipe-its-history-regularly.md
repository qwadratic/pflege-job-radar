---
id: TASK-109
title: >-
  Mark a phone as a test number: keep it out of campaigns and wipe its history
  regularly
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 14:40'
updated_date: '2026-09-16 21:38'
labels: []
dependencies: []
type: feature
ordinal: 109000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-16 for his own number (ends 8778, used for manual end-to-end tests): mark it as a test thread and delete its message history regularly so every manual test starts fresh. Today the only way is manual SQL (TASK-90 did exactly that by hand), test threads are counted in reports like real candidates, and a stale card (consent, documents, campaign context) makes the next test start from the wrong state.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 a thread can be marked as a test number and unmarked through a small CLI, the flag lives in the database (survives restarts) and is visible in GET /api/wa/threads
- [x] #2 test threads are skipped by the campaign sender (plan and send) and are excluded from candidate reports/stats; they keep answering normally so a manual test still works end to end
- [x] #3 a periodic job wipes a test threads history: messages, luna calls, follow-up and claim records, stored documents with their files, the persisted model session and the card, leaving the thread row marked test and not stopped; the wipe is reported and never touches a non-test thread
- [x] #4 the job runs on its own systemd timer (template in deploy/, documented in docs/whatsapp.md and the runbook) with the schedule and retention as arguments, and a dry-run mode shows what it would delete
- [x] #5 offline tests cover marking/unmarking, the wipe (including files and session dir), the dry-run, campaign and report exclusion, and that a non-test thread is never touched
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Flag in the database (app/wa/store.py): wa_threads.is_test (integer not null default 0) + test_marked_at, in SCHEMA and in MIGRATIONS (idempotent alter table, like the TASK-102 columns). thread()/threads() read is_test as a bool; _update_thread never writes it (a card save can never flip the flag). Helpers: mark_test_thread(c, phone, is_test) (creates the thread row when missing), is_test_thread(c, phone), test_phones(c).
2. CLI app/wa/luna/test_threads.py: --mark PHONE / --unmark PHONE / --list, phones canonicalized (meta.canonicalize_phone + MIN_PHONE_DIGITS as in migrate_candidates), prints the row it wrote. The operator runs it; nothing in this repo marks a number by itself.
3. Visible: GET /api/wa/threads rows carry is_test (ST.threads), ?phone= carries it on the thread, and the list response gets test_threads = how many of the returned rows are test numbers.
4. Campaign exclusion (app/wa/luna/campaign.py): phone_state's thread view carries is_test; decide() returns skip_test_number first, before every other action, so plan, --send and every --retry-* path skip it with the reason naming when it was marked.
5. Report/stat exclusion: reporting.report_row carries test; shadow_run.phones_owed_a_reply drops test threads (an explicit --phones still reports them); queue.queue_rows/mailing_list_rows (GET /api/wa/queue, the human handoff list) drop phones whose thread is marked test. The reply path (webhook worker, catchup, followups, the queue build itself) is untouched: a test thread answers exactly like a real one. export_known_phones is not affected (it exports the operator's own external database, not our threads).
6. Wipe app/wa/luna/purge_test_history.py: dry run by default, --apply writes, --older-than-hours N (0 = full wipe; N > 0 keeps a test thread whose last activity is younger than N hours untouched, so a card never survives the messages it was built from). Per test thread: wa_messages, wa_imported_messages, wa_message_statuses, wa_webhook_events, wa_inbound_pending, wa_reply_turn_claims, wa_nudge_claims, wa_luna_calls, wa_send_failures, wa_followups_sent, wa_campaign_sends, wa_queue_candidates/wa_queue_matches, wa_documents rows + their files under DOCUMENTS_DIR (only paths from that phone's own rows, only inside the documents tree, then the phone directory when it is empty), the claude session transcript of card._session_id, and the card/slots reset (thread row kept, is_test kept, stopped cleared). wa_ownership is deliberately kept (deleting it would route the next test message to the old system). Skips a phone with a claim in flight. Report per phone: counts per table, files, session file, skipped reasons.
7. deploy/pflege-wa-purge-test.service + .timer (daily 03:00 Europe/Berlin, Persistent, ExecStart with --older-than-hours and --apply as arguments), not installed by this repo; documented in docs/whatsapp.md (new Test numbers section) and docs/rollout-runbook.md.
8. tests/test_wa_test_threads.py: marking/unmarking through the CLI + the flag in GET /api/wa/threads; campaign plan and --send skip; reporting/shadow_run/queue exclusion; a normal inbound turn on a test thread still answers; the wipe with documents, files, session transcript, campaign rows and card; dry run writes nothing (db bytes + files unchanged); a non-test thread whose documents live in the same tree is never touched; a second run is idempotent; retention keeps a fresh thread.

Review fixes 2026-09-16 (confirmed findings, applied by the fixer):
9. shadow_run.phones_owed_a_reply is a driver query, not a report: it gets include_test (default False for the report) and catchup.run passes include_test=True, so the 3-minute catch-up timer answers a marked test thread's owed message again (AC#2, docs/whatsapp.md 'Not excluded, deliberately: ... catch-up'). Test: catchup.run answers a test thread whose inbound has no wa_inbound_pending row.
10. purge_thread's in-flight check becomes atomic with its deletes: begin immediate, re-check ST.claim_in_flight inside that transaction, then files/rows/card, commit or rollback per phone (ST._lock is in-process only and the purge is its own systemd unit next to pflege-wa.service and the catch-up timer). Test: a claim taken by a second connection inside the window makes the wipe skip instead of half-wiping.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Ivan decided 2026-09-16: wipe test-thread history COMPLETELY (no retention window) once a day at night -- timer daily 03:00 Europe/Berlin, full wipe. His own number (ends 8778) is the first test thread; Claude marks it via the CLI after verification.

Implemented (offline suite green: 1600 passed, 126 skipped, 68 deselected).

FLAG (app/wa/store.py): wa_threads.is_test + test_marked_at, in SCHEMA and in MIGRATIONS (idempotent 'alter table add column', same mechanism as the TASK-102 columns). thread()/threads() read is_test as a bool through one _thread_row helper; _update_thread does not write the column, so no card save can flip it. New helpers mark_test_thread/is_test_thread/test_phones. Verified against the real schema: the live data/wa.sqlite (copied read-only into memory) takes the two columns, a second _migrate is a no-op, every existing row gets is_test=0. The live file already carried them by the time I checked -- the catch-up timer runs from this tree and migrated it itself, which is exactly the intended idempotent path; no row was written by me.

CLI (app/wa/luna/test_threads.py): --mark PHONE / --unmark PHONE / --list. Canonicalizes the number first (meta.canonicalize_phone, >=8 digits; anything else exits 2 and writes nothing), --mark opens the thread when the number never wrote. Nothing in the repo marks a number by itself; the operator runs it. Ivan's 8778 number is NOT marked yet (per the task brief the operator runs the CLI): '.venv/bin/python -m app.wa.luna.test_threads --mark the operator's test number'.

EXCLUSIONS. campaign.decide -> skip_test_number as the FIRST check (before already_sent/uncertain and every --retry-* path), so plan, --send and any retry skip it with a reason naming when it was marked; phone_state carries is_test/test_marked_at and the printed plan shows 'test number'. reporting.report_row carries test; shadow_run.phones_owed_a_reply leaves test threads out (one left join, --phones still reports them, the row carries test); queue.queue_rows/mailing_list_rows (GET /api/wa/queue and .../mailing-list) leave them out while the queue entry is still written, so the consent path stays exercised; GET /api/wa/threads shows is_test/test_marked_at per row plus test_threads (real candidates = total - test_threads). export_known_phones needs nothing: it exports the operator's external database, not our threads. The conversation is untouched by design -- webhook, catch-up, follow-ups, documents, voice notes all run as for a real lead.

WIPE (app/wa/luna/purge_test_history.py): dry run by default (reads a read-only in-memory copy via shadow_run.db_copy, so it cannot write a row), --apply writes, --older-than-hours N (0 = full wipe; N > 0 leaves a thread whose last activity is younger than N hours COMPLETELY alone -- a card must never outlive the messages it was built from), --phones, --json. Deletes per test thread: wa_messages, wa_imported_messages, wa_message_statuses, wa_webhook_events, wa_inbound_pending, wa_reply_turn_claims, wa_nudge_claims, wa_luna_calls, wa_send_failures, wa_followups_sent, wa_documents (+ the files under DOCUMENTS_DIR and the phone's own directory once empty), wa_campaign_sends, wa_queue_candidates/wa_queue_matches, the Claude Code session transcript of card._session_id, and resets the card (slots, asked, matches_sent_at, turns, last_inbound/outbound_at, stopped). Kept: the thread row (still is_test, not stopped) and wa_ownership -- deleting that would route the next test message back to the old system. Never touches a non-test thread: every delete is keyed by the phone, a phone named with --phones that is not marked is a reported problem and exit 1, a file is only unlinked when a wa_documents row of that phone names it AND the path is inside DOCUMENTS_DIR (a row pointing outside fails that phone loudly and deletes nothing of it). A phone with a turn in flight (claim_in_flight) is skipped for the next run. A card naming a session whose transcript is not under C.LUNA_SESSION_STORE is a reported problem (exit 1) -- the conversation would stay readable on disk; the rest of that wipe still runs. New config C.LUNA_SESSION_STORE (CLAUDE_CONFIG_DIR or ~/.claude, /projects); transcripts are found by globbing for the session uuid, not by recomputing the CLI's cwd-derived directory name.

DEPLOY: deploy/pflege-wa-purge-test.service + .timer (daily 03:00 Europe/Berlin, Persistent=true, systemd-analyze calendar checked; --older-than-hours 0 --apply in ExecStart -- schedule and retention live in the units, not in the code), not installed by this repo. docs/whatsapp.md: new 'Test numbers (TASK-109)' section (flag, CLI, exclusion table, deleted-vs-kept table, retention, dry run, deploy, tests) plus the Documents PII paragraph corrected ('no retention' now has this one exception). docs/rollout-runbook.md: new section 9 (mark the number, read a dry run, install the timer, the CLAUDE_CONFIG_DIR trap).

TESTS: tests/test_wa_test_threads.py, 16 offline tests -- mark/unmark and a card save not flipping the flag, marking a number that never wrote, a non-number refused, the flag and count on GET /api/wa/threads, campaign plan + send_one skip with both retry flags set and no attempt row claimed (the fake client raises if a template is posted), report/shadow/queue exclusion with the same thread still reported when asked by name, a test thread answered end to end through the webhook, the full wipe (every table, document file, its directory, session transcript, card, ownership kept, no orphan pending row, a Stopp cleared), the dry run changing nothing, a non-test thread with documents in the same tree untouched (and named explicitly: problem, exit 1), a missing transcript reported, an idempotent second wipe, retention, a turn in flight, the CLI summary and --json. Live evidence: both CLIs smoke-run against a throwaway database in .tmp (deleted afterwards), and the dry run + --list against the live database (read-only, md5 of data/wa.sqlite unchanged, 0 test numbers).

Follow-up: purge --phones is canonicalized with the same rule as the mark CLI (a value that is not a number exits 2, never a silent 'wiped nothing'), covered by a test. Final offline suite: 1600 passed, 126 skipped, 68 deselected (2:21).

Review fixes applied 2026-09-16 (fixer, confirmed findings):

CATCH-UP ANSWERS A TEST THREAD AGAIN (high). phones_owed_a_reply(conn, include_test=False) -- the REPORT leaves a test number out, the DRIVER does not: catchup.run passes include_test=True. The filter had been added to a query with two callers, and the 3-minute catch-up timer's owed pass (an inbound with no wa_inbound_pending row behind it -- a turn the webhook worker did not finish, exactly what catch-up exists for) then skipped a marked number forever, contradicting AC#2, the plan's step 5 and docs/whatsapp.md 'Not excluded, deliberately: ... catch-up'. shadow_run.run()/main() and the reporting row are unchanged (still no test numbers, still reported when asked by --phones). docs/whatsapp.md exclusion table row rewritten; catchup's own docstring names include_test.
New test tests/test_wa_test_threads.py:test_catch_up_answers_a_test_thread_whose_owed_message_has_no_pending_row -- two threads owed a reply, no pending rows, one marked: the report query returns only the real one, include_test=True returns both, CU.run(client=FakeMeta()) answers both. Mutation-checked: reverting catchup.py to the unfiltered call fails it.

WIPE IN-FLIGHT CHECK IS NOW ATOMIC (low). purge_thread runs its claim check and its deletes inside one 'begin immediate' transaction (_wipe_transaction, commits on wiped, rolls back otherwise and on any exception) -- ST._lock is in-process only and the purge is its own systemd unit next to pflege-wa.service and the catch-up timer, so a turn claimed after the old check could save the pre-wipe card back and leave the card-without-messages state the check exists to prevent. Ordering kept: files before rows (a crash in between leaves the rows naming already-gone files, so the next run finishes; the other order orphans them), directories and session transcripts after the commit. Dry run unchanged (no lock, read-only copy).
New test test_no_other_process_can_claim_a_turn_inside_the_wipes_own_window: a second connection (timeout=0, i.e. another process) tries ST.claim_reply_turn inside the window between the check and the first delete and gets 'database is locked'; the wipe completes. Mutation-checked: with _wipe_transaction reduced to a plain commit the claim succeeds and the test fails.
Offline suite: 1600 passed, 126 skipped, 68 deselected.

Final offline suite after the review fixes: 1607 passed, 126 skipped, 68 deselected (2:23) -- 7 tests more than before (4 new brain/queue housing tests, 2 new test-thread tests, 1 queue import-shape test).

Final verification 2026-09-16 (review + adversarial verify + fixer, 6 findings fixed): offline suite 1600 passed, all 10 acceptance criteria evidenced, purge e2e 29/29 checks. Two further defects found by the final verifier and fixed by Claude afterwards: the session transcript is now unlinked inside the wipe transaction (a crash after the commit used to leave the whole conversation on disk with nothing naming it), and webhook events stored with phone NULL whose raw payload carries the test number are wiped too (reported separately in the dry run). Both mutation-checked. Offline suite after the fixes: 1609 passed. Deployed: pflege-wa.service restarted 17:27 UTC, health webhook_ready/outbound_ready/luna_ready/stt_ready true. Live: Ivan's number the operator's test number marked as a test thread at 17:27 UTC via the CLI (thread opened 2026-09-13, 9 turns); dry run reports it would wipe wa_messages 26, reply-turn claims 9, nudge claims 2, luna calls 9, followups 2, queue rows 6, 1 session transcript, card reset, 0 document files. deploy/pflege-wa-purge-test.service|.timer installed on this host (User=claude, repo paths), enabled; next run 2026-09-17 01:00 UTC = 03:00 Europe/Berlin, --older-than-hours 0 --apply (Ivan: full nightly wipe).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A thread can be marked as a test number (database flag, CLI, visible in the threads API); test threads are skipped by the campaign sender and left out of reports and the consent queue while still being answered normally, and a nightly job wipes their history: messages, imported history, statuses, webhook events (including ones stored without a phone but carrying the number), claims, calls, failures, follow-ups, campaign rows, queue entries, stored documents with their files and directories, the model session transcript and the card. Dry-run is the default and a non-test thread is never touched. Verified by 20 offline tests, a 29-check end-to-end purge run, and live on this host: Ivan's number marked, dry run correct, timer installed for 03:00 Europe/Berlin.
<!-- SECTION:FINAL_SUMMARY:END -->
