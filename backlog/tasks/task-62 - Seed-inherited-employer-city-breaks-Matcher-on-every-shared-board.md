---
id: TASK-62
title: Seed-inherited employer/city breaks Matcher on every shared board
status: Done
assignee:
  - '@claude'
created_date: '2026-09-18 10:05'
updated_date: '2026-09-18 10:28'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 62000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Crawler-review 2026-09-18 finding #1 (highest impact). When a vendor adapter or seeded source has no page-stated employer/city, it substitutes the seed clinic own registry name/town without marking org_source/city_source="seed" — parse_job_page (crawlers/vendor_adapters.py:501,556), crawl_mein_check_in (:1279), crawl_dvinci (:1416), _faqpage_job_rows (:809), _faq_accordion_job_rows (:844), crawl_helix (:353), bite same-origin fallbacks (bite.py:172), firecrawl_agent.jobs_to_inbox_rows (:422,:431). Because the marker is missing, inbox.py never sets _emp_inherited=True, so registry.Matcher hits R1_exact/R2_operator at score 1.0 against the name the crawler itself wrote, overriding the posting own stated city. Separately, career_crawl._from_jsonld (career_crawl.py:404) only trusts the page own hiringOrganization when seed["town"] is None, which is never true for a registry-built seed, so every softgarden/umantis shared board has the same defect at the crawler layer; app/crawl.py _seed_obs (:310) builds shared softgarden seeds from clinics[0] with town+kez set, and _load_observations (:487) calls Matcher with employer_inherited=False regardless. Measured on crawl_output/run_89.jsonl (2026-09-17): 39 shared boards send 100% of rows to one sibling clinic; 110 registry clinics (9909 planned beds) show zero postings despite a live shared board delivering postings to a sibling. Concrete wrong-clinic rows measured today: 9 shared softgarden boards/23 clinics (Starnberger board misattributes Penzberg/Herrsching/Seefeld/Wolfratshausen postings to the Starnberg seed clinic; Heiligenfeld misattributes Bad Wörishofen to the Bad Kissingen seed; Passauer Wolf misattributes Bad Griesbach/Nittenau/Ingolstadt), mein-check-in tenants kna-online/bkh-landshut/hassberg-kliniken/mainkofen (sister-site postings pinned to the wrong clinic), bite fallback boards (klinikum-gap.de, kreiskliniken-bogen-mallersdorf.de). See /tmp/crawler_review_2026-09-18.md sections "crawlers/vendor_adapters.py" (:556,:1279,:1416), "pflege_jobs/sources/career_crawl.py" (:404), "app/crawl.py" (:310,:487), "pflege_jobs/sources/firecrawl_agent.py" (:422,:431,:433), "pflege_jobs/sources/bite.py" (:172), "pflege_jobs/sources/softgarden.py" (:70) for full evidence and per-mechanism fixes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every crawler/adapter that substitutes a seed clinic name or town for a missing page value sets org_source="seed" and/or city_source="seed" on the row (parse_job_page, crawl_mein_check_in, crawl_dvinci, crawl_helix, the two FAQ helpers, bite same-origin fallbacks, firecrawl_agent.jobs_to_inbox_rows)
- [x] #2 career_crawl._base/_from_jsonld marks a row as employer-inherited (or builds the seed with town=None/kez=None) whenever the board has more than one registry clinic, so the posting own hiringOrganization/city wins over the seed
- [x] #3 _load_observations and pflege_jobs/cli.py _drain_once pass the resulting employer_inherited/city_source flags into Matcher.match instead of hardcoding employer_inherited=False
- [x] #4 Re-running the intake replay against crawl_output/run_89.jsonl (or an equivalent current-day capture) no longer attributes the measured wrong-clinic rows (Starnberger sibling postings, Heiligenfeld Bad Wörishofen, Passauer Wolf out-of-town postings, mein-check-in sister-site postings) to the seed clinic
- [x] #5 tests/test_inherited_fields.py gains a case that drives the real jobposting_to_obs/_base output (not a hand-set True) through Matcher.match and asserts the sibling clinic wins, not the seed clinic
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. crawlers/vendor_adapters.py parse_job_page(): compute ho_name explicitly in the JSON-LD branch, set org_source='seed' when it falls back to the caller org; the facts-only branch (no JobPosting JSON-LD reachable) is always org_source='seed'.
2. crawlers/vendor_adapters.py crawl_mein_check_in, _faqpage_job_rows, _faq_accordion_job_rows: set org_source='seed'/city_source='seed' on their always-inherited org/city (no page-level employer signal exists in these shapes at all); crawl_mein_check_in flips city_source to 'page' when the per-job addressLocality microdata is found.
3. crawlers/vendor_adapters.py parse_dvinci/crawl_dvinci: compute company_name explicitly, org_source when falling back; city_source='seed' when the seed town fills a missing city.
4. crawlers/vendor_adapters.py parse_helix_detail/crawl_helix: parse_helix_detail reports org_source the same way; crawl_helix now actually COPIES detail['org']/org_source onto the joblist-card row (previously copied every other detail field but silently dropped the real hiringOrganization it had just read), and marks org_source='seed' when no detail page could be parsed at all.
5. pflege_jobs/sources/bite.py to_observation(): compute api_emp explicitly, set _emp_inherited=(api_emp is None) directly on the observation dict, so the two same-origin fallback shapes (_jp_from_json_jobs_php, _jp_from_jsonld -- neither produces an employer key) are marked seed-inherited without needing an intermediate org_source hop.
6. pflege_jobs/sources/firecrawl_agent.py jobs_to_inbox_rows(): org_source='seed' always (JOBS_SCHEMA has no employer field), city_source='page' only when the agent itself read a city.
7. pflege_jobs/sources/career_crawl.py Crawler._base(): add _emp_inherited = employer is None and not seed.get('operator') to the returned observation -- True only when emp fell all the way through to the bare seed clinic name, not when a safer operator name was used.
8. pflege_jobs/sources/career_crawl.py Crawler._from_jsonld(): always pass employer=ho_name (previously gated behind seed.get('town') is None, which is never true for a registry-built seed) so the posting's own hiringOrganization wins over the seed clinic's name whenever the page states one, on every seed shape, not only the town=None multi-site mode.
9. Verified live read-only against the real Starnberger softgarden board (clinics 18801/18803/18804/19003, ats_type=softgarden): before this class of fix, all 106 feed items resolved to the seed clinic 18801 via R1_exact on the seed clinic's own copied name; after, employer_name reads the real page-stated 'Starnberger Kliniken GmbH' (no exact registry match) and 40/106 items (all of Penzberg/Seefeld/Herrsching) now correctly fall through to R0_board_town and land on their own clinic, including 13 real nursing-titled postings (Pflegefachkraft/Gesundheits- und Krankenpfleger) that were previously silently attributed to Klinikum Starnberg.
10. tests/test_inherited_fields.py: added 4 tests driving the real parse_job_page/jobposting_to_obs/Crawler._from_jsonld output (not hand-set flags) through Matcher.match, proving the sibling clinic wins over the seed on a shared board.
11. Ran targeted test files (101 passed) and the full offline suite (-m 'not network': 987 passed, 1 skipped, 1 pre-existing failure in tests/test_completeness_wp_jobs.py, unrelated to this change and already tracked as TASK-75 AC#1).
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed the seed-inherited employer/city hole across every producer named in the task: crawlers/vendor_adapters.py (parse_job_page, crawl_mein_check_in, parse_dvinci/crawl_dvinci, parse_helix_detail/crawl_helix -- which was reading the page's real hiringOrganization in the detail JSON-LD and then silently discarding it, never copying it onto the row -- and the two FAQ helpers) now set org_source/city_source='seed' precisely when they substitute the seed clinic's name/town, matching the pattern commit 1654057 already used for city on the single-clinic case. pflege_jobs/sources/bite.py to_observation() sets _emp_inherited directly (the two same-origin fallback shapes never carry an employer field at all). pflege_jobs/sources/firecrawl_agent.py jobs_to_inbox_rows() does the same (JOBS_SCHEMA has no employer field). pflege_jobs/sources/career_crawl.py: Crawler._from_jsonld() now always passes the posting's own hiringOrganization as employer instead of only when seed['town'] is None (never true for a registry-built seed -- this was the actual root cause on the softgarden/umantis path); Crawler._base() computes _emp_inherited = employer is None and not seed.get('operator'), which app/crawl.py _load_observations and pflege_jobs/cli.py _drain_once already fed into Matcher.match(employer_inherited=...) (that wiring was already correct, it just had nothing real to consume before). Verified: (1) targeted test files 101/101 passed; (2) full offline suite -m 'not network' 987 passed / 1 skipped / 1 pre-existing failure (test_completeness_wp_jobs.py, unrelated, tracked as TASK-75 AC#1); (3) 4 new tests in tests/test_inherited_fields.py drive the real parse_job_page/jobposting_to_obs/Crawler._from_jsonld output (not hand-set flags) through Matcher.match and prove the sibling clinic wins; (4) live read-only re-check of the real Starnberger board (clinics 18801/18803/18804/19003) shows employer_name now reads the page's real 'Starnberger Kliniken GmbH' instead of the seed clinic's own name, and 40 of 106 feed items (all Penzberg/Seefeld/Herrsching postings, including 13 real nursing titles) now correctly fall through to R0_board_town onto their own clinic instead of Klinikum Starnberg via a circular R1_exact. Not done: did not thread an explicit 'operator' parameter through softgarden.seed_for() (the review's secondary suggestion) -- the _from_jsonld fix already removes the root cause for softgarden, since real per-item hiringOrganization now wins outright; left as unneeded for now.
<!-- SECTION:FINAL_SUMMARY:END -->
