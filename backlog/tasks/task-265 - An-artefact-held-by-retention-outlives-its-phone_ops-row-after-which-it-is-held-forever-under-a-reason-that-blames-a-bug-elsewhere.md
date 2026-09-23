---
id: TASK-265
title: >-
  An artefact held by retention outlives its phone_ops row, after which it is
  held forever under a reason that blames a bug elsewhere
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-23 08:03'
updated_date: '2026-09-23 12:50'
labels:
  - rail-critique
  - degraded
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 212000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Found independently by 2 review lenses. Location: bridge/retention.py:86. Severity: degraded. 

HOW IT HAPPENS: A brain-driven send_gallery fails at 03:00; nobody resolves the op. Its screenshots and recording pass 14 days and are correctly held (OP_FAILED, no client_msg_id, no resolved_at). At day 30 the phone_ops row is swept. From day 31 the files are held forever under "op_id not found in phone_ops", and every hourly pass writes a fresh retention_held line about them.

WHAT IT COSTS: The failures review-before-delete exists to preserve are the exact ones that become permanently undeletable, the held reason misdirects whoever reads the journal, and the hourly re-logging is noise on top of unbounded disk growth on the machine that owns the handset.

PROPOSED DIRECTION (not a decision): Invert the relationship so a phone_ops row outlives any artefact naming it — or write the verdict onto the artefact when the op reaches a terminal state, instead of re-deriving it from a row that will be gone. At minimum distinguish "op row aged out" from "op row missing unexpectedly" so the reason tells the truth, and log each hold once rather than hourly.

VERIFICATION NOTES: CONFIRMED, and the code's own comment is the wrong way round. retention.py:82-86 asserts "phone_ops rows outlive these files by design (LEDGER_RETENTION_DAYS=30 vs 14 days of artefacts) -- reaching here means something deleted the row out from under its own artefact, which is a bug elsewhere" — but the 14 days is only when a file becomes a CANDIDATE (driver.py:46, list_screenshot_candidates), and a held file stays on disk indefinitely, so at day 30 ledger.sweep's `delete from phone_ops where finished_at is not null and finished_at < ?` (ledger.py:1103-1105) removes the row under a file that is still there. From then on op_status returns None and classify_op_artifact holds forever under a reason that is factually wrong. The escalation-shot half is the same: sweep deletes journal rows at the same cutoff (ledger.py:1080), so escalation_shot_index loses the entry and classify_escalation_shot returns "no escalation_shot journal entry for this file" permanently. Reachability is easy: a send_gallery op mints no client_msg_id (the tool bypasses begin_turn entirely), so resolve_op is the only way to settle it, and nobody is resolving the brain's own gallery failures. _sweep_one_kind re-logs every held file as retention_held on each hourly maintenance pass (server.py:403).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fixed. In bridge/retention.py, classify_op_artifact (and classify_escalation_shot for the journal-row-missing case) now distinguish "op/journal row aged out past LEDGER_RETENTION_DAYS while the artefact was still held" from the old always-on "op_id not found"/"no escalation_shot journal entry" text, using the artefact's own file mtime vs L.LEDGER_RETENTION_DAYS (both new optional path/now params, threaded through from _sweep_one_kind/review_and_sweep -- a direct caller that omits them keeps the old generic reason, so no existing call site broke). Rewrote the retention.py:82-90 comment that asserted rows always outlive artefacts "by design" -- that only holds for HAPPY files; HELD files have no such bound and routinely outlive ledger.sweep()'s 30-day cutoff.

Left the hourly re-logging of held files alone -- the module's own docstring says that's deliberate ("so a growing backlog is visible in the journal, never silently kept or silently dropped"), not a bug.

Did not touch bridge/executor.py, dispatcher.py, or ledger.py's sweep/schema -- diagnostics-only fix, matches this task's "at minimum" floor. TASK-277 is where the structural "keep the row alive" fix belongs.

Tests: added test_a_swept_op_row_is_held_with_the_aged_out_reason_not_the_missing_row_reason and test_a_swept_escalation_shot_journal_row_is_held_with_the_aged_out_reason to tests/test_bridge_retention.py -- both enqueue+fail/journal an op, age the artefact file and the ledger row past LEDGER_RETENTION_DAYS via os.utime + ledger.sweep(), then assert the new "aged out" reason. Verified both fail (TypeError: takes N positional arguments but 4 were given) against the pre-fix signature and pass with the fix. Also had to give test_review_and_sweep_deletes_happy_holds_the_rest_and_journals_every_hold's "mystery_shot" a real on-disk file (tmp_path) since _artifact_aged_out now stats the path whenever review_and_sweep threads a real `now` through -- it previously used a fake nonexistent Path, which is fine under the old signature but not once a real stat is required by the new "aged out" check; behavior/assertions unchanged, this was a test-fixture-only accommodation of the same fix.

Ran: .venv/bin/python -m pytest tests/test_bridge_retention.py -q -> 26 passed. Did not run the full suite (owner's one verification pass covers that).
<!-- SECTION:NOTES:END -->
