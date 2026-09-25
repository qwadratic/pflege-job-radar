---
id: TASK-82
title: >-
  _txt() crashes on a numeric JSON-LD field and aborts the whole board walk:
  both Munich Klinik sites hold zero real vacancies
status: Done
assignee: []
created_date: '2026-09-21 04:25'
updated_date: '2026-09-23 11:03'
labels: []
dependencies: []
ordinal: 82000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21. One line, 17 postings, 1,066 beds.

crawlers/vendor_adapters.py:92 _txt() does re.sub(r'<[^>]+>', ' ', s or ''). München Klinik emits "postalCode":81545 as a JSON NUMBER, not a string, so parse_job_page (crawlers/vendor_adapters.py:597) raises TypeError: expected string or bytes-like object, got 'int' on the FIRST posting page it fetches, which aborts the entire board walk. Reproduced live by the audit.

Consequence: clinic 16201 (München Klinik Schwabing) and 16203 (München Klinik Neuperlach, 545 beds) both hold zero real nursing vacancies. 16201's 15 'postings' are all marketing pages, not jobs. Independently confirmed earlier the same day: tools/compare_adapter_fc.py 16203 reports 'adapter: 175 rows' while the database holds 0 for that clinic.

Fix is s = '' if s is None else str(s). The reason this is worth its own task rather than a drive-by is that the same class of bug -- a JSON-LD field arriving as a number, list or dict where the parser assumes a string -- is likely present in the sibling helpers, and a single crash anywhere in a board walk currently costs the whole board with no recorded failure.

Related registry correction from the same audit: 16201 and 16203 should point at https://www.muenchen-klinik.de/stellenmarkt/ rather than /jobs/ -- the full list is inline as 'var allJobs' (57 jobs), so no render rung is needed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 _txt() coerces any non-string scalar rather than raising, and a test pins the numeric-postalCode case with a real JSON-LD fixture
- [x] #2 Every sibling field reader in parse_job_page that assumes a string is audited for the same numeric/list/dict-shaped input, not just the one that crashed
- [x] #3 A crash inside one posting page cannot silently abort the whole board walk with a success result: the failure is recorded (crawl_issue) and the walk's outcome reports what it lost
- [ ] #4 16201 and 16203 yield their real nursing vacancies after the fix; report the count for each, and purge 16201's 15 marketing-page rows
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC#1 закрыт 2026-09-21 в коммите 38287cc (правка сделана оркестратором вручную, до того как задача попала в какой-либо рабочий раунд, поэтому галочка осталась непроставленной).

_txt() (crawlers/vendor_adapters.py:92) теперь приводит любое не-строковое значение JSON-LD вместо падения: bare scalar через str(), массив склеивается через ", ", типизированный литерал {"@value": ...} разворачивается, объект без @value даёт None. Причина в комментарии на месте: JSON-LD допускает все три формы, поэтому это корректный разбор, а не защитная обёртка.

Тест: tests/test_vendor_adapters.py::test_txt_handles_the_non_string_json_ld_shapes_that_aborted_whole_board_walks пинит обе живые формы отказа -- числовой "postalCode":81545 с muenchen-klinik.de и массив с komm-ins-klinikland.de, плюс обычный случай и falsy-но-реальный 0.

AC#2, #3, #4 остаются открытыми и это не формальность:
- #2 требует аудита соседних читателей полей в parse_job_page на те же формы -- не делался.
- #3 требует, чтобы падение на одной странице вакансии не обрывало весь обход борда с результатом "успех". Механизм записи деградации появился в TASK-85 (крах и degraded-тег), но связка именно для исключения внутри parse_job_page не проверена.
- #4 требует живых чисел по 16201 и 16203 и чистки 15 маркетинговых строк 16201. Упирается в TASK-86: обеим клиникам нужен careers_url https://www.muenchen-klinik.de/stellenmarkt/ вместо /jobs/, и этот перевод пока в dry-run, в прод не применён.

ROUND 2026-09-23: AC#2 and AC#3 fixed, tested, mutation-tested. AC#4 partially delivered; remaining piece filed as TASK-129.

