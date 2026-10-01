---
id: TASK-170
title: >-
  Reha sites with 0 vacancies (B3): dead DRV Bund clinic domains with no adapter
  for drv-bund-karriere.de, GSB search forms stored as phantom postings,
  MEDIAN/Am Kurpark/Hessing careers_url on job-less pages
status: Done
assignee:
  - '@claude'
created_date: '2026-09-29 18:52'
updated_date: '2026-09-29 21:39'
labels:
  - crawler-coverage
  - bug
dependencies: []
priority: high
type: bug
ordinal: 168000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Nightly run 217 (2026-09-29) left these Reha registry sites at 0 open postings: DRV Klinik Bad Reichenhall RH2255, Klinik Höhenried RH2229, Reha-Zentrum Bad Aibling Klinik Wendelstein RH1576, Rehafachzentrum Bad Füssing RH2386, MEDIAN Buchberg Klinik Bad Tölz RH2655, Reha-Klinik Am Kurpark Grafenau RH1138, Hessing Reha RH1585/RH2724 ('no softgarden host found'). Ivan asked why each shows 0 and to fix what is broken. The DRV family has 25 registry rows spread over 8 regional carriers, each publishing jobs in a different place (clinic GSB sites, carrier portals drv-bund-karriere.de / drv-bayernsued-karriere.de / drv-nordbayern-karriere.de / drv-schwaben-karriere.de, a B-ITE board for DRV BW), so the task has to separate genuinely empty boards from unreadable or misrouted ones, family-wide.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every DRV, MEDIAN, Hessing and Am-Kurpark registry row has its live nursing-vacancy count stated with evidence, and each 0 is classified as genuine (board empty of nursing) or a crawler/registry defect
- [x] #2 Each defect found is fixed in code (red-green test from a live-captured fixture, mutation-tested) and/or a prepared registry correction (corrections JSON for tools/apply_clinic_corrections.py plus the matching data/registry/clinics.csv patch)
- [x] #3 A proving crawl from the worktree (registry corrections applied in memory only) shows before -> after open-posting counts per clinic_id, queried live
- [x] #4 Postings that must be retired or relinked are listed by posting_id for Ivan's approval, not written
- [x] #5 Offline suite ends 0 failed
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Live oracle per family: DRV Bund portal drv-bund-karriere.de (197 jobs, per-location filter), DRV Bayern Süd / Nordbayern / Schwaben portals, clinic GSB pages, MEDIAN portal karriere.median-kliniken.de (location route /de/jobs/0/0/<id>/), kurpark.mutter-kind.de, hessing-kliniken.softgarden.io -- count nursing postings per clinic BEFORE touching code.
2. Code (worktree grpB3, red-green + mutation): (a) NOT_JOB_PATH excludes GSB /SiteGlobals/Forms/ search-form URLs (phantom duplicates on every DRV GSB site); (b) new crawl_drv_bund adapter reading the location-filtered drv-bund-karriere.de listing (title + Ort per item, 0-based ?page=N until no new link) -- crawl_wp_jobs on that host walks the whole nationwide sitemap and stamps every job with the seed town; (c) Hessing: title names the Reha house -> employer text, since content matching (R3_tokens_bestj) otherwise files every 'Hessing Stiftung' posting under 76111.
3. Registry corrections JSON + clinics.csv patch: RH1498/RH1576/RH2930/RH2161 -> drv_bund + filtered portal URL; RH2255/RH2843 -> DRV Bayern Süd portal (their own pages only link to it); RH2655/RH1480 -> MEDIAN location-filtered listing; RH1138 -> kurpark.mutter-kind.de/stellenangebote; RH1585/RH2724 -> hessing-kliniken.softgarden.io/de/vacancies (same board as 76111).
4. Proving crawl from the worktree with corrections in memory, retirement disabled; before/after open counts per clinic_id; list posting ids to retire/relink.
5. Suite 0 failed; notes + patch paths.

