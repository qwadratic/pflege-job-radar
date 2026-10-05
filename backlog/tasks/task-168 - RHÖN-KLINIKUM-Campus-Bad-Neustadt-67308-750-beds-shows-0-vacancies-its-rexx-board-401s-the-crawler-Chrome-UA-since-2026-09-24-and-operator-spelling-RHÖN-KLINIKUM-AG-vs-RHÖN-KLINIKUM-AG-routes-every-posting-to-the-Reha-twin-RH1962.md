---
id: TASK-168
title: >-
  RHÖN-KLINIKUM Campus Bad Neustadt (67308, 750 beds) shows 0 vacancies: its
  rexx board 401s the crawler Chrome UA since 2026-09-24, and operator spelling
  RHÖN-KLINIKUM AG vs RHÖN KLINIKUM AG routes every posting to the Reha twin
  RH1962
status: Done
assignee:
  - '@claude'
created_date: '2026-09-29 17:07'
updated_date: '2026-09-29 17:38'
labels:
  - crawler-coverage
dependencies: []
priority: high
type: bug
ordinal: 166000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-29 (via coordinator, group B1): big registry sites with ZERO open vacancies whose ats_type is rexx. Named: RHÖN-KLINIKUM Campus Bad Neustadt 67308 (750 beds; nightly run 217 logs rexx bewerberportal.rhoen-klinikum-ag.com -> 0 rows in 0s, 3x) and Schön Klinik Bad Aibling 18717 (287; log shows a www.schoen-klinik.de seed -> 0 rows). Coordinator added: Kreisklinik Bad Reichenhall 17201 (240), Kreisklinik Berchtesgaden 17202 (118), Simssee Klinik 18713 (179), Wertachklinik Schwabmünchen 77201 (126). Also check every other rexx board in the live registry for the same bug.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each named site has a root cause stated with live evidence (live board count of its nursing postings + code line), fixed or explicitly explained as not a rexx bug
- [x] #2 crawl_rexx reads the full live RHÖN board again (all ids the board declares in data-count); an offline test reproducing the board shape fails on the old code
- [x] #3 RHÖN Bad Neustadt nursing postings are attributed to the acute site 67308 instead of the Reha twin RH1962 by a Matcher that treats hyphen/space spellings of one operator as one operator
- [x] #4 Every code change has a test and a mutation test (red then green, byte-identical restore); non-network suite 0 failed
- [x] #5 Affected clinics re-crawled with the fixed code; open-posting counts per clinic_id before/after recorded
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Oracle: walk each rexx board live (listing start=N to the board end), count nursing postings per site with classify_role, compare with run-217 inbox notes and live postings.
2. RHÖN 401: add bewerberportal.rhoen-klinikum-ag.com to vendor_adapters.UA_OVERRIDE (existing per-host mechanism) with a UA its bot rule accepts; RED test first with a captured listing fixture + a session that 401s the Chrome UA.
3. Attribution: registry.py Matcher folds hyphen to space in its employer_norm keys (by_name/by_op, en, ops sets, R0_board_name) -- classify.employer_norm (employers identity key) untouched. RED test first. Replay run-217 raw rows old vs new Matcher to measure blast radius.
4. Mutation tests for both, full non-network suite.
5. Re-crawl RHÖN pool (67307,67308,67370,RH1962,RH2129) + record before/after; document not-a-bug sites.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Evidence (2026-09-29, before any change)
Before snapshot: backups/task168-before-open-postings-2026-09-29.json. Open postings before: 67308=0 67307=0 67370=0 RH1962=18 RH2129=0 | 18717=0 RH2891=0 | 17201=0 17202=0 18901=8 18902=3 RH1765=0 | 18713=0 RH1386=7 | 77201=0 77202=5 | RH1289=0.

