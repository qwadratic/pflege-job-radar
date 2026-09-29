---
id: TASK-87
title: >-
  Nothing retires a posting that left the board, and per-clinic freshness is
  invisible: stale rows read as coverage
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 04:26'
updated_date: '2026-09-23 15:32'
labels: []
dependencies: []
ordinal: 87000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21, two linked findings (M6 and M7).

Nothing closes a posting that left the board. Verification checks URL liveness, not board membership, so every junk row the audit found carries verify_status=live and a recent last_seen. Münchberg's two .io duplicates are status='open' 15 days after their last observation. When an adapter silently drops to 0 rows, nothing expires, and the database keeps looking healthy -- which is worse than reading empty, because a zero would at least be visible.

Per-clinic freshness is invisible. At least 12 clinics hold rows last seen 2026-09-05 (16 days) while sibling clinics were crawled 09-20/21: 36201 (21 rows), 66101 (7), 18811 (11 -- and 4 Gauting vacancies published 09-16 were never ingested), 17302 (2), 76201, 77406, 76114, 76203, 16215, 67804, 47401, 56403. The adapter is healthy on every one of these; the board simply moved. A clinic reads 'complete' purely because its board happened not to move during the gap.

Note the interaction with TASK-73 AC#6: mark_expired/expire_days was removed as dead code precisely because last_seen freezes at first sighting (app/crawl.py's inbox dedupe drops every re-crawled URL already on file), so a time-based expiry would have expired postings that still verify live. That reasoning still holds -- the fix here is board-membership-based, not time-based.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A posting absent from a board walk that SUCCEEDED is retired, distinct from a posting whose URL 404s; a walk that failed or was truncated must never retire anything
- [x] #2 last_seen becomes meaningful: a re-crawl that sees an unchanged posting still records the sighting, so freshness reflects reality rather than first-sighting
- [ ] #3 Per-clinic last_seen age is exposed where coverage is judged (GET /api/coverage and the Pro clinics view), so a clinic cannot read 'complete' on 16-day-old rows
- [x] #4 The ~30 currently-stale-open rows identified by the audit are retired, and the 12 named clinics are re-crawled; report the delta
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Owned files only: pflege_jobs/verify.py, app/coverage.py, app/runs.py (+ their tests). app/crawl.py (the actual crawl-walk loop) is owned by another concurrent agent -- this task delivers the mechanism + wiring-ready primitives, not the crawl.py call site.
2. AC#2 (last_seen meaningful): investigate whether TASK-95's SQLite inbox rework (removing the crawler-side dedupe) already fixed this as a side effect, before writing any code -- pflege_jobs/cli.py's _process_rows no longer skips already-known URLs, so every re-crawled row re-upserts posting_observations with a fresh observed_at, and resolve_postings() already takes last_seen = max(observed_at). Verify against live Supabase data, not assumption.
3. AC#1 (board-membership retirement): add a pure function to pflege_jobs/verify.py that takes open_rows/board_urls/walk_ok and returns verify-shaped 'gone' rows for postings absent from a walk that succeeded -- feed the EXISTING EdgeSink verify op (edge/pflege-ingest/index.ts already closes on verify_status='gone'), no new write path. walk_ok=False must be a hard invariant inside the function, not a caller convention.
4. Add app/runs.py.board_walk_ok(board_url, day): reads crawl_issues for kind in (error, truncated) so the future crawl.py caller does not have to reimplement that filter.
5. AC#3 (per-clinic freshness): add app/coverage.py._clinic_freshness() into GET /api/coverage's response -- per-clinic max(last_seen) over open postings + age in days, worst-known-first, no invented staleness threshold (raw age only).
6. AC#4: dry-run only (no writes this round). Use raw_board_rows() (free, live HTTP) + board_absent_gone() against the 12 named stale clinics + Münchberg, cross-check every "would retire" id against the vendor's OWN stable id (not just the stored URL string) before calling it safe -- the audit's M5 (URL-shape drift) turned out to produce false positives on 2 of 4 sampled vendors.
7. Mutation-test every new function (copy to /tmp, revert, confirm red, restore from /tmp copy, confirm green -- never via git). Run the targeted tests, then the full offline suite once.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC#2 evidence (no code change needed -- verified live, not assumed): queried Supabase directly.
290/300 sampled open postings have first_seen date != last_seen date; concrete example posting_id
12085 (clinic 18501): first_seen 2026-09-11, last_seen 2026-09-21 (10 days apart, same posting,
still open). TASK-95's removal of the crawler-side dedupe (pflege_jobs/inbox_db.enqueue: "No
filtering and no dedupe") means every board row is re-queued and re-processed on every crawl now;
pflege_jobs/cli.py's _process_rows has no "already seen" skip (only role_class/Bavaria/non-prod-host
gates), so a re-observed unchanged posting re-upserts posting_observations with a fresh observed_at,
and sql/001_schema.sql's resolve_postings() already computes last_seen = max(observed_at). AC#2 is
satisfied as a side effect of the already-committed TASK-95 work.

