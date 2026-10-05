---
id: TASK-280
title: >-
  prompts.py's SHOW_CLINIC_PHOTOS rule names get_clinic_contact as evidence, and
  get_clinic_contact is deliberately excluded from MCP_TOOL_NAMES
status: Done
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-25 07:58'
labels:
  - rail-critique
  - cosmetic
dependencies: []
priority: low
type: bug
project: whatsapp
ordinal: 227000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: app/wa/luna/prompts.py:423. Severity: cosmetic. 

HOW IT HAPPENS: The model reads the rule literally, reaches for get_clinic_contact to establish "this clinic matches", and gets a permission denial; it then either falls back to search_postings (normal) or drops the photo step.

WHAT IT COSTS: A denied call costs a little turn budget, and the prompt names a tool the model must never have. It is the same class of bug as the show_clinic_photos allowlist gap fixed on 2026-09-23, three lines above in luna_brain.py — that one was severe because the photo tool itself was denied; this one is not.

PROPOSED DIRECTION (not a decision): Drop get_clinic_contact from that clause. The durable fix is mechanical: a test that greps prompts.py for every tool name defined in tools_server.py and asserts each is either in MCP_TOOL_NAMES or on an explicit deliberately-unnamed list would have caught both occurrences.

VERIFICATION NOTES: CONFIRMED as a mismatch, DOWNGRADED on impact. prompts.py:423 reads "one of the clinics search_postings/list_clinics/get_clinic_contact just showed you match"; get_clinic_contact is defined at tools_server.py:726 and is the one tool explicitly kept out of MCP_TOOL_NAMES (luna_brain.py:85-87, TASK-424), and --allowedTools is an allowlist, so a call is denied. The finder's line is off by one (423, not 424) and the impact is overstated: the clause is a list of SOURCES that could have shown the match, not an instruction to call anything, and search_postings/list_clinics already satisfy the precondition — a denied tool call is a tool-result error the model routes around, not a turn failure. What is left is real but small: a wasted call against a tight turn budget, and the prompt advertising to the model that a clinic-contact tool exists, which is the single thing TASK-424 wanted it not to know.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified the sceptic's chain independently against the code (prompts.py:423, luna_brain.py:85-92/920, prompts.py:685 -- sceptic cited luna_brain.py:685 for the RULES join but it's actually in prompts.py, same substance). Confirmed with a throwaway repro that 'get_clinic_contact' in text is present in the SHOW_CLINIC_PHOTOS rule before the fix. Fixed: dropped '/get_clinic_contact' from the SHOW_CLINIC_PHOTOS rule string in app/wa/luna/prompts.py:423, leaving 'search_postings/list_clinics just showed you match'. No other files touched -- MCP_TOOL_NAMES, tools_server.py, luna_brain.py, send path and ledger are all unchanged. Added test_show_clinic_photos_rule_does_not_name_get_clinic_contact to tests/test_wa_luna_dialog_rules.py, next to the existing TASK-424 get_clinic_contact-exclusion assertion (lines 542-543): pulls the SHOW_CLINIC_PHOTOS rule out of P.RULES by prefix and asserts 'get_clinic_contact' not in it. Confirmed this assertion fails on the pre-fix string and passes after the edit. Ran narrow suite only: .venv/bin/python -m pytest tests/test_wa_luna_dialog_rules.py -q -> 174 passed. Did not run the full suite (that's the owner's separate verification pass) and did not touch the task's PROPOSED DIRECTION mechanical grep-all-tools test -- that's broader test-infra, out of scope for this one-line finding. Status left at In Progress; acceptance criteria not checked, no commit made -- owner reviews the diff.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Caveat for the owner: this fix is uncommitted working-tree state (git status shows app/wa/luna/prompts.py and tests/test_wa_luna_dialog_rules.py both modified, not committed) and the backlog file itself still shows Status In Progress with both ACs unchecked and an explicit note 'no commit made -- owner reviews the diff' -- so while the engineering substance of AC1/AC2 is done and verified (prompts.py:452-457, test at tests/test_wa_luna_dialog_rules.py:552-558 passes), formal sign-off/commit is still pending and the task should not be silently marked Done without that review happening.
<!-- SECTION:FINAL_SUMMARY:END -->
