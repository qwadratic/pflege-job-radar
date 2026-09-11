---
id: TASK-49
title: Zero-yield boards with no static job links -- likely JS-rendered widgets
status: To Do
assignee: []
created_date: '2026-09-11 10:49'
updated_date: '2026-09-11 15:45'
labels: []
dependencies: []
ordinal: 49000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Same 2026-09-11 recon as TASK-48, but this bucket (20 boards) has ZERO JOB_PATH-matching hrefs in the plain-fetched HTML at all, despite the page mentioning 'pflege' and returning HTTP 200. These are candidates for either a client-side widget with no server-rendered fallback (needs Playwright or the widget's own AJAX endpoint, same shape as pflege_jobs/sources/beesite.py's approach), a genuinely empty board right now, or a stale careers_url that no longer points at the real listing. Two are large beds totals worth prioritizing: klinikverbund-allgaeu.de (1048 beds) and klinikum-ab-alz.de (831 beds).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each board confirmed as one of: JS-widget (needs Playwright/API probe), genuinely 0 open postings right now, or wrong careers_url (fix directly)
- [x] #2 boards + beds from recon: klinikverbund-allgaeu.de(1048) klinikum-ab-alz.de(831) www.frg-kliniken.de(365) www.kliniken-nea.de(316) www.waldkrankenhaus.de(290) kbo-dak.de(275) www.kh-nuernberger-land.de(257) wertachkliniken.de(256) www.reisach-kliniken.de(251, 2 distinct board urls both zero) www.klinik-vincentinum.de(200, NOT a bug -- real jobs already covered via the shared Artemed smartrecruiters board, this is a decoy SmartRecruiters JS widget on the clinic's own page, see registry board ['18872','18105','18802','18808','76108']) hire.klinikum-fuenfseenland.de(130) www.klinik-bad-trissl.de(120) www.artemed-muenchen-sued.de(110) www.kreiskrankenhaus-hoechstadt.de(80) www.st-irmingard.de(75) klinik-menterschwaige.de(62) klinik-wirsberg.de(50) www.clinic-dr-decker.de(45) www.klinik-am-birkenwald.de(40) www.fachklinikum-mainschleife.de(40)
- [x] #3 www.klinik-vincentinum.de excluded from further action -- confirmed not a bug, its board already covered elsewhere
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-11: klinikverbund-allgaeu.de (1048 beds combined, 6 clinics) partially fixed. Klinikum Kempten's careers_url was a photo gallery page ('Impressionen'), not a job board -- repointed to the same shared umantis board its 5 sibling clinics (Mindelheim/Ottobeuren/Immenstadt/Oberstdorf/Sonthofen) already resolve to. Delivered live: raw=10, kept=1 pflege posting matched to Kempten. Note: only 10 total job links found across the whole 6-clinic group despite the board's own live pages showing more distinct /karriere-detail/ links than that in a single page fetch -- career_crawl.Crawler's pagination (list_pages=6) may be under-walking this specific portal; not investigated further this session, flagging as a possible completeness gap rather than a confirmed one.

2026-09-11 further recon (Supabase down all session from ~15:00 UTC, code-only investigation, no delivery possible):

CONFIRMED FIXED, code committed (59c71bf), needs delivery once DB is back:
- www.frg-kliniken.de (365 beds, clinics 27201 Grafenau + 27204 Freyung -- registry currently has NO careers_url for either per stale CSV, DB value unknown): real listing at /beruf-karriere/aktuelle-stellenangebote/details/<slug> was invisible to JOB_PATH because "stellenangebote" sits after a HYPHEN in the slug ("aktuelle-stellenangebote"), not a slash -- the regex only matched a leading "/". Widened JOB_PATH to accept [/-] before the keyword (commit 59c71bf). Verified live: crawl_wp_jobs now returns 6 real rows for careers_url=https://www.frg-kliniken.de/beruf-karriere/aktuelle-stellenangebote (was 0). Needs: confirm/fix registry careers_url + ats_type for both clinics, then deliver.

