---
id: TASK-104
title: A flexible or unknown department answer must not empty the shortlist
status: To Do
assignee: []
created_date: '2026-09-14 22:15'
labels: []
dependencies: []
type: bug
ordinal: 104000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Live llm run 2026-09-14 (full funnel after a campaign Ja, 1 of 4): the model wrote card department_pref="flexibel" although the candidate never named a department; market_snapshot filters the board on that word (app/wa/luna_brain.py, SL.read_department(...) or the raw word), the shortlist came back empty and Luna asked for consent without naming any clinic. "egal"/"flexibel" reproduce it offline. Earlier runs also copied a department from Luna own tool result into department_pref. Ivan 2026-09-14: fix it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 a flexible answer (egal, flexibel, alles, offen, keine Präferenz and similar) settles the city/department gate but applies no department filter, so the shortlist is built from the other criteria
- [ ] #2 a department word the board vocabulary does not know never silently yields an empty shortlist: the snapshot says which filter was applied or not matched, so Luna can be honest about it
- [ ] #3 the prompt lets department_pref come only from the candidate own words naming a department, never from a tool result or an example Luna gave
- [ ] #4 offline tests cover flexible words, unknown words and alias words; the llm full-funnel persona after a campaign Ja reaches a non-empty shortlist in repeated runs
<!-- AC:END -->
