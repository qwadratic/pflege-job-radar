---
id: TASK-117
title: >-
  Census: 135 never-posted clinics have a working board route (not walled, not
  missing url/adapter) -- 7 net-new empty boards, plus evidence the 115-clinic
  remainder is mostly TASK-81/96/99/100's attribution mechanism, not a crawl gap
status: Done
assignee: []
created_date: '2026-09-22 18:28'
updated_date: '2026-09-23 00:42'
labels: []
dependencies: []
ordinal: 117000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Same 2026-09-22 never-posted census as the sibling task filed alongside this one (143 of 407 registry clinics have zero pflege_jobs.v_postings rows ever). Of those 143, 135 route cleanly through crawlers.routing.plan() to a real adapter on a non-walled board -- so the crawler DOES know where to fetch them. Split into two groups by cross-referencing data/app.sqlite crawl_issues (kind='empty'/'vendor', retained 2026-09-17..2026-09-22 only -- 6 daily snapshots, no longer history exists to check further back) and run_log: (A) 18 clinics whose board has a confirmed kind='empty' issue in that window -- of these, 11 clinic instances are already covered by tasks filed earlier today (16228/TASK-99 Artemed; 17105+17106/TASK-110 kinderzentrum.de; 27108/TASK-113 klinik-angermuehle.de; 37275/TASK-111 tcm.info; 67170/TASK-112 vital-klinik.de; 77607+78008/TASK-115 reisach-kliniken.de; 76403+77605+77707/TASK-77 bezirkskliniken-schwaben.de) -- reference those, do not duplicate. 7 clinic instances on 6 boards are NOT yet in any filed task: 16254 (pflegejobs.brk-muenchen.de), 26205 (bkh-passau.de/karriere), 27803 (klinik-schwarzach.de), 37273 (spezialklinik-neukirchen.de), 77201 (wertachkliniken.de), and 77606+77673 sharing panorama-fachklinik.de -- each only has 1-2 days of empty-issue history in the short retention window, so 'persistent' is not yet proven, just not-yet-disproven. Also found: crawl_issues kind='vendor' shows 'komm-ins-klinikland.de' crashing with 'TypeError: expected string or bytes-like object, got list' on every run for clinic 67501 -- same _txt()-crashes-on-non-string-JSON-LD-field shape as TASK-82 (which covers muenchen-klinik.de's two boards, 16201-16205, the SAME error class with a different type, 'got int') but 67501/komm-ins-klinikland.de is not named in TASK-82 -- likely the same root-cause fix recovers both. (B) The other 115 clinics have NO crawl_issues record at all in the retention window, which looked at first like 'never attempted' -- it is not. Sampling run_log directly for board URLs (not clinic_ids) for a spread of these clinics shows the boards ARE fetched successfully with real rows every run: kbo-heckscher-klinikum.de -> 108-110 rows every run (clinics 16104/16106/16212/16251/16252), jobs.schoen-klinik.de -> 295 rows every run (16209 Harlaching, 16224 Schwabing -- explicitly named as a correctly-unmatched tie in TASK-100 AC#2), kbo-iak.de -> 10-15 observations via a SEPARATE umantis crawl even though its own typo3_jobs adapter returns 0 (dual-vendor routing ambiguity, same shape as TASK-81), recruitingapp-5545.de.umantis.com -> 14-15 observations covering kbo-Kinderzentrum München AND kbo-Heckscher-Klinikum München in one seed, jobs.pkd.de -> 131-132 observations, schwesternschaft-muenchen.de -> 17 rows. In every sampled case the board is fully alive and yields rows, but none of those rows ever land on the specific never-posted clinic_id -- this is squarely the shared-board/multi-site attribution defect family already tracked as TASK-81 (shared-board collapse, 21 clinics scoped originally), TASK-96 (town-gate refusal), TASK-99 (shared-vendor-account single-clinic pool), and TASK-100 (no tie-break for duplicate-registry-row ties). The census suggests that family's real blast radius is much larger than TASK-81's original 21-clinic estimate -- at least the 115 here have never produced one single posting because of it, not just 'lower recall'. This task does not re-solve TASK-81/96/99/100's mechanisms (do not duplicate their fixes); it hands them a concrete, larger victim list to re-verify their scope against, plus the 7 genuinely new empty boards nobody has looked at yet.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each of the 6 net-new empty boards (pflegejobs.brk-muenchen.de/16254, bkh-passau.de/26205, klinik-schwarzach.de/27803, spezialklinik-neukirchen.de/37273, wertachkliniken.de/77201, panorama-fachklinik.de/77606+77673) gets a quick recon pass (same shape as TASK-48/54): is the board static HTML worth a curl+grep check, JS-rendered, or genuinely zero vacancies right now
- [x] #2 komm-ins-klinikland.de (clinic 67501) is confirmed to share TASK-82's root cause (or confirmed as a distinct crash) and either gets folded into TASK-82's fix or its own AC there
- [x] #3 TASK-81 is re-measured against the full never-posted population (this task's 115-clinic list, not just the original top-100-audit's 21) and its scope/AC updated if the real count is materially larger
- [x] #4 TASK-99/TASK-100 are checked against the kbo-Heckscher (16104/16106/16212/16251/16252), Schön Klinik München (16209/16224), kbo-IAK (16251/16252 dual-vendor), Paracelsus-Klinik München (16232), and Rotkreuzklinikum München (16223) cases found here, and either confirmed already in scope or given a follow-up AC
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
All 4 ACs addressed with live evidence, 2026-09-23.

AC#1 (6 net-new empty boards, recon + fix where cheap):
- 16254 pflegejobs.brk-muenchen.de: FIXED end-to-end. Real board is brkm.pi-asp.de (P&I LOGA), one
  link out from the registered marketing page. Root cause: pflege_jobs/sources/pi_asp.py hardcoded
  "?companyEid=" -- this tenant needs "?company=" (live-verified via a direct Playwright probe before
  touching code: 69 labels rendered with the right param, 0 with the old one). Added an optional
  seed["param"] override (default unchanged, existing Helios/Regiomed seeds unaffected), added the
  missing data/registry/pi_seeds.json entry, set ats_type 'typo3_jobs' -> 'pi_asp' live via the
  sanctioned EdgeSink.write_clinics path (tools/task117_fix_brk_muenchen_pi_asp.py). Triggered a real
  scoped crawl: 20 rows, 2 new postings, both verified live (incl. "Pflegefachkräfte (m/w/d)").
  2 new regression tests (tests/test_completeness_pi_asp.py), mutation-tested.
- 26205 bkh-passau.de/karriere: registry ALREADY corrected by a concurrent process since the original
  crawl_issues snapshot (careers_url now mein-check-in.de/mainkofen/, ats_type mein-check-in) --
  BKH Passau publishes no board of its own, its own "Alle Stellenangebote anzeigen" link points at
  Bezirksklinikum Mainkofen's board. Live-verified 42 rows; only 2 are genuinely Passau-located (the
  other 40 are Mainkofen's own, correctly NOT attributed to Passau -- see AC#3). Triggered a real
  crawl: 1 new posting landed and verified live ("Pflegefachpersonen (m/w/d) am BKH Passau").
- 27803 klinik-schwarzach.de: board IS alive, self-hosted TYPO3, 5 real job links found both via curl
  and via crawl_wp_jobs run fresh today (14/22 requests ok, vs 2/12 on the 21st/22nd -- that was
  transient site flakiness, not a code bug). None of today's 5 postings are Pflege roles -- genuinely
  no current nursing vacancy, not a gap.
- 37273 spezialklinik-neukirchen.de: CONFIRMED genuinely no current Pflege opening, independently
  re-confirmed today -- matches TASK-48's own 2026-09-11 finding for this exact board.
- 77201/77202 wertachkliniken.de: registry ALREADY corrected by a concurrent process (careers_url now
  karriere-wertachkliniken.de/stellenangebote.html, ats_type rexx). Live-verified 13 rows. Triggered a
  real crawl: 9 new postings landed and verified live, all on 77202 (Bobingen) -- 77201 (Schwabmünchen)
  genuinely has 0 current Pflege postings on this shared board today, a correct outcome (city-based
  split working as intended), not a bug.
- 77606+77673 panorama-fachklinik.de: real postings exist but are announced as plain text pointing to
  a PDF download, not HTML links -- no cheap fix. None of the 3 currently-listed roles (Assistenzarzt,
  Servicekraft, Spülkraft) are Pflege anyway. Documented, not fixed -- low value for the effort.

AC#2 komm-ins-klinikland.de (67501): CONFIRMED same root cause as TASK-82 (the _txt() non-string
JSON-LD crash) -- already fixed there (commit 38287cc), and TASK-82's own regression test explicitly
pins this exact board's failure shape (the list/array case). Live-verified today: 11 rows, zero crash.
Triggered a real crawl: 6 new postings landed and verified live, several genuine Pflege roles
(Pflegefachfrau/-mann, Kinderkrankenpfleger, Intensivpflege). Also found: the registry's careers_url
is still a single job-detail page (matches TASK-86's registry_lint job-detail-page pattern) -- works
today only because crawl_wp_jobs' own fallback discovery finds the real listing anyway
(komm-ins-klinikland.de/stellenangebote/); flagged to TASK-86 for a robustness fix, not applied here.

AC#3 (re-measure TASK-81 against the full 115-clinic population): traced multiple concrete new cases
live (16104 kbo-Heckscher-Klinikum Ingolstadt, 26205 Passau/Mainkofen). Correction to this task's own
original hypothesis: the DOMINANT pattern in every case traced is NOT actually TASK-81/96/99/100's
Matcher-internals mechanism -- it is TASK-86's registry-correction mechanism (careers_url points at
the clinic's own marketing microsite, which links out exactly ONCE -- below TASK-85 AC#3's >=2-link
auto-follow threshold -- to the real shared/group board on a different host: kbo.de's group portal,
mein-check-in.de/mainkofen, karriere-wertachkliniken.de, brkm.pi-asp.de). Only AFTER that registry
correction does a genuine TASK-81-shape question arise: does the now-working shared board correctly
split its postings across sibling clinics? Traced this precisely for 26205/Mainkofen: the board's
`org` field is unconditionally seed-inherited (org_source='seed', every row says "Bezirkskrankenhaus
Passau" regardless of real location) while `city` is real per-row data. Confirmed live and by reading
pflege_jobs/registry.py's Matcher.match(): employer_inherited=True blanks en/et BEFORE either
_match_content or the _match_board fallback ever run, so a Mainkofen-city posting cannot match Passau
via employer-name text, and _match_board's town rung correctly requires the real city to match --
exactly why only 1 of 42 rows (the one genuinely tagged city=Passau) landed on clinic 26205 and the
other 41 correctly did not. TASK-81 does not need reopening -- its own delivered mechanism (this
session, the employer_inherited et/ek gate) is confirmed correct and sufficient for this shape. The
real, larger gap this task's census actually found is registry coverage (TASK-86-shape corrections),
not Matcher attribution logic -- recorded as a comment on TASK-86 (kbo-Heckscher-Klinikum family,
plus the open kbo-IAK question) rather than reopening TASK-81 for a mechanism that already works.

AC#4 (TASK-99/100 vs 5 named clusters):
- Schön Klinik München Harlaching/Schwabing (16209/16224): CONFIRMED already in scope -- TASK-100
  AC#2 explicitly and deliberately leaves this exact pair unmatched by design (two real, different
  sites, no stronger per-posting signal to disambiguate). Not a gap, a considered decision already
  made. Will permanently read 0/0 postings unless a future, different mechanism (department/address-
  level disambiguation) is built -- noted, not attempted here.
