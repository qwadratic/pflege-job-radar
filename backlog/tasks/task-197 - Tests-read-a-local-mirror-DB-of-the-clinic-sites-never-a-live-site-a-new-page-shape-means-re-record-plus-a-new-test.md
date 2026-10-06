---
id: TASK-197
title: >-
  Tests read a local mirror DB of the clinic sites, never a live site; a new
  page shape means re-record plus a new test
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-01 21:25'
updated_date: '2026-10-05 22:52'
labels:
  - harvester
  - adapter-testing
dependencies: []
priority: high
ordinal: 194000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-01: tests hit real clinic sites. They must read a local database of mirrors of the clinic sites, and every new case that appears on new pages is handled by re-mirroring and covering it with a new test. Today tests/test_adapter_completeness.py parametrises 2,124 network-marked cases over the live registry and fetches live boards (its module level also calls the registry proxy at collection, so even -m 'not network' touches the network); further network marks sit in test_completeness_beesite_hr4you/dvinci/helix/smartrecruiters, test_verify_pi_loga_live, test_geo, test_autopilot. crawl_snapshots/ (git-ignored, 1.6 GB, 336 hosts, 34,375 pages, 14k of them JS bundles) is a write-only side effect of tests.adapter_contract.save(): oracle pages, not the adapter's request sequence, so it cannot be replayed. tests/fixtures/board_samples are hand-cut redacted slices and stay. This replaces the live-board-as-oracle part of TASK-26's method (2026-09-10): the oracle stays what the board's own client reads and declares, read from a frozen recording that is refreshed on purpose. Related: TASK-24 (browsable mirror, link graph), TASK-26, TASK-130. The mirror holds third-party pages with HR names and contacts, so it is local and git-ignored (the repo is public).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A mirror store under data/mirror/ (git-ignored) holds, per board, every response (hop by hop incl. redirects, status, headers, body, fetched_at) that the production crawl path and the completeness oracle fetches make; tools/mirror.py has record, add, diff, status
- [x] #2 A replay layer serves requests, urllib and Playwright from the mirror; a request the mirror lacks raises MirrorMiss naming board, URL and the re-record command; nothing falls back to the live site
- [x] #3 A conftest guard makes any non-local socket or DNS use in any test fail with the host; running pytest with no arguments produces zero guard hits
- [ ] #4 tests/test_adapter_completeness.py (5 checks and the mutation meta-tests) runs over the mirror index with no network at collection or run; case count and run time reported before and after
- [x] #5 Every other network-marked test is mirror-backed or deleted with a reason; the network marker is gone; boards that cannot be replayed (if any) are named, listed, and visible as explicit xfails
- [x] #6 The rule is written down in CLAUDE.md: new page shape or board => tools/mirror.py record or add => red test on the mirror => fix => green; plus the proposed backlog Definition-of-Done line
- [x] #7 A worked example from the AMEOS or Sana Oracle fix shows a test red on the old adapter and green on the new one, against a freshly recorded mirror
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Scan: run the whole suite with a socket+DNS guard, list every test that reaches the network (marked and unmarked). 2. Mirror store (data/mirror/<board_id>.sqlite, index) and tools/mirror.py record/add/diff/status. 3. Replay layer tests/mirror.py (requests, urllib, Playwright, sleep) with MirrorMiss, no fallback. 4. conftest guard for every test. 5. Convert tests/test_adapter_completeness.py and the other network-marked files to the mirror. 6. First fill: record every registry board, report size, failures, gaps. 7. Rule in CLAUDE.md, adapter_contract docstring, fixtures README; propose DoD line. 8. Worked example: AMEOS or Sana Oracle red then green. Agent E in worktree builds 2-8, brief in the job dir (briefs/E_mirror.md).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-01 scan (no .env, socket+DNS guard plugin, code of worktree-integration 7b35091, pytest -m 'not network', 17 min 40 s): 13 failed, 1754 passed, 21 skipped, 3 deselected, 2 collection errors. 10 guard hits, none on a clinic site: 5 DNS lookups of the registry read proxy supabase.int.exe.xyz at collection (module-level code of network-test modules), 5 lookups of the Supabase project REST host from tests/test_clinic_photos.py (5 tests that pass but reach our own DB host: separate finding). tests/test_completeness_dvinci.py fetches a live board at import (URLError at collection under the guard). tests/test_adapter_completeness.py skipped itself at module level because the proxy was unreachable; with the network up it parametrises 2,124 live cases at collection. The 13 failures are the known baseline (PFLEGE_INGEST_URL x5 incl. cli_inbox_drain 2, FIRECRAWL_API_KEY x3, missing sqlite tables x2, two Playwright timing flakes, one assert None) and unrelated to the network. Scan tooling and result: job dir tmp/nonet (nonet_plugin.py, pt.py, SCAN_RESULT.md). Agent E (worktree) builds the mirror, replay layer, guard and conversion; brief tmp/briefs/E_mirror.md.

