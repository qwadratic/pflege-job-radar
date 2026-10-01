---
id: TASK-167
title: >-
  Bavaria biggest hospitals (5 Hochschulkliniken + 2 Maximalversorger) have
  beds=NULL: Krankenhausplan parser drops German thousands separator "1.020"
status: Done
assignee:
  - '@claude'
created_date: '2026-09-29 15:12'
updated_date: '2026-09-29 15:24'
labels:
  - db-quality
dependencies: []
priority: high
ordinal: 165000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Open vacancies per 1000 beds is the collector-health metric (app/coverage.py _beds_ratio, app/data.py jobs_per_100_beds). Sites with beds NULL drop out of it, and on 2026-09-29 that included Bavaria biggest hospitals (LMU, TUM rechts der Isar, FAU Erlangen, Würzburg, UK Augsburg, München Klinik Bogenhausen, Klinikum Nürnberg Nord), each with 40-106 open vacancies. A first brief also counted 42 acute sites with beds=0 as "missing"; those need a per-site decision from the source (day clinic vs real hospital), not a forced number. Related: TASK-143 (beds verification, still open).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Root cause of NULL beds identified from the raw Krankenhausplan 2026 PDF cells (source omission vs parser bug)
- [x] #2 If parser bug: fixed in pflege_jobs/sources/krankenhausplan.py with a test and a mutation test; full non-network suite 0 failed
- [x] #3 Every acute site (Plan-KH/HS-Klinik/Vertrags-KH/Bedarfsfeststellung) with NULL beds filled from a citable primary source with provenance appended to `source`, or listed as no-source
- [x] #4 beds=0 acute sites classified per site from the PDF (day-clinic-only / 0 Planbetten correct vs wrong), 46170 vs 46101 Bamberg explained
- [x] #5 Live clinics rows updated via tools/apply_clinic_corrections.py, backed up and read back; data/registry/clinics.csv consistent so a re-import does not wipe values
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Parser: add _int() accepting German thousands separator (\d{1,3}(.\d{3})*) for beds/day_places; test with frozen PDF cell values; mutation test.
2. Re-parse PDF, diff beds vs clinics.csv: confirm only the 7 NULL rows change.
3. Corrections JSON for the 7 -> apply_clinic_corrections --dry-run/--push; append provenance to source.
4. Update data/registry/clinics.csv for the same 7 rows (beds + source) so a re-sync matches.
5. Classify 42 beds=0 acute rows from raw PDF cells; no writes for them (0 = source value; coverage._beds_ratio already excludes beds<50).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Root cause (AC1) -- parser bug, not a source omission
Live before (2026-09-29, acute = Plan-KH/HS-Klinik/Vertrags-KH/Bedarfsfeststellung, 399 rows): beds NULL = 7, beds = 0 = 50. The brief's "49 NULL" mixed the two groups.
Raw pdfplumber cells from data/registry/krankenhausplan_2026.pdf, column header "zugelassene Betten stationär zum 01.01.2026":
16205 '1.020' (p.21), 56401 '1.276' (p.138), 16290 '2.062' (p.245), 16291 '1.176' (p.246), 56290 '1.462' (p.248), 66390 '1.523' (p.249), 76190 '1.699' (p.250).
The PDF prints counts >= 1000 with a German thousands dot; pflege_jobs/sources/krankenhausplan.py parse() did `int(beds) if beds.isdigit() else None` -> '1.020'.isdigit() is False -> None. That is why exactly the 7 biggest sites (all >1000 beds) were NULL. Hochschulkliniken ARE listed with beds in the plan (Teil II, "1. Hochschulkliniken", p.245-250).

## Parser fix (AC2)
New _int(cell): '' / '-' -> None; ^\d{1,3}(\.\d{3})*$ -> int without dots; anything else raises ValueError (loud, no silent NULL). Used for beds and day_places.
Full re-parse of the 2026 PDF with the fix: 399 sites, no ValueError, and the diff vs clinics.csv on beds/day_places is exactly these 7 beds cells, nothing else.
Tests (tests/test_krankenhausplan.py): test_count_cells_read_the_german_thousands_separator (frozen cells 2.062, 1.020, 911, 0, -, ''), test_count_cell_in_an_unknown_format_fails_loudly ('1,020', 'ca. 50', '10.20').
Mutation test: copied to /tmp/krankenhausplan.py.orig, restored old isdigit()->None body -> 2 failed / 6 passed; restored from /tmp copy, __pycache__ deleted, 8 passed, diff -q identical.
Full suite (.env sourced): 1543 passed, 18 skipped, 0 failed. Without .env, tests/test_reverify_and_clean.py fails at collection with KeyError: 'SUPABASE_URL' (pre-existing, env-only).

