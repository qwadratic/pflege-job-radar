---
id: TASK-135
title: Freeze the staged candidate shortlist and move the exporter into our repo
status: To Do
assignee: []
created_date: '2026-09-21 09:09'
updated_date: '2026-09-22 06:10'
labels:
  - wa-transport
dependencies: []
references:
  - /home/claude/plans/2026-09-21-macmini-revision.md
priority: high
type: feature
ordinal: 143000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A staged shortlist of 59 real candidates sits on the remote machine at ~/.local/share/wa_phone/private/bayern.json -- 17,349 bytes, mode 0644, written 2026-09-20 15:55 -- in a home directory shared with a root-installed Cursor cloud agent worker. Two commands turn it into 59 cold first touches from an unidentified consumer number.

Those same 59 people already hold 2,159 WhatsApp messages with our WABA number (sales_brain.candidate_whatsapp_messages, latest inbound 2026-09-14). A first touch from a second number is a duplicate approach to warm contacts, and until TASK-137 lands there is no suppression list on either side to stop it.

The exporter that produced it is tools/wa_phone_export_shortlist.py on his side. It opens a 416 MB production database read-write and canonicalizes bare mobiles into North-American +1 numbers (his contacts.py e164() -- four of the 59 are affected; our app/wa/phones.py handles it). Move it into our repo: open the source with file:...?mode=ro, canonicalize with our phones.py, and make the emitted format carry the lane claim and a claim expiry so a staged file retires BY FORMAT rather than by someone remembering it.

State plainly in the task record and in the file header: this is advisory, not enforceable. His repo carries its own copy of the exporter and its header documents running it as root on our VPS over the unrestricted hetzner_root key. Present it as an agreement plus a format change, never as a gate.

PII: the shortlist content is off limits. Count rows, never print numbers or names.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The freeze is agreed in writing with the colleague and the agreement is recorded, or the refusal is recorded with what we do instead
- [ ] #2 Our exporter opens the source database with file:...?mode=ro and a test proves a read-write open is impossible
- [ ] #3 Phone canonicalization goes through app/wa/phones.py, and a test covers the bare-mobile case that his e164() turns into +1
- [ ] #4 The emitted format carries the lane claim and a claim expiry, and a file past its expiry is refused by the reader rather than silently used
- [ ] #5 The task record states in one sentence that this is advisory and names why it cannot be enforced from our side
- [ ] #6 No candidate number, name or message body is printed, logged or committed
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: verified NOT built. No tools/wa_phone_export_shortlist.py or equivalent found anywhere in this repo, and no record of the colleague being asked to freeze the 59-candidate shortlist. Status and description remain accurate as written.
<!-- SECTION:NOTES:END -->
