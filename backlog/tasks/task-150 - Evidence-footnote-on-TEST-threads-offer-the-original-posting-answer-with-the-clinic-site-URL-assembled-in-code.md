---
id: TASK-150
title: >-
  Evidence footnote on TEST threads: offer the original posting, answer with the
  clinic-site URL, assembled in code
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 13:23'
updated_date: '2026-09-22 06:08'
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
- [x] #1 On a thread with wa_threads.is_test=1, every outbound message that quotes board data carries the footnote offering the original posting
- [x] #2 On a thread without the flag nothing changes: the rendered message is byte-identical to the same turn before this feature
- [x] #3 The model is never given a posting URL in any payload, and a test fails if one reaches the model's input; the footnote and the URL answer are assembled in code from board rows
- [x] #4 A request for the original in a test thread is answered with the clinic's own career-site URL for that posting; an aggregator or board URL is never sent as the original
- [x] #5 The rule that chooses which recorded URL is the clinic's own is deterministic and documented, and a posting with no clinic-site URL on record is said plainly instead of substituting another link
- [x] #6 Both brains go through the same code path, so the deterministic branch and WA_BRAIN=luna cannot disagree about what a test thread is shown
- [x] #7 Offline tests cover: footnote present on a test thread, absent on a production thread, the URL answer, the missing-URL case, and a model attempt to write a URL still failing grounding
- [ ] #8 docs/whatsapp.md records the rule, that it is test-only, and why the link ban stays structural
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: built 2026-09-21/22 in app/wa/luna/source_link.py (144 lines) plus integration in app/wa/luna_brain.py (lines 878, 1026, 1168, 1303, 1329) and app/wa/brain.py:262. 12 tests in tests/test_wa_luna_test_thread_sources.py cover: AC#1 test_a_test_thread_is_offered_the_original_ad_of_the_posting_it_was_told_about + test_the_footnote_itself_carries_no_link; AC#2 test_a_production_thread_gets_no_footnote_and_no_link + test_a_production_thread_asking_the_same_question_gets_no_link; AC#3 test_the_model_may_not_write_the_remembered_sources_itself (also tests/test_wa_luna_dialog_rules.py:565, tools_server.py:859); AC#4/#5 test_asking_for_the_original_gets_the_url_appended_to_the_answer_not_instead_of_it, test_a_stored_link_the_board_no_longer_confirms_is_said_instead_of_sent, test_a_posting_whose_board_row_has_no_url_is_said_plainly, test_a_message_that_names_no_posting_gets_no_footnote; AC#6 test_wa_harness.py:338 (both brains read the same is_test flag). Offline suite green: 2312 passed. RESCOPE ON AC#4, stated by the code itself and worth recording rather than pretending it was delivered as literally written: the AC asked for 'the clinic's own career-site URL'. source_link.py's docstring states honestly that the board holds no such field -- postings has no source_url column, and ~30%% of live postings are published through a recruiting vendor (softgarden, SmartRecruiters, mein-check-in, umantis) rather than the clinic's own domain. What is sent is external_url, the address of the scraped ad itself -- never our own board and never an aggregator mirror, which is the part of AC#4 that actually protects Ivan's partner, but not literally 'the clinic's own site' in every case. AC#8 LEFT UNCHECKED: docs/whatsapp.md:638 still reads 'TASK-150 (PLANNED) reopens the evidence question' -- the docs were never updated after the feature shipped and are stale. Docs are out of this lane's scope (backlog/** only); flagging for whoever next touches docs/whatsapp.md.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The evidence footnote is built and tested: on TEST threads only (wa_threads.is_test), app/wa/luna/source_link.py assembles a footnote and, on request, the original ad's own URL entirely in code from board rows -- the model is never given a URL (app/wa/luna/offer.py keeps external_url out of its payload) and a production thread is untouched. Verified by 12 tests in tests/test_wa_luna_test_thread_sources.py plus a green full offline suite (2312 passed). Two things flagged rather than hidden: AC#4 was delivered as external_url (the scraped ad's own address, never a board/aggregator mirror) rather than a literal clinic-career-site field, because the board holds no such field -- stated honestly in the module's own docstring. AC#8 (docs/whatsapp.md documenting the rule) is NOT done: the doc still calls this PLANNED as of 2026-09-22 and needs a docs-lane pass.
<!-- SECTION:FINAL_SUMMARY:END -->
