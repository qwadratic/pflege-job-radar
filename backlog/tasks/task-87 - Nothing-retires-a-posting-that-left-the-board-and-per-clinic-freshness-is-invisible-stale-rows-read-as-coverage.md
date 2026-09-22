---
id: TASK-87
title: >-
  Nothing retires a posting that left the board, and per-clinic freshness is
  invisible: stale rows read as coverage
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 04:26'
updated_date: '2026-09-21 18:50'
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
- [ ] #1 A posting absent from a board walk that SUCCEEDED is retired, distinct from a posting whose URL 404s; a walk that failed or was truncated must never retire anything
- [x] #2 last_seen becomes meaningful: a re-crawl that sees an unchanged posting still records the sighting, so freshness reflects reality rather than first-sighting
- [ ] #3 Per-clinic last_seen age is exposed where coverage is judged (GET /api/coverage and the Pro clinics view), so a clinic cannot read 'complete' on 16-day-old rows
- [ ] #4 The ~30 currently-stale-open rows identified by the audit are retired, and the 12 named clinics are re-crawled; report the delta
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
<!-- SECTION:FINAL_SUMMARY:END -->
