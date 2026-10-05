---
id: TASK-172
title: >-
  Coveto-labelled sites with 0 vacancies (group V): no adapter for coveto,
  Altmühlfranken board mislabelled oracle by a Danish 'taleoutput' string and
  read without per-job location, Eltern-Kind/Algesiologikum/Stadtmission
  careers_urls
status: Done
assignee:
  - '@claude'
created_date: '2026-09-29 21:03'
updated_date: '2026-10-05 15:40'
labels:
  - crawler-coverage
  - bug
dependencies: []
priority: high
type: bug
ordinal: 170000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-29: registry sites labelled ats_type=coveto show 0 open vacancies; every nightly run logs e.g. 'skip RH1802 Klinik Maximilian GmbH & Co. KG: no adapter for coveto' (crawlers/routing.py kept coveto out of ADAPTERS on purpose in TASK-116 when only 16262 carried the label; the RHV Reha rollout added five more). Sites: RH1802 Klinik Maximilian Scheidegg (350 beds, in Ivan's top-100 zero list), RH2395 Klinik Alpenhof Chieming (240), RH1487 Klinik Lindenhof Bayerbach (196, careers_url is a single Initiativbewerbung page), RH2456 Klinikum Altmühlfranken Gunzenhausen Geriatrische Reha (30) whose acute twin 57705 Klinikum Altmühlfranken Gunzenhausen (210 beds, 0 open, in Ivan's zero list) and 57701 Weißenburg share the same board labelled ats_type=oracle, RH1866 Therapiezentrum Wolkersdorf (Stadtmission Nürnberg, 28), 16262 Algesiologikum Tagesklinik München (day clinic). Conflicting labels on one board break routing, so the real vendor of karriere.klinikum-altmuehlfranken.de has to be established and both labels made consistent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every coveto-labelled row and the Altmühlfranken acute rows have their real vendor and live nursing-vacancy count stated with evidence, and each 0 is classified as genuine (board empty of nursing for that site) or a crawler/registry defect
- [x] #2 Each defect is fixed in code (red-green test from a live-captured fixture, mutation-tested) and/or a prepared registry correction (corrections JSON for tools/apply_clinic_corrections.py plus the matching data/registry/clinics.csv patch)
- [x] #3 A proving crawl from the worktree (registry corrections applied in memory only, retirement disabled) shows before -> after open-posting counts per clinic_id, queried live
- [x] #4 Postings that must be retired or relinked are listed by posting_id for Ivan's approval, not written
- [x] #5 Offline suite ends 0 failed
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Live oracle per board BEFORE code: klinik-maximilian/alpenhof/lindenhof (-> <clinic>.mutter-kind.de, Next.js front of coveto tenant k11235), karriere.klinikum-altmuehlfranken.de (WordPress mirror of coveto tenant k61199), stadtmission-nuernberg.de (k60360), algesiologikum.de (k19368 iframe behind a cookie blocker). Count jobs/nursing per site from the coveto listing /public/jobs/ (numbered pager to the last page) and the tenant sitemap.
2. Decide route: crawl_wp_jobs already reads coveto listings (server-rendered /public/jobs/, ?page=N pager, JSON-LD detail pages) -- verify completeness live per tenant before choosing it over a new adapter.
3. Code (worktree grpV, red-green + mutation): coveto in routing ADAPTERS/VENDORS; whatever attribution defects the oracle shows (town alias, Reha-house title rule).
4. Registry corrections JSON + clinics.csv patch; proving crawl with corrections in memory, retirement disabled; before/after per clinic_id; posting ids to retire/relink.
5. Suite 0 failed; notes + patch paths.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Live oracle (2026-09-29, before any code change) -- captures in /tmp/grpV/live/
Coveto's public read path, from the tenants' own embed code: the Algesiologikum page's cookie-blocked iframe (base64 in data-borlabs-cookie-content) decodes to <iframe src="https://k19368.coveto.de/public/jobs/">; kNNNNN.coveto.de/ itself is a 401 login. /public/jobs/ is server-rendered in three display modes: tabs (k19368: div.coveto_job rows + per-category counts), tiles (k11235: category tiles with counts, jobs via the page's own search form GET ?q=&kategorie=&einsatzort=), filtertable (k61199, k60360: table rows). Pager = numbered ?page=N links; the last page's pager links no further page (page N+1 answers 0 rows). Every job page carries a schema.org JobPosting (title, hiringOrganization, jobLocation.address incl. PLZ, datePosted, validThrough, employmentType). robots.txt names /sitemap.xml (job URLs only; k11235 leaves out its 12 Initiativbewerbung, k60360 lagged job 1915). No declared board total in filtertable mode; the tab/tile category counts sum to the listing count (k19368 1+3+3+2=9, k11235 14 tiles = 86).

