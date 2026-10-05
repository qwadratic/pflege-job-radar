---
id: TASK-345.10
title: Forward every inbound mail of daria.s@pflege-connect.work to Ivan's Gmail
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-28 09:21'
updated_date: '2026-10-05 19:07'
labels:
  - email
dependencies: []
parent_task_id: TASK-345
priority: high
ordinal: 263000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-28: reading our outreach mailbox is allowed and required, and inbound mail has the top priority. Every message that reaches daria.s@pflege-connect.work must land in his personal Gmail (ivan.d.kotelnikov@gmail.com), which he watches. Later the same day he decided against a timer and against notifications into the working session: forwarding to his Gmail is enough, and he gives the session its tasks himself. Trigger: Valentin's reply to Daria's report of 2026-09-28 went unseen. The claude user can read daria's box only through Microsoft Graph with the root-owned shared MSAL cache (IMAP basic auth is off, no sudo), and nothing watched it. The nurse-79 clinic campaign sends from this box, and its watch reads replies, bounces and stops from it, so the box must keep its own copy of every message.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Claude reads daria.s@pflege-connect.work through one root-owned read-only command run with sudo without a password (/usr/local/sbin/daria-inbox, tools/daria_inbox.py, installed by tools/daria_inbox_install.sh)
- [ ] #2 Inbound messages Claude picks are forwarded from daria to ivan.d.kotelnikov@gmail.com with header lines, text and the original attached, each logged once in forwarded.jsonl (tools/daria_forward.py)
- [ ] #3 Valentin's reply of 2026-09-28 is forwarded
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Ivan, 2026-09-28: no timer, no session notifications, no Outlook forwarding setup ("а ты разве не можешь мне пересылать руками? не хочу настраивать"), and "И когда тебе право, дай команду".
1. tools/daria_inbox.py: self-contained root command (stdlib + msal, python -I), Graph GETs only, no MSAL cache write-back; prints daria's messages since --since as JSON lines with the MIME. Checks the .env values root uses: cache opened with O_NOFOLLOW, must be a regular root-owned file only root can write; authority must be login.microsoftonline.com.
2. tools/daria_inbox_install.sh: installs it root-owned as /usr/local/sbin/daria-inbox, runs it once, only then adds /etc/sudoers.d/daria-inbox (claude NOPASSWD for that one command).
3. tools/daria_forward.py list/send: forward picked inbound messages from daria to Ivan's Gmail over SMTP, logged in forwarded.jsonl next to the fetch file (data/email-dump/daria.s_pflege-connect.work/, gitignored).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-28: first approach, a root systemd timer running tools/inbox_relay.py (Graph poll, SMTP notification with the original attached, spool, session watcher), was written up to the tests. Ivan dropped it before installation ("Таймера мне не надо ... просто входящих на мою почту будет достаточно"). The file is deleted; nothing was installed (no /opt/pflege-inbox-relay, no /var/lib/pflege-inbox-relay, no pflege-inbox units).
Ivan's Gmail was searched through the claude.ai Gmail connector: sender Valentin's two boxes, to or cc daria, the report's subject words, last 3 days. Valentin's reply is not there, so it sits only in daria's box.
Open: whether M365 forwards messages that it files in daria's Junk Email folder is not verified.

2026-09-28 13:03 UTC: first install attempt stopped (as designed, before the sudo rule) because a directory above the MSAL cache is writable by a non-root user; the first version checked every directory because it wrote the cache back. Reworked: no write-back, only the cache file itself is checked. Ivan then allowed Claude to run the installer ("ты в ручном режиме, можешь сам сделать"); it passed its smoke run and installed the sudo rule. Note: sudo -n worked for the claude user, so the claude user has passwordless sudo in general; the rule "no sudo for Claude" is a policy, and daria-inbox is its only exception.
Fetched daria since 2026-09-28 00:00 Berlin: 41 messages, 23 inbound. Forwarded 3: Valentin's reply (10:48), Microsoft billing notice (card declined for Exchange Online Plan 1, tenant (the client)), NDR for the daria-test letter to anastasiya.yeremenko@bewerbung-pflege.work (address does not exist). Not forwarded: 20 cold sales letters to "Daria"; asked Ivan whether he wants those too.
<!-- SECTION:NOTES:END -->
