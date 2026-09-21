---
id: TASK-87
title: >-
  Nothing retires a posting that left the board, and per-clinic freshness is
  invisible: stale rows read as coverage
status: To Do
assignee: []
created_date: '2026-09-21 04:26'
labels: []
dependencies: []
ordinal: 87000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21, two linked findings (M6 and M7).

Nothing closes a posting that left the board. Verification checks URL liveness, not board membership, so every junk row the audit found carries verify_status=live and a recent last_seen. Münchberg's two .io duplicates are status='open' 15 days after their last observation. When an adapter silently drops to 0 rows, nothing expires, and the database keeps looking healthy -- which is worse than reading empty, because a zero would at least be visible.

Per-clinic freshness is invisible. At least 12 clinics hold rows last seen 2026-09-05 (16 days) while sibling clinics were crawled 09-20/21: 36201 (21 rows), 66101 (7), 18811 (11 -- and 4 Gauting vacancies published 09-16 were never ingested), 17302 (2), 76201, 77406, 76114, 76203, 16215, 67804, 47401, 56403. The adapter is healthy on every one of these; the board simply moved. A clinic reads 'complete' purely because its board happened not to move during the gap.

Note the interaction with TASK-73 AC#6: mark_expired/expire_days was removed as dead code precisely because last_seen freezes at first sighting (app/crawl.py's inbox dedupe drops every re-crawled URL already on file), so a time-based expiry would have expired postings that still verify live. That reasoning still holds -- the fix here is board-membership-based, not time-based.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A posting absent from a board walk that SUCCEEDED is retired, distinct from a posting whose URL 404s; a walk that failed or was truncated must never retire anything
- [ ] #2 last_seen becomes meaningful: a re-crawl that sees an unchanged posting still records the sighting, so freshness reflects reality rather than first-sighting
- [ ] #3 Per-clinic last_seen age is exposed where coverage is judged (GET /api/coverage and the Pro clinics view), so a clinic cannot read 'complete' on 16-day-old rows
- [ ] #4 The ~30 currently-stale-open rows identified by the audit are retired, and the 12 named clinics are re-crawled; report the delta
<!-- AC:END -->
