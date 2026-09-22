---
id: TASK-125
title: bridge_sync reconciliation pass and the pflege-wa-bridge-sync timer
status: To Do
assignee: []
created_date: '2026-09-21 01:22'
updated_date: '2026-09-22 06:09'
labels:
  - wa-transport
dependencies:
  - TASK-120
  - TASK-123
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: medium
type: feature
ordinal: 133000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M8. Push alone loses messages; the pull pass is what makes inbound loss recoverable.

Failure it exists for: a candidate reply vanishes and nobody notices until stuck_reply fires hours later on a thread whose state is already wrong.

Three passes, every 3 minutes: re-ingest GET /v1/inbox?since= (dedup is free via the UNIQUE wamid), resolve uncertain outbound rows from GET /v1/jobs?state=terminal, and run the health conjunction.

Unit and timer copy the existing catchup pair convention (OnUnitActiveSec=3min, AccuracySec=30s). Templates only, installed by a human, matching how the existing units are handled. This task does not install, enable, start or restart anything.

catchup.py runs bridge_sync first when the rail is bridge, then its two existing passes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The sync re-ingests inbound missed by the push path and creates no duplicate rows, relying on the UNIQUE wamid rather than a new dedup table
- [ ] #2 Outbound rows left uncertain are resolved from the home machine terminal job state, and only a confirmed_absent verdict may authorise a resend
- [ ] #3 An indeterminate verdict ages out and is surfaced for a human rather than being resolved by guessing
- [ ] #4 The pass exits non-zero only on the health conjunction, so an ordinary quiet run is silent
- [ ] #5 deploy/pflege-wa-bridge-sync.service and .timer exist as templates copied from the catchup pair, and nothing is installed or started by this task
- [ ] #6 catchup.py runs bridge_sync before its existing passes when the thread rail is bridge
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: verified NOT built. Grepped for a bridge_sync module/function and for pflege-wa-bridge-sync.service/.timer across app/, bridge/, tools/ and deploy/ -- the only hits are forward references in app/wa/bridge.py comments ('bridge_sync (TASK-125) is where those actually resolve') and docs/whatsapp.md prose. No reconciliation pass exists: an 'attempting' ledger row from a crash mid-send is never resolved except by bridge/executor.py's own reconcile() path when the SAME process comes back up (covered by TASK-130), not by a periodic GET /v1/inbox / GET /v1/jobs?state=terminal sweep from the VPS. TASK-146's implementation notes confirm this directly: 'nothing resolves an attempting row automatically, which is why every nothing-was-typed refusal now leaves no row.' Status and description remain accurate as written.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 09:14
---
decision-8 (2026-09-21): KEEP, PROMOTED from reconciliation backup to the PRIMARY terminal path. A phone send is paced, so the contract is 202-first: the normal answer is queued, not sent, and most sends resolve in this pass rather than in the send call. It also carries the GET /outbox pull, which is the durable handoff on a link measured at 11.4 percent retransmit.
---
<!-- COMMENTS:END -->
