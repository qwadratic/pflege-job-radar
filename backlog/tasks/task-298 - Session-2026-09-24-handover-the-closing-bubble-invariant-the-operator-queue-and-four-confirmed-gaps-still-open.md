---
id: TASK-298
title: >-
  Session 2026-09-24 handover: the closing-bubble invariant, the operator queue,
  and four confirmed gaps still open
status: To Do
assignee: []
created_date: '2026-09-24 21:33'
updated_date: '2026-09-24 22:24'
labels:
  - whatsapp
  - handover
dependencies: []
references:
  - >-
    backlog/tasks/task-297 -
    Standing-queue-operator-requests-arriving-in-Russian-on-the-WhatsApp-test-threads.md
priority: high
type: chore
ordinal: 251000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Everything from the 2026-09-24 evening session, written down so none of it depends on anyone's memory.

=== WHAT SHIPPED AND IS LIVE (do not redo) ===

1. CLOSING-BUBBLE GATE. app/wa/luna/closing_gate.py -- Haiku, --effort low, no tools, no history.
   Input: the bubble array only. Output: one boolean. Judges whether the LAST bubble hands the turn
   back (a question, a document request, a button prompt) or deliberately ends the conversation
   (declined / not placeable / a colleague takes over). Called from luna_brain._checked_reply. It
   REPLACED the earlier next_ask assertion, which checked a field the model fills in about itself
   rather than the text that goes out, and which exempted every turn with no open gate.
   Fails OPEN by design: any timeout, junk or non-boolean sends the reply unchecked and logs ERROR.
   That is the opposite of agent_note_gate.py's asymmetry and deliberately so -- this gate stands in
   front of a candidate who is waiting, so a dead classifier must cost one weaker message, never a
   silent rail.
   On rejection: the SAME turn is retried once with a short hint (not the violation quoted back),
   in the same session, then sent regardless. This gate NEVER escalates to a human (Ivan, explicit).
   Live-model check: 8/8 on realistic German bubbles, including terminal turns.

2. THE STRICTNESS GRADIENT WAS BUILT AND REMOVED THE SAME DAY, with Ivan's agreement. Softening the
   gate as a candidate nears the end sounds right and is precisely backwards: handoff_consent is the
   only requirement_scoreboard gate with no 'blocked' state, so 'one gate left' is BY CONSTRUCTION
   the consent stage, and the most lenient bucket switched on at exactly the one ask that produces
   the outcome. Recorded in closing_gate.py's docstring so it is not re-proposed.

3. THE CRITICAL RETRY HOLE (found by workflow, confirmed by probe, fixed). _checked_reply caught only
   AssertionError around the retry, so a retry that timed out, hit SessionNotFound or failed
   _validate escaped turn() entirely and the candidate got NOTHING while an already-checked reply sat
   in hand. Now: except Exception, and the decision keys on the FIRST failure, not the second --
   what decides whether a human is needed is what was wrong with the reply we are holding, not what
   went wrong while rewriting it. Grounding escalation is untouched: an invented clinic name is not a
   weak reply, it is a false one.

4. GOVERNOR REMOVED FOR THE TEST HANDSETS (Ivan). bridge/governor.py takes an 'ungoverned' set from
   WA_BRIDGE_UNGOVERNED_NUMBERS, configured in the executor's own bridge.env on the mini -- never
   accepted from a request, or any caller could switch the fuse off. Those numbers bypass the active
   window, every cap and every gap, AND their sends are excluded from the counters real recipients
   are paced against. Health reports ungoverned traffic separately rather than hiding it.
   SIDE EFFECT THAT MATTERS: the two global 'ONE NIGHT ONLY' overrides from 2026-09-23
   (WA_BRIDGE_ACTIVE_HOURS_OVERRIDE, WA_BRIDGE_FIRST_TOUCH_GAP_OVERRIDE_SEC) are now RETIRED. They
   widened the window for EVERYONE -- a real candidate was nudged at 08:02 Europe/Berlin, before the
   9-20 window opens. Real candidates have their quiet hours back. TASK-293 is honoured.

