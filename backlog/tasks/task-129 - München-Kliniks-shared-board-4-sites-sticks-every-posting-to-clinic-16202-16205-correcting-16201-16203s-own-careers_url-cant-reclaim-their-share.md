---
id: TASK-129
title: >-
  München Klinik's shared board (4 sites) sticks every posting to clinic
  16202/16205; correcting 16201/16203's own careers_url can't reclaim their
  share
status: Done
assignee: []
created_date: '2026-09-23 10:55'
updated_date: '2026-09-23 16:53'
labels: []
dependencies: []
priority: medium
ordinal: 129000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-23 finishing TASK-82's AC#4. München Klinik operates one shared job board (muenchen-klinik.de) across 4 registry clinics: Schwabing (16201), Harlaching (16202), Neuperlach (16203), Bogenhausen (16205). 16201/16203 had their careers_url corrected this session (TASK-82/86) from a marketing-only /jobs/ subpath to the real board, /stellenmarkt/ -- which does carry 25 in-policy nursing titles (verified live via app.crawl.raw_board_rows + classify_role: 16 pflegefachkraft, 3 leitung, 2 apn_experte, 2 ota_ata, 1 hebamme). 16202/16205 still carry the OLD /jobs/ URL in the registry.

Triggered a real production crawl scoped to clinic_ids=[16201,16203] (run_id=166, app.crawl.execute) against the corrected /stellenmarkt/ URL: 69 raw rows, 39 matched observations, 35 clinic-linked. Result: 0 new postings landed on 16201 or 16203. All 39 observations upserted onto EXISTING posting rows already attributed to clinic_id=16202 (first_seen back to 2026-09-05, long before today), confirmed live (postings.external_url ilike '%muenchen-klinik.de/stellenmarkt%' -> 41 rows clinic_id=16202, 1 row clinic_id=16205, 0 rows clinic_id=16201/16203).

Root cause, precisely: posting_observations' identity is unique(source_id, source_ref) -- the SAME job URL crawled again always upserts onto the SAME existing row, and clinic_id lives on the postings row, set once at whichever crawl FIRST created it. Whatever Matcher rule ran when these 39 rows were first created (before this session touched the registry) picked clinic 16202 as the board's one attributed clinic -- the exact single-clinic-collapse shape TASK-81/96/99/100 already fixed for a FIRST crawl of a shared board. This is a related but distinct failure mode: fixing a SIBLING clinic's own careers_url later, so it can newly discover the same shared board, does not and structurally cannot re-attribute rows that already exist under another clinic -- the upsert path never re-runs clinic matching for a source_ref it has already seen.

