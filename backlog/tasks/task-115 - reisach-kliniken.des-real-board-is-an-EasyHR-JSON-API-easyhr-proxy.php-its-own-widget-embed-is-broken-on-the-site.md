---
id: TASK-115
title: >-
  reisach-kliniken.de's real board is an EasyHR JSON API (easyhr-proxy.php); its
  own widget embed is broken on the site
status: Done
assignee: []
created_date: '2026-09-22 17:13'
updated_date: '2026-09-23 16:24'
labels: []
dependencies: []
ordinal: 115000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Run 120 (2026-09-22) zero-yield recon for clinics 78008/78071 (azubis-praktikanten-studenten.html) and 77607/77672 (stellenangebote.html), both www.reisach-kliniken.de. Both pages carry a raw, un-rendered developer comment left in the page instead of the actual widget markup: "Seite bearbeiten -> Element 'Code' -> diesen kompletten Block einfügen (HTML, CSS, JS zusammen). Vorher anpassen: - PROXY_URL: Pfad zu easyhr-proxy.php - DETAIL_PAGE_URL: ..." -- the site owner's own EasyHR widget deployment is incomplete/broken. The PHP proxy file still exists and works despite the missing front-end block: GET https://www.reisach-kliniken.de/easyhr-proxy.php returns clean JSON ({error, from_cache, count, positions[]}), verified live 2026-09-22: 22 real postings across BOTH sub-brands (Hochgrat Klinik and Adula Klinik -- the position's own 'jobgroup' field cleanly separates them, and 'where' names the department, e.g. 'Pflege'), including at least 2 'PFLEGEFACHKRAFT / GESUNDHEITS- und KRANKENPFLEGER (m/w/d)' rows. No existing adapter in this codebase handles EasyHR.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A new adapter fetches easyhr-proxy.php and maps positions[] to the standard row shape (title, org, loc, url, description, employmentType where available)
- [x] #2 jobgroup ('Hochgrat Klinik' vs 'Adula Klinik') is used to attribute each posting to the correct clinic_id pairing (78008/78071 vs 77607/77672 -- confirm which jobgroup maps to which pair against the registry before wiring, do not guess)
- [x] #3 Verified live: the adapter reads all current postings with correct titles, and nursing-relevant rows (Pflege where='Pflege') classify correctly downstream
- [x] #4 Red-green test using a frozen sample of the real easyhr-proxy.php JSON response, mutation-tested
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Fetch https://www.reisach-kliniken.de/easyhr-proxy.php live, confirm shape (positions[] with
   id/title/where/category/location/jobgroup/inserted_at; no description field).
2. Confirm jobgroup->clinic-pair mapping against data/registry/clinics.csv: Hochgrat Klinik ->
   77607/77672 (Hochgrat-Klinik Wolfsried), Adula Klinik -> 78008/78071 (Adula-Klinik Oberstdorf).
3. Confirm per-position description lives on a separate detail endpoint
   (easyhr-proxy.php?id=<id> -> position.editor, HTML) by fetching one live.
