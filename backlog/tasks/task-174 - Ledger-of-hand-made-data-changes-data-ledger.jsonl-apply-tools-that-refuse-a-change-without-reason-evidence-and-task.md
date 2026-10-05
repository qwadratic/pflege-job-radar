---
id: TASK-174
title: >-
  Ledger of hand-made data changes: data/ledger.jsonl + apply tools that refuse
  a change without reason, evidence and task
status: Done
assignee: []
created_date: '2026-09-29 21:38'
updated_date: '2026-09-29 21:38'
labels:
  - db-quality
  - infra
dependencies: []
ordinal: 172000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-29: every modification must be tracked -- why, when, how -- so we always know how the DB differs from the primary sources (Krankenhausplan PDF, RHV) and can re-check the evidence; a wrong explanation must be traceable to its task so systemic errors are easier to fix.

Before this, hand-made DB writes were ad-hoc EdgeSink pushes from the shell or tools/apply_clinic_corrections.py, with a backup under backups/ (git-ignored, local only) and the reasoning scattered over backlog notes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 data/ledger.jsonl (tracked in git) holds one JSON line per changed field: at, table, id, field, old, new, reason, evidence[], task, by, backup
- [x] #2 tools/apply_clinic_corrections.py and new tools/apply_posting_changes.py (retire/reopen/relink/unlink) refuse any entry without a complete _why {reason, evidence, task}, --push requires --by, and they append ledger lines only for fields verified by read-back
- [x] #3 Tests + mutation tests; full non-network suite 0 failed
- [x] #4 This session's earlier hand writes (TASK-163, TASK-153, TASK-166, TASK-167) backfilled into the ledger from their backups
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented 2026-09-29 21:00-21:40 UTC (main session; edited in worktree .claude/worktrees/ledger because this background session may not edit the shared checkout directly, deployed with git apply /tmp/grpLedger/ledger.patch).
Files: tools/ledger.py (require_why, entries, append; LEDGER=data/ledger.jsonl), tools/apply_clinic_corrections.py (load_corrections now requires _why per clinic and rejects non-CLINIC_SPEC keys; --push needs --by; ledger lines after read-back; exit 1 when not all applied), tools/apply_posting_changes.py (new: retire -> verify op gone/expired, reopen -> verify op live/open, relink -> clinic_links manual, unlink -> clinic_links NULL, lock=true -> rule manual; backup, read-back, ledger), tests/test_ledger_tools.py (13 tests).
Mutation tests (restored from /tmp copies, diff -q identical): M1 evidence no longer required -> 2 failed; M2 no-op fields logged -> 1 failed; M3 relink without manual lock -> 1 failed; M4 reopen sends gone -> 1 failed. Full non-network suite in the worktree: 1589 passed, 18 skipped, 0 failed.
Backfill (122 lines, marked backfilled=true, times reconstructed from backups): TASK-163 5 duplicate retires (5819-5823) + 10 detaches locked manual (effective write 2026-09-28T06:58:01Z); TASK-153 5 re-locks (2026-09-28T07:06:05Z); TASK-166 25 unlinks (75 field lines, 2026-09-29T16:48:15Z); TASK-167 7 beds (2026-09-29T15:14:10Z).
First real use (Ivan approved 7 items 2026-09-29, 'да давай по всем да'): registry task169 (10 rows, 17 lines), task170 (12 rows, 21 lines; RH2843 ats_type no-op not logged), task171 (1 row, 1 line); postings: 9 retires + 1 relink (20 lines). All read back OK. Ledger total 181 lines. Matching CSV patches applied to data/registry/clinics.csv so a CSV re-push cannot revert them (until the CSV is removed, see the follow-up task).
Side finding: CSV vs DB already disagree on rows nobody touched today (e.g. 16291 TUM careers_url/ats_type blank in CSV, set in DB; 57707 and 66104 beds 0 in CSV, NULL in DB) -- evidence for the CSV-removal task.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added data/ledger.jsonl and made both hand-write tools (clinic corrections, new posting changes: retire/reopen/relink/unlink) refuse any change without reason, evidence and task, log who made it, and append one ledger line per field verified by read-back. Verified with 13 tests, 4 mutation tests and a 1589-passed/0-failed suite; backfilled 122 lines for this session's earlier writes; first real use applied Ivan's 7 approved changes (23 registry rows, 10 postings, 59 lines).
<!-- SECTION:FINAL_SUMMARY:END -->