Added during the work (the live oracle showed these defects too):
6. crawl_median: a dedicated adapter for the MEDIAN portal's location listing plus its load-more JSON. crawl_wp_jobs on that listing stored junk root and filter pages as postings.
7. crawl_onapply: a crawl_wp_jobs delegate for the onapply career-page widget (Höhenried), plus a CITY_ALIASES entry for Bernried am Starnberger See = Bernried/Obb.
8. patterns.json: grounds-keeping "-pflege" compounds (Park-, Garten-, Grünflächen-, Grünanlagen-, Landschaftspflege) are nicht_pflege.
9. parse_job_page: a cookie-dialog <h1> is never the title (kurpark.mutter-kind.de).
10. Matcher: R3's same-operator bed-count guess (R6) waits for R4, the operator-token rung (DRV BW RH2467). Replayed over runs 217 and 208 before shipping. A second variant, "a decisive board beats R6", was measured and rejected: it produced a wrong Bamberg re-link and 100 label churns.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## TASK-170 evidence log (grpB3 worktree, 2026-09-29) -- read this first

### Live oracle per registry row (nursing = experienced-nursing roles the intake keeps)
Source of each count: the board read in proving run 219 (27 boards, 31 clinic_ids, from the worktree, registry corrections in memory, retirement disabled), cross-checked by hand against the live page/portal the same day. Before/after = open postings per clinic_id queried live from `postings` (snapshots /tmp/grpB3/before.json, after.json, after2.json).

