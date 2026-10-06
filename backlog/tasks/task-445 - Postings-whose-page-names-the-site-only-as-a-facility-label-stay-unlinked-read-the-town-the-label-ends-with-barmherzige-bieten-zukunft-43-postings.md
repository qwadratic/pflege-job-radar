---
id: TASK-445
title: >-
  Postings whose page names the site only as a facility label stay unlinked:
  read the town the label ends with (barmherzige-bieten-zukunft, 43 postings)
status: To Do
assignee: []
created_date: '2026-10-06 19:42'
labels:
  - crawler-coverage
  - adapter-testing
dependencies: []
priority: low
ordinal: 324000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the TASK-431.9 place work (2026-10-06, pflege-clawl, executor result). On barmherzige-bieten-zukunft.de the posting page gives its place as a facility name, not a town ('Krankenhaus Barmherzige Brueder Regensburg', 5 such employers, 43 postings stored, example in the dry-run file unlinked_place_is_facility_label.csv). The matcher finds no registry clinic by that text, so the postings stay unlinked with the facility label as their city: a missing link, not a wrong one. The same shape was fixed for dvinci and umantis in fix/place-links (geo.town_label_ends_with: the longest trailing run of words of the label that is a registry town). Related and not duplicate: TASK-36 (adapter red-green kbo + barmherzige) and TASK-431.9 (place confidence). Ivan 2026-10-06: a plain task for now, no plan.
<!-- SECTION:DESCRIPTION:END -->
