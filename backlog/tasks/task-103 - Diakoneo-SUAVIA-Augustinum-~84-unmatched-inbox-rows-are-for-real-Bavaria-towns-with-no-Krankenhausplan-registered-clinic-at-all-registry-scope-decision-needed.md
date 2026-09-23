---
id: TASK-103
title: >-
  Diakoneo/SUAVIA/Augustinum: ~84 unmatched inbox rows are for real Bavaria
  towns with no Krankenhausplan-registered clinic at all -- registry-scope
  decision needed
status: To Do
assignee: []
created_date: '2026-09-22 16:11'
updated_date: '2026-09-22 18:20'
labels: []
dependencies: []
references:
  - pflege_jobs/sources/inbox.py
  - pflege_jobs/registry.py
ordinal: 103000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Same 2026-09-22 unmatched-inbox review as TASK-99/100/101/102, but this one is a scope question, not a guessable code fix. Three seeded-adapter operators (collector seed-20, kind=observation) post their entire company-wide job feed, tagged with one generic legal-entity employer_name, spanning far more locations than the registry tracks for them: jobs.diakoneo.de (53 rows, employer_name always 'Diakoneo KdöR', towns Bruckberg/Obernzenn/Himmelkron/Erlangen/Forchheim/Rothenburg ob der Tauber/Pleinfeld/... -- the registry has only 4 Diakoneo clinics total, in Ansbach/Nürnberg x2/Schwabach, none in these towns); bewerbung.augustinum-gruppe.de (9 rows, employer_name 'Augustinum gGmbH', towns Bischofswiesen/Unterschleißheim/Bad Tölz/Oberschleißheim plus 2 rows literally 'Deutschland'/'Deutschlandweit' -- the registry has exactly one Augustinum clinic, in München, matching none of these); karriere.suavia.de (22 rows, employer_name 'SUAVIA Gesundheit gGmbH', towns Weißenhorn/Illertissen/Neu-Ulm -- SUAVIA has ZERO presence in the clinics registry at all, by name or operator). Checked whether these towns have ANY registered clinic under a different name/operator (to rule out a simple attribution gap): Weißenhorn and Neu-Ulm do have unrelated registered hospitals (Stiftungsklinik Weißenhorn 77503, Donauklinik Neu-Ulm 77502), Forchheim has two (Klinikum Forchheim 47401, Tagesklinik 47403), Bad Tölz has two (KIRINUS Schlemmer Klinik 17305, Asklepios Stadtklinik 17302) -- none confirmed as the SAME facility as the operator's posting (no shared name/operator token at all), so a code-level match would be a guess, not evidence. The likely explanation: Diakoneo/Augustinum/SUAVIA all also run non-hospital facilities (elder care/Diakonie social services) in these towns, and this registry only covers Krankenhausplan Bayern hospital sites (clinics.source = 'Krankenhausplan Bayern 2026...') -- so these postings structurally cannot match any clinic_id no matter how the Matcher improves, unless registry scope is deliberately widened. Not fixed or guessed at here per the no-self-invented-workaround rule; needs Ivan's call on scope.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Per operator (Diakoneo, SUAVIA, Augustinum), confirmed whether the unmatched towns are non-Krankenhausplan facilities (elder care/social services) of an otherwise-tracked operator, or a genuine missing-registry-row gap (a real Krankenhausplan hospital that should be listed but isn't)
- [ ] #2 Decision recorded: expand registry scope to cover these operators' non-Krankenhausplan facilities for candidate-clinic matching, or accept these rows as permanently and correctly unmatched and consider filtering them out earlier in the pipeline (e.g. at the seed/role-class stage) instead of carrying them to intake every run
- [ ] #3 If scope expansion is chosen, the specific facilities/towns to add are named with a source (Diakoneo, Augustinum, and SUAVIA's own site listings or an equivalent authoritative source)
<!-- AC:END -->

## Comments

<!-- COMMENTS:BEGIN -->
created: 2026-09-22 18:20
---
2026-09-22, zero-yield-boards follow-up: same class found for BRK-Kreisverband Muenchen's own pi_asp board (www.pflegejobs.brk-muenchen.de, companyEid=123, 20 postings live, 2 nursing-relevant). It has no Krankenhausplan clinic of its own either -- the registered careers_url just happens to sit on clinic 16254 'Tagesklinik Sued fuer Psychiatrie und Psychotherapie', an unrelated small day clinic. Do not default-attribute this board's postings to 16254 (reverted a data/registry/pi_seeds.json default.kez=16254 entry that did this). Same scope decision as Diakoneo/SUAVIA/Augustinum: fold in when this gets picked up.
---
<!-- COMMENTS:END -->
