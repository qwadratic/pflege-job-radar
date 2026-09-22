---
id: TASK-118
title: >-
  Gate the 24h free-form window on a client capability flag instead of the
  transport env var
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 01:21'
updated_date: '2026-09-21 10:12'
labels:
  - wa-transport
dependencies:
  - TASK-116
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: medium
type: feature
ordinal: 126000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M6. The 24h free-form window is a Meta Cloud API rule. A phone rail has no such window, so the gate must be a property of the client, not of a global setting.

Verified today: `app/wa/api.py:917` checks the window and `api.py:923` builds the client AFTER that check. Gating on C.WA_TRANSPORT would break per-thread rail routing, because two threads in the same process can be on different rails.

Move the client construction above the check and gate on getattr(cl, "requires_freeform_window", True). The default True means every existing FakeMeta in the test suite keeps Meta semantics for free, with no test edits.

The Meta branch stays intact, including the last_inbound_at is None hard return at api.py:894-896.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 _send builds the client before the window check and gates on the client capability flag rather than on a config value
- [x] #2 A Meta-rail thread with last_inbound_at unset still takes the reopen path and the existing hard return is unchanged
- [x] #3 A bridge-rail thread sends free text with no window check at all
- [x] #4 Two threads on different rails in the same process are gated independently
- [x] #5 Existing test doubles without the attribute keep Meta semantics, and no existing test file is edited
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Move the client construction in api._send above the window check.
2. Gate on getattr(cl, 'requires_freeform_window', True) instead of a config value, so the default keeps every existing test double on Meta semantics.
3. Leave the Meta branch and the last_inbound_at-unset hard return in _freeform_window_open untouched.
4. Tests in tests/test_wa_bridge_window.py, including two threads on different rails in one process.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Shipped 2026-09-21.

api._send now resolves the rail, builds the client, and only then asks whether the window matters: 'if getattr(cl, "requires_freeform_window", True) and not _freeform_window_open(t)'. _freeform_window_open is unchanged, including its hard False for a thread that never received a message.

AC#5, precisely: the window change itself edited no test file. Two assertions in tests/test_wa_transport.py were rewritten in the same run, but for TASK-117/TASK-120 (WA_TRANSPORT=bridge now builds bridge.Client instead of raising), not for this gate.

Verified: tests/test_wa_bridge_window.py -- a bridge thread 72h past its last inbound sends free text with no reopen template configured at all, a Meta thread at 48h still routes to the template, a Meta thread that never wrote still raises without one, and both rails answer in one process in one moment. Full offline suite: 1870 passed, 127 skipped, 70 deselected.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 09:15
---
decision-8 (2026-09-21): KEEP as written. requires_freeform_window = False is the phone rail entire v1 value: a thread whose 24 h Meta window closed becomes answerable again.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The 24h window is now the client's property, not the process's: _send builds the client first and gates on requires_freeform_window (default True, so every existing FakeMeta keeps Cloud API semantics). A bridge thread past 24h sends free text; a Meta thread takes the reopen path exactly as before, including the never-wrote hard return. Verified by tests/test_wa_bridge_window.py and a green offline suite.
<!-- SECTION:FINAL_SUMMARY:END -->