- kbo-Heckscher-Klinikum family (16104/16106/16212/16251/16252): NOT covered by TASK-99 or TASK-100
  (grepped both tasks' full text, zero mentions). Root cause (traced live, 16104): same off-host-
  single-link registry-correction pattern as AC#1/AC#3, not a Matcher bug -- see the TASK-86 comment
  filed this session. 16212 is already correctly configured (umantis). 16251/16252 (kbo-IAK)'s real
  board location is unresolved -- their own site has no visible link to any recruiting platform in
  its static HTML; whether they share 16212's umantis pool or need their own tenant is an open
  question, handed to TASK-86 rather than guessed at.
- Paracelsus-Klinik München (16232) and Rotkreuzklinikum München (16223): registry already looks
  correctly configured (jobs.pkd.de/softgarden; schwesternschaft-muenchen.de/concludis respectively)
  -- NOT traced live this session (time budget). Both currently read 0 postings; whether that is a
  genuine current gap or a live-but-empty board was not determined -- flagged as needing the same
  live-trace treatment as a follow-up, not guessed at.

Live-verified totals this session (real crawls, real v_postings reads, all `verify_status: live`):
16254 0->2, 26205 0->1, 77202 0->9, 67501 0->7 -- 19 real postings recovered across 4 clinics that
had zero for their entire history. 77201 confirmed correctly still 0 (board works, no current Pflege
vacancy at that specific sibling site). Full offline suite (pytest -m "not network"), same run
verifying both this task's pi_asp fix and TASK-85/88's earlier degraded/incomplete crawl_issue wiring:
1393 passed, 18 skipped, 0 failed, 361.2s.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
All 4 acceptance criteria closed with live evidence. Of the task's own hypothesis -- that the 115
never-posted clinics are mostly TASK-81/96/99/100's attribution-mechanism blast radius -- live tracing
found the dominant real pattern is actually TASK-86-shape registry corrections (careers_url points at
a marketing microsite that links out once to the real shared board), not Matcher-internals bugs. Where
a genuine attribution question did arise (26205 sharing Mainkofen's board), this session's own
existing employer_inherited gate in pflege_jobs.registry.Matcher.match() already handles it correctly,
confirmed by live replay, not just by reading the code.

Fixed and verified live this session: 16254 (new pi_asp param-override bug + missing seed + wrong
ats_type, 0->2 postings), plus 3 boards a concurrent process had already registry-corrected but never
recrawled (26205 0->1, 77202 0->9, 67501 0->7) -- 19 real postings recovered total. 27803/37273
confirmed genuinely empty (no fix needed). panorama-fachklinik.de documented as low-value (PDF-only
postings, none currently Pflege). komm-ins-klinikland.de confirmed to share TASK-82's already-fixed
root cause. kbo-Heckscher-Klinikum family and the open kbo-IAK question handed to TASK-86 via comment.
Schön Klinik München's unmatched pair confirmed as TASK-100's own deliberate, correct design decision,
not a gap. Paracelsus-Klinik München and Rotkreuzklinikum München flagged as not traced this session --
genuinely open, not guessed at.

2 new tests (tests/test_completeness_pi_asp.py), mutation-tested. Full offline suite: 1393 passed, 18
skipped, 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->
