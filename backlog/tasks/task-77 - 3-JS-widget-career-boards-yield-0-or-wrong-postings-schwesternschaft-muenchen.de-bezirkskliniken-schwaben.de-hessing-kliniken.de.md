---
id: TASK-77
title: >-
  3 JS-widget career boards yield 0 or wrong postings:
  schwesternschaft-muenchen.de, bezirkskliniken-schwaben.de, hessing-kliniken.de
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-20 19:30'
updated_date: '2026-09-21 03:16'
labels: []
dependencies: []
ordinal: 77000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-76 AC#5 live-checked (2026-09-20) the 3 JS-widget boards the 2026-09-18 crawler audit flagged as unexercisable without a browser. All 3 confirmed to currently yield materially fewer postings than they actually publish, each for a different root cause:

1. schwesternschaft-muenchen.de (Rotkreuzklinikum München, clinic_id 16215, ats_type blank -> falls back to crawl_wp_jobs): live Playwright render of the registry careers_url itself (https://www.schwesternschaft-muenchen.de/stellenangebote/index.php) shows '18 Stellen gefunden' with real, nursing-heavy titles (Pflegefachkraft, Pflegefachhelfer, Wohnbereichsleitung, ...), all injected by inline JS with no sitemap entries and no server-rendered anchors. crawl_wp_jobs returns 0 rows for this URL (confirmed live: '[wp_jobs] find_job_urls: no job links in sitemap ... (0 sitemap urls seen)'). The titles carry standard (m/w/d) gender markers, the same shape crawlers/portals.py's parse_mwd_anchors_pw already parses for muenchen-klinik/altmuehlfranken -- this board fits that existing pattern directly, but crawlers/portals.py's JS_PORTALS crawler is a standalone CLI script never wired into crawlers/routing.py, so even adding an entry there would not make it part of the scheduled/routed crawl without further wiring.

2. bezirkskliniken-schwaben.de (Bezirkskliniken Schwaben KU, 6 clinics: 76114, 76203, 76304, 76403, 77707, 77907, all sharing careers_url https://www.bezirkskliniken-schwaben.de/ausbildung-karriere/stellenangebote-bewerbung, ats_type blank): that careers_url is a marketing/FAQ landing page with zero job links of any kind (confirmed live: 0 job-like anchors, no iframe, no job-related XHR/script requests even after full render+scroll). The real, actively job-listing board lives on a DIFFERENT subdomain the landing page links to via 'Jetzt bewerben!'/'Offene Stellen': https://jobs.bezirkskliniken-schwaben.de/Jobs (a plain requests.get returns only an unrendered template shell containing the literal placeholder '/Job/{{Id}}'; Playwright render shows 59 distinct real posting URLs matching /Job/<numeric-id>). Neither the registry careers_url nor any current adapter targets this subdomain.

3. hessing-kliniken.de (Orthopädische Fachkliniken der Hessing Stiftung, clinic_id 76111, careers_url https://www.hessing-kliniken.de/karriere/ausbildung/, ats_type softgarden): crawl_oracle/crawl_softgarden's own pflege_jobs/sources/softgarden.py:find_host() returns (None, None) live against this exact careers_url -- 0 postings. Two compounding problems: (a) the registered careers_url is the Ausbildung (apprenticeship) subpage, not the real careers hub at https://www.hessing-kliniken.de/karriere/ (confirmed live: that page 200s and links 12+ /karriere/detail/?tx_softgarden_kategorieliste... category URLs); (b) even the correct /karriere/ page does not match find_host()'s detection patterns (SG_HOST expects *.career.softgarden.de / *.softgarden.io / jobdb.softgarden.de, or a same-page 'softgarden' + '/job/<id>/' link) -- this tenant runs softgarden through a TYPO3 extension (tx_softgarden_kategorieliste) whose links are /karriere/detail/?...cat=<uuid>&controller=Api&action=joblist, a shape find_host() has no branch for.

Same category of defect as TASK-65 (wrong/stale careers_url, aggregator host swaps) but these three were not part of that pass. Filed as its own task because two of the three also need new/widened adapter logic, not just a registry field fix.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 schwesternschaft-muenchen.de: registry ats_type/route change (or a new wired-in crawler) makes the real Stellenangebote list (Pflegefachkraft/Pflegefachhelfer roles etc., ~18 postings as of 2026-09-20) reach the inbox for clinic_id 16215
- [ ] #2 bezirkskliniken-schwaben.de: careers_url is corrected to (or a new adapter targets) https://jobs.bezirkskliniken-schwaben.de/Jobs so real postings (59 as of 2026-09-20) reach the inbox for clinic_ids 76114/76203/76304/76403/77707/77907
- [ ] #3 hessing-kliniken.de: careers_url is corrected to https://www.hessing-kliniken.de/karriere/ (or wherever the real listing lives) and pflege_jobs/sources/softgarden.py:find_host() (or a fallback) recognizes this tenant's TYPO3 tx_softgarden_kategorieliste shape so postings reach the inbox for clinic_id 76111
- [x] #4 Each fix is verified with a live re-crawl showing postings > 0 for the affected clinic_id(s), not just a code-presence check
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Live-triage each named board (plain GET + Playwright network sniff) to find its real job source: own AJAX/JSON endpoint > render > stale careers_url > genuinely empty.
2. schwesternschaft-muenchen.de: real board is the concludis widget tenant swmbrk.concludis.de (board 36). Add a concludis-widget adapter that reads host+board id off the careers page's own script tag, resolves the tenant list URL from the tenant's jobportal.js, parses the list and reads each detail page's schema.org JSON-LD. Probe+delegate from crawl_wp_jobs (beesite precedent), no registry change.
3. bezirkskliniken-schwaben.de: real board is jobs.bezirkskliniken-schwaben.de/Jobs (eRecruiter). Its full job list is already embedded as JSON in the plain HTML (window.jobList = new JobList(...)); detail pages carry JobPosting JSON-LD. Add an eRecruiter adapter + fix careers_url.
4. hessing-kliniken.de: real softgarden tenant is hessing-kliniken.softgarden.io (74 vacancies, /de/vacancies + sitemap.xml). find_host already returns it via its own-domain branch once careers_url points there -- registry fix, no code.
5. Tests that fail against pre-fix code, mutation-tested; then the full offline suite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-21 live triage + fix. All three boards re-probed with plain requests and a Playwright network sniff; all three turned out to have a plain-HTTP job source, so none of them needs rendering.

1. schwesternschaft-muenchen.de (16215 + 16223, 435 beds) -- NOT a render case. The page ships concludis' own widget loader, which names the two tenant-specific facts needed: the tenant host (swmbrk.concludis.de) and the board id (36). https://swmbrk.concludis.de/prj/lst/?b=36&jsinclude=1 answers the complete list to a plain GET (18 of 18, matching the widget's own '18 Stellen gefunden' header; ?page=2 returns the identical list). Each posting's detail page carries a schema.org JobPosting once the jsinclude fragment is asked for -- without it, it 302s away. New crawl_concludis_widget in crawlers/vendor_adapters.py, reached by probe-and-delegate from crawl_wp_jobs (the beesite contract, TASK-40 AC#3) so no registry change is needed. Verified live: crawl_wp_jobs on the clinic's EXISTING registry row returns 18 rows, collector vendor-concludis-widget-v1, 10 of them experienced-nursing class, each with its own real city from JSON-LD (Gruenwald/Lindenberg/Muenchen/Wuerzburg/Lindau).

2. bezirkskliniken-schwaben.de (8 clinics, 1210 beds) -- the real board jobs.bezirkskliniken-schwaben.de/Jobs is NOT client-side after all. The handlebars template around the list does carry the literal /Job/{{Id}} placeholder (which is why no anchor matches JOB_PATH), but the whole board is already in the plain HTML as the third argument of window.jobList = new JobList(...). TotalJobsCount 57 == embedded list length, and ?page=2 returns the identical list, so that JSON is the board's own complete listing. Detail pages /Job/<id> carry a full JobPosting JSON-LD. New crawl_erecruiter, same probe-and-delegate wiring. Verified live: 57 rows, 19 experienced-nursing class, real per-posting city + PLZ.

3. hessing-kliniken.de (76111, 150 beds) -- the tenant IS softgarden; find_host() fails because the board is fronted by a TYPO3 extension whose only softgarden reference on the careers page is certificate.softgarden.io (already in GENERIC_SG_HOSTS) and whose apply link (jobdb.softgarden.de/.../applyonline/click?jp=<id>) sits two hops deeper, on a job detail page. Following that click resolves to the real tenant: hessing-kliniken.softgarden.io, which serves /de/vacancies (74 job links) and a working /sitemap.xml. Pointing careers_url at that host makes find_host succeed on its existing own-domain branch with zero code change. Verified live: seed_for + career_crawl.Crawler returns 74 unique postings (20 experienced-nursing class), stats truncated=False.

Also fixed, found on the bezirkskliniken board and generic to parse_job_page: JSON-LD short string fields were taken verbatim, so a board that HTML-escapes inside its own JSON string ('G&#252;nzburg') gave every posting an un-matchable city.

AC status (2026-09-21):
AC#1 CHECKED -- no registry change turned out to be needed for schwesternschaft-muenchen.de: the new crawl_concludis_widget is reached from the clinics' EXISTING registry row (ats_type 'concludis' -> crawl_wp_jobs -> probe-and-delegate). Evidence: crawl_wp_jobs({careers_url: the registry value, name: 'Rotkreuzklinikum Muenchen'}) returns 18 rows, collector vendor-concludis-widget-v1, live 2026-09-21.
AC#2 NOT CHECKED -- the adapter and the correct URL are both proven live (57 rows), but the careers_url correction exists only in data/registry/clinics.csv; the production registry is the DB and this session is not permitted to write it. See the comment for the exact UPDATE.
AC#3 NOT CHECKED -- same reason: the correct listing host is proven live (hessing-kliniken.softgarden.io, 74 postings via the existing softgarden seed path, no code change needed) but clinic 76111's careers_url has not been written to the DB. Note the AC's own guess (/karriere/) is wrong: find_host has no branch for the TYPO3 tx_softgarden shape and the page carries no tenant reference, so pointing at /karriere/ would still yield 0.
AC#4 CHECKED -- all three verified by live re-crawl, postings > 0 in every case: 18 / 57 / 74.

Offline suite after the change: 1228 passed, 1 skipped, 0 failed (was 1214 passed, 1 skipped; +14 new tests). New tests mutation-tested -- reverting the crawl_wp_jobs delegation, the numeric-employmentType drop, the asklepios repeat-page stop and the JSON-LD entity decode each turns the matching test red.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 03:11
---
Registry writes still pending -- I am not permitted to write the production DB in this session, so the careers_url corrections are applied in data/registry/clinics.csv only. The exact writes needed against pflege_jobs.clinics:
  careers_url='https://jobs.bezirkskliniken-schwaben.de/Jobs' for clinic_id in (76114, 76203, 76304, 76403, 77406, 77605, 77707, 77907)
  careers_url='https://hessing-kliniken.softgarden.io/de/vacancies' for clinic_id = 76111 (ats_type stays 'softgarden')
No registry change is needed for schwesternschaft-muenchen.de (16215/16223): crawl_wp_jobs recognises the concludis widget on the page their existing careers_url already points at.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Three boards, three different plain-HTTP job sources found live; none needed a browser. New crawl_concludis_widget (reads the tenant host + board id off the clinic page's own widget loader, then /prj/lst/?b=<board>) and crawl_erecruiter (reads the whole board out of the page's own 'new JobList(...)' JSON, then each /Job/<id> JSON-LD) in crawlers/vendor_adapters.py, both reached by the same probe-and-delegate contract as beesite so no new registry label is needed; hessing needed no code at all, only its careers_url repointed at the real softgarden tenant hessing-kliniken.softgarden.io that its apply link resolves to. Also fixed generically: parse_job_page took JSON-LD address/title strings verbatim, so a board that HTML-escapes inside its own JSON ('G&#252;nzburg', live on bezirkskliniken-schwaben) gave every posting an unmatchable city. Live re-crawl: 18 / 57 / 74 postings where all three previously returned 0, 49 of them experienced-nursing class, across 11 clinics and 1795 beds. Offline suite 1228 passed, 1 skipped, 0 failed. AC#2 and AC#3 left unchecked: both fixes are proven live but need a careers_url write to the production DB, which this session is not permitted to do -- the exact UPDATEs are in the task comment, and are applied to data/registry/clinics.csv.
<!-- SECTION:FINAL_SUMMARY:END -->
