---
id: TASK-69
title: Wire an optional external contact CRM into clinic contact discovery
status: Done
assignee:
  - '@claude'
created_date: '2026-09-12 18:44'
updated_date: '2026-09-12 18:57'
labels: []
dependencies: []
ordinal: 69000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Follow-up to TASK-64. That task shipped best-effort website/JD-scrape contact discovery on the assumption no other contact source was available. It turns out this same host also runs a separate, real clinic-contact CRM outside this repo -- a proper database (companies/clinics, people, and per-person contact channels with a source/evidence trail), maintained by real outreach work, not scraped. Its exact location and internal schema are operationally sensitive (this repo is public), so this integration is entirely config-driven: unset by default, and never hardcodes which real system it points at. Ivan confirmed: when such a source is available, prefer it over a guessed website scrape. Read-only access for design/verification purposes required an explicit Bash permission-rule addition (the auto-mode classifier blocks unscoped production reads); Ivan added that rule and authorized the verification read.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New app/wa/luna/external_contacts.py queries an operator-configured external CRM (WA_EXTERNAL_CONTACT_DB env var, unset = complete no-op; read via WA_EXTERNAL_CONTACT_READER, default 'sudo sqlite3') for a Bavaria-scoped clinic match (fuzzy name match via rapidfuzz, bundesland='Bayern') and its best contact email, preferring a pflege_leadership/hr_leadership/hr role_category contact -- documented as a generic integration contract, not tied to any specific named system or real file path
- [x] #2 app/wa/luna/contacts.py:discover_contact tries the external CRM first, ahead of enr_contact_emails/website/description-rescan, falling through gracefully (same convention as the existing website-fetch source) on any failure -- unconfigured, unreachable, no read access, or a deploy target without it at all
- [x] #3 Unit tests cover the fuzzy-match threshold, role-category preference, generic-mailbox fallback, the unset-by-default no-op path, and the discover_contact chain ordering (external CRM wins and skips every other source; a miss or an error falls through), all against a fake subprocess runner -- no test touches any real database
- [x] #4 Verified once against a real configured instance for a real Bavarian clinic name from the live board, without committing that instance's real path, schema-origin claims, or any contact value into the repo
- [x] #5 Full offline suite stays green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New app/wa/luna/external_contacts.py: DB_PATH from WA_EXTERNAL_CONTACT_DB (unset by default = no-op), read via WA_EXTERNAL_CONTACT_READER (default sudo sqlite3); fuzzy-match clinic name (rapidfuzz, bundesland=Bayern scoped) -> best contact email, preferring pflege_leadership/hr_leadership/hr role_category. Documented as a generic schema contract, no real system named or pathed.
2. Wire into contacts.py:discover_contact as first-tried source, graceful fallthrough on any failure.
3. Offline tests against a fake subprocess runner; one live verification against a real configured instance.
4. Full offline suite, backlog notes/finalize.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live-verified against a real, separately-configured external CRM instance: a real Bavarian clinic name from the live board (fetched via the board's own public anon key) resolved to a real contact end-to-end, confirming the fuzzy-match + role-preference + confidence-tiering pipeline works correctly. Neither the instance's real location/schema-origin nor the resolved contact value is recorded here or anywhere in the repo -- this integration is config-driven specifically so that no real system's identity needs to appear in a public repo.

Unrelated side-note fixed along the way: a jq merge command (used while sorting out read access for verification) silently failed and its fallback branch overwrote .claude/settings.json, wiping the caveman plugin config. Restored, with Ivan's explicit authorization for that corrective write.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/wa/luna/external_contacts.py adds an opt-in (WA_EXTERNAL_CONTACT_DB, unset by default) external clinic-contact CRM lookup: fuzzy clinic-name match scoped to Bavaria, preferring a role-classified (pflege_leadership/hr_leadership/hr) contact. Wired into contacts.py:discover_contact as the first-tried, highest-confidence source, falling through gracefully like the existing website source on any failure. Deliberately generic: no real system is named, no real file path or schema-origin is asserted, so the public repo never reveals whether/where an operator has this configured. 15 new offline tests (fuzzy-match threshold, role preference, generic-mailbox fallback, unset-by-default no-op, chain-ordering/short-circuit, error fallthrough) all against a fake subprocess runner. Live-verified once against a real configured instance for a real Bavarian clinic name -- found a real, role-matched contact, without any of that instance's specifics entering the repo. Offline suite: 1003 passed, same 6 pre-existing unrelated failures.
<!-- SECTION:FINAL_SUMMARY:END -->
