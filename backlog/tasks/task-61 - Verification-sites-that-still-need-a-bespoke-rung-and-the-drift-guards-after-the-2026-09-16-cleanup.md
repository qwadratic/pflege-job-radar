---
id: TASK-61
title: >-
  Verification: sites that still need a bespoke rung, and the drift guards after
  the 2026-09-16 cleanup
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-16 22:40'
updated_date: '2026-09-21 07:31'
labels: []
dependencies: []
ordinal: 61000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Follow-up from the 2026-09-16 posting-DB cleanup (Ivan: "status must be usable as the only filter").
The table is currently in the target state -- 2925 postings, all Bavarian, all with a verdict from a
real request, 0 open-but-not-live -- so this task is about keeping it there and about the handful of
sites whose verification depends on a vendor-specific rung rather than the generic ladder.

VERIFIED ONLY VIA A BESPOKE RUNG (works today, but breaks silently if the vendor changes):
- logaallin.regiomed-kliniken.de (P&I LOGA "bewerber-web", GWT, 38 postings, clinics 46301/47801):
  no per-posting page exists at all; liveness = "is the title still in the rendered list", rendered
  with networkidle + scrolling the way pflege_jobs/sources/pi_asp.py does it. A plain render sees an
  empty 7KB GWT shell. If the list markup changes, all 38 fall back to 'error' at once.
- www.helios-gesundheit.de (556 postings before the cleanup, the biggest single host): every page is
  an Akamai "Access Denied" to plain HTTP and to headless Chromium, including a stealth context.
  Only Firecrawl gets through, at ~1 credit per posting. After the cleanup 430 of those rows were
  deleted as non-Bavarian, so the recurring cost is small -- but any Helios posting that stays in the
  table can only ever be verified on the Firecrawl rung.

WATCH ITEMS:
- referral-portal-staging.lmu-klinikum.de had 5 postings in the production table. A STAGING host
  should not be a source at all; its 5 rows turned out to be dead and are now expired, but the
  clinic's careers_url should be checked so staging URLs stop entering.
- The daily mode=verify schedule (05:17 UTC, schedule id 2) records city mismatches and unverifiable
  pages in crawl_issues. That report is the drift signal; if a host starts appearing there in bulk,
  its rung broke.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 regiomed list-membership verification still resolves all of that board's postings (spot-check after any pi_asp change)
- [ ] #2 No staging/preview host appears as a source_url in postings
- [ ] #3 crawl_issues reviewed daily; a host appearing in bulk is triaged to the rung that broke
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read pflege_jobs/verify.py's bespoke rungs (PI_LOGA board_list, Firecrawl-for-Helios) and check each live today.
2. Query the live DB for the invariant TASK-61 protects: open-but-not-live, verify_status missing, stale verified_at, staging hosts. Report drift.
3. Root-cause any rung that is silently NOT firing, and stop the generic rung from judging a board LIST as if it were a posting (a P&I LOGA URL has no detail page, so a fall-through fabricates 'live').
4. Drift guard: a P&I board that renders but lists 0 titles must record an honest 'error' with a distinctive method/note so it reaches crawl_issues on the next verify run, never a silent verdict.
5. Close the AC#2 hole: gate non-production hosts on the seeded-adapter load path too (the inbox path already has NON_PROD_HOST), and fix the registry CSV row that still points at the staging portal.
6. Offline tests, mutation-tested (revert fix -> red, restore -> green) + a network-marked live plausibility guard for the P&I rung.
7. Full offline suite, then notes/AC/summary via the backlog CLI.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Live re-verification of the bespoke rungs, 2026-09-21

**P&I LOGA board-list rung (logaallin.regiomed-kliniken.de + the 3 helios-gesundheit.pi-asp.de boards, 91 open postings).**
Works live today. board_titles() read 77 / 45 / 29 / 16 titles off the four boards registered in
data/registry/pi_seeds.json, and verify_one() routed all four through method='board_list'. Membership
against the live table: 86 of 91 open postings still listed; the 5 misses are 5 regiomed Pflegefachkraft
rows the board genuinely no longer carries (they resolve to 'gone', which is the rung working).

