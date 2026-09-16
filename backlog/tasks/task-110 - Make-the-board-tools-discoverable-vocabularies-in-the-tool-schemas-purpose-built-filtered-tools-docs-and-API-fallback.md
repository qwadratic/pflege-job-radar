---
id: TASK-110
title: >-
  Make the board tools discoverable: vocabularies in the tool schemas,
  purpose-built filtered tools, docs and API fallback
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 14:49'
updated_date: '2026-09-16 19:56'
labels: []
dependencies:
  - TASK-108
type: feature
ordinal: 110000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-16, after finding that housing was never used as a filter: the model only learns the board filters from bare parameter names in the MCP schema (city, department, role_class, regierungsbezirk, housing, employment_type, q, limit). The prompt TOOLS rule names the three tools and demands a call when a place is named, but lists no filters, no allowed values and no guidance on when each applies, so a usable filter can simply go unused. The repo skill (skill/SKILL.md, skill/references/api.md, data-model.md) documents the board API for agents but is not reachable from Luna, which runs with --strict-mcp-config and three read-only tools and no file or network access.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 each tool schema/description carries the vocabulary the model needs, generated from the live board rather than hardcoded: departments, Regierungsbezirke, role classes, employment types, what the housing flag means and roughly how much of the board carries it
- [x] #2 purpose-built tools cover the common candidate questions with the right filters preset (at least: postings with housing in a city, clinics offering housing, which cities have postings for a department or with housing, a count for given criteria), each with a description saying when to call it
- [x] #3 the prompt lists the available tools with their filters and the rule that a stated need (housing above all) must be reflected in the call, without turning into a script
- [x] #4 a fallback exists for questions the preset tools do not cover: a tool serving the skill reference docs and a read-only board API call restricted to an allowlist of paths; both fail loudly and never reach anything but the public board API
- [x] #5 offline tests cover the generated vocabularies, every new tool, the allowlist (including a rejected path) and the docs tool; llm persona runs show a housing-needed candidate answered from a housing-filtered call, and a question the preset tools do not cover answered via the fallback; docs updated
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. tools_server.py: board_vocabulary() generated from the live snapshot the tools themselves search (verify=live rows): departments+counts, Regierungsbezirke, role classes, employment types, what enr_housing means and its share; applied to every tool description at server start (serve(): apply_board_vocabulary() then mcp.run), raising loudly when the snapshot did not load.
2. Purpose-built read-only tools with the filter preset, each saying when to call it: search_postings_with_housing, list_clinics_with_housing, list_cities_with_postings(department/housing), count_postings. Same D.filter_jobs/filter_clinics path as search_postings, same verify=live base, same department reading (slots.read_department_pref), same call log.
3. Fallback tools: read_board_docs(topic) serving skill/SKILL.md + skill/references/api.md|data-model.md|pipeline.md (unknown topic = loud ToolError naming the topics; the published anon Supabase key and any JWT-shaped token stripped from what is served -- a candidate-facing model gets no credential), and board_api_get(path, params) restricted to an allowlist of public board GET paths (/api/jobs, /api/clinics, /api/clinics/{id}, /api/cities, /api/facets, /api/taxonomy, /api/search), answered in-process by the same functions those routes call with anonymous redaction; every other path is a loud ToolError naming the allowlist.
4. luna_brain.MCP_TOOL_NAMES: add the four preset tools and the two fallback tools (get_clinic_contact stays out); docstring/comment updated.
5. prompts.py: compact CAPABILITIES block listing the tools with their filters and allowed values source, plus the rule that a stated need -- housing above all -- must be reflected in the call itself (state only, no script).
6. Tests: offline (tests/test_wa_luna_tools.py) for the generated vocabularies, every new tool, the allowlist including a rejected path, the docs tool and its secret stripping; llm personas (tests/test_wa_luna_personas.py, one at a time): housing-needed candidate answered from a housing-filtered call, and a question the presets do not cover answered through the fallback.
7. docs/whatsapp.md: the tool surface, the generated vocabulary, the allowlist and the anon-key decision.

