---
id: TASK-26
title: 'Adapter completeness: shared contract + red tests for every adapter and board'
status: To Do
assignee: []
created_date: '2026-09-10 07:49'
labels:
  - harvester
dependencies: []
ordinal: 26000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's method (2026-09-10): an adapter is proven complete red-green against the board itself, never against our parser. tests/adapter_contract.py holds the oracle helpers (client read paths from page + JS bundles incl. vendor CDNs, declared total, endpoint-shape coverage, public-url check, call recorder, snapshot writer); tests/test_adapter_completeness.py parameterises them over the live registry, one test id per (adapter, board, check). Every page fetched is saved under crawl_snapshots/<host>/<date>/ with a manifest, so the mirror grows as a side effect. Tests are marked network and completeness; pytest.ini registers the markers.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 tests/test_adapter_completeness.py exists with one parametrised test id per (adapter, board, check) for read-path coverage, declared-total parity, field completeness, public url and round trip
- [ ] #2 Running it against the live registry produces red results for every board where the adapter is incomplete, each failure naming the adapter, the board and the missing item
- [ ] #3 Every fetch performed by the suite is written to crawl_snapshots with a manifest line
<!-- AC:END -->
