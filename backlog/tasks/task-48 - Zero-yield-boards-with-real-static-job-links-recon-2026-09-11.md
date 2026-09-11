---
id: TASK-48
title: Zero-yield boards with real static job links (recon 2026-09-11)
status: Done
assignee: []
created_date: '2026-09-11 10:48'
updated_date: '2026-09-11 13:05'
labels: []
dependencies: []
ordinal: 48000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
During the full 220-board delivery pass, 43 boards returned raw=0 or kept=0. A quick curl+grep recon (no JS) against each board's exact careers_url found 15 of them DO have static, JOB_PATH-matching href links on the page (job_hrefs>0), yet the adapter still returned 0 rows. This means either the ats_type/adapter routing is wrong for these, the seed_for()/detection step (e.g. softgarden's SG_HOST regex) fails to fingerprint the vendor, or the matched hrefs point at content that is itself JS-hydrated at fetch time (confirmed root cause for hessing-kliniken.de, see crawlers/vendor_adapters.py comment near JOB_PATH -- its 3 job links needed an html.unescape fix already applied this session but still return a generic page shell, not real job content, because the actual data loads via a client-side softgarden widget call). Each board needs its own quick investigation: check ats_type routing, check whether seed_for()/find_host() actually fires, and check whether the detail page needs the widget's real AJAX endpoint instead of the static href.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each board below is triaged: fixed with a registry/routing change, OR confirmed to need widget/API reverse-engineering and filed as its own follow-up
- [x] #2 boards, beds, and job_hrefs count (from recon): schwesternschaft-muenchen.de(435,1) karriere-barmherzige-muenchen.de(404,11) karriere.ameos.eu(358,11) karriere.kirinus.de/56413-Nuernberg-branch(295,71, already fixed 2026-09-11) www.kh-as.de(180,3) karriere.barmherzige.net(174,68) www.hessing-kliniken.de(150,3, JS-widget confirmed) www.spezialklinik-neukirchen.de(140,6) www.kreisklinik-woerth.de(125,3) www.klinik-feldafing.de(115,5) www.fachklinik-sankt-lukas.de(80,2) www.augenklinik-muenchen.de(47,3) www.orthopaedie-manufaktur.de(40,5) www.waldhausklinik.de(40,3) klinik-steger.de(25,5)
- [x] #3 No new adapter code added without confirming the real job content is reachable without a browser -- a page having href='...job...' text does not guarantee its content is server-rendered
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2 of 15 boards fixed 2026-09-11: both had a wrong ats_type in the registry, not a genuine adapter gap. 16214 (Krankenhaus Barmherzige Bruder Muenchen, 404 beds) was labelled softgarden but is a plain static site with clean /stellenanzeige/<slug> detail links -- blanked ats_type so it falls back to the generic wp_jobs route; delivered 23 real pflege postings (0 before). 16219 (Krankenhaus Neuwittelsbach, 122 beds) shares karriere.barmherzige.net with 16226 (Maria-Theresia-Klinik, correctly labelled personio) -- same board, same vendor, just a different ?filter[company][] query value; 16219 was mislabelled softgarden, relabelled to personio; delivered 5 real pflege postings (0 before). Remaining 13 boards in this task not yet triaged.

2026-09-11 full triage of all 15 boards, in order of the original AC list:

FIXED, delivered live:
- schwesternschaft-muenchen.de (435 beds): CONFIRMED not fixable quickly -- concludis JS widget, page literally says "Stellenangebote werden geladen..." (loading placeholder). No static job content, no discoverable API endpoint in the static HTML (checked for ajax/api patterns, found only unrelated news/events endpoints). Deferred, matches the concludis limitation already documented in vendor_adapters.py's module docstring.
- karriere-barmherzige-muenchen.de (404 beds): FIXED earlier this session -- wrong ats_type (softgarden), blanked to fall back to wp_jobs. 23 real postings delivered.
- karriere.ameos.eu (358+60 beds after merging Inntal in): FIXED -- two bugs found and fixed: (1) AMEOS's own clinic 27706 (Inntal) had a wrong careers_url pointing at the portal homepage instead of the real /offene-stellen/ listing, now merged into the shared board; (2) a real regression I introduced earlier this session (nested <span itemprop="title"> inside job-link anchors was silently dropped by a stricter anchor-text regex) -- fixed in _job_link_pairs. Delivered live: raw=772, kept=204, matched=204/204 (all to clinic 18501 Neuburg -- see TASK-51 note on the org-name-defaulting pattern this likely reflects).
- www.kh-as.de (180 beds): CONFIRMED genuinely empty -- page body is literally "Stellenangebote powered by webEdition CMS", no content at all.
- www.hessing-kliniken.de (150 beds): CONFIRMED not fixable quickly -- real per-job detail links exist (TYPO3 softgarden query-string embed, already fixed the &amp; unescape bug for these) but the detail page itself is JS-hydrated (AJAX-loaded from the softgarden widget, no server-rendered content at all). Needs the widget's real AJAX endpoint or Playwright.
- www.spezialklinik-neukirchen.de (140 beds): CONFIRMED genuinely no pflege opening -- one real inline posting exists ("Patientenverwaltung / Empfang", admin/reception), not pflege-relevant. No fix needed.
- www.kreisklinik-woerth.de -- NOT on the original AC list but found during recon of the same shape: CONFIRMED genuinely empty ("Auch wenn zur Zeit keine spezielle Stelle... ausgeschrieben ist").
- www.klinik-feldafing.de (115 beds, 2 clinics sharing the URL): one side (18872, smartrecruiters/ArtemedSE) is a decoy -- real jobs already covered via the shared Artemed jobs.smartrecruiters.com board (same non-bug as klinik-vincentinum.de, see TASK-49). Other side (18813, bite) uses a newer bite embed generation with no embedded API key -- filed as TASK-55, not fixed here.
- www.fachklinik-sankt-lukas.de (80 beds): CONFIRMED genuinely empty -- generic "send us your CV" copy only, no specific posting.
- www.augenklinik-muenchen.de (47 beds): FIXED -- postings are PDF flyers linked directly off the listing page, anchor text carries the real title. Added generic PDF-title-from-anchor-text support to crawl_wp_jobs/_wp_job_rows (also fixed a stage-ordering bug that could permanently drop a PDF discovered by the sitemap stage before its title was known). Delivered live: 3/3 matched.
- www.orthopaedie-manufaktur.de (40 beds, clinic 19001 Krankenhaus Schongau): FIXED -- the registered careers_url pointed at a completely unrelated orthopedic-equipment retail business, not the hospital at all. Real board is the same meinkrankenhaus2030.de board already used by sibling clinic 19002 (Weilheim, same operator). Repointed and merged; this also surfaced and fixed TASK-52's title-extraction bug (HubSpot template writes "Stellenanzeige | <real title>", code was taking the wrong segment). Delivered live: 2/2 matched.
- www.waldhausklinik.de (40 beds): FIXED -- postings rendered as an FAQ-accordion (no JSON-LD, plain HTML with faqAccCard/jobHeadmain/postjobFlex/faqAccCardBody classes). Added a dedicated parser (_faq_accordion_job_rows). Delivered live: 4/11 kept, 1 created.
- klinik-steger.de (25 beds): FIXED -- postings rendered as one FAQPage JSON-LD block (WordPress "Ultimate Addons for Gutenberg" FAQ block, reused as a job board). Added generic FAQPage JSON-LD support (_faqpage_job_rows) -- reusable by any other WP site using the same plugin trick, not just this one. Delivered live: 3/5 kept, 1 created.

Net: 6 of 15 boards had a real, now-fixed bug (2 registry/URL, 1 mislabelled ats_type, 3 code gaps -- PDF titles, FAQPage JSON-LD, FAQ-accordion HTML, plus the meinkrankenhaus2030 title-split bug and the AMEOS nested-span regression). 5 confirmed genuinely empty right now (not bugs). 2 deferred as real JS-widget gaps (schwesternschaft-muenchen concludis, hessing-kliniken softgarden AJAX). 1 split into a decoy (not a bug) + a new task (TASK-55, bite v5).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
15 boards triaged. 6 real bugs found and fixed (delivered live): 2 wrong careers_url, 1 mislabelled ats_type, and 3 new parser capabilities added to crawl_wp_jobs (PDF-linked postings, FAQPage JSON-LD job boards, FAQ-accordion HTML job boards) plus a title-extraction bug and a same-session regression, both fixed. 5 boards confirmed genuinely empty right now, not bugs. 2 deferred as real JS-widget gaps needing Playwright (schwesternschaft-muenchen, hessing-kliniken). Feldafing split: one side is a non-bug decoy, other side filed as TASK-55 (bite v5 API gap).
<!-- SECTION:FINAL_SUMMARY:END -->