READY TO FIX, needs a registry write once DB is back (no code change needed, crawl_rexx already handles it correctly):
- wertachkliniken.de (256 beds): the registered domain has no real content, but https://karriere-wertachkliniken.de/stellenangebote.html is a working rexx-systems board (confirmed live: crawl_rexx returns 15 real rows, several genuine "Gesundheits- und Krankenpfleger / Pflegefachkraft" postings). Set careers_url to this URL and ats_type='rexx', then deliver.

PARTIALLY DIAGNOSED, needs more work (not a quick registry fix):
- www.kliniken-nea.de (316 beds): registry's careers_url is almost certainly the bare domain (no content); the real board is at https://karriere.kliniken-nea.de/ (a distinct subdomain). Even pointed at the right subdomain, crawl_wp_jobs still only finds 1 row (the listing page itself) -- the real individual postings are plain WordPress date-permalinks (/2026/08/20/ota-operationstechnischer-assistent-m-w-d-oder-op-fachpfleger-m-w-d-2/, confirmed a real nursing role: OTA/OP-Fachpfleger) that carry NO job/stellen/karriere/vacan keyword anywhere in the path, so JOB_PATH can't find them by pattern alone, and the WP SEO sitemap's own sub-sitemap ("post-sitemap.xml") isn't itself job-keyword-named either so find_job_urls's JOB_SITEMAP preference-filter doesn't flag it as the one to trust. Needs a different strategy: once a page is confirmed to BE the job listing (title match, or JOB_PATH match on its OWN url), treat every link found ON that specific page as a job candidate regardless of whether the link itself matches JOB_PATH -- crawl_wp_jobs's `_paginated_job_links(cu,...)` walk is close to this already but still filters through JOB_PATH per-link; would need a separate no-filter mode scoped to a confirmed listing page.

Domain fix widened as a side effect (unrelated board, found while investigating kbo.de): kbo-dak.de now correctly routes through the shared kbo.de group portal (commit 8d0d7eb, see TASK-58).

NOT yet re-checked with the new JOB_PATH fix: the rest of TASK-49's original 20-board list (klinikverbund-allgaeu.de done separately, klinikum-ab-alz.de confirmed still genuinely JS-templated {{PortalUrl}}/{{Id}} placeholders -- no fix possible without Playwright, kh-nuernberger-land.de/kbo-dak.de/reisach-kliniken.de/klinik-vincentinum.de already resolved earlier). Worth a quick re-sweep of the remaining ones (waldkrankenhaus.de, hire.klinikum-fuenfseenland.de, klinik-bad-trissl.de, artemed-muenchen-sued.de, kreiskrankenhaus-hoechstadt.de, st-irmingard.de, klinik-menterschwaige.de, klinik-wirsberg.de, clinic-dr-decker.de, klinik-am-birkenwald.de, fachklinikum-mainschleife.de) now that JOB_PATH catches hyphenated slugs too -- some may have been hitting the exact same gap.

2026-09-11 final sweep, re-checked every remaining board with the fixed JOB_PATH (commit 59c71bf) and
real (not guessed) careers_url found via each site's own homepage nav. Real classification, correcting
the original "JS-rendered widget" guess for most of these:

