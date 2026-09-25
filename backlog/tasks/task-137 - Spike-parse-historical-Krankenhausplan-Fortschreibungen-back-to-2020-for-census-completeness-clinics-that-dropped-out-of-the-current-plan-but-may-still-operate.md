---
id: TASK-137
title: >-
  Spike: parse historical Krankenhausplan Fortschreibungen back to 2020 for
  census completeness -- clinics that dropped out of the current plan but may
  still operate
status: To Do
assignee: []
created_date: '2026-09-23 15:36'
updated_date: '2026-09-25 00:10'
labels:
  - research
dependencies: []
priority: low
type: spike
ordinal: 137000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's idea, 2026-09-23: Bavaria republishes an updated Krankenhausplan Fortschreibung periodically (this registry is built from the 2026, 51st Fortschreibung). Older Fortschreibungen going back to ~2020 likely exist publicly. A clinic present in an OLDER plan year but absent from the current one could mean: genuinely closed/merged (not useful), or renamed/restructured but still operating under a related name (a real registry gap today), or a Vertrags-KH/satellite site the current plan dropped from its own listing for administrative reasons while the physical site keeps hiring. Comparable in spirit to TASK-17 (2025 vs 2026 diff) but going back much further and framed around COMPLETENESS/CENSUS (finding real missing clinics), not just tracking year-over-year plan changes.\n\nExplicitly a spike -- genuinely unknown before investigating: how many historical Fortschreibungen are actually available/findable online back to 2020, whether their PDF format is consistent enough to reuse (or adapt) whatever extraction approach TASK-16337-adjacent PDF-parser-quality task settles on, and whether the signal (old-plan-only clinics) actually produces real, still-operating hospitals worth adding, versus mostly genuinely-closed sites -- this needs a small sample check before committing to parsing every year.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Located and confirmed accessible: Bavaria Krankenhausplan Fortschreibung PDFs for however many years back to 2020 are actually publicly findable (report which years exist, which don't)
- [ ] #2 A small sample (2-3 older years) parsed and diffed against the current 407-clinic registry; report how many old-plan-only clinics show up and a manual check on a handful: genuinely closed, or a real registry gap
- [ ] #3 Go/no-go recommendation: is the hit rate (real missing clinics found) worth parsing every remaining year, based on the sample
<!-- AC:END -->
