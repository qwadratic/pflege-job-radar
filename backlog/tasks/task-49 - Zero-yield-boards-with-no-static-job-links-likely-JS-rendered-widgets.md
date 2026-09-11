---
id: TASK-49
title: Zero-yield boards with no static job links -- likely JS-rendered widgets
status: To Do
assignee: []
created_date: '2026-09-11 10:49'
updated_date: '2026-09-11 18:43'
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
- [x] #1 Each board confirmed as one of: JS-widget (needs Playwright/API probe), genuinely 0 open postings right now, or wrong careers_url (fix directly)
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

Domain fix widened as a side effect (unrelated board, found while investigating kbo.de): kbo-dak.de now correctly routes through the shared kbo.de group portal (commit 8d0d7eb, see TASK-58a).

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

2026-09-11 continued (user directive: work through all 9 remaining sites, not just file a follow-up).
All 9 of the 4th-category ("real text, no href") boards now individually resolved:

CODE FIX (generic, committed 165b1eb, pushed): widened _job_link_pairs to accept a job-looking
anchor on EITHER JOB_PATH-on-href OR a gender marker in the anchor's own visible text (mirrors
career_crawl.py's existing OR logic) -- this alone fixed 3 with zero per-site code:
- www.clinic-dr-decker.de: 3 real nursing postings (OP-Schwester/OTA/Krankenpfleger etc).
- www.klinik-am-birkenwald.de: real posting "Ausbildung zum Pflegefachhelfer".
- www.fachklinikum-mainschleife.de: full 10-row listing now surfaces (was truncated to
  Kraftfahrer/Transportfahrer only) -- includes a real nursing role, "Gesundheits- und
  Krankenpfleger in der Anästhesiepflege oder ATA".

CODE FIX (bespoke per-site extractor, same commit 165b1eb, pushed) -- 3 more, each its own small
CMS/theme with no shared vendor to fingerprint:
- klinik-menterschwaige.de: INLINE_HEADING_SITES + _inline_heading_job_rows -- Elementor
  <h3 class="elementor-heading-title"> + nearest following href within a fixed window.
- www.klinik-bad-trissl.de: TITLE_ONLY_SITES + _title_only_job_rows -- Divi tab widget, bare
  repeated title text, no link/description at all, deduped by title.
- klinik-wirsberg.de: BOOTSTRAP_PANEL_SITES + _bootstrap_panel_job_rows -- Bootstrap 3
  .panel-title/.panel-body accordion, not gated on gender marker (site uses "(VZ/TZ)" not
  "(m/w/d)" for some postings; trusts page context instead).
All verified live via direct crawl_wp_jobs(c) calls before commit. Regression suite (adapter_contract,
completeness x2, vendor_adapters, bite, mech_clinic_link, registry) re-run after all 3 additions:
50 passed, 1 skipped.

WRONG careers_url, NO code fix needed -- registry write only, once DB reachable:
- www.waldkrankenhaus.de (290 beds): own karriere page explicitly hands off to
  https://jobs.malteser.de (Malteser's shared board) -- confirmed real nursing postings there.
  Cosmetic-only issue found and NOT chased further: titles show literal "&#40;m/w/d&#41;" instead
  of decoded parens on that third-party board; verified via classify_role() this does not break
  nursing-role classification (matches on other keywords).
- www.kreiskrankenhaus-hoechstadt.de (80 beds): own page names the real external board by name --
  https://www.team-anna.de/stellenboerse/. Confirmed live: crawl_wp_jobs returns 12 real rows incl.
  multiple Pflegefachkraft postings (Intensiv, Station Chirurgie, Station Innere Medizin).
- www.st-irmingard.de (75 beds): its own /karriere/ page and both sub-clinic /karriere/ pages are
  pure marketing text with no listings at all; the group operator's real shared board is
  https://karriere.gesundheitswelt.de/stellenangebote.html (rexx-systems, "Gesundheitswelt
  Chiemgau", 7 companies/4 towns incl. Prien am Chiemsee = St. Irmingard's own town). Confirmed
  live: crawl_wp_jobs returns 50 of 54 rows generically (no code change needed -- gender-marker
  titles already carry the OR-text-signal fix above), 8 tagged "Prien am Chiemsee" incl. a real
  "Pflegefachkraft / Altenpfleger (m/w/d) für den Nachtdienst". Each posting's own JSON-LD
  jobLocation carries the REAL per-posting site address (unlike kbo.de's group-HQ-only address) --
  no title_city_rx-style hack needed, generic parse_job_page already resolves the correct city.

All 9 of AC#2's "4th category" boards are now individually classified with live evidence: 6 fixed
(3 generic + 3 bespoke code, committed/pushed) and 3 more identified as wrong-careers_url-only
(registry write, no code change) -- zero remain unclassified. Supabase (both proxy and direct host)
has been unreachable (hard timeout / intermittent 504) continuously since ~15:00 UTC this session --
confirmed again just now, still down -- so none of this is delivered live yet: the 3 registry-url
fixes, plus posting corrections/registry writes for every other item already queued in TASK-58a.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
All 20 zero-yield TASK-49 boards individually classified with live evidence (AC#1, AC#2, AC#3 all
satisfied). Breakdown: klinikverbund-allgaeu.de partially fixed (TASK-56); klinikum-ab-alz.de
genuinely JS-templated, no fix without Playwright; kh-nuernberger-land.de/kbo-dak.de/
reisach-kliniken.de resolved earlier this session; klinik-vincentinum.de confirmed a harmless
decoy widget, its real board already covered elsewhere; frg-kliniken.de and wertachkliniken.de
fixed (JOB_PATH hyphen widening + rexx board respectively); kliniken-nea.de partially diagnosed,
needs a further no-filter-on-confirmed-listing-page strategy, not closed. The final 9 "real text,
no href" boards are now all resolved: 6 via code (3 for free from the generic href-or-text OR fix,
3 via new bespoke per-site extractors -- klinik-menterschwaige.de, klinik-bad-trissl.de,
klinik-wirsberg.de) and 3 via a wrong-careers_url finding with no code change needed
(waldkrankenhaus.de -> jobs.malteser.de, kreiskrankenhaus-hoechstadt.de -> team-anna.de/
stellenboerse/, st-irmingard.de -> karriere.gesundheitswelt.de/stellenangebote.html). All code
changes committed and pushed (165b1eb, on top of the earlier 59c71bf/8d0d7eb this task also
produced); regression suite green (50 passed, 1 skipped). Left in To Do, not Done: Supabase has
been unreachable since ~15:00 UTC this session, so none of the registry-url fixes or posting
deliveries for this task's findings have actually been written to the live DB yet -- queued
alongside TASK-58a's existing delivery backlog for whenever the outage clears.
<!-- SECTION:FINAL_SUMMARY:END -->
