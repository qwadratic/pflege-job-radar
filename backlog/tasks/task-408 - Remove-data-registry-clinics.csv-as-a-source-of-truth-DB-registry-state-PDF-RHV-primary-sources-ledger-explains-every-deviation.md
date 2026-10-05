---
id: TASK-408
title: >-
  Remove data/registry/clinics.csv as a source of truth: DB = registry state,
  PDF/RHV = primary sources, ledger explains every deviation
status: In Progress
assignee: []
created_date: '2026-09-29 21:38'
updated_date: '2026-10-05 13:36'
labels:
  - db-quality
  - infra
dependencies: []
ordinal: 173000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-29: 'меня смущает, что код использует CSV в качестве источника. можем мы убрать csv? есть пдф, есть база.' Proposal given, awaiting his go.

What the CSV is today: the output of the registry build scripts (data/sync_krankenhausplan_2026.py merges 2025-PDF names/towns -- the 2026 PDF broke the name/town split -- with 2026-PDF numbers/enums; data/sync_rhv_reha.py adds Reha rows; data/sync_diakoneo_social.py adds DK rows) plus hand edits without reasons; it is pushed to the DB.
Readers found (grep 2026-09-29): app/data.py plan_rows -> public /plan API; app/autopilot/seed.py fallback; pflege_jobs/mechanics.py; web/geo_shapes.py; pflege_jobs/registry_lint.lint_csv; tools/reverify_and_clean.py; tools/task95_replay.py; cli link-clinics --csv (default); legacy data/run_*.py scripts. Production matching already reads the DB since TASK-80 because the CSV had drifted (117/399 clinics blank careers_url in CSV).
Hazards: pflege_jobs/orchestrate.py 'link' stage runs 'cli link-clinics', which pushes the CSV into the DB and would revert DB fixes; CSV and DB already disagree on untouched rows (e.g. 16291 careers_url/ats_type blank in CSV, set in DB; 57707/66104 beds 0 in CSV, NULL in DB). Related: TASK-98 (edge upsert cannot clear ats_type/careers_url), TASK-133 (CSV town validation, superseded if the CSV goes).

Target design: DB is the only current registry state; the PDFs/RHV/Diakoneo list are primary sources; data/ledger.jsonl (TASK-174) explains every deviation of the DB from a primary source; the registry build parses the sources, applies ledger changes and upserts the DB; a PDF-vs-DB report lists every difference as explained (ledger line) or UNEXPLAINED.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every runtime reader of clinics.csv reads the DB instead (list above re-checked by grep); cli link-clinics no longer defaults to the CSV
- [ ] #2 Registry build script: sources (PDF 2026 + 2025 names, RHV, Diakoneo) -> parse -> ledger changes -> DB upsert, reproducible, no CSV output
- [ ] #3 PDF-vs-DB report: every differing field is either explained by a ledger line (id, field, new == DB value) or listed as unexplained; run on the current DB and the unexplained list triaged
- [ ] #4 Existing CSV hand edits backfilled into the ledger (git log/blame of clinics.csv gives when + commit message; reason from the commit or backlog task), so the first report starts near zero unexplained
- [ ] #5 data/registry/clinics.csv deleted (or kept only as a generated, read-only export if Ivan wants one); docs updated
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-29 23:00 UTC: Ivan approved removing the CSV ('насчет того, чтобы удалить CSV, я полностью согласен'). Since then the ledger moved into the DB (TASK-180): read 'data/ledger.jsonl' in this task's text as pflege_jobs.corrections (reason codes in pflege_jobs.correction_reasons; tools/ledger.py). AC#3 here = TASK-180 AC#4 (same report). Delegated to a worktree agent (no DB writes; the corrections backfill from git history comes back as a JSON for Ivan's go-ahead).

2026-09-29 23:56 UTC, worktree agent (session 663542db), worktree .claude/worktrees/nocsv. Result: /tmp/nocsv.patch (63 files; unstaged diff incl. new files; `git apply --check` against the main checkout passes). Nothing committed, no DB writes, status and ACs untouched.

