---
id: TASK-171
title: >-
  Zero-vacancy sites whose board IS read (group G): BKH Lohr 67705, Bad
  Koetzting RH2682, Asklepios Bad Abbach RH2772, MediClin Roter Huegel RH2547,
  Haus am Kurpark RH1970
status: Done
assignee:
  - '@claude'
created_date: '2026-09-29 20:11'
updated_date: '2026-09-29 21:39'
labels:
  - crawler-coverage
dependencies: []
priority: high
type: bug
ordinal: 169000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-29: registry sites with 0 open vacancies. Group G = the nightly log (run 217) shows the site's board being read (rows > 0, or a jobs page with visible postings), yet 0 open postings end up on the site. For each site the question is whether the row loss is real (parse failure, dedup, wrong attribution, wrongly classified non-nursing) or correct (board has no nursing jobs, or its jobs belong to another registry site). Run 217 log lines: concludis karriere.bezirkskrankenhaus-lohr.de -> 5 rows (66105/67705) plus a second fetch of the same board via 66104's careers_url; wp_jobs reha-badkoetzting.de -> 11 rows; wp_jobs asklepios.com/bad-abbach -> 10 rows plus the Asklepios-wide board 1386 rows; typo3_jobs mediclin-karriere.de/reha-zentrum-roter-huegel -> 2 rows; hausamkurpark.de -> 0 rows (jobs page lists 2 nursing postings). Constraints: classifier (patterns.json role rules / classify.py) is off-limits (pending Ivan's -pfleger decision); registry rows and posting links are written only after Ivan approves.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each of the 5 sites has its live nursing-vacancy count stated with evidence (board fetched today) and its 0 classified as genuine or as a crawler/registry/attribution defect, with the rows traced to where they went
- [x] #2 Each defect is fixed in code (red-green test from a live-captured fixture, mutation-tested) and/or a prepared registry correction (corrections JSON for tools/apply_clinic_corrections.py plus the matching data/registry/clinics.csv patch)
- [x] #3 A proving crawl from the worktree (registry corrections applied in memory only) shows before -> after open-posting counts per clinic_id, queried live
- [x] #4 Postings that must be relinked or retired are listed by posting_id for Ivan's approval, not written
- [x] #5 Offline suite (-m 'not network') ends 0 failed in the worktree
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Live oracle per site BEFORE code: fetch each board today and count its experienced-nursing postings (classify_role on the board's own titles); trace run 217's rows for the board in data/inbox.sqlite (process_note) and the live postings table.
2. Classify each 0: genuine (no nursing / jobs belong to another registry site) vs defect.
3. Defects (worktree grpG, red-green from live-captured fixtures, mutation-tested): (a) MediClin brajobsmdc teaser list is invisible to crawl_wp_jobs (links '-wmd-' slug + 'zur Stellenanzeige' text) -> dedicated delegate reading the teaser list, its own 'von insgesamt N' total and its AJAX pagination; (b) Haus am Kurpark lists postings as plain <li> text -> TITLE_ONLY_SITES entry; (c) BKH Lohr: 66104 careers_url is a dead single-job page (separate one-clinic board) -> registry correction; multi-site posting 10076 -> relink proposal for Ivan.
4. Registry corrections JSON + clinics.csv patch; proving crawl from the worktree with corrections in memory; before/after open counts per clinic_id.
5. Suite 0 failed; patch /tmp/grpG/g.patch; notes.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Live oracle (2026-09-29 ~20:00 UTC, boards fetched today; nursing = titles the intake keeps: classify_role + EXCLUDED_ROLE_CLASSES, classifier untouched)
- 67705 BKH Lohr (295 beds) / 66104 Tagesklinik Aschaffenburg (0 beds) / 66105 Psych. Klinik Aschaffenburg (50 beds), one WordPress board karriere.bezirkskrankenhaus-lohr.de: wp-json/wp/v2/jobs X-WP-Total 7 = wp-sitemap-posts-jobs-1.xml 7 urls. 1 experienced-nursing posting: 'Pflegefachkraft (m/w/d) in Lohr a.Main, Aschaffenburg oder für unseren Springerpool' (= posting 10076). Others: Facharzt, Elektroniker, Ausbildung Pflegefachkraft, FSJ, Praktikum/Famulatur, Ehrenamt. The crawl's '5 rows' = the 4 gendered pages + 1 Praktikum PDF; FSJ/Praktikum/Ehrenamt have no gender-marked title and are dropped as non-postings -- none is nursing, no loss.
- RH2682 Bad Kötzting: jobs-sitemap.xml = 6 jobs (Ergotherapeut, Küchenhilfe, Diätassistent/in, Aushilfsfahrer, Chefarztsekretär/in, Koch/Köchin) + the /jobs/ archive; the whole Stellenangebote page and /karriere/ contain 'Pflege' only in staff-interview quotes. GENUINE 0.
- RH2772 Asklepios Bad Abbach Reha (268 beds): its own Stellenangebote page '10 Ergebnisse', 4 nursing (Pflegefachkraft Aufnahmestation / ASV Rheuma / Notaufnahme / Anästhesie und Intensivmedizin), every card labelled 'Asklepios Fachkrankenhaus Bad Abbach'. The same 4 (and nothing else nursing for Bad Abbach) are on the Asklepios-wide board. All 4 stored and open on 27306 'Asklepios Klinikum Bad Abbach' (the Krankenhausplan acute row, same operator 'Asklepios Klinikum Bad Abbach GmbH'): postings 5366, 5372, 5378, 5558, rule R1_exact (hiringOrganization = 27306's exact name). Their text mentions the Reha only in the 'about the Klinikum' boilerplate; the jobs are acute departments. NOT A BUG: the nursing jobs belong to the acute twin.
- RH2547 MediClin Roter Hügel (284 beds): page says '1-10 von insgesamt 10 Ergebnissen', 2 nursing (Pflegedienstleitung (w/m/d) -> leitung; Gesundheits- und Krankenpfleger / Altenpfleger (w/m/d) -> pflegefachkraft). Run 217 read 0 of them: 2 rows = nav pages /jobs/ and /karrierestart/stellenangebote-fuer-nachwuchsfuehrungskraefte/ (both nicht_pflege). DEFECT (adapter).
- RH1970 Haus am Kurpark (Bad Königshofen): 'aktuelle Stellenanzeigen' = 5 bare <li> (no link, no detail page), 2 nursing ('Mitarbeiter*in für unseren Pflegedienst (m/w/d) in Teil- oder Vollzeit' / '... bevorzugt im Nachtdienst' -> sonstige_pflege, kept). Run 217: 0 rows, empty+degraded. DEFECT (adapter).

## Where run 217's rows went (data/inbox.sqlite process_note, main checkout)
- Lohr: the board was fetched TWICE -- via 66104's careers_url (a dead job page '.../jobs/mitarbeiterin-mitarbeiter-m-w-d-fuer-die-kreditorenbuchhaltung/', live 404) as a one-clinic board [66104], and via the root for [66105, 67705]. inbox 221682 (board [66104]) 'loaded -> 66104'; 221687 (board [66105,67705]) 'loaded (no site match)'. Both copies carry org_source=seed and city_source=seed (org = the triggering clinic's own registry name, city Aschaffenburg). Stored: 10076 open on 66104, clinic_match_rule R0_board (the one-clinic-pool rung), NOT R1_exact. TASK-118 (case 2) had read the employer 'Tagesklinik Aschaffenburg des BKH Lohr am Main' as page-stated; the observation's own payload says employer_source=seed -- the page never names the Tagesklinik (its <title> suffix is 'Karriere im Bezirkskrankenhaus Lohr').
- Kötzting: 11 rows = 6 jobs + /jobs/ archive (titled with the first card, 'Chefarztsekretär/in ...'), 3 /job_category/ pages and the listing page itself -- all 'skipped: nicht_pflege'.
- Bad Abbach: 10 rows (own page) + 12 Bad Abbach rows on the Asklepios-wide board; the 4 nursing ones 'loaded -> 27306' from both boards.
- MediClin: 231246 /jobs/ 'Stellenangebote bei MEDICLIN', 231247 /karrierestart/... -- both nicht_pflege.
- Haus am Kurpark: no row (0 rows, crawl_issue empty + degraded).

## Verdict per site
- RH2682 Bad Kötzting: GENUINE 0 (6 open jobs, none nursing).
- RH2772 Asklepios Bad Abbach Reha: NOT A BUG -- its board's 4 nursing ads are acute-department ads of the same Klinikum and sit on the acute twin 27306 (5366/5372/5378/5558, R1_exact). A Reha-side nursing ad would need to name the Reha; none does.
- RH2547 MediClin Roter Hügel: DEFECT, adapter -> fixed (crawl_mediclin).
- RH1970 Haus am Kurpark: DEFECT, adapter -> fixed (TITLE_ONLY_SITES entry).
- 67705 BKH Lohr: DEFECT, registry + attribution. 66104's careers_url is a dead job page (404), planned as its own one-clinic board; R0_board's single-clinic rung linked the board's only nursing ad (10076, multi-site: 'in Lohr a.Main, Aschaffenburg oder für unseren Springerpool') to the 0-bed day clinic 66104 on its first sighting (2026-09-06). Since TASK-166's pooling the copies share [66104, 66105, 67705] and the matcher correctly returns no match (org and city both seed-copied), so nothing re-links it -- the stale link stays. Fix = registry correction (one board) + a relink decision for Ivan (TASK-122 AC#3's open multi-site question). No code change.

## Code (worktree grpG, red-green from live-captured fixtures, mutation-tested)
1. crawlers/vendor_adapters.py crawl_mediclin (new crawl_wp_jobs delegate, fingerprint data-ctype="brajobsmdc_jobs" on the careers page, probed like onapply/asklepios): reads every teaser card (title, category labels -> section_labels, facility -> org with org_source None, town -> loc); board_total = the page's own 'von insgesamt N'; cards past the first 10 via the filter form's AJAX endpoint (form's hidden fields, page=k, jobfilter=0 -- jobfilter=1 returns page 1 again, verified live); stops on the board's own total; an unreadable page or a page adding no card -> RuntimeError (the endpoint repeats its last page past the end, verified live); detail page read with parse_job_page for description; unreadable detail -> row skipped, app/crawl.py records the under-read against board_total. Live: Roter Hügel 10/10 rows, Klinikum Soltau 31/31 over 4 pages (all facility 62).
2. TITLE_ONLY_SITES['hausamkurpark.de'] = <li>([^<]+)</li> (the page's only unlinked <li>s are the 5 postings; menu <li>s all carry <a>). Live: 5 rows.
Tests: tests/test_mediclin.py (5: Roter Hügel 10 cards + intake keeps exactly the 2 nursing; Gernsbach 11 = 10 + AJAX page 2 with the form's scope; unreadable page raises; no-new-card page raises; no widget -> []), tests/test_completeness_wp_jobs.py::test_haus_am_kurpark_postings_listed_as_bare_list_items_are_read (5 titles, intake keeps the 2 Pflegedienst ones). Fixtures (README entries added): mediclin_roter_huegel_sample.html, mediclin_detail_guk_sample.html, mediclin_gernsbach_sample.html, mediclin_gernsbach_page2_sample.json, hausamkurpark_ihre_karriere_sample.html. RED before the code: 6 failed; GREEN after: 6 passed.
Mutation (/tmp/grpG/mutate.py, log /tmp/grpG/mutation.log): 10 mutations -- M01 delegate not wired, M02 no paging, M03 jobfilter not reset to 0, M04 unreadable page -> silent break, M05 no-new-card -> silent break, M06 org from seed, M07 labels dropped, M08 card regex without whitespace tolerance (the AJAX HTML is unminified), M09 board_total not set, M10 HaK entry removed. Each: file copied to /tmp, mutated, targeted tests RED, restored FROM THE /tmp COPY, __pycache__ deleted, GREEN, diff -q identical. ALL OK; vendor_adapters.py byte-identical to its pre-mutation copy afterwards.

## Proving crawl: worktree run 219 (ids live only in the worktree's app.sqlite), 20:25-20:27 UTC, scope 67705,66104,66105,RH2682,RH2772,RH2547,RH1970, registry correction applied in memory (/tmp/grpG/recrawl.py), retirement disabled
5 boards (Lohr fetched ONCE for [66104, 66105, 67705], was twice): Lohr 5 rows, HaK 5, MediClin 10, Kötzting 11, Bad Abbach 10. Drain: 41 rows -> 9 observations, 8 linked, 4 new postings, clinic links pushed 8; verify: 4 new -> live 4. Process notes: Lohr 10076 'loaded (no site match)'; HaK 2x '-> RH1970'; MediClin 2x '-> RH2547'; Bad Abbach 4x '-> 27306'; everything else nicht_pflege/ausbildung. No retirement candidates (every open posting of these clinics was on its board's walk).
Open postings per clinic_id, queried live (before 20:25:33 /tmp/grpG/before.json -> after 20:27:49 /tmp/grpG/after.json):
RH2547 0 -> 2 (15299 Pflegedienstleitung, 15300 Gesundheits- und Krankenpfleger / Altenpfleger; R3_tokens; verify live) | RH1970 0 -> 2 (15297, 15298 'Mitarbeiter*in für unseren Pflegedienst ...'; R3_tokens; verify live) | 67705 0 -> 0, 66104 1 -> 1 (10076 unchanged, needs the relink below) | 66105 0 -> 0 | RH2682 0 -> 0 | RH2772 0 -> 0 | 27306 4 -> 4.

## For Ivan's approval (NOT written)
- Registry: /tmp/grpG/task171_registry.json = {66104: careers_url -> https://karriere.bezirkskrankenhaus-lohr.de/} (ats_type already concludis live). Dry-run OK: 1 row, only careers_url changes. CSV row 66104 patched to the same (careers_url + ats_type, CRLF kept). Apply: set -a; source .env; set +a; .venv/bin/python tools/apply_clinic_corrections.py /tmp/grpG/task171_registry.json --push
- Relink 10076 66104 -> 67705 as clinic_match_rule 'manual' (future crawls keep it): /tmp/grpG/task171_relink.json; current row backed up in /tmp/grpG/task171_relink_before.json. Why 67705: Lohr a.Main is the first site the ad names and the page's own employer is 'Bezirkskrankenhaus Lohr' (<title> suffix, body 'Im Bezirkskrankenhaus Lohr ...'); 66104 came only from the seed copy. Alternative: leave it (the ad also names Aschaffenburg). Apply: .venv/bin/python -c "import json; from pflege_jobs.sinks import EdgeSink; print(EdgeSink()._post(json.load(open('/tmp/grpG/task171_relink.json'))))"
- Retire: none.

## Follow-ups found, not done (need Ivan's call)
- TASK-122 AC#3 (multi-site ad on the Lohr board: link one site or both) is exactly 10076; TASK-118's 'not a bug' premise read a seed-copied employer as page-stated.
- reha-badkoetzting.de: crawl_wp_jobs stores the WP archive /jobs/ (titled with the first card), 3 /job_category/ pages and the listing page itself as rows -- harmless today (all nicht_pflege), a phantom nursing row the day the first card is nursing (same class as TASK-170's GSB search form).
- Title-only boards (hausamkurpark.de, klinik-bad-trissl.de): no sitemap -> crawl_issue 'degraded' every night although the page IS the whole board; board_walk_ok then blocks board-absent retirement, and verify.py turns a vanished title into 'error', not 'gone' -- a removed posting stays open.

## Suite + hand-back
Offline suite in the worktree with the final code (env sourced, __pycache__ cleared first): 1576 passed, 18 skipped, 2131 deselected, 0 failed (main tree was 1570 before; +6 new tests). Log /tmp/grpG/suite.log.
Patch (my changes only, git diff --binary after git add -N of the 6 new files): /tmp/grpG/g.patch, 10 files, git -C /home/exedev/repo apply --check OK (main tree unchanged since the worktree base, md5 of its diff = /tmp/grpG/base.patch). CSV part alone: /tmp/grpG/g_registry_csv.patch (apply together with the DB push; the crawl reads the registry from the DB, not the CSV). Registry correction: /tmp/grpG/task171_registry.json. Relink proposal: /tmp/grpG/task171_relink.json (+ backup /tmp/grpG/task171_relink_before.json). Before/after snapshots: /tmp/grpG/before.json, /tmp/grpG/after.json. Proving-crawl driver: /tmp/grpG/recrawl.py, log /tmp/grpG/recrawl.log. Worktree: /home/exedev/repo/.claude/worktrees/grpG (branch grpG, nothing committed).
Left In Progress: code not in main yet; the 66104 registry push and the 10076 relink wait for Ivan.

Main-session note (2026-09-29 20:37 UTC): 'Worktree run 219' above is an id in the grpG worktree's copy of app.sqlite, not main's crawl_runs. Code part of /tmp/grpG/g.patch applied to main WITHOUT data/registry/clinics.csv; registry (task171_registry.json + g_registry_csv.patch) and relink (task171_relink.json) wait for Ivan.

Deployed + pushed 2026-09-29 (main session): code 20:37 UTC without the CSV part, pflege-web restart 20:43:44. Ivan approved 2026-09-29. 66104 careers_url -> https://karriere.bezirkskrankenhaus-lohr.de/ pushed 21:37:24Z (1 ledger line; backup backups/apply_clinic_corrections_task171_registry_before_20260929T213724Z.json); /tmp/grpG/g_registry_csv.patch applied to clinics.csv. Posting 10076 relinked 66104 -> 67705 as manual 21:37:32Z via tools/apply_posting_changes.py, ledgered.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Two of five zero sites were real defects fixed in code (MediClin Roter Hügel via crawl_mediclin, Haus am Kurpark via a title-only board entry: each 0->2 verified live); BKH Lohr was a registry defect (dead careers_url, posting on a 0-bed day clinic) fixed by a ledgered registry push and a manual relink of 10076 after Ivan's approval; Bad Kötzting and Asklepios Bad Abbach Reha are correctly at 0. Tests + 10 mutations, suite 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->