**www.helios-gesundheit.de (Firecrawl-only rung, 11 open rows).** Still Akamai-walled today, to both
plain HTTP and headless Chromium -- verify_one() returns blocked/'bot wall (200 with a refusal page)'
on both rungs. Unchanged from the task description; no Firecrawl credits spent to confirm the third
rung. Those 11 rows are the whole 'open but not live' set in the table right now.

## Three defects found while checking, all fixed at the root

1. **The generic rung was allowed to judge a P&I LOGA board LIST as if it were the posting.** These
   boards have no detail page, so render()/firecrawl fetch the list -- which carries every title,
   removed ones included -- and decide() then finds the posting's own tokens on it and answers 'live'
   forever. Not hypothetical: 51 Helios rows in the table still carry verify_note 'title tokens 3/3
   [rendered]' from exactly that fall-through (before commit 19bc3dc taught FRAGMENT_URL the
   '#position,id=' shape). verify.py now answers a /bewerber-web/ URL from the board list or not at all.
2. **Drift guard.** A P&I board that renders but lists no titles is now recorded as verify_status
   'error', method 'board_list', note 'board listed no titles -- board empty or the list rung broke'.
   _run_verify already routes every error/blocked row into crawl_issues, so a vendor markup change
   surfaces there as ~91 rows under one method on the next run instead of silently freezing or
   expiring a whole board. Also stops the firecrawl rung being billed for a page it cannot answer.
3. **The daily verify has been dead since 2026-09-18** (see next note).

## The invariant this task exists to protect has drifted -- and the reason

Queried live 2026-09-21 (read-only, Accept-Profile: pflege_jobs):

| | filed 2026-09-16 | today |
|---|---|---|
| open postings | 2925 | 2560 |
| open, verify_status not 'live' | 0 | 11 (all www.helios-gesundheit.de, Akamai) |
| open, verify_status NULL (never checked) | 0 | 87 (all first_seen 09-20/09-21) |
| open, last verified before 2026-09-19 | -- | 2473 |
| open on a staging host | 0 (5 expired) | 70 |

Root cause of the staleness: **schedule 2 (daily verify, 05:17 UTC) crashed on 2026-09-21 with
`TypeError: unhashable type: 'list'` after reading 2384 postings into scope (run 109), and run 97 on
2026-09-18 was the last one that wrote a verdict at all.** Reproduced live by sweeping the http rung
over all 2560 open postings: 5 of them (www.komm-ins-klinikland.de) raise at verify.py's
_walk_jsonld, because that site ships its JSON-LD PostalAddress fields as one-item lists
({"postalCode": ["97318"], "addressLocality": ["Kitzingen"]}) and a list inside the (city, plz) tuple
is unhashable. The same shape is already documented in pflege_jobs/sources/inbox.py for the inbox
door -- this was the second door.

Two fixes, both root-cause:
- _walk_jsonld unwraps a one-item list field before the tuple is built (`_scalar`).
- verify_all's threaded http pass contains a per-row exception and escalates that row carrying
  'http rung crashed: <Type>: <msg>', instead of letting as_completed().result() re-raise and end
  the pass. Five bad pages could take down the re-verification of all 2560 rows; the crash is
  recorded per row and still reaches crawl_issues, never absorbed as a verdict.

Re-swept all 2560 open postings on the http rung after the fix: **0 crashes** (was 5), verdicts
live 2272 / gone 37 / blocked 58 / error 193 (the 193 includes the 91 P&I rows the http rung now
correctly refuses to judge, which the render pass then settles on the board_list rung).

## AC#2: staging host, still open in the table
70 open rows on referral-portal-staging.lmu-klinikum.de, all verified 'live' 2026-09-18, first_seen
09-08..09-17. The live clinics table already has clinic 16290 pointed back at www.lmu-klinikum.de/jobs,
but two doors were still open and are now closed:
- data/registry/clinics.csv row 16290 still carried the staging URL (and a single job-DETAIL page at
  that, with no ats_type) -- corrected to https://www.lmu-klinikum.de/jobs + wp_jobs, matching the
  live row, so a registry resync cannot reintroduce it.
- app/crawl.py _load_observations (the seeded-adapter path) writes straight to EdgeSink and never
  passed the NON_PROD_HOST gate that cli.py's inbox drain has. Same regex, both doors now.
