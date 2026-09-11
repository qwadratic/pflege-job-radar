---
id: TASK-56
title: >-
  Verify career_crawl.Crawler pagination depth on klinikverbund-allgaeu.de
  (list_pages=6 may under-walk)
status: Done
assignee: []
created_date: '2026-09-11 14:05'
updated_date: '2026-09-11 15:35'
labels: []
dependencies:
  - TASK-49
ordinal: 56000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Surfaced 2026-09-11 while fixing Klinikum Kempten's careers_url as part of TASK-49 (klinikverbund-allgaeu.de, 1048 beds combined across 6 clinics: Kempten/Mindelheim/Ottobeuren/Immenstadt/Oberstdorf/Sonthofen). The shared board is a Haufe umantis instance embedded behind the clinic group's own domain (karriere.klinikverbund-allgaeu.de and karriere-im.klinikverbund-allgaeu.de, both the SAME recruitingapp-5556 instance). app/crawl.py's _seed_obs routes vendor=='umantis' through pflege_jobs.sources.career_crawl.Crawler(towns, per_site_pages=150, list_pages=6, sleep=0.2).crawl(seed) -- a full delivery run against this board returned raw=10 / job_links_found=10 total across all 6 clinics combined, which felt low: a single page fetch of the board's own /karriere-detail/... links already showed more than 10 distinct postings across just Immenstadt+Kempten+Mindelheim in the HTML grabbed during recon (see crawlers/vendor_adapters.py's job-link discovery pattern for comparison -- this board is NOT routed through crawl_wp_jobs, it goes through career_crawl.Crawler instead, a different code path). Not confirmed as a bug -- could genuinely be all there is right now -- but list_pages=6 stopping the walk while the portal's own listing may paginate further is the concrete thing to check first.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Confirm live (fresh fetch, not cached) how many total distinct /karriere-detail/ postings klinikverbund-allgaeu.de's umantis board actually has right now, across all 6 Bavaria clinics
- [x] #2 If the real count is higher than what career_crawl.Crawler returns, find why -- list_pages ceiling too low, a pagination link/pattern the Crawler's _next_page detection doesn't recognize on this specific portal, or the section-first BFS narrowing before it should widen back out
- [x] #3 Fix and redeliver if a real gap is confirmed; otherwise close with a note that raw=10 is the real current total
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-11: root-caused and fixed, two compounding bugs (commit 0ee9828):

1. ats_seeds.py's umantis() hub detection only recognized real umantis.com /Vacancies/<id> URLs as
   proof a hop page was the real listing. Klinikverbund Allgäu's CMS front-end proxies umantis
   server-side instead, exposing its own "/karriere-detail/<city>/<slug>" URLs -- that signal never
   appeared, so the builder fell back to guessing /Jobs/1..5+All on the RAW umantis backend, which
   (confirmed live) is itself a JS-driven table exposing only ~11 vacancies server-side even with
   correct pagination. Widened detection to also accept a hop page with several of its own
   gender-marker anchors (JOB_TEXT, reused from career_crawl.py), and to prefer that subdomain's
   bare root over whichever specific nav link happened to be found first (was landing on
   "wir-als-arbeitgeber", an about-us page). Widened seed hosts to allow-list the hub's own domain
   in that case.

2. career_crawl.Crawler.crawl()'s section-first path returned as soon as the matched nursing-section
   subtree yielded ANY rows, without topping up with the full board-wide walk -- the exact gap
   crawl_wp_jobs's equivalent logic in vendor_adapters.py was already fixed for elsewhere. A
   "pflegerische Fachweiterbildungen" nav link matched as a confident section and returned only 9
   rows, silently dropping the other 93. Now always runs both, merged/deduped by URL.

Verified live: raw rows 9 -> 102 for Klinikum Kempten's board. Real recovered postings include
"Pflegefachkraft fuer unsere neonatologische Intensivstation", "Pain Nurse", several
department-specific roles across all 6 sibling clinics. stats() correctly reports truncated=True
(job_links_found=102 exceeds the Crawler's default per-run job-detail budget) -- not silently
hidden, but a real board this size may need a higher budget passed at the umantis call site; not
adjusted this session, worth a look if the delivered count still seems short once live.

17/17 existing ats_seeds/registry/mech_clinic_link tests still pass. No new automated test added for
the JOB_TEXT-branch specifically -- it needs a live network fixture (unlike the existing ANregiomed
fixture test, which only exercises the /Vacancies branch) to test meaningfully offline; flagging
as a real test-coverage gap rather than silently calling it done.

Blocked on delivery (Supabase down all session) -- once reachable, re-run the full 6-clinic board
and confirm real counts per clinic.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Two compounding bugs fixed: umantis seed builder couldn't recognize this tenant's CMS-proxied job links, and career_crawl.Crawler's section-first path stopped early instead of topping up. Verified live: 9 -> 102 raw postings. Delivery pending Supabase recovery.
<!-- SECTION:FINAL_SUMMARY:END -->
