---
id: TASK-145
title: >-
  Employer is a more abstract entity than a Krankenhausplan row -- pull in
  non-PDF employers (Reha, Gesundheitszentren) that share a group's board
status: To Do
assignee: []
created_date: '2026-09-23 23:09'
updated_date: '2026-09-25 00:10'
labels:
  - matching
  - crawler-coverage
dependencies: []
ordinal: 145000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Discovered while fixing TASK-128/99's Gesundheitswelt Chiemgau AG board: the shared rexx portal (karriere.gesundheitswelt.de) lists 8 legal entities behind filter[client_id][]. Only 2 (Simssee Klinik, Klinik St. Irmingard) are Krankenhausplan-registered hospitals in our registry; the other 6 are real employers under the SAME AG operator -- at least two Reha/Gesundheitszentrum GmbHs, plus a thermal spa/wellness resort -- currently completely out of scope, their nursing vacancies never entering the pool at all. Ivan's insight (2026-09-23): the Krankenhausplan PDF is only an ENTRY POINT for discovering operators/groups, not the source of truth for which employers exist or which are worth tracking. A 'group' in this codebase (GROUP_PORTALS, VENDOR_ACCOUNT_POOLS) has so far always meant 'PDF-registered sister hospitals sharing one board' -- this is the first confirmed case of a group whose OWN board also serves non-PDF employers with real, matchable nursing vacancies (Pflegefachkraft roles appear in Reha/Gesundheitszentrum job titles same as in hospitals). Ivan wants the model widened: once an operator/group is identified (via a PDF hospital, or otherwise), every entity behind its board should be considered a candidate employer -- resolve the full set of employers first, then match/dedup/classify vacancies against that set, rather than only ever discovering employers that happen to have their own PDF row. Deliberately NOT scoped for full implementation yet -- Ivan wants to first understand how much this is actually worth before building a general mechanism (how many other groups have this shape, how many net-new nursing postings a broader employer model would actually surface). This task exists to hold the idea and its most concrete, cheapest-to-validate acceptance criterion until that's decided.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The Gesundheitswelt Chiemgau AG's non-hospital entities on karriere.gesundheitswelt.de (at minimum the Reha/Gesundheitszentrum GmbHs) get their own employer records and their certified-nursing vacancies enter the same postings pool as hospital vacancies, matched/deduped the same way
- [ ] #2 A written decision on how an entity like this becomes a tracked employer without a Krankenhausplan row: manual curation per discovered group (same choice TASK-99 made for VENDOR_ACCOUNT_POOLS) vs. some other criterion, with the tradeoff stated
- [ ] #3 A survey of how many other already-known group boards (GROUP_PORTALS, VENDOR_ACCOUNT_POOLS entries) expose non-PDF sibling entities the same way, to size whether this is a one-off or a real pattern worth general tooling
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-24: two concrete leftover posting_ids from this same board, found while closing TASK-99's own
residual note, kept here so they aren't lost:

- posting_id 12315 (Bad Endorf, "Medizinischen Fachangestellten... Zentralen Funktionsdienst",
  employer="Gesundheitswelt Chiemgau", source karriere.gesundheitswelt.de) -- was stuck with
  clinic_id=null (a stale pre-rexx-fix row, no board pool ever attached to it). FIXED 2026-09-24: city
  Bad Endorf uniquely identifies Simssee Klinik (18713), Matcher.match(..., board=["18713"]) ->
  R0_board/0.9, pushed via clinic_links, confirmed live. Not this task's own AC -- a single-clinic
  case, no non-PDF employer involved.

- posting_id 13156 (Seeon-Seebruck, "Pflegehilfskraft/Altenpflegehelfer/Pflegefachassistent",
  employer="Gesundheitswelt Chiemgau") -- STILL clinic_id=null, and correctly so today: Seeon-Seebruck
  is "Klinik ChiemseeWinkel Seebruck GmbH" (client_id 8 on the board's own filter[client_id][]
  dropdown), one of the AG's non-hospital/non-PDF entities this task's AC#1 is about. This is the
  concrete example row AC#1 should resolve once an employer record + matching exists for it -- a real,
  currently-dropped certified-nursing-adjacent posting (Pflegehilfskraft/Pflegefachassistent titles),
  sitting on the exact same board already fixed for Simssee/St. Irmingard.
<!-- SECTION:NOTES:END -->
