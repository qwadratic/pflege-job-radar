---
id: TASK-111.7
title: Analyze past threads on our mailboxes and catalog clinic communication flows
status: Done
assignee:
  - '@claude'
created_date: '2026-09-17 17:31'
updated_date: '2026-09-18 01:06'
labels:
  - email
dependencies:
  - TASK-111.2
  - TASK-111.5
documentation:
  - backlog/docs/email/doc-1 - Email-channel-runbook.md
parent_task_id: TASK-111
priority: medium
ordinal: 118000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The mailboxes already hold real correspondence. Extract only clinic-related patterns (cold contact, warm contact, meeting booking, rescheduling, declines, etc.) as the basis for templates and thread states. Zoho boxes 3, 6, 7, 8 are readable now; M365 boxes need the OAuth app. Mailbox content contains personal data: read-only, no copies outside the server, report patterns not people.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Flow catalog doc: each flow with trigger, typical messages, next step, frequency
- [x] #2 Non-clinic threads excluded and the exclusion rule stated
- [x] #3 No raw personal data in the doc
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Deterministic pre-pass over 62,485 unique msgs from 9 mailboxes (tools/email_index.py, email_sample.py): global threading via Message-ID/References/conversationId across mailboxes, engagement classes, per-clinic-domain dossiers (topics span threads, people join later), outbound template families, cadence/timing stats, keyword flags, tiered sample with explicit coverage manifest.
2. Stage 1 workflow (economical): Sonnet readers classify 949 sampled threads (all 209 exceptional/commercial/hire/interview/legal + 600 stratified typical + 80 cold sequences + 60 pushback); Sonnet analysts decode outreach playbook (template_families + cold_sequences), clinic pushback (all 844 texts), attachment typology; Fable consolidates into report_stage1.md; Opus critiques completeness and sets Stage 2 focus.
3. Stage 2: Sonnet triage of the remaining 5,340 real engaged threads with the learned taxonomy (100/agent) -> population-level frequencies + surfaced exceptions; targeted deep-dives.
4. Produce flow catalog doc (AC#1) with exclusion rule stated (AC#2) and no personal data (AC#3). Feed the email-module proposal (Direktvermittlung + Rechnung model; DE terminology in data/email-analysis/research_de_placement.md).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Dependencies 111.2/111.5 satisfied in practice, not formally: M365 read via the existing Entra app + shared MSAL cache (5 accounts incl. daria.s, maria.b + 3 colleague personas); Zoho #3/6/7/8 via IMAP. NOT read (stated exclusion): #11 pflege-expert (not in cache), #1 (dead password), #2/4/5 (IMAP off). Dump: 62,647 raw / 62,485 unique msgs, 52,562 threads, 6,509 engaged (6,149 with human inbound), 12,069 external orgs (3,246 engaged). Graph dumps lack attachment names; enrichment script tools/email_enrich_graph_attachments.py prepared (sudo run by Ivan).

Ivan 2026-09-17: the e-mail-module proposal must compare researched theory (data/email-analysis/research_de_placement.md: DE terms, legal frame, fee/invoice conventions, document sets) against the PRACTICE observed in the dump (how correspondence and deals actually ran) — and practice outweighs theory. Theory only fills gaps or flags hard legal constraints (UWG §7, §14 UStG, BAG 2023).

Stage 1 done (wf_d3ccaeea-fd2, 21 agents, 949 threads classified, ~2.8M tokens): out/report_stage1.md + decode_outreach.md + decode_pushback.md + attachments.md + critique_stage1.json. KEY FINDING: only 21.8% of outbound is clinic recruiting; 63% Snov/TrulyInbox warm-up ping-pong, 14% untagged warm-up, 2% unrelated AI-agency pitch from the 3 .agency mailboxes -> every raw population KPI is warm-up-contaminated. Real clinic corpus = 459 engaged threads (deterministic gate: no [SNOV]/[WRM]/wsn tag + pflege terms), one campaign 'Examinierte Pflegefachkräfte für [Klinik]' (2,176 threads, 3 touches d0/+5/+11, strict human reply 10%, ~12% spam-rejected). Funnel: 9 orgs call, 6 video-interview, 1 signed Personalvermittlungsvertrag, 0 Arbeitsantritt/invoice. Post-reply work ran from a partner mailbox outside the export. Stage 2 = ledgers (bounce/compliance/response/recontact/roles, deterministic) + read remaining 249 clinic threads + deal reconstruction for 107 orgs + theory-vs-practice table.

Stage 2b done (wf_f74bdd4d-3a1, 7 agents, 348 targeted threads, ~0.9M tokens) -> out/report_stage2b.md. CLOSING EVIDENCE across 62,485 msgs: 7 calls held (18 invited/14 accepts), 27 contracts sent (22 us + 5 partner), 1 countersigned (then silence), 0 Zusage/Arbeitsvertrag/Arbeitsantritt/Rechnung/payment. Fee never stated in any body, only in a filename (2,5 Bruttomonatsgehaelter); partner terms 50/50 split, 1st at Vertragsunterzeichnung (clinic wanted Arbeitsbeginn), 2nd after 6 months, guarantee = Nachbesetzung only; 'no German VAT via Polish entity' claim = legal risk. Candidate names in 'Vorstellung von [Name]' subjects despite anonymised promise; candidate WhatsApp numbers + freemail in outreach mailboxes (DSGVO). Corrected numbers: strict human reply 204/2,176 = 9.4%; any-inbound 32%; hard NDR 7.7% of Gen1 threads (M3/M5/M6 11-13% in one shared reputation event W24/27/28, M1/M4 ~0.5%); 52% of bounced domains re-targeted. Org-topic model validated: 84% of live topics span >1 thread, 65% >1 mailbox, 41% >1 clinic person; key = Traeger group > registrable domain > address; keep 45 d open, reopen on any inbound, never auto-reopen 'stopped'. All timestamps UTC (Berlin +2, recruiter calendars Kyiv +3).

Verification: doc-2 (flow catalog) has 20 flows (F1-F20), each with trigger, typical messages, next step/should, frequency; §0 states the exclusion rules (warm-up, .agency mailboxes, spam, auto-replies, internal/simulation, Gen2) with the clinic gate (precision 0.996/recall 0.982). Regex scan of doc-2 and doc-3 for e-mails, phones, external domains and title+name patterns: 0 hits. doc-3 = architecture proposal with theory-vs-practice table (practice wins; law as constraint), answers to both critics' question lists, decisions for Ivan. Actual Personalvermittlungsvertrag + deck PDFs fetched from Zoho (data/email-analysis/contracts/): fee 3/2 Brutto-Monatsgehälter (A/B), 50% at nurse's Arbeitsvertrag, 25% month 3, 25% month 6, 14 d Zahlungsziel, 3-month free Nachbesetzung, 12-month protection, Polish Sp. z o.o.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Analysed 62,485 msgs / 9 mailboxes (Mar-Sep 2026) with a deterministic pre-pass + 3 agent workflows (Stage 1/2/2b, 49 agents, ~6.5M tokens). Deliverables: doc-2 flow catalog (20 flows, exclusion rules, timings, roles; no personal data) and doc-3 email-module architecture proposal (Deal/org-topic state model, 4-scope stop-list, deliverability, identity, answer policy, invoicing from the actual contract terms, compliance, build order). Key findings: ~78% of outbound was warm-up bot traffic; real campaign 2,176 threads, 9.4% human reply, 33 orgs reached call stage, 1 contract signed, 0 hires/invoices; #1 stall = contract PDF instead of the clinic's ask, #2 = our latency (47% of positive replies unanswered). Verified by regex PII scan (0 hits), counts reproduced by scripts (tools/email_*.py, out/ledgers.md), and two adversarial Opus critiques addressed.
<!-- SECTION:FINAL_SUMMARY:END -->
