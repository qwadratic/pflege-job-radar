---
id: TASK-95
title: >-
  Move the inbox to SQLite and stop filtering inside the crawler: keep every row
  raw, load only clean rows into Postgres
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 08:38'
updated_date: '2026-09-21 16:49'
labels: []
dependencies: []
ordinal: 95000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's design decision, 2026-09-21, superseding the direction TASK-92 was originally briefed with.

THE PROBLEM. The Postgres inbox carries a server-side write cap. Measured precisely (evidence in TASK-92 notes): 2000 rows per client_id over a ROLLING 24h window, counting landed rows -- not a calendar-day counter, which is why run 108 was refused on its FIRST chunk while 09-20's 2000 rows were still inside the window. Intake has therefore failed on every full scheduled run since 2026-09-19; run 108 crawled 12,251 rows and stored none.

WHY THE OBVIOUS FIXES ARE WRONG. Raising the cap needs SQL access this repo does not have, and even at an infinite cap the run would still write thousands of rows a night into a queue table whose only purpose is to be read back and acked one step later. Enqueuing only dedupe-proven-new rows cannot help either: run 108's 676 rows survived the dedupe and the very first INSERT was still refused, because the rolling window was already full of yesterday's rows.

THE DECISION. Two changes, together:

1. STOP FILTERING INSIDE THE CRAWLER. app/crawl.py's _post_inbox currently runs classify_role BEFORE the insert and drops everything that is not an experienced nursing role -- 5,976 of 12,251 rows on run 108. That filter was added to survive the cap. It goes. The crawler must deliver every vacancy it finds, because we may need those rows later and re-crawling to recover them is expensive and lossy. This is the same principle as TASK-11 (raw-first: never delete, label instead of filter) and TASK-14 (Ivan, 2026-09-09: he is against our own limits on content).

2. THE INBOX MOVES TO SQLITE. The queue stops living in Postgres and lives locally instead, where there is no per-client write cap and no reason to ration rows. Processing the inbox then becomes the place where filtering, matching and conversion happen, and ONLY the resulting clean rows are loaded into Postgres -- a volume that is small and predictable by construction, always inside any limit.

Net effect: Postgres receives finished, matched, filtered postings. SQLite holds the complete raw record of everything every board ever served, available for reprocessing without re-crawling.

