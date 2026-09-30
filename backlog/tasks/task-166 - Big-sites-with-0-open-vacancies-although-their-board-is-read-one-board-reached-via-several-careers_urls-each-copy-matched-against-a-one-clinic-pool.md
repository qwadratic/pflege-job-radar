---
id: TASK-166
title: >-
  Big sites with 0 open vacancies although their board is read: one board
  reached via several careers_urls, each copy matched against a one-clinic pool
status: Done
assignee:
  - '@claude'
created_date: '2026-09-29 15:07'
updated_date: '2026-09-29 16:55'
labels:
  - matching
  - db-quality
dependencies: []
priority: high
ordinal: 164000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-29: several big registry sites show zero open vacancies although their board is crawled every night (run 217). Group A = board IS read but postings land on a different clinic or get lost between board and DB. Cases: Bezirksklinikum Mainkofen 27105 (562 beds), München Klinik Neuperlach 16203 (545), Klinik Hohe Warte 46204 (316), Medical Park Bad Wiessee St. Hubertus RH1435 (432) and Bad Rodach RH1257 (412), Passauer Wolf Bad Gögging RH2733 (477).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each case has a root cause stated with live evidence (board data + code line), fixed or explicitly explained as not-a-bug
- [x] #2 Affected clinics re-crawled with the fixed code; open-posting counts per clinic_id before/after recorded
- [x] #3 Every code change has a test and a mutation test; non-network suite 0 failed
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Union the board pools of every queued copy of one posting (same source_ref) before matching: inbox_db.pending_board_pools(), read once in cmd_inbox before the first ack, applied via cli._pooled() in both _process_rows branches.
2. Drop the seed_kez fallback in _process_rows' observation branch.
3. One clinic_link per posting at the cmd_inbox push; copies that still disagree push nothing and print CONFLICT.
4. parse_job_page: '<hN>Stellenangebot in <Ort></hN>' heading as posting city (Medical Park).
5. Tests + mutation tests, full non-network suite.
6. Re-crawl affected clinics in one run from the worktree; before/after counts; propose DB correction for links a re-crawl cannot fix (needs Ivan's go-ahead).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Evidence (2026-09-29, before any change)
Before snapshot: backups/task166-before-open-postings-2026-09-29.json (open postings per clinic_id + rows).
Open counts before: 27105=0 RH2143=3 26205=1 | RH2733=0 27307=0 RH1892=4 27511=2 37609=2 | RH1435=0 RH1257=0 RH1503=6 RH2014=7 (all 13 medicalpark postings, city 'Bad Feilnbach') | 16203=0 16202=19 16201=1 16204=1 16205=3 | 46204=0 46201=38.

### Shared mechanism (cases 1, 4, 5)
crawlers.routing.plan groups boards by EXACT careers_url. The same real board is registered under several URLs:
- Mainkofen mein-check-in tenant 'mainkofen': 27105 (mainkofen.de/karriere-bkm/aktuelle-jobs/), RH2143 (www.mainkofen.de/), 26205 (mein-check-in.de/mainkofen/) -> 3 fetches x 42 rows in run 217 (log lines 293, 302, 838).
- karriere.passauerwolf.de softgarden: '/' (27307,27511,37609,RH2733,RH2794), no-slash (RH2939), benefits-karriere.passauerwolf.de (RH1892) -> 3 x 46 observations.
- karriere.medicalpark.de wp_jobs: 9 careers_urls -> 9 x 103 rows, 929 raw rows in run 217, ~390 s each.
Each fetch tags its copy with a one-clinic (or partial) pool (app/crawl.py _vendor_rows board_clinic_ids / o['_board'] line 847); pflege_jobs/cli.py _process_rows matched every copy independently; cmd_inbox pushed one clinic_link PER COPY; edge/pflege-ingest clinic_links does `update postings ... from json_to_recordset` -> with duplicate posting_ids Postgres applies an unpredictable one.
Run-217 process_notes prove it, e.g. mein-check-in position-430614: inbox 218082 (board [26205]) 'no site match', 218241 (board [27105]) '-> 27105', 230402 (board [RH2143]) '-> RH2143'. Stored: RH2143. softgarden:64935176 (Neustadt a.d. Donau): 218380 board [27307,27511,37609,RH2733,RH2794] '-> RH2733', 230081 board [RH1892] '-> RH1892', 232123 board [RH2939] '-> RH2939'. Stored: RH1892.
Case 1 42 rows -> 4 postings is NOT loss: live board has 42 positions, 4 nursing (3 Deggendorf + 1 'am BKH Passau'); rest are doctors/therapists/Ausbildung/kitchen.

### seed_kez fallback (case 5)
_process_rows observation branch: `o['_kez'] = (mt[0] if mt else None) or o.get('_kez')` -- softgarden single-site seeds preset _kez = seed clinic (career_crawl._base). With a board pool passed the Matcher returns None only when the posting's city is elsewhere/ambiguous, so the fallback re-applies the refused guess. Live 2026-09-29: 22 open postings carry clinic_match_rule=seed_kez, 22/22 in a town different from their clinic (3x RH1892 Ingolstadt <- Neustadt a.d. Donau, 13x RH1017 Peiting <- München/Schäftlarn/..., RH2190, RH1280, 16105, 18801).

### Case 4 Medical Park: no location field
karriere.medicalpark.de postings have no JSON-LD; the only site statement is '<h4>Stellenangebot in Bad Rodach</h4>' above the <h1> (103/105 URLs in run 217). parse_job_page returned loc city None -> _vendor_rows stamped the seed clinic's town (city_source=seed) on every row incl. Berlin/Bad Camberg/Bad Sassendorf/Mönchengladbach postings -> in_bavaria True. DB: all 13 medicalpark postings city 'Bad Feilnbach' (last seed), links RH2014/RH1503.

### Case 2 München Klinik Neuperlach 16203 -- not a matcher bug; data-model limit (follow-up)
Live allJobs blob on https://www.muenchen-klinik.de/stellenmarkt/ (58 jobs, 2026-09-29): 23 nursing-ish jobs, each with locations[]. Single-site: Harlaching 4, Bogenhausen 3, Schwabing 1, Thalkirchner 1 -- these already resolve R1_exact via crawlers/vendor_adapters.py crawl_muenchen_klinik (TASK-129). NONE lists Neuperlach alone. 7 list Neuperlach among 2-5 sites (e.g. Innere Medizin/Chirurgie/Intensiv: all 5 or 3-4 sites). Those carry org 'München Klinik gGmbH' -> Matcher R2_operator_town tie -> R6_ambiguous_sites -> _pick_site by beds -> 16202 Harlaching (660). postings.clinic_id holds ONE clinic, so a 5-site posting cannot show on Neuperlach without either a multi-clinic link model or a guess. Sub-defect: R6 picks among ALL 5 operator sites even when the posting lists fewer (live: 'Pflegefachkraft Onkologie, Hämatologie und Stammzellentherapie/KMT' lists Bogenhausen+Neuperlach only, lands on Harlaching). Not changed here -- needs a decision (multi-site link model vs. pick-within-listed-sites); proposed as follow-up.
### Case 3 Klinik Hohe Warte 46204 -- not a bug in the sense asked; board mostly doesn't state the site
Live softgarden feed https://karriere.klinikum-bayreuth.de/jobs.feed.json (111 items): hiringOrganization.name 'Klinikum Bayreuth GmbH' on all -> employer_norm == 46201's own name -> R1_exact 46201. jobLocation.streetAddress '-' on 107/111. Only 2 nursing ads state Hohe Warte: 'Stellvertretende Stationsleitung ... Schädel-Hirn-Verletzte/Neurorehabilitation Phase B' (streetAddress 'Hohe Warte 8') and 'Pflegefachkräfte für die Urologie' (hiringOrganization.industry 'Klinik Hohe Warte'). Several others are explicitly both sites (Springerpool 'an den beiden Betriebsstätten', Notaufnahmen 'zwischen der Notaufnahme Hohe Warte, der Notaufnahme Klinikum'). The softgarden path reads neither streetAddress nor industry for site choice. Left as is; follow-up for the 2 structured signals.

## Implementation (worktree /home/exedev/repo/.claude/worktrees/task166, branch task166-group-a, NOT committed; shared checkout was write-blocked for this agent)
Worktree = HEAD 14cacc4 + the shared tree's uncommitted cli.py/bite.py/test_bite.py/test_cli_manual_override.py (other session, manual override) + my changes. Patch of MY changes only: /tmp/grpA/task166.patch (git apply --check passes on the main checkout as of 16:50 UTC).
Files changed by me: pflege_jobs/inbox_db.py (+pending_board_pools), pflege_jobs/cli.py (_pooled, pools kw through _drain_once/_drain_local_once/_process_rows, cmd_inbox reads pools once before first ack, seed_kez fallback removed, one-link-per-posting + CONFLICT print at push), crawlers/vendor_adapters.py (STELLENANGEBOT_IN_RX in parse_job_page non-JSON-LD branch), tests/test_board_pool_union.py (new, 5 tests).
Why a union and not VENDOR_ACCOUNT_POOLS entries: the same shape hits 3 operators here and more elsewhere (replay below); the union is provenance (boards that actually served the URL this run), no curated list to keep in sync with the Reha registry rollout that created RH2143/RH1892/RH2939-type duplicate careers_urls.

## Tests
tests/test_board_pool_union.py: Mainkofen 3-copy shape (-> 27105 / 26205, one link each), seed_kez refused-match (no link), observation copies pooled (-> RH2733), still-disagreeing copies (no link + CONFLICT line), Medical Park heading -> city.
Mutation (copy to /tmp, break, red, restore from /tmp copy, rm __pycache__, green, diff -q byte-identical), all 5 red then green:
M1 pools={} in cmd_inbox -> 1 failed; M2 seed_kez fallback restored -> 1 failed; M3 heading ignored -> 1 failed; M4 conflict filter removed -> 3 failed; M5 pool key = source_host -> 1 failed.
Full suite (worktree, env sourced): `pytest -p no:cacheprovider -m "not network" -q` -> 1 failed, 1545 passed, 18 skipped. The 1 failure was tests/test_ontology.py::test_career_profiles_is_empty... = sqlite 'no such table: career_profiles' because the fresh worktree had no data/app.sqlite; passes in the main checkout and in the worktree after seeding app.sqlite from main (10 passed). Not related to this change.

## Whole-run replay (run 217 raw rows, 3072 linked copies, real _process_rows, sink stubbed; tools in /tmp/grpA/replay.py, diag.py)
Postings with conflicting per-copy links: 64 -> 8 (remaining 8: content-side R3_tokens on seed-inherited employer+city, e.g. gkg-bamberg.de 47101/47102, johannesbad RH1171/RH2025, kbo umantis 16257/17706 -- now pushed as CONFLICT, not linked, instead of a coin flip).
97 postings change attribution. Correct resolutions: 27105/RH2143 -> 27105 (3), Gesundheitswelt 18713/RH1386 -> RH1386 (6) and 18721/RH2091 -> RH2091, Klinikverbund Allgäu 76301/RH2748 -> 76301 (4), 19002/RH2408 -> 19002. Medical Park 13 postings: 8-way conflict -> resolved by the page city on live crawl. ~65 go from a one-board guess to unmatched: postings whose page states NO city and NO employer (both seed-inherited) on a board reached via several URLs -- InnKlinikum RH2811 (16), Donau-Ries 77901 (12), MSP Lohr 67702/RH2855 (11), Klinik Wartenberg 17705/RH2000 (9), Sana Cham 37202 (6), Lubos 16236/16239 (4), Bad Trissl RH1841, medbo RH2511 (Pflegeschule postings on a reha site), Schön München 16260, Asklepios Bad Tölz 17302/RH2777, BKH Lohr 66104. Before: whichever one-clinic board's copy won. Unmatched copies push no link, so existing DB links stay; only brand-new postings on those boards stay unlinked. This is decision-5 ('no match beats a wrong match') applied, flagged for Ivan.

## Re-crawl: run 916601 (worktree app.sqlite, sequence bumped to 916600 to avoid colliding with main run ids), trigger manual-task166, 24 clinics, 17 boards, 1211 raw rows, 15:40-16:43 UTC, status done. Drain: 135 observations, CONFLICT none, clinic links pushed 19. Medical Park rows: all 105 URLs now carry a page city (Bad Feilnbach 19, Bad Rodach 18, Berlin 15, Bad Wiessee 14, Bad Camberg 10, Bernau 7, Bad Sassendorf 7, Amerang 6, Roth 3, ...), none seed-stamped.
After snapshot: backups/task166-after-open-postings-2026-09-29.json. Open postings before -> after:
27105 0->3 (R0_board_town_bestsite), RH2143 3->0, 26205 1->1 | RH2733 0->3, RH1892 4->1 (the remaining one is Hygienefachkraft, city Ingolstadt = correct) | RH1257 0->4, RH1435 0->2, RH2014 7->5, RH1503 6->2 | 16203 0->0, 16202 19->19 (not re-crawled, no code path changed) | 46204 0->0, 46201 38->38 (not re-crawled).
Stale wrong links a re-crawl cannot clear (pooled match is None -> no push): 6376, 6378 (city Bad Camberg = Hessen, on RH2014 Prien), 12735 (Bad Feilnbach, on RH2014), 12739 ('Pflegedirektor ... Reithofpark und Blumenhof', on RH1503 Loipl), 6377 (Bad Feilnbach on RH2014) and 12741 (Bad Feilnbach on RH1503) -- last two not seen in this crawl (last_seen 09:33). Bad Feilnbach has two sites with different operators (RH2743 Blumenhof, RH2617 Reithofpark), so unmatched is the honest state. Proposed write (NOT executed, needs Ivan): clinic_links push {posting_id, clinic_id: null, clinic_match_rule: null} for 6376, 6377, 6378, 12735, 12739, 12741 after backing up those rows. Same question for the 19 other open seed_kez postings (RH1017 Peiting x13 from diakonie-muenchen.softgarden.io, RH2190 x2, RH1280, 16105, 18801) which the removed fallback no longer re-creates but a re-crawl won't clear.

## Open / follow-ups (not started, need Ivan's OK)
- München Klinik multi-site postings (single clinic_id model); R6 picks among all 5 operator sites even when the posting lists fewer.
- Klinikum Bayreuth: read softgarden jobLocation.streetAddress / hiringOrganization.industry for the 2 Hohe-Warte-stated ads.
- Seed-inherited employer+city still circularly R3-token-matches the seed when its town has one registry clinic (source of the 8 remaining conflicts).
- Clinic-scoped crawls still see only their own board's pool (e.g. crawling RH2143 alone would put Deggendorf postings back on RH2143); nightly full runs are correct.
- karriere.medicalpark.de is walked 10x per night (~390 s each) because of the 10 careers_urls.
- Merge the worktree changes into the main checkout (patch above).

MERGE + ROLLOUT (2026-09-29 ~16:45-16:55 UTC, Ivan approved items 1/2/3):
1. Patch /tmp/grpA/task166.patch applied to the main checkout (git apply; files: crawlers/vendor_adapters.py, pflege_jobs/cli.py, pflege_jobs/inbox_db.py, new tests/test_board_pool_union.py). cli.py keeps manual_posting_ids (TASK-163 guard) -- grep confirmed lines 416/690.
   Full non-network suite in the MAIN checkout (not the worktree): 1548 passed, 18 skipped, 2131 deselected, 0 failed (7 min). The worktree-only test_ontology failure does not reproduce here.
   pflege-web restarted at 16:54:48 UTC (no run was queued/running; HTTP 200 after; no errors in journal; both schedules still enabled: 03:00 full pass, 05:17 verify) so tonight's nightly run uses the pooled matcher.
2. Trade-off accepted by Ivan: postings with neither city nor employer on a multi-site board now stay unmatched instead of getting the board's guessed clinic.
3. One-off unlink executed: 25 open postings set clinic_id=null, clinic_match_rule=null (NOT 'manual', so a future crawl may re-link them correctly if a real match appears): the 6 stale R0_board links (6376, 6378 Bad Camberg/Hessen and 6377, 12735 on RH2014; 12739, 12741 on RH1503) + all 19 open seed_kez postings. Backup: backups/task166_unlink_before_20260929T164815Z.json. Read back: 0 of 25 still linked.
Live after (open postings per clinic): 27105 Mainkofen 3, RH2143 0, 26205 1, RH1257 Bad Rodach 4, RH1435 Bad Wiessee 2, RH2014 5, RH1503 2, RH2733 Bad Gögging 3, RH1892 1; 16203 Neuperlach 0 and 46204 Hohe Warte 0 (documented not-a-bug).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
One board reachable via several careers_urls was fetched once per URL and each copy matched against a one-clinic pool, so the DB kept a random copy's link. Copies now share the union of their board pools, one link per posting, CONFLICT = no link; seed_kez fallback removed; Medical Park city read from the ad heading. Verified: 5 new tests + 5 mutation tests, 1548 passed/0 failed in main checkout, live re-crawl moved Mainkofen/Medical Park/Passauer Wolf postings to their real sites, 25 stale wrong links cleared with backup. Neuperlach and Hohe Warte are not matcher bugs (ads don't name the site).
<!-- SECTION:FINAL_SUMMARY:END -->