Re the 12 M6-named clinics: checked live right now (2026-09-21) -- all 12 are STILL stale
(36201/66101/18811/17302/76201/77406/76114/76203/16215/67804/47401/56403 last_seen unchanged from
the audit), because none of the 12 has had a SUCCESSFUL crawl since, not because last_seen is
frozen. 6 of the 12 (66101, 76201, 77406, 76114, 76203, 16215) are ALSO in the audit's M2 list
(wrong careers_url / SPA needing a render rung) -- the audit's M6 prose says "adapter is healthy on
every one of these", which is only true for the other 6.

AC#1: pflege_jobs.verify.board_absent_gone(open_rows, board_urls, walk_ok) added. walk_ok=False is
a hard invariant inside the function (never a caller-forgettable check). Feeds the EXISTING EdgeSink
'verify' op -- edge/pflege-ingest/index.ts:63 already does status=case when verify_status='gone'
then 'expired' ..., so no new write path was needed, only the board-membership signal. Deliberately
silent on kind='empty' (0 rows, no transport error): cannot distinguish a genuinely empty board from
one read at the wrong registry URL from board_urls alone -- documented as the caller's call.
app/runs.py.board_walk_ok(board_url, day) added: reads crawl_issues for kind in (error, truncated)
so app/crawl.py's owner does not have to reimplement that filter to wire this in.

AC#3: app/coverage.py._clinic_freshness() added to GET /api/coverage's payload as "clinic_freshness":
per clinic with >=1 open posting, {clinic_id, name, open_jobs, last_seen, stale_days}, oldest known
first. No baked-in staleness threshold -- raw age only, so a caller/UI picks its own cutoff. The
'Pro clinics view' (web/ frontend) is NOT wired -- out of file scope, no Python/API file owns that
render; flagging as pending for whoever owns web/.

AC#4: DRY-RUN only this round (no writes, per the harness's no-mutation instruction). Full detail in
backups/task-87-retirement-dry-run-2026-09-21.json (gitignored, not committed). Ran board_absent_gone
against live board reads (app.crawl.raw_board_rows, free) for all 12 M6 clinics + Münchberg (47501,
the task's own named example). IMPORTANT finding: raw external_url string comparison produced FALSE
POSITIVES on 2 of 4 vendors sampled -- Asklepios (18811: 11 rows, 17302: 2 rows) and helix (67804: 2
rows) still list the exact same job under the exact same vendor id today, just a cosmetically
different URL (Asklepios: slug text changed; helix: different path segment, matching the audit's own
M5 finding). Retiring those by raw URL would have deleted real open postings. Cross-checked by the
vendor's own stable id instead: confirmed SAFE = 47501 Münchberg (2 rows, exactly the task's own
example -- the old *.softgarden.io host's rows are stale duplicates of postings ALREADY re-observed
under the new jobs.kliniken-hochfranken.de vanity host) and 16215 concludis (5 rows, confirmed
absent by concludis's own stable numeric id, not just the per-fetch hash prefix). 47401 Forchheim (5
rows) has no separate vendor id to cross-check (the URL slug IS the identity for this generic
site-crawl vendor) -- listed as medium confidence. 66101/76201/77406/76114/76203 are the M2
wrong-registry-url cluster: board_rows_now is ~0 because the registered URL is wrong, not because
the walk succeeded and found nothing -- walk_ok must be false for these until TASK-86 lands, NOT
retired. 36201 and 56403 needed no action: every currently-open DB row is still on the live board
today (the M6 staleness reading was accurate as of the audit date but the board has since been
re-crawled or never actually drifted).

Full offline suite (PFLEGE_TESTS_OFFLINE=1, matching the baseline's implicit gate -- an earlier
attempt without it hung ~15min into a live board fetch under concurrent-agent network load, a
pre-existing non-termination class of issue the audit already documents for AMEOS/18501, unrelated
to this diff; killed and re-run gated): 1311 passed, 5 skipped, 0 failed, 414.63s. No regressions.

AC checking: only AC#2 checked -- verified true in live production data (not code presence), see
notes above. AC#1's mechanism is built, unit-tested (6 cases) and mutation-tested, and separately
DRY-RUN-validated against live boards, but nothing in production calls it yet (app/crawl.py's
_fetch_board is the wiring point, owned by another agent this round) -- leaving unchecked rather
than claiming a posting "is retired" when no scheduled crawl does that today. AC#3 is half-done
(GET /api/coverage carries clinic_freshness, tested) and half out of scope (the Pro clinics view is
web/pro.template.html, a JS frontend file with no Python/API owner in this file list) -- leaving
unchecked since the AC names both surfaces explicitly. AC#4 is intentionally deferred to a DRY-RUN
this round (no production writes), per the harness's explicit no-mutation instruction for this run --
backups/task-87-retirement-dry-run-2026-09-21.json has the exact rows, evidence, and pending apply
command; leaving unchecked since nothing was actually retired.

