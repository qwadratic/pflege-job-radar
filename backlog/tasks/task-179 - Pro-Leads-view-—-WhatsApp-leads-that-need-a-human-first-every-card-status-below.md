---
id: TASK-179
title: >-
  Pro: Leads view — WhatsApp leads that need a human first, every card status
  below
status: In Progress
assignee:
  - '@pflege-fe'
created_date: '2026-09-29 22:06'
updated_date: '2026-09-30 22:54'
labels:
  - frontend
  - whatsapp
dependencies: []
references:
  - 'https://github.com/qwadratic/pflege-job-radar/pull/1'
  - web/pro.template.html
priority: high
type: feature
ordinal: 163000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-29: the Pro dashboard needs a WhatsApp view whose first job is to keep the leads that need human intervention in sight, and whose second job is to show the status of every other card (the "green" ones running on Luna). Until now nobody could see this without reading wa.sqlite by hand on tasker-dispatcher-01.

Data comes from the WhatsApp harness (app/wa/*, PR #1 qwadratic/pflege-job-radar from the wa-harness session, branch feat/whatsapp-harness). Ivan decided on 2026-09-29: topology B (the harness serves a token-gated read API over HTTPS; the board proxies /api/wa/* server-side with WA_API_BASE / WA_API_TOKEN), Pro stays owner-gated (Valentyn gets the passphrase too), and phone numbers are only ever shown masked with the last 4 digits visible. The backend half (proxy, Pydantic models, owner gate, deny tests) belongs to wa-harness; this task is the web/ half.

"Needs a human" is not invented here: the harness already names it. card._escalated with a code from the closed list in app/wa/luna/escalation.py (Ivan 2026-09-22: a predictable list, never model free text), stuck_reply (ball on us longer than WA_STUCK_REPLY_HOURS), wa_send_failures, wa_inbound_pending, and a consented card (funnel stage submitted), which app/wa/queue.py hands to a human. TASK-316 (P4 lead status, harness branch) will add an evaluator status plus reason later; the view must take it without a redesign.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The Leads view in /pro (#/leads) shows every thread that needs a human before anything else, one row per thread, with each reason named (escalation code, stuck reply, send failure, unprocessed inbound, consent given), since when, and a link into the thread
- [x] #2 Every other lead appears below in one board with its funnel stage, gate scoreboard, whose turn it is, rail and last activity; a lead is green only when none of the named reasons applies, and the page states that rule
- [x] #3 A thread opens in a side panel with the card gates, documents (metadata only) and the message history; drafts, deleted messages, audio and button messages are visually distinct; no raw phone number appears anywhere, only phone_masked
- [x] #4 The Leads nav item shows the number of leads needing a human on every Pro page, the tab title shows it too, and data refreshes by polling (list 15 s, open thread 5 s, nav badge 60 s)
- [x] #5 When /api/wa/* is unreachable or answers an error, the view says so with the status and what is missing, never an empty list; ?mock=1 renders deterministic synthetic fixtures that cover every state
- [x] #6 All strings exist in DE and EN, the view is keyboard operable (rows, filters, panel close with Esc) and usable at 375 px width
- [x] #7 docs/wa-dashboard.md documents the exact /api/wa/* response shapes the view reads, and the wa-harness session has them
- [x] #8 Every lead, in the needs-a-human list and in the board, shows its card as one line in the harness's gate order (region, qualification, town or department, housing, CV, certificate, consent): what we know per gate, the current step (the first gate not satisfied) marked, later gates dim; within one status the board lists the lead that has got furthest first (Ivan 2026-09-30: green is green at different stages)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read the harness code on PR #1 (app/wa/api.py wa_threads, store.py schema, luna/escalation.py, luna/reporting.py, luna_brain.py requirement_scoreboard/FUNNEL_STAGES, queue.py) so the contract matches real fields.
2. Write docs/wa-dashboard.md: list/detail/messages/health shapes, attention rules, masking, polling, error states.
3. Build #/leads in web/pro.template.html: attention section (grouped by tier), all-leads board (funnel bar, filters, table, mobile cards), side panel (gates, documents, chat, live 5 s), nav badge + title count, DE/EN.
4. Add deterministic ?mock=1 fixtures for /api/wa/* covering every escalation code, system failure, handoff, flags, stopped/suppressed, test thread and every message kind.
5. Rebuild web/pro.html, run the app locally with AUTH_DISABLED, check desktop + 375 px screenshots, keyboard, console errors; run the web test suite.
6. Commit, push branch, draft PR; send the contract to wa-harness; host a preview for Ivan.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Built the web/ half of the Pro Leads view (2026-09-29, pflege-fe session):
- web/pro.template.html (+ rebuilt web/pro.html): new nav item Leads (#/leads) with a red badge = leads needing a human, also shown in the tab title and refreshed every 60 s on every Pro page. The view has a health strip (harness readiness, WA_REPLY_SCOPE, WA_AUTOSEND, transport, rails), the "Needs a human" list grouped Escalated / Reply stuck / Handoff to clinics (longest waiting first) plus a collapsed Checks group, then All leads: a funnel (7 FUNNEL_STAGES + ended, each with its needs-a-human count), a status filter, search by number ending / town / department / code, and a sortable table with the gate scoreboard as 7 squares. A row opens a non-modal side drawer: why a human is needed (codes + the harness notes), the card (stage stepper, gates with values, the internal next_objective), the handoff, documents (metadata only), and the conversation (drafts dashed with the scope_refusal, forgotten messages as tombstones, voice notes, buttons, delivery ticks, load older, 5 s after_id poll).
- Decisions: the view adds no reason of its own. Manager = escalation_codes (closed list in app/wa/luna/escalation.py) or lead_status red; Reply needed = stuck_reply / last_send_error / pending_inbound; Handoff = stage submitted or a wa_queue_candidates row. Flags are shown but never counted, stopped and suppressed threads never count, test threads are hidden by default and never counted. A draft refused by WA_REPLY_SCOPE is not a reason by itself (a non-owned thread is answered by the other system) and is explained in the health strip instead. The lead_status mapping is provisional until TASK-316 names its statuses.
- Contract: docs/wa-dashboard.md (also listed in the Pro Docs tab through docs/index.json). The list is read page by page until next_offset is null. A page without next_offset is a loud error (it would otherwise pass a truncated list off as the whole board), and no limit is sent. 404 / 401-403 / 502-504 / other each get their own error card with the status; none renders as 0 leads. A failed refresh keeps the last data under a red line naming the error and the time of the data.
- api() in the SPA now attaches the HTTP status to its Error (err.status), so pages can tell those cases apart.
- ?mock=1 demo: 28 invented threads (every escalation code, all three reply-stuck signals, 2 handoffs, 3 checks, an evaluator red, every ended state, one test number) with realistic German conversations; Luna answers one owed thread 8 s in, so the drawer poll has something to pick up.
- Verification: tests/test_web_leads.py 15 passed (counts, grouping and order, checks not counted, green rule stated, filters, no raw phone number anywhere, drawer content and every message kind, Esc closes and drops t= from the URL, live poll, phone width, 404/502/403 error cards with the ! nav badge, a quiet two-page board, the missing next_offset error). tests/test_web_responsive.py now includes #/leads (390 and 1440 px); together with tests/test_web_clawl.py: 50 passed. Mutation check on the built page: counting the test number, counting flags, ignoring STOP, dropping the end-signal check and dropping stuck_reply each turn the suite red. Full offline suite (-m "not network and not completeness and not mutation"): 1490 passed; the 10 failures need PFLEGE_INGEST_URL / FIRECRAWL_API_KEY or a career_profiles table and touch no file of this change.
- Build note: web/pro.html was rebuilt with SUPABASE_URL=https://supabase.int.exe.xyz, the value the committed build already carried (it only fills the ai-api meta tag). web/.env.build says the public project URL is the right one; that mismatch predates this task and is left alone.
- Preview for Ivan (invented data): https://claude.ai/artifact/2RcBVgEGx5FYR97sdYjbdH
- Still open, not in this task: the board-side proxy and the /api/wa/threads/{id}, /messages and paged list endpoints (wa-harness, PR #1); exposing the harness host over HTTPS (nginx + sudo, Ivan); an escalated_at timestamp on the card (without it the view says "last message X ago" for escalations); a handoff-done state (today nothing marks a handoff as done, so consented leads stay in the list); the TASK-316 status enum. Task-ID note: the harness branch carries its own task-160..321, which overlaps this task-179 and main's 160-178.

2026-09-29 22:51 UTC, contract hand-off to wa-harness: sent over Remote Control (message 53f68213-128a-4917-aae9-ce6db0e55873). Contents: PR #2 and docs/wa-dashboard.md; what the view counts as needing a human (escalation_codes or lead_status red; stuck_reply / last_send_error / pending_inbound; stage submitted or a handoff row; flags shown but never counted; stopped, suppressed and test threads never counted); the list envelope and row fields, /threads/{id}, /threads/{id}/messages with before_id/after_id, /health; the error codes the proxy should use (404 = proxy missing, 401/403 = owners only, 502-504 = harness not answering). Findings passed on: (A) PR #1 mounts the app/wa routers inside the board app, and on the board VM wa.sqlite would be a fresh empty file, so /api/wa/threads would answer 200 with 0 rows and the view would claim nobody needs a human. Under topology B the board must serve /api/wa/* only through the proxy and answer 503 when WA_API_BASE is unset, and envelope.source should name the harness. (B) Nothing marks a handoff as done (wa_queue_candidates.status is only 'queued'), so consented leads stay listed. (C) Today's /wa/threads clamps the limit to 500 and returns no next_offset, which the view refuses by design, so the paged list must have no cap. Also asked for escalated_at, the TASK-316 status enum, keeping both docs/index.json entries (whatsapp.md first), and a renumbering of one side of the task-ID overlap (harness branch task-160..321 vs main 160-179). The dispatcher machine was offline, so delivery is queued and not confirmed. AC #7 stays open until wa-harness acknowledges.

2026-09-29 ~23:00 UTC, wa-harness acknowledged the contract (AC #7 checked). It will build the harness API against docs/wa-dashboard.md as written. Trap A is confirmed: it removes the unconditional /api/wa/* router mount from app/main.py before Ivan merges PR #1, so on the board /api/wa/* exists only as the proxy (503 when WA_API_BASE is unset, never a local DB), and envelope.source will be "harness@tasker-dispatcher-01". Paging: the new endpoints have no cap, and next_offset is always present and null on the last page. Trap B: no handoff-done status in phase 1, because phase 1 is read-only by Ivan's decision and marking a handoff done is a write. wa-harness asks Ivan; until then a consented lead correctly stays in Needs a human. The lead_status enum follows when P4 lands, and the provisional mapping (red = Manager, other non-green = check) is accepted. docs/index.json: keep both entries, whatsapp.md first. Task IDs: wa-harness's lane is 163, 164, 167, 173, 174, 175, 177, 180 and 181..391, and it asked this branch to take IDs >= 392 on rebase. TASK-179 is outside that lane, so it keeps its number. The real overlap is the board-side tasks 163-178 and 180 (data-quality tasks from other sessions). They are not on origin/main (which ends at 162) and not in any commit in this repo; they sit as files in the shared checkout's working tree. Flagged to Ivan, not renumbered here. The harness fixtures JSON and base URL follow once its endpoints exist; exposing the host (nginx, sudo) is Ivan's step.

2026-09-29 ~23:10 UTC: wa-harness agreed that TASK-179 keeps its number and does not take 179. Its new tasks are TASK-392..400 on the PR #1 branch. The backend half of this view is TASK-395 (Pro WA API). Its AC #6 ships the fixtures JSON and the Pydantic models into the repo on the PR #1 branch, and closing this task means checking those shapes against docs/wa-dashboard.md. wa-harness also reports the board-VM working-tree collision (tasks 163-178 and 180) to Ivan.

2026-09-30, Ivan's feedback on the preview: the card has a sequence the conversation follows, the view should show it, green can be green at any stage, and every lead should show what we know about it (its wishes). Done in web/pro.template.html (+ rebuilt pro.html):
- New waFacts(r) / waChain(r): the card as one line in requirement_scoreboard's gate order (read from luna_brain.py on the PR #1 branch: region, qualification, city_or_department, housing, cv_document, qualification_document, handoff_consent; the funnel stage is the first gate not satisfied). A satisfied gate shows the value from the card summary (Bayern, Defizitbescheid, München · Intensiv, ohne Wohnung / Wohnung für 2 · auch ohne, Fach egal for department_pref flexibel); CV, certificate and consent show their own name. The current step is outlined and can carry a partial answer: 'Wohnung ja, für wie viele?' (housing_needed true, people_count open) and 'Einwilligung angefragt' (anonymous_send_offered). Blocked is red; an ended thread has no current step. Screen readers get the state before each value; each item has a tooltip naming its gate.
- The line replaces the unlabeled 7 squares and the Wunsch column in the table (new column Karte = stage + since + the line) and the context text in the needs-a-human list. The side panel uses the same facts next to each gate name; the region gate is now labelled Region (the panel read 'Bayern | Bayern'), the certificate gate Nachweis / Certificate to match the Nachweise stage.
- Default sort: within one status, the lead furthest along first, so green reads consent down to contact. The green rule text now says green says nothing about progress, the card does.
- Contract (docs/wa-dashboard.md): new section 'Every card in conversation order'; the card summary gains housing_flexible and anonymous_send_offered, and department is card.department_pref with 'flexibel' = any. Sent to wa-harness.
- Demo data (?mock=1): each thread is now its card. Gates are derived from the card the way requirement_scoreboard does, the stage is the first open gate, and the conversation is built from the card in Luna's order with one plain yes/no or open question per gate (the old script asked an either/or qualification question, which TASK-200 forbids). The old demo let cards and chats disagree (card Passau, chat Nürnberg).
- Verification: tests/test_web_leads.py 17 passed (2 new: the card line of 5 leads against literal expected values incl. partial answers and a blocked gate; green rows ordered consent, documents, CV, matching x3, qualification x2, contact). Mutation check on the built page: 9 of 9 mutations turn the suite red (4 new: no current step, green not ordered by progress, housing_flexible dropped, consent asked dropped). Screenshots at 1440 and 390 px, DE and EN: no horizontal overflow, no page errors. Preview republished at the same URL (version 3).
<!-- SECTION:NOTES:END -->
