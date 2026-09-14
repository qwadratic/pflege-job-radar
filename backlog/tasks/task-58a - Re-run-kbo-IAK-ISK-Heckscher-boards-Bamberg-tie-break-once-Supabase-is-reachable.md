---
id: TASK-58a
title: >-
  Re-run kbo IAK/ISK/Heckscher boards + Bamberg tie-break once Supabase is
  reachable
status: Done
assignee: []
created_date: '2026-09-11 15:10'
updated_date: '2026-09-14 14:59'
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
- [x] #1 All 3 remaining kbo.de sub-boards delivered live; matched counts reported
- [x] #2 Bamberg board re-delivered; the 5 previously-null Bruderwald postings now show clinic_id=46101
- [x] #3 Spot-check confirms TASK-51's earlier deliveries (ukw.de, ebel-kliniken, AMEOS) are still intact
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Also found + fixed (code only, needs delivery): kbo-dak.de (clinic 16107, Zentrum für psychische Gesundheit Ingolstadt) was never routed through the shared kbo.de group portal at all -- the old GROUP_PORTALS regex was anchored to the clinic's own NAME starting with 'kbo-', which this one doesn't. Widened in commit 8d0d7eb. Add this board to the re-delivery list once Supabase is back.

Also add: re-verify TASK-52's meinkrankenhaus2030.de/karriere/stellenboerse board (clinics 19001 Schongau + 19002 Weilheim) with description-aware Matcher.match() the same way as the other TASK-51 cases -- both currently-delivered postings resolved to 19001 only, unconfirmed whether that's correct for all of them or an org-name-defaulting artifact.

Also add: re-deliver klinikverbund-allgaeu.de's umantis board (TASK-56, commit 0ee9828) -- verified live raw 9->102 rows, never delivered to prod due to the same outage.

2026-09-11 (TASK-49 continued): 3 more registry careers_url fixes queued for delivery once
Supabase is reachable (no code change needed, verified live via direct crawl_wp_jobs(c) calls):
- www.waldkrankenhaus.de (290 beds) -> https://jobs.malteser.de (real board, confirmed real
  nursing postings there).
- www.kreiskrankenhaus-hoechstadt.de (80 beds) -> https://www.team-anna.de/stellenboerse/
  (confirmed 12 real rows, several Pflegefachkraft postings).
- www.st-irmingard.de (75 beds) -> https://karriere.gesundheitswelt.de/stellenangebote.html
  (rexx-systems shared board, "Gesundheitswelt Chiemgau" group; confirmed 50 rows, 8 tagged
  "Prien am Chiemsee" incl. a real Pflegefachkraft/Altenpfleger nursing posting).
Also 3 code fixes committed+pushed (165b1eb) that only need their affected clinics' postings
delivered once DB is back: clinic-dr-decker.de, klinik-am-birkenwald.de,
fachklinikum-mainschleife.de (generic href-or-text OR fix), plus klinik-menterschwaige.de,
klinik-bad-trissl.de, klinik-wirsberg.de (bespoke extractors). See TASK-49 final summary for
full detail.

2026-09-11/12/14: Delivered once Supabase recovered.

TASK-49's 3 registry-url fixes + 6 code-fixed clinics (Batch A, 7 clinics total) delivered via the
real production path (app.crawl._vendor_rows -> _post_inbox -> pflege_jobs.cli inbox): 125 inbox
rows drained, 37 kept nursing observations, 41 open postings now live across all 7 clinics
(Decker 4, Menterschwaige 2, St. Irmingard 9, Waldkrankenhaus 17, Birkenwald 2, Höchstadt 6,
Mainschleife 1). Caught and fixed a self-inflicted over-scope bug mid-delivery: Waldkrankenhaus's
registry careers_url was set to the bare https://jobs.malteser.de domain root instead of its own
scoped listing page, so crawl_wp_jobs walked Malteser's entire nationwide sitemap (1185 rows, all
of Germany, all business lines). Per explicit direction: kept the rows, marked all 1185 ignored via
the ingest function's inbox_ack op (processed_at+note, non-destructive), fixed careers_url to the
real scoped page (https://www.waldkrankenhaus.de/karriere/unsere-stellenangebote.html, 33 real
rows), and reopened (cleared processed_at) the 31 of those 33 that had been collateral-acked as
part of the bad batch before redelivering correctly.

