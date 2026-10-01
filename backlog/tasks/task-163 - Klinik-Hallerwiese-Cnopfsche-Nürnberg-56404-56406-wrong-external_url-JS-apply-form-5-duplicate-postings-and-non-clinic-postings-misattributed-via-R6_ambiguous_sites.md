---
id: TASK-163
title: >-
  Klinik Hallerwiese/Cnopf'sche Nürnberg (56404/56406): wrong external_url (JS
  apply-form), 5 duplicate postings, and non-clinic postings misattributed via
  R6_ambiguous_sites
status: Done
assignee: []
created_date: '2026-09-28 06:27'
updated_date: '2026-09-28 16:40'
labels:
  - db-quality
  - matching
dependencies: []
ordinal: 161000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Reported by pflege-email-harness session (Ivan relay) 2026-09-28 while preparing candidate letters -- nurse-79's letter currently links posting 7087 by the broken URL.

1. bite.py's external_url picks jp.get('applyUrl') over jp['url']; for this Diakoneo tenant applyUrl is a JS-only application form (jobs.diakoneo.de/de/jobposting/<id>0/apply, 49 chars of plain-HTML text) while jp['url'] is the real, content-bearing ad (jobs.diakoneo.de/jobposting/<id>, verified live 200/4227 chars for posting 7087). Affects postings first-seen since Sept 2026: 7087,7086,7099,7081,7047,7064,7063,7097,7076,7079,7094,7098,7104,7120,7028,7048,7069,10371,11076,15138.
2. Duplicates: old postings 5819-5823 (plain ad URLs, first_published null) are the SAME jobposting ids as 5 of the new ones -- 5823=7087, 5822=7086, 5820=7099, 5821=7081, 5819=7047. Both copies open+live.
3. Misattribution via clinic_match_rule=R6_ambiguous_sites:56404,56406 -- some postings are for OTHER Diakoneo-affiliated facilities that are not Krankenhausplan clinics at all: 7120/15138 (Laurentius Sozialstation Nürnberg, ambulatory care), 7094 (Wohnstift Hallerwiese, senior residence), 7097 (Pädiatrie in Ansbach, different town). 7076/7079/7098 ('- Nürnberg' titles) need checking: genuinely Hallerwiese/Cnopf'sche or another Diakoneo-Nürnberg entity.
4. FYI only, not this task's scope: posting 7087's own text says Klinik Hallerwiese moves to Klinikum Nürnberg 2027-01-01 -- may matter for employer mapping later.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 external_url priority fixed in bite.py (prefer jp['url'], the same field already used for source_ref/identity, over applyUrl) -- live-verified on posting 7087 and at least 2 more of the named ids after a re-crawl
- [ ] #2 5819-5823 duplicate rows retired (verify_status=gone) with a backup, live-verified 5823/5822/5820/5821/5819 no longer open
- [ ] #3 Each named non-Hallerwiese/Cnopf'sche posting (7120,15138,7094,7097) unmatched (clinic_id cleared) rather than left on 56404/56406; 7076/7079/7098 checked against their actual employer_name/content and resolved one way or the other with evidence, not guessed
- [ ] #4 Reply sent to pflege-email-harness with exactly what changed, before nurse-79's letter goes out
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Full-suite verification correction: an unfiltered `pytest -p no:cacheprovider -q` run (no `-m` filter)
showed 174 failed -- all in tests/test_adapter_completeness.py, test_completeness_beesite_hr4you.py,
test_verify_pi_loga_live.py, none touching cli.py/bite.py/the new test file. Root cause: those 3 files
are @pytest.mark.network (pytest.ini: "talks to live boards ... deselect with -m 'not network'"), and
this run coincided with our own production crawl (run 208, the scheduled daily full pass) hammering
many of the same external hospital career sites for 6+ hours concurrently -- resource/rate-limit
contention, not a code regression. Re-ran the correct command, `pytest -m "not network" -q`: 1541
passed, 18 skipped, 2131 deselected, 0 failed, in 7min. That's the real signal; the unfiltered run's
174 failures should be disregarded (or re-run in isolation, off-hours, if the live-adapter contract
itself needs re-checking -- not done here, out of scope).
<!-- SECTION:NOTES:END -->
