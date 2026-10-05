---
id: TASK-186
title: >-
  Classifier reads title only: Ausbildung, non-nursing and no-vacancy pages are
  on the board, department missing from generic titles
status: To Do
assignee: []
created_date: '2026-10-01 17:49'
updated_date: '2026-10-05 11:52'
labels:
  - db-quality
  - classifier
dependencies: []
priority: high
ordinal: 183000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-01 by TASK-184 (judge review of 2,794 open postings with a description; examples read by hand). (1) Ausbildung under a staff title: 23 judged, 21 board-visible; role_class comes from the title ('Operationstechnische Assistenten', 'Pflegefachmann/-frau', 'Anaesthesietechnische/r Assistent/in'), the body says 'Voraussetzungen fuer die Ausbildung ... bei Ausbildungsbeginn', 'Starte jetzt deine Ausbildung', 'Berufsfachschule ... Ausbildungsverguetung 1.490 EUR'. Examples: Klinikum Freising OTA (#7376), Klinikverbund Allgaeu OTA (#12327), MLT Prager Pflegefachmann (#12641), InnKlinikum ATA (#14746), Allgaeu Pflegefachfrau Kinderkrankenpflege (#12325). (2) Not nursing but visible: 231 judged (144 board-visible; role classes sonstige_pflege 68, apn_experte 39, leitung 19): Pflegepaedagoge/Pflegelehrer, EDV Pflege, Patientenmanagement, Studienassistenz, Schulbegleitung, Fachweiterbildung (DKG) courses at jobs.ukr.de. (3) Not a vacancy: 'Derzeit haben wir keine offenen Stellen im Stationsbereich' (Salzachklinik #12173), career landing pages ('Pflegejobs: Stellenangebote fuer Pflegefachkraefte' Nuernberg #12681, 'Starte deine Karriere bei Sana' #11942). (4) Title hides the ward: 216 specialty_hidden (university clinics: LMU 94% of its titles, Wuerzburg 72%; 'Pflegefachkraft oder Altenpfleger' + body 'Thoraxchirurgie H6'); system department_hint is set on only 104 of those 216 (misses station codes like M84/G1/H6, 'Normalstationen der Augenklinik', 'Stammzelle M52'). Judge verdicts with evidence: TASK-184 working files.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Ausbildung is recognised from body markers (Ausbildungsbeginn, Voraussetzungen fuer die Ausbildung, Ausbildungsverguetung, 'starte deine Ausbildung', Berufsfachschule) and gets role_class ausbildung, so the board hides it; a staff posting that merely mentions an Ausbildung requirement ('abgeschlossene Ausbildung als ...') is NOT reclassified
- [ ] #2 Non-nursing titles (Pflegepaedagoge/Lehrer fuer Pflegeberufe, Patientenmanagement, EDV/IT, Studienassistenz, Schulbegleitung, Fachweiterbildung course pages) get nicht_pflege; no-open-positions and landing pages are not stored as vacancies
- [ ] #3 department_hint is filled from the body when the title names no department (station codes with a department in brackets, 'Bereich X', 'Klinik fuer X', named clinics), measured against the 216 specialty_hidden verdicts
- [ ] #4 Backfill over stored rows as a reviewed change set (like data/relabel_task177_backfill.py): dry-run list with evidence per row, applied through the corrections table with reason_code role_misclassified; red-green tests from frozen real texts, mutation-checked; false-positive check on the 1,465 judged-ok postings (none may flip)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-01 ~19:40 UTC, worktree agent a1ebce798259000c1 (session 663542db). Code on main and pushed: db7c360, 3488292, 6335f63, 7345fbf, aa1448c (trailers added). No DB read or write by the agent; nothing deployed yet (pflege-web restart needed; services run from this working tree).
P1 classify.py classify_role(desc=): patterns.json role.ausbildung_body (Ausbildungsbeginn, Haupt-/Real-/Mittelschulabschluss, mittlere(r) Reife/Schulabschluss) -> ausbildung after the offer_kind steps; 'abgeschlossene Ausbildung als ...' is no marker. desc= threaded into 6 adapters (inbox, career_crawl, feeds, bite, klinikum_passau, pi_asp) and mechanics._try_role. P2 patterns.json: new nicht_pflege rule after ausbildung (Pflege-/Medizinpaedagog, Lehrkraft, Lehrer, Dozent, school administration, Laborant, Pharmaberater, Verkaeufer, 'Stellenangebote' list titles), beats ota_ata/hebamme/apn/leitung; role.nicht_pflege += long-form MFA, Arzt/Aerztin, EDV, DKG Fachweiterbildung course, Frischetheke, Standortmanager, CT/MRT/MTL. P3 role.no_vacancy_body ('keine (offenen|passenden) Stellen/-angebote/-anzeigen') and no own Aufgaben/Profil section -> nicht_pflege / no_vacancy_page (same mechanism as speculative_application; a real vacancy that names another department keeps its sections and stays). P4 department_hint: only when title + Aufgaben + Profil name no department, read enrichment.dept_anchor (sucht fuer die/den/unsere <ward>, Die <ward> sucht ab sofort, Bereich <ward> Einstiegsdatum); four ward words added (Knochenmarktransplant, Stammzell, Saeugling, Kinderspital).
Measured offline on the 3,870 open postings (title + golden description): Ausbildung set 19: excluded 2 -> 18, class ausbildung 0 -> 16; non-nursing set 231: excluded 140 -> 213 (18 left for the owner); not-a-vacancy set 286 (crude): 20 -> 37, recall low on purpose (239 no_job_text rows stay: menu/footer pages without the phrase); specialty set 216: hint non-null 103 -> 134 (none lost or changed), 82 stay empty (HNO/Augen/Derma have no label, bare station codes). All 125 class changes are kept -> excluded (0 excluded -> kept). Outside the judged sets every flip was read: 2 edge cases (14655 Pflegepaedagoge/Medizinpaedagoge oder Praxisanleiter now nicht_pflege; 7538), 10 ausbildung rows that are true errors (6 judged ok although title and body say training, 4 admission pages), 12173, 19 nicht_pflege rows, 10 department rows all correct. Mutation checks: every new test caught its mutation (P1 6 marker alternatives, P2 31 tokens, P3 phrase and section guard, P4 anchors/articles/ward words); gaps found were closed with extra negatives. Suite: 11 baseline failures from missing env (PFLEGE_INGEST_URL x5, FIRECRAWL_API_KEY x3, missing sqlite table x2, assert None x1) + collection error test_reverify_and_clean + 1 Playwright timeout that passes alone; touched modules 196 passed with .env sourced.
LEFT FOR OWNER (not reclassified): Study Nurse/Studienassistenz 4 (10521 12336 14865 15142: 'Study Nurse' is a nursing token), Hygienefachkraft 3 (12839 12941 13200), MFA-or-nurse 3 (7504 12337 14999), Betreuungskraefte 2 (13297 14923), singles 6 (10142 10200 10344 12381 12756 13497), 14655, 7414/12310/15347 (title-level Ausbildung gap caught only via body), landing pages without the phrase (11938 11942 11943 13630), 6952 Hospitant.
BACKFILL (not run): data/relabel_task186_backfill.py + data/relabel_task186_backfill_set.json, dry run by default, same shape as the TASK-177 script (live re-replay must equal the reviewed set, one transaction, backup, read-back, one corrections row per field). Set: 166 open postings, 125 leave the board: 26 ausbildung (ota_ata 14, pflegefachkraft 9, sonstige_pflege 3), 93 nicht_pflege (apn_experte 40, leitung 20, sonstige_pflege 18, ota_ata 11, pflegefachkraft 5, fachpflege 3, hebamme 1, praxisanleitung 1), 6 no_vacancy_page (relabel, code not_a_vacancy, status stays open), 41 department hints. GAP: pflege_jobs.correction_reasons has no code for a department label; the script writes them as role_misclassified. Department hints are not an excluded class, so a normal crawl re-reads and updates them; recommended: leave the 41 to the next crawl instead of inventing a code. Not written (listed under not_pushed): 114 role drift rows (84 sonstige_pflege/fallback with no nursing token in the title, 20 board:* rules, 10 older leitung/apn rules; partly a replay limit, the export lacks crawl-time signals) and 81 department drift rows; see TASK-193.
UNVERIFIED: the backfill SELECT/UPDATE/INSERT never ran on Postgres (role_classes has ausbildung and nicht_pflege, postings_role_class_fkey is the only constraint on the column, checked by hand 2026-10-01); adapter desc= tested with fakes, no live crawl.

2026-10-05 relabel backfill applied (Ivan approved 2026-10-02: 125 role relabels, the 41 department hints skipped): data/relabel_task186_backfill.py --set <role-only set> --push: 125 postings (114 open, 11 expired), 161 observations, 250 corrections rows; groups nicht_pflege 93, ausbildung_body 26, no_vacancy_page 6. Backup backups/task186_relabel_before_20261005T115159Z.json. The 41 department_hint rows were not pushed (no reason code; they self-heal at the next crawl).
<!-- SECTION:NOTES:END -->
