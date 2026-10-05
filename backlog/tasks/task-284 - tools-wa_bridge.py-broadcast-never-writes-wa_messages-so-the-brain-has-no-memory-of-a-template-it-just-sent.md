---
id: TASK-284
title: >-
  tools/wa_bridge.py broadcast never writes wa_messages, so the brain has no
  memory of a template it just sent
status: In Progress
assignee: []
created_date: '2026-09-23 19:43'
updated_date: '2026-10-05 18:56'
labels: []
dependencies: []
priority: high
project: whatsapp
ordinal: 237000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found live during the 2026-09-23 UAT broadcast to Valentyn (+436…6780). Sequence, all
confirmed against the actual rows:

  19:21:19  broadcast sends the approved opening template. bridge/broadcast.py records it on the
            MINI ledger only (outbound row wab.o.camp.uat-2026-09-23.*, tick Zugestellt). Nothing
            is written to data/wa.sqlite::wa_messages on the VPS -- broadcast.py never goes through
            app/wa/api.py::send_and_record, the one place that writes an outbound row.
  19:22:51  a piggyback read during the send (the same send opens the chat to type) sweeps in an
            OLD unread bubble ("Passau", from a real prior conversation, not a reply to tonight)
            and records it as a fresh inbound row.
  19:23-26  app/wa/api.py::process_owed_turn answers the owed "Passau" inbound. turn_context()
            builds the prompt from wa_messages, which has NO record that we just sent anything --
            so the brain treats "Passau" as a cold first contact and drafts its OWN introduction
            ("Hallo! Ich bin Valentina, ein digitaler Assistent der <client>...") followed by a
            qualification question, both real sends to Valentyns phone, both entirely off-script
            and inconsistent with the template that had just gone out ninety seconds earlier.

The candidate now sees three different bot messages back to back: the real approved template, then
two self-invented ones. The root cause is structural, not specific to the Passau bubble -- ANY
broadcast run followed by ANY piggyback-read capture on the same thread reproduces this, because
the brains context of "what did we just send" and the rails record of "what did we just send" are
two different, disconnected stores.

TASK-283.6 (the console broadcast planner) surfaces broadcast history for a human to read; this
task is about the brain being able to read it too, which is a correctness issue independent of
whether a console ever exists.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A message sent via tools/wa_bridge.py broadcast --send appears in wa_messages as an outbound row, same shape as a send_and_record send
- [ ] #2 turn_context() includes a broadcast-sent message in what it hands the model, so a reply composed afterward knows it was just sent
- [ ] #3 A test reproduces the exact 2026-09-23 sequence (broadcast send, then an unrelated inbound on the same thread) and asserts the model turn sees the broadcast in its context
- [ ] #4 No existing broadcast test breaks, and the mini-side ledger recording is unchanged
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified the sceptic's read of the code: cmd_broadcast/cmd_send in tools/wa_bridge.py never import
app.wa.store; broadcast_status()'s run view (app/wa/bridge.py::Client._run_view <- bridge/broadcast.py
::Broadcast.view) deliberately never carries phone or body back ("bodies stay on the handset
machine" -- view()'s own comment), only client_msg_id/thread(hashed)/body_sha256/status. So a sent
broadcast genuinely never reaches wa_messages, and turn_context()/introduced() (both read
ST.messages_for(c, phone, direction="out") with no rail filter) are blind to it -- confirmed against
the actual functions, not assumed. Real, reachable (TASK-382 AC#3 depends on Luna answering a
broadcast reply correctly), not a stated design tradeoff anywhere in the file's own docstring.

Deviated from the sceptic's fix sketch in one load-bearing way: their plan assumed "the CLI already
has phone/body" at --status time. It does not -- that pairing is only known during the --send
invocation (a separate process from a later --status poll), and the executor's view intentionally
never returns it (PII minimisation, bridge/broadcast.py's own stated design). So the fix rebuilds the
pairing from --file/--body/--body-file at --status time (the same inputs --send already used), not
from the run view. Added a correctness check the sketch didn't mention: the run view's own
body_sha256 (already present, unused before this) is compared against the rebuilt body before
recording, so a --status poll with the wrong file is refused loudly rather than writing a fabricated
message into Luna's memory of what she said.

Implemented: tools/wa_bridge.py -- new _record_broadcast_sent(), wired into cmd_broadcast's --status
branch when --file is given; records each confirmed-'sent' item once into wa_messages via
ST.record_outbound(phone, client_msg_id, body, kind="text", meta={action: broadcast, run_id}), guarded
by ST.message_by_wamid so a repeat poll never duplicates. Plain --status (no --file) is unchanged in
behaviour, now prints a NOTE that sent items were not recorded. Does not touch bridge/broadcast.py,
bridge/ledger.py, bridge/executor.py, bridge/driver.py (only reuses its pure body_sha256 helper,
same cross-import pattern this file already uses for bridge/ledger.py and bridge/relay_pull.py), or
app/wa/api.py.

Test: tests/test_wa_bridge_cli.py, 4 new tests (verified each fails on the pre-fix code, AttributeError
on the not-yet-imported D module / no recording happened): a status poll without --file leaves
wa_messages untouched and says so; a status poll with --file records the sent item with the right
phone/body/meta; a repeat poll does not duplicate; a poll whose --body does not match the run's own
body_sha256 is refused (exit 2) and records nothing. Ran narrowly: .venv/bin/python -m pytest
tests/test_wa_bridge_cli.py -q -> 88 passed. Did not run the full suite (that is the separate
verification pass).

Left open, not fixed here (out of scope for this task): a broadcast recorded this way still does not
call ST.pin_rail, so wa_threads.rail is not set by a phone-rail broadcast send the way a live app/wa
send sets it. Worth its own task if it turns out to matter; AC#1-#3 here do not depend on it, since
turn_context()/introduced() read wa_messages directly.
<!-- SECTION:NOTES:END -->