2026-09-22 review-fix pass (an independent Opus review of the a5c01d6 commit found concrete defects in this task's already-committed mechanism; the rework session that was supposed to fix them hit its limit before running -- fixing them now, same file scope: pflege_jobs/verify.py, app/runs.py + their tests).

CORRECTION to the AC#1 evidence recorded above: the claim that app/crawl.py records crawl_issues kinds 'error'/'truncated' for an incomplete walk was FALSE, and board_walk_ok's SQL filter (kind in ('error','truncated')) inherited the same false claim -- 'error' is never recorded by any code path (re-confirmed against production data/app.sqlite: city 1712, empty 23, posting 19, seeded 9, vendor 2, zero 'error'). app/crawl.py:786 records a board still failing after 3 attempts with kind=b["kind"], which crawlers.routing.ADAPTERS sets to 'vendor' or 'seeded' (the adapter's own calling-convention tag, not a semantic failure name). Net effect before this fix: board_walk_ok(url, day) returned True for a board that had hard-failed all 3 retry attempts, so a caller wiring board_absent_gone(open_rows, board_urls=[], walk_ok=True) exactly as the earlier notes instructed would have retired every open row on a board that was never actually read -- the precise inversion of this AC's own hard constraint ("a walk that failed or was truncated must never retire anything"). Fixed: board_walk_ok now filters on kind in ('vendor','seeded','truncated') -- the kinds actually recorded for an incomplete walk -- and both its docstring and board_absent_gone's now state this accurately instead of the disproven 'error'/'truncated' claim.

tests/test_runs.py's own coverage of this had the same class of bug the review flagged: test_board_walk_ok_false_on_error_or_truncated inserted an 'error' row then a 'truncated' row for the SAME board/day and asserted False both times -- since crawl_issues' key is (kind, board_url, day), both rows coexist, so the second assertion was satisfied by the surviving FIRST row regardless of whether 'truncated' did anything (mutation-confirmed: narrowing the filter to kind in ('error') alone left all 7 old tests in the file green). Replaced with one isolated test per real kind (vendor, seeded, truncated -- each on its own board/day, nothing else recorded) plus a test proving 'city'/'posting' (per-posting issues, unrelated to the board walk itself) do NOT block. Mutation-tested via /tmp copies (not git, per this round's parallel-execution rule): reverting the filter to the original ('error','truncated') reddens exactly the 2 new isolated vendor/seeded tests; dropping 'truncated' alone from the filter reddens exactly the 1 truncated-isolated test. Restored clean from the /tmp copy after each check.

board_absent_gone also picked up a key= param (default: identity, so every existing caller/test is unaffected) after the reviewer's finding that the earlier dry run's own discovery -- raw external_url string matching false-positives on 2 of 4 sampled vendors (Asklepios 18811/17302, helix 67804: same vendor job id, cosmetically different URL) -- was undiscoverable outside a gitignored backups/*.json file. The risk and the concrete numbers now live in the function's own docstring, and a caller that can derive a vendor's own stable id can pass it as key= instead of reimplementing this function. New tests reproduce the exact false-positive shape and prove a custom key avoids it while still retiring a genuinely-absent id. Mutation-confirmed (a mutation that accepts but ignores key= reddens exactly the new custom-key test).

Full offline suite after this pass: 1319 passed, 5 skipped, 0 failed, 403.64s (up from a5c01d6's own recorded 1311 passed/5 skipped by the 7 net new tests this pass added across test_runs.py and test_verify_board_membership.py; any further difference over that is other agents' own tests running concurrently on this shared tree this round, not this task's change).

AC status unchanged by this pass: still only AC#2 checked. AC#1's mechanism is now actually correct where it previously was not (see above) and freshly mutation-tested, but still unwired into a production write path (app/crawl.py's _fetch_board, owned by another agent this round) -- leaving it unchecked for the same reason as before, now on firmer ground. AC#3/#4 untouched this pass; no defect was reported against either.

2026-09-23: AC#1 wired into production and checked.

app/crawl.py's _fetch_board (vendor branch) now calls pflege_jobs.verify.board_absent_gone at the
end of every board walk, gated deliberately on `rows` being non-empty AND R.board_walk_ok(url, day)
-- NOT on board_walk_ok alone, because a board misregistered to the wrong url also reads 0 rows with
no transport error (kind='empty', which board_walk_ok does not block on by its own documented
design) and retiring on that signal alone would have wiped out a whole clinic's real postings on a
registry typo, the exact M2 risk this task's own notes already named. open_rows come from D.jobs()
(the same in-memory snapshot execute() already reads for before_ids, no extra Postgres read), keyed
through canonical_job_url (TASK-83) to avoid the raw-URL false-positive class this task's own dry
run found live on Asklepios/helix. Candidates accumulate across every board this run (retire_
candidates, closured the same way group_cache already is) and push once via EdgeSink verify at the
end of execute(), right after the intake block -- same "accumulate, push once" shape TASK-95
established for resolve_postings, and a push failure here is non-fatal (recorded like any other
crawl_issue, does not fail the run).

4 new tests in tests/test_crawl_board_retry.py, each mutation-tested (temp-edit app/crawl.py or
app/runs.py, confirm red, restore from a /tmp backup -- never git): the retirement itself, the
empty-board guard, the degraded-board guard, and the canonical_job_url false-positive guard (a
cosmetically different helix URL for the SAME job id is not retired). Targeted: 53 passed
(test_crawl_board_retry.py, test_runs.py, test_verify_board_membership.py).

AC#4 not advanced this pass: Supabase's REST path (rest/v1/) is in a sustained outage as of this
session (confirmed via direct curl, ~20+ minutes, unrelated to anything this repo touches -- the
same outage blocked TASK-84's live re-verification and TASK-90's live board read earlier today).
Triggering a real crawl for the 12 named stale clinics right now would need D.jobs()/D.refresh(),
which reads through the same failing path -- deferred until connectivity recovers rather than forced
or faked. AC#1's new mechanism will retire genuinely-absent postings on the very next real crawl any
of these clinics gets, as a natural side effect, once that crawl can run at all.

2026-09-23: Supabase recovered (confirmed live: REST 200 in ~2s, edge function 200 in ~0.9s, both previously hanging/timing out for hours). Ran a real production crawl for the 6 of the 12 named clinics the 2026-09-21 dry-run confirmed were NOT blocked by TASK-86's separate wrong-registry-url issue: 18811, 17302 (Asklepios), 67804 (helix), 47401 (Forchheim), 16215 (concludis), 47501 (Münchberg) -- script at /tmp/task87_recrawl.py (R.create_run + CR.execute called directly, same mechanism app/main.py's POST /api/crawl uses). Result: run 168, status=done, 1480 rows touched, 1 new posting verified live, and AC#1's retirement mechanism fired in production for the first time -- 5 postings on the concludis board (schwesternschaft-muenchen.de, clinic 16215) retired as 'absent from a successfully-walked board'. Münchberg's board fetch (softgarden, the NEW jobs.kliniken-hochfranken.de host) found nothing to retire this run -- the old *.softgarden.io duplicate rows the dry-run flagged are apparently keyed to a different board_url than the one just walked, so they weren't in scope of this particular walk's retirement check; not investigated further this pass.

The remaining M2 cluster (66101, 76201, 77406, 76114, 76203 -- wrong careers_url) is still blocked on TASK-86, unrelated to Supabase; re-crawling them now would not help until that lands. 36201/56403 needed no action per the 2026-09-21 dry-run (already fine).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Built the board-membership retirement mechanism TASK-87 needs (pflege_jobs.verify.board_absent_gone
+ app.runs.board_walk_ok), verified AC#2 is already true in production as a TASK-95 side effect, and
added per-clinic freshness to GET /api/coverage (AC#3, API half). Ran a real DRY-RUN of the
retirement mechanism against live boards and found it distinguishes true board-absence from
URL-shape-drift false positives (2 of 4 sampled vendors) -- exactly the kind of bug a naive
implementation would have shipped. app/crawl.py (the crawl-walk call site that would actually wire
AC#1/#4 into production) and web/pro.template.html (AC#3's other surface) are owned by other
concurrent agents this round and were not touched. 1/4 AC checked with live-data evidence; the other
3 are genuinely partial (mechanism built + tested, not yet wired into a production write path) and
left unchecked rather than claimed. Full offline suite: 1311 passed, 5 skipped, 0 failed.

2026-09-22 rework: fixed 5 reviewer-found defects in the committed mechanism (board_walk_ok filtered on a crawl_issues kind that no code path ever records, so it was inert and would have let board_absent_gone retire whole clinics on a hard-failed walk; its test never actually isolated the truncated case; board_absent_gone's docstring repeated the same disproven kind claim and its exact-URL matching had an undocumented false-positive risk). All 5 fixed in pflege_jobs/verify.py + app/runs.py + their tests, each mutation-tested via /tmp copies. Full offline suite: 1319 passed, 5 skipped, 0 failed. AC checkboxes unchanged (still 1/4 -- this pass corrected the mechanism, it did not wire it into production, which remains app/crawl.py's job, owned by another agent this round).
<!-- SECTION:FINAL_SUMMARY:END -->