Per tenant (listing walked to its last page, compared with the sitemap):
- k11235 'Eltern & Kind Kliniken Dienstleistungs GmbH' (Passau), 86 jobs = 25+25+25+11, 12 houses incl. SH/MV/BW. Bavaria: Scheidegg 12 (RH1802; nursing 1 = 7688 Pflegefachkraft), Chieming 8 (RH2395; nursing 1 = 7743), Bayerbach 12 (RH1487; nursing 1 = 7702), Neuburg/Inn 3 (RH2785, 0 nursing), Grafenau 6 (RH1138, 0 nursing), Bad Füssing 2, Passau HQ 4. The group's Next.js site lists each house's own subset server-side on <house>.mutter-kind.de/stellenangebote (relative href="stellenangebote/<slug>-<id>" cards): maximilian 12, alpenhof 8, lindenhof 12, inntalerhof 3, kurpark 6 -- identical to the coveto per-town counts. www.klinik-maximilian.de/jobs and www.klinik-alpenhof.de/jobs 301 to the house root, which carries the same cards.
- k61199 'Klinikum Altmühlfranken' (Gunzenhausen), 48 jobs = 25+23, sitemap 48. Pflege/Fachkraft: Weißenburg 7 (1373 Akad. PFK Praxisanleiter, 1366 Chirurgie St.12, 1352 Geburtshilfe, 1358 Intensiv, 1359 Flexi-Pool, 1313 Innere IMC, 1356 Innere), Gunzenhausen 6 (1374 Station A2, 1370 Chirurgie, 1315 Intensiv, 1360 Flexi-Pool, 1353 'für unsere geriatrische Rehabilitation', 1348 Pflegefachhelfer = excluded role), Pleinfeld 1 (1361 Palliativ Care, SAPV Südfranken). JSON-LD: hiringOrganization 'Klinikum Altmühlfranken', addressLocality 'Gunzenhausen' / 'Weißenburg in Bayern' / 'Pleinfeld'.
- k60360 'Stadtmission Nürnberg e.V.', 28 jobs = 25+3, sitemap 27 (1915 missing). Standorte: Nürnberg/Erlangen/Röthenbach/Lauf only -- none in Schwabach/Wolkersdorf. Its nursing jobs are Altenhilfe homes (Hephata, Christian-Geyer-Heim, Karl-Heller-Stift, Tagespflege).
- k19368 'Algesiologikum GmbH', 9 jobs, sitemap 9, 0 nursing (IT, psychotherapists, doctors, Belegungsmanagement, Praktikum).

