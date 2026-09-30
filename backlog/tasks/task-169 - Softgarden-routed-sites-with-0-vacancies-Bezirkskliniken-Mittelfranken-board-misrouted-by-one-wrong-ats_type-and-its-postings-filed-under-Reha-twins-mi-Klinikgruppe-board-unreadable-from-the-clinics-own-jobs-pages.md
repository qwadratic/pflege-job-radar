---
id: TASK-169
title: >-
  Softgarden-routed sites with 0 vacancies: Bezirkskliniken Mittelfranken board
  misrouted by one wrong ats_type and its postings filed under Reha twins;
  m&i-Klinikgruppe board unreadable from the clinics own /jobs pages
status: Done
assignee:
  - '@claude'
created_date: '2026-09-29 18:06'
updated_date: '2026-09-29 21:39'
labels:
  - crawler-coverage
dependencies: []
priority: high
type: bug
ordinal: 167000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-29 (via coordinator, group B2): registry sites with ZERO open vacancies routed to the softgarden adapter, nightly run 217 logs "no softgarden host found on careers page". Named: Bezirksklinikum Ansbach 56102 (377 beds) and Klinikum am Europakanal Erlangen 56202 (363) on the shared board https://jobs.bezirkskliniken-mfr.de/index.php?ac=start; m&i-Fachklinik Enzensberg (RH1255 322 / 77706 197) and Ichenhausen (RH2404 280 / 77404 126) routed softgarden, sister sites Bad Heilbrunn (17307/RH2980) and Herzogenaurach (57202/RH1752) via wp_jobs with 1 row each. Also check the other 0-open softgarden sites: Bezirksklinik Rehau 47503, Klinik Naila 47502, Rotkreuzklinik Wuerzburg 66303, St. Theresien-Krankenhaus Nuernberg 56402. Live registry writes need Ivan (ask-before-DB-write rule): any registry correction is proposed with the exact command, not executed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each named site has a root cause stated with live evidence (live board nursing count + code line), fixed or explicitly documented as not a softgarden bug
- [x] #2 Bezirkskliniken Mittelfranken postings for Ansbach and Erlangen are attributed to the acute sites 56102/56202 instead of the Reha twins by a Matcher that treats public-law legal forms (KU, gKU, AoeR, AdoeR, KdoeR) as noise in operator keys; an offline test fails on the old code
- [x] #3 Registry corrections needed for routing (57707 ats_type; the m&i rows careers_url/ats_type to the real softgarden tenant) are written as a corrections JSON with a dry-run and a ready-to-run command for Ivan, not pushed
- [x] #4 Every code change has a test and a mutation test (red then green, byte-identical restore); non-network suite 0 failed
- [x] #5 Affected clinics re-crawled with the fixed code; open-posting counts per clinic_id before/after recorded
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Oracle first: fetch each live board and count its nursing postings per site (MFR beesite sitemap + 41 detail pages; m&i softgarden tenant feed; GeBO, HochFranken, Rotkreuz, Theresien).
2. Trace routing: crawlers/routing.plan groups by exact careers_url; one non-fallback label decides the board vendor. Find which row carries the wrong softgarden label.
3. Replay pflege_jobs.registry.Matcher on the live board items with the live registry to see where postings land once crawled.
4. Code fix where the root cause is code (Matcher operator key: public-law legal forms), test red on old code, mutation test, full suite.
5. Registry corrections (DB writes, need Ivan): corrections JSON + tools/apply_clinic_corrections.py --dry-run; exact --push command handed back.
6. Verify end to end: worktree crawl (mode adapter) with the proposed registry values applied in memory only, drain with the fixed matcher, before/after open counts per clinic_id live.
7. Document not-a-bug sites with evidence; notes + final summary.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Oracle (live boards, 2026-09-29 ~17:45-18:05 UTC, own scripts, independent of the adapters)
- jobs.bezirkskliniken-mfr.de (beesite / muz global-jobboard-client, no softgarden anywhere on it): sitemap.xml 41 ids, all 41 ac=jobad detail pages 200 with JSON-LD, hiringOrganization "Bezirkskliniken Mittelfranken" on every one. Nursing by JSON-LD city: Erlangen 7 (745 Flex-Unit, 809 Trainee psych. Pflege, 858, 880, 884 ICU ZNR, 898, 968) + 2 Ausbildung (963, 964); Ansbach 3 (613 KJP, 932, 935 Forensik) + 1 Ausbildung (859); Engelthal 2 (849, 850); Treuchtlingen 1 (969); Roth 1 (962). 14 nursing + 3 Ausbildung.
- m&i: www.fachklinik-{enzensberg,ichenhausen,bad-heilbrunn,herzogenaurach}.de/jobs are Joomla pages whose list is filled client-side (POST /jobs/list, fragment class softgarden-job-list-item); the page itself carries no softgarden host and exactly one static /job/ link (Initiativbewerbung). Detail pages link jobdb.softgarden.de/.../applyonline/click?jp=20274699 -> 302 enzensberg.softgarden.io. Tenant feed https://enzensberg.career.softgarden.de/jobs.feed.json 200, numberOfItems 144 = 144 elements, 9 companies: Enzensberg/Fuessen 23 (7 nursing), Ichenhausen 22 (4 nursing + 2 Ausbildung), Bad Heilbrunn 20 (4 nursing + FSJ + Ausbildung), Herzogenaurach 7 (2 nursing), Klinikgruppe Enzensberg 4 (0), rest outside Bavaria (Bad Liebenstein TH 22, Hohenurach BW 19, Parkland HE 17, Bad Pyrmont NI 10; addressRegion set on every item). enzensberg.softgarden.io/jobs.feed.json 404 (the .io host never serves the feed: 404 on 6/6 tenants tried).
- GeBO gebo-med.softgarden.io: 28 jobs (sitemap = /de/vacancies list); the only Rehau job is 53673856 Oberarzt. 0 nursing in Rehau.
- jobs.kliniken-hochfranken.de feed: 13 items; nursing 2, both titled "Standort Muenchberg oder Naila" with jobLocation Muenchberg; Naila-located items are 2 physicians only.
- rotkreuzklinik-wuerzburg.de/stellenangebote/ 403 to every UA (as TASK-151), homepage nav hrefs empty.
- theresien-krankenhaus.career.softgarden.de: page title "... TGE Altenhilfe"; its own pcw-api job list (userId/projectId from the page) returns 23 jobs, all TGE Altenhilfe care homes (Bad Griesbach, Rohrdorf, Neumarkt, Baden-Baden, Gablitz AT, Fuerstenfeldbruck), none in Nuernberg; sitemap 2 links. www.theresien-krankenhaus.de 301 -> kh-nuernberg.martha-maria.de ("Krankenhaus Martha-Maria St. Theresien", two sites: Mommsenstrasse = St. Theresien, Stadenstrasse = Martha-Maria).

