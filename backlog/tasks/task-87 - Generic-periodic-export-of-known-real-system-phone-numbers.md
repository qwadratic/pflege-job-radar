---
id: TASK-87
title: Generic periodic export of known real-system phone numbers
status: Done
assignee: []
created_date: '2026-09-13 15:45'
updated_date: '2026-09-13 15:53'
labels: []
dependencies: []
ordinal: 87000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-75's WA_REAL_SYSTEM_PHONES_FILE needs a producer -- nothing populates it yet. Build a fully generic, operator-configured export tool (no hardcoded schema/system knowledge, same discipline as external_contacts.py) plus deploy templates (systemd .service/.timer, matching deploy/pflege-wa.service's existing convention) so an operator can point it at their own real database and query, on a schedule. Also fills a gap noticed alongside this: TASK-78 (catchup) and TASK-85 (followups) never got deploy templates either -- add those too while establishing the pattern.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New module exports phone numbers from an operator-given sqlite db + SQL query into the exact plain newline-delimited format app/wa/routing.py already reads -- no table/column names hardcoded anywhere in this repo
- [x] #2 Opens the source database strictly read-only (SQLite URI mode=ro, same pattern as shadow_run.py) and never writes back to it
- [x] #3 Output write is atomic (temp file + rename) so a reader never sees a half-written file mid-export
- [x] #4 Phones are canonicalized (app.wa.meta.canonicalize_phone) and deduplicated before writing
- [x] #5 New deploy/*.service + *.timer template pairs for: the export job, catchup.py, and followups.py -- matching deploy/pflege-wa.service's existing comment/install-instructions convention, generic paths, not installed anywhere by this task
- [x] #6 Unit tests cover: a working export, read-only enforcement, atomic write on failure, canonicalization/dedup, an empty result set
- [x] #7 Full offline suite stays green
- [x] #8 docs/whatsapp.md documents the export tool and points at the new deploy templates
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/luna/export_known_phones.py: export(db_path, query, out_path, default_country_code=None) -- opens db_path read-only (mode=ro, same as shadow_run.py), runs the operator's exact query, canonicalizes+dedups+sorts the first column of each row, writes atomically (tempfile + os.replace). Never hardcodes any table/column name -- same discipline as external_contacts.py. 9 new tests (built and verified independently, including a real read-only-enforcement test that a mutating query raises OperationalError, and an atomic-write-on-failure test that a pre-existing output file survives untouched). deploy/pflege-wa-catchup.*, deploy/pflege-wa-followups.*, deploy/known-phones-export.* (6 files) -- systemd .service/.timer template pairs matching pflege-wa.service's established convention, none installed anywhere. Found and fixed one real bug during review: known-phones-export.service's ExecStart left  unquoted -- systemd word-splits an unquoted variable on whitespace before exec, and a SQL query always contains spaces, so this would have silently broken the --query argument into multiple argv entries. Quoted all three variables and added a comment explaining why, so a future edit doesn't 'clean up' the quotes. Offline suite: 1147 passed, same 5 pre-existing unrelated failures.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
TASK-75's WA_REAL_SYSTEM_PHONES_FILE finally has a producer: a fully generic export tool (operator supplies the db path and exact SQL query, nothing about a real schema is ever named in this repo) with the same read-only/atomic-write safety properties established elsewhere in this codebase. Also closed a gap noticed alongside it -- TASK-78 and TASK-85 never got deploy templates -- while establishing the periodic-job unit convention. Caught and fixed a real systemd quoting bug during review that would have silently broken the export job's SQL query argument in production.
<!-- SECTION:FINAL_SUMMARY:END -->
