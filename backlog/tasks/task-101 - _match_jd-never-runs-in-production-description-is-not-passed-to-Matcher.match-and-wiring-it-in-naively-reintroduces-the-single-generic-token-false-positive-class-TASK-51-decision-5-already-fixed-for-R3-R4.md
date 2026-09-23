---
id: TASK-101
title: >-
  _match_jd never runs in production (description is not passed to
  Matcher.match), and wiring it in naively reintroduces the single-generic-token
  false-positive class TASK-51/decision-5 already fixed for R3/R4
status: To Do
assignee: []
created_date: '2026-09-22 16:11'
labels: []
dependencies: []
references:
  - pflege_jobs/registry.py
  - pflege_jobs/cli.py
  - TASK-51
  - decision-5
ordinal: 101000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Same 2026-09-22 unmatched-inbox review as TASK-99/TASK-100. pflege_jobs/registry.py's Matcher._match_jd (the JD-text-mention fallback, rule R_jd_text) is fully implemented and was proven useful in a 2026-09-11 one-off re-link script (TASK-51's notes: 2 Artemed postings recovered via R_jd_text, described as 'content match correctly outranks board fallback per Matcher's own priority order') -- but it never runs in the live crawl pipeline: grep confirms zero call sites pass description= to Matcher.match() anywhere in pflege_jobs/cli.py's _process_rows (both the jobposting and observation branches), even though the observation dict already carries o['description'] at that point. Verified live: replaying the real Matcher with description=o.get('description') wired in recovers 78 of the 303 unmatched jobs.smartrecruiters.com rows via R_jd_text. But the same replay against all 1,717 currently-matched jobposting rows in data/inbox.sqlite shows 5 flip to a DIFFERENT clinic_id, and tracing them found a real, systemic false-positive mode: _match_jd requires only that a candidate's post-town-stripped name/operator token SET is a literal substring of the description, with no minimum-evidence gate -- when that set reduces to ONE generic word after STOP-word removal, it spuriously 'uniquely' matches any posting mentioning that word in passing. Concretely reproduced: clinic 16235 'Artemed Fachklinik München' reduces to {'artemed'} once 'Fachklinik'/'München' are stripped, so it wins R_jd_text on 4 different Artemed sibling-site postings (Tutzing, Berg x2) whose real employer is a completely different site, purely because their description mentions the parent group 'Artemed' in passing; clinic 16307 'Augenklinik Rosenheim' reduces to {'augenklinik'} (the generic German word for 'eye clinic'), so inbox_id 12124 (a Bergman Clinics Aschaffenburg/Bremen posting, city Aschaffenburg) flips to it purely because its own title says 'Augenklinik Bremen'; inbox_id 2752 (ATOS Starmed Klinik, München) flips to 'München Klinik Bogenhausen' purely because its description says the ATOS clinic sits 'im Stadtteil Bogenhausen' -- a Munich district name, not the employer. This is the exact same failure shape TASK-51/decision-5 already found and fixed for R3/R4 (an unguarded single-leftover-token fallback let a Hesse clinic false-match a Bavaria one) -- _match_jd never received the equivalent hardening because it was never live, so nobody hit it until this replay.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 _match_jd gains a minimum-evidence gate before returning a match, mirroring the len(et)<=1 lesson from decision-5/TASK-51 (e.g. a minimum real-token count across name+operator combined, and/or refusing a token set that collides with another registry clinic's own name/operator)
- [ ] #2 The reproduced false positives no longer occur once description is wired in: inbox_id 2752 does not flip away from ATOS Starmed Klinik (16258), inbox_id 12124 stays unmatched rather than landing on Augenklinik Rosenheim (16307), and the 3 Artemed sibling-site flips (8177, 8585, 8587) stay on their board-matched clinic
- [ ] #3 description=o.get('description') is wired into both pflege_jobs/cli.py _process_rows Matcher.match() call sites
- [ ] #4 A full replay of all currently clinic_id-matched postings (not just the 5 already found) shows zero unintended clinic_id changes after the gate is added
- [ ] #5 Red-green test against real stored postings plus a mutation test; report the real post-gate recovery count across the 463 unmatched inbox rows (78 is only the naive, unguarded upper bound)
<!-- AC:END -->
