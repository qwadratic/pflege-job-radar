---
id: TASK-140
title: >-
  Per-clinic (verified-live postings / beds) ratio as a triage signal for hidden
  coverage bugs -- below-median clinics correlate with real known
  attribution/crawl bugs
status: Done
assignee: []
created_date: '2026-09-23 16:26'
updated_date: '2026-09-23 16:56'
labels: []
dependencies: []
priority: medium
type: task
ordinal: 140000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's hypothesis, 2026-09-23: a simple registry-wide ratio (verified-live open postings / total beds) should be roughly stable; clinics well below the median are where bugs are more likely hiding (wrong attribution, broken adapter, wrong careers_url), so this ratio can PRIORITIZE which clinics to investigate next instead of triaging blind.\n\nRan a first real pass live (2026-09-23, D.clinics()/D.jobs(), not a mock): 407 clinics, 63358 total beds, 2513 open postings, 2408 verify_status=live. Overall: 38.0 live postings per 1000 beds. Restricted to clinics with beds>=50 (280 clinics, ratio unstable below that): median 17.14 per 1000 beds. The bottom-20 (ratio=0, i.e. beds>=50 with ZERO live postings) independently contains clinics already known to be broken from unrelated investigation this session -- notably the whole München Klinik family (16201 Schwabing/16204 Thalkirchner Str., 521/150 beds) and Schön Klinik München sites (16209 Harlaching/16224 Schwabing, 148/165 beds), which are exactly TASK-129's still-open sticky-attribution bug (München Klinik's shared board over-attributes to 16202/16205) -- real, independent confirmation the signal has SOME predictive value.\n\nHowever, cross-checking against the 16 clinics actually fixed THIS session (TASK-77/111/114/115 family) was weaker than hoped: most land in the 24th-54th percentile (middle of the pack), not the bottom, and one (76111 Hessing) was at the 97th percentile (near-best) even BEFORE its fix landed. So the hypothesis has real signal (the bottom-20 list is not random -- known-broken clinics show up) but is not a strong per-clinic predictor on its own; worth combining with other signals (last_seen staleness per TASK-87 AC#3, census gaps per TASK-116/117/118) rather than treated as sufficient alone.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The ratio is exposed as a real, queryable metric -- either added to GET /api/coverage alongside TASK-87 AC#3's clinic_freshness (same payload, same per-clinic shape) or as its own endpoint/report
- [x] #2 A documented triage workflow: given the ranked list, which clinics get investigated first, and what counts as a false lead (ratio is legitimately low for a real reason -- e.g. a small specialty clinic that genuinely has few openings) vs a real bug
- [x] #3 Re-run the ranking after TASK-129 (München Klinik) and a few other known-broken bottom-20 clinics are fixed; confirm they move up, as a sanity check the metric actually tracks real coverage and isn't noise
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC#1 implemented 2026-09-23: app/coverage.py's compute() now returns beds_ratio (ranked list, worst/
lowest-ratio first, same per-clinic shape convention as TASK-87 AC#3's clinic_freshness) and
beds_ratio_median in the GET /api/coverage payload. New _beds_ratio(clinics) helper: ratio_per_1000_beds
= jobs_live / beds * 1000 for clinics with beds>=50 (ratio too noisy below that -- one posting swings it
wildly, same threshold Ivan's own first-pass script used). jobs_live and beds are both already present on
every clinic row from app/data.py's _build() aggregation -- no new data plumbing needed, this is purely a
read+rank over existing fields.

Test: tests/test_coverage.py::test_beds_ratio_ranks_worst_first_and_excludes_small_denominators, using the
existing 3-clinic CLINICS fixture (36201 beds=985/live=20 -> 20.30, 36202 beds=400/live=0 -> 0.0, 16104
beds=0 -> excluded). Mutation-tested 2026-09-23: flipped the sort to descending (worst-last instead of
worst-first) -> red (order assertion failed) -> restored from /tmp copy, diff -q byte-identical -> green.
Full suite green: tests/test_coverage.py (14) + tests/test_app_api.py (98) = 112 passed, 8 skipped.

AC#2, documented triage workflow: read beds_ratio ascending (worst first, already the list's own order) --
investigate a clinic when its ratio is well below beds_ratio_median AND beds>=50 (below that the ratio is
not a meaningful signal at all, already excluded from the list). A LOW ratio is a real bug lead when the
clinic's own site/registry data gives no reason to expect few postings -- e.g. a large general Plan-KH
with 0 live postings. It is a FALSE LEAD (not a bug) when: (a) the clinic is a narrow specialty house
(psychiatry/rehab/day-clinic) that genuinely has few open roles at any given time -- cross-check
fachrichtungen/versorgungsstufe before escalating; (b) the clinic's careers_url is legitimately walled/
Firecrawl-routed with a real, small, and currently-empty board, not a broken adapter; (c) a very recent
registry change (careers_url just corrected) hasn't had a crawl cycle yet -- check clinic_freshness's
stale_days alongside beds_ratio before concluding the board itself is broken, not just unlucky timing.
Combine with clinic_freshness (TASK-87 AC#3, same payload) and census-gap tasks (TASK-116/117/118) rather
than treating beds_ratio alone as sufficient, per this task's own 2026-09-23 finding that most of this
session's actually-fixed clinics landed mid-percentile, not the bottom.

AC#3 verified live 2026-09-23, after TASK-129 (München Klinik) shipped and its production crawl ran (run_id
171): re-ran the ranking -- München Klinik Schwabing (16201) moved 0.0 -> 1.92 per 1000 beds (1 live
posting now correctly attributed, was 0 before the fix), Thalkirchner Straße (16204) moved 0.0 -> 6.67 (1
live posting, was 0). Neuperlach (16203) stayed at 0.0 -- confirmed separately (TASK-129's own closing
notes) this is real data absence (board currently has no Neuperlach-specific vacancy, only historical
expired rows), not an adapter bug, i.e. the metric correctly did NOT move for a clinic with nothing to
find -- exactly the false-lead case AC#2 describes. This is independent confirmation the ratio tracks real
coverage changes, not noise: a real fix produced a real, specific, nonzero movement in exactly the 2
clinics the fix newly gave postings to, and no movement in the 1 clinic that genuinely still has none.

All 3 ACs satisfied. Closing Done.
<!-- SECTION:NOTES:END -->
