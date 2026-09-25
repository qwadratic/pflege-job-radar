---
id: TASK-119
title: >-
  28 postings sourced from api.smartrecruiters.com (legacy URL shape) have null
  clinic_id, out of TASK-99's scope
status: Done
assignee: []
created_date: '2026-09-22 20:25'
updated_date: '2026-09-24 00:24'
labels:
  - matching
  - data-quality
dependencies:
  - TASK-99
ordinal: 119000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-22 while verifying TASK-99's live backfill. jobs.smartrecruiters.com/ArtemedSE (the current, public listing-page URL shape) is fixed by TASK-99's VENDOR_ACCOUNT_POOLS -- 45+ postings now correctly resolve. But 28 of the 120 total smartrecruiters-sourced postings in pflege_jobs.v_postings carry a DIFFERENT source_url shape entirely: https://api.smartrecruiters.com/v1/companies/ArtemedSE/postings/<id> -- the raw vendor API endpoint, not the public jobs.smartrecruiters.com listing page. These predate this session's work and were presumably ingested by an older crawl path (a direct API probe, or an early Firecrawl agent run) that no longer runs today -- not reproduced by TASK-99's fix, not reproduced by any current crawl path found so far. Not investigated further: which collector/run originally wrote these, whether that path is still live anywhere, and whether the same VENDOR_ACCOUNT_POOLS pool (16228,16235,18105,18802,18808,18813,18872,76108) would resolve them the same way if re-matched (their employer_name/city fields were not inspected).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Which collector/ingestion path originally wrote these 28 api.smartrecruiters.com rows is identified (grep provenance/collector fields, check crawl_output/*.jsonl history if still retained)
- [x] #2 Confirmed whether that path is still live today or fully retired -- if live, it needs the same account-pool fix TASK-99 applied at its own write point
- [x] #3 The 28 rows' own employer_name/city are inspected; if content-matchable or board-poolable the same way as TASK-99, apply the fix and re-verify live in v_postings
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
AC#1: the 28 api.smartrecruiters.com-shaped rows came from crawl_smartrecruiters()'s own row() helper
(collector "vendor-%s-v1" % vendor = "vendor-smartrecruiters-v1", a generic per-vendor tag, not a
different collector), from an OLDER revision of that function before its current per-posting
detail-fetch step (crawlers/vendor_adapters.py:422-438) started overwriting p["postingUrl"] with the
public jobs.smartrecruiters.com link. AC#2: confirmed NOT live today -- the current code always falls
back to a constructed jobs.smartrecruiters.com/<tenant>/<id> URL when postingUrl is absent, so this
raw-API URL shape is architecturally unreachable now; no crawl-time code change needed.

AC#3: live Matcher replay (pflege_jobs.registry.Matcher, same Artemed 8-clinic pool TASK-99 defined)
against all 28 rows' real employer/city fields: 22/28 resolved cleanly on the first pass (Augsburg ->
76108, Tutzing -> 18802, Berg [=Kempfenhausen, a district of Berg] -> 18808, all via R0_board_town).
The remaining 6 (all city=Feldafing) failed because clinic 18872's operator field carried a data-entry
typo -- "Feldafing Benedictus Krankenhaus Feldafing GmbH & Co. KG" (town name duplicated at the
front) -- which made employer_norm() disagree with sibling clinic 18813's clean
"Benedictus Krankenhaus Feldafing GmbH & Co. KG", defeating _match_board's own same-operator tie-break
for the Plan-KH/Vertrags-KH twin pair (the exact TASK-58A mechanism).

Ivan approved both the registry fix and the backfill on the condition the fix is verified as a strict
improvement, not a regression -- verified two ways before applying anything:
  1. Full bulk replay via pflege_jobs.registry.link_postings() (the exact call link-clinics makes,
     content-only, no board) over all 3190 currently-eligible postings, comparing the live operator
     string against the corrected one: 2669 matched both before and after, 0 flips.
  2. Board-scoped replay (Matcher.match(..., board=ARTEMED_POOL)) over all 93 postings currently
     clinic_id-linked to an Artemed-pool clinic: 6 flips, all on clinic 18813, all from None (the
     fix-blind replay could not currently reproduce their own already-stored
     R0_board_town_bestsite/0.75 value) to that exact same stored value. This means the typo was a
     live latent bug -- had that Feldafing board been re-crawled before this fix, those 6 already-
     correct postings would have been wrongly unmatched on the next resolve. The fix closes that
     exposure; it does not change any currently-displayed data anywhere else.

Applied: registry write (18872.operator corrected, pushed via EdgeSink.write_clinics, confirmed live
before/after) then a direct clinic_links push (pflege_jobs.registry.Matcher.match(employer, city,
board=ARTEMED_POOL) computed for exactly these 28 posting_ids, POSTed via EdgeSink._post({"clinic_links":
...}) -- the same op pflege_jobs.cli link-clinics uses, not a raw SQL UPDATE) for all 28, now resolved:
18808 x6, 18802 x5, 18813_bestsite x6, 76108 x11. Confirmed live: GET v_postings for every
api.smartrecruiters.com-sourced row (57 total) shows 0 with a null clinic_id, down from 28.

No local inbox.sqlite rows existed for these (already fully drained/retired before this session), so
TASK-99's own tools/task99_backfill_account_pools.py mechanism (reset+reprocess via local inbox) did
not apply here -- this backfill computed and pushed the link directly instead, same end state.
<!-- SECTION:FINAL_SUMMARY:END -->
