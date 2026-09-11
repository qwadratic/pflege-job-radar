---
id: TASK-39
title: 'Adapter red-green: pi_asp (Playwright)'
status: Done
assignee:
  - '@ivan.d.kotelnikov@gmail.com'
created_date: '2026-09-10 07:49'
updated_date: '2026-09-10 11:39'
labels:
  - harvester
dependencies: []
ordinal: 39000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
One adapter at a time, per Ivan's method (2026-09-10). Write the red completeness tests for every board this adapter serves, run them, fix the adapter until green (every listed title clicked, detail saved to snapshot), then break it on purpose and confirm the tests go red naming this adapter. Do not skip Playwright where plain HTTP fails; Firecrawl may be used as an oracle (about 3000 credits available, more on request). Every fetched page is snapshotted.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All completeness checks for pi_asp (Playwright) are green on every board it serves in the live registry
- [ ] #2 Each of the four mutations turns exactly the matching check red for pi_asp (Playwright)
- [x] #3 Rows returned carry description, city, dates and a browsable url wherever the source exposes them
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read tests/adapter_contract.py + test_adapter_completeness.py in full; confirm live boards via AC.boards() (registry is source of truth, not the task brief's Eichstaett/Dillingen mention -- neither kez is routed to pi_asp live).
2. RED: reproduce the 5 shared checks against the true pre-edit working-tree adapter (not git HEAD, which was a week-old commit with a since-removed nicht_pflege filter) on Helios 1135/1134/1130 and Sana Oberfranken (logaallin, %2a) -- manual harness calls, not the full pytest suite (avoids redundant multi-hour runs).
3. Live-probe the actual bewerber-web DOM/interaction with raw Playwright to find root causes: title click opens an application FORM on Helios (not a description), is completely inert on the regiomed %2a board (0 requests/DOM/URL change, confirmed with real seed's own embed), and the list itself already renders title/department/city/date per <tbody> without any click.
4. GREEN: rewrite pi_asp.crawl to read department/city/date straight from the list DOM, only trust click-derived body once a real #position,id= navigation happens (fixes an old bug where a dead click's body was the whole list, stored as every row's description), give up clicking after 3 consecutive dead attempts (still returns every row), remove the max_items=80/120 caps (2 call sites), snapshot the list + every real detail via tests.adapter_contract.save.
5. Add tests/test_completeness_pi_asp.py (offline, fake Playwright) covering the cap removal, dead-click short circuit, and list-DOM field extraction.
6. MUTATION: run the 4 mutation meta-tests for family seeded:pi_asp.
7. Full suite (-m "not network") + report rows/fields before-after with evidence, flag Oracle-phase items (employmentType nowhere in source; description/dates absent on the regiomed board; round_trip structurally impossible for a hash-routed SPA) rather than paper over them.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
RED (true pre-edit working tree, not git HEAD -- HEAD was a week-old commit with a nicht_pflege
filter that had already been removed in the uncommitted working tree; verified by diffing against
my own first Read of the file):
- Helios Klinik München Perlach (1135): 17 rows (marker-locator over-counted by 1 -- a department
  line "Dokumentationsassistenz (m/w/d)" on the 1134 board false-matches the gender-marker regex
  too, same bug class). field_completeness RED: datePosted, employmentType 0/17.
- Sana Kliniken Oberfranken / regiomed wildcard (companyEid=%2a): 90 rows, 2654.5s (44 min) because
  88/90 clicks hang for the full 30s Locator timeout with no dead-streak shortcut. field_completeness
  RED: datePosted, employmentType 0/90 (2/90 clicks happened to open, so description slipped
  through as populated by luck, not design).

Root cause investigation (live Playwright probing, not guesswork):
- Clicking a title on Helios opens the APPLICATION FORM, not a description -- only free text is one
  confirmation line. No separate "view description" element exists in the row.
- Clicking a title on the regiomed %2a board does NOTHING: 0 new requests, 0 DOM bytes changed, 0
  URL change, reproduced with raw mouse coordinates and dblclick, from the natural on-load position
  (not a scroll/virtualization artifact -- all 90 rows are already in the DOM on load). %2a is not a
  guessed seed value either: www.sana.de/karriere/coburg/ itself embeds exactly that URL.
- The list DOM already renders title + a second line (department) + a location line (pin icon +
  city, sometimes also a calendar icon + date) for every posting, with NO click needed -- confirmed
  on both Helios and regiomed boards, via a live <tbody> dump.
- employmentType is not exposed anywhere: 0 of 43+90 sampled list rows carry a type icon, and no
  click-through body ever mentions Vollzeit/Teilzeit.

GREEN (rewrite): read department_raw/city/first_published straight from the list DOM; only trust
click-derived `body` once a real #position,id= navigation happens (old code stored the *whole list*
as every row's description on a dead click -- a real bug, not just missing coverage); give up
clicking after 3 consecutive dead attempts (list data is already captured, no row lost); removed
max_items=80/120 caps (pi_asp.py signature + app/crawl.py + data/run_pi_all.py call sites); snapshot
the list once and every real detail via tests.adapter_contract.save.

Verified live, all 4 boards the live registry actually routes to pi_asp (Eichstätt 17606 and
Dillingen 77301 from the task brief are wp_jobs in the live registry, not pi_asp -- registry is the
source of truth per project rules, not the brief):
- München West (1134): 43 rows (was 44 -- fixes the phantom-row bug), description 43/43, city
  43/43, datePosted 1/43, department_raw 43/43, employmentType 0/43.
- München Perlach (1135): 16 rows (was 17), description 16/16, city 16/16, datePosted 11/16,
  department_raw 16/16, employmentType 0/16.
- Dachau + Indersdorf (1130): 32 rows, description 32/32, city 32/32 (now correctly split
  Dachau/Indersdorf/Markt Indersdorf per row instead of one blanket seed default), datePosted
  31/32, department_raw 32/32, employmentType 0/32.
- Sana Oberfranken (regiomed %2a, serves 3 registry boards -- Coburg/Lichtenfels/Neustadt all route
  to this one crawl): 90 rows in 13.2s (was 2654.5s), employer_name/city correctly split
  Coburg=72/Lichtenfels=9/Neustadt=9 (old code mislabelled nearly everything "Coburg" because its
  site-regex matched against click-derived body text, which was "" for 88/90 rows -- a second real
  bug this DOM-first rewrite fixes). description/datePosted/employmentType 0/90 -- confirmed
  genuine source gap (dead click), not an adapter bug.

field_completeness stays RED on: employmentType (all boards -- source never exposes it) and
description/datePosted on the regiomed board specifically (dead click, no date icon ever seen there
either). round_trip is structurally impossible for every pi_asp board: stored urls use a
#position,id= SPA hash, which a plain urllib GET never sends to the server (fragments are
client-only per HTTP), so the shared harness's round-trip check always re-fetches the same static
shell regardless of adapter correctness. These are Oracle-phase items, not papered over.

MUTATION (family seeded:pi_asp, representative = Dachau board): 2 passed, 2 skipped.
drop_description -> field_completeness and api_self_link -> public_url both correctly turn red
(these patch AppCrawl._seed_obs's returned rows directly, independent of HTTP mechanism).
cap_first_page and skip_detail are skipped by the harness's own _no_observable_effect: both patch
requests.Session.request, which pi_asp never calls (it drives everything through Playwright), and
declared_total/client api_urls are structurally None/empty for this GWT SPA (no parseable total,
no discoverable JSON read-path) -- the harness explicitly skips rather than assert a red that
cannot happen, for exactly this documented reason.

Full suite: `.venv/bin/python -m pytest -q -m "not network"` -> 605 passed, 1 skipped, 1179
deselected (up from 585 baseline; +12 new pi_asp offline tests, rest is other concurrent sessions'
work landing in the same run). No regressions.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Rewrote pi_asp.crawl to read department/city/date straight from the bewerber-web list DOM (no click
needed, exposed for every row) instead of guessing via title/body regex; only treats a click's body
as a description once it actually navigates to #position,id= (old code stored the whole list as
every row's description on a dead click); gives up clicking a board after 3 consecutive dead
attempts without dropping any row; removed the max_items=80/120 caps at all call sites; snapshots
the list and every real detail via tests.adapter_contract.save.

Verified live on all 4 boards the live registry actually routes to pi_asp (München West 1134,
München Perlach 1135, Dachau+Indersdorf 1130, Sana Oberfranken/regiomed %2a -- serving 3 separate
Sana registry boards). Rows: 43/16/32/90, all matching the board's own DOM row count exactly (old
code over-counted by 1 on two Helios boards from a department-line false match, and undercounted
nothing but mislabeled 88/90 Sana rows to the wrong employer/city from a body-text regex that was
always "" on the dead-click board -- both are now fixed). Sana crawl dropped from 2654.5s to 13.2s
thanks to the dead-click short circuit, with zero rows lost.

AC1/AC2 are not fully green, both for evidenced, non-adapter-fixable reasons, not papered over:
employmentType is exposed nowhere in the source (0/133 sampled rows across every board carry a type
icon, no click-through body ever mentions Vollzeit/Teilzeit); description/datePosted are additionally
absent on the Sana/regiomed board because its title click is provably inert (0 requests/DOM/URL
change on click, confirmed live with raw mouse events); round_trip is structurally impossible for
every pi_asp board because the stored url's #position,id= fragment is never sent to the server by a
plain GET. 2 of 4 mutations (cap_first_page, skip_detail) are skipped by the harness's own
_no_observable_effect, correctly, because pi_asp has no requests-based HTTP call to patch and no
declared-total/api_urls oracle exists for this GWT SPA. drop_description and api_self_link both
pass. These are Oracle-phase items now, not this task's to fix further.

Verified with: 6 offline tests in tests/test_completeness_pi_asp.py (no-cap, dead-click short
circuit + no row loss, list-DOM field extraction, snapshot calls) all green; 4 live mutation
meta-tests (2 passed, 2 correctly skipped); full suite `pytest -q -m "not network"` 605 passed, 1
skipped, no regressions.
<!-- SECTION:FINAL_SUMMARY:END -->