## Root causes (code lines)
1. MFR: crawlers/routing.py plan(): `if b["vendor"] is None or (b["vendor"] in FALLBACK_VENDORS and vendor not in FALLBACK_VENDORS)` -- the shared board has 8 clinics (56202/57706/57504/57506 self_hosted, 56102/57407/RH2398 blank -> wp_jobs, 57707 softgarden); the single real-looking label on 57707 (Treuchtlingen, Bedarfsfeststellung, 0 beds) wins, app/crawl.py _seed_obs -> softgarden.find_host(careers_url) -> (None, None) -> "no softgarden host found on careers page" (reproduced live). 57707 had no ats_type/careers_url in backups/clinics_20260906T071908.json; provenance of the label unknown. Registry data bug, not code.
2. MFR attribution (code): even when crawled (the 2026-09-08 Firecrawl rows), pflege_jobs/registry.py Matcher R2_operator keys operators with employer_norm, which strips only private-law legal forms (patterns.json legal_forms). Krankenhausplan rows: "KU Bezirkskliniken Mittelfranken, AoeR"; RHV Reha rows RH2272/RH2398/RH2720 and every posting: "Bezirkskliniken Mittelfranken". by_op hit = Reha rows only -> Erlangen -> RH2720 (R2_operator_town), Ansbach -> RH2398 (R6 RH2272,RH2398). Live now: RH2720 7 open, RH2398 3 open, 56202/56102 0.
3. m&i: careers_url of every m&i row is the clinic Joomla /jobs page that does not expose the vendor -> find_host None (Enzensberg, Ichenhausen: softgarden label) or crawl_wp_jobs reads only the one static Initiativbewerbung link (Bad Heilbrunn, Herzogenaurach: 1 row, degraded sitemap_and_wp_json_empty). Registry data bug: the board is the tenant https://enzensberg.career.softgarden.de/.