THINGS THE IMPLEMENTATION MUST RESOLVE, not assume:
- The Postgres inbox has OTHER producers that cannot be moved: the browser collector, POST /api/ingest and the Firecrawl webhook all hold only the anon key, which is the entire reason sql/010_inbox.sql exists. They are tens of rows a day, far under the cap. The processing step should drain BOTH queues; the Postgres one stays for them.
- Retention and reprocessing: raw rows are kept, marked processed rather than deleted, and it must be possible to re-run processing over historical raw rows after a classifier or matcher change. That is the main payoff of the whole design.
- data/app.sqlite already exists and holds crawl_runs/run_log. Decide deliberately whether the inbox belongs in that file or its own, and say why.
- Volume check: measure what actually reaches Postgres per night under the new shape, and confirm it is comfortably inside the 2000/24h window.
- The 57014 statement-timeout failure mode fired on the inbox INSERT itself and disappears with the table; confirm rather than assume.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 app/crawl.py writes every crawled row to the SQLite inbox unfiltered -- classify_role no longer gates what is stored, and run 108's 5,976 dropped rows would now be retained
- [x] #2 Inbox processing reads the SQLite queue, applies filtering/matching/conversion there, and writes only clean matched rows to Postgres via EdgeSink; raw rows are marked processed, never deleted
- [x] #3 The Postgres inbox remains available for the anon-key producers (browser collector, /api/ingest, Firecrawl webhook) and the processing step drains both queues
- [x] #4 Historical raw rows can be reprocessed without re-crawling, demonstrated by re-running processing over an existing run's raw rows and showing the result
- [x] #5 Measured: rows written to Postgres per full scheduled run under the new shape, shown to be comfortably inside the 2000-per-rolling-24h cap, with the 12,251-row run 108 as the worked example
- [ ] #6 A full adapter run completes intake end to end with n_new reflecting real new postings, and the 57014 timeout mode is confirmed gone rather than assumed gone
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New pflege_jobs/inbox_db.py: local SQLite queue (data/inbox.sqlite, own file -- pflege_jobs must not import app/, and a bulk fast-growing queue should not contend with app.sqlite's session/settings writes). Same row shape as pflege_jobs.inbox so one processor handles both.
2. app/crawl.py: adapter rows go to inbox_db.enqueue() unfiltered (classify_role filter deleted from _post_inbox, which stays for the anon-key producers); refs for link-cross/verify come from the rows the drain actually loaded; _unseen_source_urls also consults the local queue so the Firecrawl spend gate keeps refusing 'adapter covers it'.
3. pflege_jobs/cli.py: extract the row processing out of _drain_once; cmd_inbox drains the SQLite queue first, then the Postgres one. Local rows are acked in place (processed_at/process_note), never deleted.
4. Reprocessing: inbox_db.reset() clears processed_at for a run so the next drain re-runs classifier+matcher over historical raw rows.
5. tools/task95_replay.py: replay crawl_output/run_108.jsonl through the new path with no writes; report rows enqueued vs rows that would reach Postgres.
6. Tests, mutation-tested; targeted then full offline suite. No production writes this round.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
IMPLEMENTED 2026-09-21. No production data was written this round; everything below is measured offline or by replaying a real run.

Files changed
  pflege_jobs/inbox_db.py     NEW. The local raw queue: enqueue/pending/ack/reset/loaded_refs/known_urls/counts over data/inbox.sqlite (WAL). Same row shape as pflege_jobs.inbox so one processor handles both.
  pflege_jobs/cli.py          _drain_once split into _process_rows (the shared processing: NON_PROD_HOST gate, jobposting_to_obs, role/Bavaria filter, Matcher, EdgeSink, clinic_links) + two thin drains, one per queue. cmd_inbox pages the SQLite queue first, then the Postgres one; --reprocess-run/--reprocess-all replay stored raw rows; --inbox-db points at another queue file. Per-page log line now names the queue.
  app/crawl.py                execute() calls _enqueue_local() instead of _post_inbox(); refs for link-cross/verify come from inbox_db.loaded_refs(run_id), i.e. the rows the drain actually turned into observations. _post_inbox keeps the anon-key producers and no longer runs classify_role before the insert. _unseen_source_urls also consults the local queue (otherwise the Firecrawl 'adapter covers it' refusal could never fire again). Intake failure is recorded as a crawl_issue (TASK-92 AC#5's first half).
  pflege_jobs/sources/inbox.py  observation payload provenance carries the queue, since inbox_id is only unique within its own queue.
  app/data.py                 inbox_summary reports the local queue too -- otherwise GET /api/inbox shows an empty queue while a night's crawl waits on disk.
  crawlers/load_crawl_output.py  loads a directory of crawl output into the local queue instead of POSTing thousands of rows at the capped table.
  sql/010_inbox.sql, docs/overview.md, docs/scraping.md  scope of the Postgres table and the measured 2000-per-rolling-24h rule written down.
  tools/task95_replay.py      the no-write proof (below). tests/test_inbox_sqlite_queue.py 6 new tests; tests/test_crawl_board_retry.py + tests/test_cli_inbox_drain.py updated.

Which SQLite file, decided rather than defaulted: its own, data/inbox.sqlite, not data/app.sqlite. app/ imports pflege_jobs and never the reverse, and the queue is written by the CLI as well as by the app, so the module has to live in pflege_jobs; and this is bulk, fast-growing data (40.8 MB for one run's 8,743 rows) with its own retention story, next to app.sqlite's small operational state that a web process holds a lock on. Path override: PFLEGE_INBOX_DB or cli inbox --inbox-db.

Replay of the last real run, no writes (tools/task95_replay.py crawl_output/run_108.jsonl, full output /tmp/task95-replay-run108.txt):
  crawl_output/run_108.jsonl   8743 raw rows, 6797 distinct source_url
    (run 108's n_rows=12251 = these 8743 queue rows + 3508 seeded-adapter observations that already went straight to EdgeSink)
  OLD PATH  5976 rows dropped by classify_role before the insert; 2137 distinct urls still offered to a table that takes 2000 per client_id per rolling 24h -> refused on the first chunk, 0 stored
  NEW PATH  8743 rows queued locally (40.8 MB), 0 rows written to the capped table by the crawler at all
            processing -> 1467 observations to Postgres (16.8% of the raw rows), 1221 of them matched to a clinic
            skips: 5423 nicht_pflege, 1300 outside Bavaria, 350 ausbildung, 143 pflegehelfer, 60 werkstudent_praktikum -- every one of them still on disk with its process_note
  REPROCESS 8743 rows unmarked and run again -> 8743 read, 1467 observations, same result, no re-crawl

Volume against the cap (AC#5): the crawler now writes ZERO rows to pflege_jobs.inbox, so the 2000/rolling-24h rule is not approached from this path at all. What it does write is 1467 observations through EdgeSink (pflege-ingest), which is the same path that already carried 768 observations plus 2901 resolve-refreshes on run 96 without a cap error. The remaining Postgres inbox producers are the anon-key ones: the live queue read today holds exactly 1 unprocessed row.

Both Postgres failure modes are removed from the crawler path by construction, not by tuning: 400 P0001 and 500 57014 both fired on A.rest_post('inbox', ...) (every landed row count is an exact multiple of that call's 200-row chunk), and execute() no longer makes that call. They remain reachable for /api/ingest and the Firecrawl webhook, at tens of rows a day.

Mutation tests, each confirmed red then restored green:
  re-add a classify_role filter before enqueue        -> test_the_crawler_queues_every_row_it_found... FAILS
  ack deletes rows instead of marking them processed  -> test_processing_writes_only_clean_rows... and ...reprocessed... FAIL
  reset() becomes a no-op                             -> test_historical_raw_rows_can_be_reprocessed... FAILS
  cmd_inbox drains only the Postgres queue            -> test_cmd_inbox_drains_the_postgres_queue_too FAILS
  _unseen_source_urls ignores the local queue         -> test_the_spend_gate_sees_urls_the_adapter_already_queued_locally FAILS
  drain stops filtering by role_class                 -> two tests FAIL
  drop the intake crawl_issue                         -> test_a_failed_intake_is_recorded_as_a_crawl_issue FAILS
Targeted: 6 passed. Full offline suite: 1266 passed, 1 skipped, 0 failed (1260 passed before; 6 new tests, one existing test inverted because the behaviour it pinned is what this task removes).

Storage note, not a cap and not acted on: the raw rows are now stored twice, once as crawl_output/run_*.jsonl (386 MB so far) and once in data/inbox.sqlite (~40 MB per full run). Worth a decision on whether the JSONL archive is still wanted; nothing was deleted.

Per-criterion evidence.
AC#1 CHECKED. tests/test_inbox_sqlite_queue.py::test_the_crawler_queues_every_row_it_found_including_the_ones_intake_will_drop: execute() over a board serving 5 mixed titles stores all 5 (doctor and kitchen included); mutation-tested by re-adding the filter. Run 108's 5,976 dropped rows are retained under this shape -- the replay stores 8,743 where the old path stored at most 2,767.
AC#2 CHECKED. tests/...::test_processing_writes_only_clean_rows_to_postgres_and_keeps_the_raw_rows -- 6 queued rows, 2 observations written, 6 rows still in the table each carrying a process_note (loaded / not an experienced nursing role / non-production host). Confirmed at scale by the run 108 replay: 8,743 stored, 1,467 written.
AC#3 CHECKED at the code level, with one honest gap. cmd_inbox pages the local queue and then the Postgres queue (tests/...::test_cmd_inbox_drains_the_postgres_queue_too, mutation-tested by removing the Postgres loop), _post_inbox still serves POST /api/ingest and the Firecrawl webhook (tests/test_crawl_board_retry.py::test_post_inbox_stores_every_row_it_is_given, tests/test_agent_api.py), and the Postgres drain's ack path stays covered by tests/test_cli_inbox_probe.py. NOT done live: acking real rows is a production write, which this round may not make. The live queue read-only today holds 1 unprocessed row (inbox_id 25612, collector vendor-wp_jobs-v1), which the next real drain will take.
AC#4 CHECKED. Twice: a unit test that changes EXCLUDED_ROLE_CLASSES between two drains of the same stored rows (2 observations -> 3, no re-crawl), and the run 108 replay, which unmarks all 8,743 stored rows and reprocesses them to the same 1,467.
AC#5 CHECKED. Worked example above: 0 rows to the capped table from the crawler, 1,467 observations through EdgeSink, against a rule of 2000 inbox rows per client_id per rolling 24h that the old path hit on its first chunk.
AC#6 NOT CHECKED. It needs a full adapter run that actually writes to production, which this round is not allowed to do. The 57014 mode is removed by construction (the statement that timed out was the inbox INSERT the adapter path no longer issues), not re-observed; the next scheduled mode=adapter run at 03:00 UTC is the evidence for both halves.

ИСПРАВЛЕНИЕ 2026-09-21: AC#1 снят, он был отмечен ошибочно. Независимая проверка (Opus) доказала, что сырой корпус НЕ полный.

Что не так: seeded-адаптеры (softgarden, bite, umantis, pi_asp) вообще не проходят через SQLite-очередь. _fetch_board возвращает их как observations, а не inbox_rows (app/crawl.py:737-758), _write_jsonl архивирует только inbox_rows (app/crawl.py:880), и _load_observations по-прежнему фильтрует их на этапе краула по EXCLUDED_ROLE_CLASSES / in_bavaria is False / NON_PROD_HOST -- без единой строки лога и без сохранения куда-либо.

Измерено на run 96 (последний полностью успешный): n_rows 12 630 минус 9 121 строк в crawl_output/run_96.jsonl = 3 509 seeded-строк накраулено; до EdgeSink дошло 871 ("ingested observations: {'observations': 768, 'dropped_non_pflege': 103}"). 2 638 строк -- 21% всего прогона -- выброшены на этапе краула и невосстановимы без перекрола.

Это прямо опровергает формулировку коммита 571f2c8 "Every row the crawler finds is now kept". Поведение досталось по наследству, не внесено этим коммитом, но заявление было сделано и оказалось неверным. Чинится малой правкой: гнать seeded observations через IB.enqueue тоже (или хотя бы архивировать в _write_jsonl) и дать фильтрацию drain-у, как всем остальным строкам.

ЧТО ПОДТВЕРДИЛОСЬ (проверено независимо, не на веру):
- Вопрос headroom СНЯТ: observations не подпадают под лимит inbox. Правило называет таблицу inbox и client_id; у pflege_jobs.posting_observations нет колонки client_id (проверено live, 60 колонок); пишет их edge-функция pflege-ingest через собственный SUPABASE_DB_URL, а не anon-клиент PostgREST, который несёт client_id. Решающее: run 107 в 2026-09-21T00:02, через ~14.5 ч после срабатывания лимита 09-20 09:37 и внутри скользящего окна, записал 105 + 221 observations через EdgeSink и отработал resolve (refreshed 2 986) без ошибки.
- Ночной объём по новой схеме ~1 798 (очередь) + ~870 (seeded) ~= 2 670, что превысило бы 2 000, ЕСЛИ БЫ правило действовало на этом пути. Оно не действует -- поэтому фикс действительно снимает отказ интейка.
- Переобработка работает: reset + повторный drain дал те же 1 798 observations.
- Postgres-inbox цел для anon-производителей: _post_inbox на месте, вызывается из app/main.py:693 (POST /api/ingest) и app/firecrawl_hooks.py:75 (Firecrawl webhook), cmd_inbox дренит сначала локальную очередь, потом Postgres.

ЕЩЁ ТРИ ДЕФЕКТА, найденные той же проверкой:
1. pflege_jobs/cli.py:454 -- sink.write(obs, resolve=True) вызывается на каждой странице по 1 000 строк. Очередь раньше была десятки строк (1-2 страницы), теперь ~9 000: replay на run_104 дал 10 страниц, то есть resolve_postings() теперь запускается 10 раз за ночь вместо одного. Это исторически самая тяжёлая серверная операция в логе (refreshed 2 901-3 915 постингов за вызов). Нужно resolve=False на страницах и один resolve после последней.
2. tools/task95_replay.py:78,111 -- run_id=108 захардкожен и для IB.enqueue, и для IB.reset независимо от переданного jsonl. На /tmp-базе безвредно, но с --db data/inbox.sqlite пометит реальные строки чужим run_id, а reset(run_id=108) снимет отметку с реальных строк run 108.
3. Неограниченный рост локально: ~40 МБ/ночь в data/inbox.sqlite поверх ~45 МБ/ночь в crawl_output/*.jsonl (уже 386 МБ) при 11 ГБ свободных на /dev/root -- примерно 4 месяца до заполнения диска. data/inbox.sqlite* в gitignore и теперь это единственная копия строк, которые никто не перекраулит. Нужно явное решение по retention -- НЕ выдуманный кап, а решение владельца.

РЕШЕНИЕ ВЛАДЕЛЬЦА по retention (Ivan, 2026-09-21): ротация по возрасту, окно ОДИН МЕСЯЦ. Строки старше 30 дней из data/inbox.sqlite удаляются. Это снимает вопрос неограниченного роста: при ~40 МБ/ночь установившийся размер около 1.2 ГБ при 11 ГБ свободных.

Реализовать ротацию как обычный шаг обслуживания, а не как «кап» внутри пути записи: краул по-прежнему пишет ВСЁ без фильтра, отдельная операция удаляет то, что старше 30 дней. Возраст берётся по времени постановки строки в очередь, не по времени обработки, иначе необработанная строка будет жить вечно.

ВТОРОЙ ВОПРОС -- класть ли data/inbox.sqlite в git -- НЕ проходит по технике, и это не предпочтение, а жёсткий предел:
- при ~40 МБ/ночь файл превысит хардлимит GitHub в 100 МБ на файл примерно через 2.5 дня, после чего push начнёт отклоняться;
- при месячном окне установившийся размер ~1.2 ГБ, что уже сильно за мягким лимитом репозитория в 1 ГБ;
- SQLite -- бинарник, git не дельтит его осмысленно: каждый ночной коммит кладёт в историю полную новую копию, то есть десятки ГБ за месяц, и удалить их потом можно только переписав историю.

Поэтому: data/inbox.sqlite остаётся в .gitignore. В git идут схема, код ротации и тесты. Если нужна копия вне этой VM, это отдельный механизм бэкапа, а не git.

ROUND 2 (2026-09-21, this session): fixed the 4 concrete defects the independent review found, plus investigated run 109. Files owned/touched: app/crawl.py, pflege_jobs/cli.py, pflege_jobs/inbox_db.py, tools/task95_replay.py, and tests/test_inbox_sqlite_queue.py, tests/test_cli_inbox_probe.py, tests/test_cli_inbox_drain.py, tests/test_crawl_board_retry.py, tests/test_task95_replay.py (new). No production writes made or attempted.

DEFECT 1 (headline, AC#1) FIXED. Seeded-adapter observations (softgarden/bite/umantis/pi_asp) now go through the same local queue as every other row instead of straight to EdgeSink. app/crawl.py: new _obs_row(o) wraps a finished observation as a queue row (kind='observation'); execute() merges inbox_rows + wrapped observations into one queued_rows list before _write_jsonl (archive) and _enqueue_local (queue) -- both now see every row a board served, seeded or vendor. The old direct-to-EdgeSink path (_load_observations, which duplicated ~30 lines of filtering/matching logic app/crawl.py already had no business owning) is deleted; pflege_jobs/cli.py._process_rows gained a kind=='observation' branch that applies the identical three gates (NON_PROD_HOST, EXCLUDED_ROLE_CLASSES, in_bavaria is False) plus Matcher, mirroring the retired function almost line-for-line, so a seeded row now gets the same on-disk retention, process_note trail, and reset()-driven reprocessing as a jobposting row.
  Evidence: tests/test_inbox_sqlite_queue.py::test_seeded_adapter_observations_are_queued_too_not_sent_straight_to_edgesink (execute() over a seeded board with a doctor row, a kitchen row and an Ausbildung row -- all 5 land in the queue, not just the 2 nursing ones) and ::test_seeded_adapter_observations_are_filtered_by_the_drain_not_at_crawl_time (the drain, not the crawl, does the filtering -- 5 queued, 2 written, all 5 still on disk with a process_note). tests/test_cli_inbox_probe.py::test_observation_kind_rows_pin_the_non_prod_host_and_in_bavaria_false_drop_gates replaces the deleted CR._load_observations unit test with the same 4-row gate matrix (Bavarian / non-Bavarian / unknown-location / staging-host), now exercised through the real drain. All mutation-tested (branch removed / gate deleted -> test fails; restored -> passes).

DEFECT 2 FIXED. resolve_postings() now fires once per whole `cli inbox` run, not once per 1,000-row page. pflege_jobs/cli.py: _process_rows/_drain_once/_drain_local_once take resolve= and stats= (a shared dict any page sets stats['wrote']=True on if it actually wrote observations); cmd_inbox calls every page with resolve=False, then calls EdgeSink()._post({'resolve': True}) exactly once at the end, and only if stats['wrote'] is True (a run that wrote nothing, e.g. an empty queue, makes no resolve call at all -- the old per-page code had the same "only if obs" property, now preserved at the whole-run level).
  Evidence: tests/test_inbox_sqlite_queue.py::test_cmd_inbox_resolves_postings_once_per_run_not_once_per_page (1,500 local rows force 2 pages; asserts EdgeSink.write() got resolve=False on both pages, and exactly one {'resolve': True} POST happened after) and ::test_cmd_inbox_does_not_resolve_when_nothing_was_written. Both mutation-tested (reverting resolve=False->True, or removing the final call, or removing the stats['wrote'] gate -> each fails independently).

DEFECT 3 FIXED. tools/task95_replay.py no longer hardcodes run_id=108. New run_id_for(jsonl_path, explicit=None): --run-id if given, else parsed from the run_<N>.jsonl filename via regex; raises SystemExit (no silent fallback) if neither is available. Both IB.enqueue and IB.reset calls now use the derived run_id.
  Evidence: tests/test_task95_replay.py (3 tests: parses from filename, --run-id overrides, unparseable filename with no --run-id raises), mutation-tested. Also re-ran the tool live against crawl_output/run_108.jsonl end to end (no writes, /tmp default db) to confirm no regression: 8,743 rows queued -> 1,467 observations (1,221 matched), reprocess -> same 1,467 -- identical to the original notes' numbers, now produced by a script that no longer has 108 baked in.

DEFECT 4 (retention). This round's brief said not to invent a cap and to measure + present options instead. By the time I reached it, Ivan's actual decision had already landed in this same task's notes (Russian-language note above, timestamped 2026-09-21 12:44, written by a concurrent session): 30-day age-based rotation on data/inbox.sqlite, as an ordinary maintenance step separate from the write path. I implemented that recorded decision rather than re-inventing my own: pflege_jobs/inbox_db.py gained purge_older_than(days, path=None, now_iso=None) -- deletes rows whose received_at (enqueue time, NOT processed_at, per the decision -- an unprocessed row must age out too) is older than `days`; raises ValueError on days<=0 rather than silently defaulting. pflege_jobs/cli.py gained a new `purge-inbox [--days 30]` subcommand (cmd_purge_inbox) -- a separate command an operator/cron runs, never called from cmd_inbox's drain, so it can never throttle what the crawler writes. 3 new tests in tests/test_inbox_sqlite_queue.py, mutation-tested (delete-count return value, received_at-not-processed_at semantics, the days<=0 guard).
  CORRECTION to the recorded growth math: the note's ~40 MB/night -> ~1.2 GB/30-days estimate predates my Defect-1 fix. With seeded-adapter observations now also queued, a full run's real growth to data/inbox.sqlite is ~40.8 MB (jobposting rows, from the original run-108 replay) + ~14.7 MB (3,508 synthetic seeded-observation rows built with the real career_crawl._base()/_obs_row shape, matching run 96/108's measured seeded-row count) =~ 55 MB/run. At 30 days that is a steady ~1.6 GB (not ~1.2 GB), still comfortably inside the ~11.85 GB free measured today (df: 11,853,942,784 bytes free). The decision holds; I corrected the number it rests on in purge_older_than's docstring.
  ALSO FLAGGED, not fixed (no decision recorded, out of scope): crawl_output/*.jsonl (currently 386 MB, 424 files) now ALSO archives the seeded-observation rows (previously unarchived), adding roughly the same ~13.4 MB/run on top of its existing ~34-45 MB/run (measured directly: run_108.jsonl 34,381,648 bytes/8,743 rows, run_96.jsonl 45,021,635 bytes/9,121 rows). Nothing rotates this archive. This is the same "worth a decision" storage note the original TASK-95 notes already flagged, now larger because of my fix; still nobody's decision to make unilaterally.

RUN 109 (TypeError: unhashable type: 'list') -- INVESTIGATED, REPRODUCED BY READING THE CODE (not guessed). Root cause found: pflege_jobs/verify.py's verify_all() ran every open posting's HTTP check inside a ThreadPoolExecutor and called f.result() via as_completed() with no per-row exception handling in the http_one() closure -- one malformed JSON-LD address anywhere in verify_one() re-raised straight through as_completed().result(), aborting verify_all() entirely. That propagated out of app.crawl._run_verify() (execute()'s mode=='verify' branch has no try/except around the call) and was caught only by app/runs.py's worker-loop last-resort handler (`except Exception as e: log(f"FAILED: {type(e).__name__}: {str(e)[:400]}")`) -- which matches run 109's stored fields exactly: error "unhashable type: 'list'", log line "FAILED: TypeError: unhashable type: 'list'", right after the log line "verify: 2384 open posting(s) in scope". This is why the earlier investigation could not reproduce it against the current tree: it was ALREADY FIXED, by commit 38287cc ("Recover 401 postings from boards that read as empty, and stop a JSON-LD crash killing whole board walks"), which landed immediately before 571f2c8 (TASK-95's own commit, confirmed via git log) and is already in this tree -- its http_one() now wraps the per-row check in try/except with a comment that names run 109 explicitly as the reason it exists. No code change needed from me; pflege_jobs/verify.py is outside this round's file ownership regardless.

TESTS. Targeted (every file I touched + its tests): 103 passed (test_inbox_sqlite_queue.py, test_cli_inbox_probe.py, test_cli_inbox_drain.py, test_crawl_board_retry.py, test_task95_replay.py, test_agent_api.py). Every new/changed assertion mutation-tested: edited a /tmp copy of the source file to break the specific behavior, confirmed the test goes red, restored the real file from the /tmp copy (never via git), confirmed green again -- done individually for all 4 defects (9 mutations total, each isolated).
Full offline suite: `pytest -q -m "not network" --ignore` on the 7 Playwright-driven UI dashboard files (tests/test_web_{clawl,deck,dropdowns,hero,login,map,responsive}.py, 123 tests, none of which touch app/crawl.py|pflege_jobs/cli.py|pflege_jobs/inbox_db.py|tools/task95_replay.py) -> 1153 passed, 1 skipped, 0 failed in 175s. Those 7 files could not complete reliably in this shared VM right now (multiple concurrent agent sessions' own uvicorn/Chromium processes observed running alongside mine); test_web_clawl.py run alone in a clear window passed cleanly (12/12 in 13.33s), so this reads as environment contention, not a regression -- but I could not get a clean same-window run of all 7 to attach a number here, and did not want to under-report a real failure by force-continuing past a genuine hang. 1153+123=1276 reconciles with the pre-round 1266-passed/1-skipped baseline (1267) + the 10 net new tests I added (7 in test_inbox_sqlite_queue.py, 3 in the new test_task95_replay.py).

Nothing in this round mutated production data. data/inbox.sqlite today is schema-only (0 rows) -- no real crawl has run under this code yet.

ROUND 3 (2026-09-21, this session): fixed the 3 concrete problems an independent review found in round 2's own fixes. Files touched: pflege_jobs/cli.py, crawlers/load_crawl_output.py, tests/test_inbox_sqlite_queue.py, tests/test_cli_inbox_probe.py. No production writes made or attempted.

PROBLEM 1 (regression introduced by round 2's own defect-2 fix) FIXED. Deferring resolve_postings() to once per whole cmd_inbox run (round 2, correct) was combined with a posting-id lookup + clinic_links push that still ran per page, inside _process_rows, immediately after sink.write(obs, resolve=False) -- i.e. before the single end-of-run resolve had run at all. posting_observations.posting_id is NULL on insert (edge/pflege-ingest/index.ts's OBS_COLS has no posting_id; sql/002_task73_migration.sql is what assigns it, inside resolve) and lookup_posting_ids only returns rows that already have one, so every brand-new posting created this run got 0 clinic_links, every night, since round 2 landed -- postings.clinic_id/clinic_match_rule/clinic_match_score stayed NULL for 24h on every posting a run created (run 96: 21 created), 100% on any rebuild/reingest, and cmd_link_cross's cross-source pass (filters clinic_id=not.is.null) also skipped them that run.
  Fix: _process_rows no longer looks up posting_id or pushes clinic_links itself. It takes an optional link_candidates list and, when given one, extends it with every matched (_kez-bearing) observation instead. cmd_inbox owns one link_candidates list across the whole drain (both queues, every page), and after the single resolve call (same stats['wrote'] gate as before) does the posting_id lookup + clinic_links push exactly once, over everything accumulated. _drain_once/_drain_local_once thread link_candidates through.
  Evidence: tests/test_inbox_sqlite_queue.py::test_cmd_inbox_links_brand_new_postings_after_the_deferred_resolve -- a stub that models the server precisely (posting_observations lookup returns nothing until a {'resolve': True} POST has actually happened, same repro method the review used) -- 2 brand-new matched postings both get their clinic_links after the fix; mutation-tested by reverting _process_rows to do the lookup+push per page again (via a /tmp copy, never git): the test goes red (0 links found), restoring the fixed file from the /tmp copy brings it back green. tests/test_cli_inbox_probe.py::test_drain_once_never_fabricates_a_verify_stamp updated to match the new contract (asserts link_candidates is populated and clinic_links is NOT posted inside _drain_once itself); same mutation-test confirms it too goes red against the reverted code.

PROBLEM 2 FIXED, one-string change. crawlers/load_crawl_output.py:31 filters reloaded jsonl rows on kind in ("jobposting", "listing", "probe") -- app/crawl.py now archives seeded-adapter rows (softgarden/bite/umantis/pi_asp) into crawl_output/run_*.jsonl as kind="observation" (round 2's own fix), so this reload path silently dropped every one of them, defeating the "raw corpus is complete and recoverable" claim for exactly the rows round 2 fixed. Added "observation" to the tuple. No dedicated test: the script reads SUPABASE_URL/SUPABASE_ANON_KEY from the environment at import time, which makes it import-untestable without a larger refactor outside this fix's scope; the change itself is a literal tuple-membership one-liner, verified by inspection.

PROBLEM 3, backlog-notes correction (not a code defect). The round-2 note above claims the new pflege_jobs/cli.py kind=='observation' branch "mirrors the retired [app/crawl.py] function almost line-for-line" -- true for the three filter gates (NON_PROD_HOST, EXCLUDED_ROLE_CLASSES, in_bavaria is False) and the seed_kez fallback, but it omits an undisclosed registry-source swap the retired function did NOT have: the retired app/crawl.py._load_observations built its Matcher from D.clinics() (the LIVE pflege_jobs.clinics table); cmd_inbox builds it from --clinics, which defaults to the static data/registry/clinics.csv. This is TASK-80 (still To Do): measured live today against pflege_jobs.clinics (407 rows both sides), careers_url is blank in the CSV for 117 clinics that have one live (28 of them on seeded vendors: softgarden 16, pi_asp 7, bite 3, umantis 2), differs for 29, and name/town differs for 24. Practical impact on this task is small, not zero: seeded rows pass an explicit board=<clinic_id> pool into _match_board, keyed on clinic_id rather than careers_url, so the 117-blank and 29-differ groups can't change a seeded-row verdict here -- only the 24 name/town drifts can (R0_board_name/R0_board_town keys on those). This difference was not mentioned anywhere in the round-2 notes; recorded here so the "almost line-for-line" claim isn't read as "identical."

Targeted tests: 44 passed (test_inbox_sqlite_queue.py, test_cli_inbox_probe.py, test_cli_inbox_drain.py, test_crawl_board_retry.py, test_task95_replay.py). Full offline suite (pytest -q -m "not network", nothing else excluded -- all 7 Playwright UI files included and green this run, unlike round 2's shared-VM contention): 1277 passed, 1 skipped, 0 failed, 1197 deselected (network-marked) in 460s. 1277 = round-2's own reconciled 1276 + the 1 net new test this round (test_cli_inbox_probe.py's test was edited, not added).

Nothing in this round mutated production data. Verified: data/inbox.sqlite untouched by any test (all use tmp_path fixtures); no write-path code (EdgeSink/PostgREST PATCH/DELETE) was exercised outside test fakes.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 09:16
---
Left In Progress: AC#6 is the only open one and it needs a real scheduled run writing to production, which this round was not allowed to make. Everything else is verified offline or by replaying crawl_output/run_108.jsonl. Nothing in this round mutated production data; there is nothing pending human application except deciding whether to also keep the crawl_output/*.jsonl archive now that the same raw rows live in data/inbox.sqlite.
---

author: @claude
created: 2026-09-21 13:38
---
Round 2 done: all 4 review-found defects fixed and mutation-tested (seeded-observation queueing, resolve-once-per-run, task95_replay run_id, and Ivan's already-recorded 30-day retention decision implemented as a separate purge-inbox command). Run-109 TypeError investigated and closed: already fixed by commit 38287cc, no action needed. AC#1-5 checked. Left In Progress: AC#6 still needs a real production scheduled run, which this round may not make. Nothing here mutated production data.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The crawler's queue is local SQLite and nothing filters at the queue. app/crawl.py writes every row a board serves to pflege_jobs.inbox_db (data/inbox.sqlite) unfiltered and undeduped; pflege_jobs.cli cmd_inbox is now the single processing step, draining the local queue and then the Postgres inbox (which stays for the producers holding only the anon key), and only the finished observations go to Postgres through EdgeSink. Raw rows are marked processed, never deleted, and cli inbox --reprocess-run/--reprocess-all replays them after a classifier or matcher change. classify_role no longer runs before any insert. Proven without writing to production by replaying the last real run: crawl_output/run_108.jsonl, 8,743 raw rows -- old path dropped 5,976 at crawl time and still offered 2,137 urls to a table that takes 2,000 per client_id per rolling 24h (refused on the first chunk, 0 stored); new path stores all 8,743 locally, writes 0 rows to the capped table and 1,467 observations (16.8%, 1,221 matched to a clinic) to Postgres, and reprocessing the same stored rows returns the same 1,467 with no re-crawl. Both Postgres failure modes (400 P0001, 500 57014) fired on the inbox INSERT the adapter path no longer issues, so they are gone from this path by construction. 6 new tests, each mutation-tested red then green; full offline suite 1266 passed, 1 skipped, 0 failed. AC#6 is open: it needs a real scheduled run, which this round could not make.

ROUND 2 UPDATE (2026-09-21): fixed all 4 defects the independent review found. (1) Seeded-adapter observations (softgarden/bite/umantis/pi_asp) now go through the same local SQLite queue as vendor rows -- app/crawl.py._obs_row wraps them as kind='observation' queue rows, archived and drain-filtered by pflege_jobs/cli.py's new observation branch instead of going straight to EdgeSink unfiltered-and-unrecorded (was 21% of run 96 dropped with no trace); AC#1 re-checked with mutation-tested evidence. (2) resolve_postings() now fires once per whole `cli inbox` run instead of once per 1,000-row page (stats['wrote'] tracked across pages/both queues). (3) tools/task95_replay.py derives run_id from --run-id or the run_<N>.jsonl filename instead of hardcoding 108; fails loudly if neither is available. (4) Retention: Ivan's 30-day age-based rotation decision (recorded in this task's notes by a concurrent session while I worked) is now implemented as pflege_jobs/inbox_db.purge_older_than() + a separate `cli purge-inbox` maintenance command, never wired into the write/drain path; I corrected the growth estimate it rests on (~55 MB/run, not ~40, once seeded observations are included) -- still comfortably inside free disk at 30 days (~1.6 GB vs ~11.85 GB free). Also investigated and closed the open run-109 TypeError question: root cause found by reading the code (an unhandled per-row exception in pflege_jobs/verify.py's threaded HTTP pass could abort the whole verify_all() call), already fixed by commit 38287cc which landed just before TASK-95's own commit and is already in this tree -- no code change needed. Targeted tests: 103 passed across every file/test I touched, every defect mutation-tested red-then-green (copy to /tmp, break, confirm red, restore from /tmp, confirm green). Full offline suite (network-marked and 7 Playwright-driven UI-dashboard test files excluded -- those hang under this heavily shared VM's browser-resource contention, confirmed unrelated to my changes since test_web_clawl.py passes cleanly alone): 1153 passed, 1 skipped, 0 failed. AC#1-5 now checked; AC#6 still needs a real production scheduled run, which this round could not make. No production data was written or mutated this round.

ROUND 3: fixed the review's 3 remaining problems in round 2's own fixes. (1) clinic_links regression: the resolve-once fix (round 2) deferred resolve_postings() to end-of-run but left the posting_id lookup + clinic_links push per-page, before that resolve had ever run -- so every posting created in a run got 0 clinic_links. Now cmd_inbox accumulates matched observations across the whole drain and does the lookup+push once, after the single resolve. (2) crawlers/load_crawl_output.py's reload filter didn't include kind="observation", silently dropping the seeded-adapter rows round 2 started archiving -- one-string fix. (3) Corrected round 2's notes: the new observation-branch registry lookup uses the static data/registry/clinics.csv (via cmd_inbox's --clinics default), not the live pflege_jobs.clinics table the retired function used (TASK-80) -- "almost line-for-line" undersold this difference. New/updated tests mutation-tested red-then-green via /tmp copies (never git). Targeted: 44 passed. Full offline suite: 1277 passed, 1 skipped, 0 failed in 460s (no files excluded this run). No production data mutated.
<!-- SECTION:FINAL_SUMMARY:END -->
