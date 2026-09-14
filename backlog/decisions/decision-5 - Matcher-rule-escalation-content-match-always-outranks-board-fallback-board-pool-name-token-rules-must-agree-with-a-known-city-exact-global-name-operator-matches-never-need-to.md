---
id: decision-5
title: >-
  Matcher rule escalation: content match always outranks board fallback;
  board-pool name/token rules must agree with a known city, exact global
  name/operator matches never need to
date: '2026-09-14 14:57'
status: accepted
---
## Context

Prompted 2026-09-14 by a TASK-59a fix: `pflege_jobs/registry.py`'s `Matcher._match_board()` gained a
city cross-check on `R0_board_name`/`R0_board_tokens` (confirmed live: karriere.ameos.eu's
crawl_wp_jobs sets every posting's `employer_name` to whichever clinic seeded the crawl, so
`R0_board_name` silently matched Haldensleben/Oberhausen/Hameln postings — nowhere near Bavaria — to
AMEOS Klinikum Neuburg just because they shared its board pool). Before generalizing that fix, this
decision records a full trace of `Matcher.match()`'s whole rule ladder, to confirm the escalation
order is correct and to make explicit *why* the new city-guard belongs only where it was added.

`Matcher.match(employer, city, board, description)` runs in two phases:

1. `_match_content(employer, city, description)` — always tried first, board never consulted:
   - `R1_exact` / `R1_exact_town` (score 1.0 / 0.98): normalized employer text exactly equals one
     site's normalized `name`, globally, across the whole registry (town only needed to break a tie
     between two same-named sites).
   - `R2_operator` / `R2_operator_town` (0.95 / 0.9): same, against `operator` instead of `name`.
   - `R6_ambiguous_sites` at the operator level (0.5): operator matches exactly, but more than one
     site shares BOTH that operator and the posting's city — no more information is left in the
     employer string to disambiguate further (it already reduced to exactly the operator string),
     so this returns immediately via `_pick_site()` rather than falling through to the token loop
     below, which operates on `name` tokens and would only rediscover the same ambiguity.
   - `R3_tokens` / `R4_tokens_op` (0.8 / 0.75, restricted to same-town candidates), with an internal
     tie-break cascade when more than one candidate survives: `_full` (one candidate's own tokens are
     a full subset of the employer's, score −0.05) → `_bestj` (Jaccard gap ≥ 0.1 between best and
     second-best, −0.1) → `_realsite` (decision-4: exactly one candidate carries real bed capacity
     among placeholders, −0.1) → same-operator `R6_ambiguous_sites` tie-break (0.5) → give up on this
     rule, try the next (name tokens, then operator tokens) before falling further.
   - `R5_loose` (0.6): Jaccard ≥ 0.5, and the site is the *only* candidate in that town at all.
   - `_match_jd` (0.65): last content-side check — exactly one clinic's name/operator token set
     appears verbatim in the job description.
2. Only if phase 1 returns `None` and a `board` was supplied: `_match_board()`, restricted to the
   board's own clinic pool — `R0_board` (0.9, trivial single-candidate board), else
   `R0_board_name` (0.9) → `R0_board_town` (0.85) → `R0_board_tokens` (0.7), in descending trust
   order, each requiring a `len(hit) == 1` unique result.

This ordering is correct and does not need to change:

- Content-before-board is intentional and already documented in `match()`'s own docstring
  ("board-first was tried and reverted: it forced a guess on shared boards that host non-Bavaria
  entities too"). Board membership is provenance, not identity — it only gets a vote when the
  employer/city text genuinely cannot decide on its own.
- `R1_exact`/`R2_operator` never checking city is intentional, not an oversight: a normalized
  employer string that is *globally* unique across the entire Bavaria registry is the strongest
  possible signal Matcher has, specifically because it is unique — nothing about it depends on which
  board happened to serve the posting. Weakening R1/R2 with a city check would only ever produce
  false NEGATIVES (a real site whose posting carries a slightly different city spelling/PLZ
  the crawler didn't normalize), for zero benefit against the AMEOS-style failure mode, which is a
  problem with the INPUT (a fabricated, uniform employer_name), not with the rule.
- `R0_board_name`/`R0_board_tokens` are different in kind, not degree: they only run after content
  matching already failed to find a REAL distinguishing signal, against a POOL the crawler itself
  chose (not the whole registry) — exactly the shape a crawler's org-name-defaulting bug corrupts
  (see TASK-51's "org-name-defaults-to-clinic0" note, TASK-57's kbo.de city-defaulting fix, and this
  session's Klinikverbund Allgäu `_kez`/employer-defaulting fixes). A city cross-check here costs
  nothing when the city is genuinely unknown (`ck` falsy still falls through unchanged) and blocks
  exactly the failure mode that already recurred twice this session under two different crawlers.

One related, EXPLICITLY NOT fixed here: karriere.ameos.eu's current employer_name text
("AMEOS Klinikum St. Elisabeth Neuburg") happens to be globally unique in the registry, so it
resolves via `R1_exact` — before board is ever consulted, and R1_exact does not (and should not)
check city. Verified live (2026-09-14, 205-row dry run against stored `posting_observations`): this
is a LATENT risk (a future re-delivery of this board would mass-mislink every posting nationwide to
clinic 18501), not currently active corruption (today's live `postings.clinic_id` values predate this
and were separately corrected). The right fix for that failure mode is upstream, at the crawler
layer — stop feeding Matcher a fabricated per-posting employer_name in the first place (the same shape
of fix already applied to `pflege_jobs/sources/career_crawl.py`'s `_base()` for the Klinikverbund
Allgäu umantis case) — not a further change to Matcher's rule order or to R1_exact specifically.

## Decision

Keep the existing escalation order (content match, unconditionally, before any board fallback) and
the existing asymmetry (R1/R2 exact-identity rules never check city; R0_board_* rules must, when a
city is known) exactly as traced above. Formalize it as the standing design:

- Any NEW Matcher rule added at the content-match level (`_match_content`) earns its trust from being
  a genuine, unique textual signal across the WHOLE registry — it must not be gated on board/city
  agreement, by the same reasoning R1/R2 aren't.
- Any NEW Matcher rule added at the board-fallback level (`_match_board`) must cross-check city
  whenever the posting carries one, following `R0_board_name`/`R0_board_tokens`'s TASK-59a shape —
  board-pool rules are exactly where a crawler's org-defaulting bug shows up, so they carry the
  burden of defending against it that content rules don't need to.
- Fixing a crawler that feeds Matcher a fabricated employer_name/city (uniform-per-board defaulting)
  belongs in the crawler/seed-builder layer, not in Matcher's rule order — Matcher's job is to trust
  a genuine signal maximally and decline to guess otherwise, not to detect that its input was faked.

## Consequences

- Confirms the TASK-59a fix (city-gating `R0_board_name`/`R0_board_tokens`) was placed at the correct
  layer and needs no further widening into `_match_content`'s R1/R2.
- Leaves the AMEOS `R1_exact` latent risk open, deliberately, as a crawler-layer problem rather than a
  Matcher problem — tracked separately, not by weakening R1/R2 or by this decision.
- Any future "board matched the wrong site" bug report should be triaged against this ladder first:
  if the failure is inside `_match_board`, apply the same city-cross-check shape; if it's inside
  `_match_content` via R1/R2, the bug is almost certainly in what the crawler fed Matcher, not in
  Matcher's own logic.

