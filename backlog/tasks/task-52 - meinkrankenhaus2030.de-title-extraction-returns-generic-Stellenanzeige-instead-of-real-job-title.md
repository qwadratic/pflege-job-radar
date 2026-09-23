---
id: TASK-52
title: >-
  meinkrankenhaus2030.de: title extraction returns generic 'Stellenanzeige'
  instead of real job title
status: Done
assignee:
  - '@claude'
created_date: '2026-09-11 10:49'
updated_date: '2026-09-21 04:32'
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
- [x] #2 Krankenhaus Weilheim's real nursing postings appear in prod after the fix
- [x] #3 Fix scoped to not regress other wp_jobs-routed HubSpot or non-HubSpot boards (existing test suite in tests/test_completeness_wp_jobs.py stays green)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Re-verify live what the meinkrankenhaus2030.de board yields after the 2026-09-11 parse_job_page fix (title pipe-segment preference).
2. Freeze the evidence offline: save a trimmed real slice of one detail page under tests/fixtures/board_samples/ and add regression tests to tests/test_completeness_wp_jobs.py (title extraction + end-to-end crawl_wp_jobs + the opposite-direction guard that plain '<title> | SiteName' still takes segment 0).
3. Mutation-test: revert the segment preference, confirm the new tests go red, restore, confirm green.
4. Verify AC#2 against prod (v_postings for clinic_id 19001/19002) rather than from code presence.
5. Report, without silently fixing, anything found that is outside this task's title-extraction scope.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fixed 2026-09-11 as part of a broader session (crawlers/vendor_adapters.py parse_job_page: prefer whichever <title> pipe-segment carries a gender marker instead of always taking segment 0 -- this HubSpot template writes 'Stellenanzeige | <real title>', generic label first, opposite of the assumed convention). Delivered live: raw=17, kept=2 real postings (both matched via R1_exact). Caveat before closing: both kept rows resolved to clinic_id=19001 (Krankenhaus Schongau), not 19002 (Krankenhaus Weilheim) as AC#2 specifically names -- this board is shared by both clinics, and crawl_wp_jobs defaults employer_name to the board's clinic0 (Schongau, alphabetically/dict-order first) for every row, the same org-name-defaulting bias documented in TASK-51. Not yet confirmed whether any of the 17 raw postings are genuinely Weilheim-specific and got mis-attributed, or whether Schongau happens to be the correct answer for all of them. AC#2 left unchecked pending a board-aware re-verification (pass each posting's real description through Matcher.match(..., board=[19001,19002], description=...) the same way TASK-51's Diakoneo/Rotkreuzklinikum cases were re-checked) once Supabase is reachable again (see TASK-58a).

2026-09-21 -- re-verified end to end and frozen offline.

