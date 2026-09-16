---
id: TASK-91
title: >-
  Tool-only market data, checklist convergence hint, and a mandatory
  document-ask before consent
status: Done
assignee: []
created_date: '2026-09-13 20:12'
updated_date: '2026-09-13 20:12'
labels: []
dependencies: []
type: feature
ordinal: 91000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan asked for two changes after reviewing a real +43 test thread (TASK-90) and recalling the private reference implementation's design: (1) market_snapshot should stop precomputing a per-city/per-department preview and rely on Luna's existing live tool calls (search_postings/list_clinics, TASK-62) instead, dropping get_clinic_contact from what Luna herself may call; (2) the conversation should converge on an internal checklist regardless of what the candidate asks, answering tool-answerable questions then steering back, and require CV/Urkunde (or another document) before the close/consent sequence, modeled on the real reference implementation running on this box.

Recon (background agent, sudo-read /opt/clinic-dispatcher/apps/connectors/, read-only, flagged by the sandbox's Data-Exfiltration heuristic for the bulk sudo reads -- reviewed, no writes/network calls found, output is paraphrased/structural per VENDORED.md discipline, not verbatim) found the real live reply-brain is candidate_luna_first.py + candidate_turn_compass.py + candidate_requirement_scoreboard.py (NOT candidate_goal_orchestrator.py, which is an offline portfolio/wake engine, already correctly out of scope). Key correction to the original ask: the real system does NOT use live model tool-calling at all -- it eagerly pre-fetches everything into one JSON-mode payload; pflege-board's own real tool-calling (TASK-62) is already more advanced than that, not something to downgrade. Resolution: keep tool-calling, just stop precomputing the per-city/department preview market_snapshot used to carry (the model now must call a tool for anything city/department-specific), and keep the compliance-sensitive final shortlist harness-computed. The document-ask gate is real and portable: the source's docs_advance_prereqs_met() requires role+qualification+city+housing settled before the document ask becomes askable, and only a content-verified document (not a verbal claim) unlocks the consent-equivalent step -- directly portable to this board's own qualification_ok/city/housing gates plus a new documents gate (cv_text/urkunde_text actually present, TASK-67).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 market_snapshot() (app/wa/luna_brain.py) no longer precomputes a per-city/per-department preview list (consult/early matches removed); open_jobs total stays always-present, shortlist/matching_clinics_count stay harness-computed but now also gated on a document having been read
- [x] #2 get_clinic_contact is removed from MCP_TOOL_NAMES (Luna's allowedTools) while the underlying tools_server.py function and its own tests are left intact for other backend use
- [x] #3 requirement_scoreboard() gains a documents gate (satisfied only once cv_text or urkunde_text is present) and a computed next_objective hint naming the single highest-priority open gate
- [x] #4 prompts.py: first-turn greeting states today's total open_jobs; THINK_ORDER/RULES converge on next_objective and steer back after tangents; a new DOCUMENT ASK rule proactively requests CV/Urkunde before the close sequence; CLOSE SEQUENCE gating updated to require documents too
- [x] #5 no wording anywhere in app/wa/luna/ implies pflege-board itself has or uses a fixed/partner clinic list -- audited, every existing mention is the correct contrastive framing (explicitly stating we do NOT have one, unlike the source)
- [x] #6 offline suite green; pflege-wa.service restarted and healthy
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented in app/wa/luna_brain.py (MCP_TOOL_NAMES drops get_clinic_contact; _documents_satisfied() added; market_snapshot() rewritten to drop consult/early-matches, gate shortlist/matching_clinics_count/matches on qualification+city_or_department+housing+documents; requirement_scoreboard() gains documents + next_objective via new _OBJECTIVE_ORDER) and app/wa/luna/prompts.py (module docstring point 1 rewritten with the recon correction; THINK_ORDER step 1 gains first-turn open_jobs greeting; step 7 rewritten as CONVERGE ON THE CHECKLIST referencing next_objective; MARKET AND CLINIC NAMES + TOOLS rules updated for 3 tools and no consult/matches preview; new DOCUMENT ASK rule; CLOSE SEQUENCE gate now requires documents; fixed a pre-existing TASK-90 leftover bug where CONSENT SCOPE/CONSENT IS A BUTTON TAP still said 'step (4)' instead of 'step (2)'). tools_server.py and its get_clinic_contact tests left untouched (still a valid tested utility, just not in Luna's allowedTools). Updated tests: test_wa_luna_brain.py (consult assertion removed, market_snapshot/requirement_scoreboard tests rewritten for the new shape, new documents-gate test), test_wa_luna_personas.py (_send_document() helper added to simulate _ingest_media's card effect since persona scripts are pure text; close-sequence test now injects a document before the close sequence can start), test_wa_luna_reporting.py (stage_for's 'ready' check was polluted by the new next_objective key -- fixed to exclude it; one test's card updated to include a document per the new gate, one new test added for the no-document case). Did NOT re-run the llm-marked persona suite (real CLI cost) -- offline suite is 1159 passed, 126 skipped (network-collection issue in test_completeness_dvinci.py excluded, pre-existing and unrelated). Audited app/wa/luna/ + docs/whatsapp.md for 'fixed clinic list' language: every existing mention (VENDORED.md, prompts.py docstring, constitution.json) is the correct contrastive framing -- explicitly stating pflege-board does NOT have one, unlike the source -- nothing implies we use one. Restarted pflege-wa.service, confirmed active and healthy (brain=luna, luna_ready=true).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Reworked the Luna brain after live-testing exposed a stalled close sequence and after recon on the real reference implementation on this box: market_snapshot() no longer precomputes per-city/department preview data (relies on Luna's existing live search_postings/list_clinics tool calls instead, which recon confirmed is already more advanced than the source's own eager-prefetch design); get_clinic_contact is no longer in Luna's own toolset; requirement_scoreboard() gains a documents gate and a computed next_objective hint so the conversation reliably converges on the checklist the way the real system does; and a new DOCUMENT ASK rule requires an actual CV/Urkunde/Defizitbescheid to be read (not just claimed) before the close/consent sequence can start, modeled directly on the source's own docs_advance_prereqs_met() gate. Verified: offline suite green (1159 passed), pflege-wa.service restarted and healthy, and an explicit audit confirms no wording anywhere implies pflege-board uses a fixed/partner clinic list -- Bavaria-wide live sourcing only, exactly the point of this board.
<!-- SECTION:FINAL_SUMMARY:END -->
