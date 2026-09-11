---
id: TASK-38
title: 'Adapter red-green: umantis'
status: In Progress
assignee:
  - '@ivan.d.kotelnikov'
created_date: '2026-09-10 07:49'
updated_date: '2026-09-10 19:14'
labels:
  - harvester
dependencies: []
ordinal: 38000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
One adapter at a time, per Ivan's method (2026-09-10). Write the red completeness tests for every board this adapter serves, run them, fix the adapter until green (/Jobs/All + /Vacancies/<id>/Description detail fetch), then break it on purpose and confirm the tests go red naming this adapter. Do not skip Playwright where plain HTTP fails; Firecrawl may be used as an oracle (about 3000 credits available, more on request). Every fetched page is snapshotted.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All completeness checks for umantis are green on every board it serves in the live registry
- [ ] #2 Each of the four mutations turns exactly the matching check red for umantis
- [x] #3 Rows returned carry description, city, dates and a browsable url wherever the source exposes them
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read tests/adapter_contract.py + test_adapter_completeness.py in full; run RED for -k umantis.
2. Diagnose each red: (a) datePosted/employmentType 0/N -> career_crawl.py heuristic never read
   the source's own "Veroeffentlichung ab DATE"/Vollzeit text (fixed in a prior uncommitted
   session, verified live); (b) read-path-coverage missing bare /Jobs/1 on hub-hop boards (also
   already fixed in ats_seeds.py, verified live); (c) newly found: crawlers/routing.py plan()
   let a shared board's vendor flip between umantis/self_hosted/wp_jobs depending on registry row
   order when clinics sharing one careers_url disagree on ats_type (klinikverbund-allgaeu.de).
3. Fix (c): real fingerprint (umantis) always outranks a fallback label (self_hosted/wp_jobs) in
   plan(), regardless of iteration order. Regression test in tests/test_routing.py.
4. Add tests/test_completeness_umantis.py: offline regression tests for the date/employmentType
   heuristic (source exposes vs never exposes a date), the /Jobs/<n> pagination exclusion, the
   bare-Jobs/1 read-path seed, and the routing-table entry.
5. Re-run RED->GREEN for -k umantis + -m mutation -k umantis; run full -m "not network" suite.
6. For boards where datePosted stays unpopulated after (2)+(3): confirm with a live plain-HTTP
   fetch (and one Playwright render as a cross-check) that the template genuinely never states a
   publish date anywhere (only a job *start* date) -- document as a source limitation per board,
   not paper over it by repurposing the start date.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