Agent E, 2026-10-01 (branch worktree-agent-aba79e1b41c5c3b98, commits bc0ae5d..a51b16a): built so far -- tests/mirror.py (store: one sqlite per board, xz as a whole because pages of one board are near-copies: AMEOS 860 pages 14.2 MB zlib-per-blob vs 0.36 MB solid xz; replay of requests via HTTPAdapter.send, urllib via AbstractHTTPHandler.do_open, Playwright via a context route, time.sleep; MirrorMiss names board, URL and the re-record command and also fails the run when an adapter swallows it), tools/mirror.py (record/add/diff/status/list-urls/show/sql/reindex/record-infra; every recording is replayed from memory and compared with the live run before it is saved), tests/adapter_harness.py (the five checks and the mutation machinery, shared by the tests and the recorder), conftest.py network guard (found tests/test_clinic_photos.py: 5 route tests read the real Supabase REST host, now stubbed), test_adapter_completeness and the 6 other network-marked files read the mirror, network marker removed. Mirror root: MIRROR_ROOT or <main checkout>/data/mirror, shared by every worktree (git-ignored). Measured: Playwright replay of the Klinikum Ingolstadt P&I board is identical once analytics beacons (Matomo POST with a random id and clock in the URL) are answered 204 and not stored; first fill in progress.

2026-10-05 resume (agent in worktree, branch wip/task-197-mirror, tip fe302d1; Ivan approved resuming). Done, in this order:

