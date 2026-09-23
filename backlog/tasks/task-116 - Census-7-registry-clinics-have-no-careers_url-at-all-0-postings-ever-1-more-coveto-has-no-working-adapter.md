---
id: TASK-116
title: >-
  Census: 7 registry clinics have no careers_url at all (0 postings ever); 1
  more (coveto) has no working adapter
status: To Do
assignee: []
created_date: '2026-09-22 18:27'
labels: []
dependencies: []
ordinal: 116000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-22 never-posted census: compared the full live 407-row pflege_jobs.clinics registry (rest_get, clinic_id/careers_url/ats_type) against every distinct clinic_id that has EVER appeared in pflege_jobs.v_postings (all statuses, no status filter -- 264 distinct clinic_id out of 407). 143 clinic_ids have zero postings across all of history. Running crawlers.routing.plan() on the live registry against that 143-clinic set: 7 clinics have careers_url == '' (crawlers/routing.py:141 'no careers_url' unroutable reason) -- 26105 Krankenhaus Landshut-Achdorf, 27773 AMEOS Klinikum Inntal Simbach am Inn, 37102 St. Johannes-Klinik Auerbach, 56406 Cnopf'sche Kinderklinik Nürnberg, 67402 Haßberg-Kliniken Haus Ebern, 67802 Krankenhaus Markt Werneck, and 17274 Klinik für Schlafstörungen (Bad Reichenhall). One more, 16262 Algesiologikum Tagesklinik München, has careers_url but ats_type='coveto', which has no entry in routing.py's ADAPTERS table (crawlers/routing.py:150 'no adapter for coveto'). Two of these 8 are already explained, not gaps: 17274 and 16262 were each individually checked by the firecrawl_agent hunter this session (data/app.sqlite crawl_issues kind='firecrawl', run_id 122, 2026-09-22) -- 17274's operator site (schlaflabor-bayern.de) has no careers page and the public hospital directory confirms it has no listings; 16262's Coveto board (k19368.coveto.de) is live and complete with 9 current listings, none of them nursing. The other 6 are unverified: nobody has checked whether they even have a public careers page under a different URL. Separately, 23 more never-posted clinics have careers_url present but ats_type='' -- these are NOT stuck (crawlers/routing.py:143-149 already defaults any url-but-no-vendor-label board to the generic wp_jobs crawler), so they are lower priority; listed here so a census run that fingerprints one of them doesn't have to re-derive that they were already checked. This is investigation-only census data, not a mass-implementation task -- do not fix all 399 clinics in this task; see AC for scope.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each of the 6 unverified no-careers_url clinics (26105, 27773, 37102, 56406, 67402, 67802) gets one manual check: does the operator have a public careers/Stellenangebote page at all; if yes, add careers_url (+ ats_type if fingerprinted) to pflege_jobs.clinics via the registry correction tool (tools/apply_registry_corrections.py pattern), if no, record that finding (same shape as the existing 17274/16262 firecrawl verdicts) so future census runs don't re-check it
- [ ] #2 16262's ats_type='coveto' either gets a routing.py ADAPTERS entry (if Coveto boards turn out common enough to be worth an adapter) or gets documented as a deliberately-unsupported vendor with a comment explaining why, so 'no adapter for coveto' stops looking like an open question
- [ ] #3 The 23 has-url-no-ats_type clinics are left alone by this task (already routed via wp_jobs fallback) -- just recorded here as context, no action required
- [ ] #4 Report which of the 6 unverified clinics turned out to genuinely have zero public job postings (small/specialty clinics, same shape as 17274/16262) vs which needed a real registry fix
<!-- AC:END -->
