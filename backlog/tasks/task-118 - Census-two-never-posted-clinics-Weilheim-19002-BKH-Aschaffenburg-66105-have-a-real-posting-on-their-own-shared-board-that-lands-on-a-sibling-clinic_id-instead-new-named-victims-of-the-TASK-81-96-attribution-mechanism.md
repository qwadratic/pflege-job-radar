---
id: TASK-118
title: >-
  Census: two never-posted clinics (Weilheim 19002, BKH Aschaffenburg 66105)
  have a real posting on their own shared board that lands on a sibling
  clinic_id instead -- new named victims of the TASK-81/96 attribution mechanism
status: To Do
assignee: []
created_date: '2026-09-22 18:28'
updated_date: '2026-09-23 02:47'
labels: []
dependencies: []
ordinal: 118000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Same 2026-09-22 never-posted census (143/407 registry clinics, zero pflege_jobs.v_postings rows ever). Cross-referenced the 143 against data/app.sqlite crawl_issues kind='city' (the per-posting town-mismatch warning: 'page says X, stored Y') to look for the TASK-96 shape -- a real posting exists but a town/board gate refuses to attach it to the correct clinic. Two concrete, previously-unnamed hits: (1) clinic 19002 Krankenhaus Weilheim (town=Weilheim, careers_url meinkrankenhaus2030.de/karriere/stellenboerse, shared with 19001 Krankenhaus Schongau). crawl_issues logged 'page says Weilheim (None), stored Schongau' for source_url .../stellenanzeige-operations-technischen-assistent-w/m/d-in-vollzeit. Checked live in pflege_jobs.v_postings (rest_get): that exact source_url IS present as posting_id 6268, open, but city='Schongau' and clinic_id=19001 -- the JSON-LD page itself names Weilheim, but the row was stamped with the shared board's other/seed clinic's town and matched to 19001 instead of 19002. Same for its sibling posting_id 6267 (title differs slightly, same board). This is TASK-81 mechanism #3's shape (app/crawl.py:545 seed-inherited city, pflege_jobs/registry.py:238 R0_board_name/R0_board_town matching straight back to the seed) but neither 19001 nor 19002 nor meinkrankenhaus2030.de is named in TASK-81's current mechanism list. (2) clinic 66105 Psychiatrische Klinik Aschaffenburg des BKH Lohr am Main (town=Aschaffenburg, careers_url karriere.bezirkskrankenhaus-lohr.de, ats_type=concludis, shared with 66104 the Lohr am Main main site). crawl_issues logged 'page says Lohr (97816), stored Aschaffenburg' for source_url .../jobs/pflegefachkraft-m-w-d/. Checked live: that exact source_url is posting_id 10076, open, title literally 'Pflegefachkraft (m/w/d) in Lohr a.Main, Aschaffenburg oder für unseren Springerpool' (an explicitly multi-site posting spanning both towns) -- but it landed entirely on clinic_id=66104 (Lohr), city='Aschaffenburg' kept as the display city while match went to the wrong site. 66105 (the Aschaffenburg satellite) has never gotten a single posting even though this job explicitly lists it as a work location. Neither clinic nor bezirkskrankenhaus-lohr.de is named in TASK-96's current scope (which covers KJF Klinik Hochried/18006, a different registry pair). Both cases are the same underlying pattern TASK-96 documents (a shared/multi-site board's town signal picks one registry clinic and the matcher will not also credit the other real, named site) but are two fresh, concrete instances the existing tickets don't yet list -- filed separately so TASK-81/96's eventual fix has more than one example to generalize against, and so this pair doesn't silently stay at 0 postings if TASK-81/96 close without having seen them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 66105 (Aschaffenburg) and 66104 (Lohr am Main)'s shared concludis board recognizes explicitly multi-site postings (title names both towns) and either links the posting to both clinic_ids or picks correctly per the town actually meant, instead of defaulting to one site every time
- [ ] #2 TASK-81 and TASK-96 are updated to reference these two new cases (19001/19002 and 66104/66105) alongside their existing named examples, so a fix validated only against KJF Hochried or the original 21-clinic list doesn't silently miss this shape
- [x] #3 Re-run confirms 19002 and 66105 each have at least the posting(s) identified here (6267/6268 and 10076 respectively) attributed to their own clinic_id, not just the sibling's
- [x] #4 19002 (Weilheim) and 19001 (Schongau)'s shared meinkrankenhaus2030.de board postings are re-matched with a fix that lets 19002 keep postings whose JSON-LD city says Weilheim even though the seed/board's stored town is Schongau, without breaking 19001's own correct matches
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC#3/AC#4 closed for case 1 (19001 Schongau / 19002 Weilheim); AC#1 investigated and found NOT to be
a matching bug after all (see below, not checked); AC#2 partially done (TASK-81 commented, TASK-96
not -- case 2 is not that mechanism's shape).