5. THE BROADCAST TEMPLATE IS FROZEN. app/wa/broadcast_template.py, read back off the handset byte for
   byte (it existed in no file anywhere -- every phone-rail broadcast had been sent ad-hoc with
   --body). tools/wa_bridge.py grew --template, which renders it per recipient from their 'name'
   column and refuses a hand-typed body. An unknown name gets the plain greeting, a second approved
   wording, never an invented name. WHY THIS EXISTS: an agent asked to re-send 'the test broadcast'
   could not find a template, wrote its own German opener, and it reached Ivan's real WhatsApp before
   the run was stopped.

6. OPERATOR QUEUE. TASK-297 is the standing queue; tools/operator_queue_hook.py is a UserPromptSubmit
   hook wired in .claude/settings.json. It prints nothing when the queue is empty, so an idle day
   costs no tokens, and every failure mode exits 0 silently. Chosen over a 5-minute poll (burns
   tokens all day) and over a side branch (merge conflicts nobody wants to referee).

TESTS: 23 closing-gate, 13 ungoverned-governor, 12 broadcast-template, 419 bridge lane all green.
THE FULL tests/test_wa_*.py LANE HAS NOT BEEN RUN TO COMPLETION ON THE FINAL CODE -- see the open
items below.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Decide the nudge fix: should a follow-up name the one still-open checklist item instead of asking 'sind Sie noch da?', and is that text code-assembled from requirement_scoreboard (a fact) or written by the brain (a judgement)? followups.py's own docstring argues a fixed reviewable text is safer for an unprompted message -- that argument has to be answered, not ignored
- [ ] #2 Decide when silence is legitimate at all: luna_brain's no_send branch returns bubbles=[] without ever calling the gate, and api.py records the turn as finally answered, which makes the silence permanent (no catch-up, no nudge, no watchdog)
- [ ] #3 Decide the media-ack fix: MEDIA_REPLY reaches a real candidate before any brain call, with no next step, and the nudge ladder is deliberately disabled for exactly that state -- so it is the last message that will ever be sent unless she writes again
- [ ] #4 Approve or reject any NEW German wording the four fixes need -- no agent may invent candidate-facing text (see app/wa/broadcast_template.py's docstring for why)
- [ ] #5 Decide what to do about the escalation promise: card._escalated has exactly ONE reader in the whole product (the unfiltered 50-row GET /wa/threads list). Nothing pauses the brain, assigns the thread or notifies anyone -- and ~15 minutes after being told a colleague is taking over, the same bot asks 'sind Sie noch da?'
- [ ] #6 Decide whether the holding message may be re-sent every turn: BLOCKED_REPLY_DE is stateless across turns, so a persistent failure re-serves the identical no-forward-step message on every inbound -- Valentyn's original complaint with one phrasing instead of three
- [ ] #7 Resolve the two unconfirmed sends from 2026-09-23 23:15 and 23:18 (age >85000s): reconcile returns indeterminate because the visible window does not reach them. TASK-237's guard is working correctly by refusing to confirm off an older identical bubble
- [ ] #8 Check for an orphaned phone_ops row: the executor was restarted while a reconcile op was in state 'running', which TASK-232 describes as never re-run, never terminal, never swept
- [x] #9 Run the full tests/test_wa_*.py lane to completion on the final code and fix the fallout -- the run was killed at the 30-minute timeout twice and the ~27 pre-existing Supabase PostgREST 401 failures must be distinguished from anything new
- [x] #10 Read workflow wjb3qywg2's output (four gap fixes, each adversarially attacked by three lenses) and decide what to implement
- [ ] #11 Decide the prompt diet: 45 RULES entries, 49249 chars, ~14300 tokens EVERY turn, of which 51 percent (24934 chars, 18 rules) apply only at one stage or on one trigger. The proposal is relevance-gating (inject a rule only when its predicate holds), NOT deletion -- every rule encodes a live incident. Workflow weyo45o4f has the exact per-rule predicates
- [x] #12 THE WA LANE HANGS, and this is new. tests/test_wa_*.py was killed by timeout twice: at 30 min and again at 45 min (exit 124), both times stalling around 63 percent. Before tonight's changes the two core files ran in 8.5s and the whole bridge lane runs in 33s, so this is almost certainly something shipped tonight -- the prime suspect is a test reaching the REAL closing gate (a 30s claude -p call per reply) despite the autouse _closing_gate_offline fixture in tests/conftest.py, e.g. from app.wa.api's background turn thread after the monkeypatch is undone. A per-file timing run was launched at handover to name the file; if its output is gone, re-run: for f in tests/test_wa_*.py; do timeout 150 .venv/bin/python -m pytest $f -q; done
- [ ] #13 SALVAGE from the rejected link_bubble fix: its EDIT 1 was called 'sound and necessary' by the attacker that killed the package -- insert the link list at position len-1 so the model's own closing bubble stays last. It also fixes a defect nobody had noticed: consent buttons attach to the LAST bubble (api.py:1135-1137), so today they hang off the URL list instead of the question. EDIT 2 of that package is refuted and must not be taken with it
- [ ] #14 SALVAGE from the rejected nudge fix: the CORE IDEA survived ('an unprompted turn is still a turn' -- drive the nudge through luna_brain.turn() with an 'unprompted' fact in the payload rather than a fixed string). Four specified edits are wrong, two proven by probe: (a) dropping 'followup' from NOT_MODEL_ACTIONS is a real regression -- it hides the historic nudges from the model on legacy threads and reds test_wa_luna_campaign.py::test_a_session_from_before_the_marker...; (b) ST.claim_nudge is taken BEFORE the model call and is non-reclaimable, so the no_send case the fix is designed for silently burns the rest of the streak; (c) turn() has five CODE branches keyed on the candidate's text and the proposal addresses one
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
OPERATIONAL STATE AT HANDOVER (2026-09-24 ~21:45 UTC)