AC#2 CHECKED. Audited every JSON-LD field reader alongside parse_job_page (the one that crashed). Found and fixed 3 real, unaudited siblings in crawlers/vendor_adapters.py:
  1. _sane_date(raw) -- fixed at the root: `d = (raw or "")[:10]` had the identical non-string-crash class _txt's own fix (AC#1) targeted, just never audited. Routed through _txt first (`d = (_txt(raw) or "")[:10]`), which closes every one of the ~15 call sites across this file that feed JSON-LD datePosted into this one function, not just the two touched directly.
  2. parse_helix_detail (helix vendor's own detail-page JSON-LD reader, e.g. bezirk-unterfranken.helixjobs.com) read addressLocality/postalCode/addressRegion raw, unlike parse_job_page's already-fixed version -- wrapped in _txt() to match.
  3. parse_job_page's own n_url = n.get("url") -- urlparse(n_url) raises on a non-string JSON-LD "url" (a list of alternates, a typed literal); added an isinstance(str) guard.
  Other JSON-LD readers in this file (erecruiter, concludis-widget) call parse_job_page itself, already covered; dvinci/smartrecruiters are vendor-proprietary JSON APIs, not JSON-LD, not exposed to this specific looseness class.
  Tests: tests/test_vendor_adapters.py -- test_sane_date_handles_the_same_non_string_json_ld_shapes_txt_does, test_parse_helix_detail_handles_non_string_json_ld_address_fields, test_parse_job_page_ignores_a_non_string_json_ld_url_field. Each mutation-tested (revert the specific fix via a /tmp-backed in-place edit, confirm red, restore from /tmp, confirm green -- never git).

AC#3 CHECKED. One posting page's parse crash can no longer cost the whole board. crawlers/vendor_adapters.py: _wp_job_rows gained a `crashes` list (threaded the same way `seen`/`titles` already are, through both its own recursive calls and all 3 external call sites in crawl_wp_jobs); `j = parse_job_page(...)` wrapped in try/except -- a crash is appended to `crashes` and that one URL is skipped, every other page in the same walk still returns its row. crawl_wp_jobs surfaces a non-empty `crashes` as `out.page_crashes` (new _BoardTotalRows attribute, same convention `.degraded` already uses, including through the hr4you-fallback reassignment branch). app/crawl.py's _fetch_board reads it and records a crawl_issue kind='degraded' naming the count and the first error, exactly mirroring the existing `.degraded` handling right above it.
  Test: test_wp_job_rows_survives_a_single_page_parse_crash -- two-URL board, page A's parse forced to raise, asserts the crash is captured in `crashes` and the function returns normally instead of propagating. Mutation-tested (remove the try/except -> red -> restore -> green).

AC#4 PARTIAL. Registry correction (16201/16203 -> /stellenmarkt/) was already live (TASK-86). Triggered a real production crawl scoped to these two clinics (run_id=166, app.crawl.execute, no shortcuts): 69 raw rows, 39 matched observations, 0 new postings on either clinic. Traced why: all 39 upserted onto EXISTING posting rows already attributed to clinic_id=16202 (first_seen back to 09-05, long before this session) -- posting_observations' unique(source_id, source_ref) means the SAME job URL always upserts the SAME row, and clinic_id is set once at first-create, never re-run by a later crawl even after a sibling clinic's own careers_url is corrected to the same board. This is a related but distinct mechanism from TASK-81/96/99/100's first-crawl attribution collapse (already fixed) -- filed as TASK-129 with full live evidence, not fixed here (needs a real design decision on re-attribution, not a drive-by).
  16201's marketing-page rows: purged. Live count today was 4 (not the original audit's 15 -- most had already resolved via other work this session), all confirmed marketing/informational copy (JobPlus benefits pages), none real vacancies. Prepared as a verify-op script; production write denied by the auto-mode classifier, handed to Ivan as a ready command (/tmp/task82_purge.py) -- same pattern as this session's other blocked writes.

Targeted tests: 108 passed (test_vendor_adapters.py, test_crawl_board_retry.py, test_cli_inbox_probe.py, test_cli_inbox_drain.py). Full offline suite last green this session at 1435 passed, 0 failed (re-run deferred per Ivan's standing instruction to only run it at the very end).

Purge applied live by Ivan 2026-09-23: /tmp/task82_purge.py -> {'ok': True, 'verify': 4}. 16201's 4 marketing rows retired (verify_status='gone', status auto-expired).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
_txt() coercing non-string JSON-LD values (AC#1) was already fixed before this round. This round: audited every sibling field reader (AC#2) and fixed 3 real unaudited crash risks -- _sane_date (the root cause, ~15 call sites closed at once), parse_helix_detail's address fields, parse_job_page's own "url" field -- each mutation-tested. Added crash containment (AC#3): one posting page's parse crash is now caught, recorded (crawl_issue kind=degraded via a new .page_crashes signal on the existing _BoardTotalRows/.degraded convention), and skipped, instead of aborting every other row the same board walk already found. AC#4: registry already pointed 16201/16203 at the real board; a live production crawl (run_id=166) proved they still show 0 real vacancies because the shared board's postings are permanently stuck to sibling clinic 16202 from an earlier crawl (posting_observations' upsert never re-runs clinic attribution) -- a distinct, previously-undocumented mechanism, filed as TASK-129 with full live evidence rather than fixed as a drive-by. 16201's 4 remaining marketing rows are purged (script ready, write blocked by the auto-mode classifier, handed to Ivan). AC#2/#3 checked with mutation-tested evidence; AC#4 left unchecked, honestly -- the clinics still show 0 real vacancies pending TASK-129. 108 targeted tests passed.
<!-- SECTION:FINAL_SUMMARY:END -->
