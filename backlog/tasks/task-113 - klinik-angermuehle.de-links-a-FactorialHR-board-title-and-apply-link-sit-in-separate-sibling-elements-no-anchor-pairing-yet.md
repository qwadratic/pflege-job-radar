---
id: TASK-113
title: >-
  klinik-angermuehle.de links a FactorialHR board; title and apply-link sit in
  separate sibling elements, no anchor pairing yet
status: To Do
assignee: []
created_date: '2026-09-22 17:12'
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
- [ ] #1 Check the registry for other clinics whose careers_url is a factorialhr.de/embed/jobs page or links to one -- if none, this may not be worth building yet
- [ ] #2 If worth building: a bespoke extractor pairs each .../factorial__headingFontFamily title div with its following .../job_posting/<slug> href (structural sibling-walk, not JOB_PATH/GENDER-gated) and reads real rows, verified live red-green, mutation-tested
- [ ] #3 Verified: role classification on the resulting rows behaves correctly even though this board's own current postings are not nursing (no false 'Pflege' classification on Reinigungsfachkraft/MFA/Sozialpädagoge)
<!-- AC:END -->
