---
id: TASK-49
title: Zero-yield boards with no static job links -- likely JS-rendered widgets
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-11 10:49'
updated_date: '2026-09-22 09:47'
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Re-triage the two high-value boards left open on this task.
2. klinikverbund-allgaeu.de (1048 beds, 6 clinics): the registered careers_url /karriere carries zero job links today and the umantis board recruitingapp-5556 publishes only 10 vacancies; the real, current board is karriere.klinikverbund-allgaeu.de with 82 server-rendered /karriere-detail/ postings. Verify crawl_wp_jobs reads it, then fix careers_url (+ ats_type off umantis).
3. klinikum-ab-alz.de (831 beds): careers page links its own jobs subdomain jobs.klinikum-ab-alz.de/Jobs -- the same eRecruiter engine as bezirkskliniken-schwaben (TASK-77), full job list embedded as JSON in plain HTML. Covered by the eRecruiter adapter written for TASK-77 + a careers_url fix.
4. Quantify postings recovered per board and record the registry writes needed.
<!-- SECTION:PLAN:END -->

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

2026-09-21, the two high-value boards this task left open:

klinikverbund-allgaeu.de (6 clinics, 1048 beds) -- two separate problems, both now fixed.
(a) Wrong board. The registered careers_url https://klinikverbund-allgaeu.de/karriere carries zero job links of any kind today (confirmed live, plain fetch AND full Playwright render + scroll: 309 anchors, not one job link, no XHR). The umantis instance the registry routes 3 of the 6 clinics to (recruitingapp-5556.de.umantis.com/Jobs/1) publishes only 10 vacancies. The real, current board is https://karriere.klinikverbund-allgaeu.de/ -- 82 server-rendered /karriere-detail/ postings, plain HTTP, no render needed. That also answers TASK-56's old 'is list_pages=6 under-walking?' question: it was not pagination, it was the wrong host.
(b) Every detail page on that board returned the SAME title. The board renders its real headline as <strong class="h1 font-weight-bolder"> and has no <h1>/<h2>/<h3> anywhere, so parse_job_page fell through to the generic page <title> and gave all 82 postings the title 'Karriere Detail - Klinikverbund Allgaeu'. parse_job_page now also accepts a Bootstrap h1/h2/h3 utility class on a non-heading tag as a heading. Verified live: 93 rows, 82 distinct real titles (was 3), 19 experienced-nursing class.
Known remaining gap, NOT fixed (out of this task's ACs): the board names each posting's site as a URL path segment (/karriere-detail/Immenstadt/..., /karriere-detail/Mindelheim-Ottobeuren/...), but career_crawl.city_from_url only recognises the trailing '-in-<city>' shape, so 48 of the 82 postings will carry the seed clinic's town (Kempten) with city_source='seed' rather than their own. Widening city_from_url to any placeable path segment would change attribution on every board that uses it, so it is flagged here rather than done silently.

klinikum-ab-alz.de (2 clinics, 831 beds) -- not JS-templated after all. The {{PortalUrl}}/{{Id}} placeholders the 2026-09-11 recon saw are the handlebars TEMPLATE of the same eRecruiter engine bezirkskliniken-schwaben runs (TASK-77); the board itself lives on the clinic's own jobs subdomain, https://jobs.klinikum-ab-alz.de/Jobs, which the careers page links, and ships its whole job list as JSON in the plain HTML. Covered with no extra code by the crawl_erecruiter adapter written for TASK-77. Verified live: crawl_wp_jobs on that URL returns 62 rows via vendor-erecruiter-v1, 19 experienced-nursing class, with per-posting city and PLZ.

2026-09-22 status audit (read-only; no source/data files touched; run 118 live throughout). Re-verified fresh: code artifacts for every fix cited above still present (JOB_PATH hyphen widening L485, INLINE_HEADING_SITES/TITLE_ONLY_SITES/BOOTSTRAP_PANEL_SITES + their row helpers, crawl_erecruiter delegate, Bootstrap h1/h2/h3 heading fallback, all in crawlers/vendor_adapters.py) and commits 59c71bf/165b1eb/8d0d7eb are in git log. Targeted tests (test_completeness_js_widget_boards, test_routing, test_completeness_wp_jobs, test_vendor_adapters, test_erecruiter_board_total): 103 passed, 0 failed. Live pflege_jobs.clinics queried via PostgREST just now still carries the STALE values for both headline boards: 76301/77801/77802/78001/78002/78003 (klinikverbund-allgaeu.de) all still careers_url=https://klinikverbund-allgaeu.de/karriere, ats_type unchanged (78001/78002/78003 still 'umantis', 77801/77802 'self_hosted', 76301 blank); 66101/67101 (klinikum-ab-alz.de) still careers_url=https://klinikum-ab-alz.de/karriere/. So the two registry blockers named in comment #1 (2026-09-21) are still open today -- production yield for both boards is unchanged from before this task started. Decision: all 3 ACs are honestly checked (classification/code claims hold under fresh evidence) and NOT moved to Done -- this task's own headline value has not reached the live system yet, matching its own final summary's reasoning, independently reconfirmed rather than assumed. Separate finding, not acted on here (file restrictions): TASK-86's dry-run list covers only 66101 of these 8 clinic_ids, and its proposed 76301 fix (set ats_type='umantis') directly CONTRADICTS this task's comment #1 (clear ats_type to '' on the 3 umantis rows) -- whoever applies either dry-run should resolve that conflict first, or the wrong one will ship.

Поправка 2026-09-22: рекомендация из заметок этой задачи -- очистить ats_type у клиник Klinikverbund Allgaeu -- измерена и оказалась неверной.

Замеры на живом борде: старый careers_url (https://klinikverbund-allgaeu.de/karriere) с ats_type=umantis даёт 92 observations, он же с пустым ats_type -- 0 строк. Новый хост (https://karriere.klinikverbund-allgaeu.de/) с пустым ats_type даёт 74, с umantis -- 10.

То есть цифра "10 вместо 93", на которой строилась рекомендация, возникает не от ats_type=umantis самого по себе, а от комбинации НОВЫЙ хост + umantis. На старом хосте umantis -- лучший из четырёх вариантов.

Правильное действие: careers_url не трогать, ats_type=umantis выровнять у 76301, 77801, 77802. Подробности и все четыре замера -- в заметках TASK-86.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 03:11
---
Registry writes still pending (no production DB write permitted this session); applied in data/registry/clinics.csv only. Needed against pflege_jobs.clinics:
  careers_url='https://karriere.klinikverbund-allgaeu.de/' AND ats_type='' for clinic_id in (76301, 77801, 77802, 78001, 78002, 78003) -- ats_type MUST be cleared off the three 'umantis' rows, otherwise routing's vendor precedence keeps the whole shared board on the umantis seeded runner, which finds no instance on the new host.
  careers_url='https://jobs.klinikum-ab-alz.de/Jobs' for clinic_id in (66101, 67101)
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
2026-09-21 addendum to the 2026-09-11 close-out: the two high-value boards this task had left open are now both read in full. klinikverbund-allgaeu.de (1048 beds) had two stacked faults -- the registered careers_url and the umantis instance are both stale (0 and 10 postings respectively; the real board is karriere.klinikverbund-allgaeu.de with 82), and every detail page on that real board returned the same title because it renders its headline as <strong class='h1'> with no h-tag anywhere, which parse_job_page now handles. Live: 93 rows, 82 distinct titles (was 3), 19 experienced-nursing class. klinikum-ab-alz.de (831 beds) is not JS-templated -- the {{PortalUrl}}/{{Id}} placeholders are the handlebars template of the same eRecruiter engine as bezirkskliniken-schwaben, and the real board jobs.klinikum-ab-alz.de/Jobs ships its whole list as JSON in plain HTML; covered free by the crawl_erecruiter adapter written on TASK-77 (62 rows, 19 nursing-class, was 0). Offline suite 1228 passed, 1 skipped, 0 failed. Left In Progress: both fixes need a careers_url write to the production registry (plus clearing ats_type='umantis' on the three Allgaeu rows), which this session may not do -- exact UPDATEs in the task comment, applied to data/registry/clinics.csv.
<!-- SECTION:FINAL_SUMMARY:END -->
