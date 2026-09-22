---
id: TASK-161
title: >-
  Content gate: a weakly-attributed image still reaches vision and the model,
  only documents are gated
status: To Do
assignee: []
created_date: '2026-09-22 13:14'
labels:
  - wa-transport
  - media-identity
dependencies:
  - TASK-131
references:
  - app/wa/api.py
  - bridge/envelope.py
priority: medium
ordinal: 169000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-131 round 6 verifier scope note (2026-09-22, not a blocker -- the brief's literal wording named documents only, so this is not a regression, but the leak shape is identical for an image). app/wa/api.py's content gate skips text-extraction and keeps the model call out for a weakly-attributed DOCUMENT (its wa_documents row stays NULL, the thread is flagged _escalated). A weakly-attributed IMAGE is not gated at all: vision still reads it and its description reaches the card and the model's prompt. An Urkunde or a passport photo attached to the wrong candidate at 'weak' strength leaks exactly the same way a wrongly-attributed CV would. Whether to extend the gate to images (and video) is a product decision Ivan should make, not an inference.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Ivan has decided whether the content gate extends to a weakly-attributed image (and video), or stays document-only by design
- [ ] #2 If extended: a weak image's vision description never reaches the card or the model, mirroring the existing document gate, with a passing test
<!-- AC:END -->
