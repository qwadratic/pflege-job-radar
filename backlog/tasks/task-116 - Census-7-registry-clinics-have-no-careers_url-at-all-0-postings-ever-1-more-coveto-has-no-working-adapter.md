---
id: TASK-116
title: >-
  Census: 7 registry clinics have no careers_url at all (0 postings ever); 1
  more (coveto) has no working adapter
status: Done
assignee:
  - '@ivan'
created_date: '2026-09-22 18:27'
updated_date: '2026-09-24 11:24'
labels: []
dependencies: []
ordinal: 116000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-22 never-posted census: compared the full live 407-row pflege_jobs.clinics registry (rest_get, clinic_id/careers_url/ats_type) against every distinct clinic_id that has EVER appeared in pflege_jobs.v_postings (all statuses, no status filter -- 264 distinct clinic_id out of 407). 143 clinic_ids have zero postings across all of history. Running crawlers.routing.plan() on the live registry against that 143-clinic set: 7 clinics have careers_url == '' (crawlers/routing.py:141 'no careers_url' unroutable reason) -- 26105 Krankenhaus Landshut-Achdorf, 27773 AMEOS Klinikum Inntal Simbach am Inn, 37102 St. Johannes-Klinik Auerbach, 56406 Cnopf'sche Kinderklinik Nürnberg, 67402 Haßberg-Kliniken Haus Ebern, 67802 Krankenhaus Markt Werneck, and 17274 Klinik für Schlafstörungen (Bad Reichenhall). One more, 16262 Algesiologikum Tagesklinik München, has careers_url but ats_type='coveto', which has no entry in routing.py's ADAPTERS table (crawlers/routing.py:150 'no adapter for coveto'). Two of these 8 are already explained, not gaps: 17274 and 16262 were each individually checked by the firecrawl_agent hunter this session (data/app.sqlite crawl_issues kind='firecrawl', run_id 122, 2026-09-22) -- 17274's operator site (schlaflabor-bayern.de) has no careers page and the public hospital directory confirms it has no listings; 16262's Coveto board (k19368.coveto.de) is live and complete with 9 current listings, none of them nursing. The other 6 are unverified: nobody has checked whether they even have a public careers page under a different URL. Separately, 23 more never-posted clinics have careers_url present but ats_type='' -- these are NOT stuck (crawlers/routing.py:143-149 already defaults any url-but-no-vendor-label board to the generic wp_jobs crawler), so they are lower priority; listed here so a census run that fingerprints one of them doesn't have to re-derive that they were already checked. This is investigation-only census data, not a mass-implementation task -- do not fix all 399 clinics in this task; see AC for scope.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each of the 6 unverified no-careers_url clinics (26105, 27773, 37102, 56406, 67402, 67802) gets one manual check: does the operator have a public careers/Stellenangebote page at all; if yes, add careers_url (+ ats_type if fingerprinted) to pflege_jobs.clinics via the registry correction tool (tools/apply_registry_corrections.py pattern), if no, record that finding (same shape as the existing 17274/16262 firecrawl verdicts) so future census runs don't re-check it
- [x] #2 16262's ats_type='coveto' either gets a routing.py ADAPTERS entry (if Coveto boards turn out common enough to be worth an adapter) or gets documented as a deliberately-unsupported vendor with a comment explaining why, so 'no adapter for coveto' stops looking like an open question
- [x] #3 The 23 has-url-no-ats_type clinics are left alone by this task (already routed via wp_jobs fallback) -- just recorded here as context, no action required
- [x] #4 Report which of the 6 unverified clinics turned out to genuinely have zero public job postings (small/specialty clinics, same shape as 17274/16262) vs which needed a real registry fix
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Live-check operator/website for each of 6 unverified no-careers_url clinics (26105, 27773, 37102, 56406, 67402, 67802) via pflege_jobs.clinics.operator + the operator's own site (WebSearch/WebFetch/requests+fingerprint()).
2. For any real careers page found, fingerprint the ATS vendor with crawlers/ats_discover2.py's fingerprint(), cross-check against sibling clinics on the same operator/board already in the registry, and confirm the board actually yields postings (v_postings count on the sibling, or a live crawl of the exact adapter).
3. Write confirmed corrections via a tools/task116_apply_6_clinic_fixes.py script following the tools/apply_registry_corrections.py pattern (backup live rows, write only changed columns, read back and verify).
4. For clinics with no real public careers page, do not write anything -- record the finding in backlog notes.
5. AC#2 (16262 coveto): COUNT clinics with ats_type=coveto live; if only 1, document coveto as deliberately unsupported with a comment in crawlers/routing.py rather than building an adapter.
6. AC#3: leave the has-url-no-ats_type clinics untouched, just confirm count.
7. Close out with detailed notes separating genuine zero-posting clinics from real registry fixes.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Applied 4 of 6 confirmed corrections live via tools/task116_apply_6_clinic_fixes.py (backup: backups/task116_corrections_before_20260924T112013Z.json), verified by independent psycopg2 read + crawlers.routing.plan() re-run against the live registry (all 4 now route, previously 'no careers_url'):
- 26105 Krankenhaus Landshut-Achdorf -> careers_url=https://www.la-regio-kliniken.de/stellenportal, ats_type=typo3_jobs. Operator La.KUMed; lakumed.de/beruf-karriere/stellenangebote 301-redirects here. Shared board with sibling 26101 Klinikum Landshut (already typo3_jobs on this exact URL); the page explicitly lists 'Klinik Landshut-Achdorf' (40 mentions) as one of its site cards.
- 37102 St. Johannes-Klinik Auerbach -> careers_url=https://www.kh-as.de/ausbildung-karriere/stellenangebote/ (ats_type left blank, matching sibling). Operator KU Krankenhaeuser des Landkreises Amberg-Sulzbach; shared board with sibling 37101 St. Anna Krankenhaus Sulzbach-Rosenberg (ats_type='', wp_jobs fallback, 9 live postings in v_postings). Page footer names 'St. Johannes Klinik ... Auerbach/OPf.'.
- 56406 Cnopf'sche Kinderklinik Nuernberg -> careers_url=https://www.klinik-hallerwiese.de/de/allgemeines/unternehmen/karriere/stellenangebote.html, ats_type=bite. Operator DIAKONEO. Live fingerprint via crawlers/ats_discover2.py's fingerprint() found a real B-ITE widget (static.b-ite.com script) directly on the clinic's own page -- NOT the same as sibling 56404's stale Instagram/workday entry. Live walk_all_postings: customer=diakoniewerk-schwaebisch-hall, listing=klinik-hallerwiese-cnopfsche-listing, 224 total postings, 31 in Nuernberg, 21 of those nursing (incl. 'Gesundheits- und Kinderkrankenpfleger ... Kinderonkologie', matching the clinic's pediatric specialty). No bite_seeds.json entry needed -- auto-detects like 36101/36102.
- 67402 Hassberg-Kliniken Haus Ebern -> careers_url=https://www.hassberg-kliniken.de/informationen/karriere-beruf.html, ats_type=mein-check-in. Operator KU Hassberg-Kliniken. Shared board with sibling 67401 Haus Hassfurt (already mein-check-in). Page's own JSON-LD explicitly lists both 'Hassberg-Kliniken Haus Hassfurt' and 'Hassberg-Kliniken Haus Ebern' as the same Hospital org's two locations (27 'ebern' mentions incl. the JSON-LD block itself).

AC#2 (16262 coveto): live SQL COUNT confirms exactly 1 of 407 registry clinics has ats_type='coveto' -- 16262 Algesiologikum Tagesklinik Muenchen itself, no other. Documented as deliberately unsupported with a comment in crawlers/routing.py (added just after the ADAPTERS dict, next to the existing WALLED/FALLBACK_VENDORS documentation pattern) rather than building an adapter -- matches the task's own framing (single clinic, 0 qualifying nursing vacancies on its board per the 2026-09-22 firecrawl_agent check already on file). Not added to ADAPTERS, so plan() still reports 'no adapter for coveto' -- that is now an intentional, documented state, not an open question. tests/test_routing.py still 9/9 passing after the edit (comment-only, no ADAPTERS/plan() logic touched).

AC#3 (23 has-url-no-ats_type clinics): confirmed live, not touched. Current count among the census's 143-never-posted set is 26 (was 23 at the 2026-09-22 census; small drift is expected -- registry corrections since then, including this task's own writes, move rows in and out of the never-posted set). All already route via the wp_jobs fallback per crawlers/routing.py:143-149; no action taken on any of them.

67802 Krankenhaus Markt Werneck -- verdict: genuinely NO public careers page, nothing written (matches the existing 17274/16262 shape). Registry website https://www.krankenhaus-werneck.de returns HTTP 404 (confirmed live via WebFetch). Operator field 'KU Krankenhaus Markt Werneck' is distinct from the three other, unrelated Werneck-area hospitals already correctly in the registry (66205 Tagesklinik Schweinfurt des BKH Werneck, 67803 Orthopaedisches Krankenhaus Schloss Werneck, 67804 Bezirkskrankenhaus Werneck -- all ats_type=helix, a different operator, the Bezirk Unterfranken psychiatric/orthopaedic complex). Public sources (kma-online.de, mydrg.de, meincharivari.de -- 'Werneck: Marktkrankenhaus schliesst zum Jahresende') confirm the Marktkrankenhaus Werneck (this clinic, 'KU Krankenhaus Markt Werneck') transferred its inpatient surgical/orthopaedic services to Krankenhaus St. Josef Schweinfurt and closed. No careers page exists to add.

- 27773 AMEOS Klinikum Inntal (Simbach am Inn) -> careers_url=https://karriere.ameos.eu/offene-stellen/, ats_type=typo3_jobs. Operator AMEOS Klinikum Inntal GmbH. Same exact URL as sibling 18501 AMEOS Klinikum St. Elisabeth Neuburg, which already runs typo3_jobs on this board with 79 live postings in v_postings -- a proven-working board, not a guess. Independently confirmed live: the location-filtered variant of the same AMEOS TYPO3 system (.../offene-stellen?...searchWords=Simbach+am+Inn) returns 24 'simbach' + 6 'pflege' text mentions, i.e. Simbach am Inn nursing postings genuinely exist inside this same AMEOS board that 18501 already crawls successfully. A full live crawl_wp_jobs() re-run against the exact base URL to directly confirm Simbach rows in the unfiltered extraction was started (a real multi-hundred-location AMEOS-network crawl with politeness-limited per-job detail fetches) but had not finished after 10+ minutes -- did not block this fix on it, since the operator-match + proven-sibling-board + confirmed-postings-exist-in-system evidence chain is the same strength already used for the shared-board Landshut/Auerbach/Ebern fixes above. routing.plan() confirms 27773 now routes correctly (board shared with 18501, 2 clinics).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
5 of the 6 unverified no-careers_url clinics had a real public careers page and got a registry fix, all applied live via tools/task116_apply_6_clinic_fixes.py (backup in backups/, every write independently re-verified with a fresh psycopg2 read + a crawlers.routing.plan() re-run against the live registry, confirming all 5 now route with no adapter, all previously 'no careers_url'):
  26105 Krankenhaus Landshut-Achdorf -> la-regio-kliniken.de/stellenportal, typo3_jobs (shared board with sibling 26101, page names the clinic by name)
  37102 St. Johannes-Klinik Auerbach -> kh-as.de/ausbildung-karriere/stellenangebote/, blank ats_type (shared board with sibling 37101, 9 live postings)
  56406 Cnopf'sche Kinderklinik Nürnberg -> klinik-hallerwiese.de/.../stellenangebote.html, bite (own B-ITE widget, live walk: 224 total / 31 Nürnberg / 21 nursing postings)
  67402 Haßberg-Kliniken Haus Ebern -> hassberg-kliniken.de/.../karriere-beruf.html, mein-check-in (shared board with sibling 67401, JSON-LD names both locations)
  27773 AMEOS Klinikum Inntal (Simbach am Inn) -> karriere.ameos.eu/offene-stellen/, typo3_jobs (same board as proven-working sibling 18501, 79 live postings; Simbach postings independently confirmed to exist in this AMEOS system via the location-filtered URL)

1 of the 6 genuinely has zero public postings and nothing was written (same shape as the existing 17274/16262 verdicts):
  67802 Krankenhaus Markt Werneck -- registry website 404s live; public sources confirm this specific facility (Marktkrankenhaus Werneck, distinct operator from the unrelated Schloss Werneck complex already in the registry) transferred services to Krankenhaus St. Josef Schweinfurt and closed.

AC#2: live COUNT confirms exactly 1 of 407 clinics uses ats_type=coveto (16262 itself). Documented as deliberately unsupported with a comment in crawlers/routing.py (comment-only change, tests/test_routing.py still 9/9 green) rather than building an adapter for a single zero-nursing-vacancy board.

AC#3: confirmed live, not touched -- 26 has-url-no-ats_type clinics currently in the never-posted set (23 at the 2026-09-22 census; small drift from registry churn since, including this task's own writes), all already routed via the wp_jobs fallback.

No unit test exists for this kind of one-off registry-correction script anywhere in tools/ (apply_registry_corrections.py, apply_allgaeu_ats.py, task117/120 scripts are all the same shape, none covered by pytest) -- verification instead used the script's own built-in backup+write+read-back, plus an independent psycopg2 read and a full routing.plan() re-run, both confirming the live registry state matches intent. The routing.py change is comment-only (no logic touched), so no mutation test applies there; existing test_routing.py suite stayed green across the edit.
<!-- SECTION:FINAL_SUMMARY:END -->
