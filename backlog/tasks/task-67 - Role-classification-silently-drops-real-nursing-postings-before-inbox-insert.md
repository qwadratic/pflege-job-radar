---
id: TASK-67
title: Role classification silently drops real nursing postings before inbox insert
status: Done
assignee:
  - '@claude'
created_date: '2026-09-18 10:07'
updated_date: '2026-09-18 13:08'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 67000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Crawler-review 2026-09-18. Three defects in pflege_jobs/patterns.json plus one wiring gap cause certified nursing postings to be classified nicht_pflege/pflegehelfer and dropped by app/crawl.py :410 before they ever reach the inbox (they never show up as a crawl error, since classify.py returns first before any excluded-class logging). (1) role.pflege_gate (patterns.json:76) is missing tokens its own kept role rules match on -- hygienefachkraft, hygienebeauftragte, nachtwache/dauernachtwache, "advanced practice", plural "nurses", plural "betreuungskraefte" -- so classify.py:88 returns (nicht_pflege, no_pflege_token) before the _ROLES loop runs whenever the department label is missing (71% of rows) or names a non-nursing bucket. Measured: 12 distinct Bavarian posting URLs lost in run_89.jsonl alone, recurring every run (Klinikum Memmingen 2x Hygienefachkraft, LA Regio/Kinderkrankenhaus St. Marien Landshut, Rottal-Inn Simbach am Inn, AMEOS Neuburg 4 postings, Barmherzige Brueder Muenchen Dauernachtwache, ProSomno). (2) In patterns.json first-match-wins roles array, the pflegehelfer rule (:113) sits before pflegefachkraft (:117), so a title advertising both qualifications ("Pflegefachkraft (m/w/d) oder Pflegefachhelfer (m/w/d)") is labelled pflegehelfer -- an EXCLUDED_ROLE_CLASSES member -- and silently dropped. Measured: 12 distinct URLs / 45 rows across 10 runs including today, on Donau-Ries Kliniken (6 URLs), Klinikum Nuernberg, Kreiskrankenhaus Schrobenhausen, LA-Regio Kliniken Landshut, Klinik Kitzinger Land, Vitrea Kipfenberg. (3) strong_pflege (patterns.json:78) leads with the bare substring "pfleg", so a facility name like Pflegezentrum/Pflegeheime/Tagespflege vetoes the entire nicht_pflege list, letting non-nursing titles (Hauswirtschaftshilfe, Reinigungskraft) at such facilities fall through to the sonstige_pflege fallback and get published as nursing vacancies -- the inverse failure mode, ~6 confirmed on live registry boards including dongku.de. (4) pflege_jobs/sources/firecrawl_agent.py jobs_to_inbox_rows (:433) writes the agent-read board category to payload["department"], but both role gates (app/crawl.py:409, pflege_jobs/sources/inbox.py:54) read only payload["section_labels"], so a Firecrawl-agent nursing posting whose title has no pflege token is dropped even though its own board category says "Pflegedienst" -- confirmed real loss on clinic 37504 (Caritas-Krankenhaus St. Maria Donaustauf, "Intensivfachkraefte fuer Intensiv- und Weaningstation"). See /tmp/crawler_review_2026-09-18.md "pflege_jobs/patterns.json" :76/:78/:113 and "pflege_jobs/sources/inbox.py" :54 for full evidence.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 pflege_gate in patterns.json includes nachtwache|hygienefachkraft|hygienebeauftragte|advanced practice|fachweiterbildung|critical care|kinderintensiv|intensivfachkr|\bgkp\b|\bguk\b (and nurses? instead of nurse), and a test asserts every alternative of a non-excluded role rule passes the gate on its own
- [x] #2 The pflegehelfer rule is moved after pflegefachkraft/fachpflege in the roles array so a title naming both qualifications is classified by the higher one
- [x] #3 strong_pflege is narrowed from bare "pfleg" to role stems (pflegefach|pflegekr[aä]ft|pfleger\b|pflegerin\b|pflegerisch|pflegedienst|pflegeexpert|pflegepädagog|op-pflege), and nicht_pflege gains back-office stems (buchhalt|controlling) that were previously masked by the bare-pfleg override
- [x] #4 firecrawl_agent.jobs_to_inbox_rows also emits payload["section_labels"] from the agent department label so app/crawl.py and inbox.py role gates see the board own nursing-category signal
- [x] #5 Replaying run_89.jsonl (or an equivalent current capture) through the fixed patterns shows the 12 pflege_gate-dropped URLs and the 12 pflegehelfer-order URLs now classified as nursing and reaching the inbox, and the dongku.de Hauswirtschaftshilfe/Reinigungskraft titles no longer classified as nursing
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. pflege_jobs/patterns.json role.pflege_gate: added nachtwache, hygienefachkraft, hygienebeauftragte, 'advanced practice', fachweiterbildung, 'critical care', kinderintensiv, intensivfachkr, \bgkp\b, \bguk\b, betreuungskräfte (plural); widened \bnurse\b to \bnurses?\b -- every one of these tokens was already matched by a KEPT role rule (apn_experte, fachpflege, pflegefachkraft) but the gate itself didn't recognise them, so a title with no OTHER pflege keyword and no section confirmation was dropped as nicht_pflege before the rules loop ever ran.
2. patterns.json role.rules: moved pflegefachkraft ahead of pflegehelfer (first-match-wins) -- a title offering both qualifications ('Pflegefachkraft oder Pflegefachhelfer') was classified by the lower one and silently dropped (pflegehelfer is in EXCLUDED_ROLE_CLASSES).
3. patterns.json role.strong_pflege: narrowed from the bare substring 'pfleg' to role-specific stems (pflegefach|pflegekr[aä]ft|pfleger\b|pflegerin\b|pflegerisch|pflegedienst|pflegeexpert|pflegepädagog|op-pflege|krankenpfleg|altenpfleg, plus the pre-existing op-fachkr/krankenschwester/hebamme/entbindungs/ota/ata/operationstechn/anästhesietechn/stationsleit/praxisanleit/nurse/apn) -- the bare stem let any facility NAME containing '...pflege...' (Pflegezentrum, Pflegeheim, Pflegebuchhaltung) veto the whole nicht_pflege exclusion list for every title at that site. Also added buchhalt|controlling to nicht_pflege (this is what the narrowing actually exposed to be excludable -- previously masked by the same override).
4. pflege_jobs/sources/firecrawl_agent.py jobs_to_inbox_rows(): now also emits payload['section_labels'] = [department] alongside the existing (unused-by-the-gates) 'department' key -- app/crawl.py's pre-inbox filter and pflege_jobs/sources/inbox.py's intake gate both read section_labels via pflege_jobs.section.job_confirmed_nursing, never department.
5. Empirically replayed BOTH old and new patterns.json against 5086 distinct real titles from 4 production crawl captures (crawl_output/run_{89,91,94,96}.jsonl): 46 titles flip classification. 8 flip to a MORE correct nicht_pflege (facility-name false positives the strong_pflege narrowing was meant to catch: 'Finanzbuchhalter... Pflegeheime', 'Medizinische Fachangestellte - Pflegestation', 'Hauswirtschaftshilfe für das Pflegezentrum...', 'Reinigungskraft... Haus für Pflege...', a Sozialpädagoge/Casemanager title that was wrongly reaching apn_experte via the old override). 38 flip to a correctly-kept nursing class (hygienefachkraft x3, nachtwache/dauernachtwache, GKP abbreviation, betreuungskräfte plural, fachweiterbildung, and ~15 dual-qualification titles that now correctly land on pflegefachkraft instead of pflegehelfer). Zero titles found where a genuinely-nursing title flipped to excluded, or a genuinely-non-nursing title flipped to kept.
6. During the replay, found and fixed one narrowing side effect before it shipped: titles combining 'Gesundheits- und Krankenpflege' (the bare profession noun, no -kraft/-fach suffix) with a competing occupation lost the strong_pflege override; added krankenpfleg|altenpfleg to strong_pflege specifically (does not match 'Pflegezentrum'/'Pflegeheim'/'Pflegebuchhaltung', confirmed).
7. Updated 3 pre-existing tests whose assertions encoded the OLD buggy behaviour as an expected invariant (tests/test_classify_section.py, tests/test_sinks.py) -- each now asserts the corrected classification, with one (Gerontofachkraft) kept as a real example that still needs the nursing_section_confirmed relaxation. Added 3 new test functions in tests/test_mech_role_class.py (gate tokens, dual-qualification order, strong_pflege narrowing) and section_labels assertions in tests/test_firecrawl_agent.py.
8. Full offline suite -m 'not network': 1007 passed / 1 skipped / 1 pre-existing unrelated failure (tracked as TASK-75 AC#1).
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed all 4 named defects in pflege_jobs/patterns.json plus firecrawl_agent.py's missing section_labels wiring. Empirically validated against 5086 distinct real titles from 4 production crawl captures (not just hand-picked examples): 46 titles flip classification, 8 to a more-correct exclusion (facility-name false positives the strong_pflege narrowing targeted) and 38 to a correctly-kept nursing class (hygienefachkraft, nachtwache, GKP, betreuungskräfte plural, ~15 dual-qualification titles). Found and fixed one narrowing side effect during that replay (bare 'Krankenpflege'/'Altenpflege' profession nouns without a -kraft/-fach suffix) before shipping, via krankenpfleg|altenpfleg added to strong_pflege. Updated 3 pre-existing tests whose assertions encoded the old buggy behaviour as an expected invariant; added 3 new test functions plus section_labels assertions. Full offline suite -m 'not network': 1007 passed / 1 skipped / 1 pre-existing unrelated failure (tracked as TASK-75 AC#1).
<!-- SECTION:FINAL_SUMMARY:END -->