## Fix (worktree /home/exedev/repo/.claude/worktrees/grpB2, branch grpB2, NOT committed; patch /tmp/grpB2/b2.patch, `git -C /home/exedev/repo apply --check` passes)
Code (1 file + test): pflege_jobs/registry.py adds PUBLIC_LAW_FORMS = {ku, gku, aoer(ö), adoer(ö), kdoer(ö) + ascii} and drops those tokens in the Matcher key employer_norm() (same function TASK-168 folded hyphens in; classify.employer_norm, the employers identity key, untouched; registry.employer_norm has no user outside Matcher). toks() already treated these as noise (STOP has gku/aör/aoer, ALIASES["ku"] = set()); only the exact R1/R2 keys did not.
Replay before writing it (live registry 649 rows x all 3790 open postings, content match, old vs new key): exactly 10 matches change, all "Bezirkskliniken Mittelfranken": RH2720 -> 56202 (7), RH2398 -> 56102 (3). Nothing else in the registry moves (DIAKONEO KdöR, InnKlinikum gKU, KU Klinikum Nuernberg, ... all unchanged).
Test: tests/test_mech_clinic_link.py::test_public_law_legal_forms_do_not_split_one_operator (live rows trimmed): Erlangen -> ("56202","R6_ambiguous_sites:56202,RH2720",0.5), Ansbach -> ("56102","R6_ambiguous_sites:56102,RH2272,RH2398",0.5), Engelthal -> ("57407","R2_operator_town",0.9). Red on old code: got ("RH2720","R2_operator_town",0.9).
Mutation test: cp registry.py /tmp/grpB2/registry.py.fixed; M1 PUBLIC_LAW_FORMS = set() -> 1 failed/26 passed; M2 PUBLIC_LAW_FORMS = {"ku"} -> 1 failed/26 passed; restored from /tmp copy, __pycache__ deleted, 27 passed, diff -q byte-identical.
Suite (worktree, .env sourced, -m "not network"): 1552 passed, 18 skipped, 0 failed (base 1551 + 1 new). Without .env sourced tests/test_reverify_and_clean.py errors at collection on KeyError SUPABASE_URL -- pre-existing, not from this change.

## Registry corrections -- PROPOSED, NOT PUSHED (ask-before-DB-write)
File /tmp/grpB2/task169_registry.json (copy: backups/task169_registry.json). tools/apply_clinic_corrections.py --dry-run output (live rows read 18:10 UTC):
- 57707 ats_type softgarden -> self_hosted (only fix the MFR board needs; its 7 siblings are self_hosted/blank; blank cannot be written, the edge fn coalesces "" to the stored value)
- m&i, all 9 Bavarian rows -> careers_url https://enzensberg.career.softgarden.de/ (the tenant career site, title "Karriereseite und Stellenangebote - m&i-Klinikgruppe Enzensberg", serves /jobs.feed.json), ats_type softgarden where not already: 77706, RH1255, 77404, 77473, RH2404, 17307, RH2980, 57202, RH1752. Routing then makes ONE softgarden board with all 9 as the pool (checked with crawlers.routing.plan on live rows + overlay).
- CSV half: data/registry/clinics.csv carries non-empty careers_url .../jobs for 17307, 57202, 77404 -> a later registry push would revert the DB value. Separate patch /tmp/grpB2/b2_registry_csv.patch (3 lines, apply --check OK on main). Apply both or neither.
- Optional, separate file backups/task169_registry_optional_mfr_reha.json: RH2272/RH2720 careers_url www.bezirkskliniken-mfr.de/karriere/offene-stellen/ (a marketing page that only links jobs.bezirkskliniken-mfr.de, wp_jobs 0 rows + crawl_issue empty/degraded every night) -> the portal URL. Not needed for attribution (content match is registry-wide).
Command (Ivan):
  cd /home/exedev/repo && set -a && source .env && set +a
  git apply /tmp/grpB2/b2_registry_csv.patch
  .venv/bin/python tools/apply_clinic_corrections.py backups/task169_registry.json --dry-run
  .venv/bin/python tools/apply_clinic_corrections.py backups/task169_registry.json --push   # writes backups/apply_clinic_corrections_task169_registry_before_*.json, prints read-back OK/NO per row
  .venv/bin/python -m crawlers.routing --plan | grep -E "bezirkskliniken-mfr|enzensberg"   # expect self_hosted board (8 ids) + softgarden board (9 ids)

