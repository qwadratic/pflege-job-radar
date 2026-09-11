---
id: TASK-55
title: >-
  bite adapter: newer 'loader-v1' embed generation has no embedded API key (v5,
  niiid)
status: To Do
assignee: []
created_date: '2026-09-11 12:38'
updated_date: '2026-09-11 15:38'
labels: []
dependencies: []
ordinal: 55000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Discovered while triaging TASK-48 (Benedictus Krankenhaus Feldafing, clinic_id 18813, 70 beds, careers_url https://www.klinik-feldafing.de/karriere/stellenangebote). pflege_jobs/sources/bite.py's detect()/api_key() correctly recognize the newer data-bite-jobs-api-listing="{customer}:{listing}" embed (here: customer=artemed-8, listing=niiid) and correctly fetch https://cs-assets.b-ite.com/artemed-8/jobs-api/niiid.min.js -- but unlike the older bite embed generation this docstring documents (bundle embeds key:"<40 hex>"), this bundle calls t.createClient({key:""}) with an EMPTY key. It resolves a v5 API base (https://static.b-ite.com/jobs-api/v5/api-v5.js) via window.__$BiteJobsApiLoaderV1$__ at runtime instead -- the real per-tenant credential/endpoint is not present anywhere in the static HTML or the two JS bundles fetched so far; finding it needs either reading further into the v5 api-v5.js bundle for its own data-fetch endpoint convention, or a Playwright probe of what network calls the loader actually makes once it runs.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Real v5 API endpoint (or confirmation there is none without a browser) documented for the niiid/loader-v1 bite generation
- [ ] #2 Benedictus Krankenhaus Feldafing's real postings (if any) reachable via this adapter, or the board correctly flagged as needing Playwright
- [ ] #3 Check whether other Artemed-group clinics share this same newer bite embed (the group also runs a separate smartrecruiters board for the same clinics, company_code ArtemedSE -- confirm the two aren't just duplicate postings of the same jobs before adding a second read path)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-11 further investigation: fully reverse-engineered the v5 API's real endpoint and request
shape (useful for any OTHER bite tenant on this same generation with a real key, not just Feldafing):
  POST https://jobs.b-ite.com/api/v1/postings/search
  body: {"key": "<the tenant's real key>", "filter": {...}}  (JSON)
  (also GET https://jobs.b-ite.de/api/v1/address-autocomplete for the location-filter UI, irrelevant
  to scraping)
Confirmed live: calling this with key="" (exactly what the niiid.min.js bundle hardcodes for
artemed-8/Feldafing) returns 400 {"error":"API key is missing"} -- the empty key is not a bug in our
reading of the bundle, the real API genuinely rejects it.

This means either: (a) the "niiid" listing type is not a standard job-search widget at all -- static.b-ite.com/niiid/v1
references a chatbot/"recruiting-assistant" product (dot.niiid.io, BiteChatbotV1) distinct from the
postingSearch API, so this specific Feldafing listing may be chatbot-driven jobs discovery with no
REST search surface at all; or (b) the real key is resolved by a runtime JS call this static analysis
didn't find (would need Playwright to observe the actual network traffic once the loader executes).
Not resolved further this session -- genuinely needs either Playwright or confirmation from bite that
this listing type has no scrapeable API.
<!-- SECTION:NOTES:END -->
