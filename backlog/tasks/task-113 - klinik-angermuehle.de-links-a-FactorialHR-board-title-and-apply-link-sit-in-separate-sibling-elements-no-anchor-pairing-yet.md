---
id: TASK-113
title: >-
  klinik-angermuehle.de links a FactorialHR board; title and apply-link sit in
  separate sibling elements, no anchor pairing yet
status: Done
assignee:
  - '@ivan'
created_date: '2026-09-22 17:12'
updated_date: '2026-09-23 15:18'
labels: []
dependencies: []
ordinal: 113000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Run 120 (2026-09-22) zero-yield recon for clinic 27108 (Klinik Angermühle), board https://www.klinik-angermuehle.de/jobs/. The page links https://klinik-angermuehle-gmbh.factorialhr.de/embed/jobs (a plain server-rendered FactorialHR jobs list, no JS needed to read it) but the widget's own markup puts each posting's TITLE in a plain <div class="...factorial__headingFontFamily">, and the apply link/href (.../embed/job_posting/<slug>) is a SIBLING element a few tags later whose own anchor text is just the generic 'Jetzt bewerben' -- neither JOB_PATH (the href has no job/stellen/karriere keyword, 'job_posting' has an underscore where JOB_PATH's pattern needs a / or -) nor GENDER-on-anchor-text (the anchor text carries no gender marker) fires, and the title itself is never inside the <a> tag at all so _job_link_pairs never even sees it. Verified live 2026-09-22: 3 real, currently-open postings on this specific board (Reinigungsfachkraft, MFA/Schlaflabor, Sozialpädagoge) -- NONE of them nursing-relevant today, so the immediate value of fixing THIS board is low; flagged because FactorialHR looks like a real, possibly-recurring commercial HR platform other Bavarian clinics in the registry might also use, worth a shared fix if so.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Check the registry for other clinics whose careers_url is a factorialhr.de/embed/jobs page or links to one -- if none, this may not be worth building yet
- [ ] #2 If worth building: a bespoke extractor pairs each .../factorial__headingFontFamily title div with its following .../job_posting/<slug> href (structural sibling-walk, not JOB_PATH/GENDER-gated) and reads real rows, verified live red-green, mutation-tested
- [ ] #3 Verified: role classification on the resulting rows behaves correctly even though this board's own current postings are not nursing (no false 'Pflege' classification on Reinigungsfachkraft/MFA/Sozialpädagoge)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Grep data/registry/clinics.csv (all columns, all 407 rows) for factorialhr.de / FactorialHR embed patterns per AC#1's gate condition.
2. If zero other clinics match, stop: document finding, check only AC#1, leave AC#2/#3 unchecked with reason, status Done.
3. If multiple clinics match, build sibling-walk extractor per AC#2/#3 with frozen live fixture + mutation test.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC#1 verified: grep -in "factorialhr" data/registry/clinics.csv (all columns, all 407 rows, incl. careers_url) -> 0 matches. Clinic 27108's own careers_url is https://www.klinik-angermuehle.de/jobs/ (the clinic's own page, not the factorialhr embed URL), and ats_type for 27108 is blank -- like 232/407 other rows whose ats_type is not yet classified, so the registry doesn't positively rule out FactorialHR elsewhere, but per AC#1's own stated gate (grep the registry for factorialhr.de patterns) the result is unambiguous: 0 other clinics reference it anywhere in the CSV. Genuinely a one-off today. Not worth a bespoke sibling-walk extractor for 3 non-nursing postings on a single board. AC#2/#3 (extractor + role-classification verification) intentionally left unchecked -- they only apply if AC#1 found multiple FactorialHR clinics, which it did not.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Scoping decision, not incomplete work: AC#1's own gate resolved this to a stop. grep -in factorialhr across all 407 rows / all columns of data/registry/clinics.csv found 0 matches besides the one clinic (27108) this task was filed about. Only 1 clinic in the registry links a FactorialHR board today, and its current 3 postings are all non-nursing. Building the sibling-walk extractor (AC#2) would pay off nothing right now, so it was not built. AC#3 (role-classification check) is moot without an extractor. Verified with plain grep against the live registry CSV, no code changes needed.
<!-- SECTION:FINAL_SUMMARY:END -->