## Live write (AC3, AC5)
Corrections file backups/task167_beds.json -> tools/apply_clinic_corrections.py --dry-run then --push. Backup: backups/apply_clinic_corrections_task167_beds_before_20260929T151410Z.json. "clinics upserted 7/7", read-back OK for all 7.
source appended (existing text kept): " | beds: 'zugelassene Betten stationär zum 01.01.2026' = <cell>, Krankenhausplan 2026 PDF p.<n> (Planbetten; was NULL via thousands-dot parse bug, TASK-167)".
Values (Planbetten = zugelassene Betten, Stand 01.01.2026 -- same number kind as the other rows):
16205 München Klinik Bogenhausen 1020 | 16290 LMU 2062 | 16291 TUM rechts der Isar 1176 | 56290 FAU Erlangen 1462 | 56401 Klinikum Nürnberg Nord 1276 | 66390 UK Würzburg 1523 | 76190 UK Augsburg 1699.
data/registry/clinics.csv: same 7 rows, beds + source only (git diff 7+/7-). A re-sync via data/sync_krankenhausplan_2026.py now produces the same beds (parser fixed); it would reset `source` to the plain edition string (TAKE_FROM_2026 includes source) -- values stay, only the appended note would go.
After: acute beds NULL = 0 (was 7).

## beds = 0 (AC4) -- all 50 are the source value, no writes
Every one reads '0' in "zugelassene Betten stationär" of the 2026 PDF. Split:
- Day-place-only Plan-KH / Vertrags-KH (0 beds, N Plätze), correct: 16104(15) 16105 Danuvius Ingolstadt(15) 16254(25) 16256(50) 16259(88) 16260(35) 16261(15) 16262(50) 16263(30) 16266(80) 16268(20) 16304 kbo-ISK Rosenheim(40) 17105(16) 17106 kbo-ISK Altötting(30) 17405 kbo-IAK Dachau(20) 17502(30) 17606(20) 17803 kbo-IAK Freising(20) 18104 kbo-Heckscher Landsberg(15) 19005 kbo-LMK Peißenberg(30; Bemerkung: 20 PSY-Betten interimsweise von Garmisch, Verlagerung nach Weilheim) 26204 BKH Passau KJP(18; Bemerkung: interimsweise 6 KJP-Betten bei KeZ 26107; 26 Betten in Planung) 26206(25) 27107(15) 36102(32) 36302(12) 46110(12) 46306(20) 46307(12) 46405(12) 47403(30) 47602(15) 56413(40) 57504(20) 57605(24) 57706(20) 66104(40) 66205(20) 66310(14) 77605(20) 77908(15) 18475 Urologische Klinik Planegg (Vertrags-KH, 3 Plätze Brachytherapie, befristet bis 31.12.2026) 46170 (see below).
- Bedarfsfeststellung, 0 beds / 0 places today, only planned capacity: 16106(20 Betten geplant) 16257(40 Pl.) 17306(20 Pl.) 17308(16 Pl.) 17706(20 Pl.) 37304(25 B/10 Pl.) 57506(14 Pl.) 57707 Klinik Altmühltal(140 B geplant).
The brief's "full hospitals" among these are the day-clinic KeZ of multi-site operators; the beds sit on sister KeZ (e.g. kbo-ISK Wasserburg 18712=518, kbo-LMK Garmisch 18005=120, kbo-IAK München-Ost 18402=750, BKH Passau Erwachsenen 26205=60, Danuvius Neuburg 18505=40 / Pfaffenhofen 18605=80).
Metric: app/coverage.py _beds_ratio already skips beds<50 and app/data.py jobs_per_100_beds is None for beds=0, so these are already excluded from per-bed metrics; no change needed. Side note: vacancies matched to a 0-bed day-clinic KeZ instead of the bed-carrying sister drop out of the ratio -- a matching question (TASK-166 area), not a beds one.
46170 vs 46101 Klinikum Bamberg Bruderwald: not a duplicate. 46101 = Plan-KH, 911 Betten / 61 Plätze (p.114). 46170 = separate Vertrags-KH entry (Versorgungsvertrag, p.258), 0 Betten / 20 Plätze, "Befristet bis 31.12.2027 für Leistungsbereich Post-Covid-Syndrom". Same physical site, two plan entries; both kept.
No source lookups beyond the Krankenhausplan were needed: it had a number for every row.

## Files changed
pflege_jobs/sources/krankenhausplan.py, tests/test_krankenhausplan.py, data/registry/clinics.csv (7 rows), backlog TASK-167. Not touched: pflege_jobs/registry.py, pflege_jobs/cli.py.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
7 NULL beds were a parser bug (German thousands dot '1.020' failed isdigit). Fixed _int() + tests + mutation test; 7 live rows filled from Krankenhausplan 2026 cells with page provenance; acute NULL 7->0. The 50 beds=0 rows are literal 0 in the PDF (day clinics / Bedarfsfeststellung), left as-is and already excluded from the per-bed metric.
<!-- SECTION:FINAL_SUMMARY:END -->
