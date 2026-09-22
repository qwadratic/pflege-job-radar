---
id: TASK-120
title: >-
  Implement app/wa/bridge.Client with BridgeError(MetaError) and the six
  production methods
status: To Do
assignee: []
created_date: '2026-09-21 01:21'
updated_date: '2026-09-21 09:12'
labels:
  - wa-transport
dependencies:
  - TASK-116
  - TASK-119
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: feature
ordinal: 128000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M6, rescoped by decision-8 (2026-09-21). The duck-typed second transport behind the seam.

BridgeError subclasses M.MetaError on purpose, so every existing except M.MetaError in the harness keeps catching without being touched, and so campaign.py error classification stays BYTE-IDENTICAL: BridgeError carries .status_code, which is exactly why a 4xx maps to failed with ownership restored and anything else to uncertain.

Six production methods as before: send_text, send_buttons, send_template, media_url, download_media, get_template. Class attributes requires_freeform_window = False and supports_buttons = False.

TWO ADDITIONS from the wrap architecture:
- begin_turn: the executor needs a turn boundary to key the ledger and to hold one bubble per flock acquisition. A multi-bubble reply is many HTTP calls with one turn key, never one call the executor splits.
- wants_idempotency_key: the capability flag that tells the caller this transport needs a deterministic client_msg_id (TASK-114). The Meta client does not have it, which is how the seam stays honest for both rails.

ONE REQUIREMENT RE-DECIDED. The original acceptance said a 200 with no provider_msg_id must raise. There is no provider message id on this rail and never will be. The requirement becomes: a 200 with no verified delivery tick must raise. A tick value of unverified is not a tick -- it is a 504. Their own code does the opposite (whatsapp.py:141-142 treats it as sent, cli.py:111 then stores a NULL fingerprint, and NULLs are unlimited in a SQLite UNIQUE column); it fired on 2 of 23 live sends. Refusing it at our boundary is one of the two reasons the wrap is worth owning.

No safety nets: the client never invents an id, never invents a tick, and never downgrades an uncertain answer into a confident one.

Injectable transport= and media_transport= mirroring meta.Client, so tests need no network stub. Media needs a separate binary transport for the same reason meta.py has one: a JSON-parsing transport corrupts bytes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 BridgeError subclasses M.MetaError and carries .status_code, and every existing except M.MetaError path catches it unchanged
- [ ] #2 Against a fake transport, 400/401/409/422/429 raise with those int status codes so campaign.py classifies them as failed, and 500/503/504 so it classifies them as uncertain
- [ ] #3 A 504 send_unconfirmed is never auto-resent, proven by a test that drives the campaign path and asserts no second send
- [ ] #4 An unreachable bridge raises status 424 on a send but None on a media call, so import_history aborts the run rather than skipping a document
- [ ] #5 A 200 response with no verified delivery tick raises rather than recording a send
- [ ] #6 A 200 response whose tick is unverified raises as a 504 and is never recorded as sent
- [ ] #7 A 202 response is never reported as sent by the client
- [ ] #8 begin_turn returns a turn boundary the executor keys its ledger on, and one turn of several bubbles is several calls under one turn key
- [ ] #9 wants_idempotency_key is True on the bridge client and absent or False on the Meta client
- [ ] #10 requires_freeform_window is False and supports_buttons is False on the class
- [ ] #11 Tests run fully offline through the injected transports, with no network marker
<!-- AC:END -->
