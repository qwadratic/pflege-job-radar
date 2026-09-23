---
id: TASK-82
title: >-
  _txt() crashes on a numeric JSON-LD field and aborts the whole board walk:
  both Munich Klinik sites hold zero real vacancies
status: To Do
assignee: []
created_date: '2026-09-21 04:25'
updated_date: '2026-09-22 08:52'
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
- [ ] #2 Every sibling field reader in parse_job_page that assumes a string is audited for the same numeric/list/dict-shaped input, not just the one that crashed
- [ ] #3 A crash inside one posting page cannot silently abort the whole board walk with a success result: the failure is recorded (crawl_issue) and the walk's outcome reports what it lost
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
<!-- SECTION:NOTES:END -->
