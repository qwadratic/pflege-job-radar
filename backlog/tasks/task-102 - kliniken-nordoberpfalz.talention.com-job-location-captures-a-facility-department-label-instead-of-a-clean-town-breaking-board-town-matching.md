---
id: TASK-102
title: >-
  kliniken-nordoberpfalz.talention.com: job location captures a
  facility/department label instead of a clean town, breaking board-town
  matching
status: Done
assignee: []
created_date: '2026-09-22 16:11'
updated_date: '2026-09-22 20:11'
labels: []
dependencies: []
references:
  - crawlers/vendor_adapters.py
  - TASK-57
ordinal: 102000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Same 2026-09-22 unmatched-inbox review as TASK-99/100/101. All 5 unmatched data/inbox.sqlite rows for kliniken-nordoberpfalz.talention.com already have the correct 3-clinic board pool (36301 Klinikum Weiden, 37701 Krankenhaus Tirschenreuth, 37703 Krankenhaus Kemnath, all sharing this one careers_url) -- the failure is upstream, in what gets written to loc[0].city. Raw payload city values seen: 'Klinikum Weiden' (a clinic NAME, not a town), 'Kinderklinik am Klinikum Weiden', 'Klinikum Weiden Zentrale Notaufnahme' (both department/site labels), and 'Neustadt a. d. Waldnaab, Bayern, Deutschland' (an uncleaned comma-separated string). None of the three 'Klinikum Weiden'-prefixed strings town_match()-equal or prefix-match registry town 'Weiden' (city_key('Klinikum Weiden') starts with 'klinikum', not 'weiden'), so even though the correct 3-clinic pool is right there, R0_board_town has no agreeing city to select on. ats_type for this board is 'talention', which crawlers/routing.py's ADAPTERS map (line 51) maps directly to crawl_wp_jobs -- so this vendor's generic wp_jobs parser (parse_job_page) is what produces a facility/department label as the city on Talention-hosted boards rather than a plain town, similar in shape to the kbo.de Einsatzort-block problem TASK-57 already solved for that vendor.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Root cause found: which parser and which HTML/JSON field on this vendor's page produces a facility/department label as the city
- [x] #2 City extraction returns a clean, registry-comparable town for this board's postings (e.g. 'Weiden' rather than 'Klinikum Weiden Zentrale Notaufnahme')
- [x] #3 At least the 4 Weiden-area postings resolve to clinic 36301 after the fix; the 5th ('Neustadt a. d. Waldnaab') is checked separately for whether that town has any registry clinic at all before expecting it to match
- [x] #4 Red-green test against the live board plus a mutation test
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Root cause confirmed live (fetched a talention detail page's own JSON-LD): jobLocation.address.addressLocality is the SAME field for every posting, but the employer fills it inconsistently -- 'Weiden, Bayern, Deutschland' on one job, 'Klinikum Weiden Zentrale Notaufnahme' on another, same board, same page structure. No cleaner alternate field exists on the page. Fix is pool-scoped and vendor-gated (ats_type=='talention' only): crawlers/vendor_adapters.py clean_talention_city() extracts the one board-pool town named as a whole word in the raw string; 0 or 2+ matches (ambiguous, e.g. 'Krankenhaus Tirschenreuth und Klinikum Weiden' names two, or a town outside this board's pool like Erbendorf/Neustadt a.d. Waldnaab which have no registry clinic at all, confirmed live) leave the raw string untouched -- no match beats a wrong match. Wired into app/crawl.py _vendor_rows() right after board_clinic_ids.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed + verified live and in production. Replay against real stored payloads (exact jobposting_to_obs + Matcher.match production call): baseline 7/32 resolved, with fix 27/32, 0 regressions among the 7 already-resolved. Tests: tests/test_vendor_adapters.py (pure-function unit tests incl. a 'Weidenberg' false-positive guard) + tests/test_vendor_account_pools.py (integration via _vendor_rows), mutation-tested (reverted, confirmed red, restored). Applied retroactively via tools/task102_backfill_talention_city.py to the real stuck rows (payload.loc[].city was already-stored garbage from the original crawl, the code fix alone only helps future crawls) -- confirmed live in Postgres v_postings: 13 of 14 kliniken-nordoberpfalz.talention.com postings now carry clinic_id=36301 (was 6 before), 1 correctly still null (the ambiguous two-town posting).
<!-- SECTION:FINAL_SUMMARY:END -->
