---
id: TASK-180
title: >-
  Hand-made data corrections live in the DB with a classified reason
  (pflege_jobs.corrections)
status: In Progress
assignee: []
created_date: '2026-09-29 22:39'
updated_date: '2026-09-30 16:02'
labels:
  - registry
  - audit
dependencies: []
ordinal: 177000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-29: the reason for every hand-made change must be in the database itself, classified -- so it is always clear where the DB knowingly differs from the state Krankenhausplan (or RHV) and why, and where it only fixes our own reading of it. Replaces data/ledger.jsonl (TASK-174).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 pflege_jobs.correction_reasons (10 codes, overrides_source flag) and pflege_jobs.corrections (one row per changed field, composite FK code+table) exist in production, RLS on
- [ ] #2 The 181 ledger.jsonl lines are in corrections with a reason code each; the file is gone from data/
- [ ] #3 tools/apply_clinic_corrections.py and tools/apply_posting_changes.py refuse a change without _why.code from correction_reasons and record into the table; clinic inserts supported (_insert)
- [ ] #4 A PDF-vs-DB deviation report lists every field where the DB differs from the Krankenhausplan 2026 parse, each either explained by a corrections row or flagged unexplained
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-29 ~22:30-23:40 UTC, session 663542db, Ivan approved the taxonomy (#1) and the DDL (#2) the same evening.
DDL: sql/013_corrections.sql applied over SUPABASE_DB_POOLER_URL (role postgres, autocommit, file has its own begin/commit). Read back: 10 codes -- clinics: parse_error, source_outdated*, source_error*, not_in_source, board_location; postings: wrong_clinic, duplicate, not_a_vacancy, false_gone, role_misclassified (* overrides_source = true: the DB knowingly differs from the primary source). corrections: id, at, table_name, row_id, field ('*' = whole row inserted), old_value/new_value jsonb (SQL NULL for None), reason_code, reason, evidence text[] (non-empty), task (TASK-n), made_by, backup; FK (reason_code, table_name) -> correction_reasons (code, applies_to), so a clinics code cannot land on a postings row. RLS enabled on both, no policies (pflege_jobs default privileges would otherwise grant anon/authenticated SELECT); relrowsecurity read back true for both.
Migration: data/migrate_ledger_to_corrections.py --push inserted all 181 data/ledger.jsonl lines; read back by code = clinics board_location 39, parse_error 7; postings duplicate 14, not_a_vacancy 14, wrong_clinic 107 (rules in the script docstring; every line matched exactly one). The script refuses a non-empty table (no double migration). data/ledger.jsonl moved to backups/ledger_jsonl_migrated_to_corrections_20260929.jsonl.
Tools: tools/ledger.py now writes pflege_jobs.corrections (connect/reason_codes/require_why(why, where, codes)/entries/record(lines, conn)); _why needs code from correction_reasons for that table. apply_clinic_corrections: _insert:true adds a clinic (refused if already live; recorded as one '*' row). Both tools: if recording fails after the data change is live, the rows are saved to <backup>.corrections.json and the run fails loudly. tests/test_ledger_tools.py 17 tests; mutations (no code check / JSON-wrapping None / _insert left in fields) each caught, restored -> 17 passed.
Used tonight (rows recorded): reopen 20 false_gone (40), group V 6 clinics board_location (8) + 13 duplicate retires (26), insert 16101/16102 parse_error (2).
AC#4 (PDF-vs-DB deviation report) not done yet.

2026-09-30: sql/014_source_refresh.sql applied (code source_refresh, clinics, overrides_source false: a newer edition of the source changed the value). TASK-175 backfill inserted: 141 rows (parse_error; 123 from the first registry build keeping 2025 names/operators/towns that the 2026 cell still states word for word, 18 hand edits TASK-131/57/86). Safe fill/repair pushed: 43 clinics, 97 values (parse_error). One accidental second run of the same safe file (--by x) re-sent identical values: read-back OK, 0 corrections rows (no field changed), extra backup file 20260930T155508Z. corrections total 794. Deviation report after: explained name 53, operator 66, town 22; unexplained 51 (name 24, operator 14, town 10, landkreis 1, regierungsbezirk 2); missing_in_db 0; not_in_source 8, all explained (nicht_mehr_im_plan). The 51 are being checked one by one against the PDF cell and first-hand sites (agent, /tmp/review51/, no DB write).
<!-- SECTION:NOTES:END -->