### RHÖN-KLINIKUM Campus Bad Neustadt 67308 -- TWO root causes
1) Transport: bewerberportal.rhoen-klinikum-ag.com answers HTTP 401, content-length 0, to any UA claiming Chrome, 200 to others. Measured with curl + requests on stellenangebote.html: repo UA (Chrome/125 Linux) 401/0 B; Chrome/131 Windows 401; Chrome/125 + sec-ch-ua/Sec-Fetch headers 401; Firefox/128 200/106238 B; curl default 200; python-requests default 200; Edge UA 200; Safari 200; bare "Mozilla/5.0" 200. crawlers.vendor_adapters.get() sends H (Chrome UA) -> 401 -> crawl_rexx first listing fetch not ok -> loop break -> base None -> return [] (vendor_adapters.py crawl_rexx `if not r or not r.ok: break` / `if base is None: return []`). app/crawl.py _fetch_board then records "transport failure: 0/1 request(s) to this board succeeded" (crawl_issues kind=vendor) -- every day 2026-09-24..29 (first row 2026-09-24), retry pass 3/3 each night (run 217 log lines 532, 1015, 1033: "-> 0 rows ... 0s").
   Live board oracle (listing walked start=0..400 with a Firefox UA, `data-count="337" data-all-count="337"` on the page): 337 ids = data-count; sites Frankfurt (Oder) 98, Marburg 63, Gießen 61, Bad Berka 53, Bad Neustadt an der Saale 53 (+6 multi-site incl. Bad Neustadt). Bad Neustadt 59 jobs through classify.classify_role: 13 pflegefachkraft, 1 sonstige_pflege, 1 hebamme, 2 leitung, 1 apn_experte = 18 in-scope; 4 ausbildung, 37 nicht_pflege. All 18 are in the DB, open, last_seen 2026-09-24 17:27 (stale since the 401 started), and ALL 18 carry clinic_id RH1962 rule R2_operator.
2) Attribution: all posting pages carry JSON-LD hiringOrganization.name "RHÖN-KLINIKUM AG" (employers row 1173 name_norm rhön-klinikum). Live registry operators: 67307/67308/67370 (Krankenhausplan) "RHÖN KLINIKUM AG" -> employer_norm "rhön klinikum"; RH1962 (RHV Reha row, "RHÖN-KLINIKUM Campus Bad Neustadt", 407 beds) "RHÖN-KLINIKUM AG" -> "rhön-klinikum". classify.employer_norm keeps hyphens (`[^\w\säöüß-]`), so registry.Matcher.by_op["rhön-klinikum"] == [RH1962] alone -> _match_content R2_operator `len(c) == 1` -> RH1962 (0.95) for every posting; 67308 (750 beds, the acute hospital: Intensivstation, ZNA, Kreißsaal, Herzchirurgie ads) never reachable. Same hyphen/space operator split exists for 2 more Reha-rollout groups (m&i Ichenhausen 77404/77473 vs RH2404; m&i Klinikbetriebsgesellschaft 77706 vs RH2980/RH1255) -- no other operator or name pair in the 649-row registry differs only by hyphen.

