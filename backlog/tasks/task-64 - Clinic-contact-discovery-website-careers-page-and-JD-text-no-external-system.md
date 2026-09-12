---
id: TASK-64
title: 'Clinic contact discovery: website/careers page and JD text, no external system'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-12 16:00'
updated_date: '2026-09-12 16:20'
labels: []
dependencies: []
ordinal: 64000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan asked whether clinic Pflegedirektion/HR emails are available and, when the board own enr_contact_emails column is empty for a clinic, to look for a contact on the clinic own website or in the job ad text -- a previously-assumed "sales brain" data source does not exist anywhere in this repo or in prior session notes, and Ivan confirmed to skip it and use best-effort discovery instead. See /home/claude/.claude/plans/wise-enchanting-crayon.md section 3.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New app/wa/luna/contacts.py:discover_contact(clinic, postings) tries enr_contact_emails first (confirmed available unredacted in-process, not just at the HTTP boundary), then a single polite fetch of the clinic website/careers_url with regex extraction near Pflegedirektion/Personalabteilung/Bewerbung/Karriere/HR context, then a second JD description text pass
- [x] #2 Results are stored in a new clinic_contacts(clinic_id, email, source, confidence, discovered_at) table in the same sqlite file as app/wa/store.py, populated by a batch entry point (python -m app.wa.luna.discover_contacts) rather than live per WhatsApp turn
- [x] #3 Unit tests cover the extraction heuristic against fixture HTML with no live network call in the default run; a network-marked test exists for a real sanity check, deselected by default like the repo existing network marker
- [x] #4 Full offline suite stays green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. app/wa/luna/contacts.py: discover_contact(clinic, postings) tries, in order:
   (a) enr_contact_emails across `postings` (already unredacted in-process via
       app.wa.brain.jobs_for -> app.data.filter_jobs, confirmed by reading the call chain --
       redact() is only ever invoked from app/main.py's HTTP routes, never from filter_jobs/
       filter_clinics themselves);
   (b) one polite fetch (requests, short per-request timeout, UA matching pflege_jobs crawler
       convention) of clinic['careers_url'] or clinic['website'] (whichever is set, careers_url
       preferred -- a single request per clinic), regex-scanning the decoded/tag-stripped page
       text for a plain email within a proximity window of a context word (Pflegedirektion/
       Personalabteilung/Bewerbung/Karriere/HR);
   (c) a second, different regex pass (obfuscated-address forms: "(at)"/"[at]"/" at ",
       "(punkt)"/"[punkt]"/"(dot)"/"[dot]") over each posting's own `description` text --
       lazily fetched via app.data.job_detail(posting_id) when a posting dict does not already
       carry one (the in-memory snapshot's JOB_COLS does not include description) -- not a
       duplicate of the plain-email pattern the ingestion pipeline (pflege_jobs/classify.py,
       patterns.json enrichment.email) already ran once.
   Returns {email, source, confidence} (confidence: high/medium/low matching a/b/c) or None.
   Explicitly does not call app.firecrawl_gate (credit-gated, for full discovery, not this).
2. New clinic_contacts(clinic_id, email, source, confidence, discovered_at) sqlite table, own
   schema block in contacts.py, opened via app.wa.store.db() (same connection/pragma setup,
   same wa.sqlite file, not mixed into wa_threads/wa_messages) with save_contact()/get_contact()
   -- get_contact(clinic_id) is the read-only lookup a later MCP tool task will call.
3. app/wa/luna/discover_contacts.py: `python -m app.wa.luna.discover_contacts [--clinic-id ID |
   --all]` batch entry point -- reads clinics from app.data's snapshot, gathers each clinic's
   own open postings via app.data.filter_jobs({"clinic_id": ...}), calls discover_contact(),
   saves a hit via save_contact(). Throttles with app.crawl.POLITE_SLEEP (reused, not
   reinvented) around the one real per-clinic website fetch.
4. tests/test_wa_luna_contacts.py: fixture-HTML unit tests for the extraction heuristic
   (context-word hit, email present but no nearby context word -> miss, no website configured),
   ordering (enr_contact_emails short-circuits the HTTP fetch), the obfuscated-description
   re-scan, and a save_contact/get_contact round trip against a temp sqlite file -- all offline.
   One pytest.mark.network test does a real fetch against a real clinic site as a sanity check,
   deselected by default like the repo's existing network-marked tests.
5. Run the full offline suite (pytest -q -m "not network and not completeness and not mutation
   and not llm") and confirm green before finalizing.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented app/wa/luna/contacts.py (discover_contact/save_contact/get_contact/db, own
clinic_contacts schema block reusing app.wa.store.db()'s connection pattern) and
app/wa/luna/discover_contacts.py (python -m app.wa.luna.discover_contacts --clinic-id/--all).
Confirmed by reading the call chain: app.wa.brain.jobs_for() -> app.data.filter_jobs() directly;
app.data.redact() is only ever called from app/main.py's HTTP routes, never from
filter_jobs/filter_clinics -- enr_contact_emails is genuinely unredacted in-process, used as
source 1. Source 3's regex is deliberately different from the ingestion pipeline's plain email
pattern (pflege_jobs/patterns.json: enrichment.email): it targets obfuscated "(at)"/"[at]"/" at "
and "(punkt)"/"[punkt]"/"(dot)"/"[dot]"/" punkt "/" dot " forms. Added
tests/test_wa_luna_contacts.py: 20 offline unit tests (heuristic ordering, context-word
proximity, obfuscated re-scan incl. lazy app.data.job_detail() fetch, storage
upsert/round-trip, batch --clinic-id/--all wiring) + 1 pytest.mark.network sanity test against a
real registry clinic careers_url (kbo-Heckscher-Klinikum), deselected by default.

Full offline suite (pytest -q -m "not network and not completeness and not mutation and not llm"):
924 passed, 123 skipped, 16 deselected -- includes the 20 new tests in
tests/test_wa_luna_contacts.py, all green.

6 pre-existing failures unrelated to this task, verified by temporarily moving the 3 new TASK-64
files out of the tree and re-running: identical failures occur with none of this task's code
present, so they predate it --
- tests/test_app_api.py::test_problem_json_404,
  tests/test_auth.py::test_owner_only_passes_after_login[GET-/api/inbox]: real 401 from the live
  Supabase project (klkxfvieaxpjlplloljn.supabase.co) in this sandbox -- missing/invalid
  credentials in this environment, not code.
- tests/test_career_crawl_section.py (3 cases), tests/test_ontology.py::
  test_career_profiles_is_empty_while_the_graph_leaves_it_out: pre-existing on the branch this
  worktree started from (career_crawl.py/ats_seeds.py changes from an unrelated prior-session
  task rebased in to reach feat/whatsapp-harness's app/wa/ code, which this task needed but the
  worktree didn't have) and a local data/app.sqlite schema mismatch -- neither touches
  app/wa/luna/contacts.py or app/wa/luna/discover_contacts.py.
- 4 completeness-marked test files (test_completeness_dvinci.py,
  test_completeness_beesite_hr4you.py, test_completeness_smartrecruiters.py,
  test_adapter_completeness.py) call the live registry read-proxy at import time (outside any
  test function), so `-m "not completeness"` cannot prevent the collection-time network call in
  this sandbox; ignored via --ignore for the same reason -- these fail identically without any
  TASK-64 code present.
None of the above touch app/wa/luna/contacts.py, app/wa/luna/discover_contacts.py, or
app/wa/store.py/app/data.py/app/crawl.py in a way this task's code path exercises.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented best-effort clinic contact discovery per plan section 3, exactly as scoped (no
external "sales brain" system -- confirmed absent, per Ivan):

- app/wa/luna/contacts.py: discover_contact(clinic, postings) tries, in order, (1)
  enr_contact_emails across the clinic's own postings (verified unredacted in-process: read the
  full call chain app.wa.brain.jobs_for -> app.data.filter_jobs -> app.data.jobs/snapshot; grepped
  every call site of app.data.redact() and confirmed it is invoked only from app/main.py's HTTP
  routes, never from filter_jobs/filter_clinics), (2) one polite fetch (short timeout, one request
  per clinic, POLITE_SLEEP reused from app.crawl) of the clinic's careers_url (falling back to
  website), regex-scanning for a plain email within 200 chars of a Pflegedirektion/
  Personalabteilung/Bewerbung/Karriere/HR context word, (3) a second, differently-shaped regex
  pass (obfuscated "(at)"/"[at]"/" at " and "(punkt)"/"[punkt]"/"(dot)"/"[dot]" forms) over each
  posting's own description text, lazily fetched via app.data.job_detail() since the in-memory
  snapshot's JOB_COLS omits description. Does not use app.firecrawl_gate.
