---
id: TASK-314
title: >-
  P2: One pipe for every model-written message — event turns, one exit check,
  tools over context, correction at close
status: To Do
assignee: []
created_date: '2026-09-26 08:48'
updated_date: '2026-10-07 15:11'
labels:
  - dialog
  - architecture
dependencies: []
priority: high
project: whatsapp
ordinal: 2
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Three separate writers produce candidate-facing text today: the reply turn, the warming turn, and the correction compose on branch task-305x/correction-turn. Each has its own checks. The Opus 5.5 review of that branch (2026-09-25, REJECT) found the correction path:
- skipped the URL ban;
- raced the reply turn;
- sent before saving;
- used the live client instead of the injected one;
- truncated bubbles silently.

Ivan, 2026-09-25/26: design it at harness level, "красиво", high priority for the week of 2026-09-28.

**Decisions** (Ivan, 2026-09-25/26)
1. **Event turn.** A turn is triggered by a candidate message, by a follow-up tick (was TASK-301), or by a lead close (the correction). Code puts an internal event note into the same turn(); the brain answers with its usual prompt and tools. All three triggers share:
   - the queue and the claim;
   - the exit checks;
   - send plus end-of-turn reconciliation;
   - the injected client.

   The correction module shrinks to the fact check.
2. **Per-turn exit checks.** Only three, nothing else:
   - The bubble limit, deterministic. Code allows 5; the prompt tells the model 3 (Ivan 2026-09-26). The gap is a known theoretical risk; recorded here, nothing changed yet.
   - The URL ban, as a deterministic hook on every model-written bubble. It is never an AI gate.
   - The closing gate: Haiku checks that the last bubble hands the turn back; it fails open.

   The per-turn truth filter is gone.
3. **Fact check once, at lead close** (card complete + consent).
   - It reads ALL bubbles we sent. There is no shortcut when the ledger names no posting ids; that was review blocker 2.
   - It checks them against the posting text, the clinic registry facts and the IDEOLOGY block. Firecrawl comes later (P5).
   - Hallucination is normal LLM behaviour ("штатная галлюцинация"), cured at the last risky step. The real risk is that we fooled ourselves.
   - On a false claim: re-match, correct our own records, then send the update.
   - The lead enters the clinic pool in every outcome.
4. **Correction message shape.**
   - Bubble 1: the new vacancy.
   - Bubble 2: the hook, then "the vacancy above replaces the earlier one, which is no longer listed", a line break, then the question.
   - Never an apology, never "we reported incorrectly".
5. **Last-bubble rule, every turn.** The last bubble is the push-notification preview and is read first.
   - It opens with a hook built from what the candidate herself said (city, department, wish) — not from what she merely agreed to.
   - Fallback: an abstract opening about her job search.
   - The question sits after a line break inside the last bubble.
6. **Tools over context.** A model seeing only a posting id is fine, as long as it has tools to fetch the facts:
   - a posting by id, including delisted ones, plus what we told the candidate about it;
   - search;
   - clinic facts.

   The fact-check and correction models get the same tools.
7. **Yellow flag.** Missing data → the model says "no data" internally → a yellow flag on the card, which feeds P4. With the candidate:
   - widen the radius first;
   - then drop one criterion honestly: bubble 1 the offer, bubble 2 which criterion was dropped and that with it there is nothing.
8. **IDEOLOGY block** (already built on the branch). One modular block near the top of the brain prompt, shared with the fact check and the bench judge. It says:
   - the success metric is the probability of a hire;
   - widen the funnel: many candidates, many vacancies;
   - funnel quality still matters, because junk leads spoil clinic relationships;
   - notify and manage expectations, don't be afraid.
9. **Race.** Record only; do not build now.
   - Option: the correction works in memory, and the commit step runs a small check that nothing changed since it started (e.g. a version or marker file); otherwise it redoes.
   - Accepted alternative: correct only once the lead is a full pool candidate.
10. **Partial send.** Covered by end-of-turn reconciliation (Ivan).
11. **Tests.** conftest already scrubs WA_BRIDGE_*, so the live-client finding is defence in depth. The pipe passes the injected client anyway.

**Starting point**
- Branch task-305x/correction-turn, commits 5a59304 and a5c5cd6. Worktree: .claude/worktrees/agent-a448e95672e9aa941, whose data/app.sqlite is a symlink to the main one.
- It already has:
  - per-turn truth rules made record-only;
  - the IDEOLOGY block;
  - compare() and compose() with a clinic registry payload;
  - the bubble knobs C.LUNA_MAX_BUBBLES / C.LUNA_WARMING_MAX_BUBBLES;
  - 7 tests.
- The live close probe took 26.8 s and had the right tone.
- The full review findings are in archived TASK-307.

**Also folded**
- TASK-301: follow-ups become model turns.
- TASK-300: never promise a colleague. The fixed German fallback wording needs Ivan's verbatim approval.
- TASK-250, the remaining expensive tier: governor pacing and ledger idempotency on the bridge's send_gallery. The cheap tier is done, per TASK-250's notes.

**Housekeeping:** remove the real candidate phone number from the app/wa/luna/closing_gate.py module docstring (around line 10).

