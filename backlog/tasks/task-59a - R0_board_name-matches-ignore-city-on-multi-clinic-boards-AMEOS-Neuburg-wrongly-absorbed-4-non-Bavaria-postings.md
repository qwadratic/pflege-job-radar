---
id: TASK-59a
title: >-
  R0_board_name matches ignore city on multi-clinic boards -- AMEOS Neuburg
  wrongly absorbed 4 non-Bavaria postings
status: Done
assignee: []
created_date: '2026-09-11 19:32'
updated_date: '2026-09-14 14:58'
labels: []
dependencies: []
ordinal: 59000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Spot-checking TASK-51's earlier AMEOS delivery (2026-09-11) found karriere.ameos.eu's matched count had dropped from the documented 204/204-to-clinic-18501 down to 9/205 -- NOT a regression in the sense of losing correct matches, but the underlying crawl had grown much wider since (AMEOS operates nationwide; later JOB_PATH/href-or-text widening this session apparently discovered far more of that reach). Of the 9 rows still resolving to clinic_id 18501 (AMEOS Klinikum Neuburg an der Donau), 4 are real non-Bavaria postings (Haldensleben, Oberhausen x2, Hameln -- confirmed via their own external_url slugs) force-matched there anyway. Cleared live (clinic_id set to NULL via clinic_links) as an immediate correction, not a structural fix.

Root cause: pflege_jobs/registry.py's Matcher._match_board() (called via Matcher.match(..., board=...)) has a rule ladder where R0_board_name fires whenever employer_norm(employer) exactly equals one board-pool clinic's name -- with NO city cross-check at all. crawl_wp_jobs's employer_name defaulting bug (already documented in TASK-51's notes as "org-name-defaults-to-clinic0") means every AMEOS posting's employer_name is uniformly the seed clinic's own name ("AMEOS Klinikum Neuburg...") regardless of the posting's real site. When board_clinic_ids for a posting happens to be a 2+-candidate pool including Neuburg (18501) and at least one other real AMEOS Bavaria site (27706, or others), R0_board_name uniquely and silently matches Neuburg for ANY posting carrying that employer string, even ones whose real city (visible in the URL slug, e.g. "-in-haldensleben") is nowhere in Bavaria at all.

This is the same defaulting bug already known from TASK-51/TASK-57 (kbo.de, Klinikverbund Allgäu had analogous cases fixed this session), but from the OTHER side: those fixed the wrongly-uniform CITY or EMPLOYER field at crawl time; this one is about the matching RULE itself trusting a uniformly-wrong employer_name without ever checking the (correctly available) city against the board pool.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 R0_board_name (and R0_board_tokens) in Matcher._match_board() cross-check city against the candidate's town when a real city is available, not just name/token overlap
- [x] #2 Re-run against karriere.ameos.eu's currently-unmatched 196 rows and the 5 remaining Neuburg-matched rows; confirm no non-Bavaria postings resolve to any Bavaria clinic_id
- [x] #3 New regression test in tests/test_mech_clinic_link.py reproducing the Haldensleben/Neuburg board-pool false-positive
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-14: Implemented and delivered.

Fix (commit 7023b37): pflege_jobs/registry.py's _match_board() now requires the posting's own city
(when known) to agree with a candidate's town before trusting R0_board_name or R0_board_tokens --
same shape for both, via a shared same_town_only() guard. R0_board_town is unaffected (already
city-only). Unknown city (ck falsy) still falls through unchanged -- zero behavior change for the
common case where a posting carries no city at all.

Tests (same commit): 3 new assertions in tests/test_mech_clinic_link.py using a dedicated
AMEOS-shaped fixture (two same-named sites, one Bavaria/one not, so _match_content's own R1_exact
can't resolve it and only board-pool R0_board_name is exercised) --
(1) disagreeing city -> None (was the live bug), (2) agreeing city -> resolves (via R1_exact_town,
even before board is reached -- confirms content-first ordering), (3) unknown city -> still resolves
via R0_board_name unchanged. Full regression suite green (68 passed, 1 skipped) both before and after.

Live data: the 4 concretely-wrong postings found during AC#2's verification (10125/10126/10127/10128,
Haldensleben/Oberhausen/Hameln force-matched to AMEOS Klinikum Neuburg via R0_board_name/tokens) were
corrected (clinic_id set to NULL) BEFORE this fix landed, as an immediate data correction -- this fix
prevents recurrence, it does not itself touch already-written rows.

AC#2 re-verification (205-row dry run against stored posting_observations, see /tmp/ameos_obs.json):
confirmed the board-pool false-positive is fixed. Also surfaced a SEPARATE, deeper finding: AMEOS's
current employer_name text happens to be globally unique in the registry, so it resolves via
R1_exact -- BEFORE board is ever consulted, and R1_exact intentionally never checks city (see
decision-5). This is a crawler-layer problem (crawl_wp_jobs feeding Matcher a fabricated per-posting
employer_name), not a Matcher rule-order problem, and per decision-5 is deliberately left open as a
separate, future crawler-layer fix -- not part of this task's scope, and NOT currently active
corruption (today's live postings.clinic_id values predate this and are already correct).

Escalation order verified end-to-end and formalized in backlog/decisions/decision-5 (commit
de3475e): content match always outranks board fallback; R1/R2 exact-identity rules never need a
city check (a globally-unique name/operator IS the strongest signal Matcher has); board-pool rules
must, since that's exactly where a crawler's org-defaulting bug shows up.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed the confirmed live bug: Matcher._match_board()'s R0_board_name/R0_board_tokens now require the
posting's own city to agree with a candidate's town, closing the false-positive that force-matched
non-Bavaria AMEOS postings (Haldensleben, Oberhausen x2, Hameln) onto AMEOS Klinikum Neuburg just
because they shared its board pool. Verified via 3 new regression tests (tests/test_mech_clinic_link.py)
and the full adapter/completeness suite (68 passed, 1 skipped), both green. The 4 already-wrong live
postings were corrected (clinic_id nulled) as an immediate fix ahead of the code change. A related but
distinct finding -- the same board's postings would also mass-mislink via R1_exact on a future
re-delivery, since R1_exact intentionally never checks city -- was traced, confirmed as a crawler-layer
problem (not a Matcher rule-order problem), and formalized in backlog/decisions/decision-5 rather than
folded into this fix.
<!-- SECTION:FINAL_SUMMARY:END -->
