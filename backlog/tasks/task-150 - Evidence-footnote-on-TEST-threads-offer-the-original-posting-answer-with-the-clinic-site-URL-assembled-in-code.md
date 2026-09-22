---
id: TASK-150
title: >-
  Evidence footnote on TEST threads: offer the original posting, answer with the
  clinic-site URL, assembled in code
status: To Do
assignee:
  - '@claude'
created_date: '2026-09-21 13:23'
labels:
  - wa-luna
dependencies: []
documentation:
  - docs/whatsapp.md
priority: high
type: feature
ordinal: 158000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-21. When he reads a test conversation he has no way to check that a named posting is real without opening a second system. He asked for the proof to travel with the message: on TEST threads only (wa_threads.is_test), every message that quotes board data carries a footnote offering the original posting, and when the candidate asks for it the bot answers with the ORIGINAL CLINIC-SITE URL -- the clinic's own career page for that posting, not an aggregator mirror and not our board.

Why this is delicate. Candidates must never receive links: app/wa/luna/prompts.py bans it in words, and app/wa/luna/offer.py makes it structural by keeping source_url and external_url out of the payload the model writes from -- a rule in a prompt is a request, a field the model never sees is a guarantee. That structure must survive this feature. So the footnote and the URL answer are assembled in code after the model has written, from board rows, gated on the thread's test flag; the model is never handed a URL and never asked to produce one. A production thread's outbound message must come out byte-identical to today's.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 On a thread with wa_threads.is_test=1, every outbound message that quotes board data carries the footnote offering the original posting
- [ ] #2 On a thread without the flag nothing changes: the rendered message is byte-identical to the same turn before this feature
- [ ] #3 The model is never given a posting URL in any payload, and a test fails if one reaches the model's input; the footnote and the URL answer are assembled in code from board rows
- [ ] #4 A request for the original in a test thread is answered with the clinic's own career-site URL for that posting; an aggregator or board URL is never sent as the original
- [ ] #5 The rule that chooses which recorded URL is the clinic's own is deterministic and documented, and a posting with no clinic-site URL on record is said plainly instead of substituting another link
- [ ] #6 Both brains go through the same code path, so the deterministic branch and WA_BRAIN=luna cannot disagree about what a test thread is shown
- [ ] #7 Offline tests cover: footnote present on a test thread, absent on a production thread, the URL answer, the missing-URL case, and a model attempt to write a URL still failing grounding
- [ ] #8 docs/whatsapp.md records the rule, that it is test-only, and why the link ban stays structural
<!-- AC:END -->
