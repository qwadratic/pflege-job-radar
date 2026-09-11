---
id: decision-4
title: >-
  Prefer the Plan-KH row when a fuzzy match ties between near-duplicate
  Krankenhausplan entries
date: '2026-09-11 15:04'
status: accepted
---
## Context

Found 2026-09-11 while re-verifying TASK-51's fuzzy-matched postings (see TASK-57). Klinikum Bamberg
has two registry rows at the same physical site, both sourced from "Krankenhausplan Bayern 2026 (51.
Fortschreibung)", same operator ("Sozialstiftung Bamberg"), same town ("Bamberg"), near-identical
names:

- `46101` "Klinikum Bamberg - Betriebsstätte am Bruderwald-" — status `Plan-KH`, 911 beds,
  Maximalversorgung (III), full department list (AUG|CHI|GUG|HNO|HUG|INN|KIN|MKG|NCH|NEU|NUK|PSO|PSY|SON|STR|URO|HD)
- `46170` "Klinikum Bamberg - Betriebsstätte am Bruderwald" — status `Vertrags-KH`, 0 beds,
  versorgungsstufe `-`, one department (INN)

This is not a data-entry error to merge away: `Plan-KH` and `Vertrags-KH` are distinct regulatory
categories in the source document (a planned hospital vs. a contracted one), legitimately co-existing
at the same address. A posting whose employer text just says "Klinikum Bamberg (Bruderwald)" carries
no token that distinguishes which of the two it means — `pflege_jobs/registry.py`'s Matcher correctly
declines to guess and returns `None` (5 postings affected, confirmed via a board-aware re-check with
the real board_ids `[46101,46103,46170,47403]` and each posting's own description).

The same registry shape recurs elsewhere in the Bayern Krankenhausplan (a real `Plan-KH` alongside a
near-identical low-info `Vertrags-KH`/partial-parse entry for the same site) — e.g. `47504`/`47570`
"Klinik am Park (Bad Steben)" hit an adjacent bug this session (a Matcher false-positive, fixed
separately, see TASK-51) but is the same registry pattern.

## Decision

When a fuzzy content match ties between exactly two (or more) same-site candidates where all but one
carry essentially no distinguishing operational data (`beds == 0` or missing, `versorgungsstufe == '-'`,
a near-empty `fachrichtungen` list), prefer the candidate with the real data — i.e. the actual `Plan-KH`
entry — over the placeholder `Vertrags-KH`/partial entry, rather than returning `None`. This mirrors
the tie-break `pflege_jobs/registry.py`'s `_pick_site()` already applies for `R6_ambiguous_sites`
(`max(cands, key=lambda x: x.get("beds") or 0)`), just reached from the token-overlap comparison
(`_match_content`'s R3/R4 loop) rather than the operator-match branch that currently calls
`_pick_site()`.

Not implemented yet — this decision records the accepted approach; the code change is tracked in
TASK-57 AC #2.

## Consequences

- The 5 Bamberg-Bruderwald postings currently sitting unmatched become attributable to `46101` (the
  real 911-bed Plan-KH site) once implemented.
- Any other Bavaria site sharing this exact registry shape (a real Plan-KH row plus a low-info
  Vertrags-KH/partial twin) gets the same benefit automatically once the tie-break is generalized
  into the R3/R4 loop, not hand-fixed per site.
- Risk: if a `Vertrags-KH` twin is ever the genuinely correct answer for some posting (a contract-bed
  arrangement distinct from the main hospital), this tie-break will silently prefer the wrong one. No
  evidence of that case found this session; worth a second look if a future audit finds a suspicious
  Plan-KH attribution.