Both test threads were wiped and re-seeded: cards empty, 0 messages, wa_ownership intact, is_test
kept. DB snapshot before the wipe: data/wa.sqlite.before-reset-20260924T194844Z. The Luna session
transcript was ARCHIVED, not destroyed: /home/claude/.local/state/pflege-wa-archive/20260924T195320Z/

Broadcast uat-2026-09-24-restart-2 delivered the frozen template to both at ~21:30 UTC (Zugestellt /
Gesendet) -- sent at 23:30 Europe/Berlin with the window closed and the first-touch daily cap already
at 10/10, which is the end-to-end proof the ungoverned path works.

EARLIER RUNS, left in place deliberately:
- uat-2026-09-24-fresh-start: STOPPED. Its first item delivered an INVENTED German opener to
  +436704048778 before the stop; the second recipient was never attempted. That message is still
  visible in the chat on the handset -- it was removed from the DB but not from the phone, because
  phone-side deletions are what drove the screen/DB desync in TASK-289. Ivan to decide.
- uat-2026-09-24-restart: both items send_unconfirmed. Do NOT re-record them.

VERIFIED, so nobody re-litigates it: a screen read cannot re-ingest that leftover message. Four
independent reasons -- bridge/inbound.py::thread_messages filters b.direction == 'in' and ours is
'out'; only four inserts into wa_messages exist and the read-piggyback feeds record_inbound alone; a
cold read mints nothing without a 'today' divider; both inbound_id and wamid are UNIQUE. Measured
too: two full screen reads, counters 28/0/0 unchanged. The ONE path that could bring it back is
'broadcast --id uat-2026-09-24-fresh-start --status --file ... --body <that text>', guarded by a
body_sha256 match. Do not run it.