The 2026-09-11 adapter fix (crawlers/vendor_adapters.py parse_job_page: among the <title>'s
pipe-separated segments, prefer whichever one carries a gender marker instead of always taking
segment 0) is correct and still live. Re-measured against the real board today via
app.crawl.raw_board_rows(19002): 20 rows, every one with its real per-posting title, none of them
the literal 'Stellenanzeige'. classify_role now keeps 2 of them as pflegefachkraft
('Gesundheits- und Krankenpfleger/ Operations-Technischen-Assistent (w/m/d)' -> R
pflegefachkraft:gesundheits- und, and 'Operations-Technischen-Assistent / OP-Pflegefachkraefte
(w/m/d) in Vollzeit/Teilzeit' -> pflegefachkraft:pflegefachkraefte). The listing itself links 17
postings; the 3 extra rows are a nav page ('PFLEGE | JOBS & AUSBILDUNG') and a press headline, both
of which classify out (ausbildung / nicht_pflege are in EXCLUDED_ROLE_CLASSES) and so never reach
the postings table -- checked, not assumed.

Why the <title> is the only source here: the detail pages carry no JSON-LD JobPosting, no <h1>, and
no <h2>/<h3> at all, so every earlier rung of parse_job_page is empty and segment 0 ('Stellenanzeige')
was the whole title for all 17.

Frozen offline so this cannot silently regress: tests/fixtures/board_samples/
meinkrankenhaus2030_stellenanzeige_sample.html (two contiguous unedited slices of the live 132 KB
page) plus 3 tests in tests/test_completeness_wp_jobs.py -- the title extraction, the whole
crawl_wp_jobs listing->detail path, and the opposite-direction guard that an ordinary
'<real title> | SiteName' still takes segment 0. Mutation-tested: reverting the segment preference
to 'segs[0]' turns the first two red (2 failed, 15 passed) and restoring it returns 17 passed.

AC#2 evidence (prod, read-only): GET v_postings?clinic_id=in.(19001,19002) returns exactly the two
postings above, posting_id 6267 and 6268, status open, both linked to clinic_id 19002 Krankenhaus
Weilheim. The caveat recorded on 2026-09-11 (rows resolving to 19001 Schongau) no longer holds.

Out of scope, NOT fixed here, reported instead: posting 6267 is really a Schongau job, not a
Weilheim one. Its body says 'Fuer das AOZ im Medizinischen Zentrum SOGESUND', contact 08861 (Schongau
dialling code) and HR address '86956 Schongau'; only 6268 says 'Fuer unsere OP-Abteilung am Standort
Weilheim' with '82362 Weilheim'. Both also carry city='Schongau', which crawl_wp_jobs's
_wp_job_rows stamps from the board's representative clinic on EVERY row -- app/crawl.py already
guards that same defaulting with 'if len(ids) == 1', but the adapter's own fallback does not see
board membership and bypasses it. That is shared-board clinic attribution (TASK-51's area), not the
title-selector gap this task is about, so it is left for a decision rather than widened into here.

Validation 2026-09-21: full offline suite 1236 passed, 1 skipped, 1195 deselected, 0 failed (.venv/bin/python -m pytest -m "not network" -q, 377s). Targeted: tests/test_completeness_wp_jobs.py 17 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The title-selector gap itself was fixed on 2026-09-11 (parse_job_page prefers the <title> pipe-segment that carries a gender marker, because this HubSpot template writes 'Stellenanzeige | <real title>' with the generic label first); this session re-verified it against the live board and froze it so it cannot regress unnoticed. Live re-measure via app.crawl.raw_board_rows(19002): 20 rows, all with real per-posting titles, none 'Stellenanzeige', 2 kept as pflegefachkraft by classify_role (was 0 of 17). The 3 rows beyond the listing's 17 links are a nav page and a press headline, both classified out before ingest. Added tests/fixtures/board_samples/meinkrankenhaus2030_stellenanzeige_sample.html (trimmed real page: no JSON-LD, no heading tag at any level, so <title> is the only title source) and 3 tests in tests/test_completeness_wp_jobs.py covering the title extraction, the full crawl_wp_jobs listing->detail path, and the opposite direction ('<real title> | SiteName' must still take segment 0). Mutation-tested: reverting to segs[0] gives 2 failed / 15 passed, restoring gives 17 passed. AC#2 verified read-only against prod: v_postings?clinic_id=in.(19001,19002) returns posting_id 6267 and 6268, both open, both linked to 19002 Krankenhaus Weilheim -- the 2026-09-11 caveat about them resolving to 19001 no longer holds. Full offline suite green: 1236 passed, 1 skipped, 0 failed. One defect found but deliberately NOT fixed here because it is outside this task's scope (shared-board clinic attribution, already filed as TASK-81): posting 6267 is really Schongau's job (AOZ/SOGESUND, dialling code 08861, HR address 86956 Schongau) and both rows carry city='Schongau' because _wp_job_rows stamps the board's representative clinic's town on every row, bypassing the 'if len(ids) == 1' guard app/crawl.py already applies to the same defaulting.
<!-- SECTION:FINAL_SUMMARY:END -->
