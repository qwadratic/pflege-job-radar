---
id: TASK-105
title: >-
  Job-side requirements signal (enr_requirements, enr_language_req,
  enr_experience) is extracted but never reaches candidate-clinic matching
status: Done
assignee: []
created_date: '2026-09-22 16:21'
updated_date: '2026-09-22 21:11'
labels: []
dependencies: []
references:
  - pflege_jobs/classify.py
  - sql/001_schema.sql
  - sql/011_PENDING_anon_scope.sql
  - app/data.py
  - app/autopilot/seed.py
  - app/autopilot/matching.py
  - TASK-94
  - TASK-101
ordinal: 105000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-22 investigation triggered by a request to check department/requirements extraction quality feeding candidate-clinic matching (app/autopilot/matching.py's score()/rank(), docs/autopilot.md feature 5). classify.enrich_description() already extracts enr_requirements (the "Ihr Profil"/Anforderungen section text), enr_language_req (stated German level) and enr_experience (years required) from the description body via patterns.json's req_head/req_stop/language/experience regexes, and every live adapter call site (career_crawl.py, bite.py, pi_asp.py, feeds.py, klinikum_passau.py, inbox.py) writes them onto the postings row. Measured live 2026-09-22 on pflege_jobs.postings, status=open, 3166 rows: enr_requirements filled 1802/3166 (56.9%), enr_experience filled 462/3166 (14.6%), enr_language_req filled 212/3166 (6.7%) -- partial but real signal, already sitting in Postgres today.

None of it reaches the matcher. Three layers each drop it:
1. sql/001_schema.sql's pflege_jobs.v_postings view -- the one relation the app ever reads for search/facets/matching -- selects only enr_housing, enr_tariff, enr_contact_emails, enr_bonus, enr_childcare (plus enr_pay_grade, confirmed live but absent from the checked-in 001 view definition, so the deployed view has already drifted from sql/ -- same drift pattern TASK-94 flagged). Confirmed live: "select enr_requirements from v_postings" returns PostgREST 42703 "column v_postings.enr_requirements does not exist". enr_requirements, enr_experience, enr_language_req, enr_pay_text, enr_housing_evidence and enr_anerkennung_mentioned are absent from the view entirely, even though pflege_jobs.postings itself has and fills them.
2. app/data.py's JOB_COLS (the Python-side select list for the app snapshot) does not list them either, so a view fix alone would still not surface them to the app.
3. app/autopilot/seed.py's registry_postings cache table (EXTRA_SCHEMA, cache_registry()) hardcodes 9 columns -- posting_id, clinic_id, title, role_class, department_hint, qualification_hint, city, url, first_published -- the only job fields app/autopilot/matching.py ever sees. Even with 1-2 fixed, this cache drops the fields before matching.py's score() runs.

Downstream effect on matching.py's score(): the "German 10" block scores candidate.german_level in isolation and never checks it against the specific posting's enr_language_req, so a candidate is scored identically against a posting that is silent on language and one that states "Deutsch mindestens B2" -- the requirement the posting actually states is never read. The "qualification 15" block only compares against the coarse qualification_hint enum (GuK/GKiK/Altenpflege/generalistisch -- see the sibling qualification_hint task), never against the free-text enr_requirements. enr_experience is not read anywhere in matching.py.

Sequencing note for whoever picks this up: sql/011_PENDING_anon_scope.sql (written, explicitly NOT applied -- "Applying this is Ivan's call, not an agent's") already plans to move enr_requirements, enr_experience and enr_housing_evidence behind the same column-block as enr_contact_emails, and its own precondition #1 notes the app server authenticates to PostgREST with the anon key only -- there is no service-role key anywhere in this codebase. If 011 lands after this task's view/JOB_COLS change without an explicit carve-out for these three columns, the app's own server-side read breaks too, not just an external caller's. Whoever implements this should raise the anon-scope interaction to Ivan rather than deciding it alone, and should not attempt the Postgres view migration against the live project without Ivan's own run of it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 pflege_jobs.v_postings exposes enr_requirements, enr_language_req and enr_experience (a checked-in SQL migration under sql/, reconciling the enr_pay_grade drift already found live vs. sql/001_schema.sql in the same view)
- [x] #2 app/data.py JOB_COLS includes the three fields, and app/autopilot/seed.py's registry_postings cache (schema plus cache_registry()) carries them through to matching.py
- [x] #3 matching.py's score() cross-checks candidate.german_level against the specific posting's enr_language_req when present, instead of scoring the candidate's level in isolation, with a reason string reflecting the actual posting requirement
- [x] #4 The privacy/anon-scope interaction with sql/011_PENDING_anon_scope.sql is explicitly raised to Ivan before or alongside this change, not silently decided
- [x] #5 Measured before/after: how many open postings' scores or reasons change once the real requirement is read, reported as a number
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC#1: sql/012_task105_requirements_fields.sql, written from pg_get_viewdef('pflege_jobs.v_postings', true) run live by Ivan (supabase db query --linked), not from the stale sql/001 text -- the live view had drifted far beyond the enr_pay_grade gap this task originally named (linked_towns CTE, employer_class CASE, clinics join, verify_status/source_codes/source_url all absent from 001). Reproduced verbatim, 3 new columns appended at the end only (the one change CREATE OR REPLACE VIEW allows without drop+recreate). Applied by Ivan 2026-09-22, confirmed live: existing columns (clinic_id, verify_status, source_url) unchanged, 3 new columns readable. AC#2: app/data.py JOB_COLS extended; app/autopilot/seed.py registry_postings gets 3 new columns (schema + an ALTER TABLE guard for an existing data/autopilot.sqlite, since --reset isn't forced) + cache_registry() INSERT. AC#3: matching.py's German-10 block now reads _stated_level(jobs) (max GERMAN_RANK across the clinic's own open postings' enr_language_req, extracted from the regex-snippet field via _LEVEL_RX) instead of a fixed B2 bar, with a reason string naming the actual posting text; falls back to the old fixed-B2 behavior when no posting states a level. AC#4: raised to Ivan before writing any SQL -- decided to keep enr_requirements/enr_experience open (app server has no key but anon, needs both for matching); sql/011_PENDING_anon_scope.sql's own block-list updated in the same round to drop them, with a comment explaining why.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed + verified live end-to-end (Postgres view -> JOB_COLS -> matching.py), plus unit tests. AC#5 measured live via the real JOB_COLS select against v_postings (3183 open postings): 1759 have enr_requirements, 106 enr_language_req, 454 enr_experience. Of 18 clinics with >=1 open posting stating a language level: 12 state B2 (candidate scores unchanged, but the reason string now names the actual posting text instead of a generic message), 2 state B1 (B1-level candidates now get FULL credit instead of the old fixed bar's half credit), 4 state C1 (B2-level candidates now correctly get REDUCED/no credit instead of automatic full credit under the old fixed B2 bar -- the more consequential direction, previously over-crediting candidates for clinics that actually need C1). Tests: tests/test_autopilot_matching.py (5 cases), mutation-tested (reverted the German-10 block, confirmed 4 of 5 tests red -- the 5th, silent-posting fallback, correctly stayed green -- restored). Full non-network suite: 1375 passed, 18 skipped, 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->