4. Add crawl_easyhr/parse_easyhr to crawlers/vendor_adapters.py, mirroring crawl_dvinci's shape and
   crawl_smartrecruiters' per-posting detail-fetch-for-description pattern. Filter by jobgroup
   (derived from the clinic's own name) so each clinic pair only ever gets its own sub-brand's rows --
   the site's own widget script does NOT filter by jobgroup (confirmed live), so this can't be
   delegated to routing/board grouping alone.
5. Register "easyhr" in vendor_adapters.VENDORS and routing.ADAPTERS.
6. Freeze real samples (list + one detail response, HR-contact fields redacted per the existing
   fixtures/board_samples/README.md convention) into tests/fixtures/board_samples/, write red-green
   tests in tests/test_vendor_adapters.py against them (jobgroup filtering both ways, url/city/
   description mapping, downstream classify.classify_role via section.job_confirmed_nursing).
7. Mutation-test: break the jobgroup filter on a /tmp copy, confirm the new tests go red, restore.
8. Run tests/test_vendor_adapters.py and tests/test_routing.py in full.
9. Registry write (careers_url for 77607/78008, ats_type=easyhr for all 4) needs a live Postgres
   write -- Supabase is down this session, so leave a ready-to-run script in /tmp instead of writing.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live findings (2026-09-23, re-verified -- task's own 2026-09-22 recon was already partly stale):
- easyhr-proxy.php confirmed live: 22 positions, 12 jobgroup=Hochgrat Klinik / 8 Adula Klinik / 2
  Reisach Kliniken (group-wide Initiativbewerbung -- belongs to neither clinic pair, correctly
  excluded by the jobgroup filter since it matches neither "hochgrat" nor "adula").
- Registry cross-check (data/registry/clinics.csv): 77607/77672 = Hochgrat-Klinik Wolfsried
  (Stiefenhofen), 78008/78071 = Adula-Klinik Oberstdorf (Oberstdorf). jobgroup "Hochgrat Klinik" ->
  77607/77672, "Adula Klinik" -> 78008/78071 -- matches the task's own hypothesis, confirmed not
  guessed.
- The site's own widget is NOT simply "broken": azubis-praktikanten-studenten.html (78071's
  careers_url) still has no widget markup at all (task's original finding still holds there), but
  stellenangebote.html (77672's careers_url) now has a FUNCTIONING script (fixed since 2026-09-22)
  that fetches easyhr-proxy.php with NO jobgroup filter and renders all 22 positions from both
  sub-brands together. Neither page is a reliable per-clinic source -- this adapter's own jobgroup
  filter (derived from the clinic's name) is the only correct routing signal, confirmed necessary by
  re-checking the live JS, not assumed from the task text.
- description has no field on the list endpoint; easyhr-proxy.php?id=<id> carries it as
  position.editor (HTML). Fetched per matched posting (same pattern as crawl_smartrecruiters).
- AC#3 downstream classify, real values: title "PFLEGEFACHKRAFT / GESUNDHEITS- und KRANKENPFLEGER
  (m/w/d)", section_labels=["Pflege"] -> section.job_confirmed_nursing == True ->
  classify.classify_role(..., nursing_section_confirmed=True) == ("pflegefachkraft",
  "pflegefachkraft:pflegefachkraft"). Asserted in
  tests/test_vendor_adapters.py::test_easyhr_filters_hochgrat_jobgroup_from_the_frozen_live_sample_and_fetches_description.

Files changed:
- crawlers/vendor_adapters.py: added parse_easyhr/crawl_easyhr (+ EASYHR_JOBGROUP/_easyhr_jobgroup_for),
  registered in VENDORS, module docstring vendor list updated.
- crawlers/routing.py: registered "easyhr" in ADAPTERS.
- tests/test_vendor_adapters.py: 4 new tests (parse_easyhr unit test, Hochgrat-jobgroup frozen-sample
  integration test incl. description + downstream classify, Adula-jobgroup frozen-sample test,
  unknown-clinic-name no-request test).
- tests/fixtures/board_samples/easyhr_proxy_reisach_list_sample.json,
  easyhr_proxy_reisach_detail_sample.json (frozen live samples, fetched 2026-09-23; HR contact
  name/phone redacted in the detail sample per this dir's existing README convention).
- tests/fixtures/board_samples/README.md: catalogued the two new fixtures.

Mutation test: cp crawlers/vendor_adapters.py to /tmp, removed the `p.get("jobgroup") != jobgroup`
condition (kept only the `id` check) -- both new jobgroup-filter tests went red (22 rows instead of
12/8, Adula/group-wide titles leaked into the Hochgrat board). Restored the original condition via a
targeted Edit (not git checkout -- this working tree has other uncommitted in-flight changes from the
session this task is part of) and confirmed tests/test_vendor_adapters.py -k easyhr is green again (4/4).

Test results: tests/test_vendor_adapters.py 76 passed (72 pre-existing + 4 new, no regressions).
tests/test_routing.py 9 passed (confirms "easyhr" -> crawl_easyhr resolves/imports correctly). Did
NOT run the full repo-wide `pytest -q`: it hangs on tests/test_app_api.py / tests/test_auth.py's
unmocked live Supabase REST calls while Supabase is down -- this is a separate, already-tracked
problem (see the concurrently-created TASK-130 in this same backlog, found while checking git status
mid-task: multiple other sessions are working this same checkout in parallel right now, unrelated to
TASK-115), not something in scope here.

Supabase-blocked write (per this session's own constraint -- confirmed live via curl: connect
succeeds, request hangs): clinics 77607/78008 need careers_url + ats_type=easyhr, 77672/78071 need
ats_type=easyhr only. Left a ready-to-run script at /tmp/task115_registry_easyhr_writeback.py
(uses pflege_jobs.sinks.EdgeSink.write_clinics + pflege_jobs.schema.CLINIC_SPEC, the same write path
crawlers/career_discover_exa.py's own ATS write-back uses -- default dry-run, prints the rows; pass
--write to push once Supabase is back). NOT run this session -- do not attempt against a hanging
Supabase per this session's own instructions.

AC#2's routing design: rather than inventing new pool-widening code, the write-back script gives
77607 the SAME careers_url as 77672 (and 78008 the same as 78071) -- crawlers/routing.py's _boards()
already groups clinics sharing an exact careers_url onto one board (same mechanism as the existing
TASK-118 Schongau/Weilheim precedent in vendor_adapters.py's VENDOR_ACCOUNT_POOLS comment), so no
new adapter-side pooling logic was needed for AC#2 -- crawl_easyhr's own per-clinic jobgroup filter
does the rest.

2026-09-23: Supabase back up. Ran /tmp/task115_registry_easyhr_writeback.py --write (fixed to use SUPABASE_SECRET_KEY) -- 4/4 clinic rows upserted (77607/78008 got careers_url+ats_type=easyhr+website, 77672/78071 got ats_type=easyhr only, per the script's own already-correct dry-run). Verified live with a real re-crawl (run 170): easyhr board -> 12 rows for Hochgrat-Klinik Wolfsried, 8 rows for Adula-Klinik Oberstdorf, both attributed via jobgroup as designed. No AC change needed (already 4/4) -- this closes the loop on the registry write the task's own implementation had left pending.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added crawl_easyhr/parse_easyhr to crawlers/vendor_adapters.py (registered in VENDORS + routing.ADAPTERS):
fetches easyhr-proxy.php live, filters by jobgroup (Hochgrat Klinik -> 77607/77672, Adula Klinik ->
78008/78071, confirmed against data/registry/clinics.csv, not guessed), fetches each matched
posting's own detail endpoint for description. Verified live 2026-09-23 against the real board (22
positions, correct jobgroup split, real Pflege postings) and downstream through
pflege_jobs.classify.classify_role (nursing_section_confirmed -> "pflegefachkraft"). 4 new tests in
tests/test_vendor_adapters.py against two frozen real-response fixtures
(tests/fixtures/board_samples/easyhr_proxy_reisach_{list,detail}_sample.json, PII redacted),
mutation-tested (jobgroup filter reverted on a /tmp copy -> tests red; restored -> green).
tests/test_vendor_adapters.py 76/76 passed, tests/test_routing.py 9/9 passed, no regressions.
Registry activation (careers_url for 77607/78008, ats_type=easyhr for all 4) needs a live Postgres
write Supabase can't currently take -- left /tmp/task115_registry_easyhr_writeback.py, ready to run
(--write) once Supabase is back.
<!-- SECTION:FINAL_SUMMARY:END -->
