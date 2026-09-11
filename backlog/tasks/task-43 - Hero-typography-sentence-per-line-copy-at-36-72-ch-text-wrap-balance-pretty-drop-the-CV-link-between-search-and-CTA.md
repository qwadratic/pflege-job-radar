---
id: TASK-43
title: >-
  Hero typography: sentence-per-line copy at 36/72 ch, text-wrap balance/pretty,
  drop the CV link between search and CTA
status: To Do
assignee: []
created_date: '2026-09-10 12:35'
labels:
  - frontend
  - copy
dependencies: []
ordinal: 43000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Measured 2026-09-10 (headless Chromium, DE, per-line widths): at 768-1366 px the hero lead wraps 513/369/504/279 px (long-short-long-short) and the "Gerade im Index" board row wraps 576/576/72 px (one-word orphan); at 412 px the lead is six lines ending in an 81 px line; the hero-rest puts a "Lebenslauf hochladen" link between the search box and the CTA. Ivan's complaint: hanging words, centred blocks with long and short lines, blocks placed between texts.

Body font is JetBrains Mono (15 px = 9 px/char, 13 px = 7.8 px/char), so the lead measure is 36 ch on a 360 phone and 60 ch on desktop, the board row 42/73 ch. The fix is copy length plus CSS, not a layout rewrite.

Final strings (both languages, char counts checked):
- hero_lead as three block spans, one sentence each:
  DE: Jede Klinik im Krankenhausplan. | Jede Stelle für Pflegefachkräfte. | Vom Karriereportal, mit Nachweis.
  EN: Every hospital in the state plan. | Every job for registered nurses. | From the career portal, with proof.
- hb_idx (board row, two sentences, count from /api/stats and last_crawl time):
  DE: {open_jobs} Stellen an {clinics} Kliniken. Stand heute {HH:MM}.
  EN: {open_jobs} jobs at {clinics} hospitals. Updated today {HH:MM}.
- hb_title: delete (it explained the animation).
- hb_cv: DE: Kein Ort dabei? Lebenslauf hochladen. Wir prüfen jede Stelle für dich.  EN: No town that fits? Upload your CV. We check every open job for you.
- facet rows: drop "offene": "{n} Stellen {phrase}" / "{n} jobs {phrase}".
- cv_link paragraph in #hero-rest: remove (duplicate of hb_cv; control between text and CTA).
- KPI 4 (k_cities "Orte"): replace with freshness "Stand heute {HH:MM}" / "Updated today {HH:MM}".

CSS: .hero h1{text-wrap:balance} .hero h1 .flap{font-size:.86em} .hero .lead{max-width:36ch;text-wrap:pretty} .hero .lead span{display:block} .board{grid-template-columns:minmax(0,72ch)}.

References measured the same day: mercury.com (h1 one line, lead two lines, balance+pretty, centred), vercel.com (h1 balance), stripe.com/de (text-wrap:pretty, left), linear.app (left-aligned). Full review with screenshots was hosted at /skill/reviews/copy/ (deleted end of day); rules persist in .claude/agents/copy-lead.md module M2 and the rag-check agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 At 360, 412, 768, 1024, 1366 and 1920 px no line of .hero h1, .hero .lead or the visible .board row is shorter than 50% of the longest line in the same block, unless that line is a complete sentence (verify with the rag-check agent or the same Playwright measurement)
- [ ] #2 DE and EN hero_lead render as exactly three lines at every width >= 360 px, each <= 36 ch, and the strings match the task description
- [ ] #3 The visible board row shows count, clinics and last-crawl time from /api/stats in one desktop line and two phone lines; hb_title string and the hero-rest cv_link paragraph are gone from both templates
- [ ] #4 node --check passes on every extracted <script> block of web/index.template.html after the edit and web/build.py rebuilds without diff noise outside the changed strings
<!-- AC:END -->