**DRV Bund -- carrier portal drv-bund-karriere.de (Drupal Views, `?field_location=<id>`, 0-based `&page=N`, "keine Treffer" = the board's own end).** Location ids read off the portal's own filter `<select name="field_location">` (313 options): 45 Bad Aibling, 47 Bad Brückenau, 54 Bad Kissingen, 64 Bad Steben, 69 Bayerisch Gmain.
- RH1498 Hartwald (Bad Brückenau): DEFECT. careers_url www.reha-klinik-hartwald.de -> 302 to a www host with NO DNS A record; crawl_issues 09-26..09-29 every night "transport failure: 0/8 request(s)". Portal loc 47: 9 jobs, 2 nursing. Open 0 -> 2 (15294 Pflegefachkraft, 15295 Leitende Pflegefachkraft, R2_operator_town, verify live).
- RH1576 Wendelstein (Bad Aibling): same dead-DNS defect; portal loc 45 = "keine Treffer" -> GENUINE 0 (now recorded loudly as crawl_issue kind=empty instead of a transport failure).
- RH2930 Hochstaufen (Bayerisch Gmain): same dead-DNS defect; portal loc 69: 2 jobs (Betriebshandwerker*in, Küchenhilfskraft), 0 nursing -> GENUINE 0.
- RH2161 Bad Steben: DEFECT. careers_url was the NATIONWIDE DRV Bund Reha-Zentren listing (deutsche-rentenversicherung.de/.../reha-zentren-node.html); its only open posting 15097 "Gesundheits- und Krankenpfleger*in in einem Rehabilitationszentrum" is a nationwide pool ad ("sucht für ihre Reha-Zentren in ganz Deutschland", no Bad Steben anywhere on the page) stamped with the seed town. Portal loc 64: 1 job (Oberarzt), 0 nursing -> GENUINE 0; 15097 -> retire list.
- RH1901 Klinik Saale / RH2954 Klinik Rhön (Bad Kissingen, own GSB sites): 1 real PFK each (SharedDocs/Stellenangebote/.../PFK), stored and still open. DEFECT: 3 phantom open rows EACH (15271-15273, 15276-15278) = the GSB job SEARCH FORM (/SiteGlobals/Forms/Stellenanagebotssuche/...?search_coordinates.HASH=<new token per request>) matched JOB_PATH, first listed job became the title, the per-request token made a new "posting" every night (30 more already expired). Fixed in NOT_JOB_PATH; phantoms -> retire list.

**DRV Bayern Süd -- portal drv-bayernsued-karriere.de (typo3_jobs).** 30-32 jobs live, 0 nursing.
- RH2255 Bad Reichenhall, RH2843 Tegernsee: DEFECT (registry). Their own GSB pages only link to the portal: both read 0 rows, "empty"+"degraded" every night 09-26..09-29. Moved onto the one portal board (same URL string as RH1449, so routing plans ONE board for all three). GENUINE 0.
- RH1449 Gaißach: already on the portal. GENUINE 0. (The portal's "degraded discovery (sitemap_and_wp_json_empty)" crawl_issue is pre-existing: recorded every night 09-26..09-29 for RH1449.)
- RH2386 Bad Füssing (rehafachzentrum.de, GSB): 4 rows, 0 nursing -> GENUINE 0 (its "degraded" issue is also pre-existing, 09-26..09-29).
- RH2229 Höhenried (Bernried): DEFECT, now read. hoehenried.de's Stellenangebote page renders no job link: two `onapply-career-page-container` widgets are filled client-side from JSON feeds (hoehenried.onapply.de 13 listings + cep-hoehenried.onapply.de 1). Nightly result was "empty"+"degraded" (09-26..09-29). Now 14 rows read; the only Pflege listing is "Initiativbewerbung Pflegefachkraft" (posting text: all positions filled) -> classifier's speculative-application rule keeps it off -> GENUINE 0. Also: every listing names "Bernried am Starnberger See", the registry (RHV) says "Bernried/Obb." -> town keys apart, single-clinic board refused every row -> CITY_ALIASES entry.

**DRV Nordbayern / Schwaben / Knappschaft / Berlin-Brandenburg / Oldenburg-Bremen** (boards read in run 219, rows / nursing): RH1090 Bischofsgrün 5/1 (open 1 -> 1, unchanged), RH1652 Frankenwarte 2/0 (recovered on retry 2), RH1775 Frankenklinik 2/0, RH1791 Herzoghöhe 1/0, RH2278 Sinntalklinik 2/0, RH2294 Frankenland 4/0, RH2766 Ohlstadt 4/0, RH1330 Bad Wörishofen 1/0 ("derzeit sind leider keine offenen Stellen vorhanden"), RH2069 Lindenberg-Ried 1/0, RH2243 Oberstdorf 1/0, RH2813 Chiemgau-Klinik 1/0, RH2031 Lautergrund 5/0, RH1070 Marbachtal 3/0 -> all GENUINE 0 (DRV Schwaben portal itself says "Pflege: Derzeit keine Angebote").

**DRV Baden-Württemberg -- RH2467 Rehaklinik Am Kurpark (Bad Kissingen), b-ite board karriere.rehazentren-bw.de:** DEFECT (matcher). 50 postings, 2 nursing in Bad Kissingen (Pflegedienstleitung, Pflegefachkraft), employer "RehaZentren der Deutschen Rentenversicherung". R3 name tokens tie the two DRV BUND houses RH1901/RH2954 on "deutschen rentenversicherung" -> same-operator bed-count guess R6_ambiguous_sites:RH1901,RH2954 returned before R4 (operator tokens), which names RH2467 alone. Result: 15160/15161 open on RH1901, RH2467 at 0. Fix below (item 9); proving run 220 (RH2467's board, 50 observations, 2 kept): 15160 Pflegedienstleitung + 15161 Pflegefachkraft relinked by the normal drain to RH2467 (R4_tokens_op, verify live). Open RH2467 0 -> 2, RH1901 6 -> 4 (1 real PFK + the 3 phantoms), RH2954 4 -> 4.

**MEDIAN -- portal karriere.median-kliniken.de (TYPO3, `/de/jobs/0/0/<location id>/`, widget load-more JSON `{html, more}`).**
- RH2655 Buchberg (Bad Tölz): DEFECT. careers_url = the clinic's page on www.median-kliniken.de (6000-url sitemap, no posting; "empty"+"degraded" nightly). Loc 62: 8 jobs, 1 nursing -> open 0 -> 1 (15296 "Examinierte Pflegefachkraft (m/w/d)", R3_tokens, verify live).
- RH1480 Frankenpark (Bad Kissingen): DEFECT (portal start page, "degraded" nightly). Loc 126: 2 jobs (Masseur, Physiotherapeut), 0 nursing -> GENUINE 0.
- crawl_wp_jobs on the location listing would also store the unfiltered /de/jobs/ root and the canonical filter page as "postings" (titled with another site's first job, seed-town stamped) -> dedicated adapter.

**RH1138 Reha-Klinik Am Kurpark (Grafenau):** DEFECT (registry). careers_url www.klinik-kurpark.de/ (no job; "empty"+"degraded" nightly). Real board kurpark.mutter-kind.de/stellenangebote: 6 postings (Diätassistent, Facharzt, Oberarzt, Sport-/Gymnastiklehrer, Initiativbewerbung, Praktikumsplätze), 0 nursing -> GENUINE 0. Second defect on that board: its cookie dialog is the FIRST `<h1>` on every job page -> 2 rows titled "Diese Website verwendet Cookies", the other 4 kept the `<title>` label "Stellenangebot: ...". Fixed in parse_job_page; live re-read after the fix: all 6 titles clean.
(RH1970 "PARITÄTISCHE Haus am Kurpark" and RH1087 "Residenz im Kurpark" are other operators, outside B3 -- see follow-ups.)

**Hessing (Augsburg):** DEFECT (registry + attribution). RH1585 / RH2724 had hessing-kliniken.de pages + ats_type softgarden -> "no softgarden host found on careers page" every night 09-26..09-29. Their postings live on the Hessing Stiftung's one softgarden board (76111's, hessing-kliniken.softgarden.io/de/vacancies, 82 rows) and the content matcher filed every "Hessing Stiftung" row under 76111 (R3_tokens_bestj). Fix: same board for all three + org override from the title (app/crawl.py, mirrors the München Klinik TASK-129 override). 6403 "Pflegefachkräfte ... orthopädische Rehabilitationsklinik" 76111 -> RH1585 (R1_exact), 6397 "Gesundheits- und Krankenpfleger/Altenpflegekraft/Pflegefachkraft ... geriatrische Rehabilitation" 76111 -> RH2724 (R1_exact). Open: RH1585 0 -> 1, RH2724 0 -> 1, 76111 20 -> 18.

### Code changes (worktree grpB3; red-green from live-captured fixtures; mutation-tested)
1. crawlers/vendor_adapters.py NOT_JOB_PATH += `/SiteGlobals/Forms/` (GSB search form). Live A/B earlier today over all DRV GSB boards: 35 phantom rows gone on 9 boards, 0 real SharedDocs postings lost. Test: test_gsb_search_form_links_on_a_drv_careers_page_are_not_postings.
2. crawl_drv_bund (new, VENDORS + routing ADAPTERS "drv_bund"): location-filtered listing, title + Ort per item from the listing, description from the detail page; stops on the first page with no new item (the portal's own "keine Treffer"); page 0 unreadable -> [] (app/crawl.py records the transport failure); a later page unreadable -> RuntimeError (never a short read passed as complete); a failed detail keeps the listing row and is reported in page_crashes. tests/test_drv_bund.py.
3. crawl_median (new, "median"): location listing + every load-more page until the JSON names no `more`; detail JSON-LD; failed load-more -> RuntimeError; failed detail -> page_crashes. tests/test_median.py.
4. crawl_onapply (new crawl_wp_jobs delegate, probed on the careers page like asklepios/erecruiter/concludis): every `onapply-career-page-container` feed; board_total = listings; department -> section_labels; unreadable feed -> RuntimeError. tests/test_onapply.py.
5. app/crawl.py + vendor_adapters HESSING_CLINIC_IDS / hessing_reha_house(): a Hessing-board row whose title names exactly one Reha house gets that house's registry name as org. tests/test_vendor_account_pools.py (+ a non-Hessing board stays untouched).
6. pflege_jobs/registry.py CITY_ALIASES "bernried starnberger see" -> "bernried/obb.".
7. pflege_jobs/patterns.json nicht_pflege += (park|garten|grünflächen|grünanlagen|landschafts)pflege (Höhenried's "Mitarbeiter in der Parkpflege" was sonstige_pflege; same gap on open 14939, 15043, 15044, 15046 -- corrected on their next observation, not written).
8. parse_job_page: skip an `<h1>` that is a cookie dialog, take the next one. Fixtures kurpark_*_sample.html; test_parse_job_page_skips_a_cookie_consent_h1_for_the_jobs_own_h1.
9. pflege_jobs/registry.py _match_content: R3's same-operator bed-count guess (R6) now waits for R4; returned only if R4 does not decide. Replayed with the real drain arguments/gates/board pools over run 217 (3529 matched rows, production data/inbox.sqlite read-only): exactly 2 rows change -- the 2 RH2467 postings -- and 0 label-only changes. Same replay over run 208 (3553 matched rows): the same 2 postings, nothing else. Rejected alternative measured the same way ("a decisive board beats R6"): 4 clinic changes incl. a wrong one (dialysis posting 229543 from Klinikum Bamberg 46101 to the Saludis Reha RH1611) and 100 R6 -> R0_board_* label churns; not shipped.
Mutation tests: 25 mutations (M01-M19 adapters/routing/crawl/alias/patterns, M20 cookie h1, M21-M22 the rejected board variant before it was reverted, M23-M25 R6 deferral incl. "first guess stands") -- each: file copied to /tmp, fix broken, targeted tests RED, restored FROM the /tmp copy, __pycache__ deleted, GREEN, `diff -q` byte-identical. Logs /tmp/grpB3/mutation.log, mutation2.log, mutation3.log.

### Registry corrections (NOT written -- Ivan approves)
/tmp/grpB3/task170_registry.json (12 clinics) + matching data/registry/clinics.csv rows in the patch (CRLF kept; separate /tmp/grpB3/b3_registry_csv.patch). `tools/apply_clinic_corrections.py /tmp/grpB3/task170_registry.json --dry-run` against live: all 12 ids found; diff = RH1498/RH1576/RH2930/RH2161 -> drv-bund-karriere.de ?search=&field_location=47/45/69/64 + drv_bund; RH2255/RH2843 -> https://www.drv-bayernsued-karriere.de/stellenangebote/?no_cache=1 + typo3_jobs; RH2655 -> .../de/jobs/0/0/62/ + median; RH1480 -> .../de/jobs/0/0/126/ + median; RH1138 -> https://kurpark.mutter-kind.de/stellenangebote; RH1585/RH2724 -> https://hessing-kliniken.softgarden.io/de/vacancies + wp_jobs; 76111 ats_type '' -> wp_jobs. Why wp_jobs and not '' for Hessing: pflege-ingest's upsert keeps the stored ats_type on an empty value (coalesce(nullif(excluded,''), stored)), so '' can never clear 'softgarden'; and 76111's CSV said 'softgarden' -- a CSV re-push would have flipped the shared board to the seeded softgarden adapter (the one that finds no host). Apply: `set -a; source .env; set +a; .venv/bin/python tools/apply_clinic_corrections.py /tmp/grpB3/task170_registry.json --push` -- only AFTER the code patch is deployed (drv_bund/median ats_types need the new adapters).

### For Ivan's approval (not written)
Retire (verify_status gone): 15271, 15272, 15273 (RH1901 GSB search-form phantoms), 15276, 15277, 15278 (RH2954, same), 15097 (RH2161 nationwide pool ad). All 7 are absent from a clean walk of their clinic's (corrected) board, so the first post-deploy nightly would also retire them through the normal board-absent path; this proving crawl ran with retirement disabled on purpose.
Relink: none left -- 15160/15161 already moved to RH2467 by proving run 220's own drain (the matcher fix).

### Verify side effect
Run 219's own verify step: "verified 3 new postings: {'live': 3}" -- no fresh posting marked error this time.

### Follow-ups found, NOT done (need Ivan's call)
- Classifier: strong_pflege `pfleger\b` overrides nicht_pflege for non-nursing "-pfleger" compounds. Title replay over all 4863 postings: Kinderpfleger (childcare) + Landschaftspfleger -> 43 open postings would become nicht_pflege (6 on clinics, e.g. 13624 "Erzieher/ Kinderpfleger ... Kindertagesstätte im Hessing Förderzentrum" open on 76111; 14947 "Garten-und Landschaftspfleger"). Adding Heilerziehungspfleger (already in nicht_pflege by design, defeated by the same override) flips 62 more open -- a policy question, psychiatric hospitals list HEP in their nursing teams. Measured with /tmp/grpB3/replay_classify.py, no change shipped.
- RH1970 PARITÄTISCHE Haus am Kurpark (Bad Königshofen): careers page lists 2 nursing postings in-page ("Mitarbeiter*in für unseren Pflegedienst (m/w/d) ..." x2) with no per-job URL; nightly read 0 ("empty"+"degraded" 09-26..09-29). Needs a TITLE_ONLY_SITES entry + fixture test.
- DRV Bund portal also lists Bad Kissingen (loc 54: Physio, Ergo, Pflegefachkraft, Sozialarbeiter) -- the same jobs the two house GSB sites carry; the GSB sites stay the per-house boards, no change.

### Suite + hand-back
Offline suite in the worktree with the final code (`.venv/bin/python -m pytest -p no:cacheprovider -m "not network" -q`, env sourced): 1570 passed, 18 skipped, 2131 deselected, 0 failed (was 1552 before this task).
Patch: /tmp/grpB3/b3.patch = `git diff --binary` of the worktree after `git add -N` of the 3 new test files and 14 new fixtures. It has 26 files, and `git -C /home/exedev/repo apply --check /tmp/grpB3/b3.patch` passes. CSV part alone: /tmp/grpB3/b3_registry_csv.patch. Registry corrections: /tmp/grpB3/task170_registry.json.
Before/after snapshots: /tmp/grpB3/before.json and after.json (run 219), before2.json and after2.json (run 220). Replays: /tmp/grpB3/replay_r6defer_217.txt, replay_r6defer_208.txt, replay_r6_217.txt (the rejected variant).
Left In Progress: the code is not in main yet, and the registry push waits for Ivan.

AC scope note: AC#2 is checked for every defect behind a B3 row's 0 or its misattribution (all fixed in code and/or corrections JSON). Two defects found on the way are deliberately NOT fixed and are listed under Follow-ups, because each needs Ivan's call: (a) the classifier '-pfleger' override, which also over-counts 76111 by one (13624 Kinderpfleger) and is a policy question for Heilerziehungspfleger; (b) RH1970 Haus am Kurpark, a different operator outside B3.

Main-session note (2026-09-29 ~19:55 UTC): the 'runs 219 and 220' cited above are ids in the grpB3 WORKTREE's copy of data/app.sqlite (copied from main at 18:31, when main's last run was 218). Main's own crawl_runs table has no 219/220 yet; tonight's nightly in main will take id 219 with unrelated content -- don't cross-reference them. Code part of b3.patch applied to the main checkout WITHOUT data/registry/clinics.csv (git apply --exclude); the CSV part (/tmp/grpB3/b3_registry_csv.patch) and the DB push (/tmp/grpB3/task170_registry.json) wait for Ivan's approval together, since the crawl reads the registry from the DB snapshot (app/targets.clinics_for -> data.clinics()), not the CSV.

Deployed + pushed 2026-09-29 (main session): code 19:5x UTC without the CSV part, pflege-web restart 19:56:44. Ivan approved 2026-09-29. Registry pushed 21:37:23Z via tools/apply_clinic_corrections.py (12 rows, 21 ledger lines; RH2843 ats_type unchanged so not logged; backup backups/apply_clinic_corrections_task170_registry_before_20260929T213723Z.json); /tmp/grpB3/b3_registry_csv.patch applied to clinics.csv. Retired 15271-15273, 15276-15278 (GSB search-form phantoms) and 15097 (DRV pool ad) 21:37:32Z via tools/apply_posting_changes.py, ledgered. The app snapshot TTL is 600 s, so the 03:00 nightly reads the new registry values without a restart.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed why DRV, MEDIAN, Kurpark and Hessing sites showed 0: dead DRV Bund domains replaced by crawl_drv_bund on the location-filtered portal, MEDIAN read per location by crawl_median, Höhenried's onapply widget read, Kurpark on its real domain, Hessing on the shared softgarden board with the house taken from the title, DRV GSB search-form phantoms excluded, and the R6 bed-count guess deferred behind the operator-token rule. Verified live (RH1498 0->2, RH2655 0->1, RH1585 0->1, RH2724 0->1, RH2467 0->2), 25 mutation tests, suite 0 failed; registry (12 rows) and 7 retirements applied with ledger entries after Ivan's approval.
<!-- SECTION:FINAL_SUMMARY:END -->
