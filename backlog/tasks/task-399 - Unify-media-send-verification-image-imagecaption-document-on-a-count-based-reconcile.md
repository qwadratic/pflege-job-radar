---
id: TASK-399
title: >-
  Unify media send verification (image, image+caption, document) on a
  count-based reconcile
status: To Do
assignee: []
created_date: '2026-09-29 23:08'
labels:
  - rail
  - media
dependencies: []
references:
  - bridge/adb_driver.py
priority: medium
type: enhancement
project: whatsapp
ordinal: 274000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-25: one verification for every media send, based on "a new message appeared in the chat". A before/after count already exists for send_photos, send_gallery and send_document. The session research recommended four refinements:
- count, not clock;
- at least 1 new bubble;
- never mark media absent automatically;
- key without the caption.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One verification path covers image, image+caption and document
- [ ] #2 It follows the four refinements, and tests cover each media kind
<!-- AC:END -->
