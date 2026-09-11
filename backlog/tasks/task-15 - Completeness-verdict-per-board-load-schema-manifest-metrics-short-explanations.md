---
id: TASK-15
title: >-
  Completeness verdict per board load: schema, manifest, metrics, short
  explanations
status: To Do
assignee: []
created_date: '2026-09-09 11:35'
labels:
  - harvester
dependencies: []
ordinal: 15000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan wants every crawl to end by answering 'why is this the end?'. Each board load must record which completeness proof it used and whether it held: api_total (the API reports a total and we fetched that many), listing_total (the listing page states N Stellen), pagination_end (the next link disappeared or a page returned nothing new), sitemap_count (job URLs in the sitemap equal the pages fetched), single_page (an all-in-one listing such as umantis /Jobs/All). Known hard cases to design for: filter-only boards where the listing is a set of sidebar groups and completeness is the union over all groups (mein-check-in), group boards with a per-site filter (kbo jobSite), infinite scroll with no pagination signal (only an API total or scroll-until-nothing-new works), sitemaps that omit postings (München Klinik). The verdict lives next to the mirror's manifest.jsonl for the load and in harvest_report. Ivan asked for metrics plus short explanations: a per-load figure such as 'criteria met on 83% of boards' and, for every board that did not meet its criterion, two sentences saying why.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each board load records completeness method, expected count, obtained count, page list and a verdict in the snapshot manifest and in harvest_report
- [ ] #2 A load summary states the share of boards whose completeness criterion held, and every board below the bar carries a two-sentence explanation
- [ ] #3 A cap-stopped load can never receive the verdict complete
<!-- AC:END -->