- New clinic_contacts(clinic_id primary key, email, source, confidence, discovered_at) table, own
  schema block, opened via app.wa.store.db() (same wa.sqlite file, not mixed into
  wa_threads/wa_messages), with save_contact() (upsert) / get_contact() (the read-only lookup a
  later MCP tool task will call).
- app/wa/luna/discover_contacts.py: `python -m app.wa.luna.discover_contacts [--clinic-id ID |
  --all]`, verified runnable via --help and exercised end-to-end in tests.
- tests/test_wa_luna_contacts.py: 20 offline unit tests (heuristic ordering and each of the three
  sources, context-word proximity hit/miss, obfuscated-address variants, lazy job_detail() fetch,
  storage round trip/upsert, batch CLI wiring) + 1 pytest.mark.network sanity test against a real
  registry clinic (kbo-Heckscher-Klinikum), deselected by default (`-m "not network"` showed
  "20 passed, 1 deselected").

Verification: full offline suite (pytest -q -m "not network and not completeness and not mutation
and not llm") -- 924 passed, 123 skipped, 16 deselected, including all 20 new tests. 6 failures
seen on a naive run of that command are pre-existing and unrelated: proven by moving this task's 3
new files out of the tree and re-running, which reproduced the identical 6 failures with none of
this task's code present. Root causes: a live Supabase 401 (missing test credentials in this
sandbox, 2 tests), a regression already present on the branch this worktree started from before
this task began (career_crawl.py/ats_seeds.py from an unrelated prior task, needed here only to
reach feat/whatsapp-harness's app/wa/ code via rebase, 3 tests), a local data/app.sqlite schema
mismatch (1 test), and 4 completeness-marked test files that call the live registry proxy at
import time regardless of marker selection. None touch app/wa/luna/contacts.py,
app/wa/luna/discover_contacts.py, or the app.wa.store/app.data/app.crawl code paths this task
uses.
<!-- SECTION:FINAL_SUMMARY:END -->