RED (before): -k umantis -m completeness -> 4 failed / 21 passed. All 4 failures were
test_field_completeness on klinikverbund-allgaeu.de, anregiomed.de, recruitingapp-5610, and
karriere-vinzenz-klinik.de: "datePosted populated on 0/N rows" (employmentType/description/city
already green -- an uncommitted prior session had already fixed those via career_crawl.py's
Veroeffentlichung/Vollzeit heuristics and ats_seeds.py's bare-/Jobs/1 extra seed).

Found + fixed one new bug while diagnosing: crawlers/routing.py plan() let a shared board's vendor
flip between umantis/self_hosted/wp_jobs depending on registry row order whenever clinics sharing
one careers_url disagreed on ats_type (klinikverbund-allgaeu.de/karriere groups 3 umantis clinics
with 2 self_hosted ones) -- confirmed live across repeated fetches (wp_jobs one run, umantis the
next, with only 1 of 5 clinics attached). Fixed: a real fingerprint now always outranks a fallback
label (self_hosted/wp_jobs), independent of iteration order. Regression test added:
tests/test_routing.py::test_mixed_vendor_board_prefers_the_real_fingerprint.

Root-caused the remaining 4 field_completeness reds with live evidence (plain HTTP + one Playwright
render cross-check on St. Vinzenz, byte-identical DOM): these umantis tenants configure fully
custom per-posting HTML templates that never state a publish date anywhere -- only a job *start*
date ("zum 01.09.2027" / "ab 01.10.2026"), a different field. Confirmed live on all 4:
recruitingapp-5556 (klinikverbund-allgaeu), recruitingapp-5610 (Medic-Center Fuerth), recruitingapp-5580
(St. Vinzenz), recruitingapp-5511 (ANregiomed). Only recruitingapp-5545's template ("Veroeffentlichung
ab DATE") exposes a publish date and is fully green. This is a genuine source limitation, not an
adapter bug -- left first_published=None rather than repurposing the start date (would misrepresent
the source). Per Ivan's method this is a documented, evidence-backed board limitation for the Oracle
phase, not something plain HTTP/Playwright can produce.

GREEN after: -k umantis -m completeness -> 21 passed, 4 failed (same 4 field_completeness reds,
now for the confirmed reason above, not a bug). Rows per board: klinikverbund-allgaeu 10/10 desc+city+emp,
0/10 date; anregiomed 113 rows (101 real umantis Vacancy rows + 12 non-job WP navigation/news pages
picked up by the section-first hub walk -- see judgement call below), 0/113 date; recruitingapp-5545
14 rows, 11/14 date, 14/14 emp+desc+city; recruitingapp-5610 10/10 desc+city, 9/10 emp, 0/10 date;
karriere-vinzenz-klinik.de 17/17 desc+city, 16/17 emp, 0/17 date.

MUTATION (-m mutation -k umantis, representative = klinikverbund-allgaeu.de/karriere, biggest board):
drop_description -> field_completeness: red as expected. api_self_link -> public_url: red as
expected. cap_first_page and skip_detail: SKIPPED, not false-green -- verified live that this
representative's own page (a one-hop CMS hub) embeds no absolute umantis URL and states no
parseable job count, so the harness's own "no observable effect" guard correctly recognises
neither mutation has an oracle to react to on that specific page (client.api_urls=set(),
declared_total=None). Both target checks (declared_total_parity, read_path_coverage) are exercised
and pass on the live (non-mutated) run of this board and others (recruitingapp-5545, vinzenz) where
the client page does expose those signals.

Full suite: `.venv/bin/python -m pytest -q -m "not network"` -> 781 passed, 1 skipped (pre-existing,
unrelated), 0 failed.

Snapshots this session: recruitingapp-5545 (103 manifest lines), recruitingapp-5610 (84),
klinikverbund-allgaeu.de (149), anregiomed.de (134), karriere-vinzenz-klinik.de (103), under
crawl_snapshots/<host>/2026-09-10/.

JUDGEMENT CALLS / open items (not fixed, flagging for follow-up per scope discipline -- did not
expand scope without approval):
1. AC#1 ("green on every board") is not fully met: 4/5 boards fail field_completeness on datePosted
   for the confirmed source-limitation reason above. Recommend accepting these 4 as documented
   Oracle-phase candidates for datePosted specifically (every other field is green).
2. AC#2 ("each mutation turns exactly the matching check red") is 2/4 on the live representative;
   the other 2 are legitimate skips (see MUTATION above), not false passes -- the shared harness's
   fixed one-representative-per-family selection doesn't re-pick a board per mutation based on
   which client-side oracle signals it exposes.
3. Found but NOT fixed (out of TASK-38's owned files / scope, flagging for a follow-up task):
   anregiomed.de's section-first walk (career_crawl.Crawler, shared) includes ~12 non-job WP
   navigation/news pages (JOB_HREF's bare "/stellenangebot" substring match also fires on sibling
   paths like ".../stellenangebote-bewerbung/bewerbungsprozess/") alongside the 101 real umantis
   Vacancy rows. Doesn't fail any of the 5 automated checks on this board today (round-trip only
   samples titles/URLs that do resolve live; declared_total is unparseable on that hub page) but is
   a real data-quality gap worth a dedicated task since JOB_HREF is shared across several adapter
   families, not umantis-only.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
umantis is green on the fields it can control (description/city everywhere; employmentType on
4/5 boards; datePosted on the 1/5 board whose template states it) and 2/4 mutations verified red
live; the other 2 mutations legitimately skip (no observable oracle on the representative board,
not a false pass). Fixed a real routing bug found along the way: crawlers/routing.py's plan() no
longer lets a shared board's vendor flip between umantis/self_hosted/wp_jobs by registry row
order. datePosted stays unpopulated on 4/5 boards for a confirmed, evidence-backed source
limitation (their templates never state a publish date, only a job start date) -- verified via
plain HTTP and one Playwright DOM cross-check; not papered over. Left AC#1/#2 unchecked pending
Ivan's call on whether to accept those 4 boards' datePosted gap for the Oracle phase. Full
non-network suite green (781 passed, 1 pre-existing skip, 0 failed).
<!-- SECTION:FINAL_SUMMARY:END -->
