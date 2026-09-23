---
id: TASK-70
title: >-
  Firecrawl hunter: sibling-skip coverage loss, invented halts, ledger
  undercounts
status: Done
assignee:
  - '@claude'
created_date: '2026-09-18 10:09'
updated_date: '2026-09-18 13:49'
labels: []
dependencies: []
priority: medium
type: bug
ordinal: 70000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Crawler-review 2026-09-18, app/hunter.py + app/crawl.py Firecrawl paths. (1) hunter.py next_target() (:267) marks every other firecrawl-mode clinic sharing a careers host as terminally skipped once one of them returns rows, but the agent prompt harvests only the single submitted hospital, and those siblings are already excluded from the adapter pass as walled -- so they get zero coverage from any path. Live blast radius today: HELIOS Klinik Erlenbach a. Main (67601, 267 beds) and HELIOS St. Elisabeth-Krankenhaus Bad Kissingen (67201, 175 beds) skipped as siblings of Kronach; 67601 has exactly one crawl ever (run 34, 2 postings, disjoint from Kronach 7), 67201 has never had one. (2) precheck() (:68) marks a whole clinic skipped for the day on any "keine (offenen) Stellen" phrase match, including the department-scoped box the NO_JOBS regex explicitly permits via "(in diesem bereich )?" -- so a board that groups vacancies per department is skipped even with live nursing vacancies. Measured: Kreiskrankenhaus Grafenau (frg-kliniken.de board) skipped on 2026-09-08 while the board carried 12 postings. (3) suspicious() zero_streak rule (:111) halts the hunter for the rest of the UTC day after 3 consecutive billable 0-row runs, but a billable 0-row run is the normal healthy-board result -- happened 3x on 2026-09-08 -- so the day remaining firecrawl clinics (up to ~19 in the worst case) never get crawled. (4) Hunter.run_once() submit loop (:399) never re-reads is_enabled()/the day persisted stop_reason -- only HUNTER_STOP/CR.kill_switch() -- so the /pro Stop button does not stop an in-flight pass, and check_stop() overwrites the operator stop_reason. (5) execute() generic except for a Firecrawl agent run (app/crawl.py:747, and refetch_career :815) charges nothing to the local ledger on a failure, permanently under-counting usage_total(24h)/_budget_left() -- confirmed 3 historical runs (3/4/5, 2026-09-06) left no ledger row at all. (6) The Firecrawl branch of execute() (:739) discards res["data"], so an agent answer reporting blocked_reason or an admittedly-partial read is stored as a plain successful run with no crawl_issue -- confirmed on Helios Frankenwaldklinik Kronach dropping 7->4 postings between runs with the agent explicitly saying its job-search endpoint returned no parsable list. (7) _unseen_source_urls (app/crawl.py:123) still packs 200 URLs into one PostgREST in.() filter (past the ~25KB gateway limit _post_inbox was already chunked to 50 for) and swallows the resulting 400 with two bare except:pass, so every posting on an 11-board set larger than ~170 rows is reported "unseen" and the spend_gate "adapter already covers it" refusal never fires -- 4908 of 9142 rows in the 2026-09-17 run are on such boards. See /tmp/crawler_review_2026-09-18.md "app/hunter.py" and "app/crawl.py" (:123,:739,:747) sections for full evidence.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 next_target() only skips a sibling clinic when the harvested run rows actually mention that sibling town/site name, not merely because it shares a careers host; Erlenbach and Bad Kissingen get submitted (or are confirmed genuinely covered) on the next hunter pass
- [x] #2 precheck() drops the "(in diesem bereich )?" alternative from its no-jobs match (or only skips when no job links are found at all), so a department-scoped no-vacancy box no longer skips a board that has other live vacancies; Kreiskrankenhaus Grafenau is no longer falsely skipped
- [x] #3 The zero_streak halt only counts a 0-row run toward the streak when it also carries a blocked_reason/error, so three healthy empty boards in a row no longer halt the rest of the days hunter pool
- [ ] #4 Hunter.run_once() re-checks is_enabled() and the days stop_reason inside the submit loop so POST /api/hunter/stop takes effect mid-pass, and check_stop() does not overwrite an already-set stop_reason
- [x] #5 The generic except branches in execute() and refetch_career() charge the ledger (at least the run cap, or the measured delta if available) before returning, so a failed agent run is not invisible to the 24h kill switch and weekly budget
- [x] #6 A Firecrawl agent run whose result carries a non-empty blocked_reason, or that reports 0 jobs with no explicit success signal, calls R.record_crawl_issue with the full notes/blocked_reason text
- [x] #7 _unseen_source_urls chunks its PostgREST lookup at 50 URLs like _post_inbox, and a failed lookup batch refuses the Firecrawl spend instead of silently reporting everything as unseen
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. app/hunter.py precheck() NO_JOBS regex: dropped the '(in diesem bereich )?' alternative -- a department-scoped 'no openings in THIS area' box no longer skips the whole clinic; only a board-wide 'no openings' phrase does.
2. app/hunter.py next_target(): sibling-skip now requires evidence, not just a shared host. Added _run_mentions_town(run_id, town) (reads crawl_output/run_<id>.jsonl, the same file app/crawl.py._write_jsonl produces, checking title/city text for the sibling's own town) and only marks a sibling 'skipped' when at least one harvested run for that host actually mentions it; otherwise it is submitted, logged as 'harvested today but did not cover this site'.
3. app/hunter.py suspicious(): dropped the self-invented '>60 rows from one clinic' ceiling (never fired in production, max observed 19). on_result()'s zero_streak now increments only on a billable, 0-row run that ALSO carries a non-empty blocked_reason (threaded from run_one()'s new blocked_reason extraction out of the run log) -- three healthy empty runs in a row no longer halt the day.
4. Mid-pass stop: added a distinct hunt_meta key 'operator_stop' (separate from 'stop_reason', which check_stop() also writes for its OWN computed verdicts -- reusing stop_reason for both roles is why re-reading it unconditionally would have let check_stop() see and re-trigger on its own earlier verdict). POST /api/hunter/stop and /api/hunter/start (app/hunter_api.py) now set/clear it alongside stop_reason. Hunter.check_stop() reads operator_stop first, before any rule evaluation, and never lets the rule evaluation overwrite it. Did NOT add an is_enabled() check inside _kill_switch() as literally suggested -- HUNTER_DEFAULT['enabled'] is False and the whole test suite (and, per hunter.py's own docstring, the real daemon loop) relies on run_once() being gate-worthy on its own without the caller having called set_enabled(True) first; adding it broke 12 tests whose scenarios call Hunter.run_once()/check_stop() directly. The operator_stop fix covers the actual reported bug (POST /api/hunter/stop not stopping an in-flight pass) completely on its own.
5. app/crawl.py execute()'s firecrawl branch and refetch_career(): the generic 'except Exception' (distinct from FA.AgentFailed, which already carries a measured cost) now charges the ledger with the approved cap as an unmeasured-upper-bound estimate before returning, instead of charging nothing.
6. app/crawl.py execute()'s firecrawl branch: a completed run whose own answer carries a non-empty blocked_reason, or that read zero jobs, now calls R.record_crawl_issue with the full blocked_reason/notes text (kind='firecrawl').
7. app/crawl.py _unseen_source_urls(): chunks at 50 (was 200, matching _post_inbox's own gateway-length fix) and now RAISES on a failed lookup batch instead of swallowing it; spend_gate() catches that and refuses the spend ('unseen-url lookup failed: ...') instead of treating every url as unseen, which had silently defeated the 'adapter already covers it' refusal.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC#4 delivered via a different, more targeted mechanism than literally suggested (see plan item 4) -- the reported bug (mid-pass stop ineffective, operator's reason overwritten) is fully fixed and covered by 2 new tests; the literal is_enabled() re-check inside _kill_switch() was tried and reverted after it broke 12 existing tests whose design relies on run_once()/check_stop() being callable without first enabling the hunter (matches the real daemon's own architecture: the OUTER daemon loop gates entry on is_enabled(), not run_once() itself).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed 6 of 7 named defects exactly as specified, and the 7th (mid-pass stop) via a more targeted, test-compatible mechanism than the literal suggestion. precheck() no longer skips a whole clinic on a department-scoped 'no openings here' box. next_target()'s sibling-skip now requires the harvested run's own rows to demonstrably mention the sibling's town (reading crawl_output/run_<id>.jsonl), closing the coverage gap that left 2 real Helios clinics with zero coverage from any path. suspicious()'s self-invented row ceiling is gone; zero_streak only counts a billable 0-row run that also carries a blocked_reason, so three healthy empty boards no longer halt the day. A new 'operator_stop' hunt_meta key (distinct from 'stop_reason', which check_stop() also writes for its own verdicts) makes POST /api/hunter/stop take effect on an already-running pass and never get silently overwritten. execute()'s generic except now charges the ledger with the approved cap instead of nothing; a completed run reporting blocked_reason or zero jobs now calls record_crawl_issue. _unseen_source_urls chunks at 50 and raises on a failed lookup, which spend_gate now turns into a refusal instead of a silent 'everything is unseen'. 7 new tests (2 in tests/test_hunter.py for zero_streak/operator_stop, 2 in tests/test_firecrawl_hooks.py for the ledger-charge/crawl_issue fixes, 1 for the 50-url chunking, 1 rewritten + 1 new for the sibling-skip evidence requirement) plus updated the hdb test fixture to isolate CRAWL_OUT (it was previously reading the real repo's crawl_output/ directory during tests, an unrelated pre-existing test-isolation gap this task's own new file-reading code exposed). AC#4's literal is_enabled()-in-_kill_switch() suggestion was tried and reverted -- it broke 12 tests whose design (and the real daemon's own architecture) relies on run_once() being callable without the hunter having been separately enabled first; the operator_stop mechanism fixes the actual reported bug without that side effect. Full offline suite -m 'not network': 1030 passed / 1 skipped / 1 pre-existing unrelated failure (tracked as TASK-75 AC#1).
<!-- SECTION:FINAL_SUMMARY:END -->