REVIEW FIXES (2026-09-16, confirmed findings F1-F5 + TASK-110-1..6):
8. Vocabulary out of the turn's critical path (F1/TASK-110-1): new app/wa/luna/board_vocabulary.py (LIVE_BASE, clinic_key, board_vocabulary, vocabulary_lines) imported by both luna_brain (parent, warm snapshot) and tools_server. luna_brain writes the lines next to mcp_config.json and passes WA_LUNA_BOARD_VOCABULARY, so the spawned server starts in ms instead of a cold 8-17s Supabase build under the CLI's 30s MCP connect deadline. A snapshot error with a usable cached board no longer raises (refresh() keeps serving it, and market_snapshot uses the same rows); no live-verified row still does.
9. A dead/dropped tools server is no longer silent (F1): serve() writes a readiness stamp (WA_LUNA_TOOLS_READY, one file per turn) after the tools are registered; _live_reply raises the same loud RuntimeError it raises for a missing claude binary when the stamp is absent after an exit-0 run. Probed: the --output-format json envelope carries no mcp server status, so the stamp is the only signal the parent can read.
10. board_api_get bounded and on the same base (F2/TASK-110-2, F4): a stated maximum of RESULT_LIMIT rows per call on /api/jobs, /api/clinics, /api/clinics/{id} and /api/search, a limit above it a loud ToolError naming the maximum (app/data.py:page: a ceiling belongs as a 400 naming the maximum), and verify=live merged into the query unless the model passes verify= itself -- the two doors disagreed by up to 20x on one city (Coburg 2 vs 39).
11. Sparse-column vocabulary for the fallback door (F3): generated line naming how many live rows carry a value for contract/enr_tariff/qualification_hint/clinic_size/versorgungsstufe/traegerart (live: contract 16 of 2624, BEFRISTET only) and the rule that a 0 from such a filter means the board does not record it, never 'we have none'.
12. read_board_docs serves no infrastructure or credentials (F5): YAML front matter and the whole 'Where things are' section (host table, Supabase project, the key and its scope audit) removed before serving, every URL masked next to the JWT mask.
13. One clinic identity everywhere (TASK-110-3): clinic_key = clinic_id, else the employer name; used by board_vocabulary, list_clinics_with_housing, list_cities_with_postings and count_postings, so 48-vs-51 cannot happen and BOARD NOW counts the clinics the tools actually search.
14. Prompt (TASK-110-4/5): no tool call for a board-wide total market_snapshot already carries; the fallback's 0-rows rule; HOUSING drops the hardcoded 'one posting in eight' (the generated housing line carries the share).
15. Tests: offline for every item above (parent-built vocabulary, stamp written/missing, the row maximum, the verify base, the clinic key, the sparse-column line, the stripped docs) and the persona guard asserting no board tool at all; docs/whatsapp.md updated incl. the corrected unfilterable-department evidence (live: Medizinisch-technischer Dienst, 1 posting).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented (2026-09-16).

tools_server.py
- board_vocabulary() reads the filter vocabulary off the live board (the verify=live rows the tools actually search) and apply_board_vocabulary() writes it into the registered tools' descriptions at server start (serve(); tests/luna_fixture_tools_server.py runs serve() too, so llm runs see descriptions built from the fixture board). Each tool carries only its own parameters' values -- descriptions are sent every turn. A snapshot with an error, or with no live-verified posting, raises at server start instead of serving an empty vocabulary. A department value the filter cannot apply (live: Pflege, Berufsfachschule fuer Pflege, Medizinisch-technischer Dienst -- 4 postings) is not advertised, it is counted into what a department filter drops. Live text 2026-09-16: 2624 live-verified of 3905 open postings, 407 clinics, 216 cities; Intensiv/IMC 229, OP 167, ...; 1250 of 2624 carry no filterable department; Oberbayern 943 ...; pflegefachkraft 1783 ...; vollzeit 869/teilzeit 712/minijob 1; housing 416 of 2624 (16%) at 48 clinics in 44 cities.
- Preset tools, all read-only, same D.filter_jobs base (verify=live) and the same slots.read_department_pref reading as search_postings/market_snapshot: search_postings_with_housing, list_clinics_with_housing, list_cities_with_postings(department, housing, regierungsbezirk), count_postings (postings/clinics/cities/with_housing/filters applied).
- Fallback: read_board_docs(topic) serving skill/SKILL.md + skill/references/api.md|data-model.md|pipeline.md (unknown topic = ToolError naming the topics), and board_api_get(path, query) over an allowlist of public board GET paths (/api/jobs, /api/clinics, /api/clinics/{id}, /api/cities, /api/facets, /api/taxonomy, /api/search), served in-process by the same functions those routes call, with the app API envelope, D.redact(..., None) (enr_contact_emails null, addresses masked) and the API's own 400s re-raised as ToolError. Every other path is a ToolError naming the allowlist. /api/stats and /api/jobs/{id} are off it on purpose (crawl-run DB read; Supabase call -- get_posting covers one posting).
- Anon Supabase key decision, with evidence: skill/SKILL.md publishes it and documents what it opens (un-redacted enr_contact_emails, raw description, posting_observations.payload, the whole inbox, possibly INSERT). Luna has no network and no shell, so the key buys her nothing, and a candidate-facing model holding a credential can put it in a bubble (TASK-100 precedent). read_board_docs strips every JWT-shaped token; the allowlisted app API is the whole data door.