AC#1 (readers). grep for clinics.csv / CLINICS_CSV / registry_csv_rows / lint_csv / REGISTRY_CSV / sync_krankenhausplan_2026 now finds only history comments/docstrings and data/backfill_registry_corrections.py (reads the CSV's git history, not the file).
- app/data.py plan_rows (public /api/plan): built from the snapshot's clinics(); registry_csv_rows and app/config.py CLINICS_CSV removed. No second cache.
- app/autopilot/seed.py load_registry_source: snapshot only, the CSV fallback is gone; an unavailable snapshot raises, an empty one raises "registry snapshot has no clinics".
- pflege_jobs/mechanics.py clinic_link / bavaria "try it": D.clinics() / D.towns(); module-level CSV caches removed.
- pflege_jobs/cli.py link-clinics: --csv removed; matches against _live_clinics() and pushes clinic_links only. It used to push every CSV row into clinics first (reverting DB fixes) -- orchestrate's 'link' stage runs it, so that hazard is gone. cmd_inbox: --clinics removed, always the live table.
- pflege_jobs/registry_lint.py: lint_csv removed; __main__ lints the live table. Live run finds 7 job-detail careers_urls (16268, 47102, 47601, 67201, 67601, 77902, 77903), not fixed here.
- web/geo_shapes.py, tools/reverify_and_clean.py towns(), tools/task95_replay.py, data/repair_split_merged.py: read the clinics table. geo_shapes output vs the CSV version: 2251 vs 2250 town keys, 29 vs 28 pinned.
- Legacy data/run_ats.py, run_bite.py, run_browser_crawl.py, run_crawl.py, run_feeds.py, run_pi_all.py, run_softgarden.py, load_all_crawls.py: towns/clinics via app.config.rest_get_all (a trivial switch; all compile; the reads smoke-tested live: 288 towns, 651 clinics). Not deleted.
- data/purge_retired_sources.py: step g (CSV status -> DB) removed.

AC#2 (build). New tools/registry_build.py, read-only: parses the Krankenhausplan 2026 PDF (401 KeZ), RHV 2024 XLSX (229 RH) and the Diakoneo list (13 DK); reads clinics + corrections in one read-only transaction (psycopg2 set_session(readonly=True)); --report writes the deviation report, --proposals an apply_clinic_corrections.py file. Codes: parse_error for "DB takes the source value" and for plan inserts; not_in_source for RHV/DK inserts and for plan sites that left the plan (status nicht_mehr_im_plan, source += " | nicht in <plan>", never deleted). No CSV output, no push. data/sync_krankenhausplan_2026.py deleted (superseded; it wrote the CSV).
Parser fix, pflege_jobs/sources/krankenhausplan.py: the 2026 PDF prints the Landkreis once per group ('-' or blank below); parse() now carries it forward inside the KeZ group and raises when a group opens blank (3 new tests).
2025 PDF: not needed by the build. Evidence (live, 2026-09-29): the 2026 parse disagrees with the DB on 80 names, 32 towns, 95 operators; on 68 / 28 / 77 of them the DB equals the 2025 parse -- the values the first build (git 391d82f) kept from 2025 because the 51. Fortschreibung dropped the 'Träger' line and the 2026 name/town split is positional. The DB keeps those values; the backfill explains each as parse_error only where the 2026 cell itself still states it word for word. After the backfill is inserted no code path reads the 2025 PDF (only the one-off backfill script, as evidence).

AC#3 (report), live run 2026-09-29T23:39Z, DB 651 clinics, 56 (clinic, field) correction keys -> /tmp/nocsv_deviations.json.
Krankenhausplan 2026: 0 explained / 290 unexplained -- name 80, operator 95, town 32, landkreis 36, regierungsbezirk 11, versorgungsstufe 9, traegerart 9, day_places 9, fachrichtungen 9. RHV 2024: 0 deviations. Diakoneo: 0 deviations. Plan rows missing from the DB: 0. DB plan rows missing from the PDF: 8 (26101, 26105, 27773, 37102, 47902, 56406, 67402, 67802), all status nicht_mehr_im_plan, i.e. explained. DB rows in no source: 0. K.validate on the 2026 parse: town_missing 5, parse_quality partial 22, error_rate 0.067.
Triage: 9 Schwaben rows (76111, 76114, 76203, 76304, 76403, 77406, 77605, 77707, 77907) hold NULL operator / landkreis / regierungsbezirk / versorgungsstufe / traegerart / day_places / fachrichtungen (fill). 16290 regierungsbezirk 'Oberfranken' and 16370 'Schwaben' are wrong in the DB (both Oberbayern). landkreis: 2025-parse garbles such as 'Landkreis Berchtesgadene r Land' (repair). Real 2026 changes: 17605 VAMED -> VITREA; 26103/27401/27402 now LA-Regio Kliniken; 56402/56403 Martha-Maria St. Theresien; 56404 Klinik Hallerwiese -Cnopfsche Kinderklinik; KIRINUS 56413/16266; operators 16212, 17501, 17606, 66103, 66202. 2026 parse errors that must NOT be pushed: towns 17308 'Rottmannshöhe,', 27701 'KU Rottal-Inn-Kliniken', 46110 'Oberfranken (GeBO)', 57401 'Nürnberger Land', 67401 'KU Haßberg-Kliniken, AöR', 77301 'Dillingen-Wertingen', and the '-Nürnberg KU Klinikum' name tails (46101, 46103, 46110, 56401, 56410, 67401).

