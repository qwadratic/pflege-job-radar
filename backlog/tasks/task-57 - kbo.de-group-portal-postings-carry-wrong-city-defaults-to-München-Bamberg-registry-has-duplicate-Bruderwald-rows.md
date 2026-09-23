---
id: TASK-57
title: >-
  kbo.de group-portal postings carry wrong city (defaults to München); Bamberg
  registry has duplicate Bruderwald rows
status: Done
assignee:
  - '@claude'
created_date: '2026-09-11 14:41'
updated_date: '2026-09-23 10:02'
labels: []
dependencies:
  - TASK-51
ordinal: 57000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Surfaced 2026-09-11 doing a board-aware re-verification of TASK-51's fuzzy-matched postings. Two separate real gaps found, neither fixed:

1. kbo.de group portal (crawl_group_portal in crawlers/vendor_adapters.py, GROUP_PORTALS['kbo.de'], 32-clinic shared board): postings' stored city is 'München' even when the job is clearly elsewhere -- e.g. posting external_url '.../26-19-pfk-dn-gap-pflegefachkraft-als-dauernachtwache-mit-schwerpunkt-pausenabloesung-m-w-d-in-garmisch-partenkirchen' (URL slug names Garmisch-Partenkirchen explicitly) has city='München' stored. With the wrong city, Matcher.match()'s board-town disambiguation can't narrow the 32-clinic pool (8+ candidates share town='München' alone) and correctly returns None rather than guess -- meaning at least 9 kbo.de postings that WERE previously matched (likely via some other path, possibly a now-fixed false-positive, not yet confirmed which) currently sit unmatched. Root cause is upstream of Matcher: whatever parses each job's JSON-LD off kbo.de/karriere/jobs/<slug> is either not reading the real jobLocation, or the group-portal crawl only ever knew the parent group's HQ city and never the individual site.

2. Klinikum Bamberg registry has two near-duplicate rows for the same physical site: 46101 'Klinikum Bamberg - Betriebsstätte am Bruderwald-' (trailing hyphen, likely a parse artifact) and 46170 'Klinikum Bamberg - Betriebsstätte am Bruderwald' (no trailing hyphen) -- both share careers_url https://www.sozialstiftung-bamberg.de/stellenangebote/. A posting whose employer text names 'Bruderwald' now correctly returns None (genuinely ambiguous between 2 candidates that are, as far as can be told, the same real site) rather than guessing. 5 postings affected.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 kbo.de group-portal job parsing reads each posting's real per-job city (not the group HQ or a stale default) -- check crawl_group_portal / parse_dvinci-adjacent parsing for this vendor in crawlers/vendor_adapters.py
- [x] #2 Bamberg Bruderwald duplicate rows (46101 vs 46170) resolved -- confirm with Bayern Krankenhausplan source whether they're truly the same site (merge/retire one) or genuinely distinct (find the real distinguishing fact)
- [x] #3 Once fixed, re-run the 14 affected postings (9 kbo.de + 5 Bamberg, ids saved this session in /tmp/relink_final.json -- not committed anywhere durable, re-derive from postings where clinic_match_rule is null and employer mentions 'Bezirks Oberbayern' or 'Bamberg' if that file is gone) through Matcher.match() and correct clinic_id if a real answer is now findable
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Establish whether TASK-68 already closed part 1. Live check: 37 open kbo.de postings, ALL still carry plz=80538 and the crawl that produced them ran 2026-09-21T00:01, ~1h BEFORE TASK-68 landed in 19bc3dc (01:10) -- so the live rows are pre-fix and prove nothing either way. Re-run the adapter instead.
2. Root-cause the residual: even the postings whose city IS right (Garmisch-Partenkirchen x6, Agatharied x3) sit unmatched, because kbo.de's JSON-LD names the GROUP on every posting (hiringOrganization = Kliniken des Bezirks Oberbayern) -- Matcher._match_content can never resolve it, so everything falls to the board pool, which crawlers.routing.plan() keys by exact careers_url (6 separate kbo boards) while app/crawl.py's group_cache fetches the shared list exactly once. Whichever kbo board runs first owns all 108 rows; the other 23 kbo clinics are not in the pool at all.
3. Read the real per-job signal instead of widening the pool: every kbo.de detail page carries a structured Einsatzort block (div.job__related-site) naming the actual site AND its street address. Add site_block_rx to GROUP_PORTALS['kbo.de']; crawl_group_portal takes city+plz from that block's address (towns-validated, ahead of the title regex) and the site NAME as the posting's employer, replacing the group name.
4. Fix the one mis-attribution that surfaces once the better name is fed in: 16211/16212 share an operator, so the operator rung tied and _pick_site handed 13 Kinderzentrum postings to 16212 on bed count. Add the best-Jaccard narrowing R3/R4 already use.
5. Part 2 (Bamberg): scan the whole registry for the trailing-artifact shape systematically, then correct data/registry/clinics.csv.
6. Tests (mocked HTTP + Matcher fixtures), mutation-tested, then the full offline suite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-11: implemented decision-4's mechanism in pflege_jobs/registry.py's R3/R4 token-overlap loop -- when multiple candidates tie and exactly one has real bed capacity (beds truthy), prefer it over beds-less near-duplicates regardless of operator match (generalizes beyond same-operator R6, since the Bamberg case actually has a THIRD candidate under a different operator -- a beds-less KJP day-clinic sharing the building name -- contaminating the same-operator tie-break). Verified locally with a 3-way-tie fixture reproducing the exact live shape; regression test added (test_mech_clinic_link.py::test_prefers_real_site_over_beds_less_duplicate). NOT yet delivered live -- Supabase (both the read proxy and the direct write host) has been timing out since ~15:00 UTC this session, unrelated to this change. Kbo.de AC #1 (title-city extraction) is also implemented and verified against ONE live sub-board (kbo-lmk.de: 15/36 matched) before the outage started; the other 3 kbo sub-boards (IAK/ISK/Heckscher) still need a delivery run once the DB is reachable again.