Correction to the command above: `python -m crawlers.routing --plan` 401s today (its load() uses the hard-coded project URL + anon key), do not use it. Routing read-back that works (run today, pre-push output: "softgarden .../index.php?ac=start [8 ids]", "softgarden www.fachklinik-enzensberg.de/jobs [RH1255, 77706]"):
  .venv/bin/python -c "import sys; sys.path.insert(0, \".\"); from app import config as A; from crawlers.routing import plan; b, _ = plan(A.rest_get_all(\"clinics\", {\"select\": \"*\"})); [print(v[\"vendor\"], u, [c[\"clinic_id\"] for c in v[\"clinics\"]]) for u, v in b.items() if \"bezirkskliniken-mfr\" in u or \"enzensberg\" in u]"
Expected after push: "self_hosted https://jobs.bezirkskliniken-mfr.de/index.php?ac=start [8 ids]" and "softgarden https://enzensberg.career.softgarden.de/ [9 ids]".

## Re-crawl: run 916801 (worktree app.sqlite, sequence bumped to 916800), trigger manual-task169, mode adapter (0 Firecrawl), 17 clinics, 2 boards, 18:13-18:19 UTC, status done
Driver /tmp/grpB2/recrawl.py: unmodified app.crawl.execute from the worktree; the only difference from production is that the proposed registry values (backups/task169_registry.json) are applied IN MEMORY to the rows crawlers.routing.plan groups (crawl._boards wrapper). Supabase clinics table untouched.
Log: "softgarden https://enzensberg.career.softgarden.de/ -> 144 observations {job_links_found 144, truncated false, board_total 144} 86s"; "wp_jobs https://jobs.bezirkskliniken-mfr.de/index.php?ac=start -> 41 rows 57s" (= 41 sitemap ids, crawl_wp_jobs -> beesite delegate). Drain: sqlite inbox rows 185 -> observations 29, kez-linked 29, clinic links pushed 29, CONFLICT none, resolve created 15; link-cross 0 pairs; verify 15 new -> live 15. No retirement.
29 kept = MFR 14 nursing (all oracle ids: 613 932 935 / 745 809 858 880 884 898 968 / 849 850 / 969 / 962) + m&i 15 (Enzensberg 6, Ichenhausen 3 new, Bad Heilbrunn 4 new, Herzogenaurach 2). Oracle items not kept are policy drops, not reads: Ausbildung (MFR 859 963 964, m&i 3), FSJ, Pflegehelfer/Pflegefachhelfer (Enzensberg 1, Ichenhausen GuK-/Altenpflegehelfer 1).

