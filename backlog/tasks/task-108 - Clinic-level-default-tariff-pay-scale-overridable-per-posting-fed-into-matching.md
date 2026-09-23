---
id: TASK-108
title: >-
  Clinic-level default tariff/pay-scale, overridable per posting, fed into
  matching
status: To Do
assignee: []
created_date: '2026-09-22 16:31'
labels:
  - research
  - matching
  - data-quality
dependencies:
  - TASK-105
ordinal: 108000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-22: candidate-clinic matching should account for tariff (TVöD, TV-L, AVR Caritas/Diakonie, Haustarif etc). Posting-level extraction already exists -- pflege_jobs.classify.enrich_description() regex-extracts enr_tariff (plus enr_pay_grade) from ad text via patterns.json's TARIFF list (pflege_jobs/config.py), and it already reaches app/data.py's JOB_COLS. What's missing: clinics have no tariff field at all (pflege_jobs.schema.CLINIC_SPEC has none), so a posting silent on tariff has no fallback. Ivan's resolution rule: each clinic normally has one fixed default tariff; a posting's own extracted tariff, when present, overrides the clinic default; otherwise the clinic default applies. Research first -- catalog what tariff schemes actually appear across the registry/postings before deciding the clinic-level field shape, then wire the resolved value into matching (app/autopilot/matching.py score(), same drop-through pattern already found and documented in TASK-105 for other enr_ fields -- check whether v_postings/JOB_COLS/registry_postings cache carry tariff through as far as matching.py actually reads, not just as far as the app snapshot).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Catalog of distinct tariff/pay-scale schemes observed live across clinics and postings (TVöD-P/K, TV-L, AVR Caritas, AVR Diakonie, Haustarif, etc.), with counts and example clinic_ids per scheme
- [ ] #2 Current enr_tariff extraction coverage measured live (% of open postings with a non-null enr_tariff), so the real override hit-rate is known before building on top of it
- [ ] #3 Clinic-level default tariff field added (schema + registry population plan), scoped by the research above, not guessed
- [ ] #4 Resolution rule implemented: posting's own enr_tariff overrides the clinic default when present, clinic default used otherwise; resolved value is what matching reads, not either raw field alone
- [ ] #5 Resolved tariff reaches app/autopilot/matching.py's score() (verify it survives v_postings / JOB_COLS / registry_postings cache the way TASK-105 found other enr_ fields did not) and is reflected in match reason strings
<!-- AC:END -->
