---
id: TASK-145
title: >-
  Board tools: full posting detail, 5-row listings with a truthful total, city
  resolution, hard live-only, CV ranking
status: Done
assignee: []
created_date: '2026-09-21 10:30'
updated_date: '2026-09-21 10:45'
labels: []
dependencies:
  - TASK-110
priority: high
type: feature
ordinal: 153000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-21, from the first real phone-rail conversation: what the Luna tools layer (app/wa/luna/tools_server.py, TASK-110) lets the model see is too narrow, too loose and too silent. Verified on a fixture board 2026-09-21:

1. The model cannot read what a posting IS. get_posting returns a 10-field projection; description, enr_requirements, enr_language_req, enr_housing_evidence and shift_night_weekend are not even in the snapshot (app/data.py JOB_COLS), and enr_tariff/enr_pay_grade are in the snapshot but never projected. The board serves all of them on GET /api/jobs/{id} (app/data.py job_detail), a path board_api_get does not allow.
2. No volume control: 100 matching postings answer with 10 rows (RESULT_LIMIT 50) and no total anywhere, so the model cannot say how many more exist. Ivan's rule is at most 5 positions per listing turn, enforced where the result set is assembled (see TASK-144 for the dialog side).
3. City is exact lowercase equality (app/data.py filter_jobs), so with 100 postings in Nuernberg, city=Nuernberg and city=Landkreis Nuernberger Land both answer [] and the bot then tells the candidate there is nothing there. The department word is already resolved in the tools layer; the city word is not.
4. The live-only base can be overridden: board_api_get(query='verify=gone') returns postings the verifier found gone, and get_posting reads all open postings. Ivan: remove the override, never validate it.
5. app/cv.py match() runs only after consent, off the reply path, so the conversation can never answer 'which of these fits my CV'.

PII: no candidate number or message body in any tool result or call log.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A tool reads one posting in full: description, requirements, tariff, pay grade, language requirement, housing evidence and night/weekend shift as the board holds them, with the tool description naming exactly what comes back
- [x] #2 Every posting-listing tool returns at most 5 rows plus the true number of matches, as {shown, total}; the cap is in code, no parameter can raise it, and no board link is added to any result
- [x] #3 A city word is resolved the way the department word is: Nuernberg/Wuerzburg/a Landkreis reach the right board city, and a word no board city matches raises a ToolError naming the near matches instead of returning an empty list
- [x] #4 No tool can reach a posting the verifier has not confirmed live: no verify= override on the raw query door, get_posting reads the live-only base, and a withheld posting is reported, never silently dropped
- [x] #5 A read-only tool ranks the candidate's stored CV text against current postings with app/cv.py match(), sending nothing and re-implementing no matching
- [x] #6 Offline suite green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Reproduce all five defects against a fixture board before changing anything.
2. app/wa/luna/board_vocabulary.py: add the generated 'city' line the tool descriptions carry.
3. app/wa/luna/tools_server.py: LISTING_LIMIT + _listing() {shown,total} on every posting listing; _resolve_city over slots.read_city with a near-match refusal; get_posting through app/data.py job_detail, live-only, withheld-not-missing; _live_only replacing _verified (no verify= override anywhere) with a withheld count in the envelopes; match_cv_to_postings over app/cv.py profile_from_text + match().
4. tests/test_wa_luna_tools.py: move the existing expectations to the new shape and add the six new proofs.
5. Full offline suite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified before changing (fixture board, 2026-09-21):
- get_posting returned 10 projected fields and no ad text; /api/jobs/{id} not on the allowlist.
- 100 matching postings -> 10 rows, no total anywhere; limit=999 -> 50.
- city='Nuernberg' and 'Landkreis Nuernberger Land' -> [] while 'Nürnberg' had 100.
- board_api_get(query='city=Coburg&verify=gone') returned the gone posting; get_posting(999) returned it too.
- no CV tool registered at all.

Decisions:
- get_posting is the way to read one posting in full, NOT a new /api/jobs/{id} entry in BOARD_API_PATHS: get_posting is already on luna_brain's --allowedTools list, and the raw route's own row carries external_url, source_url, observations[] and clinic.website -- link surface for a model that must never send a link. The curated projection (POSTING_DETAIL_FIELDS) names exactly what the description promises.
- Genuinely absent from the snapshot's JOB_COLS and read through job_detail: description, enr_requirements, enr_experience, enr_language_req, enr_housing_evidence, shift_night_weekend. enr_tariff and enr_pay_grade were already in JOB_COLS (the gap analysis said otherwise) but were never projected to the model; both are now. No field was invented: all eleven exist in pflege_jobs/schema.py.
- The city word is resolved in every door that takes one, including board_api_get(/api/jobs, /api/clinics) -- otherwise the raw door is the way around the reading. Postings resolve against the towns that carry live postings, clinics against the registry's towns.
- CITY_NEAR_MATCH_RATIO 0.75 is a spelling threshold, not a result ceiling (measured: 'nuremberg'/'Nürnberg' 0.78, 'hamburg'/'Bamberg' 0.71), and the refusal says a near match is a DIFFERENT town.
- match_cv_to_postings takes no arguments: the candidate is the turn's own context (WA_LUNA_PHONE, like WA_SQLITE_PATH), so no number enters the model's tool call, the call log or any result. It reads the stored cv_text with a plain select rather than store.thread(), which would create a thread row.

HANDOFF, another lane's file (app/wa/luna_brain.py): the model cannot reach the new tool until (1) MCP_TOOL_NAMES names 'match_cv_to_postings' and (2) _mcp_config_path passes WA_LUNA_PHONE in the server env next to WA_SQLITE_PATH. Until then it is served and never called; the tool fails loudly rather than guessing whose CV it is.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/wa/luna/tools_server.py + app/wa/luna/board_vocabulary.py (+ tests/test_wa_luna_tools.py).

get_posting now returns the ad itself (description, enr_requirements, enr_experience, enr_language_req, qualification_hint, enr_tariff, enr_pay_grade, enr_housing_evidence, shift_night_weekend, start_date, contract) through app/data.py job_detail, e-mail-scrubbed like every other door; every posting listing returns {shown, total} with at most LISTING_LIMIT=5 rows and the true match count, and the listing tools no longer take a limit argument at all; the candidate's own town word is resolved through slots.read_city in every door that takes one, and an unrecognised word raises naming the spelling-nearest board towns instead of returning []; the verify= override is gone (any verify= is refused) and get_posting reads the live-only base, with withheld_not_live in the envelopes and a named refusal for a withheld id; match_cv_to_postings ranks this conversation's stored CV with app/cv.py profile_from_text + match(), live rows only, sending nothing and never taking or logging the number.

Verified: the five defects were reproduced first on a fixture board; tests/test_wa_luna_tools.py now proves 100 matches -> 5 rows + total=100, an unknown city raising with near matches, no tool (get_posting and the raw query door included) handing over a non-live posting, the detail tool returning the fields its description claims, and the CV tool ranking with nothing sent. Offline suite: 1947 passed, 127 skipped, 70 deselected in 178.56s.
<!-- SECTION:FINAL_SUMMARY:END -->
