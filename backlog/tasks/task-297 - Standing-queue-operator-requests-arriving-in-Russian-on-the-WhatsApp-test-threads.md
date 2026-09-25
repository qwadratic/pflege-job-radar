---
id: TASK-297
title: >-
  Standing queue: operator requests arriving in Russian on the WhatsApp test
  threads
status: In Progress
assignee: []
created_date: '2026-09-24 21:25'
updated_date: '2026-09-24 21:27'
labels:
  - whatsapp
  - operator-inbox
dependencies: []
priority: high
type: chore
ordinal: 250000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
THE ONE PLACE this coding session looks for work that did not come from Ivan directly.

WHY IT EXISTS (Ivan, 2026-09-24). Valentyn tests the bot in German and writes his findings and
requests in Russian, and was told they would queue up and be worked on. Two earlier designs were
rejected: a five-minute poll of the WhatsApp inbox burns tokens all day for nothing, and a separate
git branch merged back later creates conflicts nobody wants to referee. This task is the queue
instead -- no branch, no polling, one visible artefact Ivan can read and reorder himself.

HOW AN ITEM GETS HERE. A Russian message on a test thread is classified by app/wa/luna/agent_note_gate.py
and recorded in wa_agent_notes (app/wa/store.py). A separate session -- never this one, and never the
web process -- turns each new note into one acceptance criterion below, verbatim enough that nobody
has to re-read WhatsApp to know what was asked.

HOW THIS SESSION SEES IT. A UserPromptSubmit hook reads this task and injects any unchecked
criterion. It costs tokens only on a turn that was happening anyway; an idle day costs nothing.

PRIORITY RULE (Ivan): an unchecked criterion here outranks whatever Ivan asks in the same turn.
He set that order deliberately -- he is present and can re-prioritise in one sentence, Valentyn is
not.

THIS TASK IS NEVER DONE. It stays In Progress for the life of the rail. Completing an item means
checking that one criterion and sending the WhatsApp completion note, not closing the task.
<!-- SECTION:DESCRIPTION:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
HOW AN ITEM ENTERS THE QUEUE (the separate session's one command, never this one's):

    backlog task edit TASK-297 --ac "<the request, close to the operator's own words>"

One criterion per note in wa_agent_notes. Keep the wording recognisable to whoever wrote it -- the
point of the queue is that nobody has to re-open WhatsApp to know what was asked.

HOW AN ITEM LEAVES:

    backlog task edit TASK-297 --check-ac <n>        # after the work is really done
    python -m app.wa.luna.agent_notes --done <id> --done-text ... --not-done-text ... --needed-text ...

Check the criterion only once the WhatsApp completion note has actually been sent, so the two can
never disagree about what the operator was told.

THE HOOK: tools/operator_queue_hook.py, wired in .claude/settings.json (UserPromptSubmit, project
scope). Prints nothing when every criterion is checked, so an idle day costs no tokens. Every
failure mode exits 0 silently -- an unreadable queue must never cost Ivan a turn.
<!-- SECTION:PLAN:END -->
