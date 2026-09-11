---
id: TASK-13
title: Russian language for the site
status: To Do
assignee: []
created_date: '2026-09-09 11:12'
labels:
  - frontend
dependencies: []
ordinal: 13000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan asked for Russian on the site (2026-09-09), alongside German. The light frontend at / and the operator dashboard at /pro are single-page apps built from web/*.template.html by web/build.py; strings are currently inline German. Needs a language switch that persists, translated UI strings for both SPAs, and no change to the German default for existing visitors. Posting content stays in the source language; only the interface is translated.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A language switch on / and /pro toggles the interface between German and Russian and persists across reloads
- [ ] #2 German remains the default for visitors who have not chosen a language
- [ ] #3 Posting titles and descriptions are not translated, only interface strings
<!-- AC:END -->