WORKFLOW OUTPUTS (large; read before redesigning anything):
- Converge audit, 49 agents, 22 confirmed gaps across 12 outbound paths:
  /home/claude/.tmp/claude-1003/-home-claude-repo/4e983037-6874-59b2-9744-feb738486035/tasks/w88wsyahr.output
- Prompt partition + gate stress test, 18 agents, 9 surviving objections, per-rule predicates:
  /home/claude/.tmp/claude-1003/-home-claude-repo/4e983037-6874-59b2-9744-feb738486035/tasks/weyo45o4f.output
- Four gap fixes, launched at handover, results land later: run wf_9aef5321-b9a / task wjb3qywg2

THE LIVE EVIDENCE FOR THE NUDGE GAP, real candidate +491709990589: 21 outbound against 7 inbound,
EIGHT of them 'Nur zur Sicherheit nachgefragt - sind Sie noch da?', card holding exactly one slot
(region=Bayern) after 7 turns. She asked twice to be phoned and got a document request both times.
This thread is untouched and is the best test case for whatever the nudge fix turns out to be.

=== CORRECTION, and the full lane is now GREEN (2026-09-24 ~22:05 UTC) ===

THE 'LANE HANGS' DIAGNOSIS WAS WRONG AND IS WITHDRAWN. Nothing shipped tonight hangs. Three files --
test_wa_luna_personas.py, test_wa_luna_import_reuse_personas.py, test_wa_luna_campaign_personas.py --
carry the 'llm' marker and spawn the REAL claude CLI by design (pytest.ini declares the marker but
does NOT auto-deselect it; it has to be passed). My lane command simply omitted -m 'not llm'. The
correct command runs in 105 seconds:

    .venv/bin/python -m pytest tests/test_wa_*.py tests/test_bridge_*.py -q -m 'not llm'

FINAL RESULT ON THE FINAL CODE: 28 failed, 1907 passed, 1 skipped, 66 deselected. Every one of the 28
is the known pre-existing Supabase PostgREST 401 baseline (27 in test_wa_luna_brain, 1 in
test_wa_harness -- 'snapshot unavailable: PostgREST 401: No API key found'). ZERO failures are
attributable to tonight's work.

ONE REAL FAILURE WAS FOUND AND FIXED on the way: test_wa_test_threads.py's wipe test asserts that its
fixture seeds every table in PURGE.CORE_TABLES, and adding wa_agent_notes to that tuple made the
assertion fail. The fixture now seeds an agent note too -- the right fix, since that table is
phone-keyed and holds the operator's own Russian text, so a wipe that skipped it would leave the most
readable thing on the thread behind as an orphan row.

=== WORKFLOW wjb3qywg2 RESULT: ALL FOUR FIXES REJECTED (16 agents, 3 lenses each) ===

survived: 0 of 4. Nothing from it was implemented, deliberately. Full text:
/home/claude/.tmp/claude-1003/-home-claude-repo/4e983037-6874-59b2-9744-feb738486035/tasks/wjb3qywg2.output

nudges      killed 3/3 -- core idea sound, four specified edits wrong, two proven by probe
no_send     killed 3/3 -- 'silently re-arms the nudge machine on exactly the threads it un-silences,
                          and routes the most common dead-end turn into a human escalation plus a
                          false promise'; its central historical claim is false in the code
media_ack   killed 2/3 -- 'makes a real candidate worse off, and its own central premise is false in
                          the code'; clean on the four convention rules, wrong on correctness/scope
link_bubble killed 3/3 -- EDIT 1 sound and necessary, EDIT 2 refuted; package also ships a red lane

WHAT THIS RESULT IS WORTH. A 0-of-4 outcome is not a wasted run: it stopped four plausible,
well-argued changes from being written into a live rail at midnight, and two of the four were killed
by a probe that actually ran the suite rather than by an opinion. The gaps themselves are unchanged
and still real -- they are AC #1, #2, #3 and the link_bubble half of the session. Tomorrow starts
from 'here is why the obvious fix is wrong', which is a better starting point than a blank page.
<!-- SECTION:NOTES:END -->