Folded here: TASK-307, TASK-301, TASK-300, TASK-250. Their full text is kept in the archive.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One turn entry point serves a candidate message, a follow-up tick and a lead close, with the same claim/queue, exit checks, send plus reconciliation and injected client
- [ ] #2 Per-turn exit checks are exactly: the bubble limit (code 5, prompt 3), a deterministic URL hook on every model-written bubble, and the closing gate; no per-turn truth filter remains
- [ ] #3 The fact check at close reads every bubble we sent, and the lead is queued in every outcome
- [ ] #4 The correction arrives as two bubbles, the last opening with a hook and ending with the question after a line break, with no apology (bench scenario)
- [ ] #5 The last-bubble hook rule is in the prompt and judged by the bench on every scenario
- [ ] #6 Tools for a posting by id (including delisted ones, plus what we told the candidate), search and clinic facts are available to the brain and to the fact check
- [ ] #7 Missing data sets a yellow flag on the card, and the candidate-side policy is radius first, then drop one criterion honestly (bench scenario)
- [ ] #8 [TASK-301] The cadence covers every active conversation, not only threads that have gone silent
- [ ] #9 [TASK-301] Each tick builds context from at most the last 30 bubbles verbatim plus a summary of everything older, the card state, and the single open question
- [ ] #10 [TASK-301] Exactly one message is sent per conversation per tick, and it addresses the open question
- [ ] #11 [TASK-301] Silence happens only under mute or an explicit opt-out; every other state produces a message
- [ ] #12 [TASK-301] The reply goes through the closing gate like any other turn, so the last bubble hands the turn back
- [ ] #13 [TASK-301] The old fixed nudge strings are removed, not merely bypassed
- [ ] #14 [TASK-301] A test proves a conversation that never went silent still receives its cadence turn
- [ ] #15 [TASK-300] No candidate-facing string anywhere in the repo promises that a human will look at something or get back to them
- [ ] #16 [TASK-300] Both fallbacks render as a short acknowledgement plus the card's currently open question, keyed on the same gate order requirement_scoreboard already computes
- [ ] #17 [TASK-300] The German wording for every gate is approved by Ivan verbatim and frozen in code with a provenance comment, in the manner of app/wa/broadcast_template.py
- [ ] #18 [TASK-300] The not-placeable case sends the acknowledgement with no question, because the NOT PLACEABLE rule stops the checklist
- [ ] #19 [TASK-300] Escalation still records internally and is visible to the operator
- [ ] #20 [TASK-300] Tests assert the exact frozen strings and that no promise-a-human phrasing survives anywhere
- [ ] #21 [TASK-250] send_gallery gets governor pacing and ledger idempotency, with a test that fails without the fix and passes with it
- [ ] #22 The real candidate phone number is gone from the closing_gate.py docstring
- [ ] #23 One brain answers a test user on both numbers: a reply goes out on the rail its inbound arrived on (Meta webhook -> Meta, handset -> bridge), replacing TASK-220's pin-forever rule; proactive sends use the last inbound's rail
- [ ] #24 [Ivan 2026-10-07, supersedes AC 8 and 11] The follow-up tick is selective: a periodic job picks silent candidates, a model chooses whom to follow up, exactly one message per candidate, no pre-scheduled series
- [ ] #25 [Ivan 2026-10-07] Every card carries next_step (what we expect from her, or the one follow-up planned), set by a brain tool on every run, and shown to the next turn together with the card scoreboard
- [ ] #26 [Ivan 2026-10-07] A candidate with no or pending diploma recognition gets a next_step to ask again in about a week instead of a terminal dead end
- [ ] #27 [Ivan 2026-10-07] No identical reminder can be sent: the follow-up text comes from next_step and the card state, with no fixed nudge constant left
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-27: send-scope kill switches live (b5c78c7): WA_REPLY_SCOPE=test_only (both rails) and WA_META_SCOPE=test_only (Meta channel muted; set to all to re-activate). A refused send is stored as a draft with meta.scope_refusal. Why: WA_OWN_ALL_CHATS=1 (b69d336, 2026-09-23) also captured Meta-webhook inbound, so the bot answered a real candidate via the Cloud API on 09-23..25 (thread of that candidate; two call requests answered with 'a colleague will call'; 9 fixed nudges). Still open: the thread rail is pinned forever, so a bridge-pinned test user writing to the Meta number is answered from the handset (new AC above).

Ivan 2026-10-07 (voice, after an audit of origin/main against 20 old-bot conversations; no candidate data here):
- SUPERSEDES AC 8 and 11 (the TASK-301 cadence over every active conversation; silence only under mute): the follow-up tick is SELECTIVE. A periodic job lists candidates silent long enough, a model decides whom to follow up, and sends exactly ONE message per candidate. No pre-scheduled series. The fixed nudge series (tiers 15/60/240 min, up to 4 per streak, one constant text) goes away (AC 13 stands).
- next_step: every card carries a next_step: what we expect from her, or the single follow-up planned. Each brain run judges the conversation, may amend the card and sets next_step with a tool. Call it next_step, NOT agent_note (that name is taken by app/wa/luna/agent_note_gate.py = operator notes). The author of the next step must see the card scoreboard and states (documents already received etc.), so nothing is asked twice. Identical reminders are excluded by this, not by a separate guard.
- No diploma / recognition pending is not a dead end: next_step = ask about the diploma again in about a week. Today not_placeable is terminal in followups.py and the locked reject text ends the thread. Which exact states count as pending: ask Ivan. Any German wording needs his verbatim approval.
- AC 15 stands and widens: besides the two shipped strings (MEDIA_REPLY, BLOCKED_REPLY_DE) the model itself must never promise human contact. The manager handoff is internal only and tied to card temperature (TASK-316).
- Output check (internal labels, dates, phones on outgoing text): NOT a per-turn exit check; AC 2 stays at exactly three. It is a regular job, deferred, own card.
<!-- SECTION:NOTES:END -->
