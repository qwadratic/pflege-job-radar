---
id: TASK-159
title: >-
  Identity matcher: a wrong weak pick silently consumes the other candidate,
  producing a second unmarked wrong attribution
status: To Do
assignee: []
created_date: '2026-09-22 13:13'
labels:
  - wa-transport
  - media-identity
dependencies:
  - TASK-131
references:
  - bridge/identity.py
  - bridge/executor.py
priority: medium
ordinal: 167000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-131 round 6 blocker B5 (verifier, 2026-09-22): decide() marks a tied pick 'weak' when nothing distinguishes two same-kind candidates (Ivan's own two-image example) -- correct and intentional. But the candidate it did NOT pick is then consumed too: the next file of that kind sees only ONE candidate left, and the sole-candidate path (even after the B2 fix, which only catches a CONTRADICTED sole candidate) marks it 'strong'. If the first weak guess was wrong, the second file's attribution is now also wrong, but carries no audit signal at all -- weak does not propagate through a chain of picks that shared a candidate pool. Deferred out of the round-6 UAT-critical fix pass; needs a design answer for what 'weak' should mean when it's inherited rather than direct.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A file whose sole remaining candidate was only available because an earlier weak pick consumed the alternative is itself marked weak (or otherwise flagged), not strong
- [ ] #2 A repro test proves the second file of Ivan's two-image scenario is not silently strong
<!-- AC:END -->
