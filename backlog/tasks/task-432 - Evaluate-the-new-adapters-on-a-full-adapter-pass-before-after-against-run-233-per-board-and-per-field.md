---
id: TASK-432
title: >-
  Evaluate the new adapters on a full adapter pass: before/after against run
  233, per board and per field
status: To Do
assignee: []
created_date: '2026-10-06 07:40'
updated_date: '2026-10-06 15:50'
labels:
  - crawler-coverage
  - adapter-testing
dependencies: []
priority: medium
ordinal: 306000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06: judge how effective the new crawler is. Baseline: nightly adapter run 233 (2026-10-05, old adapters, commit 111e8f7: 21700 lines, 255 job-posting boards, 16285 job postings). New code: commit 3e891d0 and later (TASK-185/186 adapters: P&I position popup, Oracle CE REST for Sana, BITE place from custom field, place of the posting itself). Run 235 (night of 2026-10-06) was killed by a service restart at 07:33 UTC after 175 of 255 boards; on the 173 boards both runs share it showed no regression: 9902 vs 9875 postings, description filled 98.4 to 99.7 percent, loc 100 percent, employmentType 81.5 to 82.6 percent, datePosted 80.5 to 80.4 percent; 52 boards differ (AMEOS 734 to 694, Kreisklinik Woerth 11 to 1, LMU 159 to 152, Klinik Ebe 25 to 20, Schoen Klinik 296 to 291 and 294 to 291; kbo +6, TUM +4). The effect of the Sana Oracle and P&I changes is not visible yet. Work: take a full manual adapter run (started by the owner from the Pro panel, schedule Daily full pass, trigger run-now; record its run id here) and compare with run 233 and the earlier nights per board and per field (title, loc, description, employmentType, datePosted, org): counts, fill rates, posting identity (only in old, only in new), and name each drop as closed posting, adapter loss or different keying with evidence from the page. Separate the adapter effect from the day-to-day change by also comparing run 233 with run 231 (same old code, two nights). Not part of this task: any restart or deploy; other work does not wait for this run.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Full manual adapter run id recorded in the notes with its commit_sha and duration
- [ ] #2 Per-board table old vs new (counts and field fill rates) with the 231-vs-233 night-to-night noise as the baseline
- [ ] #3 Every board that lost more than 5 percent or 3 postings is classified (closed posting, adapter loss, keying change) with evidence; adapter losses get a task
- [x] #4 Effect of the Sana Oracle, P&I and BITE changes stated in postings and field fill rates
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-06 RESULT (pflege-clawl, read-only on crawl_output; scripts in the job directory). RUN: id 237, commit b7e353a4239e, started 07:56:54, finished 15:44:15 UTC = 7 h 47 min against 6 h 43 min for run 233 (+16 percent); 26 422 lines, n_new 109 (run 233: 94, run 231: 2). VOLUME: job postings 16 285 to 20 989 (+4 704). Sana (vendor-oracle-v1) 22 to 3 278 rows, Asklepios (new vendor-asklepios-v1) 1 381 rows; the rest +67 (wp_jobs +43). Rows with a Bavarian place on the new national boards: Sana 684 of 3 589, Asklepios 118 of 1 381; the others are kept raw (raw-first, labelled later). Boards 255 to 256 (only in 233: krankenpflegejobs24 7 rows, kwa-rehaklinik 2; only in 237: asklepios.com 1 381 and two Sana boards 1 086 and 1 085). FIELD FILL on the 253 common boards: description 98.9 to 99.7, datePosted 79.0 to 80.5, title, loc, org 100; employmentType 83.1 to 78.8, explained by the Sana Oracle rows (employmentType 100 to 1 percent on 3 278 rows): a defect. By adapter: erecruiter description 63 to 100 and employmentType 50 to 74, group-v1 description 86 to 100, muenchen_klinik 98 to 100, all others unchanged. Observation stream (collector seed-20, where P&I and BITE rows arrive): 5 415 to 5 433 rows, city 100 and plz 89 percent unchanged. NOISE (233 against 231): 27 of 254 common boards differ, one loses 3 or more (vitrea -6). NEW CODE (237 against 233): 76 of 253 differ, 13 lose more than 5 percent or 3 postings, 52 postings in all (0.3 percent of 16 269); AMEOS stable 734 to 735 (the earlier partial-run drop was an artefact). CLASSIFICATION of the 13 (by title, lost titles looked up on the live page, one GET per board): closed on the site, not on the page now: Klinik Ebe 5, Schoen Klinik about 12 over three board URLs, khdw 2, Wolfart 1, Muerz 2; moved to the new Asklepios group board: Asklepios Bad Abbach 2; junk removed: Kreisklinik Woerth 10 of 11 rows were titled "Online- bewerbung" (an application link, not a vacancy; one such row remains); unknown, the page is JS or blocked: LMU 7 (every URL key changed, the count differs by 7 only), Fuerth 4 on two board URLs, Nordoberpfalz 3, Reisach 1. Adapter defect: muerz.de titles now end with "(opens in new tab)" (0 rows in 233, 2 in 237). Two lost titles are still on the live page (Ebe Intensivstation/IMC, a Schoen Klinik multi-site title): not decidable from one day. NOT DONE (usage limit): per-board CSV (script eval_237_table.py written, not run); follow-up tasks for the Sana employmentType defect and the muerz.de title suffix; re-check of LMU, Fuerth, Nordoberpfalz, Ebe, Schoen Klinik on run 238 with the same code (if the title returns the loss is flapping, if not it is expiry). AC 1 and 4 are met; 2 and 3 are partial, so the task stays open.
<!-- SECTION:NOTES:END -->