The 70 existing rows need a production write (tools/reverify_and_clean.py, secret key) that this
session is not permitted to make.

## Files changed
- pflege_jobs/verify.py -- P&I LOGA rung made exclusive + its zero-titles drift alarm; _scalar for
  list-valued JSON-LD address fields; per-row crash containment in verify_all's http pass.
- app/crawl.py -- NON_PROD_HOST gate on _load_observations, with the dropped hosts logged by name.
- data/registry/clinics.csv -- clinic 16290 careers_url/ats_type corrected off the staging portal.
- tests/test_verify_escalation.py -- 8 new offline regressions.
- tests/test_cli_inbox_probe.py -- the _load_observations test now actually exercises the staging
  host its name already claimed.
- tests/test_verify_pi_loga_live.py (new) -- network-marked drift guard, reads the boards out of
  data/registry/pi_seeds.json so a new board is covered automatically. Asserts behaviour, not markup:
  every registered board still yields titles, and the rung still discriminates (a listed title reads
  live, an invented one reads gone -- the fall-through bug answered 'live' to both).

## Validation
- Mutation-tested, each fix reverted then restored: no _scalar -> 2 red; no http containment -> 1 red;
  old P&I branch -> 2 red; no NON_PROD_HOST gate -> 1 red. All green again after restore.
- tests/test_verify_pi_loga_live.py -m network: 2 passed in 31s against the live boards.
- Full offline suite: 1259 passed, 1 skipped, 1197 deselected (`pytest -m "not network"`).
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 07:31
---
AC#1 checked on live evidence. AC#2 and AC#3 left unchecked on purpose:

AC#2 -- both remaining entry doors are closed (registry CSV row 16290, and the seeded-adapter load path
that bypassed NON_PROD_HOST), but 70 referral-portal-staging.lmu-klinikum.de rows are still status=open
in production right now. Clearing them is a secret-key write (tools/reverify_and_clean.py) which this
session is not permitted to make, so the criterion as written ('No staging/preview host appears as a
source_url in postings') is still false. Want me to prepare that cleanup for you to run?

AC#3 -- crawl_issues is being written (26 rows today, 871 on 09-18), but the daily verify that feeds
the posting/city kinds has produced nothing since 2026-09-18 because run 109 crashed; that crash is
fixed here, and the next scheduled run at 05:17 UTC is the proof. 'Reviewed daily and triaged' is an
ops loop I have no evidence for either way, so I am not checking it.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Re-verified both bespoke rungs live and fixed three defects the check exposed.

The P&I LOGA rung works (77/45/29/16 titles off the four registered boards; 86 of 91 open postings
still listed, the 5 misses genuinely delisted), but the rungs BELOW it were allowed to answer for a
/bewerber-web/ URL -- and since those boards have no detail page, they were judging the board LIST,
which carries every title including removed ones. 51 Helios rows in the table still show 'title tokens
3/3 [rendered]' from that fall-through. verify.py now answers a P&I URL from the board list or not at
all, and a board that renders but lists no titles is recorded as error/method='board_list' so a vendor
markup change lands in crawl_issues as one labelled block instead of silently freezing 91 rows.

Checking the invariant found it broken and found why: the daily verify (schedule 2) has written no
verdict since 2026-09-18 and crashed outright on 2026-09-21 with TypeError: unhashable type: 'list'.
Reproduced live -- 5 www.komm-ins-klinikland.de pages ship JSON-LD address fields as one-item lists,
which _walk_jsonld put inside a tuple it hashes, and the exception propagated out of
as_completed().result() and ended the pass for all 2560 open postings. Fixed at the field (_scalar)
and at the pass (a crashed row is escalated carrying the crash, never absorbed). Re-swept all 2560
open postings on the http rung afterwards: 0 crashes, was 5.

Also closed the two doors that let staging hosts in on the crawler side (registry CSV row 16290 and
app/crawl.py _load_observations).

Verified with: live board renders and a live full-table http sweep; 8 new offline regressions plus a
network-marked live drift guard (2 passed, 31s); every fix mutation-tested red-green; full offline
suite 1259 passed, 1 skipped.
<!-- SECTION:FINAL_SUMMARY:END -->