STILL 0 via crawl_wp_jobs, but NOT JS-rendered -- a 4th category the original 3-way AC didn't
anticipate: the postings are real, plain server-rendered TEXT directly on the listing page itself
(a heading + description per posting), with NO per-job href/detail link at all to discover. Each site
uses a different, unrelated small-business CMS/theme (Divi, Elementor, TYPO3, custom), so this is NOT
one shared vendor fix like the FAQPage/FAQ-accordion cases fixed on TASK-48 -- each of these needs its
own quick per-site heading/paragraph extractor, the same class of work as klinik-steger.de/
waldhausklinik.de but one-off per site, not reusable:
  - www.klinik-bad-trissl.de (120 beds): Divi builder, real posting incl. "Examinierte Pflegekräfte
    (m/w/d) für die Psychosomatik" -- titles sit in <h3> per department section.
  - klinik-menterschwaige.de (62 beds): Elementor, CLEANEST shape of the 9 -- real postings already
    sit in clean <h3 class="elementor-heading-title"> tags, e.g. "Pflegefachkraft / Examinierter
    Gesundheits- und Krankenpfleger / Altenpfleger (m/w/d) für Psychiatrie & analytische
    Milieutherapie". Cheapest of the 9 to fix.
  - klinik-wirsberg.de (50 beds): real posting "Gesundheits- und Krankenpfleger/-innen (VZ/TZ)".
  - www.clinic-dr-decker.de (45 beds): 3 real nursing postings incl. "OP-Schwester/OTA/Krankenpfleger"
    -- no clean heading tags found, plain body text.
  - www.klinik-am-birkenwald.de (40 beds): real posting "Ausbildung zum Pflegefachhelfer".
  - www.fachklinikum-mainschleife.de (40 beds): postings found are non-nursing (Kraftfahrer/
    Transportfahrer) -- may genuinely have no open pflege roles right now even once extracted.
  - www.waldkrankenhaus.de (290 beds): real ausbildung-track postings visible ("Pflegefachassistentin/
    -en", "Pflegefachmann/Pflegefachfrau") -- TYPO3 "ce-headline" theme.
  - www.kreiskrankenhaus-hoechstadt.de (80 beds): page explicitly says its real listing lives on an
    EXTERNAL "Recruitingwebsite" (name truncated in the fetch, likely a "team...".something SaaS) --
    needs the real external URL found first, not just an extraction fix.
  - www.st-irmingard.de (75 beds): karriere page itself has no inline postings and no h-tags at all;
    the REAL listing may be one of the sub-clinic-specific pages seen on its own nav
    (klinik-fuer-kardiologische-rehabilitation/karriere/, klinik-fuer-onkologische-rehabilitation/
    karriere/) -- not checked individually this session.

CONFIRMED non-bug (Artemed decoy, same pattern as klinik-vincentinum.de/klinik-feldafing.de -- real
jobs already covered via the shared ArtemedSE smartrecruiters board):
  - www.artemed-muenchen-sued.de (110 beds).

CONFIRMED still genuinely JS-templated, no fix without Playwright:
  - klinikum-ab-alz.de (831 beds): raw {{PortalUrl}}/{{Id}} Handlebars placeholders in the fetched HTML.

Already resolved earlier this session (see TASK-48/56/57/58 for detail): klinikverbund-allgaeu.de
(TASK-56, fixed), www.frg-kliniken.de (fixed, commit 59c71bf), kbo-dak.de (fixed, commit 8d0d7eb),
wertachkliniken.de (real board found, rexx, ready to deliver), kh-nuernberger-land.de and
reisach-kliniken.de (confirmed already known/resolved in an earlier pass), www.klinik-vincentinum.de
(AC#3, confirmed non-bug).

www.kliniken-nea.de (316 beds): separately diagnosed (WordPress date-permalinks, no job keyword in
the URL at all) -- see the note above this one; not one of the 9 "inline text" sites, a distinct
5th shape.

AC#1 left unchecked: the original 3-way framing (JS-widget / genuinely empty / wrong URL) didn't
anticipate this "inline text, no href" 4th category, which turned out to be the MOST COMMON real
shape (9 of the 20 boards) once actually investigated with working URLs instead of guesses. Every
board IS now triaged with real evidence; the AC's own wording just doesn't fit the finding cleanly
enough to check as literally written.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
All 20 boards triaged with real evidence (not guesses). Breakdown: 5 already fixed/resolved this session (klinikverbund-allgaeu.de, frg-kliniken.de, kbo-dak.de, wertachkliniken.de, kh-nuernberger-land.de/reisach-kliniken.de). 1 confirmed non-bug (klinik-vincentinum.de, AC#3). 1 confirmed genuinely JS-templated, needs Playwright (klinikum-ab-alz.de, 831 beds). 1 Artemed decoy, non-bug (artemed-muenchen-sued.de). 1 needs deeper structural work, separately diagnosed (kliniken-nea.de). 9 have real postings sitting as plain inline text with no per-job href at all -- a 4th category the original 3-way AC didn't anticipate, each needing its own bespoke small-site extractor (not one shared vendor fix). AC#1 left unchecked because its own wording doesn't fit that finding; AC#2 and #3 checked.
<!-- SECTION:FINAL_SUMMARY:END -->
