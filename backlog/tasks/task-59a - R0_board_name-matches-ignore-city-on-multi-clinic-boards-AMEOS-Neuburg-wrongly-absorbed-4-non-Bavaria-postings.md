---
id: TASK-59a
title: >-
  R0_board_name matches ignore city on multi-clinic boards -- AMEOS Neuburg
  wrongly absorbed 4 non-Bavaria postings
status: To Do
assignee: []
created_date: '2026-09-11 19:32'
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
- [ ] #1 R0_board_name (and R0_board_tokens) in Matcher._match_board() cross-check city against the candidate's town when a real city is available, not just name/token overlap
- [ ] #2 Re-run against karriere.ameos.eu's currently-unmatched 196 rows and the 5 remaining Neuburg-matched rows; confirm no non-Bavaria postings resolve to any Bavaria clinic_id
- [ ] #3 New regression test in tests/test_mech_clinic_link.py reproducing the Haldensleben/Neuburg board-pool false-positive
<!-- AC:END -->