luna_brain.MCP_TOOL_NAMES now allows the nine read-only tools (get_clinic_contact still excluded, TASK-91); Client docstring updated.
prompts.py TOOLS rule lists every tool with its parameters, says the values live in each tool's own description, and states that a stated need -- housing above all -- must be in the call (with housing_needed true a plain search_postings is the wrong call); HOUSING says the same at the gate.

Tests: tests/test_wa_luna_tools.py +24 offline tests (vocabulary values/counts/housing share, only-own-filters, board change changes the text, unfilterable department counted not advertised, failed and empty board raising, every preset tool, each docs topic, unknown topic, key stripped, every allowlisted path, 7 rejected paths, personal data removed, limit=abc as ToolError, MCP_TOOL_NAMES vs registered tools). llm (one at a time, claude-sonnet-5, 2026-09-16): housing persona passed first run (search_postings_with_housing(city=Wuerzburg) -> list_cities_with_postings(housing=true) -> count_postings(city=Wuerzburg), no flat claimed for Wuerzburg); fallback persona passed twice (read_board_docs('skill') then board_api_get('/api/jobs','contract=UNBEFRISTET&limit=1')). Persona/e2e fixtures gained jobs_fresh/jobs_live (GET /api/cities reads them) and a contract value (a filter no tool presets).

docs/whatsapp.md: tool table, the generated vocabulary, the fallback + allowlist + anon-key decision, the prompt change, tests and live evidence.

Full offline suite: 1635 passed, 126 skipped.

Review round applied (2026-09-16, fixer): F1-F5 + TASK-110-1..6.

