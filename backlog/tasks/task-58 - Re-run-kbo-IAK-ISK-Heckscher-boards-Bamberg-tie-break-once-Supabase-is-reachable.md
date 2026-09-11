---
id: TASK-58
title: >-
  Re-run kbo IAK/ISK/Heckscher boards + Bamberg tie-break once Supabase is
  reachable
status: To Do
assignee: []
created_date: '2026-09-11 15:10'
updated_date: '2026-09-11 15:22'
labels: []
dependencies:
  - TASK-57
ordinal: 58000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Blocked handover from TASK-57: both fixes (kbo.de title-city extraction in crawlers/vendor_adapters.py's crawl_group_portal, and the real-capacity tie-break in pflege_jobs/registry.py's Matcher) are implemented, committed (14320c0), and verified against local fixtures + one live board (kbo-lmk.de: 15/36 matched, delivered) -- but Supabase (both supabase.int.exe.xyz proxy and the direct klkxfvieaxpjlplloljn.supabase.co host) started timing out around 2026-09-11 15:00 UTC and never recovered this session, blocking everything else.

Exact commands to run once confirmed reachable (quick check first: curl -sS -o /dev/null -w '%{http_code}\n' https://klkxfvieaxpjlplloljn.supabase.co/rest/v1/clinics?select=clinic_id&limit=1 -H "apikey: $SUPABASE_SECRET_KEY" -H "Authorization: Bearer $SUPABASE_SECRET_KEY" -H 'Accept-Profile: pflege_jobs' -- expect 200 fast, not a hang):

1. Deliver the 3 remaining kbo.de sub-boards the same way kbo-lmk.de already was (see this session's transcript for the exact script shape -- app.crawl._vendor_rows + pflege_jobs.sources.inbox.jobposting_to_obs + pflege_jobs.registry.Matcher + pflege_jobs.sinks.EdgeSink.write, board-scoped):
   - https://kbo-iak.de/kbo-karriere/stellenangebote-pflege (11 clinics)
   - https://kbo-isk.de/karriere (4 clinics)
   - https://kbo-heckscher-klinikum.de/arbeiten-bei-uns (9 clinics)
2. Re-run the Klinikum Bamberg board (careers_url https://www.sozialstiftung-bamberg.de/stellenangebote/, ats_type dvinci, clinics 46101/46103/46170/47403) and confirm the 5 previously-null Bruderwald postings (ids were 5906-5910 as of 2026-09-11, re-derive if stale) now resolve to 46101 via the new _realsite tie-break.
3. Re-verify TASK-51's earlier evidence (ukw.de, ebel-kliniken, AMEOS) is still correctly delivered -- those were written before the outage and should be fine, but worth a quick spot-check given how much registry/Matcher churn happened in one session.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All 3 remaining kbo.de sub-boards delivered live; matched counts reported
- [ ] #2 Bamberg board re-delivered; the 5 previously-null Bruderwald postings now show clinic_id=46101
- [ ] #3 Spot-check confirms TASK-51's earlier deliveries (ukw.de, ebel-kliniken, AMEOS) are still intact
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Also found + fixed (code only, needs delivery): kbo-dak.de (clinic 16107, Zentrum für psychische Gesundheit Ingolstadt) was never routed through the shared kbo.de group portal at all -- the old GROUP_PORTALS regex was anchored to the clinic's own NAME starting with 'kbo-', which this one doesn't. Widened in commit 8d0d7eb. Add this board to the re-delivery list once Supabase is back.

Also add: re-verify TASK-52's meinkrankenhaus2030.de/karriere/stellenboerse board (clinics 19001 Schongau + 19002 Weilheim) with description-aware Matcher.match() the same way as the other TASK-51 cases -- both currently-delivered postings resolved to 19001 only, unconfirmed whether that's correct for all of them or an org-name-defaulting artifact.
<!-- SECTION:NOTES:END -->
