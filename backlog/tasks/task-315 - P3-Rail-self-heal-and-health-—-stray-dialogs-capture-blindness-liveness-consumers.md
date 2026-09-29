---
id: TASK-315
title: >-
  P3: Rail self-heal and health — stray dialogs, capture blindness, liveness
  consumers
status: To Do
assignee: []
created_date: '2026-09-26 08:48'
updated_date: '2026-09-29 10:31'
labels:
  - rail
  - reliability
dependencies: []
priority: high
project: whatsapp
ordinal: 3
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Stray dialogs took the phone rail down twice in two days, and no health signal caught either one.

**2026-09-25, 07:34-10:35 UTC**
- WhatsApp's SmsDefaultAppWarning dialog blocked every send for 3 h.
- The executor mislabelled it not_on_whatsapp.
- The reconcile watcher failed 55 times, and nobody saw it.

**2026-09-26 ~08:38 UTC** — two dialogs on the handset at once:
- Android's "USB-Nutzung" dialog, with focus on com.android.settings. It ignores BACK; it closed on a tap on ABBRECHEN.
- Behind it, the same SmsDefaultAppWarning ("number not registered on WhatsApp", buttons Einladen / SMS). It also ignores BACK; it closed on one tap on the dimmed area outside the dialog.

**Why park() does not help**
- A send attempt to a number that is not on WhatsApp leaves this warning inside WhatsApp's own task.
- park() launches WhatsApp via monkey and then presses HOME, which hides the warning but never closes it.
- So the next send runs into it again.

**Manual recovery** (Ivan's standing OK to dismiss stray dialogs):
- adb on macmini: ~/agentos-phone/bin/platform-tools/adb, serial L2N4C19B14054874.
- Check focus with `dumpsys window | grep -m1 mCurrentFocus`.
- Afterwards, HOME puts the phone back in the parked state.

Folded here: TASK-225 (ACs 1 and 3 done, 2 and 4 open), TASK-259, TASK-262, TASK-263. Their full text is kept in the archive.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The executor recognises the known stray dialogs (SmsDefaultAppWarning, USB-Nutzung) and closes them itself, logging every dismissal with a screenshot
- [ ] #2 A send to a number that is not on WhatsApp is labelled as exactly that and never leaves the warning open in WhatsApp's task
- [ ] #3 A reconcile-watcher failure streak shows up in health, not only in logs
- [ ] #4 [TASK-225 AC2] tools/wa_bridge.py read is verified against a chat with real, currently-visible unread bubbles and confirmed to find them (or the bug in that path is found and fixed)
- [ ] #5 [TASK-225 AC4] A capture-health check exists independent of any per-thread stuck_reply flag, catching messages the capture pipeline never saw (full text in archived TASK-225)
- [ ] #6 [TASK-259] Something polls /v1/health on a schedule, so every subsystem's liveness signal has a consumer; fixed with a failing-then-passing test, or closed with a written argument
- [ ] #7 [TASK-262] One item the webhook will not handle no longer wedges all inbound behind it, and the wedge raises a real signal; test or written argument
- [ ] #8 [TASK-263] /v1/health ok and /api/wa/bridge-health ok reflect real subsystem state, not a constant or only 'socket answered'; test or written argument
- [ ] #9 Phone doctor (Ivan 2026-09-29: 'постоянно устранять всё, что мешает основному сценарию'): a bridge thread that, every minute and only when the phone lock is free, checks the handset and fixes what blocks WhatsApp automation -- low memory (am kill-all), known stray dialogs, WhatsApp not running, leftover recordings -- journals every action with a screenshot and shows counters in /v1/health
- [ ] #10 Disk janitor on the VPS (Ivan 2026-09-29): cron job that deletes pure caches and moves old Claude transcripts/subagent logs to the Mac mini with a note, never touching /opt, /var/log or Luna brain sessions
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-26 ~09:43 UTC: SmsDefaultAppWarning came back every time WhatsApp opened, because it sat in WhatsApp's task. Fixed by: a tap outside the dialog, then am force-stop com.whatsapp, then relaunch via monkey. Two reopens came up clean on HomeActivity, then HOME to park. Candidate recovery step for the executor: after dismissing, force-stop and relaunch.

2026-09-29 10:15 UTC incident: sends took 30 s-3 min, the bridge's own threads timed out on the phone lock (503) from 08:36; handset had 244 MB free RAM + 1.7 GB swap (ChatGPT, YouTube, Gmail in background). Manual fix: bridge restart + am kill-all -> MemAvailable 1.46 GB. VPS / at 100% (300 MB free); freed by moving 600+ MB of old subagent transcripts to macmini:~/vps-backup/claude-transcripts.
<!-- SECTION:NOTES:END -->
