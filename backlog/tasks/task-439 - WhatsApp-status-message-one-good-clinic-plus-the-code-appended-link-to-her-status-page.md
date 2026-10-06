---
id: TASK-439
title: >-
  WhatsApp status message: one good clinic plus the code-appended link to her
  status page
status: In Progress
assignee:
  - wa-harness
created_date: '2026-10-06 08:55'
updated_date: '2026-10-06 09:57'
labels:
  - whatsapp
  - luna
dependencies: []
priority: medium
ordinal: 315000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06, relayed by pflege-fe: the WhatsApp message to a pooled candidate carries one good clinic as an example, plus a line "here you can see where we sent your profile" with the status-page link (TASK-436). Everything else she asks in the chat. The German line is fixed candidate-facing text and needs Ivan's verbatim approval. The link is appended by code. It cannot be sent while the WhatsApp rail is down (handset lent out since 10-02). Requested by the WhatsApp lane (wa-harness).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Ivan approved the German line verbatim, and it is stored as a constant
- [x] #2 The message picks one clinic by a stated rule and appends the link in code; covered by offline tests
- [ ] #3 A dry run on a test thread shows the exact bubbles; no live send without Ivan
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Ivan's verbatim German as constants in app/wa/luna/prompts.py (STATUS_*_DE).
2. app/wa/luna/status_message.py: bubbles(status_json, url) -> 2 bubbles; clinic = the one sent entry with best: true (email lane picks, harness never ranks); N = len(sent); link checked against the status-page URL shape and appended by code.
3. tools/wa_status_message.py: dry-run printer, no send path.
4. Offline tests tests/test_wa_status_message.py.
5. Dry run on her real JSON once the email lane marks best: true; no live send without Ivan.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Ivan 2026-10-06 (wa-harness session), relayed asks via pflege-fe: nothing is sent now; bubble 1 = the best clinic we already sent her profile to, in detail; bubble 2 = the real number of clinics plus the full-report link. Text approved verbatim ("ок") in two bubbles:
1) Wir haben Ihr Profil an diese Klinik geschickt, sie passt besonders gut zu Ihren Wünschen: / {Klinik}, {Ort} / Stellen: ... / Wohnung: ... / Weg: ...
2) Insgesamt ist Ihr anonymisiertes Profil an {N} Kliniken gegangen. Wir warten jetzt auf deren Antworten. / Alle Kliniken und den vollständigen Bericht finden Sie hier: / {Link}
Wording is plural only: fewer than two clinics raises (singular not approved). The best marker is pflege-fe's best: true on one sent entry (PR #17); the email lane sets it.
<!-- SECTION:NOTES:END -->
