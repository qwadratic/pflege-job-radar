---
id: TASK-104
title: >-
  qualification_hint shares department_hint's title-only, single-match defect,
  and a null value silently auto-passes the autopilot qualification gate
status: Done
assignee:
  - '@claude'
created_date: '2026-09-22 16:20'
updated_date: '2026-09-24 15:29'
labels: []
dependencies:
  - TASK-97
references:
  - pflege_jobs/classify.py
  - app/autopilot/matching.py
  - TASK-97
ordinal: 104000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-22 investigation triggered by a request to check department/requirements extraction quality feeding candidate-clinic matching. pflege_jobs/classify.py qualification_hint(title, hauptberuf="") has the exact same two defects TASK-97 already found (and is being fixed) for department_hint: it reads only title plus hauptberuf (an Arbeitsagentur occupation code, not the posting body) through norm_text, and returns next((n for n, r in _QUAL if r.search(s)), None) -- the FIRST matching pattern only, never the description body or the "Ihr Profil"/Anforderungen section that classify.enrich_description() already extracts cleanly into enr_requirements for most live postings.

Measured live 2026-09-22 against v_postings status=open (3166 rows): qualification_hint is null on 1087/3166 (34.3%). app/autopilot/matching.py's _quali_ok(cand_q, job_q) treats a null job_q as an automatic pass ("if not job_q or job_q == generalistisch: return True"), so for those 1087 postings every candidate silently scores the full qualification points in score() regardless of actual fit -- the gate looks like it is checking something but for a third of live postings it is not checking anything at all.

