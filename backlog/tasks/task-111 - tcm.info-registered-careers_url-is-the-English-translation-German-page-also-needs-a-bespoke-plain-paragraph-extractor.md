---
id: TASK-111
title: >-
  tcm.info registered careers_url is the English translation; German page also
  needs a bespoke plain-paragraph extractor
status: To Do
assignee: []
created_date: '2026-09-22 17:12'
labels: []
dependencies: []
ordinal: 111000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Run 120 (2026-09-22) zero-yield recon for clinic 37275 (TCM-Klinik Bad Kötzting), board https://tcm.info/en/tcm-clinic/about-the-clinic/job-offers/. Two stacked problems found live: (1) the registered URL is the ENGLISH page -- real postings are there as inline paragraph text ('Specialist in psychosomatics and psychotherapy', 'Psychologists', ...) but gender-marked English-style '(m / f / d)' -- GENDER (crawlers/vendor_adapters.py) only recognises the German letter set (m/w/d/x/i/gn), never 'f', so even a perfect extractor gates nothing through on this page. The site's own language switcher names the correct German page: https://tcm.info/tcm-klinik/ueber-die-klinik/stellenangebote-tcmk/. (2) The German page ALSO reads 0 via crawl_wp_jobs: same inline-paragraph shape (2026-09 TASK-49 fixed several of these -- klinik-menterschwaige.de, klinik-bad-trissl.de, klinik-wirsberg.de, etc.) but with NO shared class/heading/accordion wrapper at all around each posting (just <h2>Stellenangebote</h2> then raw <p> text), unlike those precedents -- not confidently fixable without a paragraph-boundary heuristic risking false positives on the surrounding clinic-description prose.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 careers_url for clinic 37275 is corrected to the German page (https://tcm.info/tcm-klinik/ueber-die-klinik/stellenangebote-tcmk/) via the registry write path (not directly -- see this repo's no-safety-nets/DB-write rules)
- [ ] #2 A bespoke extractor (or a general fix, if a reliable paragraph-boundary signal is found) reads the German page's real postings; verified live red-green against the current live page, mutation-tested
- [ ] #3 Verified: the extracted titles carry real German '(m/w/d)' markers, not the English page's unmatchable '(m / f / d)' form
<!-- AC:END -->
