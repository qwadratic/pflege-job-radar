---
id: TASK-41
title: Firecrawl as completeness oracle on boards where HTTP and Playwright both fail
status: In Progress
assignee:
  - '@ivan.d.kotelnikov'
created_date: '2026-09-10 07:49'
updated_date: '2026-09-11 04:52'
labels:
  - harvester
dependencies: []
ordinal: 41000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan re-approved Firecrawl on 2026-09-10 (about 3000 credits, 2000 more on request). For the boards left red after plain HTTP and Playwright (walled or JS-only with no discoverable API), run a sampled Firecrawl pass limited to N pages to prove what the board shows and whether every page type is reachable; record credits spent per board in the harvest report. Prefer raw HTML output over the agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every board still red after HTTP + Playwright has a Firecrawl sample with page count and credits recorded
- [ ] #2 The sample's postings are compared against our adapter's rows and the gap is listed
- [ ] #3 Total credits spent stay within the approved budget and are reported
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Executed the HTTP->Playwright->Firecrawl protocol against the 70 boards flagged red in the prior Adapters-phase report. Root cause analysis found most reds were fixable oracle/adapter bugs, not genuinely-unreachable boards, so Firecrawl spend stayed far under budget (2 credits used of the ~1500 approved for this pass; balance 3918->3916).