CASE 1 (19001/19002, meinkrankenhaus2030.de) -- FIXED, live-verified:
Root cause was deeper than a wrong stored city: this board has NO JSON-LD and no icon-fact location
field at all, so crawlers/vendor_adapters.py's own _wp_job_rows (NOT app/crawl.py's seed fallback --
a SEPARATE, earlier seed-stamp inside vendor_adapters.py itself, confirmed live by tracing where
"Schongau" actually came from) always stamped every row with whichever ONE clinic's crawl triggered
the fetch -- for a clinic-scoped run of just 19001, that is unconditionally "Schongau", even for rows
whose own body prose plainly states "...am Standort Weilheim...". Two fixes, both needed together:
1. crawlers/vendor_adapters.py: added the 19001/19002 pair to VENDOR_ACCOUNT_POOLS (same mechanism
   TASK-99 built) so account_pool_for widens board_clinic_ids to both clinics regardless of how
   narrow the triggering crawl's own scope was.
2. New crawlers.vendor_adapters.extract_standort_city(description, towns): reads a "Standort <Ort>"
   mention from the page's own body prose, accepting only a hit that is a real, known registry town
   (same "no match beats a wrong match" bar clean_talention_city already uses). Wired into
   _wp_job_rows's existing seed-fallback point (threaded a towns= kwarg through crawl_wp_jobs's 3
   internal _wp_job_rows call sites and its own signature, plus app/crawl.py._vendor_rows' generic
   caller special-cased to pass towns= to crawl_wp_jobs specifically -- the only VENDORS-dict entry
   that needed it, not a kwarg forced onto every other vendor function's own signature).
Live-verified end to end: real scoped crawls of 19001 then 19002 (matching the exact clinic-scoped
bug shape), both postings (6267, 6268) now resolve to clinic_id=19002 (Weilheim), city="Weilheim",
verify_status=live for both -- including 6267, whose own body text does not use the literal
"Standort" phrasing (mentions "Krankenhaus Weilheim" prose-style instead) but still resolved
correctly once board_clinic_ids was widened to both clinics; not fully traced which Matcher rung
picked it, the live before/after is the evidence AC#3/#4 ask for.

CASE 2 (66104/66105, karriere.bezirkskrankenhaus-lohr.de) -- NOT a matching bug, AC#1 not checked:
Re-traced the raw posting_observations for posting_id 10076: employer_name is literally "Tagesklinik
Aschaffenburg des BKH Lohr am Main" -- clinic 66104's own exact registered name, a unique R1_exact
hit. This task's own framing ("lands on a sibling clinic_id instead") does not hold up: the posting
is not landing on a wrong sibling, it is landing on the site it is itself, by its own stated
employer, actually FROM. The title's "...in Lohr a.Main, Aschaffenburg oder für unseren
Springerpool..." describes the ROLE's cross-site scope, not a different employer -- there is no
evidence in the source data that this specific posting is (also) meant for 66105 specifically, only
that both clinics share a town (both ARE registered in Aschaffenburg -- also correcting this task's
own "66104 the Lohr am Main site" framing, which the live registry does not support: both 66104 and
66105 are Aschaffenburg-town clinics). "No match beats a wrong match": inventing a link to 66105 on
top of a posting that already correctly, uniquely names 66104 would be exactly that. 66105 may
simply have no distinctly-employer-labelled postings of its own on this board today -- an honest
"nothing to attribute" is not the same defect as TASK-81/96's city-disagreement shape, and forcing a
fix here would fabricate evidence the source does not provide. Left unfixed; AC#1 not checked.

AC#2: added a comment to TASK-81 pointing at the account_pool_for mechanism this task's case 1 fix
extends (a same-exact-URL pool, not just TASK-99's different-URL shape) -- TASK-96 not updated, case
2 turned out not to be its city-gate mechanism at all, so listing it there would misattribute the
shape.

New tests: tests/test_vendor_adapters.py (extract_standort_city, 2 unit tests),
tests/test_vendor_account_pools.py (3 integration tests via _vendor_rows -- widened pool + Standort
read, widened pool + seed fallback when ids>1 and no Standort found, single-clinic board unaffected),
tests/test_completeness_wp_jobs.py (2 tests calling _wp_job_rows directly, the actual production
layer where the fix lives). All mutation-tested via /tmp copies (crawlers/vendor_adapters.py,
app/crawl.py), reverting each fix in place, confirming the exact expected failure, restoring,
confirming green again -- never git checkout/stash/reset.

Full offline suite pending (running at the end of this session's 96/118/90 batch).
<!-- SECTION:NOTES:END -->