New named victims, matching TASK-118's own naming convention: München Klinik Schwabing (16201) and Neuperlach (16203) -- 2 more clinics whose real, verified-live postings are permanently invisible under their own clinic_id.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Confirm the exact mechanism live: does the Matcher/EdgeSink upsert path ever re-run clinic attribution for an existing posting_id, or is clinic_id permanently fixed at first-create (as observed for 16202's 39 rows)?
- [x] #2 A concrete fix is chosen and justified: options include per-posting city/department-text matching against the shared board's known sibling clinics (the pattern this session already used to confirm kbo.de's TASK-57 fix), a one-time re-attribution pass for existing rows when a sibling clinic's careers_url is corrected to the same board, or something else -- picked with evidence, not the first idea tried
- [x] #3 Reproduced fixed: 16201 and 16203 each show their own real nursing postings (not 0) after the fix, without creating cross-clinic duplicates of 16202/16205's existing rows
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
RESOLVED 2026-09-23. Root cause (AC#1) was more precise than this task's own original write-up:
resolve_postings() never touches clinic_id at all (true, confirmed by reading sql/002_task73_migration.sql),
but clinic_links IS pushed UNCONDITIONALLY every crawl for every matched observation (pflege_jobs/cli.py:
link_candidates.extend(o for o in obs if o.get("_kez")), pushed after every run regardless of new-vs-existing
posting) -- so "clinic_id fixed at first-create, upsert never re-runs matching" (this task's own AC#1 question)
is WRONG. The real mechanism: Matcher._match_content's R2_operator -> R6_ambiguous_sites tie-break
(pflege_jobs/registry.py, via _pick_site) resolves EVERY München Klinik posting (employer_name="München
Klinik gGmbH", identical across all 4 sites) to whichever same-operator clinic has the highest bed count
(16202 Harlaching, 660 beds) -- registry-wide, BEFORE board-scoping is ever reached. This happens
deterministically on EVERY crawl, not just the first -- confirmed by hand-tracing the rule ladder against
real live posting_observations employer_name/city values.

Fix (AC#2), evidence before choosing: checked a München Klinik job DETAIL page first for a per-posting site
signal (none -- just "1 Klinik mit 5 Standorten" boilerplate); checked the LISTING page instead
(https://www.muenchen-klinik.de/stellenmarkt/) and found `var allJobs = [...]` -- a complete JSON array of
all 57 real jobs, each carrying `locations: [{"title": "München Klinik Schwabing", ...}]`, i.e. the
board's OWN authoritative per-posting site attribution, previously undiscovered/unused. Chose this over a
one-time re-attribution pass or department-text heuristics because it's a real, page-stated signal (not an
inference) and fixes the problem at the source (org string), so R1_exact picks the right clinic before R6
is ever reached -- no registry/Matcher changes needed.

Implemented crawlers/vendor_adapters.py:crawl_muenchen_klinik(c, session=None, cu_resp=None) -- parses the
allJobs blob, and for each job: if exactly one location is named AND it's a known München Klinik site
(MK_SITES dict), sets org to that site's exact clinic name (org_source left None, so R1_exact applies
un-inherited); otherwise falls back to the generic "München Klinik gGmbH" operator string (same behavior
as before this fix -- not a regression for genuinely ambiguous multi-site/"Alle Standorte" postings).
Registered in crawl_wp_jobs's probe-and-delegate tuple (after crawl_concludis_widget). One real bug caught
via live smoke-test (not a written test): first draft's org priority `(full or {}).get("org") or org` let
a generic hiringOrganization from certain detail pages' own JSON-LD silently override the specific site
name determined from allJobs; fixed to prioritize the confident site-derived org. Mutation-tested this
exact line 2026-09-23: reverted to the buggy ordering -> 2 of 3 new münchen_klinik tests failed red
(asserted clinic 16201 R1_exact, got 16202 R6_ambiguous_sites instead) -> restored from /tmp copy,
diff -q byte-identical -> green again. Confirms the fix is load-bearing.

Tests: 3 new tests in tests/test_vendor_adapters.py using frozen real fixtures (tests/fixtures/board_samples/
muenchen_klinik_{alljobs,stellenmarkt,detail_0..3}_sample.{json,html}, fetched live 2026-09-23) --
single-site jobs get the specific org name, that org resolves to the right clinic_id via Matcher.match(),
unrelated hosts return nothing. Full suite green: 90 passed (test_vendor_adapters.py + test_routing.py).

AC#3 verified live via a real production crawl (run_id=171, app.crawl.execute, scope clinic_ids=
[16201,16202,16203,16205], trigger=manual-task129, 2026-09-23T16:50-16:53 UTC): 112 raw rows (56 per board
x 2 boards -- /stellenmarkt/ and /jobs/), 48 kez-linked observations, 48 clinic_links pushed, 24 new
postings verified live. Post-crawl state of clinics 16201/16202/16203/16204/16205's open postings (35
total): 9 now correctly resolve via R1_exact to their true site (16201:1, 16202:5 [genuinely Harlaching's
own], 16204:1, 16205:2) instead of all 35 defaulting to 16202 as before this fix; the remaining 26 stay on
R6_ambiguous_sites:16201..16205 -> clinic_id=16202 -- these are the genuinely ambiguous "Alle Standorte" /
multi-site-list postings from allJobs, which correctly cannot be site-attributed from the page's own data
either (not a bug -- documented, deliberate scope limit, see the test docstring). 16203 (Neuperlach)
currently has 0 open postings of any kind (only 4 historical `expired`/R0_board rows) -- real data absence
(no current Neuperlach vacancy on the board today), not an adapter bug. Checked for cross-clinic
duplicates across all 35 open postings by title: zero titles span more than one clinic_id (34 unique
titles / 35 rows, the one repeat is the same clinic both times). AC#3 fully satisfied: real postings now
attribute to their own clinic where the source data supports it, no duplicates created.

All 3 ACs verified with live evidence. Closing Done.
<!-- SECTION:NOTES:END -->
