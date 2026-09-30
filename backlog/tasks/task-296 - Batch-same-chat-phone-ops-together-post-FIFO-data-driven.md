---
id: TASK-296
title: 'Batch same-chat phone ops together (post-FIFO, data-driven)'
status: To Do
assignee: []
created_date: '2026-09-24 11:02'
labels:
  - whatsapp
  - later
dependencies: []
ordinal: 249000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-24: FIFO is correct for now. Future idea, not to be built yet: when the phone_ops queue has several items destined for the SAME chat, it's cheaper to send them back-to-back (one chat-open, several bubbles) than to round-robin across different chats (open/close/re-navigate per item). Needs a real strategy, not just 'group same-chat items' -- e.g. does grouping starve a different chat's single queued item indefinitely, does it change FIFO fairness guarantees campaign operators rely on, what's the actual measured savings per grouped batch vs added complexity. Ivan's own instruction: do NOT design this now. Run FIFO for about two weeks, then look at the accumulated usage data -- screenshots, screencasts (debug capture, already on via WA_BRIDGE_DEBUG_CAPTURE), and phone_ops/ledger journal history -- to see which task types actually cluster and are worth collapsing into one pass, and design the batching strategy from that evidence rather than upfront guesswork.
<!-- SECTION:DESCRIPTION:END -->
