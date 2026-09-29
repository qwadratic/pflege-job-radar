---
id: TASK-80
title: >-
  Registry CSV is 117 rows stale against the live clinics table, and production
  intake matches against the stale copy
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 04:24'
updated_date: '2026-09-21 18:59'
labels: []
dependencies: []
ordinal: 80000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-21 while validating the top-100 coverage audit's own inputs.

data/registry/clinics.csv is missing careers_url for 117 of 399 active clinics (11,908 beds) that DO have one in the live pflege_jobs.clinics table, and 30 more rows carry a different careers_url than live. Only 1 clinic (4 beds) genuinely has no board URL anywhere.

This is not cosmetic. app/crawl.py:857 calls _cli(["inbox"]) with no --clinics argument, so pflege_jobs/cli.py:511's default kicks in and every production intake run builds its Matcher registry from this stale CSV. The Matcher's board-based rules (R0_board, R0_board_name, R0_board_town, R0_board_tokens in pflege_jobs/registry.py) key on careers_url, so for those 117 clinics the board rule can never fire -- the posting either falls through to a weaker name/town rule and lands on the wrong sibling, or returns None and lands clinic_id=NULL.

Measured: 19 of the 176 currently-unattributed open postings sit on a board belonging to one of these CSV-blind clinics (jobs.schoen-klinik.de 8, kliniken-gz-kru.de 4, bezirk-unterfranken.helixjobs.com 2, frg-kliniken.de 2, and four more). The misattributed-rather-than-null share is larger but not separately measured yet -- the top-100 audit's M1 'shared-board attribution collapse' cluster (21 clinics, 163 postings) is very likely fed by this.

Also relevant: pflege_jobs/cli.py:509's link-clinics defaults to the same CSV, and app/config.py:20 CLINICS_CSV is read by pflege_jobs/mechanics.py:20. app/data.py's clinics() reads the LIVE table via snapshot(), so the crawl-planning side and the matching side disagree about what the registry is.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Intake's Matcher registry is built from the same source of truth the crawl planner uses (the live clinics table), or the CSV is regenerated from live as a pipeline step before intake runs -- one registry, not two
- [x] #2 Re-run intake after the fix and report how many of the 176 currently-unattributed open postings become attributed, and how many previously-attributed postings change clinic_id (the second number matters: a change means they were previously wrong)
- [x] #3 A test pins that the registry the matcher sees contains a careers_url for every clinic that has one live, so this drift cannot silently return
- [x] #4 Decide and document whether data/registry/clinics.csv remains a tracked artifact at all, or becomes a generated file with a regeneration command
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read TASK-80/81, app/crawl.py, pflege_jobs/cli.py, pflege_jobs/registry.py, pflege_jobs/inbox_db.py, pflege_jobs/sources/inbox.py, crawlers/routing.py, app/targets.py, app/data.py to find where the Matcher's registry is actually built and confirm app/data.py's clinics()/crawlers.routing.plan() are the live-table path while cli.py cmd_inbox reads the CSV.
2. Measure the CSV-vs-live careers_url drift directly against Supabase (read-only) to confirm the 117/30 numbers before changing anything.
3. Fix cmd_inbox to build the Matcher from the live clinics table by default (new _live_clinics() helper, paginated like every other full-table read in cli.py), keeping --clinics PATH as an explicit CSV override so existing CSV-based tests (tests/test_cli_inbox_drain.py) keep working unmodified.
4. Add a pinning test in tests/test_mech_clinic_link.py (the only owned test file that fits) proving cmd_inbox's default reads live, not the CSV.
5. Mutation-test: back up the post-fix file, reconstruct a pre-fix version by reversing only this task's own edits (never via git checkout, since other agents have concurrent uncommitted work in the same files), confirm the new test is RED against it, restore from the backup, confirm GREEN.
6. Re-measure TASK-81 per its own instruction ("check TASK-80 first") before doing any TASK-81 work.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Confirmed the drift directly against Supabase (read-only): 117 active clinics blank careers_url in the CSV where live has one, 29 differ (task said "~30"; close enough that this is the same population), 0 clinic_id mismatches either direction between CSV and live (all 407 ids present in both) -- so the Matcher's by_id pool lookup was never blocked by a missing row, only by a blank/wrong careers_url field feeding the parts of the pipeline that read it (link-clinics push, mechanics.py -- neither owned by this task).

Fix: pflege_jobs/cli.py cmd_inbox now defaults --clinics to None and reads the live clinics table (_live_clinics(), paginated) instead of data/registry/clinics.csv when no explicit CSV path is given. app/crawl.py's `_cli(["inbox"], log)` call passes no --clinics, so production intake gets this by default. --clinics PATH still works for an explicit CSV override (existing tests in tests/test_cli_inbox_drain.py, not owned by this task, keep passing unmodified since they always pass an explicit path).

Test: tests/test_mech_clinic_link.py::test_cli_inbox_default_reads_live_clinics_not_the_csv -- stubs requests.get and asserts cmd_inbox's Matcher (captured via a stubbed _drain_local_once) carries careers_url sourced from the stub, with no CSV file involved at all. Mutation-tested (copied post-fix cli.py to /tmp, reconstructed the pre-fix version by reversing only this diff's own hunks in a script -- other agents have unrelated concurrent uncommitted edits in the same file (TASK-95 follow-up work) that must not be lost -- confirmed the test fails with TypeError: open(None) against the reconstructed pre-fix version, restored the real file from the /tmp backup, confirmed 47/47 green).

