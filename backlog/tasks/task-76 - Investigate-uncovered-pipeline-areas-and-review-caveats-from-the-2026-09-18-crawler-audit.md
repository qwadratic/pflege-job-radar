---
id: TASK-76
title: >-
  Investigate uncovered pipeline areas and review caveats from the 2026-09-18
  crawler audit
status: Done
assignee:
  - '@ivan.d.kotelnikov'
created_date: '2026-09-18 10:12'
updated_date: '2026-09-20 19:35'
labels: []
dependencies: []
priority: low
type: spike
ordinal: 76000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The 2026-09-18 crawler review (14 lens finders + adversarial verify + completeness critic + 4 follow-up finders, 177 agents total, /tmp/crawler_review_2026-09-18.md) explicitly named areas it did not open or could not verify live, plus a handful of borderline REFUTED/PLAUSIBLE calls worth a second look once the higher-priority fixes (TASK-62..TASK-75) land and the data has shifted. This task is to actually read/exercise those areas and either fold new confirmed defects into the existing tasks or file new ones, then close this one with a coverage note. Explicitly uncovered by every finder: crawlers/portals.py parse_jobposting_feed + crawl_oracle end to end (4 oracle clinics were flagged "not covered" by two separate finders); the shared-Chromium lifecycle across concurrent verify passes (crawlers/portals.py, referenced but not stress-tested); app/main.py job/clinic/collect endpoints and web/collect.html, specifically the browser-collector write path into the inbox and its daily client quota interaction; crawl_rexx real ?start=N page-size behavior (asserted by the adapter docstring, never measured against a live rexx tenant); parse_helix anchor-regex behavior on a real helixjobs tenant using the absolute /jobad?prj= URL shape (the two helix-labelled registry rows route through crawl_wp_jobs instead, so this path is untested in production); the ~3 JS-widget boards that could not be exercised without a browser (hessing-kliniken.de, bezirkskliniken-schwaben.de, schwesternschaft-muenchen.de) and whether _listing_page_key/find_job_urls caps currently cost them postings; the 18 run-89 zero-row boards causes beyond the ones already traced (JS-only listings are suspected for some, not confirmed). Borderline verdicts worth rechecking: the REFUTED smartrecruiters [:8] subpage probe, personio "haus" office-name rejection, and _page_hosts_ok TLD-widening findings (mechanism confirmed real, but the finder could not show it firing against the live registry -- worth a fresh check once TASK-65s registry fixes land, since some careers_url values will have changed); the PLAUSIBLE Regiomed wildcard companyEid "%2a" finding (0 misattributions today only because the board currently serves exactly the 3 seeded towns -- re-check after any Regiomed/Sana-Oberfranken registry change); the PLAUSIBLE _EINSATZORT site-directory false-positive rate (verify.py:90, folded into TASK-73 AC#13 but worth a fresh measurement after that fix ships to confirm the false-positive rate actually dropped). See /tmp/crawler_review_2026-09-18.md coverage_notes and the REFUTED section at the end for the finders own wording on each item.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 crawlers/portals.py parse_jobposting_feed and crawl_oracle are read function-by-function and exercised against at least one of the 4 oracle registry clinics; any defect found is filed as a new task or folded into an existing one with file:line evidence
- [x] #2 The shared Chromium browser lifecycle in crawlers/portals.py is checked for thread-safety issues when the 03:00 adapter crawl and a manual/hunter Firecrawl run could overlap, and when two verify_all phases run back to back; any race or crash risk found is filed
- [x] #3 app/main.py collect endpoints and the browser-collector inbox write path are read end to end, including how they interact with the ~2000-row/day inbox quota, and any risk (e.g. a single browser-collector session able to starve the daily quota for every other source) is documented or filed
- [x] #4 crawl_rexx ?start=N behavior is checked against at least one live rexx-tenant board (e.g. jobs.schoen-klinik.de or bewerberportal.rhoen-klinikum-ag.com) to confirm the page-size assumption in the docstring matches reality; a mismatch is filed as a bug
- [x] #5 The three JS-widget boards (hessing-kliniken.de, bezirkskliniken-schwaben.de, schwesternschaft-muenchen.de) are checked with a render-capable fetch (Playwright, matching the render rung already available in pflege_jobs/verify.py) to determine whether find_job_urls/_listing_page_key caps currently cost them postings; findings are filed
- [x] #6 The 3 borderline REFUTED findings (smartrecruiters [:8] probe, personio "haus" rejection, _page_hosts_ok TLD widening) are re-run against the registry state after TASK-65 lands, and the Regiomed wildcard companyEid finding is re-checked after any Sana-Oberfranken registry change; each is confirmed still-refuted or escalated to a new task with fresh evidence
- [x] #7 A short coverage note is added to this task final summary listing what was checked, what was found, and which new/existing task IDs absorbed each new defect
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read crawlers/portals.py, crawlers/vendor_adapters.py (crawl_oracle/crawl_rexx/find_job_urls/_listing_page_key), app/main.py collect/ingest path, pflege_jobs/verify.py, app/runs.py, pflege_jobs/sources/career_crawl.py (_page_hosts_ok), pflege_jobs/sources/softgarden.py, pflege_jobs/sources/pi_asp.py + data/registry/pi_seeds.json.
2. Exercise crawl_oracle end to end against the 3 distinct oracle careers_url values in the registry (josef feed, klinikum-ffb WP fallback, altmuehlfranken WP fallback) with real HTTP.
3. Reproduce the Chromium shared-singleton cross-thread hazard with a live threaded Playwright script; trace verify_all/app.runs.py's actual call graph to determine current reachability.
4. Read app/main.py's /api/ingest envelope path + app/crawl.py:_post_inbox end to end; check client_id namespacing between browser-collector agent keys and scheduled adapter client_ids.
5. Live-test crawl_rexx's ?start=N paging against jobs.schoen-klinik.de (raw listing probe + full crawl_rexx run).
6. Live Playwright-render the 3 named JS-widget boards; trace their real job listings and compare to what the current registry-driven adapters return.
7. Re-run the 3 borderline REFUTED findings (smartrecruiters [:8], personio haus rejection, _page_hosts_ok TLD widening) plus the Regiomed wildcard PLAUSIBLE finding against live data/current registry state; escalate _page_hosts_ok with fresh reproduction.
8. File/fold findings, run the offline suite, write the coverage note, check ACs with evidence.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC#1 crawl_oracle: exercised live against all 3 distinct oracle careers_url values in the registry (karriere.josef.de feed path: 21 rows; klinikum-ffb.de WP fallback: 35 rows; karriere.klinikum-altmuehlfranken.de WP fallback, shared by Weissenburg+Gunzenhausen: 6 rows). No crash, no defect found. Checked clean.

AC#2 Chromium lifecycle: reproduced live 'greenlet.error: Cannot switch to a different thread' when a second OS thread reuses a browser crawlers.portals._get_browser() handed to a different thread (Playwright sync API is thread-bound). Confirmed the CURRENT call graph never triggers it: verify_all()'s render pass is deliberately sequential/single-threaded (documented in pflege_jobs/verify.py), app/runs.py serializes all crawl/verify work through one background worker thread, and crawl_portals()/JS_PORTALS is not wired into routing.py or app/crawl.py at all (standalone CLI, separate process). No enforcement exists though -- filed as TASK-78 (latent risk, Low priority, no live impact today).

AC#3 /api/ingest + browser-collector inbox write path read end to end (app/main.py api_ingest -> app/crawl.py:_post_inbox). client_id used for a browser-collector write is the requesting agent key's own label (or 'owner-session'), distinct from the scheduled adapter crawl's 'vendor-adapters-default' and the portal crawler's 'playwright-portals-default' -- by design this isolates a browser-collector session's daily quota from other sources' quota, assuming the (out-of-repo) Supabase inbox daily-limit check is genuinely scoped per client_id as its own error text ('daily limit reached for THIS client') states. Could not verify the server-side enforcement itself (no Supabase access token in this environment, a known standing limitation, not new). No in-repo defect found; documented rather than filed.

AC#4 crawl_rexx ?start=N: live-tested against jobs.schoen-klinik.de. Raw listing probe: start=0/100/200/300 -> 100/100/92/0 jobs, 292 distinct ids total, matching the docstring's '100 jobs per page, page until zero new ids' claim exactly. Full crawl_rexx() run (with detail-page fetches) returned exactly 292 rows, confirming no truncation. Checked clean, no defect.

AC#5 3 JS-widget boards: all 3 live-rendered with Playwright and all 3 confirmed to currently miss real postings, each for a different root cause (schwesternschaft-muenchen.de: 18 real postings, unrouted JS-only list, 0 captured; bezirkskliniken-schwaben.de: 59 real postings live on a DIFFERENT subdomain (jobs.bezirkskliniken-schwaben.de/Jobs) than the registered careers_url, which is a job-link-free marketing page; hessing-kliniken.de: softgarden find_host() returns None against the registered careers_url, which is also the wrong subpage -- the real /karriere/ page uses a TYPO3 softgarden widget shape find_host() does not recognize). Filed as TASK-77 with full per-board evidence; pointer note appended to TASK-65 (same defect category, already Done, not reopened).

AC#6 borderline findings re-checked live against current registry state (post-TASK-65): smartrecruiters [:8] subpage probe -- REFUTED reconfirmed (ms-klinik.de, the only registered smartrecruiters clinic, resolves its ident directly on the base page, the [:8] fallback never fires). personio 'haus' office rejection -- REFUTED reconfirmed (live XML feed for the only real personio tenant, maximilians-augenklinik-ggmbh.jobs.personio.de, has no office label containing 'haus'; barmherzige.net clinics labelled personio in the registry turned out not to run personio at all, per crawl_personio's own docstring). Regiomed wildcard companyEid=%2a -- PLAUSIBLE reconfirmed still-fine: data/registry/pi_seeds.json still seeds exactly the same 3 towns (Coburg default, Lichtenfels, Neustadt) as the original finding, and a live render of the wildcard board today shows only Coburg and Lichtenfels postings (Neustadt currently has none open) -- 0 misattribution. _page_hosts_ok TLD widening -- ESCALATED with fresh evidence: reproduced live that the widening degenerates to a bare-TLD match (matches ANY unrelated .de host containing 'job'/'karriere'/etc.) when a seed's own host is a bare two-label apex domain; confirmed 59 registry careers_url values are bare-apex, and traced that kbo-iak.de (7 umantis clinics) is a live board that reaches this exact code path via ats_seeds.py's umantis hub-hop. Did not confirm a wrong URL has actually been pulled into a live crawl (per-tenant HTML tracing was out of this spike's remaining budget) -- filed as TASK-79 with that as its first AC.

Offline suite verified after investigation (no code changes made): .venv/bin/python -m pytest -m 'not network' -q -> exit code 0, all green (dots + 1 skip only, matching the pre-existing 1214 passed/1 skipped/0 failed baseline).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
All 7 areas investigated live against the current registry/codebase state (2026-09-20), each checked with real reads/greps/live HTTP+Playwright, not guesses. Coverage note: (1) crawl_oracle exercised end to end against all 3 distinct oracle careers_url values -- checked clean. (2) crawlers.portals' shared Chromium singleton reproducibly crashes on cross-thread reuse (Playwright sync API constraint), but today's call graph (verify_all's deliberate sequential render pass + app/runs.py's single crawl-worker thread + crawl_portals() not being wired into routing.py at all) never triggers it -- filed TASK-78 (Low, latent, no enforcement). (3) /api/ingest + browser-collector inbox write path read end to end; client_id namespacing isolates a browser-collector session's daily quota from other sources by design -- no in-repo defect found, server-side quota enforcement itself is unverifiable without Supabase access (standing environment limitation). (4) crawl_rexx ?start=N live-verified against jobs.schoen-klinik.de: exactly matches its own docstring (100/page, 292 total, no truncation) -- checked clean. (5) All 3 named JS-widget boards (schwesternschaft-muenchen.de, bezirkskliniken-schwaben.de, hessing-kliniken.de) confirmed live to currently miss real postings (18, 59, and an unknown-but-nonzero count respectively) for three different root causes -- filed TASK-77 with full per-board evidence, pointer note appended to the already-Done TASK-65 (same defect category, not reopened). (6) smartrecruiters [:8] probe and personio 'haus' rejection reconfirmed REFUTED live against current registry data; Regiomed wildcard companyEid=%2a reconfirmed PLAUSIBLE/still-fine (same 3 seeded towns, 0 misattribution live); _page_hosts_ok TLD-widening ESCALATED with fresh reproduction (degenerates to a bare-TLD match for any bare-apex seed host, e.g. kbo-iak.de/umantis) -- filed TASK-79. New tasks filed: TASK-77, TASK-78, TASK-79. Existing task annotated: TASK-65 (append-notes only, left Done). No code changes were made in this investigation -- every finding was either checked-clean, reconfirmed, or filed/folded per the spike's own instruction to prefer filing over speculative fixes. Full offline suite reverified green after the investigation (pytest -m 'not network': exit 0, no failures).
<!-- SECTION:FINAL_SUMMARY:END -->
