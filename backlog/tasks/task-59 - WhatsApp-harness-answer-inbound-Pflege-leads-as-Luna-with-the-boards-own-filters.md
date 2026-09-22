---
id: TASK-59
title: >-
  WhatsApp harness: answer inbound Pflege leads as Luna with the board's own
  filters
status: Done
assignee:
  - '@claude'
created_date: '2026-09-11 17:19'
updated_date: '2026-09-22 06:10'
labels:
  - whatsapp
  - luna
  - harness
dependencies: []
references:
  - docs/autopilot.md
  - >-
    tasker-dispatcher-01:/opt/clinic-dispatcher/apps/wa_inbox (source of the
    copied behaviour)
ordinal: 59000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Nurses reach us on WhatsApp from Meta ads and landing pages. Today the replies come from Luna/Valentina inside the clinic-dispatcher monorepo on tasker-dispatcher-01 (apps/wa_inbox on the Meta Cloud API), which pflege-board cannot import, and the autopilot proof of concept in this repo is a simulation that sends nothing. The board already knows every open experienced-level nursing job at every Krankenhausplan site with rich filters (Regierungsbezirk, city, role class, department, employment type, shift, housing, tariff), so it can qualify a lead and hand over real openings itself. This task copies the existing Luna/Valentina behaviour into one self-contained subproject here, terse (short WhatsApp bubbles, one question per turn) and driven by the board's filters, so a lead gets useful matches in a few turns. Scope is the minimal harness: transport, persona, question ladder, matching, opt-out. No clinic-side outreach, no document collection, no approval queue.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Meta webhook verification (GET with hub.challenge) and inbound POST parsing for text and button replies work against recorded fixtures, and a payload with a wrong X-Hub-Signature-256 is rejected
- [ ] #2 The first message from an unknown number gets a Luna-style greeting plus exactly one question; every later turn asks at most one question and each bubble stays short
- [ ] #3 Once region, role and department (or a free-text equivalent) are known, the reply lists real matching postings from the board data with title, clinic, town and source link, using the same filters as GET /api/jobs
- [ ] #4 STOP or an equivalent opt-out ends the conversation and no further message is sent to that number
- [ ] #5 All outbound messages go through one send function; missing credentials or a failed provider call raise and are recorded, never reported as sent
- [ ] #6 Tests run offline and cover verification, inbound parsing, the question ladder, matching and opt-out
- [ ] #7 A docs page, a README layout entry and a deploy unit describe how to run the harness and register the webhook
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read the production bot on tasker-dispatcher-01 (apps/connectors/meta_whatsapp_cloud.py, candidate_whatsapp_webhook.py, candidate_luna_first.py, pflege_jobs_match.py, configs/recruitment_funnels/*.json) and keep transport + persona + style rules, drop the 250KB reply brain.
2. app/wa/ as one self-contained package: config.py (META_WHATSAPP_* env, same names as production), meta.py (signature/challenge copied verbatim, trimmed client for text + reply buttons), store.py (data/wa.sqlite, wamid UNIQUE for Meta redelivery, stopped flag), slots.py (slot vocabulary, production department aliases), brain.py (the turn function), api.py (routes), asgi.py (own process).
3. Conversation: slots are board filters; the next question is the unfilled slot whose answer would narrow the current result set most (coverage x (1 - top-value share)), not a fixed script. Two bubbles, one question, enforced by an assertion. Urkunde/Defizit/Kenntnispruefung gates the handover like the production constitution does.
4. Reuse app/data.py:filter_jobs in-process rather than calling the HTTP API, so the harness and GET /api/jobs cannot drift.
5. Mount the router in app/main.py (dev) and ship deploy/pflege-wa.service (production, port 8502); gate GET /api/wa/threads as an owner read in app/auth.py.
6. tests/test_wa_harness.py: offline, snapshot stubbed like tests/test_app_api.py, Meta faked; cover verification, signature rejection, redelivery, the ladder, matching, widening, the qualification block, opt-out and the send path.
7. docs/whatsapp.md + docs/index.json + README layout/run lines + .env.example + .gitignore for data/wa.sqlite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
CLOSED AS SUPERSEDED, 2026-09-22, not done as written. This is the original genesis task for a self-contained minimal WhatsApp harness (Meta webhook, basic question ladder, board-filter matching). What actually got built over TASK-60 through TASK-152 is a categorically different and far larger system: a persistent Claude-session brain (TASK-60/61), an MCP tool server (TASK-62/110/145), campaign sending with full delivery/retry/history semantics (TASK-98-106), a second full transport (the phone rail: TASK-116/117/120/142/143/146/147/148), cross-lane suppression (TASK-113), and dialog-rule enforcement (TASK-144/150/151/152) -- none of which resembles this task's own Implementation Plan (a deterministic slot-filling ladder with no LLM). The task's acceptance criteria (webhook verification against fixtures, one-question-per-turn ladder, opt-out, a docs page and deploy unit) are all satisfied by since-superseding, already-Done tasks rather than by this one: TASK-84 (webhook routing), TASK-113 (opt-out/suppression), TASK-149 (docs), and the existing deploy/pflege-wa.service. Left In Progress with 0/7 acceptance criteria checked and nobody working it -- the harness has been live in production for over a week under a completely different design. No acceptance criterion is checked: this specific implementation plan was never followed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Obsolete as written. The single self-contained minimal harness this task describes was overtaken almost immediately by a much larger system built across dozens of later tasks (TASK-60 onward): an LLM-driven brain, an MCP tool layer, campaign sending, and eventually a second full transport (the phone rail). Every acceptance criterion this task lists is satisfied by later, already-Done tasks instead. Closing to match a tree that has run a completely different design in production for over a week.
<!-- SECTION:FINAL_SUMMARY:END -->