MERGE. origin/wip/task-197-mirror (20 commits on 111e8f7) merged into main 83ca0f4 (then main 33e4302, PR #6). One conflict, tests/test_completeness_dvinci.py: main's only change there since 111e8f7 was the CI commit's PFLEGE_TESTS_OFFLINE skip (`_SKIP_REASON`), which the mirror conversion replaces; took the branch side (checked with git diff 111e8f7 origin/main -- that file). .github/ untouched. Not merged to main.

FIRST FILL. Already complete: the registry has 402 boards, all 402 had a recording (403 in the index; wp_jobs__krankenpflegejobs24.de has left the registry). Registry data of every board (clinic ids, names, towns, urls, vendor) is identical to what was recorded. Nothing was left to record. The mirror was recorded on 2026-10-01/02 with the adapters of base 111e8f7, i.e. before TASK-184/185 (P&I popup, Oracle CE REST, BITE place_field, place per posting, F8 LINK_BAD).

RE-RECORDED today (16 boards): the new adapters made requests the mirror lacked, each showed as a MirrorMiss naming board, URL and command, then `tools/mirror.py record`: oracle__jobs.sana.de, oracle__jobs.sana.de-1, oracle__sana.de (old adapter: 0 rows from the SPA shell; new: 1,088 rows = the site's own TotalJobsCount, 8 min each); umantis__klinikverbund-allgaeu.de (a page F8 now follows: .../medizinische-technologin-fuer-radiologie, new gap datePosted 0/93); softgarden__karriere.uk-augsburg.de (UKA job 67659167, was dropped by LINK_BAD); 11 pi_asp boards (3 Helios, BRK, wirkzvin, 3 Sana/Regiomed, 2 wz-kliniken, ameos.eu). Recording order one board at a time with the adapters' own sleeps.

THREE DEFECTS OF THE REPLAY/RECORD LAYER found while re-recording P&I (all fixed with a red test first, mutation-checked, tests/mirror.py):
1. The recorder's route broke the P&I popup flow: Playwright hands a route the request URL without #fragment and re-issues a redirected navigation without it, so window.open('...?company=*#position,id=<uuid>,popup=y') (302 on the way) landed on #positions and the form never rendered: 77 of 77 Regiomed clicks 'did not open', stats['error'] set, rows 0, five vacuously green checks. Plain Chromium: opened, 2,375-char ad. Fix: the page tells the route the fragment before window.open (synchronous XHR to a path the route answers itself), the route carries it over the hop. Test: test_a_popup_opened_on_a_redirecting_url_keeps_the_fragment...
2. A URL that redirects to itself (wirkzvin redactorfilesloader?id=..., 302 to its own URL) made the route follow hops with no end; the recorder's replay check spun at 100% CPU for 40 minutes. The route now ends a chain at 20 hops like Chromium (ERR_TOO_MANY_REDIRECTS), 21 rows recorded.
3. A miss inside a Playwright walker was only raised when the walker ended (pi_asp waits 15 s per row: 20 minutes a board; raising in the route handler deadlocks the sync driver, measured). A miss now closes the browser context, every later wait fails at once, the run ends with the MirrorMiss.
Also: Tag Manager's logging beacon (googletagmanager.com/a?v=3&l=...) is answered 204 like the other analytics beacons (it broke the replay of a Helios mutation run); the recorder now names the error the adapter itself ended with (index field adapter_error, ADAPTER-ERROR in the recorder line and in `status`; run_adapter(stats=...)); the AMEOS worked example lost its xfail marker (XPASS over the merged adapter).

WORKED EXAMPLE (AC 7), Sana/Oracle CE fix 9bf74cd: new tests/test_completeness_oracle_mirror.py (adapter rows == the TotalJobsCount of the first REST list answer in the recording; no posting carries nothing or the SPA shell title 'Sana'). GREEN on the mirror freshly recorded today with the fixed crawl_oracle (1,088 rows). RED ('the adapter returned nothing for the board') on a mirror freshly recorded today by the adapter of this tree with 9bf74cd reverted (throwaway copy under /tmp, MIRROR_ROOT=/tmp/oracle_old_mirror, 23 pages, 0 rows; the five shared checks stay 4/5 there, they cannot see it). Mutation: dropping one row -> red (declares 1088, returned 1087); every description set to 'Sana' -> red.

SUITE (placeholder env as in .github/workflows/tests.yml, no .env, no arguments, this tree): 3876 passed, 51 skipped, 187 xfailed, 0 failed, 0 errors, `network guard hits: 0`, 2,474 s (41 min). Cases: 4,114 (4,108 before the 6 tests added today). Skips: 19 outside the completeness files (autopilot router unmounted x17, no career_profiles table, beesite no-first-page), 32 in completeness = mutation meta-tests whose adapter shape has nothing to break (pytest.skip with that reason). 187 xfails, all named: field_completeness 121, round_trip 25, read_path_coverage 27, declared_total_parity 10 (each = a check already red when its board was recorded, 157 of 403 boards, `tools/mirror.py status` lists them, `pytest --runxfail` shows the finding), BRK placeholder companyEid 2, mutation gaps 2 (crawl_drv_bund and crawl_oracle cap_first_page: the adapter fails the board loudly instead of returning fewer rows). No board is a gap for 'cannot be replayed': replay_identical is true for all 403.

AC 4 NUMBERS. tests/test_adapter_completeness.py: before 2,082 cases (collected against the live registry, 1.9 s, network at collection), after 2,087 (403 boards x 5 checks + 72 mutation cases; +5 = the board that has left the registry but stays in the mirror). Run time after: whole file about 34 min (non-P&I/Oracle slice 532 s, Oracle + P&I slice 1,504 s; P&I replays drive Chromium through popups, 90-210 s per board setup); whole suite 41 min. Run time before: never run end to end (live); the recorder's own seconds for one live pass over the 403 boards add up to 28,770 s (8.0 h, adapters' sleeps included). That 'before' time is an estimate, so AC 4 stays unchecked.

FINDINGS (not fixed here, adapter or data decisions): (a) the fixed Oracle adapter reads no employmentType (0/1088) and the harness's client reads a path 'https://jobs.sana.de/' the adapter never calls (two named gaps on the three Sana boards); (b) 62 of 403 boards return 0 rows, so their five checks are vacuous (list in `status`, rows 0); (c) the three Sana/Regiomed registry boards are one site recorded three times (3 x 90 MB); the ad images inside position pages (redactorfilesloader) are 115 of 138 MB; (d) job 4378 on jobs.sana.de has the placeholder ad 'folgt von PDL'; (e) pi_asp wirkzvin lists 61 now, 62 on 10-01.

WITHOUT data/mirror (a GitHub runner; MIRROR_ROOT=empty dir): 98 failed + 72 errors of 2,017 (1,826 pass). 131 are web tests (test_web_*): the Chromium of the web tests loads Google Fonts, answered from the mirror board infra__web-fonts (0.05 MB), absent -> `network guard: <test> reached 1 non-local host(s): font (not in the mirror; MIRROR_RECORD=1 pytest <this web test> records it)`; 133 guard hits. The rest are loud MirrorMiss: 'no mirror index: <dir>/INDEX.json does not exist. record the boards: ...', 'no mirror for board <id>: <file> does not exist', 'the mirror has no board with <part> in its url: ... record <part>'. Today's CI runs `pytest -m "not network and not llm"`: with the network marker gone that is everything, so a runner without the mirror is red until the mirror is fetched. Only infra__web-fonts and infra__registry-read-proxy (0.07 MB together) are needed by the non-completeness tests.

UPLOAD (for the CDN decision): data/mirror = 405 files *.sqlite.xz (403 boards + 2 infra) = 491.9 MB + INDEX.json 0.64 MB; largest 90.0 MB (each of 3 Sana P&I boards), wirkzvin 44.9, BRK 14.8, then 13.7, 10.2, 6.4 ... 29 *.prev files (44 MB) are not for upload. The replay layer reads exactly two things: INDEX.json (at collection, to parametrise) and <board_id>.sqlite.xz, each whole (lzma then sqlite deserialize in memory; no range reads), so file-by-file by URL works. It cannot read them inside a test (the guard refuses the socket), so a fetch step before pytest (INDEX.json + the boards a run needs, or all) into data/mirror is the shape; the cache key today is mtime+size, a remote copy would use ETag/Content-Length. The files hold third-party HR names and contacts: a private zone (token), not a public pull zone.

DoD LINE (AC 6, proposed, not applied): `backlog config set definitionOfDone` does not exist (config set takes no such key; the field is definition_of_done in backlog/config.yml). Proposal: definition_of_done: ["A new page shape or board is re-recorded (tools/mirror.py record|add) and covered by a test that is red on the mirror before the fix"]. CLAUDE.md carries the rule ("Tests never touch a live site", plus today's two lines: re-recording drops pages a test recorded under its own scope, run MIRROR_RECORD=1 pytest <that file> again; ADAPTER-ERROR is a finding).

DISK: free 5.9 GB before (df, 75% used), 5.2 GB after; mirror dir 202 MB -> 540 MB with the .prev files.
<!-- SECTION:NOTES:END -->