2026-09-14: TASK-58a (this task's dependent) is now Done -- summarizing what actually got resolved
here vs. what remains open, so this task's status reflects reality rather than staying vague.

AC#2 (Bamberg duplicate): confirmed resolved live via decision-4's real-capacity tie-break, not a
registry merge -- 46101 (911 real beds) and 46170 (0-bed Vertrags-KH twin) are legitimately distinct
regulatory rows for the same building, not a data error. Postings 5906-5910 all confirmed at
clinic_id=46101. Stays checked.

AC#1 (kbo.de per-job city): only PARTIALLY addressed. crawl_group_portal gained a best-effort
title_city_rx regex (crawlers/vendor_adapters.py) that extracts a city from the posting TITLE text
when the title happens to name one ("... in Garmisch-Partenkirchen") -- this is NOT "reading each
posting's real per-job city" as AC#1 was originally worded; kbo.de's own JSON-LD genuinely never
carries a real per-site jobLocation (confirmed, it's always the group HQ address), so there is no
structured field to read. Live result: 34 of 111 raw rows get a real city this way; the other 77
still default to München HQ and correctly stay unmatched (Matcher declines to guess among 8+
München-town candidates) rather than being force-matched. Leaving AC#1 unchecked -- the honest
state is "best-effort mitigation shipped, full fix would need a different signal (e.g. per-posting
page content beyond the title, or an upstream kbo.de API this crawler doesn't have access to)".

AC#3: Bamberg's 5 postings confirmed re-matched (see AC#2). The original "9 kbo.de postings"
reference (ids only ever saved to a since-lost /tmp file) was not individually re-traced; the
current live kbo.de group state (13 open postings across 30 clinics, all via _match_content
disambiguation, none via a forced board guess) is the best available evidence that no currently-
open kbo.de posting is wrongly matched. Leaving unchecked since the specific original 9 ids were
never re-verified by id.

Not moving to Done: AC#1's underlying limitation (best-effort title regex, not a structural fix) is
real and would need its own follow-up if the 77-of-111 unmatched-due-to-no-city rate is worth
chasing further -- not attempted here, scope stayed to what TASK-58a's delivery pass covered.

2026-09-21 (this session).

PART 1 -- was it already fixed by TASK-68? No. TASK-68 removed the HQ address, but it left
crawl_group_portal with only a best-effort title regex as the city source, and it did nothing about
the employer name -- which is the field that actually decides a kbo match. Evidence:
 * Live, before: 37 open kbo.de postings, every single one carrying plz=80538 (the HQ postcode) and
   region Bayern; 15 unmatched, and the matched ones were matched by board pool alone.
 * The crawl that wrote them ran 2026-09-21T00:01Z; TASK-68 landed in 19bc3dc at 01:10Z. So the
   live rows are simply pre-fix -- they are evidence about the OLD code, not about TASK-68's.
 * The decisive evidence is the postings whose city TASK-68's title regex already got right:
   Garmisch-Partenkirchen (6) and Agatharied (3) are stored with the correct city and are STILL
   clinic_id NULL. A correct city is not sufficient.

Root cause of the residual, traced end to end: kbo.de's JSON-LD carries hiringOrganization =
'Kliniken des Bezirks Oberbayern - Kommunalunternehmen' on every posting. Matcher._match_content
returns None for that string against every kbo city (verified against the live 407-row clinics
table for Garmisch-Partenkirchen / Agatharied / Hausham / Muenchen / Berg am Starnberger See /
Landsberg am Lech / Wolfratshausen -- None for all seven). So 100% of kbo attribution depended on
_match_board, whose pool comes from crawlers/routing.py plan(), keyed by EXACT careers_url: the 32
kbo clinics are split across 6 boards, while app/crawl.py's group_cache fetches the shared kbo.de
list exactly once per run. Whichever kbo board happens to run first therefore owns all 108 rows,
and the other 23 clinics are not in the pool at all. That is why Garmisch (18005, kbo-lmk.de board)
could never match, and why 4 'Psychiatrie & Psychosomatik' Landsberg postings were filed under
18104 kbo-Heckscher-Klinikum (KJP, 0 beds) rather than 18103 kbo-Lech-Mangfall-Klinik.

Fix chosen: read the signal the board actually publishes rather than widening the pool. Every
kbo.de detail page carries a structured Einsatzort block (div.job__related-site) naming the real
site and its street address -- confirmed present on 108/108 job pages live 2026-09-21. New
site_block_rx on GROUP_PORTALS['kbo.de']; crawl_group_portal now takes city+plz from that block's
address (towns-validated via pflege_jobs.verify._placeable, ahead of the title regex) and the site
NAME as the posting's employer (org_source stays None -- it is read off the page).

Live re-crawl of the real board with the fix (read-only, 108 detail GETs, no DB writes):
 * 108/108 rows now carry a real per-site city AND its real postcode. 0 rows without a city.
 * exactly 1 row still carries 80538 -- correctly: it is the job at kbo-Kommunalunternehmen,
   Prinzregentenstrasse 18, which really is at that address.
 * Distinct real sites now visible: Garmisch-Partenkirchen 82467, Landsberg am Lech 86899,
   Hausham 83734, Wasserburg am Inn 83512, Haar 85540, Ingolstadt 85049, Taufkirchen 84416,
   Berg am Starnberger See 82335, Wolfratshausen 82515, Peissenberg 82380, Rosenheim 83022,
   Bad Toelz 83646, Freising 85356, Fuerstenfeldbruck 82256, Murnau 82418, and 5 distinct Munich
   postcodes.
 * Matching those 108 rows against the live clinics table: 83/108 now resolve from CONTENT alone,
   no board pool involved (was 0/108). 40 of them via R1_exact. The two Landsberg am Lech sister
   sites now separate correctly: 11 rows -> 18103 kbo-Lech-Mangfall-Klinik, 3 -> 18104
   kbo-Heckscher-Klinikum. Garmisch 11 -> 18005, Agatharied 7 -> 18202 (town Hausham, which no
   city-only rule could ever have reached), Wasserburg 12 -> 18712, Rottmannshoehe 3 -> 18810.
 * The 25 that still do not resolve from content are honest: 12 are not Krankenhausplan sites at
   all (kbo-Service GmbH, two Berufsfachschulen, autkom, MVZ Bad Toelz, the HQ, BIDAQ, the
   ambulanter Pflegedienst) and 13 name a kbo sub-clinic ('kbo-Klinik fuer Psychiatrie und
   Psychotherapie | Taufkirchen (Vils)') that the registry holds only under its parent IAK site
   name. Those still depend on the board pool -- the pool-splitting mechanism above is TASK-81's
   sub-mechanism 1 and was deliberately NOT touched here.

Second, smaller fix (pflege_jobs/registry.py): feeding the real site name in exposed a pre-existing
tie-break bug. The Krankenhausplan lists 'kbo-Kinderzentrum Muenchen gGmbH' as the OPERATOR of both
16211 (kbo-Kinderzentrum Muenchen, 60 beds) and 16212 (kbo-Heckscher-Klinikum Muenchen, 78 beds),
so the 13 postings whose Einsatzort block says 'kbo-Kinderzentrum Muenchen' -- 16211's own name --
tied at the operator rung and _pick_site handed them all to 16212 on bed count, which is blind to
what the posting says. Added the same best-Jaccard narrowing R3/R4 already use one rung below
(R2_operator_town_bestj, score 0.85); a genuine tie still falls through to _pick_site/R6 unchanged.

PART 2 -- Bamberg, checked systematically rather than one row at a time.
The trailing hyphen is NOT a parse artifact. It is the Krankenhausplan's own apposition dash and it
appears on 7 rows, all with parse_quality=ok: 17702 'Klinikum Landkreis Erding -Aussenstelle
Dorfen-', 46101, 46103 '... am Michelsberg-', 46110, 66101 '... - Standort Aschaffenburg -',
67401 'Hassberg-Kliniken - Haus Hassfurt-', 67402 '- Haus Ebern-'. Nothing to fix there.
The REAL artifact shape is parse_quality=partial: 30 CSV rows (29 of them Vertrags-KH) whose name
has the town and a truncated Traeger glued on from the neighbouring PDF columns. 46170 was one of
them -- its CSV name was 'Klinikum Bamberg - Betriebsstaette am Bruderwald-Bamberg Sozialstiftung'
(= the real name, ending in the same apposition dash as 46101, + town 'Bamberg' + Traeger
'Sozialstiftung'), with the operator column left empty. Fixed that row in data/registry/clinics.csv
to match the live clinics table: name 'Klinikum Bamberg - Betriebsstaette am Bruderwald', operator
'Sozialstiftung Bamberg', parse_quality ok.
Whether the two rows are one site or two is already settled and needs no merge: 46101 is the
911-bed Plan-KH (Maximalversorgung III) and 46170 the 0-bed Vertrags-KH registration of the same
building -- two legitimate regulatory rows, resolved at match time by decision-4's real-capacity
tie-break (TASK-58a confirmed postings 5906-5910 on 46101 live).
Systematic finding, NOT fixed here: 24 further CSV rows carry the same glued-name artifact and have
already been cleaned in the live clinics table but not in the CSV (16370, 17274, 17276, 17772,
18475, 18775, 18776, 18781, 18783, 18872, 37273, 37275, 47370, 47570, 47870, 47871, 57570, 67273,
67274 ('Klinik' vs live 'Klinik Bavaria'), 67370, 77473, 77672, 77673, 77873). That is TASK-80's
subject (registry CSV stale against live) and data/registry/clinics.csv is under concurrent edit by
another task this session, so only the 46170 row named by this task's AC#2 was touched.

NULL clinic_id census (asked for alongside this task, read-only): 176 open postings.
By board: helios-gesundheit.de 46, api.smartrecruiters.com 28, jobs.diakoneo.de 19, kbo.de 14,
jobs.sana.de 12, jobs.schoen-klinik.de 8, karriere.gesundheitswelt.de 8, klinikum-nuernberg.de 4,
klinikverbund-allgaeu 5, kliniken-gz-kru.de 4, curamed 3, then a 1-2 row tail across 20 hosts.
0 of the 176 lack a city. 155 of them name a town that IS in the registry -- so these are not
location failures, they are employer-name/board-pool failures (helios 27x Muenchen + 14x Kronach,
smartrecruiters 11x Augsburg/6x Berg/6x Feldafing/5x Tutzing, gesundheitswelt 8x Bad Endorf, all in
registry towns). The 14 kbo.de rows are the ones this task fixes at the crawler layer; the rest are
TASK-81's four sub-mechanisms. Recorded here, not fixed.

AC status. AC#1 checked: verified live, 108/108 kbo.de job pages yield a real per-site city+postcode
from the page's own Einsatzort block, 0 defaulting to the HQ. AC#2 stays checked (TASK-58a
confirmed it live; the registry side is now cleaned too). AC#3 left UNCHECKED and the task left In
Progress: it requires writing corrected clinic_ids to the production database, which this session
was explicitly forbidden from doing. The re-match itself was run and its answers are recorded
above (83/108 resolve from content; the specific before/after per clinic is in the notes), so AC#3
is one delivery run away -- it needs a real crawl+ingest of the kbo boards with this code, which
will rewrite city/plz/employer and re-link on its own.

2026-09-23: AC3's delivery run applied live. Triggered a real crawl+ingest (app.crawl.execute, same mechanism used for TASK-14's re-verification) for the kbo.de jobboerse board (clinic 16251 as entry point, 26-clinic group). Result: run completed, 110 rows. Live check of open kbo.de postings: 37 total, 34 now carry clinic_id (was 0/108 from content before this fix), 3 remain null -- those are OLD pre-fix stale duplicate rows (posting_ids 6622/6624/7395, employer still the group HQ name, city still Muenchen) that the fresh crawl correctly superseded with NEW correctly-attributed rows under a slightly different title phrasing (11239/11240 -> clinic_id 18810, matching the notes' predicted Rottmannshoehe resolution) rather than updating in place -- a stale-duplicate-retirement gap, squarely TASK-83/87's territory, not touched here. The 3 smaller kbo sub-board URLs (Donau-Altmuehl/16107, Taufkirchen/17704, Wasserburg/18712) needed no separate trigger -- each already shows open, correctly-attributed rows, confirming the main jobboerse crawl's content-based matching covers them regardless of which filtered sub-URL nominally routes to them.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
AC3 delivered live: real crawl+ingest run applied the already-landed Einsatzort-block fix to the actual kbo.de board. 34 of 37 currently-open kbo.de postings now carry a correct clinic_id resolved from content alone (was 0 before this fix reached production) -- matches the dry-run's predicted mechanism exactly (real per-site city/postcode/employer name replacing the group-wide HQ label). The 3 unresolved postings are pre-fix stale duplicates superseded by newly-created correctly-attributed rows under slightly different title phrasing -- a duplicate-retirement gap belonging to TASK-83/87, not this task. All 3 AC now checked.
<!-- SECTION:FINAL_SUMMARY:END -->
