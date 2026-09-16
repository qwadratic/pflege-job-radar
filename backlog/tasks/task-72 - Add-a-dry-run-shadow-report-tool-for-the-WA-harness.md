---
id: TASK-72
title: Add a dry-run shadow report tool for the WA harness
status: Done
assignee: []
created_date: '2026-09-13 09:40'
updated_date: '2026-09-13 10:09'
labels: []
dependencies: []
ordinal: 72000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Matching the real production teams own wa_shadow_run.py pattern (report what the agent would do next, without sending, always against a DB copy) -- Ivan wants the same safety-net tooling for our harness before any real traffic, and using it is how the last comparison round happened.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New app/wa/luna/shadow_run.py (or similar): for threads owed a reply, runs the real turn() logic against a COPY of the wa sqlite file, reports the proposed action/bubbles/gate without calling Meta send
- [x] #2 Report includes the 24h-window gate state (TASK above) so a closed window shows up as a gate reason, not a silent skip
- [x] #3 Never mutates the real wa.sqlite; always operates on a temp copy, same guarantee as the real teams tool
- [x] #4 Unit tests cover the report shape against a fixture set of threads (open window / closed window / no reply owed)
- [x] #5 Full offline suite stays green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/luna/shadow_run.py: db_copy() opens the live wa sqlite strictly read-only (SQLite URI mode=ro -- refuses to write, and refuses to even create the file if missing, unlike a plain connect()), then uses sqlite3's own online-backup API to copy it into a fresh in-memory database -- safe against a concurrent writer, unlike a raw file copy that could race a WAL checkpoint. Everything else (shadow_turn, phones_owed_a_reply) only ever touches that in-memory copy.

phones_owed_a_reply(conn): one query (self-join on max(id) per phone) for every thread whose last message is inbound -- reporting.ball_for() == 'us' -- rather than N per-phone queries.

shadow_turn(conn, phone, client=None): re-runs the exact same brain (deterministic or luna, whichever C.BRAIN selects) against the thread's own last inbound message, and app.wa.api._freeform_window_open() for the TASK-70 gate, reporting one of freeform/reopen_template/reopen_template_missing/no_send/stopped -- never calling app.wa.meta.Client, never writing the result anywhere.

Real safety issue found and fixed before it could bite: WA_BRAIN=luna threads carry a real, resumable Claude Code session id on the card (_session_id). A dry run that resumed it with --resume would durably append the shadow turn to the SAME shared external session the live webhook resumes from next time -- an irreversible side effect on production state that a report-only tool must never risk. shadow_turn() now always strips _session_id before calling the brain, so a luna reply is always generated from a fresh, throwaway session (documented tradeoff: the card/slots state that actually drives gates and matching is identical to production; only the model's in-session conversational memory is not replayed, so wording may read a little colder than the live reply would).

13 new tests, including one that asserts the stripped-session-id behavior directly (fake client records the session_id it was actually called with) and one that runs the full run() against a real seeded database and asserts it is byte-for-byte unchanged afterward. Offline suite: PFLEGE_TESTS_OFFLINE=1 pytest -q -m "not network and not completeness and not mutation and not llm" -> 1044 passed, same 5 pre-existing unrelated failures as before this task.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/wa/luna/shadow_run.py mirrors the real production team's wa_shadow_run.py safety contract: report what the configured brain would say next for every thread owed a reply, always against a read-only-sourced, in-memory backup of the live database, never calling Meta send and never writing anything back -- verified by a test that runs it against a real seeded database and asserts the database is unchanged afterward. Also closes a real safety gap found while building it: a luna dry run must never resume the live thread's actual Claude Code session (an irreversible write to shared external state) -- shadow_turn() strips _session_id before calling the brain, verified by a dedicated test. 13 new tests. Offline suite: 1044 passed, 5 pre-existing unrelated failures.
<!-- SECTION:FINAL_SUMMARY:END -->