AC#4 (backfill). New data/backfill_registry_corrections.py (one-off, writes JSON only) -> /tmp/nocsv_backfill_corrections.json: 141 corrections rows (tools/ledger.py shape, all parse_error). 123 come from the first build (391d82f, task TASK-175: name 47, operator 58, town 18); 18 from hand edits (TASK-131: 15, TASK-57: 2, TASK-86: 1). Each row: at = commit date, old = the 2026 parse (first build) or the previous CSV value (hand edit), new = the DB value, evidence = the commit, the 2026 PDF cell text, the 2026 (and 2025) parse readings. A row is written only when the 2026 cell states the DB value word for word: whole lines, name at the head, operator at the tail, a line-end word of >= 4 chars may be clipped at the column edge. Unexplained remainder: 149 = fill 75 (DB NULL) + repair 22 (DB = source up to spaces/hyphens/case) + review 52 (listed with reasons in the JSON). Simulated proposals once the backfill is in: /tmp/nocsv_proposals_after_backfill_safe.json (fill+repair: 43 clinics, 97 values) and /tmp/nocsv_proposals_after_backfill_review.json (42 clinics, 52 values; decide by hand, several are parse errors). Raw proposals without the backfill: /tmp/nocsv_proposals.json (136 clinics, 290 values) -- do not push that one.

AC#5. data/registry/clinics.csv deleted in the worktree. Docs: README.md, docs/api.md, docs/errors.md, docs/feature-matrix.md, docs/overview.md, data/registry/README.md (rewritten: registry = DB table, sources, build/report, why the 2026 names are parse_error corrections), skill/references + web/skill + web/pro + web/deck via their templates (built files checked identical to web/build.py's fill()).

Tests (worktree, -m "not network", every write endpoint dummied): final full run 1619 passed, 18 skipped, 0 failed (6m52s). New: tests/test_registry_build.py (4), tests/test_backfill_registry_corrections.py (6), 3 in test_krankenhausplan.py; 15 existing test files moved from CSV fixtures to snapshot / _live_clinics stubs. The first full run had 1 real failure: tests/test_web_deck.py::test_printing_gives_one_page_per_slide ("17 slides printed as 18 A4 pages") because my longer deck cell 'Postgres (pflege_jobs.clinics)' wrapped; the baseline deck passes, the cell is now 'Postgres' like the other rows, 16/16 deck tests pass.
Mutation testing, final tree: 32 mutations, 32 caught (carry-forward 3; registry_build explanation/compare/proposals 8; plan_rows 1; link-clinics 2; seed 2; mechanics try-it 2; reverify towns 1; cmd_inbox 1; backfill states/_words/explain/triage 12). The first round had 2 survivors (parse() not calling the carry; clipped words accepted mid-line); tests were added for both. Every mutated file was restored from a /tmp copy and byte-compared; the tree's diff hash after the run equals the patch's.

Needs Ivan's go-ahead (DB writes, none done):
0. Order: apply the patch and restart pflege-web first -- the running process still holds the CSV reader (app/data.py registry_csv_rows for /api/plan, mechanics try-it), which fails once the file is gone; and until the patch is in, a manual `cli link-clinics` or `orchestrate --stages link` from the main checkout still pushes the CSV back into clinics and would revert steps 1-2 (no timer or cron runs orchestrate today).
1. Insert the 141 backfill rows into pflege_jobs.corrections (tools/ledger.py L.record).
2. Push the fill+repair proposals with tools/apply_clinic_corrections.py (--dry-run first).
3. Decide the 52 review items by hand before any push.
Open, not touched: data/sync_rhv_reha.py --push and data/sync_diakoneo_social.py --push still push their CSVs straight into clinics, bypassing corrections; data/registry/reha_bavaria.csv still exists; the 7 registry_lint findings; pflege_jobs/registry.py merge_discovered is now called by no production code (only an API hint in app/main.py and its tests); there is no reason code for "the DB takes a newer source value" (the build uses parse_error) -- worth a code in correction_reasons.

2026-09-30 00:00-00:10 UTC (orchestrating session): /tmp/nocsv.patch (sha256 2854f665...16d32, 63 files, +854/-1184) applied to main with git apply after --check; data/registry/clinics.csv and data/sync_krankenhausplan_2026.py are gone from main (pre-apply copy of the CSV: /home/exedev/.claude/jobs/663542db/tmp/clinics_csv_before_task175.csv). The old pflege-web process answered /api/plan 500 (FileNotFoundError on the CSV) for ~2 min until restart; restarted 00:0x UTC, /api/plan 200 with total 651 = the clinics table, / 200. Full suite on main after apply: 1619 passed, 18 skipped, 0 failed (7:01). Not done (need Ivan): insert the 141-row backfill, push the safe fill/repair file (43 clinics, 97 values), per-item decisions on the 52 review values.

2026-10-05: renumbered from TASK-175 by backlog doctor --fix (two tasks had the ID TASK-175). A mention of TASK-175 in a task text written before this date may mean this task, not the one that kept TASK-175.
<!-- SECTION:NOTES:END -->