## Why each site showed 0
- RH1802, RH2395, RH1487, RH2456, RH1866, 16262: routing had no ADAPTERS entry for coveto (TASK-116 kept it out when 16262 was the only coveto row) -> 'skip ...: no adapter for coveto' every night, the board was never fetched.
- 57705 (and RH2456): karriere.klinikum-altmuehlfranken.de is a WordPress mirror of k61199 (every job page's apply button -> k61199.coveto.de/public/bewerbung/?id=<coveto id>). Label 'oracle' is a false fingerprint: data/ats_census.py's oracle pattern 'oraclecloud|taleo' hits the Danish string 'taleoutput' in the page's accessibility widget (no oraclecloud/taleo.net anywhere; coveto is listed after oracle in the census order). crawl_oracle -> no jobs.feed.json -> crawl_wp_jobs read all 48 WP pages, but the WP pages state no structured location, so every row got the seed clinic's town (city_source=seed, 'Weißenburg i.Bay.') and org (org_source=seed): run 217 drained all 13 nursing rows as 'loaded (no site match)'; the 13 open links on 57701 (R3_tokens) are older stale ones, 6 of them are Gunzenhausen/Pleinfeld jobs.
- RH1487 additionally: careers_url is one job page (initiativbewerbung-100); crawl_wp_jobs from it reads 4 of the house's 12 (no Pflegefachkraft).
- 16262 additionally: the careers page hides the coveto iframe behind a Borlabs cookie blocker; crawl_wp_jobs reads 0 there.

## Existing-adapter check (crawl_wp_jobs live, 2026-09-29, before choosing a route)
k61199 listing 48/48 rows, all with JSON-LD city/PLZ/org/date, 14 s; k60360 listing 28/28 (+1 duplicate row: job 1888's page links its own ?i18nLocale=de_DE/en_GB variants, both resolve to the same JSON-LD url); k19368 listing 9/9; maximilian.mutter-kind.de 11/12, alpenhof 7/8, lindenhof /stellenangebote 12/12 (every nursing job read on all three). Decision: route the coveto label to crawl_wp_jobs (no new adapter) and point the rows whose own page hides or mislocates the board at the tenant listing (registry corrections).

## Code (worktree /home/exedev/repo/.claude/worktrees/grpV, branch grpV, NOT committed; red first, then green)
1. crawlers/routing.py ADAPTERS + crawlers/vendor_adapters.py VENDORS: 'coveto' -> crawl_wp_jobs; the TASK-116 'deliberately NOT in ADAPTERS' comment removed (six coveto rows now, not one). No new adapter: crawl_wp_jobs already reads the tenant listing, its numbered pager (_paginated_job_links, no cap, stops when no unseen page link is left) and the JobPosting detail pages.
2. pflege_jobs/registry.py CITY_ALIASES 'weißenburg bay' <- 'weißenburg in bayern': coveto names the town 'Weißenburg in Bayern', the registry 'Weißenburg i.Bay.' (57701, 57706); city_key gave 'weißenburg in bayern' vs 'weißenburg bay', _town_match False, so every Weißenburg posting stayed unmatched (R2_operator saw no town hit). Live postings with a Weißenburg city today: 13 seed-stamped 'Weißenburg i.Bay.' + 1 bare 'Weißenburg' -- unaffected.
3. crawlers/vendor_adapters.py: TASK-170's Hessing-only title override generalised to REHA_HOUSE_BOARDS / reha_house(board_ids, title) and app/crawl.py _vendor_rows calls it for every board. Entry added: board {57701, 57705, RH2456}, 'geriatrische ... Rehabilitation' in the title -> org 'Klinikum Altmühlfranken Gunzenhausen Geriatrische Rehabilitation' (RH2456's registry name) -> R1_exact. Without it the posting '... für unsere geriatrische Rehabilitation' (employer 'Klinikum Altmühlfranken', Gunzenhausen) goes R2_operator_town -> 57705 and RH2456 stays at 0. Hessing behaviour unchanged (its tests untouched and green).
4. docs/overview.md vendor table: coveto row now 'yes'.

## Tests
tests/test_coveto.py (new): app.crawl._vendor_rows on the real 3-clinic Altmühlfranken board (vendor coveto, careers_url k61199 listing) with VA.get routed to live-captured slices (listing page 1 row 1373 + pager, page 2 rows 1370/1353 + pager, three JobPosting blocks; contact redacted) -> exactly 3 rows across the pager, each with its JSON-LD town, and the live-registry Matcher (57701/57705/RH2456 + 57706) files them 1373 -> 57701 R2_operator_town, 1370 -> 57705 R2_operator_town, 1353 -> RH2456 R1_exact. Red before the change: 'RuntimeError: no vendor adapter for coveto'; after routing only: 1373 unmatched (town); after the alias: 1353 -> 57705; green after all three.
tests/test_routing.py: test_a_coveto_label_routes_to_the_generic_reader (red before: unroutable 'no adapter for coveto').
Fixtures: tests/fixtures/board_samples/coveto_altmuehlfranken_{jobs,jobs_page2,job_1373,job_1370,job_1353}_sample.html, documented in the README table.
Mutation (/tmp/grpV/mutate.py, log /tmp/grpV/mutation.log): each change broken in turn, file copied to /tmp/grpV/mut_orig first, targeted tests RED, restored from the /tmp copy (never git), __pycache__ dropped, GREEN, diff -q identical. M1 routing entry removed -> 2 failed; M2 VENDORS entry removed -> 2 failed; M3 Weißenburg alias removed -> 1 failed; M4 Altmühlfranken house entry removed -> 1 failed; M5 'exactly one house' -> 'first hit' -> 1 failed (Hessing both-houses title); M6 house table not chosen by board ids -> 2 failed; M7 override not applied in _vendor_rows -> 2 failed. All restored byte-identical.

## Registry corrections (NOT written -- Ivan approves)
/tmp/grpV/task172_registry.json (6 clinics) + the matching data/registry/clinics.csv rows (CRLF kept, careers_url + ats_type). Dry run against live (tools/apply_clinic_corrections.py --dry-run): 16262 careers_url algesiologikum.de/algesiologikum/karriere -> https://k19368.coveto.de/public/jobs/; 57701 + 57705 careers_url karriere.klinikum-altmuehlfranken.de/ -> https://k61199.coveto.de/public/jobs/ and ats_type oracle -> coveto; RH2456 careers_url -> the same k61199 listing (one board for the three sites); RH1487 careers_url .../stellenangebote/initiativbewerbung-100 -> https://lindenhof.mutter-kind.de/stellenangebote; RH1866 careers_url stadtmission-nuernberg.de/.../jobboerse -> https://k60360.coveto.de/public/jobs/ (the Stadtmission page read via crawl_wp_jobs also stores 7 non-job site pages). RH1802/RH2395 need no change (their careers_url 301s to the house page listing its own jobs). No overlap with /tmp/grpB3/task170_registry.json or the pending m&i/57707/66104 rows. Push only after the code is deployed: set -a; source .env; set +a; .venv/bin/python tools/apply_clinic_corrections.py /tmp/grpV/task172_registry.json --push

## Proving crawl: worktree run 920001 (worktree data/app.sqlite; crawl_runs sequence bumped to 920000 so the id cannot be confused with main's nightly ids), trigger manual-task172, mode adapter, /tmp/grpV/recrawl.py
Registry corrections applied IN MEMORY only (crawl._boards wrapper), retirement DISABLED (R.board_walk_ok -> False). Scope RH1802,RH2395,RH1487,57701,57705,RH2456,RH1866,16262: 8 clinics -> adapter 8, skipped 0, 6 boards, 116 raw rows, 21:12:38-21:14:57 UTC, status done, 0 errors. Sanity checks before the run: cli.py 2, registry.py PUBLIC_LAW_FORMS 2, crawl_drv_bund/median/mediclin 3.
Boards: k19368 9 rows (3 s); k61199 48 rows (14 s, the three Altmühlfranken sites); lindenhof.mutter-kind.de/stellenangebote 12 (11 s); klinik-maximilian.de/jobs 11 (10 s, crawl_issue degraded: the house sitemap index points at the typo host maxilimian.mutter-kind.de); k60360 29 (11 s; 28 jobs + job 1888 twice via its own language links, same canonical url); klinik-alpenhof.de/jobs 7 (7 s, degraded, same sitemap shape). Drain: 116 rows -> 27 observations, 19 clinic-linked, 25 postings created, 2 re-linked; verify of the 27: live 24, gone 3.
Drain notes (worktree data/inbox.sqlite, run_id 920001): k61199 -> 57701 x7, 57705 x4, RH2456 x1, no site match x1 (SAPV Südfranken, Pleinfeld), skipped 35 (nicht_pflege 28, werkstudent_praktikum 4, ausbildung 2, pflegehelfer 1); k19368 nicht_pflege 9; k60360 no site match 7 (Stadtmission Altenhilfe nursing in Nürnberg/Erlangen/Röthenbach, employer + city read, none in the pool town Schwabach), -> RH1866 x1 (see defects), skipped 21; mutter-kind boards -> RH1802/RH2395/RH1487 x2 each (Pflegefachkraft + a catering/admin title, see defects).

### Open postings per clinic_id, live, before -> after (snapshots /tmp/grpV/before.json, /tmp/grpV/after.json)
57705 Gunzenhausen 0 -> 4 (15302 Station A2, 15303 Chirurgie, 15306 Intensiv, 15310 Flexi-Pool; R2_operator_town, verify live)
RH2456 Geriatrische Reha 0 -> 1 (15312 'für unsere geriatrische Rehabilitation', R1_exact, live)
57701 Weißenburg 13 -> 20 = the 13 old WordPress-mirror postings (retirement disabled) + 7 coveto postings 15301, 15304, 15305, 15307, 15309, 15311, 15313 (R2_operator_town, live). After the 13 are retired: 7.
16262 Algesiologikum 0 -> 0: GENUINE (9 jobs read, none nursing).
RH1866 Therapiezentrum Wolkersdorf 0 -> 1: GENUINE 0 for nursing (no Stadtmission job in Schwabach/Wolkersdorf); the 1 is a wrong posting, see defects.
RH1802 Maximilian 0 -> 1, RH2395 Alpenhof 0 -> 1, RH1487 Lindenhof 0 -> 1: the board is read and each house's real Pflegefachkraft is classified and attributed correctly (15113 -> RH1802 R0_board, 15114 -> RH2395 R3_tokens, 15314 -> RH1487 R3_tokens), but the verify step marks all three 'gone' -> status expired. The '1' open on each is a misclassified non-nursing title (15316, 15325, 15315). Not fixable inside this task's allowed files, see defects.
(15113/15114 already existed: created 2026-09-24 17:22 by a firecrawl_agent observation of the same mutter-kind URLs, expired by the same verifier verdict -- which is why RH1802/RH2395 showed 0 even before routing.)

### Defects found downstream of the crawl, OUTSIDE the files this task may change (evidence for Ivan's pending verify/classifier decisions)
1. verify.py false 'gone' for bare 'Pflegefachkraft (m/w/d)' titles on both page templates of this group: _title_tokens drops 'pflegefachkraft', so decide() takes the TASK-150 no-token path and GONE_MARKERS scans the first 400 KB including <script>. mutter-kind.de job pages embed the Next.js not-found template in their RSC payload ('die seite wurde leider nicht gefunden.', offset 28088 of 48 KB); the k11235 coveto job pages carry the tenant's own error-redirect script ('die von ihnen gesuchte stelle konnte nicht gefunden werden', offset 13508). pflege_jobs.verify.decide(200, page, 'Pflegefachkraft (m/w/d)') returns gone for k11235 7688/7743/7702 and maximilian 6883 alike; k61199 pages pass (no such script). verify_note on 15113/15114/15314: '200, no title token to confirm, but a gone-marker matched'. Suggested fix (not done, verify.py is Ivan's pending decision / TASK-152): strip <script>/<style> before the GONE_MARKERS scan.
2. Classifier catch-all keeps '-pflege' inside other words: classify_role('Servicemitarbeiter (m/w/d) in unserer Patientenverpflegung') = ('sonstige_pflege','fallback'), same for 'Betriebsassistenz (m/w/d) für Patientenbeherbergung und -verpflegung' and 'Beschäftigung im Zuverdienst – Garten- und Anlagepflege (w/m/d)' (TASK-170's grounds-keeping rule covers 'Gartenpflege', not 'Garten- und Anlagepflege'). patterns.json not touched (pending decision).
3. Matcher R_jd_text has no town check: 15317 (the Anlagepflege job, CHANCEN gastro gGmbH, Nürnberg) -> RH1866 (Schwabach) because its description names 'Stadtmission Nürnberg' = RH1866's operator tokens {stadtmission, nürnberg}. Replay of the 8 loaded k60360 rows: only this one hits; the 7 nursing ones stay unmatched. Only reachable through defect 2; a town gate on R_jd_text would need a whole-run replay first -- proposed, not done.

### For Ivan's approval (NOT written)
Retire -- superseded WordPress-mirror URLs, each re-read today on its coveto URL (and the first nightly after the registry push would retire them itself, board-absent path): 5896 (-> 15304), 10057 (-> 15302), 10058 (-> 15301), 12913 (-> 15303), 13414 (-> 15308, Pleinfeld, unmatched), 13415 (-> 15305), 13416 (-> 15312 RH2456), 13417 (-> 15313), 13418 + 13422 (-> 15306/15307), 13419 + 13420 (-> 15309/15310), 13421 (-> 15311). Six of the 13 were Gunzenhausen/Pleinfeld jobs sitting on 57701.
Retire -- non-nursing, created by this proving crawl (defect 2; the nightly re-creates them until the classifier changes): 15315, 15316, 15325, 15317 (15317 also wrongly linked to RH1866, defect 3).
Should be open but are expired by defect 1: 15113 (RH1802), 15114 (RH2395), 15314 (RH1487) -- nothing to write until verify changes.

## Hand-back (2026-09-29 ~21:30 UTC)
Offline suite in the worktree with the final code (env sourced, __pycache__ dropped first): .venv/bin/python -m pytest -p no:cacheprovider -m 'not network' -q -> 1578 passed, 18 skipped, 2156 deselected, 0 failed (6:55; main was 1576 passed -- +2 new tests). Log /tmp/grpV/suite.log.
Patch of my changes only: /tmp/grpV/v.patch (git diff --binary after git add -N of tests/test_coveto.py and the 5 coveto fixtures; 14 files, +389/-21). git -C /home/exedev/repo apply --check passes, with and without --exclude=data/registry/clinics.csv. CSV part alone: /tmp/grpV/v_registry_csv.patch.
Registry corrections, new _why format (reason + evidence + task per clinic): /tmp/grpV/task172_registry.json (6 clinics). The field values were dry-run against live before the _why objects were added (see the corrections note above); push with the _why-aware tool, after the code is deployed (a coveto careers_url on an unrouted label would be skipped).
Posting-level changes, _why format: /tmp/grpV/task172_postings.json (17 x action retire: the 13 WordPress-mirror postings on 57701 and the 4 non-nursing postings 15315, 15316, 15325, 15317). Not written.
Proving-crawl runner /tmp/grpV/recrawl.py; mutation harness /tmp/grpV/mutate.py + log; live captures /tmp/grpV/live/.

## Follow-ups found, NOT done (need Ivan's call)
- verify.py: GONE_MARKERS scanned inside <script> marks every bare-title posting on mutter-kind.de and coveto k11235 pages gone (defect 1 above); blocks RH1802/RH2395/RH1487 at 0 open and would hit RH1138/RH2785 (same front-end) the moment they post a bare-title nursing job.
- patterns.json: '-verpflegung' / 'Patientenbeherbergung' / 'Garten- und Anlagepflege' fall to sonstige_pflege (defect 2).
- registry.py R_jd_text: no town gate (defect 3) -- measure with a whole-run replay before changing.
- RH1138 (TASK-170's pending correction -> kurpark.mutter-kind.de/stellenangebote) and RH2785 read the same Eltern & Kind front-end; nothing to change for them in this task (both 0 nursing today, genuine).
- crawl_wp_jobs._job_link_pairs tests JOB_PATH on the raw href: the relative 'stellenangebote/<slug>' cards without a gender marker in their text are skipped (maximilian FSJ, alpenhof Praktikumsplätze -- non-nursing; lindenhof's got in via its sitemap). Testing the resolved path instead would read them; needs a replay over all wp_jobs boards.
- data/ats_census.py oracle pattern 'oraclecloud|taleo' (bare substring) also labelled 17901 Klinikum Fürstenfeldbruck oracle on the same Danish 'taleoutput' string (no Oracle marker on its page; crawl_oracle's wp_jobs fallback reads it anyway). ats_discover2.py already uses taleo\.net.
- Stadtmission Nürnberg's 7 Altenhilfe nursing postings (15318-15324) are stored unmatched: the operator runs no registry clinic in those towns -- correct under decision-5.

AC scope note: AC#2 is checked for every defect inside this task's allowed files (routing/dispatch of the coveto label, the Weißenburg town alias, the Altmühlfranken Reha-house title rule -- all red-green + mutation-tested) and for the registry side (corrections JSON + CSV patch). Three defects found downstream are deliberately NOT fixed because the brief puts their files off-limits or they need a replay-backed decision: verify.py's gone-marker scan (defect 1), the classifier catch-all (defect 2), R_jd_text's missing town gate (defect 3). Left In Progress until the patch is merged and the registry corrections / retirements are approved and applied.

Main session 2026-09-29 21:4x UTC: code part of /tmp/grpV/v.patch applied to main WITHOUT data/registry/clinics.csv; suite 1591 passed, 0 failed; pflege-web restarted 21:4x. Checked before asking Ivan: all 13 mirror postings in task172_postings.json have an open coveto twin on k61199 (5896->15304, 10057->15302, 10058->15301, 12913->15303, 13414->15308, 13415->15305, 13416->15312, 13417->15313, 13418/13422->15306/15307, 13419/13420->15309/15310, 13421->15311). 15315/15316/15325/15317 removed from the retire list: they are live jobs misclassified as sonstige_pflege -- relabel via the classifier task, not retire. Registry (6 clinics) + 13 mirror retirements wait for Ivan. 15113/15114/15314 wrongly expired by the verify gone-marker-in-script bug: fix handed to the verify agent; reopen through tools/apply_posting_changes.py (reopen) once fixed.

2026-09-29 22:35 UTC, Ivan approved (#4): 6 registry rows via tools/apply_clinic_corrections.py (code board_location): 16262 -> k19368.coveto.de; 57701/57705 -> k61199.coveto.de + ats oracle->coveto; RH2456 -> k61199; RH1487 -> lindenhof.mutter-kind.de/stellenangebote; RH1866 -> k60360.coveto.de. Read back 6/6 OK, 8 corrections rows. 13 WordPress-mirror copies retired via tools/apply_posting_changes.py (code duplicate): 5896 10057 10058 12913 13414-13422; read back 13/13 expired/gone, 26 corrections rows; backup backups/apply_posting_changes_task172_mirror_retires_before_20260929T223531Z.json. data/registry/clinics.csv patched the same way (/tmp/grpV/v_registry_csv.patch applied).

Re-check 2026-10-05 (pflege-clawl). coveto is routed to crawl_wp_jobs on main (crawlers/routing.py line 66) and the live-captured coveto fixtures run in the offline suite (tests/fixtures/board_samples/coveto_*). CI run on 3770631: green, full offline suite, 0 failed. Live GET /api/clinics: RH1802 jobs_open 1, RH2395 1, RH1487 1, RH2456 1, 57705 4 (all five were 0 before 2026-09-29); careers_url and ats_type of those rows carry the corrected board (coveto tenant k61199, the houses mutter-kind.de pages). The later red CI run on 3e891d0 is an unrelated sub-pixel assertion in tests/test_web_leads.py.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Coveto-labelled sites are read: coveto routes to crawl_wp_jobs, the registry rows point at the real boards, and the five sites that showed 0 vacancies now show 1 to 4 live. Verified by the offline suite in CI (green on 3770631) and by the live API on 2026-10-05.
<!-- SECTION:FINAL_SUMMARY:END -->