### Not rexx bugs (documented, left as is)
- Schön Klinik Bad Aibling 18717: IS on the jobs.schoen-klinik.de pool (careers_url ...?search_mode=...; run 217 line 60: 295 rows). Live board 298 ids; 14 at Bad Aibling, 0 nursing: classify_role -> 13 nicht_pflege (Arzt x2, Oberarzt, Betriebstechniker, Küchenhilfe, Sprachtherapie-Leitung, MTRA x2, MTA-F/MFA, Physiotherapeut, 2 Praktikanten, Servicekraft; run-217 inbox notes: skipped nicht_pflege) + 1 new ad j16292 "Mitarbeiter (m/w/d) Therapieplanung" (posted after run 217, section tag includes Pflege -> sonstige_pflege fallback; next nightly run picks it up). The www.schoen-klinik.de/bad-aibling-harthausen/karriere/aerzte "0 rows 0s" log line is RH2891 (RHV Reha twin, name "Schön Klinik Bad Aibling SE & Co. KG – Rehabilitation", truncated to 30 chars in the log), whose registry careers_url is a marketing page with no rexx links (crawl_issue kind=empty daily). Same for RH1968 (Berchtesgadener Land Reha, careers_url points at the BAD AIBLING /karriere/aerzte page), RH2116, RH2178.
- Kreisklinik Bad Reichenhall 17201 / Berchtesgaden 17202: jobs.kliniken-suedostbayern.de, data-count 47 = 47 ids read in run 217. Bad Reichenhall-only 5 (2 Ausbildung, Meister Elektrotechnik, Oberarzt Gefäßchirurgie, Schulplatz Auszubildende), Berchtesgaden-only 1 (Physiotherapeut); the 7 four-site ads are Ausbildung/MFA-Ausbildung/BFD/Praktikum/Physician Assistant + "Freigestellte Praxisanleitung (Zentrale Praxisanleitung)" -> 18901. 0 experienced-nursing ads for either town: 0 is correct.
- Wertachklinik Schwabmünchen 77201: karriere-wertachkliniken.de data-count 13 = 13 read. Schwabmünchen-only 3 (Ergotherapeut, Physiotherapeut, Stationssekretär) = 0 nursing; 3 nursing ads list "Bobingen, Schwabmünchen" and are linked to 77202 (R4_tokens_op, city Bobingen). postings.clinic_id holds one clinic -- same data-model limit as München Klinik Neuperlach in TASK-166.
- Simssee Klinik 18713: karriere.gesundheitswelt.de data-count 53 = 53. Bad Endorf nursing 7 (Bereichsleitung Pflege, Pflegefachkraft Akut Orthopädie Station C6, 2x Dauernachtwache, Schmerztherapie, Pflegefachkräfte/Altenpflege, + 1 three-site ad) all linked to RH1386 "Simssee Klinik GmbH" (RHV Reha row, 248 beds) via R0_board_town. Plan-KH row vs RHV Reha row of one campus, bed tie-break favours the Reha row here -- a registry-model question (acute vs Reha twin), not rexx; TASK-166 recorded 18713/RH1386 -> RH1386 as its resolution. Left for a decision.
- Other rexx rows: bbw.de jobportal (RH1289 RPK Lichthof, 18 beds) has the SAME Chrome-UA 401 (transport failure daily) but was NOT added to the UA override: its "real_table" skin (<tr>/real_table_col1 rows, no joboffer_container) would make _rexx_listing_jobs return 1 job per page -- unblocking it would turn a loud failure into a silent 1-per-100 under-read; and filter[client_id][]=4 is gfi gGmbH (100+ Bavaria-wide social/education jobs, 0 nursing at Hof on page 1). klinik-bavaria.com (RH1996), altmuehlseeklinik.de (RH2921), asklepios.com/aidenbach (RH2902), www.schoen-klinik.de/* (RH2891, RH1968, RH2116, RH2178), karriere.medicalpark.de/jobs/ (RH2374): Reha-rollout rows whose careers_url is a marketing/homepage labelled rexx (200 OK, no -j<id>.html links) -> kind=empty daily. Registry careers_url follow-up, not an adapter bug. All other rexx boards serve the Chrome UA fine (klinik-ebe, kliniken-suedostbayern, schoen-klinik, wertachkliniken, gesundheitswelt, khagatharied, klinikbavaria-portal, isarklinikum, drbecker: 200 to both UAs).

## Fix (worktree /home/exedev/repo/.claude/worktrees/grpB1, branch grpB1-rexx, NOT committed -- Edit/Write to the shared checkout was blocked for this agent)
Worktree = HEAD 14cacc4 + the main tree uncommitted diff (git diff HEAD, 422 lines, byte-identical re-check before the crawl) + untracked tests/test_board_pool_union.py, tests/test_cli_manual_override.py; grep confirmed cli.py manual_posting_ids (l.416) and _pooled (l.478). Patch of MY changes only: /tmp/grpB1/b1.patch (git apply --check passes on the main checkout).
1) crawlers/vendor_adapters.py UA_OVERRIDE: + "bewerberportal.rhoen-klinikum-ag.com" -> Firefox/128 Linux UA (existing per-host mechanism from TASK-114; exact host only, www.rhoen-klinikum-ag.com serves the Chrome UA fine). All 337 RHÖN job URLs are on that one host.
2) pflege_jobs/registry.py: module-local employer_norm = classify.employer_norm with "-" folded to space and whitespace re-collapsed; all 7 Matcher uses (by_name/by_op keys, en in match/_match_content, R3/R4 and R0 same-operator `ops` sets, R0_board_name) now see one spelling. classify.employer_norm (employers.name_norm identity key) untouched; no other module imports registry.employer_norm (grep).
Why not a registry data fix (set 67307/67308/67370 operator to the hyphen form): a live DB write needs Ivan, and the RHV sync / Krankenhausplan re-import would re-create the split; the code fold also covers the two m&i groups.
Tests (new): tests/test_completeness_rexx.py::test_rexx_reads_the_rhoen_board_that_401s_a_chrome_user_agent (real get() through a session that 401s any Chrome UA, fixtures tests/fixtures/board_samples/rexx_rhoen_*: listing slice with data-count=337 + j1232 article, ?start=400 end page, j1232 JSON-LD with 2 phone numbers redacted); tests/test_mech_clinic_link.py::test_hyphen_and_space_spellings_of_one_operator_are_one_operator (the 5 live Bad Neustadt rows -> ("67308", "R6_ambiguous_sites:67307,67308,67370,RH1962", 0.5)); ::test_a_spaced_hyphen_and_an_en_dash_spell_one_employer_name (RH2968 shape found by the replay below).
RED on unfixed code: rexx test `assert [] == [(...j1232..., FULL_TIME)]`; matcher test got ("RH1962", "R2_operator", 0.95).
Mutations (copy to /tmp/grpB1/mut_orig, break, run, restore from the copy, rm -rf __pycache__, re-run, diff -q byte-identical -- all 3 identical):
 M1 drop the RHÖN UA_OVERRIDE entry -> 1 failed (test_rexx_reads_the_rhoen_board...), restored 3 passed.
 M2 employer_norm returns the unfolded key -> 2 failed (both matcher tests), restored 3 passed.
 M3 fold without whitespace re-collapse -> 1 failed (test_a_spaced_hyphen_and_an_en_dash...), restored 3 passed.
Full suite in the worktree (env sourced, data/app.sqlite seeded from main via .backup): `pytest -p no:cacheprovider -m "not network" -q` -> 1551 passed, 18 skipped, 2131 deselected, 0 failed (7:18). After a blank-line-only style edit in registry.py: matcher/rexx/mechanics tests 94 passed.
## Blast radius of the Matcher change: replay of ALL 22898 run-217 raw rows (/tmp/grpB1/replay_hyphen.py: real cli._drain_local_once with pending_board_pools, sink stubbed, old key vs new key, 649 live clinics)
old 2960 linked observations / 1960 postings; new 2964 / 1964. Exactly 4 postings change, all unmatched -> RH2968 R1_exact: gro.jobs.personio.de 1942658, 2444250, 2444254, 2716673 (employer "Geriatrische Rehabilitation Oberbayern - Bruckmühl GmbH" vs RHV name with an en dash; city "Stationäre Reha, Bruckmühl" names no registry town, so R0_board refused them on RH2968 own board). Live: posting_ids 15109-15112, open, clinic_id NULL. RHÖN rows are not in run 217 (401), so they do not appear in this replay.

## Re-crawl: run 916701 (worktree app.sqlite = .backup of main data/app.sqlite, crawl_runs sequence bumped to 916700 so ids never collide with main runs), trigger manual-task168, mode adapter, clinics 67307,67308,67370,RH1962,RH2129,RH2968, 17:31-17:35 UTC, status done, credits 0, firecrawl 0.
Log: "rexx https://bewerberportal.rhoen-klinikum-ag.com/stellenangebote -> 337 rows (...) 117s" (= the board data-count 337; nightly runs since 2026-09-24: 0 rows 0s transport failure); "personio https://gro.jobs.personio.de/ -> 13 rows". Drain: sqlite inbox rows 350, observations 22 (18 RHÖN Bad Neustadt + 4 Bruckmühl), kez-linked 22, dropped_non_pflege 0, postgres inbox rows 0, clinic links pushed 22, CONFLICT none, 22 postings touched / 0 new, nothing retired.
After snapshot: backups/task168-after-open-postings-2026-09-29.json. Open postings before -> after:
 67308 RHÖN-KLINIKUM Campus Bad Neustadt 0 -> 18 (all rule R6_ambiguous_sites:67307,67308,67370,RH1962, last_seen 2026-09-29 17:33)
 RH1962 (RHV Reha row) 18 -> 0; 67307 0 -> 0; 67370 0 -> 0; RH2129 0 -> 0
 RH2968 Geriatrische Reha Bruckmühl 0 -> 4 (R1_exact; posting_ids 15109-15112, were open with clinic_id NULL)
 not re-crawled, unchanged (not rexx bugs, see above): 18717 0, RH2891 0, 17201 0, 17202 0, 18901 8, 18902 3, RH1765 0, 18713 0, RH1386 7, 77201 0, 77202 5, RH1289 0.
Side effect to know: the run-end verify (app/crawl.py _verify_ids -> verify_all(firecrawl=False)) re-verified the 22 touched postings: 4 Bruckmühl live; the 18 RHÖN went verify_status live -> error ("200 but title not found (JS-rendered or list page)"): pflege_jobs/verify.py sends its own Chrome/126 UA (401 on this host), the Playwright render rung does not see the title either, and only the Firecrawl rung passes (this morning 05:17 verify: all 18 "live ... [firecrawl]"). status stays open and jobs_open counts them; jobs_live/proof chip for 67308 reads error until the next scheduled verify. Nightly runs verify only NEW ids when there are any, so this mainly hits newly posted RHÖN ads until the follow-up below.
## Follow-ups (not started, need Ivan/coordinator OK)
1. pflege_jobs/verify.py (l.28 UA, l.92/l.420 session.get) ignores crawlers.vendor_adapters.UA_OVERRIDE -> every RHÖN posting check 401s over plain HTTP and burns a Firecrawl credit per posting per day (18 today) or lands on error; honour the same per-host UA there.
2. bbw.de jobportal (RH1289): same Chrome-UA 401, plus real_table skin unparsed by _rexx_listing_jobs, plus filter[client_id][]=4 = gfi gGmbH -- adapter + registry question, tiny site.
3. Reha-rollout rows labelled rexx whose careers_url is a marketing page (RH1996 klinik-bavaria.com -- real board is klinikbavaria-portal.rexx-systems.com like 67274; RH2921; RH2902; RH2891; RH1968 pointing at the Bad Aibling page; RH2116; RH2178; RH2374 karriere.medicalpark.de) -> crawl_issue kind=empty every night.
4. Plan-KH vs RHV-Reha twin rows of one campus (Simssee 18713/RH1386: Bad Endorf ads go to the Reha row by bed count; RHÖN now goes to the acute row by bed count) -- needs a policy, not a heuristic.
5. crawl_rexx does not read the listing own data-count (present on the RHÖN, Südostbayern, Wertach, Gesundheitswelt skins; absent on jobs.schoen-klinik.de) -- could feed TASK-88 board_total under-read alarm.
6. Merge: apply /tmp/grpB1/b1.patch to the main checkout (git apply --check passes) and run the non-network suite there; worktree .claude/worktrees/grpB1 can then be removed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
RHÖN-KLINIKUM Campus Bad Neustadt (67308, 750 beds) had two causes for 0 vacancies: its rexx board (bewerberportal.rhoen-klinikum-ag.com) 401s any Chrome User-Agent, so every nightly run since 2026-09-24 read 0 of 337 postings; and the matcher treated "RHÖN-KLINIKUM AG" (RHV Reha row RH1962 + every posting) and "RHÖN KLINIKUM AG" (Krankenhausplan rows) as different operators, so all 18 Bad Neustadt nursing ads went to the Reha row. Fixes: per-host Firefox UA in vendor_adapters.UA_OVERRIDE; registry.py matcher-local employer_norm folds hyphens to spaces (classify.employer_norm, the employers identity key, is untouched). Verified: 3 new offline tests red on old code, 3 mutations red then byte-identical green; non-network suite 1551 passed / 0 failed (worktree); run-217 replay (22898 rows) changes only 4 postings (unmatched -> RH2968, correct); live re-crawl run 916701 read 337/337 rows, 67308 0 -> 18, RH1962 18 -> 0, RH2968 0 -> 4. 18717, 17201, 17202, 77201, 18713 are not rexx bugs (board has 0 nursing ads for the town / multi-site ad on the sibling / Reha twin) -- evidence in notes. Code is in worktree .claude/worktrees/grpB1; NOT yet in the main checkout: apply /tmp/grpB1/b1.patch (git apply --check OK). Side effect: the 18 RHÖN postings verify_status went live -> error in the run-end verify (verify.py Chrome UA, follow-up 1) until the next scheduled verify.
<!-- SECTION:FINAL_SUMMARY:END -->
