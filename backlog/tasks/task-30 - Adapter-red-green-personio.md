---
id: TASK-30
title: 'Adapter red-green: personio'
status: In Progress
assignee:
  - '@ivan.d.kotelnikov'
created_date: '2026-09-10 07:49'
updated_date: '2026-09-10 11:37'
labels:
  - harvester
dependencies: []
ordinal: 30000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
One adapter at a time, per Ivan's method (2026-09-10). Write the red completeness tests for every board this adapter serves, run them, fix the adapter until green (XML vs page parity, site label from office, no narrowing), then break it on purpose and confirm the tests go red naming this adapter. Do not skip Playwright where plain HTTP fails; Firecrawl may be used as an oracle (about 3000 credits available, more on request). Every fetched page is snapshotted.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All completeness checks for personio are green on every board it serves in the live registry
- [ ] #2 Each of the four mutations turns exactly the matching check red for personio
- [ ] #3 Rows returned carry description, city, dates and a browsable url wherever the source exposes them
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. RED: run tests/test_adapter_completeness.py -k personio -m completeness against the live registry (5 personio boards).
2. Diagnose each red: (a) barmherzige.net field-completeness -- not real Personio, falls to shared crawl_wp_jobs (TASK-35 scope, leave alone); (b) munich-airport-clinic.com read-path-coverage -- adapter hardcoded .de even though the tenant only links .com; (c) maximilians-augenklinik declared-total-parity -- harness declared_total() mis-reads Personio's per-subgroup sr-only job-count badges as one page total.
3. GREEN: crawlers/vendor_adapters.py -- personio_domain() keeps the tenant's own TLD instead of forcing .de; add a WordPress "Personio Integration Light" REST fallback (wp-json/wp/v2/personioposition) discovered while auditing prosomno.de, which was silently returning 0 rows through the wp_jobs fallback despite 5 real public postings.
4. Shared-harness fixes (tests/adapter_contract.py, contract helpers only, no personio branch): FETCH_RX no longer treats window.open(url) navigation as a data fetch; COUNT_RX also matches singular "Position"; declared_total() sums per-group sr-only badge counts instead of taking max() when every hit sits inside an sr-only span.
5. MUTATION: run -m mutation -k personio, confirm each of the 4 mutations turns exactly its check red (or is legitimately inert for this adapter's shape) naming personio.
6. Regression: full non-network suite + a broad spot-check of test_read_path_coverage/test_declared_total_parity across every other family, to confirm the shared-harness edits caused no new failures elsewhere.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
RED (live registry, 5 personio-labelled boards): 3 failures --
  field_completeness[karriere.barmherzige.net] :: datePosted, employmentType populated on 0/4 rows
  read_path_coverage[munich-airport-clinic.com] :: client read path never called: medicare-flughafen-muenchen.jobs.personio.com/
  declared_total_parity[maximilians-augenklinik-ggmbh.jobs.personio.de] :: board declares 3, adapter returned 8

Root causes and fixes (crawlers/vendor_adapters.py, owned):
  - personio_domain() (renamed from personio_slug) now keeps the tenant's own TLD end-to-end instead
    of forcing .de -- munich-airport-clinic.com only ever links the .com form; personio answers both,
    so the bug was invisible in row counts, only in which host got called. Fixes read_path_coverage.
  - Added a WordPress "Personio Integration Light" plugin fallback (personio_wp_posts/parse_personio_wp):
    prosomno.de has no <slug>.jobs.personio.* tenant at all, and was silently falling through to the
    generic crawl_wp_jobs and returning 0 rows, even though 5 real postings are public at
    wp-json/wp/v2/personioposition. Found by manual audit (not in the original 3 red failures -- the
    shared harness's checks all no-op `if not rows`, so a silent 0 never surfaces on its own). Added a
    dedicated adapter-specific regression in tests/test_completeness_personio.py for this gap.

Shared-harness fixes (tests/adapter_contract.py, contract helpers only -- verified against all 220
live boards with a targeted before/after diff script, not adapter-specific branches):
  - declared_total(): sums per-group sr-only job-count badges instead of max() when every COUNT_RX
    hit sits inside an sr-only span (personio's page groups jobs by subcompany, each with its own
    "N Position(en)" screen-reader-only subtotal and no single page-level total anywhere -- max() of
    the subtotals undercounted).
  - COUNT_RX now also matches singular "1 Position" (was plural-only "Positionen") -- guarded with a
    (?!-) so it does not also match Bootstrap's "w-100 position-relative"/"col-lg-4 position-absolute"
    utility-class pairs (found and fixed after a full-registry false-positive sweep: ukw.de, ameos.eu,
    hessing-kliniken.de all flipped their declared_total on the unguarded version).
  - FETCH_RX's `.open(` alternative no longer matches `window.open(url)` (browser-tab navigation from
    a consent-gated "apply" button), only real XHR-shaped `.open(method, url)` calls -- a full-registry
    diff confirmed the only URLs this newly excludes are window.open() targets (share buttons, mail
    verify links, this board's own personio.com landing page), never a real api_url.

GREEN: 4/5 personio boards fully green (bergmanclinics.de 56 rows, maximilians-augenklinik 8 rows,
munich-airport-clinic 5 rows, prosomno.de 5 rows -- all with description/city/datePosted/
employmentType on 100% of rows). karriere.barmherzige.net stays red on field_completeness: it is not
actually Personio (WordPress site, census mislabel -- comment already noted this pre-existing), the
code path is the shared crawl_wp_jobs, owned by TASK-35. Not touched; the fix belongs there.

MUTATION (-m mutation -k personio, representative board munich-airport-clinic.com): drop_description
-> field_completeness red, api_self_link -> public_url red, both naming personio, no collateral.
cap_first_page and skip_detail both skip as structurally inapplicable to every personio board checked
(verified, not a weak check): personio fetches the tenant's whole roster in one XML/REST request (no
pagination to cap), and the client's own page/scripts never literally reference the /xml or
wp-json/wp/v2/personioposition read path anywhere (it's a documented convention, not client-observed),
so client.api_urls is empty on every personio board -- nothing for skip_detail to block.

Regression: tests/test_vendor_adapters.py + tests/test_completeness_personio.py (22 tests, offline,
mocked) green; full suite `pytest -q -m "not network"` 605 passed, 1 skipped, 0 failed.

Snapshots: crawl_snapshots/{karriere.barmherzige.net,prosomno.de,munich-airport-clinic.com,
maximilians-augenklinik-ggmbh.jobs.personio.de,bergmanclinics.de}/2026-09-10/ (8-19 files each),
written by the harness's own client_read_paths/round-trip fetches across the completeness runs.

Open item: AC#1 not fully green (1/5 boards blocked on TASK-35's wp_jobs fix); AC#2 mutations are
2/4-applicable-and-passing, 2/4 legitimately inapplicable to this adapter's shape (evidenced above,
not weak checks). Leaving status at In Progress pending that decision rather than closing out AC I
can't fully evidence.
<!-- SECTION:NOTES:END -->
