---
id: TASK-52
title: >-
  meinkrankenhaus2030.de: title extraction returns generic 'Stellenanzeige'
  instead of real job title
status: To Do
assignee: []
created_date: '2026-09-11 10:49'
updated_date: '2026-09-11 15:22'
labels: []
dependencies: []
ordinal: 52000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Krankenhaus Weilheim (clinic_id 19002, 220 beds) is hosted on a HubSpot-templated career site (meinkrankenhaus2030.de, ?hsLang=de-de query params). Its careers_url was fixed 2026-09-11 from a stale single-job link to the real listing (/karriere/stellenboerse), which confirmed the listing has 17 real postings including at least one certified-nursing role ('Gesundheits- und Krankenpfleger/-Operations-Technischen Assistent'). But crawl_wp_jobs's generic title extraction returns the literal string 'Stellenanzeige' (German for 'job posting') for every single row instead of the per-posting title, so classify_role() correctly rejects all 17 as non-nursing (no real title to match against) and 0 rows are kept. This is a template-specific title-selector gap, not a routing or URL problem.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Real per-posting title extracted from meinkrankenhaus2030.de detail pages (check for HubSpot-specific meta/JSON-LD/heading selector this template uses instead of the generic <title> or <h1> crawl_wp_jobs currently falls back to)
- [ ] #2 Krankenhaus Weilheim's real nursing postings appear in prod after the fix
- [x] #3 Fix scoped to not regress other wp_jobs-routed HubSpot or non-HubSpot boards (existing test suite in tests/test_completeness_wp_jobs.py stays green)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fixed 2026-09-11 as part of a broader session (crawlers/vendor_adapters.py parse_job_page: prefer whichever <title> pipe-segment carries a gender marker instead of always taking segment 0 -- this HubSpot template writes 'Stellenanzeige | <real title>', generic label first, opposite of the assumed convention). Delivered live: raw=17, kept=2 real postings (both matched via R1_exact). Caveat before closing: both kept rows resolved to clinic_id=19001 (Krankenhaus Schongau), not 19002 (Krankenhaus Weilheim) as AC#2 specifically names -- this board is shared by both clinics, and crawl_wp_jobs defaults employer_name to the board's clinic0 (Schongau, alphabetically/dict-order first) for every row, the same org-name-defaulting bias documented in TASK-51. Not yet confirmed whether any of the 17 raw postings are genuinely Weilheim-specific and got mis-attributed, or whether Schongau happens to be the correct answer for all of them. AC#2 left unchecked pending a board-aware re-verification (pass each posting's real description through Matcher.match(..., board=[19001,19002], description=...) the same way TASK-51's Diakoneo/Rotkreuzklinikum cases were re-checked) once Supabase is reachable again (see TASK-58).
<!-- SECTION:NOTES:END -->