kbo group (AC#1): delivered via crawl_group_portal (all 4 sub-boards -- IAK/ISK/Heckscher/DAK --
plus LMK route through the one shared kbo.de board automatically via group_portal_for()'s widened
match). 111 raw rows fetched, 1 genuinely new (rest already delivered in an earlier pass this
session); 13 open postings live across the 30-clinic group, correctly conservative on the ~77 rows
that default to München HQ address with no extractable per-posting city (left unmatched rather than
guessed, per Matcher's own design).

Bamberg (AC#2): re-delivered the dvinci board (129 raw rows, 7 new). Confirmed live: postings
5906-5910 (the specific ids named in this task) all now resolve to clinic_id=46101 via
R3_tokens_full -- the token-subset match alone disambiguated Bruderwald from its near-duplicate
registry twins (46103 Michelsberg, 46170 Vertrags-KH), without even needing decision-4's
_realsite tie-break fallback. 38 total open Bamberg-group postings, 37 at 46101, 1 at 47403
(Forchheim) -- zero at either near-duplicate/wrong-operator candidate.

Klinikverbund Allgäu (mentioned in notes, not in original ACs but delivered as part of this task's
scope): delivered via the umantis seeded path (_seed_obs + _load_observations). Found and fixed TWO
real bugs while verifying attribution, not just delivering rows -- see TASK-59a-adjacent work,
committed separately (631285e, 1c5e5c3):
1. career_crawl.py's _base() defaulted employer_name to the seed clinic's own registered name
   whenever the page carried no real org field -- every OTHER real site on this shared hub
   (Kempten/Mindelheim/Ottobeuren/Oberstdorf/Sonthofen) was silently misattributed to Immenstadt
   (the seed) despite each row's own city being correctly extracted. Fixed by threading the seed
   clinic's registry `operator` string through so Matcher's R2_operator_town resolves by real city
   instead. Corrected result: 76301 (Kempten) 10, 77801 (Mindelheim) 2, 78002 (Oberstdorf) 1, 78001
   (Immenstadt, genuinely) 24.
2. The SAME seed's `_kez` fallback still defaulted unmatched rows to the seed clinic even after fix
   #1 -- one Memmingen posting (no registered clinic in this group at all) landed on Immenstadt via
   rule "seed_kez". Fixed by suppressing that default specifically when seed.get("operator") is set
   (a genuinely multi-site hub), so a content-match failure correctly stays unmatched instead of
   guessing. The one already-wrong live posting (12325) was corrected (clinic_id nulled) directly.

Weilheim/Schongau (mentioned in notes): re-checked live, already correct -- 2 real nursing postings
(Gesundheits- und Krankenpfleger/OTA roles), both genuinely city="Weilheim" (independently extracted
per posting, not a seed default) and both correctly clinic_id=19002. No further action needed; the
original TASK-52 worry (both postings landing on Schongau) does not reflect current live state.

TASK-51 spot-check (AC#3): ukw.de intact (47 postings, 0 unmatched). jobs.ebel-kliniken.com's 0/9
unmatched is CONFIRMED CORRECT BY DESIGN per TASK-51's own notes (all 9 are real postings for
other Ebel-group sites outside Bavaria) -- not a regression. karriere.ameos.eu's spot-check found a
real live bug (4 non-Bavaria postings force-matched via R0_board_name/tokens ignoring city) --
corrected live and structurally fixed via TASK-59a (city cross-check in Matcher._match_board(),
now Done), with the escalation-order design formalized in decision-5.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Every item in this task's handover, plus what it grew into while delivering, is now live and
verified: kbo group (13 open postings, 30 clinics), Bamberg (postings 5906-5910 confirmed at
clinic 46101, 38 total open), Klinikverbund Allgäu (delivered + two real employer/city-defaulting
bugs found and fixed at the crawler-seed layer), Weilheim/Schongau (already correct, no action
needed), and TASK-49's queued Batch A (7 clinics, 41 open postings, including a self-caught
over-scope incident on Waldkrankenhaus that was corrected without discarding any data). TASK-51's
spot-check (AC#3) found one more real bug in AMEOS's board matching, fixed and closed as TASK-59a
with its own decision record (decision-5) rather than folded into this task. Code changes across
7 commits (631285e, 1c5e5c3, 7023b37, de3475e, 8d0d7eb/165b1eb from earlier, plus this task's own),
all pushed; full regression suite green throughout.
<!-- SECTION:FINAL_SUMMARY:END -->
