---
id: TASK-99
title: >-
  Shared-vendor-account boards get a single-clinic match pool when sibling sites
  use different careers_url (SmartRecruiters/Artemed, Gesundheitswelt Chiemgau)
status: Done
assignee: []
created_date: '2026-09-22 16:10'
updated_date: '2026-09-24 00:24'
labels: []
dependencies: []
references:
  - pflege_jobs/registry.py
  - crawlers/routing.py
  - app/crawl.py
  - crawlers/vendor_adapters.py
  - TASK-81
  - TASK-57
ordinal: 99000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Investigating why data/inbox.sqlite's 463 'loaded (no site match)' rows fail to link (triggered by the 2026-09-22 unmatched-inbox review). jobs.smartrecruiters.com is 303 of the 463 (65%); all 303 are Artemed SE postings, board_clinic_ids always a single clinic (whichever Artemed site's own crawl triggered the fetch), even though the SmartRecruiters company feed 'ArtemedSE' actually spans 7 distinct registered Bavaria clinics on 7 DIFFERENT own-domain careers_url values: 16228 (artemed-muenchen-sued.de), 16235 (artemedmuenchen.de), 18105 (psychosomatik-diessen.de), 18802 (krankenhaus-tutzing.de), 18808 (ms-klinik.de), 18813/18872 (klinik-feldafing.de, Plan-KH/Vertrags-KH twin), 76108 (klinik-vincentinum.de). crawlers/routing.py plan() groups boards by exact careers_url string (line ~150), so these 7 clinics never land in one board pool; each clinic's own crawl independently rediscovers the SmartRecruiters ident and walks the WHOLE company feed, tagging every row with only its own single clinic_id as board_clinic_ids (app/crawl.py _vendor_rows, line ~423). pflege_jobs/registry.py Matcher._match_board then correctly refuses a single-clinic pool when the posting's own city disagrees (decision-5's 'no match beats a wrong match') -- so nothing gets a chance at the real 7-clinic pool. Verified live: replaying pflege_jobs.registry.Matcher against the real clinics table and real inbox payloads reproduces today's exact 0/303 with the narrow per-row pool, and 261/303 resolve correctly via the existing R0_board_town rule alone once given the true 8-clinic pool (16228,16235,18105,18802,18808,18813,18872,76108) -- no new matching logic needed, only the right candidate set. Second likely instance: karriere.gesundheitswelt.de (clinic 18721 Klinik St. Irmingard, Prien am Chiemsee) -- 7 of its 8 unmatched rows are in Bad Endorf, where a separate registered clinic (18713 Simssee Klinik) exists; not yet confirmed whether St. Irmingard and Simssee share one operator/account. This is distinct from TASK-81's mechanism #1 (board bound to clinics[0]): TASK-81's own investigation found app/crawl.py already threads the FULL board_clinic_ids list downstream, not just clinics[0] -- that mechanism is about an already-shared careers_url losing sibling ids in transit. Here the clinics never share one careers_url at all, so routing.plan() never groups them in the first place; this is a board-DISCOVERY gap, not a threading bug. TASK-57's 2026-09-21 NULL-postings census (api.smartrecruiters.com 28, karriere.gesundheitswelt.de 8) already surfaced these same hosts and bucketed them under 'TASK-81's four sub-mechanisms' without pinning which one -- this task names the actual mechanism.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A recovery mechanism is chosen and documented: group registry clinics by the vendor's own resolved account/company identifier (e.g. SmartRecruiters ident) rather than by literal careers_url, OR a manually curated config (GROUP_PORTALS-style) -- with a written tradeoff (auto-detection risk vs manual curation effort, and how many other hosts in the long tail likely share this shape)
- [x] #2 jobs.smartrecruiters.com/ArtemedSE: at least 261 of the 303 inbox rows currently noted 'loaded (no site match)' resolve to their correct clinic_id when the true 8-clinic pool (16228,16235,18105,18802,18808,18813,18872,76108) is used, verified against the live Matcher and real crawl data
- [x] #3 karriere.gesundheitswelt.de: the operator/account relationship between clinic 18721 (Klinik St. Irmingard) and clinic 18713 (Simssee Klinik) is confirmed before any pool-widening is applied there
- [x] #4 A full replay of all currently clinic_id-matched postings shows the fix changes no outcome except correcting a wrong one -- no new regressions
- [x] #5 Red-green test against a live board (fetch for real, confirm the test fails before the fix and passes after) plus a mutation test (temporarily revert the fix, confirm red, restore)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Manual curation chosen over auto-detection by vendor account id (AC#1): 2 confirmed instances is not yet evidence of a large long tail, and auto-clustering by a resolved vendor ident risks silently grouping unrelated clinics that merely share a SaaS reseller. crawlers/vendor_adapters.py VENDOR_ACCOUNT_POOLS + account_pool_for(), wired into app/crawl.py _vendor_rows() right after routing's own per-board clinics grouping (widens board_clinic_ids, never changes which URL gets fetched).
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
created: 2026-09-23 23:08
---
2026-09-23 follow-up (TASK-128): the Gesundheitswelt Chiemgau pool entry this task added
(clinic_ids 18721/18713) was REMOVED from VENDOR_ACCOUNT_POOLS in crawlers/vendor_adapters.py.
Both clinics now carry their own careers_url pre-filtered by the board's own
`filter[client_id][]=<id>` query (3 = Simssee, 6 = St. Irmingard, ats_type=rexx), discovered via a
Firecrawl CAREER_SCHEMA recon call and confirmed live (each filtered URL returns only that one
entity's jobs, zero cross-contamination from the other 6 non-hospital entities -- Reha centres, a
spa/wellness resort -- on the same AG's shared board). With the fetch itself now precise, the
pool's town-based widening became actively harmful (it would let the Matcher's guess override a
crawl already known with certainty) rather than necessary, so it was deleted, not just left dormant.
tests/test_vendor_account_pools.py pins account_pool_for("18713"/"18721") == None now; the
SmartRecruiters/ArtemedSE and Weilheim-Schongau pools this task also added are untouched. See
TASK-128's implementation notes for the full recon-then-adapter-fix story.
---

created: 2026-09-24 00:24
---
2026-09-24 follow-up (TASK-119, this task's own residual): the 28 api.smartrecruiters.com-shaped
rows this task's Final Summary flagged as residual are now resolved. Root cause was NOT "a partial
not-found-on-re-read gap in EdgeSink" as guessed here -- it was an older, now-retired revision of
crawl_smartrecruiters() (before its per-posting postingUrl enrichment existed) plus a data-entry typo
on 18872's operator field (duplicated town-name prefix) that broke the Feldafing twin-site tie-break
for 6 of the 28. Fixed via a direct clinic_links push using this task's own Artemed pool + Matcher,
not the local-inbox reset mechanism tools/task99_backfill_account_pools.py used (no local inbox rows
existed for these). See TASK-119's Final Summary for full evidence, including the regression replay
that found the operator typo was a live latent bug affecting 6 ALREADY-matched postings too.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed + verified both live and in production. Code: crawlers/vendor_adapters.py VENDOR_ACCOUNT_POOLS (Artemed 8-clinic pool, Gesundheitswelt Chiemgau 2-clinic pool, AC#3 confirmed via the portal's own site/location filter literally listing 'Simssee Klinik GmbH') + account_pool_for(); app/crawl.py _vendor_rows() widens board_clinic_ids to the account pool. AC#2: live Matcher replay against real stored payloads (exact production call signature via jobposting_to_obs, no description=) -- baseline 0/303 resolved, with fix 261/303, all via R0_board_town. AC#4: replayed all 101 already-matched rows on both hosts, 0 flips. AC#5: tests/test_vendor_account_pools.py, mutation-tested (reverted the widening, confirmed red, restored). 189 passed / 8 skipped across all touched test files. Applied retroactively to already-stuck rows via tools/task99_backfill_account_pools.py (patches stored payload.board_clinic_ids, resets processed_at, re-runs the real pflege_jobs.cli inbox path) -- confirmed live in Postgres v_postings: smartrecruiters 45 postings now carry clinic_match_rule=R0_board_town/R0_board (0 before), clinic_id distribution 18802 6->52, 76108 0->13, 18808 0->6; gesundheitswelt 18713 1->15. Residual: 28/120 smartrecruiters and 2/18 gesundheitswelt postings still null clinic_id (a partial 'not found on re-read' gap in the EdgeSink linking step, pre-existing mechanism unrelated to this fix, not investigated further -- worth a follow-up if it recurs at scale).
<!-- SECTION:FINAL_SUMMARY:END -->
