---
id: TASK-141
title: 'Agree the two-lane coexistence with the colleague, and hand over the two gifts'
status: To Do
assignee: []
created_date: '2026-09-21 09:11'
updated_date: '2026-09-22 06:10'
labels:
  - wa-transport
dependencies: []
references:
  - /home/claude/plans/2026-09-21-macmini-revision.md
priority: high
type: chore
ordinal: 149000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
NOTE ON THE WORD: this is coexistence with the colleague lane on one handset. It is NOT Meta Coexistence, which decision-6 rejected and decision-8 leaves rejected.

Nobody has spoken to him yet. He owns the machine, the WhatsApp account, the daemon, the flock and the code. There are six veto points and we hold none of them. Everything below is a question; none of it is a demand, and none of it is enforceable from our side.

The list (revision section 6):
1. May our process take huawei01.lock on Huawei 01 (...874), one bubble per acquisition, released between bubbles?
2. May we place ~/pflege-wa-bridge/ and one systemd --user unit in the cursorworker1 account?
3. Until we agree otherwise: does his daemon run --auto-reply-to <his test number> only -- never bare --auto-reply, never --send-first-touches?
4. Will he freeze the staged 59-candidate shortlist until a suppression list exists on both sides (TASK-135)?
5. Will he re-run his own read-only identify task against huawei_p30_lite_01 and tell us the E.164 (TASK-136)?
6. The on-device phone agent still heartbeats as huawei_p30_lite_01 every ~6s to an API bound 0.0.0.0:8791 with no flock. Can that device be retired from the task lane, or documented as a second master with agreed rules?
7. May we read ~/.local/share/wa_phone/wa_phone.sqlite in mode=ro over ssh for reconciliation?
8. Three bug reports as a gift -- fingerprint collision, unverified-counted-as-sent, attach_media without a phone predicate. Does he want patches or just the reports?
9. A systemd --user unit for wa-phone-daemon? Today it is a bare tmux session, so a reboot ends his lane while our tunnel comes back green.
10. Screenshot rotation: 91 MB of candidate chat contents as PNGs after 16h of ONE test conversation, no off switch (device.py shoots before every input). Ours will sweep at 48h; does he want the same?
11. We intend to restrict his hetzner_root key on our VPS (TASK-138). What exactly does farm_autopilot.py need to keep working?
12. An agent key (read:board) and a pinned four-endpoint contract for his market.py, instead of anonymous scraping of pflege-board.exe.xyz?

The two gifts are not preconditions and are not bargaining chips. Finding real bugs in somebody first day of work is the cheapest goodwill available, and it is owed regardless of what he answers:
- The three defect reports, each with its file, line and the observed effect at scale.
- A systemd --user unit for his daemon, as a template he installs or ignores.

Question 3 is the entry condition for our whole build (TASK-134): two automated senders on one consumer account is the one failure neither side can observe from its own side.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All twelve questions are put to the colleague in one written message, and every answer is recorded including the refusals
- [ ] #2 The answer to question 3 is recorded explicitly, because it is the entry condition for TASK-134 and nothing starts without it
- [ ] #3 The three defect reports are handed over with file, line and observed effect, regardless of what he answers to anything else
- [ ] #4 A systemd --user unit for his daemon is offered as a template, with no expectation that he installs it
- [ ] #5 Refusals are recorded as outcomes with what we do instead, not as open items to be re-asked
- [ ] #6 Nothing is deployed, written or run on his machine, and no question is treated as answered by silence
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: verified NOT done. No record found anywhere in the repo or plans/ of the twelve questions having been sent to the colleague or of any answers received. The three defect reports (fingerprint collision, unverified-counted-as-sent, attach_media without a phone predicate) were all independently fixed at OUR boundary already (TASK-131's fingerprint fix, TASK-130/142's refusal-of-unverified) but that is not the same as handing the reports to him as a gift, which this task also requires and which has no record of having happened. Status and description remain accurate as written.
<!-- SECTION:NOTES:END -->
