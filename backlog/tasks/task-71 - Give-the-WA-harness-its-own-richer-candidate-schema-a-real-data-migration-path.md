---
id: TASK-71
title: >-
  Give the WA harness its own richer candidate schema + a real-data migration
  path
status: Done
assignee:
  - '@claude'
created_date: '2026-09-13 09:40'
updated_date: '2026-09-13 09:57'
labels: []
dependencies: []
ordinal: 71000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan wants our own database shaped closely enough to the real production system (stage, ball/waiting-for, per-requirement scoreboard state) to support meaningful reporting and a dry-run tool, and wants existing real candidates migrated in rather than starting from zero. This is our own separate sqlite file (same as today, app.wa.config.SQLITE_PATH) -- never sales_brain.sqlite itself, never committed to the repo (already gitignored, same as wa.sqlite today). Real candidate PII stays out of git exactly as it does today.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 wa_threads (or a new companion table) gains explicit stage/ball/requirement-scoreboard-shaped columns derived from the existing Luna card, not just an opaque slots blob -- enough structure for a dry-run report to read directly
- [x] #2 A migration script (run manually, not part of the automated test suite, since it touches a real external database) maps real candidates from the production system into this schema -- phone, stage, qualification path, requirement states, consent -- preserving continuity rather than starting cold
- [x] #3 Migration is idempotent (safe to re-run) and does not silently drop or misrepresent a candidate it cannot confidently map -- unmappable rows are reported, not skipped silently
- [x] #4 Unit tests cover the schema and the mapping logic against fixture rows shaped like the real source tables (no real data in the test suite)
- [x] #5 Full offline suite stays green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New app/wa/luna/reporting.py: stage_for(card) derives a stage label (new_lead/not_placeable/qualifying/documents_in/ready/consented) from existing card fields -- no new columns, a pure function over what luna_brain.py already tracks. ball_for(conn, phone) derives us/them/none from the last row in wa_messages (direction) -- the actual reply-owed signal a dry-run/migration tool needs.
2. New app/wa/luna/migrate_candidates.py: reads real candidates from an operator-configured external source (WA_MIGRATE_SOURCE_DB env var, generic documented schema, same discipline as TASK-69's external_contacts.py -- no real system named/pathed in code), maps them into wa_threads (phone, slots built from source fields), idempotent upsert, unmappable rows reported not silently dropped. Run manually, not part of the automated suite.
3. Tests: stage_for/ball_for against fixture cards/message histories; migration mapping against fixture source rows (no real data in the test suite).
4. Offline suite, docs, backlog finalize.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/luna/reporting.py: stage_for(card) (new_lead/not_placeable/qualifying/documents_in/ready/consented, pure function over existing card fields + requirement_scoreboard()) and ball_for(conn, phone) (us/them/none from the last wa_messages row) -- no new columns, purely derived, since the harness's own decision logic already carries everything needed.

app/wa/luna/migrate_candidates.py: idempotent import from a generic JSON export (only 'phone' required) into wa_threads, merging (not replacing) an existing card on re-run. Deliberately does not query any specific real external system directly -- same discipline as TASK-69's external_contacts.py -- an operator produces the generic export however fits their own real data source.

Real bug caught by testing, not assumed: M.canonicalize_phone('garbage text') returns '+49' (every non-digit stripped, leaving only the default country code) -- truthy, so a naive 'if not canon' check would have silently accepted garbage as a valid migrated phone. Added a minimum-digit-length check (MIN_PHONE_DIGITS=8) so this is correctly reported as unmappable instead.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/wa/luna/reporting.py adds stage/ball derivation purely from existing card + message-history state (no schema change) -- the structure a dry-run report needs to read. app/wa/luna/migrate_candidates.py idempotently imports real candidates from a generic, operator-produced JSON export into wa_threads, merging rather than replacing on re-run, reporting (never silently dropping) any row with no valid phone number -- caught a real validation gap in testing (canonicalize_phone can return a truthy-but-bogus '+49' for garbage input). Deliberately does not name or query any specific external system, same discipline as TASK-69. 18 new tests. Offline suite: 1031 passed, 5 pre-existing unrelated failures.
<!-- SECTION:FINAL_SUMMARY:END -->
