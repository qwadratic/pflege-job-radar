---
id: TASK-100
title: >-
  _match_board has no tie-break for duplicate-registry-row ties (Schön Klinik
  Roseneck/Haus Rosenheim/Berchtesgadener Land pairs stay unmatched even with
  the right board pool)
status: Done
assignee: []
created_date: '2026-09-22 16:10'
updated_date: '2026-09-22 20:35'
labels: []
dependencies: []
references:
  - pflege_jobs/registry.py
  - TASK-99
  - TASK-57
  - TASK-81
ordinal: 100000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Same 2026-09-22 unmatched-inbox review as TASK-99. jobs.schoen-klinik.de has 22 unmatched rows total, but they arrive via TWO different board_urls, not one. 14 rows arrive via the correctly-pooled board_url (12 clinics, all sharing one literal careers_url): 8 of these are genuine ties (18716/18776 both 'Schoen Klinik Roseneck' in Prien am Chiemsee, 16306/16370 both 'Schoen Klinik Roseneck - Haus Rosenheim' in Rosenheim, 17206/17276 both 'Schoen Klinik Berchtesgadener Land' in Schoenau am Koenigssee) that pflege_jobs/registry.py Matcher._match_board's R0_board_name/R0_board_town/R0_board_tokens (around line 292) refuse outright on any len(hit)>1 -- unlike Matcher._match_content, which already resolves this exact shape via _pick_site (real-bed-capacity preference, decision-4/TASK-58A), gated on the tied candidates sharing one normalized name or operator so it never fires across genuinely different sites. _match_board has no equivalent call anywhere. The remaining 6 of these 14 are Munich's genuinely-different Harlaching (16209) vs Schwabing (16224) -- a real ambiguity, must stay unmatched. The OTHER 8 rows (Prien 4, Rosenheim 2, Schoenau 2) arrive via a DIFFERENT board_url (https://jobs.schoen-klinik.de/, no path) whose pool is a single unrelated clinic, 16260 'Schoen Klinik Tagesklinik Muenchen' -- there is no tie there at all, town just does not match, so this task's tie-break fix cannot recover them; they are a third live instance of TASK-99's mechanism (sibling careers_urls of one board never grouped into one pool) and belong there. Interaction with TASK-99: the Feldafing pair (18813/18872) would hit this exact tie-break gap once TASK-99's pool-widening lands -- today it never even reaches a tied _match_board call because the pool is wrong first, so this task alone does not recover Feldafing without TASK-99, and TASK-99 alone does not recover Feldafing without this task.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 _match_board's R0_board_name/R0_board_town apply the same same-name-or-same-operator-gated real-capacity tie-break (_pick_site) that _match_content already uses, not a blanket max-beds pick across any tied candidates regardless of relationship
- [x] #2 München's genuinely-different Harlaching (16209) vs Schwabing (16224) tie (the board's remaining 6 unmatched rows) is NOT force-resolved by this change -- two real, different sites stay correctly unmatched without a stronger per-posting signal
- [x] #3 Red-green test against the live board (or the real stored payloads) plus a mutation test
- [x] #4 The 8 currently-unmatched rows that ARE genuine ties within the correctly-pooled board_url (Prien am Chiemsee 4, Rosenheim 2, Schoenau am Koenigssee 2) resolve to the correct clinic_id, confirmed against the Bayern Krankenhausplan source for which twin is the real Plan-KH site. The other 8 rows on the board_url-alias (Prien 4, Rosenheim 2, Schoenau 2, pool=[16260] only) are explicitly out of scope for this task -- they belong to TASK-99
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
_match_board's tie-break reuses _pick_site gated on the tied candidates sharing one normalized name or operator (employer_norm(operator or name)) -- same gate decision-4/_match_content already uses. Applied uniformly to all 3 rungs (R0_board_name/_town/_tokens), each returning '<rule>_bestsite' at score-0.1 on a resolved tie. Confirmed live: Rosenheim (16306 beds=99 vs 16370 beds=24), Schoenau (17206 beds=116 vs 17276 beds=80), Prien (18716 beds=234 vs 18776 beds=179) -- 8 postings now carry clinic_match_rule containing 'bestsite'. AC#2 confirmed both by construction (the gate is structurally untouched for len(hit)==1, and Harlaching 16209 vs Schwabing 16224 have different operators so the new elif branch's own gate excludes them) and by live replay.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed + verified live and in production. Replay against real stored payloads (jobs.schoen-klinik.de's correctly-pooled board_url, 14 rows): baseline 0/14 (excluding the 6 genuinely-different Munich sites which correctly stay 0), with fix 8/14 resolved via the new R0_board_town_bestsite rule, matching the task's own predicted Prien(4)/Rosenheim(2)/Schoenau(2) split exactly. Zero regressions possible by construction: the fix only adds a new elif len(hit)>1 branch, the existing len(hit)==1 code path (all 601 currently R0_board*-matched postings registry-wide) is untouched. Tests: tests/test_match_board_tiebreak.py, mutation-tested (reverted, confirmed both tie-break tests red while the Munich-stays-unmatched test stayed green, restored). Applied retroactively via a direct IB.reset(inbox_ids=[...]) + pflege_jobs.cli inbox rerun (no payload patch needed this time, unlike TASK-99/102 -- the board pool and city were already correct, only the matcher logic changed) -- confirmed live in Postgres v_postings: 8 postings now carry a *_bestsite match rule, clinic_id distribution 18716 0->4, 16306/17206 correctly split from their shared totals.
<!-- SECTION:FINAL_SUMMARY:END -->