TASK-97 is landing a targeted-section body-extraction approach for department_hint (title plus Aufgaben/Taetigkeiten/Profil-shaped sections, excluding page tail/nav/contact text) plus multi-label support, with false-positive discipline already worked out there. qualification_hint should get the same treatment once that mechanism exists: reuse the section extraction, apply it to the qualification patterns instead of the department patterns. Kept as a separate task rather than folded into TASK-97 because the consumer is different (app/autopilot/matching.py's Matcher.score(), not the web search facet) and TASK-97's acceptance criteria are scoped to the facet/search UI, not to autopilot matching.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 qualification_hint is computed from title plus the same targeted requirements/tasks section(s) TASK-97 extracts for department_hint, reusing that extraction rather than re-deriving a second one
- [x] #2 The post-fix null rate for qualification_hint is measured and reported as a number against the same live open-postings set, not assumed fixed
- [x] #3 _quali_ok's null-job_q auto-pass behavior is either resolved by the reduced null rate or explicitly documented as a remaining known gap with its own new count
- [x] #4 Red-green test against real stored postings: fetch live, confirm the current title-only extraction fails the case, confirm the fix passes; plus a mutation test that reverts the fix and confirms the test goes red
- [x] #5 A manual sample of at least 20 newly-populated qualification_hint values is checked for correctness; the false rate is reported as a number
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. pflege_jobs/classify.py: widen qualification_hint to qualification_hint(title, hauptberuf="", desc="") --
   scan text becomes hauptberuf + title + extract_section(desc, _TASKH, _TASKSTOP) + extract_section(desc, _REQH, _REQS),
   reusing TASK-97's extract_section() helper (already in this file) exactly as department_hint does, applied to _QUAL
   instead of _DEPT. Keep first-match-only semantics (task text doesn't ask for multi-label like department_hint got;
   _QUAL patterns are mutually exclusive licence types, not independently-combinable specialties) -- note this decision
   in Done notes.
2. Widen the same 6 call sites TASK-97 widened for department_hint (bite.py, klinikum_passau.py, career_crawl.py,
   feeds.py, pi_asp.py, inbox.py) to also pass desc to qualification_hint. board_csv.py left unchanged (no desc in that
   source, same as TASK-97).
3. Live-verify (read-only, anon key, pflege_jobs.postings table, status=open) before/after null rate for
   qualification_hint recomputed from title+hauptberuf+desc vs current stored value. Report as a number (AC#2).
4. Check _quali_ok's auto-pass gap against the new null count -- report whether narrowed or still a gap, with its own
   number (AC#3). No code change to _quali_ok itself unless the investigation shows a clear in-scope bug, not a policy
   decision.
5. tests/test_mech_qualification.py: keep existing tests passing (signature backward compatible), add red-green tests
   using real stored posting text where the old title+hauptberuf-only call misses a qualification that title+desc
   catches, plus page-tail-exclusion style negative case if found. Mutation test on qualification_hint.
6. Manual review of >=20 newly-populated qualification_hint values against live description text; report false rate.
7. Sanity-check department_hint still passes its existing tests after this file edit (no revert of TASK-97 work).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
IMPLEMENTED. pflege_jobs/classify.py::qualification_hint(title, hauptberuf="", desc="") -- widened from (title, hauptberuf="") -- now reuses TASK-97's extract_section(desc, head_rx, stop_rx) helper unchanged (same function, same TASKH/TASKSTOP and REQH/REQS compiled regexes department_hint() already uses) to also scan desc's own TASKS (Aufgaben/Taetigkeiten) and PROFIL (Ihr Profil/Anforderungen) sections, applied to _QUAL patterns instead of _DEPT. Scan text = norm_text(hauptberuf + title + tasks-section + profil-section), same join order as before with the two new sections appended. Kept single-value/first-match (unlike department_hint's TASK-97 multi-label change): the 4 _QUAL patterns (GKiK/GuK/Altenpflege/generalistisch) are alternative licence types a person holds one of, not independently-combinable specialties -- no live evidence of a posting genuinely requiring two licences at once, so multi-label would be manufactured complexity, not a real gap. Documented this decision in the function's own docstring.

AC#1: DONE. Reuses extract_section() directly, no second extraction mechanism written.

CALL SITES WIDENED (6, same set TASK-97 widened for department_hint, each already had `desc` in local scope from its own enrich_description(desc) call a few lines above): pflege_jobs/sources/bite.py:195, klinikum_passau.py:104, career_crawl.py:508, feeds.py:21, pi_asp.py:152, inbox.py:63 -- each changed qualification_hint(title, "") -> qualification_hint(title, "", desc). board_csv.py left untouched: confirmed (grep) it has no `desc` variable anywhere in the file, same reason TASK-97 left it alone for department_hint.

pflege_jobs/mechanics.py: _try_qualification now forwards i.get("description", "") as qualification_hint's 3rd arg; the "qualification" Settings-page mechanic gained a "description" (optional) input, mirroring the "department" mechanic's own TASK-97 change, plus updated DE/EN description text to mention the section-extraction source and _quali_ok. New test (test_settings_page_try_it_wiring_passes_description_through) proves this wiring is live, not dead code.

AC#2 -- LIVE VERIFICATION (read-only, anon key, pflege_jobs.postings table, Accept-Profile: pflege_jobs, status=open, 2026-09-24): 3638 open postings (same day/table TASK-97 used, count matches TASK-97's own "3638 open postings" note -- the 3166 figure in this task's own description is now 12 days stale, as flagged).
  STORED (current DB column, whatever contract classified it at ingestion time): null 1133/3638 = 31.1%
  RECOMPUTED BEFORE (old title+hauptberuf-only contract, applied fresh to live title/hauptberuf): null 1139/3638 = 31.3%
  RECOMPUTED AFTER (new title+hauptberuf+desc[Tasks+Profil-sections] contract): null 907/3638 = 24.9%
  Newly labeled (was null under the old contract, now has a value): 232/3638 (+6.4 percentage points)
  Note: STORED (1133) vs RECOMPUTED-BEFORE (1139) differ by 6 rows (4924, 5826, 6226, 6367, 6620, 12312) -- all 6 have a non-null stored value but their current title+hauptberuf alone (no desc) cannot produce one under the old contract (e.g. posting 4924's title is bare "Pflegefachkräfte (m/w/d)", hauptberuf null). This is pre-existing pipeline write-time staleness (classify ran once at an earlier observation with different input data than what's live now), not a defect this task creates or is in scope to fix -- flagged, not touched.

AC#3 -- _quali_ok's null-auto-pass gap: NARROWED, NOT RESOLVED. Before: 1139/3638 (31.3%) of open postings had qualification_hint=null and so app/autopilot/matching.py's _quali_ok(cand_q, None) auto-passed every candidate against them. After this fix: 907/3638 (24.9%) still do -- a real reduction of 232 postings but a genuine remaining gap of its own, not eliminated. app/autopilot/matching.py itself was NOT touched: whether to change _quali_ok's null-handling policy (e.g. treat null as "unknown, skip the qualification point" vs the current "unknown, auto-pass full points") is a scoring-policy decision, not a mechanical extension of this task's extraction fix -- flagging for Ivan rather than silently changing gate semantics. Exact number for his decision: 907 open postings (24.9%) would still receive full W_QUALI=15 points regardless of candidate qualification if _quali_ok's null branch stays as-is.

AC#4 -- RED-GREEN + MUTATION TEST: tests/test_mech_qualification.py, 5 new tests (8 total in file, all passing):
  - test_qualification_named_only_in_the_tasks_section_is_found / test_qualification_named_only_in_the_profil_section_is_found: synthetic fixtures, same style as TASK-97's department tests.
  - test_title_only_misses_a_qualification_stated_in_the_body_live_example: live regression using posting_id 5023 (title "Pflegefachkräfte (m/w/d) für die Aufnahmestation/Chest Pain Unit", trimmed real "Ihr Profil" excerpt) -- asserts qualification_hint(title, "") is None (RED under the old contract, confirmed by literally calling the 2-arg form) and qualification_hint(title, "", desc) == "GuK" (GREEN with the fix).
  - test_text_past_the_section_boundary_produces_no_label: proves extract_section's stop-boundary (TASK-97) still applies -- a qualification word placed after "Wir bieten"/"Kontakt" in the description is not scanned.
  - test_settings_page_try_it_wiring_passes_description_through: mechanics.py wiring test.
  MUTATION TEST (per CLAUDE.md): saved pflege_jobs/classify.py to /tmp/task104_mut/classify.py.orig, mutated qualification_hint back to the exact old body (`s = norm_text(f"{hauptberuf} || {title}"); return next(...)`, no extract_section calls) -- the precise defect this task fixes. Result: 3 of the 5 new tests went RED (test_qualification_named_only_in_the_tasks_section_is_found, test_qualification_named_only_in_the_profil_section_is_found, test_title_only_misses_a_qualification_stated_in_the_body_live_example), each failing with `AssertionError: assert None == '<expected>'` exactly as expected. Restored pflege_jobs/classify.py from the saved /tmp copy (never via git), `diff -q` confirmed byte-identical restoration, re-ran tests: 8/8 green again (16/16 combined with test_mech_department.py).

AC#5 -- MANUAL REVIEW: sampled 25 of the 232 newly-labeled postings (random.seed(104) over the live fetch, same methodology as TASK-97's AC#4 review), traced EVERY hint back to its exact regex match + surrounding context in the scanned (hauptberuf+title+tasks+profil) text via script, not eyeballing truncated printouts. Result: 0/25 (0%) false positives -- every match is a genuine "required qualification" statement inside the posting's own Aufgaben/Profil section (e.g. posting 5023: "...Berufsausbildung als Pflegefachkraft (m/w/d) oder Gesundheits- und Krankenpfleger (m/w/d)" -> GuK; posting 7070: "...Fuehrungserfahrung in der stationaeren Altenpflege..." -> Altenpflege; posting 6339: "...gesundheits- und kranken bzw. kinderkrankenpfleger (m/w/d)..." -> GKiK). Sampled ids:hints: 5015:GuK, 5020:GuK, 5023:GuK, 5771:GuK, 5829:GuK, 5925:GuK, 5929:GuK, 5931:generalistisch, 5937:GuK, 5959:GuK, 6250:GuK, 6339:GKiK, 6655:generalistisch, 6931:GuK, 7070:Altenpflege, 11776:GKiK, 12010:GuK, 12317:GuK, 12623:generalistisch, 12739:GuK, 12838:GuK, 13025:GuK, 13030:GuK, 13626:GuK, 13646:generalistisch. No section-boundary leaks (menu/contact/hospital-wide text) found in this sample -- extract_section()'s existing guarantee holds for _QUAL the same way TASK-97 proved it for _DEPT.

PATTERNS.JSON _QUAL PRECISION CHECK (not an AC, done because TASK-97 found an analogous bug in _DEPT and this task's description explicitly flagged the risk class): reviewed all 4 patterns.qualification regexes (kinderkrankenpfleg; gesundheits- und krankenpfleg|krankenschwester|krankenpfleger; altenpfleg; pflegefachmann/-frau|pflegefachkraft|pflegefachfrau|pflegefachmann|pflegefachperson). None are short/bare-word patterns like _DEPT's unanchored "operations"/"sucht" -- all 4 are long, specific German compound-word substrings with no identified colliding host word. This is a by-inspection check, not a full live scan of every match (that would be a separate audit); no false positives surfaced in the 25-sample manual review either. No patterns.json change made.

department_hint SANITY CHECK (per instructions, since I edited the same file TASK-97 just landed): pflege_jobs/classify.py's department_hint() function body is byte-for-byte untouched by my edit (only qualification_hint(), which sits above it in the file, changed). Verified live after my edit: department_hint("Pflegefachkraft für unsere zentrale Notaufnahme", <Klinikum-Fürstenfeldbruck phone-directory desc>) == "Notaufnahme" (no leak), department_hint(<Ilmtalklinik "verfügt über Stroke Unit" desc>) == "Geburtshilfe" (no leak), department_hint("...Intensivstation und Anästhesie") == "Intensiv/IMC|Anästhesie" (multi-label intact) -- all 3 match TASK-97's own documented live results exactly. tests/test_mech_department.py: 9/9 passed. tests/test_app_api.py::test_multi_label_department_hint_facet_filter_and_search_agree: passed.

TESTS RUN: targeted run covering every touched/adjacent module (tests/test_mech_qualification.py, test_mech_department.py, test_autopilot.py, test_autopilot_matching.py, test_app_api.py, test_board_csv.py, plus a -k filter across bite/klinikum_passau/career_crawl/feeds/pi_asp/inbox/qualification/department): 135 + 122 (overlapping sets) all green, 0 failed, 17 skipped (unmounted autopilot router / network-gated, same as TASK-97's own skip count). Attempted a full untargeted `pytest tests/ -m "not network"` in the background as an extra check (not required by the finalization guide, TASK-97 treated its own equivalent run as supplementary too): it hung mid-run on a live network socket (epoll_wait, open sockets, zero CPU progress across repeated checks) unrelated to my change -- did not wait it out since targeted coverage of every file I touched was already green; flagging the hang itself as a possible pre-existing test-suite issue for Ivan, not investigated further (out of scope for this task).

SCOPE CHECK: `git status --porcelain` for the 9 files I edited (pflege_jobs/classify.py, pflege_jobs/mechanics.py, pflege_jobs/sources/{bite,klinikum_passau,career_crawl,feeds,pi_asp,inbox}.py, tests/test_mech_qualification.py) shows exactly those 9, nothing else. Confirmed pflege_jobs/sources/board_csv.py and app/autopilot/matching.py are NOT modified by me (both already showed " M" in git status at session start from a concurrent session's unrelated work -- I read both but wrote to neither). Did not touch app/data.py, app/cv.py, app/autopilot/seed.py: unlike department_hint, qualification_hint's return shape is unchanged (still a single string or None, never a list), so none of the list-splitting/snapshot-layer changes TASK-97 needed for department_hint apply here.

NOT DONE / DEFERRED (explicit, not silent): (1) app/autopilot/matching.py::_quali_ok's null-handling policy -- narrowing vs eliminating the auto-pass gap is Ivan's call, not mine (AC#3 above). (2) patterns.json _QUAL precision audit beyond visual inspection -- no bug found, but not exhaustively live-scanned across all matches. (3) The 6-row stored-vs-recomputed discrepancy (pipeline staleness) -- noted, not chased further, not this task's defect.

CORRECTION to the full-suite note above: the background `pytest tests/ -m "not network"` run did NOT hang -- it completed normally after it finished (I checked too early/impatiently; it was legitimately slow, not stuck). Actual result, now in hand: 1519 passed, 18 skipped, 2131 deselected, 0 failed, in 415.64s (~7 min). Confirms the full suite is clean with this change in place -- no pre-existing failures this time (unlike TASK-97's run, which hit one unrelated concurrent-edit failure that has since resolved). Withdrawing the "possible pre-existing test-suite issue" flag from the note above -- there is no hang, just a ~7-minute full run.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
qualification_hint(title, hauptberuf="", desc="") now reuses TASK-97's extract_section() to also scan the posting's own Aufgaben/Taetigkeiten and Ihr Profil sections (via _TASKH/_TASKSTOP, _REQH/_REQS), not just title+hauptberuf. 6 source adapters widened to pass desc (same set TASK-97 widened for department_hint); board_csv.py left unchanged (no desc there). Kept single-value/first-match by design (licence types are alternatives, not combinable specialties, unlike department_hint's multi-label).

Live-verified (anon key, pflege_jobs.postings, status=open, 2026-09-24, 3638 postings): null rate 31.3% (recomputed old contract, matches stored 31.1%) -> 24.9% after the fix, 232 newly labeled. _quali_ok's null-auto-pass gap is narrowed (1139 -> 907 postings) but NOT eliminated -- 907/3638 (24.9%) still auto-pass the qualification gate; whether to change _quali_ok's null policy is left as an explicit decision for Ivan, not changed here. Manual review of 25 newly-labeled postings (random.seed(104), full regex-trigger tracing): 0/25 false positives. Reviewed _QUAL's 4 patterns for TASK-97's unanchored-regex bug class -- none found (by inspection).

Verified with pytest: 8/8 in tests/test_mech_qualification.py (5 new: 2 synthetic section tests, 1 live-posting regression using real posting_id 5023 text, 1 section-boundary negative test, 1 mechanics-wiring test), plus a mutation test (reverted qualification_hint to the old title+hauptberuf-only body, confirmed 3 tests go RED with exact AssertionErrors, restored byte-identical via /tmp copy + diff -q, confirmed green again). department_hint sanity-checked after the edit: byte-identical function body, all 9 of its own tests pass, and its 3 live-documented TASK-97 cases (Notaufnahme, Geburtshilfe, Intensiv/IMC|Anästhesie) reproduce exactly. Targeted test run across every touched/adjacent module: 135+122 (overlapping) passed, 0 failed, 17 skipped (pre-existing, network-gated). git status confirms exactly the 9 intended files changed; board_csv.py and app/autopilot/matching.py were read but not written.
<!-- SECTION:FINAL_SUMMARY:END -->
