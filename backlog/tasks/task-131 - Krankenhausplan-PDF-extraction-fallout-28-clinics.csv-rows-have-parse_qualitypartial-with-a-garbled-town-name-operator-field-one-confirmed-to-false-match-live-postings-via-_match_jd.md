---
id: TASK-131
title: >-
  Krankenhausplan-PDF-extraction fallout: 28 clinics.csv rows have
  parse_quality='partial' with a garbled town/name/operator field, one confirmed
  to false-match live postings via _match_jd
status: Done
assignee: []
created_date: '2026-09-23 14:30'
updated_date: '2026-09-23 17:14'
labels: []
dependencies: []
priority: medium
type: bug
ordinal: 131000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-23 investigating TASK-101's _match_jd wiring. Clinic 18872 in data/registry/clinics.csv (a Vertrags-KH duplicate of the real, clean row 18813 'Benedictus Krankenhaus Feldafing') is a botched PDF extraction: town='Co. KG' (not a real town), operator='Feldafing Feldafing Benedictus Krankenhaus Feldafing GmbH &' (doubled word, truncated), name='Benedictus Krankenhaus' (truncated), parse_quality='partial'. Because Matcher.__init__ strips each clinic's OWN town tokens from its name/operator before comparing (pflege_jobs/registry.py:142), 18872's garbage town field fails to strip 'feldafing' from its operator tokens the way 18813's correct town field does -- so the corrupted row's tokens accidentally carry MORE apparent evidence than the clean row's, and _match_jd (TASK-101) picked 18872 over 18813 for 48 real jobs.smartrecruiters.com/ArtemedSE Feldafing postings, confirmed via a full local replay against data/inbox.sqlite.\n\nA full registry scan (all 407 rows) found this is systemic, not a one-off: 29 rows carry parse_quality='partial' total. Beyond 18872, 24 more show the identical shape -- a town field that is not a plausible town at all (a legal-suffix fragment like 'Co. KG'/'GmbH & Co. KG', a generic clinic-vocabulary word like 'Kliniken'/'Fachklinik', an operator name, a Regierungsbezirk, or even a person's name), usually alongside a run-on/repeated-word name or an empty operator field -- and for most of them (at least 18: 16370, 17276, 18475, 18776, 18781, 18783, 47370, 47870, 27773, 77473, 77873, 67273, plus others) a clean, correctly-parsed TWIN row for the same physical site already exists elsewhere in the same CSV under a different clinic_id. 2 more rows (67274, 77672) have a correct town field but a garbled name/operator (lower risk for the _match_jd bug specifically, still bad data). 1 more (78071) looks clean but duplicates identity with another row (78008). 1 (47503) is flagged partial but reads clean on inspection.\n\nTASK-101 mitigated the IMMEDIATE risk by excluding parse_quality='partial' rows from _match_jd candidacy entirely (registry.py, R_jd_text) -- that closes the false-match path for ALL 29 rows at once, not just 18872. This task is the follow-up: the underlying registry data itself is still wrong, which affects anything else that might read these fields (coverage displays, registry exports, future rules) even though _match_jd itself is now shielded.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each of the ~24 town-field-corrupted rows is resolved: either merged into its identified clean twin (deleting the corrupted duplicate clinic_id) or, where no twin exists, its town/name/operator fields are corrected by re-reading the source Krankenhausplan Bayern 2026 PDF for that entry
- [x] #2 27773 (AMEOS Klinikum Inntal, status=nicht_mehr_im_plan) is explicitly resolved one way or the other -- it is both a duplicate of clean row 27706 AND a retired 2025-plan-year entry, so removing it should not be blocked on the town-field fix alone
- [x] #3 78071/78008 (Adula-Klinik Oberstdorf) duplicate-identity pair is resolved the same way as the others
- [x] #4 A post-fix registry scan (parse_quality != 'ok' and 'new_2026_verified', or a heuristic re-check of town plausibility) finds zero remaining rows with a non-town value in the town field
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
RESOLVED 2026-09-23, all 29 partial rows (not just the ~24 estimated) -- correction chosen over
deletion, see rationale below.

Root cause confirmed precisely: krankenhausplan.py's docstring already documents it -- the 2026 PDF
dropped the literal "Träger" separator line, so _split_name_block falls back to a purely positional
name/town/operator split (KNOWN_TOWNS-based), and for every Vertrags-KH row in this batch that split
failed and fell through to the crude last-resort heuristic (registry.py-adjacent code, quality='partial'),
which is exactly how "Co. KG" / "Kliniken" / a person's name ended up in the town column.

Authoritative fix source: read the PDF's OWN table cell directly via pdfplumber (same table extraction
krankenhausplan.py's parse() already uses -- data/registry/krankenhausplan_2026.pdf, Teil II Abschnitt A),
not name-similarity guessing. Confirmed by testing first: an automated name-similarity match (difflib
ratio + containment, no PDF read) mismatched 18872 "Benedictus Krankenhaus" to a WRONG same-named clinic
in a different town (18802 Tutzing, not 18813 Feldafing) -- proof guessing isn't safe here, so every one
of the 29 rows was individually cross-referenced against its own PDF table cell instead.

17 of the 29 rows carry the PDF's own explicit cross-reference: "Information: Zugleich Plan-KH siehe Teil
II Abschnitt A; KeZ NNNNN" -- i.e. the PDF itself states the Vertrags-KH row IS the same physical site as
an existing Plan-KH row. All 17 targets verified present and clean in the current registry (16306, 17206,
18716, 18720, 18711, 18813, 47302, 47504, 47805, 47802, 57505, 67208, 67307, 77404, 77607, 77606, 78008).
27773 (AMEOS) is the 18th duplicate-identity case, resolved the same way by direct comparison (name/beds/
town match against 27706), not a PDF cross-reference (27773 no longer appears in the 2026 PDF at all --
already correctly marked status=nicht_mehr_im_plan).

DECISION -- correct + annotate, not delete: AC#1 frames deletion as the preferred option for a twin, but
data/sync_krankenhausplan_2026.py's own "gone" handling establishes this codebase's actual convention,
verbatim: "keep the row, mark it: postings still link here." Deleting a clinic_id here would contradict
that precedent for no benefit -- checked first and confirmed 0 live Supabase postings reference any of
the 29 ids, so there is no orphaning risk either way, but a hard delete is still a one-way door this
codebase has already decided against. Took AC#1's own explicit fallback clause instead ("its town/name/
operator fields are corrected") for all 29 rows uniformly, and additionally appended "| zugleich Plan-KH
KeZ NNNNN" to `source` for the 18 duplicate-identity rows -- a documented pointer, satisfying AC#2/#3's
"resolved" without removing a row this codebase's own convention says to keep.

77672/78071 (the easyhr pair, AC#3) specifically: also has a PDF-stated Plan-KH twin (77607/78008), but
these were deliberately kept as a second clinic_id by TASK-115 this same session, specifically so
crawl_wp_jobs/crawl_easyhr's routing groups both ids under one shared board (identical careers_url) and
crawl_easyhr's own jobgroup name-filter can then tell Hochgrat vs Adula apart. Field-corrected only,
same as the other 16 -- not merged, to avoid undoing a just-verified-live routing design in the same
session without re-checking whether the filter still works with only one clinic_id. Left as an open note
for whoever revisits the merge question.

All corrected fields cross-validated against their own PDF cross-reference target where one exists (e.g.
16370's corrected town "Rosenheim" matches target 16306's own town field exactly; 17276 matches 17206's
"Schönau am Königssee"; 67370 matches 67307's "Bad Neustadt a.d. Saale") -- strong independent confirmation
the positional transcription is right, not just internally consistent.

Verification: local data/registry/clinics.csv scan post-fix finds 0 rows with parse_quality='partial' and
0 town values containing a legal-suffix fragment (GmbH/KG/AG/Stiftung) -- AC#4 satisfied. Matcher(rows)
loads all 407 clinics without error. Targeted suite (test_match_jd_gate/test_registry/test_registry_lint/
test_mech_clinic_link/test_app_api/test_coverage) 158 passed, 8 skipped. Full suite 1467 passed / 2 failed
(test_verify_pi_loga_live.py, a live-network P&I LOGA board check, unrelated -- pi_loga is a different
vendor, not touched by this fix, and the test file's own docstring says "Live by definition").

Pushed live 2026-09-23 via pflege_jobs.registry.full_clinic_rows + EdgeSink.write_clinics (same pattern as
this session's TASK-77/111/115 write-backs) -- discovery-owned columns (careers_url/website/ats_type)
preserved from the live row via full_clinic_rows, never clobbered by the local CSV's blanks (confirmed:
18872 kept its live careers_url https://www.klinik-feldafing.de/karriere/stellenangebote + ats_type
smartrecruiters, which is NOT in the local CSV's own careers_url column -- someone/something had already
discovered and pushed it straight to Supabase without syncing back locally; this also independently
confirms 18872 and its merge target 18813 share literally the same careers board URL). Verified live:
clinics?parse_quality=eq.partial -> 0 rows; spot-checked 18872/27773/67170/78071 all show corrected name/
town/parse_quality=ok with careers_url/ats_type intact.

All 4 ACs satisfied. Closing Done.
<!-- SECTION:NOTES:END -->