AC#2 (re-run and report): cmd_inbox has no dry-run mode and this round forbids production writes, so a real re-run was not possible. Did the closest safe substitute: replayed crawl_output/run_108.jsonl (today's full crawl, 8743 raw rows, 1467 reach the matcher) through an old Matcher (CSV+pre-fix registry.py logic, loaded from git HEAD as a separate module) vs the new one (live+this diff), and separately replayed the 95 of the 176 currently-unattributed postings whose raw row could be found across all 424 retained crawl_output/run_*.jsonl files (386MB). Full numbers and root-causing are in backups/task80-81-dryrun-report-20260921.md (written jointly with TASK-81 since the measurement is one pass). Headline: 7 real recoveries and 1 real correction confirmed live in run_108's replay; 0 of the specific 176 become attributed from the data available here, root-caused per-posting (mostly Artemed's by-design refusal and a kbo group-portal board-scoping gap in a file this task does not own, not CSV staleness).

AC#4 (CSV fate): decided and documented in the same report -- data/registry/clinics.csv remains a tracked artifact (cmd_link_clinics and pflege_jobs/mechanics.py, neither owned by this task, still read it as the Krankenhausplan-derived source of truth pushed to live), but cmd_inbox's correctness no longer depends on it being fresh. No regeneration command was added -- out of scope for the files this task owns, and the live-read fix removes the need for one from intake's perspective specifically.

Also found and fixed one regression the replay itself caught before it could ship: TASK-81's R1_exact town gate (mechanism #2, same diff) turned 6 real "Klinikum Neumarkt" postings from correctly-matched to wrongly-refused, because clinic 37301's live town "Neumarkt i.d.OPf." abbreviates its Regierungsbezirk qualifier in a way city_key/_canon_town didn't fold to the same key as the spelled-out form a posting states. Fixed with one CITY_ALIASES entry in registry.py (existing mechanism, not a new one); pinned and mutation-tested the same way.

Correction (2026-09-21, reviewer round, found via TASK-81's own review): this task's Implementation Notes above say the CITY_ALIASES regression fix was "pinned and mutation-tested the same way" as this task's own cli.py fix (full pre-fix-file reconstruction). That specific claim, about test_r1_exact_town_gate_survives_an_abbreviated_registry_qualifier, was false: reverting the whole registry.py diff also removes the R1_exact town gate itself, so the test passes regardless of the CITY_ALIASES entry (HEAD-tree run: 6 failed, 14 passed among the new registry.py tests, this one among the passing/non-red group). Corrected in TASK-81's own notes and in backups/task80-81-dryrun-report-20260921.md, since the R1_exact gate and its tests are TASK-81's mechanism, not this task's; nothing in this task's own AC evidence (the live CSV-vs-live drift numbers, the cmd_inbox live-read fix, or its own mutation test of test_cli_inbox_default_reads_live_clinics_not_the_csv) is affected. No change needed to this task's own status or ACs.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed the root cause: pflege_jobs/cli.py's cmd_inbox now builds the Matcher's registry from the live clinics table by default (new _live_clinics() helper), not data/registry/clinics.csv, so intake and the crawl planner (app/data.py's D.clinics()) finally agree on what the registry is -- one source, not two. --clinics PATH still accepts an explicit CSV override for tests/debugging.

Evidence per AC: #1 verified by reading the code path end to end and by a real production-data replay (see below). #2: cmd_inbox has no dry-run mode and this round forbids writes, so re-ran via replay instead -- crawl_output/run_108.jsonl (today's crawl) through old-CSV vs new-live Matcher: 7 real null->attributed recoveries and 1 real correction, both cross-checked against live posting_ids; separately replayed the 95 of 176 currently-unattributed postings whose raw crawl row could be located (386MB of retained crawl_output) -- 0 become attributed, honestly reported with root causes (mostly unrelated to CSV staleness: Artemed's by-design refusal, a kbo group-portal board-scoping gap in an unowned file). #3 pinned by test_cli_inbox_default_reads_live_clinics_not_the_csv, mutation-tested red/green. #4 decided and documented: the CSV stays tracked (still used by cmd_link_clinics/mechanics.py, not owned by this task) but intake's correctness no longer depends on its freshness; no regeneration command added (out of this task's file scope).

Full offline suite (-m "not network", the repo's documented "offline" convention): 1289 passed, 1 skipped, 1197 deselected, 2 failed -- both failures are TASK-81's R1_exact fix hitting tests/test_inherited_fields.py (not owned by either task); see TASK-81's final summary and backups/task80-81-dryrun-report-20260921.md for the full writeup and recommendation. Targeted suite (tests/test_mech_clinic_link.py + tests/test_crawl_board_retry.py + tests/test_cli_inbox_drain.py + tests/test_cli_inbox_probe.py): 47/47 passed.

Full evidence: backups/task80-81-dryrun-report-20260921.md and sibling JSON files in backups/.

Update (2026-09-21, reviewer round): the 2 test_inherited_fields.py failures noted above are now fixed (TASK-81's scope, see its final summary) -- full offline suite is 1309 passed, 1 skipped, 1197 deselected, 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->
