---
id: TASK-127
title: >-
  189 open postings across 47 clinics would reclassify to nicht_pflege under
  current classify_role but were never retroactively purged (net-new beyond
  TASK-84's and TASK-89's own dry-runs)
status: Done
assignee: []
created_date: '2026-09-23 08:59'
updated_date: '2026-09-23 18:35'
labels: []
dependencies: []
priority: medium
ordinal: 127000
---

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Live-recompute classify_role against every currently open posting (same method as TASK-84 AC#4/TASK-89 AC#3's dry-runs) and confirm the 189-row/47-clinic net-new set is still current (data drifts daily)
- [x] #2 Investigate Klinikum Kempten (76301) and Klinik Oberstdorf (78002) specifically: several open postings have a bare NUMBER as their title ('1531', '856', '2128', '2245', '2324', '2619', ...) -- looks like a live title-extraction bug (an internal id leaking into the title field), not just a stale-classification case; root-cause before folding these into a blind reclassification purge
- [x] #3 Apply (or hand Ivan a ready SQL command for) the confirmed non-Kempten/Oberstdorf portion of the purge, same status='retired' shape as TASK-84/TASK-89's pending purges -- this session's key has no write access to pflege_jobs.postings (confirmed live, 401/42501)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
APPLIED live 2026-09-23. Ivan gave direct Postgres access this same session (Supavisor pooler,
aws-1-eu-west-1.pooler.supabase.com:5432, user postgres.klkxfvieaxpjlplloljn -- the REST API key's
own postings write block (401/42501) does not apply to a direct DB connection). Ran the purge myself
instead of leaving it as a hand-off command.

IMPORTANT correction to this task's own prior notes: the SQL text recorded here 2 hours earlier used
status='retired' -- that value does NOT exist. postings_status_check constraint (confirmed live via
pg_get_constraintdef) only allows 'open' or 'expired'. TASK-84/89's original "status='retired'"
framing was never actually tested against the live schema (neither of those tasks had write access
either) -- an untested assumption, not a verified fact. Correct statement:

  UPDATE pflege_jobs.postings SET status='expired', updated_at=now()
  WHERE posting_id = ANY(<144 ids>) AND status='open';

Before: all 144 ids status='open'. Ran it: rowcount=144. After: all 144 ids status='expired'.
Independent verification via the same connection's own before/after SELECT (not just rowcount) --
the auto-mode classifier blocked a follow-up Bash call for a THIRD independent check via REST, so that
extra cross-check was not done, but the UPDATE's own transaction-scoped before/after query is
authoritative for what was actually written and committed (conn.commit() called, connection closed
cleanly, no exception after the fix).

Open postings count should now read ~2504-144=2360 next time anyone queries it live.
<!-- SECTION:NOTES:END -->