Root-cause fixes (all offline suite green after, 838 passed/1 skipped/0 failed, no regressions):
- tests/adapter_contract.py COUNT_RX: WordPress custom-post-type numeric IDs printed right before their own class name ("post-942 stellenangebote", "loop-item-3237 post-3237 stellenangebote") were winning as the page's "declared total". Added a hyphen to the negative lookbehind. Verified against all 3741 cached snapshot files from the prior full run: zero behavior change except the two false positives (veramed.de, krankenhaus-st-camillus.de) correctly disappearing.
- tests/adapter_contract.py client_read_paths: added html.unescape() to URLs pulled from data-url attributes and API_HINTS matches -- an un-unescaped "&amp;" in a data-url query string made a real, correctly-called endpoint (medbo.de's own cn_medbo_jobs AJAX read path) compare unequal to itself and read as "missing" (read_path_coverage false red on medbo.de, la-regio-kliniken.de).
- crawlers/vendor_adapters.py JOB_PATH: widened stellen\w* to stellen?\w* (TYPO3 ameosjobs names a single posting's own path segment "stelle", not "stellen") and added NOT_JOB_PATH to exclude a job-alert subscribe widget's own path ("/job-newsletter", concludis' "/jobletter") that otherwise parsed as a fake posting.
- crawlers/vendor_adapters.py: new _paginated_job_links()/_next_page_url() -- crawl_wp_jobs's section/page-link stages only ever read page 1 of a server-rendered listing. Follows a plain "next page" anchor (ameosjobs) or a TYPO3 Solr search widget's own JSON `"next":"..."` pager field (concludis' martha-maria group). karriere.ameos.eu (typo3_jobs) now returns 765/765 vs previously ~1; karriere.martha-maria.de 60/60 vs ~10; kh-nuernberg.martha-maria.de 26/26 vs ~10.
- crawlers/vendor_adapters.py _enrich_wp_fallback_fields: added a bare schema.org itemprop="datePosted" meta-tag fallback (TYPO3 boards without JSON-LD) -- karriere.ameos.eu's 766 rows went from 0 with a date to populated from the same detail pages already being fetched.

Firecrawl sample: helios-gesundheit.de (the one wp_jobs board whose careers_url is a specific job's own detail page, Akamai-walled to datacenter IPs -- confirmed 403 via plain HTTP and via Playwright/render_probe, both showing the WAF page). Firecrawl's /v2/scrape bypassed the WAF (real 149KB page, statusCode 404 "Seite nicht gefunden") -- proves the round_trip failure is genuine job-posting churn, not a crawler defect; 2 credits.

New false-positive found, not yet fixed (time-boxed out of this pass): bezirkskliniken-schwaben.de's generic "/detail/" JOB_PATH alternative (meant for TYPO3/PERSIS job detail pages) also matches this site's unrelated news/"aktuelles" detail pages, since this TYPO3 install uses the same /detail/ route for news. Confirmed live (sample "row" was a news article about a concert, not a job). Needs a board-specific investigation of the real job listing mechanism (its careers_url page only surfaces a generic tx_solr site-search widget, not a jobs list) -- flagged, not fixed.

Confirmed genuine gaps (plain HTTP has no JSON-LD/meta/itemprop signal AND a Playwright-rendered DOM check of a sample detail page also shows none): karriere.bezirkskrankenhaus-lohr.de (concludis, datePosted), csm-donaustauf.de (self_hosted, datePosted), and the pattern holds via direct crawl re-run across ~25 more wp_jobs/typo3_jobs boards from the prior report's "not covered" list (csm-donaustauf.de, diako-augsburg.de, panorama-fachklinik.de, tcm.info, ukmp.de, wolfartklinik.de, arabellaklinik.de, gaertnerklinik.de, geisenhoferklinik.de, gkg-bamberg.de, ichbindannmalhier.bayern, johannesbad.com, josephinum.de, karriere.medicalpark.de, khdw.de, kkh-sob.de, klinikum-ingolstadt.de, klinikum-msp.de, koenig-ludwig-haus.de, kwm-klinikum.de, meinkrankenhaus2030.de, navicare.med, referral-portal-staging.lmu-klinikum.de, rheuma-kinderklinik.de, salzachklinik-fridolfing.de, simssee-klinik.de, bkh-passau.de, csj.de, diakoneo.de, fachklinik-osterhofen.de) -- datePosted stayed 0/N after re-crawl with the fixed adapter; a JS-rendered exception was found at kliniken-gz-kru.de (JobPosting JSON-LD is injected client-side, absent from plain HTTP -- Playwright sees it, plain crawl_wp_jobs does not; flagged, not fixed -- would need a Playwright fallback in the shared wp_jobs family, a bigger change than this pass's budget covers). sana.de's 3 boards resolve to jobs.sana.de/de/sites/CX_4025/... (a SAP SuccessFactors career site, different vendor, no adapter) -- crawl_wp_jobs correctly finds only the single link out, matching TASK-35's own prediction.

Re-confirmed (not fixed, matches prior adapter reports, evidence refreshed): kbo-iak.de's umantis link (recruitingapp-5656.de.umantis.com) is NOT actually defunct (200, live "kbo-Isar-Amper-Klinikum Stellen" branding) but genuinely lists 0 postings today, confirmed via a fresh Playwright render (0 job links after autoscroll) -- prior report's "dead link" wording was imprecise but the substance (0 reachable postings) holds. mein-check-in mainkofen.de/klinikum-erding.de dead links re-confirmed live (404s).

Did not re-verify every one of the ~13 "already explained" pi_asp/umantis/group_portal/wp_jobs(medbo/la-regio) boards from scratch -- relied on the prior adapter reports' evidence for those not touched by a fix in this pass, per the prior report's own framing that they needed no new Oracle-phase work.

DIVERGENCE FROM THE TASK'S ACCEPTANCE CRITERIA: AC#1 assumes every red board needs a Firecrawl sample after HTTP+Playwright both fail. In practice the large majority of reds were oracle/adapter bugs (fixed above) or confirmed genuine source gaps (no data exists to extract, Firecrawl cannot recover what isn't on the page) rather than walled/JS-only boards -- only 1 board (helios-gesundheit.de) fit the "Firecrawl needed" case this task was scoped for. Leaving AC boxes unchecked and status as-is for review rather than force-closing against criteria that don't literally match what was found.
<!-- SECTION:NOTES:END -->
