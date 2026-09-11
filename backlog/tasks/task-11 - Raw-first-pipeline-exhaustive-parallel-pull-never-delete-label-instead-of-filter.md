---
id: TASK-11
title: >-
  Raw-first pipeline: exhaustive parallel pull, never delete, label instead of
  filter
status: To Do
assignee: []
created_date: '2026-09-09 07:27'
labels: []
dependencies: []
ordinal: 11000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Direction change from Ivan, 2026-09-09. Today the crawlers filter while they crawl and the sinks drop rows before they are ever stored: pflege_jobs/sinks.py only_pflege discards four role classes, classify.py's pflege_gate rejects a title outright, and the inbox drain drops rows on a Bavaria check. That makes the stored corpus a product of whatever criteria were active on the day of the crawl, so no classification or geo strategy can be re-evaluated without paying to re-crawl.

The new shape: pull every posting from every Bavarian clinic board in full with parallel parsers, store it raw and untouched in its own table, and never remove a row unless it is a genuine duplicate. Everything that is a filter today becomes a label written alongside the raw row, so a strategy can be re-run over frozen data and two strategies can be compared.

Geo becomes data plus an algorithm rather than a regex: ship Bavarian toponyms with their PLZ, code and comment the rare cases explicitly, and let a small documented algorithm assign a Land label that we then filter on. This has to extend to the other fifteen Bundeslaender without redesign -- Ivan intends to add other Laender's clinic-list PDFs and scale to Germany.

Research runs must not cost money or hit the network: a remotely controlled session runs Clawl against the raw table as fixtures, so every research run is reproducible and free.

Context a future agent cannot recover from the code: the live site's current keep/drop behaviour must stay byte-identical while this is built alongside it, because the nursing criteria are frozen -- only fixes, or additions proven to add relevant vacancies. Measured evidence behind the change: the 'fallback' role rule is 169 rows (7.1% of posting_observations) and roughly 91% of it is not a nursing job; a pure denylist strategy would admit 751 extra distinct titles including 'Postbote fuer Briefe' and 'Bauhelfer'; the allowlist gate is too tight in five enumerable places; and the current Bavaria detector returns True for 'Neustadt an der Weinstrasse', 'Landau in der Pfalz', 'Friedberg (Hessen)', 'Weilheim an der Teck' and 'Hof, Westfalen'. None of that could be measured from the stored corpus alone -- it needed the raw files on disk, which is the whole argument for this task.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every posting a parser sees is stored raw, with provenance, before any classification or geo decision runs
- [ ] #2 No code path deletes a stored observation; the only removal is deduplication, and the dedup key is proven not to merge two genuinely different postings
- [ ] #3 Every filter that exists today is expressed as a label on the raw row, recording both the verdict and the rule that produced it
- [ ] #4 A frozen raw corpus can be re-labelled by a new strategy without re-crawling, and two strategies can be compared on the same corpus
- [ ] #5 Land labelling is driven by a shipped toponym dataset plus a documented algorithm that returns UNKNOWN rather than guessing, and adding a second Bundesland requires no change to the algorithm
- [ ] #6 A research session runs Clawl entirely from fixtures with zero network calls and zero paid API credits, and fails loudly rather than silently falling through to the network
- [ ] #7 The live site's keep/drop behaviour is unchanged while this is built
<!-- AC:END -->
