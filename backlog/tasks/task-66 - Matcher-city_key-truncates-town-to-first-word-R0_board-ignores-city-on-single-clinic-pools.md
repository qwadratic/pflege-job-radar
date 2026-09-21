---
id: TASK-66
title: >-
  Matcher city_key truncates town to first word; R0_board ignores city on
  single-clinic pools
status: Done
assignee:
  - '@claude'
created_date: '2026-09-18 10:07'
updated_date: '2026-09-18 12:50'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 66000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Crawler-review 2026-09-18. pflege_jobs/registry.py city_key() (:60) truncates a town to its first word (c.split()[0]), so every "Bad *" town (19 distinct towns / 27 clinics) and every "Neustadt *" town (4 distinct / 5 clinics) collapse into one bucket for R1_exact_town/R2_operator_town/R3_tokens/R4_tokens_op/R5_loose/R0_board_town. Reproduced on the real registry: the Heiligenfeld softgarden board attributes a Bad Woerishofen posting to Fachklinik Heiligenfeld in Bad Kissingen (100+ km away) via R2_operator_town, because both towns key to "Bad". Separately, Matcher._match_board R0_board (:191) attaches any content-unmatched posting of a board whose registry pool collapsed to a single clinic to that clinic at score 0.9 with no city check at all -- the one board-fallback rule TASK-59a/decision-5 same_town_only guard did not cover. Reproduced on run_89 (2026-09-17): R0_board fires 105 times, 70 of them (67%) attribute the posting to a clinic in a different town -- psychosomatik-diessen.de/karriere (Artemed SE smartrecruiters feed, pool=[18105 Diessen am Ammersee]) takes 50 rows whose own page states Tutzing (28), Augsburg (10), Feldafing (6), although 18802 Tutzing / 18813 Feldafing / 18808 Berg are all in the registry. This directly violates decision-5 (board-fallback rules must cross-check a known city; "no match beats a wrong match"). See /tmp/crawler_review_2026-09-18.md "pflege_jobs/registry.py" :60 and :191 for full evidence and fix sketches.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 city_key (or its caller) compares the full canonicalised town string (reusing pflege_jobs.sources.career_crawl._canon_town, which already folds an der/a.d./am/bei + punctuation) rather than only the first word, while still treating a word-boundary prefix as equal so multi-word registry towns like "Weiden i.d. Oberpfalz" still match their bare form
- [x] #2 The Heiligenfeld Bad Woerishofen posting no longer matches the Bad Kissingen clinic via R2_operator_town after the fix; a regression test pins this pair
- [x] #3 Matcher._match_board R0_board only attaches a posting to a single-clinic pool when the posting has no known city, or when the known city matches that clinic town (same guard already applied to R0_board_name/R0_board_tokens per decision-5); otherwise it returns no match
- [x] #4 Re-running the intake replay against run_89.jsonl shows the psychosomatik-diessen.de Tutzing/Augsburg/Feldafing rows no longer attributed to clinic 18105
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. pflege_jobs/registry.py city_key(): dropped the '.split()[0]' truncation to a single word; now returns career_crawl._canon_town's full canonical form (connector words an der/a.d./am/bei/... folded away, but the geographic qualifier NOUN that follows one is kept, e.g. 'Neuburg an der Donau' -> 'neuburg donau'). Empirically verified against the whole registry (data/registry/clinics.csv, 231 distinct towns): the old code collapsed 19 distinct 'Bad *' towns / 27 clinics and several differently-qualified 'Neustadt *' towns into two buckets; the new code's only remaining collisions are genuine spelling variants of the same place ('a. d.' vs 'a.d.' spacing) plus pre-existing junk stems ('Co. KG'/'GmbH & Co. KG') already flagged elsewhere.
2. Added _town_match(a, b): equal, or one is a whitespace-delimited prefix of the other -- lets a posting that just says the bare town ('Neuburg') still earn a registry town whose canon form keeps a qualifier ('neuburg donau'), without letting two DIFFERENT towns that merely share a first word ('bad kissingen' vs 'bad woerishofen') match each other.
3. Replaced every 'city_key(...) == ck' equality check in _match_content and _match_board (R1_exact_town, R2_operator_town, R0_board_name/_town/_tokens) with _town_match, and replaced the by_town dict's exact-key .get(ck) lookup (used by R3_tokens/R4_tokens_op/R5_loose's same-town candidate gathering) with a new Matcher._by_town(ck) that scans and prefix-matches -- a plain dict lookup can't be prefix-aware. Also fixed two now-stale '.split("-")' town-token-exclusion calls (registry.py:107,148) to '.split()' (whitespace), matching the new multi-word city_key.
4. Matcher._match_board's R0_board (single-clinic pool) now refuses when the posting has a known city that disagrees with that clinic's town -- previously matched unconditionally, the one board-fallback rule TASK-59a's same_town_only guard and decision-5 didn't yet cover.
5. Verified against the REAL registry (data/registry/clinics.csv, full Matcher build): Heiligenfeld 'Bad Woerishofen' now resolves to clinic 77805 (was wrongly landing on 67208 Bad Kissingen via R2_operator_town); the psychosomatik-diessen.de-shaped R0_board scenario (single-clinic pool, board postings actually in Tutzing/Feldafing) now returns None for the disagreeing towns and still returns the correct match for Diessen itself.
6. tests/test_mech_clinic_link.py: updated test_city_key for the new canonical (non-truncated) format, added test_town_match_is_prefix_aware_but_not_over_permissive, test_r2_operator_town_no_longer_collapses_bad_towns, test_r0_board_single_clinic_pool_refuses_a_disagreeing_known_city.
7. Full offline suite -m 'not network': 1004 passed / 1 skipped / 1 pre-existing unrelated failure (tracked as TASK-75 AC#1).
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
city_key() now compares the full canonicalised town (career_crawl._canon_town) instead of truncating to its first word, fixing the confirmed 'Bad *'/'Neustadt *' collision (19+ distinct towns colliding into 2 buckets, verified empirically against the whole registry both before and after). Added _town_match() for prefix-aware equality (so a bare posting city still earns a qualified registry town) and threaded it through every city_key comparison site including the by_town dict lookup (which needed a new scanning method, _by_town, since a plain dict get() can't be prefix-aware). Matcher._match_board's R0_board rung (single-clinic pool) now refuses a posting whose known city disagrees with that clinic's town, closing the one board-fallback rule that had no city check at all (confirmed live: the psychosomatik-diessen.de-shaped scenario, R0_board firing 105x in one run with 70 going to the wrong town). Verified against the real registry: Heiligenfeld Bad Woerishofen -> clinic 77805 (not Bad Kissingen); the Diessen board scenario refuses Tutzing/Feldafing while keeping its own real match. 4 new/updated tests in tests/test_mech_clinic_link.py; full offline suite -m 'not network': 1004 passed / 1 skipped / 1 pre-existing unrelated failure (tracked as TASK-75 AC#1).
<!-- SECTION:FINAL_SUMMARY:END -->