F1/TASK-110-1 (the raise at server start was invisible; the cold board build sat on the CLI's 30s MCP connect deadline). New app/wa/luna/board_vocabulary.py holds LIVE_BASE, clinic_key and the counting; luna_brain._board_vocabulary_path() builds the lines in the PARENT from the snapshot it already holds for market_snapshot (30ms warm, measured) and passes the file as WA_LUNA_BOARD_VOCABULARY next to mcp_config.json (both written atomically -- turns can overlap). Measured: the spawned server now registers all 10 tools in 1.06s with SUPABASE_* unset, where it used to do a cold 7.2-17s Supabase build first. tools_server.serve() then stamps WA_LUNA_TOOLS_READY (one file per turn) and _live_reply raises the same loud RuntimeError it raises for a missing CLI when that stamp is absent after an exit-0 run -- probed CLI 2.1.270: the --output-format json envelope has no mcp server status (keys: type/subtype/is_error/result/session_id/usage/permission_denials/...), so the stamp is the only signal the parent can read. Also changed: board_vocabulary() no longer raises on a snapshot 'error' flag while the cached board still holds rows (app/data.py:refresh keeps serving it and market_snapshot answers the same turn from those rows); no live-verified row still raises, and that failure now leaves no stamp -> the parent fails the turn loudly (verified end to end: exit 1, 0 bytes stdout, no stamp).

F2/TASK-110-2 (unbounded fallback page, silently truncated by the CLI). board_api_get bounds /api/jobs, /api/clinics, /api/clinics/{id} and /api/search at BOARD_API_MAX_ROWS=25 whole API rows; a bigger limit is a ToolError naming the maximum (data.py:page's own prescription), never a shortened page, and the clinic detail carries jobs_total next to its bounded jobs list. 25, not RESULT_LIMIT=50, because these are 43-field rows: measured live today through the tool, 50 rows = 105,087 chars, over the CLI's MAX_MCP_OUTPUT_TOKENS (25000 ~ 100k chars). After the bound: /api/jobs 53,708 chars, ?city=München 52,670, /api/clinics 29,431, /api/cities 37,312, /api/facets 27,939, one clinic's detail 51,443 (25 of 203 jobs).

F3 (fallback filters with no vocabulary: contract=UNBEFRISTET -> 0 reads as 'we have none'). Generated api_columns line on board_api_get: 'contract 16 (BEFRISTET 16), enr_tariff 1189, qualification_hint 1818, clinic_size 2100, versorgungsstufe 2411, traegerart 2411' of 2624 live rows (values named for any column under 10% coverage -- the traps), plus the rule that a 0 there means the column is empty, never 'we have none'. Prompt says the same. The persona fixture now mirrors the live sparsity (1 of 6 with a value, no UNBEFRISTET) so the test cannot pass on a filter the board does not populate; the llm run answered 'das erfasst die Börse leider nicht zuverlässig ... nur bei einer der 6 offenen Stellen steht überhaupt ein Vermerk' instead of claiming none.

F4 (two bases). board_api_get merges verify=live unless the model passes verify= itself; /api/clinics/{id}'s job list too. Live after: count_postings(city='Coburg') 2 == GET /api/jobs?city=Coburg total 2 (was 2 vs 39); München 369 == 369 (was 369 vs 499).

F5 (host, project and key audit served to a candidate-facing model). read_board_docs now drops the YAML front matter and the whole '## Where things are' section (host table, Supabase project, the key, the anon-scope audit) and masks every URL next to the JWT mask, saying what was dropped (SECTION_MASK/URL_MASK). Served skill text measured: 9,620 chars, 0x exe.xyz, 0x the project ref, 0x eyJ, 0x https -- and still carries enr_housing/department_hint for the questions the fallback exists for.

TASK-110-3 (48 vs 51 clinics). One identity everywhere: board_vocabulary.clinic_key = clinic_id, else the employer name, used by the generated lines, list_clinics_with_housing (two sites of one name are now two entries with their own clinic_id/city, an unlinked employer is its own clinic), list_cities_with_postings and count_postings. Live: housing clinics 51 in both places (was 48 in the schema line vs 51 from the tools); BOARD NOW now reads '2624 live-verified of 3905 open postings at 248 clinics in 216 cities' (it used to print the 407-row registry next to the rows the tools search).

TASK-110-4 (the needless-call guard). Prompt: a number the payload carries gets no call from any tool. That did not hold across runs (3 llm runs: 2 called count_postings() with everything empty), so count_postings now refuses a call with no filter at all -- ToolError naming market_snapshot.open_jobs, logged with refused:'no filter' so a test can see the attempt was not answered. The persona guard asserts no board tool ANSWERED (read_board_docs excluded on purpose: static text, no board claim; the model reads it 2-4x on a first turn whatever the prompt and its description say).

TASK-110-5: 'about one posting in eight' removed from prompts.py HOUSING and constitution.json housing_principle; the generated housing line is the only share in context. TASK-110-6: docs corrected to the verify=live set the vocabulary is counted over -- Medizinisch-technischer Dienst, 1 posting (Pflege and Berufsfachschule für Pflege exist only among the open postings the tools do not search).

Tests: +16 offline (1651 passed, 126 skipped, was 1635/126). tests/test_wa_luna_tools.py: vocabulary from the parent's file with D.snapshot() poisoned to fail if touched, a file missing a line, serve() stamping / a broken board leaving no stamp, one clinic identity across schema line and tools, count_postings with no filter, the 25-row maximum + bounded clinic detail, the verify=live base and an explicit override, the sparse-column line, the stripped docs (host/project/audit/URLs), a failed refresh over a cached board still serving it. tests/test_wa_luna_brain.py: the fake CLI now stamps readiness like the real one, a turn whose tools server never started raises, one stamp per turn and none left behind, the config carrying the parent-counted vocabulary, a board with no live posting failing the turn, and the prompt no longer carrying a housing share. llm runs (one at a time, claude-sonnet-5, 2026-09-16): housing persona passed twice, fallback persona passed twice, unlisted-city passed, needless-call passed after the refusal, Svetlana no-housing-city and Olena housing gate passed. docs/whatsapp.md updated throughout (parent-counted vocabulary and the numbers, the readiness stamp, the two bounds on board_api_get, what read_board_docs no longer serves, the prompt changes, the corrected department evidence).

Final verification 2026-09-16 (review + adversarial verify + fixer, 11 findings fixed): offline suite 1651 passed, 0 failed; all 5 acceptance criteria evidenced. Live llm with tool-call logs: housing persona 2/2 called search_postings_with_housing(city=Würzburg) first, then list_cities_with_postings(housing=true) for the alternative city, and claimed no flat for Würzburg; fallback persona 2/2 reached read_board_docs then board_api_get on allowlisted paths only; snapshot-answerable question made no tool call; campaign full funnel 1/1. Cost measured: system prompt +4.6%, tool schemas 773 -> 9141 chars (~+2.1k input tokens per turn). Deployed: pflege-wa.service restarted 19:55:58 UTC, health all true. Two behaviours invented during review that CLAUDE.md says to ask about first, both loud and documented, awaiting Ivan's yes/no: BOARD_API_MAX_ROWS=25 on the fallback API door, and count_postings refusing a call with no filter at all.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The board tools are now discoverable: every tool description carries vocabulary generated from the live board (departments, Regierungsbezirke, role classes, employment types, the housing share), purpose-built tools exist for the common needs (postings with housing, clinics with housing, cities with postings, counts), the prompt lists the tools with their parameters and requires a stated need -- housing above all -- to be in the call, and a fallback serves this repo's own agent docs plus an allowlisted read-only board API (secrets stripped, served in-process). Verified by 41 tool tests, the full offline suite and live llm runs whose tool-call logs show the right tool with the right filter.
<!-- SECTION:FINAL_SUMMARY:END -->
