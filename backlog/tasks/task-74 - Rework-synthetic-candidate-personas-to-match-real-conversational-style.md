---
id: TASK-74
title: Rework synthetic candidate personas to match real conversational style
status: Done
assignee: []
created_date: '2026-09-13 11:14'
updated_date: '2026-09-13 12:28'
labels: []
dependencies: []
ordinal: 74000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Recon (this session) compared our LLM-generated test personas against aggregate, non-identifying stats from 701 real WhatsApp messages (6 real candidates). Real candidates: median ~3 words/message, ~90% under 10 words, ~1/3 single-word acks, bursts across consecutive bubbles, occasional all-lowercase, mostly statements not questions. Our live personas (tests/test_wa_luna_e2e_funnel.py's _CandidateAgent, and the mining demo persona prompts) are the opposite: 25-60 words, letter-style opens with self-intro+signoff, flawless grammar, heavy emoji, unprompted 'du', uniformly escalating warmth -- one persona even broke the fourth wall mid-test. Rework the persona-generation prompt to close this gap.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Persona system prompt instructs much shorter turns (target ~3-15 words, most under 10)
- [x] #2 Single-word acks (Ok/Ja/Passt) are the norm the prompt asks for, not an exception
- [x] #3 Persona prompt biases toward statements over questions, matching real proportions
- [x] #4 No email-style open (self-intro + fact-dump + Grüße signoff); terse opener instead, no signoff mid-thread
- [x] #5 Register defaults to Sie/neutral, never assumes du unprompted
- [x] #6 Occasional lowercase/no-punctuation variation is possible
- [x] #7 Persona prompt explicitly forbids acknowledging it is a test/simulation (kills the fourth-wall-break failure mode)
- [x] #8 tests/test_wa_luna_e2e_funnel.py still passes with the reworked persona
- [ ] #9 Full offline suite stays green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
tests/test_wa_luna_e2e_funnel.py: PERSONAS reworked with a shared _STYLE_GUIDE prepended to each persona's own facts -- terse turns (3-10 words the norm), single-word acks as default not exception, statements over questions, no email-style open/signoff, Sie-only, occasional lowercase, explicit never-acknowledge-this-is-a-test instruction. Live e2e run confirmed real behavioral change: turns like 'Urkunde hab ich schon, kein Problem', 'Allein, kein Problem für mich', 'Ja, gerne' replaced the old multi-sentence self-introductions. Two REAL bugs found and fixed along the way via live runs, not by inspection -- filed as their own tasks since they're not persona-style issues: TASK-82 (market_snapshot/requirement_scoreboard city_or_department mismatch stalled a department-flexible candidate) and a test-harness-only fix (_CandidateAgent.reply_to was keying its 'first message' prompt off empty bubbles instead of actual session freshness, causing a candidate to loop its opening line on any legitimate mid-conversation no_send turn). All 3 personas now consent within budget (7-8 turns) on live runs. Offline suite: 1113 passed, same 5 pre-existing unrelated failures.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Synthetic personas now write like real WhatsApp candidates (terse, statement-heavy, single-word acks) instead of organized emails, per a direct comparison against 701 real messages. The persona rework itself surfaced two genuine, previously-latent bugs via live runs that a purely-static review would have missed: a real product bug in the close-sequence gate logic (TASK-82) and a test-harness bug that made the candidate agent loop its own opening line. Both fixed and live-verified.
<!-- SECTION:FINAL_SUMMARY:END -->