## Open postings per clinic_id, live, before 18:07 UTC -> after 18:19 UTC (backups/task169-before-open-postings-2026-09-29.json, /tmp/grpB2/after.json)
- 56102 Bezirksklinikum Ansbach 0 -> 3 (613, 932, 935 moved from RH2398; rule R6_ambiguous_sites:56102,RH2272,RH2398)
- 56202 Klinikum am Europakanal 0 -> 7 (from RH2720; R6_ambiguous_sites:56202,RH2720)
- RH2398 3 -> 0, RH2720 7 -> 0 (Reha twins of the same campuses)
- 57407 Engelthal 2 -> 2, 57707 Treuchtlingen 1 -> 1, 57605 Roth 1 -> 1 (now R2_operator_town), 57706/57504/57506 day clinics 0 -> 0 (no posting names their towns)
- RH1255 m&i Enzensberg (Reha, 322) 0 -> 6; 77706 (acute twin, 197) 0 -> 0: its Krankenhausplan row is a PDF-parse artifact (name "m&i-Fachklinik Enzensberg Hopfen am", town "See/Fuessen"), town never matches the feed city "Fuessen", so R3_tokens sees only RH1255. Same site, one row carries it.
- 77404 m&i Ichenhausen 1 -> 4 (3 new + old 5706); 77473 (12-bed Vertrags-KH twin) 0, RH2404 (Reha row named after the GmbH) 0 -> 0: R6_ambiguous_sites:77404,77473 picks 77404.
- 17307 m&i Bad Heilbrunn 1 -> 5 (4 new + old 6634); RH2980 0 -> 0 (twin, R1_exact name hit is 17307)
- 57202 m&i Herzogenaurach 0 -> 2; RH1752 0 -> 0 (twin)
Duplicates created (DB write, PROPOSED not done): 6634 "Stationsleitung ... Pflege Nachtdienst" (fachklinik-bad-heilbrunn.de/job/..., last_seen 2026-09-05) = 15291 (enzensberg.career.softgarden.de/jobs/64280338/...); 5706 "Pflegefachkraft im Springerpool" (fachklinik-ichenhausen.de/job/..., last_seen 2026-09-05) = 15290 (jobs/63892129/...). The Joomla /job/ page is a render of the same softgarden job; after the registry push nothing re-observes the /job/ URL, verify keeps it live while the Joomla page exists. Proposed: EdgeSink()._post({"verify": [{"posting_id": 6634, "verify_status": "gone", "verify_http": None, "verified_at": <now>, "verify_note": "duplicate of 15291, TASK-169"}, {"posting_id": 5706, ... "duplicate of 15290, TASK-169"}]}) -- same shape as the crawl retirement push.
Until the registry push lands, the nightly (main code) still logs "no softgarden host found" for the MFR/Enzensberg/Ichenhausen boards and re-observes nothing there; the links set by this run stay (a link only changes on re-observation), and Bad Heilbrunn/Herzogenaurach wp_jobs walks record kind=degraded every night, which board_walk_ok treats as not-ok, so the new postings are not retired.

## Other 0-open softgarden sites -- not the same bug, documented, left
- 47503 Bezirksklinik Rehau: board gebo-med.softgarden.io is read (run 217: BFS 59 observations; feed 404 on .softgarden.io, 200 on gebo-med.career.softgarden.de -- see side finding). Live board 28 jobs; the one Rehau job is 53673856 Oberarzt. 0 nursing located in Rehau today -> 0 is correct. (Older expired 5621/5623 with city Rehau went to 47802/46110 via R1_exact on the posting employer name; both expired, not live.)
- 47502 Klinik Naila: jobs.kliniken-hochfranken.de feed 13 items, read fully (run 217 13 observations). Nursing 2, both "Pflegefachkraft ..., Standort Muenchberg oder Naila" with JSON-LD jobLocation Muenchberg -> stored under 47501 (open 3). Naila-located items: 2 physicians. One posting = one clinic_id; not a crawler bug.
- 66303 Rotkreuzklinik Wuerzburg: /stellenangebote/ 403 to every UA today (same as TASK-151); clinic ceased operations 2026-04-01 (decision-1, TASK-151 dead board). Re-confirmed, nothing to fix.
- 56402 St. Theresien-Krankenhaus Nuernberg: careers_url theresien-krankenhaus.career.softgarden.de now shows TGE Altenhilfe (page title; its pcw-api job list = 23 care-home jobs in Bad Griesbach/Rohrdorf/Neumarkt/Baden-Baden/Gablitz/Fuerstenfeldbruck, none in Nuernberg; no jobs.feed.json: "No such client for domain"). The hospital merged into "Krankenhaus Martha-Maria St. Theresien" (www.theresien-krankenhaus.de 301 -> kh-nuernberg.martha-maria.de); that board lists 25 jobs, its "Pflege" facet 3, all at elderly-care homes (Luisenheim, Seniorenzentrum). 0 hospital nursing postings for the St. Theresien site today. Stale careers_url is a registry fact (would point 56402 at the Martha-Maria board), not a softgarden bug. Seen on the way, out of scope: 56403 has 2 open Seniorenzentrum postings (sz-nuernberg.martha-maria.de, 7412/12600) attributed to the hospital, and 3 open hospital postings (6222, 6223, 15177) whose URLs 404 today while verify_status says live.

