---
id: TASK-237
title: >-
  reconcile can confirm a send off an older bubble with identical text, marking
  a message that never went out as delivered
status: In Progress
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-23 09:20'
labels:
  - rail-critique
  - loses-messages
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 184000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/executor.py:477. Severity: loses-messages. 

HOW IT HAPPENS: The tier-1 nudge is delivered and ticked. Days later the tier-2 nudge (new key, byte-identical body) ends UNCONFIRMED because the driver reported "unverified" or no tick appeared within TICK_WAIT_SEC. An operator runs `wa_bridge reconcile --keys <tier-2 key>`; the visible thread (quiet -- that is why a nudge was due) still shows the tier-1 bubble; its sha matches, it carries a tick, and _scan promotes it. mark_sent writes the tier-1 bubble's tick and clock onto the tier-2 row.

WHAT IT COSTS: A message that was never delivered becomes terminal SENT: never resent, the operator is told it went out, and the ledger row now carries another message's delivery evidence. The candidate's thread goes quiet with nobody aware of it.

PROPOSED DIRECTION (not a decision): _scan should require the match to be consistent with the attempt before promoting it, the same way the absent branch already reasons about clocks: a body match whose bubble clock is earlier than entry.attempted_at is evidence of an older message, not of ours, and should leave the verdict indeterminate. Where several identical bubbles are visible, the count matters too -- one match when the thread should now hold two is itself the answer.

VERIFICATION NOTES: CONFIRMED. executor.py:475-481: `mine = [b for b in outgoing if D.body_sha256(b.text) == entry.body_sha256]` then `if mine and mine[-1].tick_state is not None: self.ledger.mark_sent(...)` -- no comparison against entry.attempted_at, even though the very next branch (executor.py:484-486) computes `attempted_clock` and reasons about clocks for the absent case. Constant bodies really do recur in one thread: C.FOLLOWUP_NUDGE_DE is sent once per tier from followups.py:132 with only the turn_key differing, so tier 1 and tier 2 are byte-identical messages to the same person; the same is true of api.MEDIA_REPLY (api.py:239), P.DECLINE_ACK_DE (prompts.py:715) and P.BLOCKED_REPLY_DE. D.wait_for_tick guards precisely this hazard for itself and its docstring (driver.py:634-639) names it -- "Identical bodies do recur on this rail... an older identical bubble already carries a tick" -- and it is safe only because AdbDriver._verify proved a NEW bubble appeared first. _scan has no such precondition. One reachability note: reconcile is only ever invoked by hand from tools/wa_bridge.py (see finding 5), so the bad outcome needs an operator running the documented remedy -- which is exactly when it fires.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Sceptic pass upheld: implemented. Verified each of the sceptic's citations against the current
code -- executor.py:507-514 promoted on body-sha match + tick alone, with attempted_clock computed
only one branch later (was line 519, used only by the absent case). ledger.mark_sent/_resolve is a
dumb setter with no clock check; SENT is not in RESENDABLE, so a wrong promotion is unrecoverable.
Confirmed the recurrence claim (followups.py:132: tier-1/tier-2 nudges send byte-identical
C.FOLLOWUP_NUDGE_DE, only turn_key differs) and the wait_for_tick analogy (driver.py:664-677: its
bottom-most-match reasoning is only safe because AdbDriver._verify already proved a NEW bubble
appeared; _scan has no equivalent precondition). Also confirmed the reachability caveat is stale:
UnresolvedSendWatcher (bridge/watcher.py:496-559, wired in server.py:517-524, both uncommitted in
this tree from TASK-235) auto-enqueues reconcile for every ATTEMPTING/UNCONFIRMED row every 60s --
no operator required once this branch deploys.

Fix: moved attempted_clock above the mine-match branch in _scan and require
mine[-1].clock >= attempted_clock before promoting to confirmed_sent. A body+tick match that fails
the clock check now returns indeterminate with its own evidence string ("body match predates this
attempt -- an older identical bubble, not ours"), distinct from the existing no-tick indeterminate.
No ledger schema change, no send-path change, single function touched (bridge/executor.py::_scan).

Test: tests/test_bridge_executor.py::test_reconcile_will_not_confirm_sent_off_an_older_bubble_with_the_same_body
-- begins at 10:00 Berlin, thread holds a same-body ticked bubble at 09:59 (before the attempt);
asserts verdict stays indeterminate and ledger state stays ATTEMPTING. Verified this test fails
(confirmed_sent) against the pre-fix branch and passes with the fix, via a throwaway file copy --
never touched the working tree's executor.py itself, so the other uncommitted batch work in that
file was never at risk. Confirmed the two sibling promotion/absent tests (10:01 clock, after the
10:00 attempt) and all other cases still pass.

Ran narrow only: .venv/bin/python -m pytest tests/test_bridge_executor.py -q -> 136 passed. Did not
run the full suite (owner's single verification pass covers that). Not committed; status left at
In Progress, acceptance criteria left unchecked per instructions.
<!-- SECTION:NOTES:END -->
