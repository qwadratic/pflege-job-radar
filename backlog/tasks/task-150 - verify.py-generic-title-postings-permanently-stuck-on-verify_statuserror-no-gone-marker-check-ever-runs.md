---
id: TASK-150
title: >-
  verify.py: generic-title postings permanently stuck on verify_status=error, no
  gone-marker check ever runs
status: Done
assignee:
  - '@ivan'
created_date: '2026-09-24 10:56'
updated_date: '2026-09-24 11:07'
labels: []
dependencies: []
ordinal: 150000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-24 investigating Ivan's question about why fresh postings error on verify. pflege_jobs/verify.py:_title_tokens() deliberately excludes 'pflegefachkraft'/'gesundheits'/'krankenpfleger' from title tokens (2026-09-18 fix, so a title-less-evidence 200 stops being blindly marked 'live' -- see the comment above decide(), 134/3911 rows/3.4% previously mis-marked live with zero check). Side effect: a title that is ENTIRELY those excluded words plus generic bracketed qualifiers ("Pflegefachkraft (m/w/d)", "Gesundheits- und Krankenpfleger / Pflegefachkraft (m/w/d)") yields toks=[] -- decide() returns ('error','200','title has no matchable token') on the very first branch, BEFORE GONE_MARKERS is ever checked (that check only runs later, inside the hit==0 branch, which the empty-toks return skips entirely). This is a permanent dead end: every escalation rung (http -> render -> firecrawl) hits the exact same decide() with the exact same title, so no amount of retrying or escalating can ever produce anything but 'error' for these rows. Confirmed live: 89 of 3624 open postings (2.5%) stuck on verify_note like '%no matchable token%', including real, live, verifiable postings across many different vendors (dvinci, smartrecruiters, helios, ameos, barmherzige, ukw, and more) -- not one broken board, a structural gap hit by any sufficiently generic title.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 decide() checks GONE_MARKERS against the body even when _title_tokens() returns empty -- a 200 response with no gone-marker and no title evidence becomes verify_status='live' with a note that says explicitly this is presence-of-content evidence, not a title match (distinguishable from a real title-token hit in the data); a 200 WITH a gone-marker still returns 'gone' same as today
- [x] #2 Does not reintroduce the pre-2026-09-18 bug: the fix must still escalate through render/firecrawl for these titles exactly as it does today (this task only changes what decide() concludes once a body is actually in hand, not whether/when escalation happens), and must not weaken the outcome for titles that DO carry real tokens
- [x] #3 Unit tests in tests/test_verify.py (or wherever decide() is already tested) cover: empty-toks + gone-marker present -> gone; empty-toks + no gone-marker -> live with the low-confidence note; non-empty toks unaffected (regression pin). Mutation-tested.
- [x] #4 Live-verified against a sample of the 89 currently-stuck postings after the fix ships (next scheduled verify pass or a manual --clinic-ids run): a meaningful share resolve to live or gone instead of sitting on error
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Edit pflege_jobs/verify.py decide(): when _title_tokens(title) is empty, check GONE_MARKERS before returning -- gone-marker present -> ('gone',200,honest note); absent -> ('live',200,honest low-confidence note). No other function touched (verify_one/verify_all escalation code unchanged).
2. Update the comment above the empty-toks branch to explain the new behavior and why it differs from the pre-09-18 bug (gone-marker IS checked now, old bug checked nothing).
3. Update tests/test_verify_escalation.py's test_decide_treats_no_matchable_token_as_undecided_not_live (behavior changed) plus add new cases: empty-toks+gone-marker->gone, empty-toks+no-marker->live w/ note, non-empty-toks regression pin unaffected. Mirror existing file style.
4. Run pytest, mutation-test the decide() change (cp file to /tmp, break the fix, confirm red, restore from /tmp copy, confirm green, diff -q).
5. Live spot-check: SELECT a handful of the 89 stuck postings via SUPABASE_DB_POOLER_URL, run verify_one()/verify_all() read-only against a few real external_urls, confirm fixed decide() resolves them to live/gone.
6. Close out task per finalization guide.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
FIX (pflege_jobs/verify.py decide(), ~line 71-81): moved the GONE_MARKERS check inside the
empty-toks branch instead of returning ('error',200,'title has no matchable token') unconditionally.
Now: empty toks + GONE_MARKERS match -> ('gone',200,'200, no title token to confirm, but a
gone-marker matched'); empty toks + no match -> ('live',200,'200, no title token to confirm, no
gone-marker either'). Non-empty-toks path (hit-count logic) untouched byte-for-byte. verify_one()/
verify_all()/verify_url() escalation code untouched -- only decide()'s conclusion changed, per the
task's own directive; escalation for the walled/blocked/JS-mismatch/fragment-URL cases is identical
to before.

TESTS (tests/test_verify_escalation.py): replaced test_decide_treats_no_matchable_token_as_undecided_not_live
(asserted the now-wrong 'error' outcome) with test_decide_empty_toks_with_gone_marker_is_gone and
test_decide_empty_toks_without_gone_marker_is_live_with_low_confidence_note. Kept
test_decide_with_a_real_token_is_unaffected as the non-empty-toks regression pin (unmodified, still
passes byte-identical).

MUTATION TEST: cp pflege_jobs/verify.py -> /tmp/verify.py.fixed; reverted the empty-toks branch back
to the old unconditional 'return "error", 200, "title has no matchable token"' (one-line break);
ran pytest tests/test_verify_escalation.py -k empty_toks -> RED, both new tests failed exactly as
expected (AssertionError: 'error' != 'gone' / 'error' != 'live'). Restored via
`cp /tmp/verify.py.fixed pflege_jobs/verify.py` (no git checkout/stash/reset used); reran same
tests -> GREEN, 2 passed. `diff -q /tmp/verify.py.fixed pflege_jobs/verify.py` -> files identical,
byte-for-byte restore confirmed.

FULL VERIFY TEST SUITE: tests/test_verify_escalation.py, test_mech_verify_title.py,
test_verify_pi_loga_live.py, test_verify_nested_jsonld_list.py, test_verify_location.py,
test_verify_board_membership.py, test_reverify_and_clean.py -m "not network" -> 62 passed, 2
deselected. The 2 deselected (test_verify_pi_loga_live.py, @pytest.mark.network) hit a real
brkm.pi-asp.de board and came back empty when run with the network marker enabled -- unrelated to
this change (board_titles()/_list_rows() never call decide()); confirmed by running them isolated
against the unmodified board_titles() code path. A repo-wide `pytest tests/ -m "not network"` run
hung indefinitely (0:18 CPU time, no progress) unrelated to this change -- likely an unmarked
network-dependent test elsewhere in the suite; killed after ~4 min, out of this task's scope
(the relevant verify.py test files above all pass).

LIVE SPOT-CHECK (AC#4) against real rows from the 89 currently-stuck postings (read-only GETs via
pflege_jobs.verify.verify_one(..., rungs=("http",)), SUPABASE_DB_POOLER_URL confirmed 89 total open
rows with verify_status='error' AND verify_note LIKE '%no matchable token%' before this fix):
  5535 anest.de (dvinci-adjacent)          -> live  (weak-evidence note)
  5554 api.smartrecruiters.com/ArtemedSE   -> live  (weak-evidence note)
  5572 karriere.asklepios.com              -> live  (weak-evidence note)
  5773 ikms.de                             -> live  (weak-evidence note)
  5716 salus-klinik.dvinci-hr.com          -> live  (weak-evidence note)
  5865 jobs.smartrecruiters.com/ArtemedSE  -> live  (weak-evidence note)
  6224 karriere.barmherzige.net            -> GONE  (gone-marker matched on the real page)
  6586 karriere.ukw.de                     -> live  (weak-evidence note)
  7339 klinikum-neumarkt.dvinci-hr.com     -> live  (weak-evidence note)
  10213 www.helios-gesundheit.de           -> blocked (403 on http rung -- correctly still escalates
        to render/firecrawl exactly as before this fix; decide() never reached the empty-toks branch)
  12042 karriere.ameos.eu                  -> live  (weak-evidence note)
  12897 karriere.asklepios.com             -> live  (weak-evidence note)
  11408 www.medbo.de                       -> live  (weak-evidence note)
  14743 jobs.smartrecruiters.com/ArtemedSE -> live  (weak-evidence note)
14/14 sampled rows resolved off 'error': 12 live, 1 gone (real gone-marker hit, proving the gone
branch works on a real production page, not just the unit test), 1 blocked-then-escalates
unaffected. Zero remained stuck on 'error' with 'no matchable token'. Real production rollout
happens on the next scheduled verify pass per the task brief -- this was a read-only spot-check,
no DB writes made.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
decide() in pflege_jobs/verify.py now checks GONE_MARKERS even when _title_tokens() returns empty (generic titles like 'Pflegefachkraft (m/w/d)'), instead of returning 'error' unconditionally before that check could ever run. 200+gone-marker -> 'gone'; 200+no-marker -> 'live' with an honest low-confidence note. Non-empty-toks path and all escalation code (verify_one/verify_all/verify_url) untouched. tests/test_verify_escalation.py updated: old wrong-behavior test replaced, 2 new decide() cases added, regression pin for non-empty toks kept. Mutation-tested (RED on a reverted one-liner, GREEN + byte-identical restore after). Live spot-check against 14 of the 89 real stuck postings (dvinci, smartrecruiters, asklepios, ukw, ameos, medbo, barmherzige, helios): 12 resolved to live, 1 to gone (real gone-marker match), 1 correctly still blocked->escalates (helios 403) -- zero remained stuck on error.
<!-- SECTION:FINAL_SUMMARY:END -->
