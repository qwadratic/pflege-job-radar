---
id: TASK-106
title: >-
  Retry an undelivered campaign template within the same campaign by explicit
  flag
status: To Do
assignee: []
created_date: '2026-09-14 22:15'
labels: []
dependencies:
  - TASK-103
type: feature
ordinal: 106000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-103 plans a template that Meta later reported failed (status webhook, e.g. 131042 unsettled payments, 131026, 131049) as delivery_failed and never resends it under the same campaign id; a retry needs a new --campaign-id, which splits one campaign across ids and reports. Ivan 2026-09-14: allow the retry within the same campaign via an explicit flag. The live 131042 failure on 2026-09-14 is exactly this case: after billing is fixed the same campaign must be resendable.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 with an explicit flag (e.g. --retry-delivery-failed) the sender re-sends phones planned delivery_failed in the same campaign; without it behaviour is unchanged
- [ ] #2 every attempt keeps its own wamid, error code and delivery status (history is not overwritten), --status shows all attempts, and a later reply is matched to the attempt it answers
- [ ] #3 ownership, claim and card.campaign stay consistent across attempts; phones that declined, stopped or replied in the meantime are not resent
- [ ] #4 offline tests cover failed then retried then delivered, failed twice, and reply-after-first-attempt; runbook updated
<!-- AC:END -->