## Side findings (not changed)
- softgarden tenants: <tenant>.softgarden.io/jobs.feed.json 404 on 6/6 tenants tried; <tenant>.career.softgarden.de/jobs.feed.json 200 for gebo-med (and enzensberg), 400 "No such client" for theresien-krankenhaus, dritter-orden, josefinum, klipa. seed_for only tries the page-linked host (often .io) -> GeBO falls back to BFS every night although a feed exists. Not needed for any 0-site here.
- Hessing (same failure class, not in the named list): RH2724 (140 beds) and RH1585 (60) careers_url www.hessing-kliniken.de/karriere/... -> "no softgarden host found" (run 217). The page names the tenant only in a certificate badge iframe (certificate.softgarden.io/widget-new/hessing-kliniken/de, a GENERIC_SG_HOSTS entry) and a TYPO3 tx_softgarden_kategorieliste list; the tenant board hessing-kliniken.softgarden.io/de/vacancies is already read for 76111 via wp_jobs (83 rows, 20 open). Same fix shape as m&i (registry careers_url -> the tenant board; ats_type must then be a fallback label, not softgarden, or the shared board flips from wp_jobs to the .io feed 404 + BFS). Not investigated further.
- 57707 label provenance unknown: absent in backups/clinics_20260906T071908.json; no "clinic updated" line in the local run_log.

Deployed 2026-09-29 (main session): code 18:2x UTC, pflege-web restart 18:31:16. Ivan approved the registry write and the duplicate retirement 2026-09-29 ('да давай по всем да'). Registry pushed 21:37:22Z via tools/apply_clinic_corrections.py (10 rows, 17 ledger lines in data/ledger.jsonl, backup backups/apply_clinic_corrections_task169_registry_before_20260929T213722Z.json); /tmp/grpB2/b2_registry_csv.patch applied to clinics.csv. Postings 5706, 6634 retired 21:37:32Z via tools/apply_posting_changes.py (duplicates of 15290/15291), ledgered.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Three causes, none in the softgarden adapter itself. (1) Bezirkskliniken Mittelfranken: the shared board jobs.bezirkskliniken-mfr.de (beesite, 41 live postings, 14 nursing) is routed softgarden because one of its 8 registry rows, 57707, carries a wrong ats_type=softgarden and routing.plan lets a real label beat fallback labels -> "no softgarden host found". (2) Even when crawled, Matcher R2_operator filed Erlangen/Ansbach postings under the Reha rows RH2720/RH2398, because the Krankenhausplan spells the operator "KU Bezirkskliniken Mittelfranken, AöR" and the Reha rows/postings "Bezirkskliniken Mittelfranken". Fixed in code: registry.employer_norm drops public-law legal forms (KU/gKU/AöR/AdöR/KdöR); full-registry replay moves exactly 10 open postings, all MFR; test red on old code, mutation-tested, suite 1552 passed 0 failed; patch /tmp/grpB2/b2.patch (applies to main). (3) m&i: every m&i careers_url is a Joomla /jobs page that hides the vendor; the real board is the softgarden tenant https://enzensberg.career.softgarden.de/ (feed 144 items, 9 companies). Registry fixes for (1) and (3) are proposed, not pushed: backups/task169_registry.json (57707 -> self_hosted; 9 m&i rows -> tenant URL + softgarden) + /tmp/grpB2/b2_registry_csv.patch, command in notes. Verified with run 916801 (worktree code, proposed registry values in memory only): 41 + 144 rows read, 29 observations linked, 15 new postings verified live. Open per clinic before -> after: 56102 0->3, 56202 0->7, RH2398 3->0, RH2720 7->0, RH1255 0->6, 77404 1->4, 17307 1->5, 57202 0->2. Rehau, Naila, Rotkreuzklinik Wuerzburg, St. Theresien: live boards have 0 nursing postings for those sites today (reasons in notes). Pending Ivan: registry push + CSV patch, and retiring 2 old-URL duplicates 6634/5706.
<!-- SECTION:FINAL_SUMMARY:END -->
