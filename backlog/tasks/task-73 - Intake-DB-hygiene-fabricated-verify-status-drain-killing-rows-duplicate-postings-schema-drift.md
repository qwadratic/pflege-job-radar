---
id: TASK-73
title: >-
  Intake/DB hygiene: fabricated verify status, drain-killing rows, duplicate
  postings, schema drift
status: Done
assignee:
  - '@ivan'
created_date: '2026-09-18 10:10'
updated_date: '2026-09-18 18:33'
labels: []
dependencies: []
priority: medium
type: bug
ordinal: 73000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Crawler-review 2026-09-18, mixed intake and DB-lifecycle defects. (1) pflege_jobs/cli.py _drain_once (:345) stamps verify_status=live/verify_http=200/verify_note="collected in a users browser" on every observation it writes, including vendor-adapter and Firecrawl-agent rows whose URL was never requested -- 248 observations in the 2026-09-17 03:00 run alone got this fabricated mark. (2) The ats_discovery probe branch (cli.py:325) interpolates the payload clinic_id directly into the PostgREST query string instead of using params= like lookup_posting_ids does; _rows turns a resulting 400 into SystemExit inside the row loop before any ack, so one poison row (clinic_id containing "&") permanently wedges intake -- every later run re-reads and re-dies on the same row. (3) cli.py:351 posts the whole probes list in one raw sink._post({"clinics":...}) with no 500-row batching and no clinic_id dedupe; any non-200 raises before the inbox_ack at :352, so the page is never acked and re-fails forever. (4) cmd_link_cross (cli.py:243) used set guards only the merge source, so a posting already merged away can still be picked as a later pairs destination -- the resulting chain is POSTed in one merges call and the edge op single CTE moving observations while deleting the intermediate posting hits a posting_observations FK violation, aborting the whole batch; a concrete chain shape already exists at clinic 77402 (Klinik Krumbach/Kreiskliniken Guenzburg-Krumbach). (5) Two URL aliases of one ATS job seen by the same source (softgarden vanity domain vs *.softgarden.io, personio .de vs .com, jobs. vs api.smartrecruiters.com) become two permanently-open postings because same_source_variant_pairs (cli.py:180) skips same-source pairs and canonical_ref (cli.py:177) keeps the differing netloc -- 175 fuzzy_key groups have more than one open posting today, 99 of them proven duplicates (218 open postings). (6) sql/001_schema.sql resolve_postings() (:208) no longer matches the deployed function -- it sets status=open for every posting with an observation and never assigns department_raw/enr_* fields; if ever replayed it would reopen all 202 currently-expired postings. (7) mark_expired()/expire_days is unreachable from any scheduled path (only caller orchestrate.py:78 is scheduled by nothing) yet is still published as a runbook curl in skill/references/pipeline.md and web/skill/pflege-jobs.skill.md; firing it would expire 564 of 2678 open postings (21%) that verify live but have last_seen>7 days, because last_seen freezes at first sighting (app/crawl.py:444 drops every re-crawled URL already present in the inbox, so no fresh observation ever gets upserted for an unchanged posting). (8) parse_job_page (vendor_adapters.py:505) copies a JSON-LD epoch placeholder datePosted="1970-01-01" verbatim; app/data.py _fresh() (:215) then never counts that posting as fresh, permanently -- 11 open postings today (9 kbo.de, 2 frg-kliniken.de). (9) app/data.py cities() (:550) aggregates jobs_open/jobs_fresh per clinic-registry-town only and skips every posting without a clinic_id, so GET /api/cities contradicts GET /api/jobs?city= -- 198 unattributed postings appear in no city row, and towns where the posting own city differs from its clinic town are undercounted (Augsburg shows 57 vs 148 postings that actually name it). (10) JOB_PATH /(karriere-)?detail/ (vendor_adapters.py:432) matches TYPO3 news-archive URLs; 1545 rows/run are CMS news pages, 31 surviving the role filter as fake nursing postings on klinikum-memmingen.de. (11) crawl_wp_jobs / _job_link_pairs follow off-host links with no same-site check, stamping a sibling site posting with the crawled clinic own name -- confirmed on psychiatrie-werneck.de fetching 8 rows from koenig-ludwig-haus.de. (12) bite _jp_from_json_jobs_php (bite.py:255) builds the posting address from the tenant HQ only, discarding the per-ad job_site field -- klinikum-gap.de Murnau postings stored under Garmisch-Partenkirchen HQ address. (13) klinikum_passau _parse() (:53) bounds each posting with a fixed html[start:start+8000] slice instead of the next JOB_START_RX match; already 3 of 17 live postings exceed the window, corrupting description/enr_* fields or bleeding into the next postings date. (14) verify.py city-mismatch detection is noisy: extract_location returns the first of several distinct JSON-LD jobLocation entries (verify.py:177, wrong-site attribution on multi-site boards like karriere.ge-passau.de), _clean_city does not split a leading PLZ out of addressLocality (verify.py:181, inverts the mismatch signal on jobs.klinikum-gap.de), and _EINSATZORT bare-Standort matching (verify.py:90) picks up site-directory prose ("zum Standort Bremen") as if it were the posting own location, producing ~110 false city mismatches per daily verify pass. See /tmp/crawler_review_2026-09-18.md for full per-item evidence (files: pflege_jobs/cli.py, pflege_jobs/sinks.py, sql/001_schema.sql, app/data.py, crawlers/vendor_adapters.py, pflege_jobs/sources/bite.py, pflege_jobs/sources/klinikum_passau.py, pflege_jobs/verify.py).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The inbox drain only stamps verify_status=live/200 for rows whose collector really is the browser collector; adapter and Firecrawl-agent rows get their status from _verify_ids/the daily verify instead of a fabricated mark
- [x] #2 The ats_discovery probe branch in cli.py uses params= (not string interpolation) for its PostgREST filter, and is wrapped so a bad row is acked with an error note instead of raising SystemExit and wedging the drain; the probes post at cli.py:351 is batched the same way every other EdgeSink caller batches (e.g. chunks of 400)
- [x] #3 cmd_link_cross collapses merge chains (rewriting a pairs dst to its own dst repeatedly, or skipping any pair whose dst also appears as a src) before posting, so one merges call never deletes a posting that is another pairs destination
- [x] #4 same_source_variant_pairs folds a vanity-domain alias into its ATS-host twin when they share a fuzzy_key and the same trailing numeric job id from source_ref, closing the 99 proven-duplicate groups measured today
- [x] #5 sql/001_schema.sql is regenerated from the actually-deployed resolve_postings function (dropping the status=open assignment, adding the enr_*/department_raw assignments), or split into a create-only DDL file separate from a current-functions file so "apply the schema" is not silently destructive
- [x] #6 mark_expired/expire_days is either removed as dead code (including its runbook mentions in skill/references/pipeline.md and web/skill/pflege-jobs.skill.md) or last_seen is made meaningful (re-upserted on every crawl) before it is scheduled anywhere
- [x] #7 parse_job_page (and the sibling datePosted readers) treat an implausible date (before year 2000) as None instead of passing it through, so the epoch-dated kbo.de/frg-kliniken.de postings count as fresh via first_seen
- [x] #8 app/data.py cities() derives jobs_open/jobs_fresh from jobs() keyed on the posting own city/clinic_town (the same OR filter_jobs already uses) instead of the clinic-registry aggregate alone, so unattributed postings and city/town mismatches no longer disappear from GET /api/cities
- [x] #9 NOT_JOB_PATH excludes TYPO3 news/press/blog/event paths so /aktuelles/detail/ no longer passes as a job detail page; the 31 fake nursing postings on klinikum-memmingen.de no longer appear
- [x] #10 _job_link_pairs/_widget_endpoint_job_links restrict followed links to the boards own registrable domain unless reached via the boards own redirect, closing the psychiatrie-werneck.de/koenig-ludwig-haus.de leak
- [x] #11 bite _jp_from_json_jobs_php prefers the per-ad job_site field for address.city when present, over the tenant HQ address
- [x] #12 klinikum_passau._parse bounds each posting block by the next JOB_START_RX match instead of a fixed 8000-char window
- [x] #13 verify.extract_location rejects a JSON-LD jobLocation list with more than one distinct (city, plz) rather than silently taking the first, strips a leading 5-digit PLZ out of addressLocality before storing it as city, and _EINSATZORT requires either a separator or excludes the common site-directory idioms (zum/zur/unsere/weitere/alle/andere Standort) so daily city-mismatch noise drops substantially
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. AC1 cli.py _drain_once: stop fabricating verify_status=live/200 for every inbox row -- no collector in this codebase is a real browser fetch of the stored URL. Remove the `ver` build/post; rows stay verify_status=NULL until _verify_ids (crawl run) or the daily `cli verify` pass checks them for real.
2. AC2 cli.py ats_discovery probe branch: switch the clinics lookup to params= (like lookup_posting_ids), catch a non-200/parse failure per-row and ack with an error note instead of letting SystemExit escape mid-loop; batch the probes POST at cli.py:351 in chunks of 400 like every other EdgeSink caller.
3. AC3 cmd_link_cross: after computing same-source pairs, collapse chains (follow dst->dst repeatedly) and drop any pair whose dst is itself some other pair's src, so one merges POST never asks the edge function to delete a posting that is also a destination.
4. AC4 same_source_variant_pairs: also fold vanity-domain/ATS-host aliases (softgarden vanity vs *.softgarden.io, personio .de/.com, jobs./api.smartrecruiters.com) sharing a fuzzy_key and trailing numeric job id, in addition to same-source pairs.
5. AC5 sql/001_schema.sql resolve_postings(): drop the unconditional status='open' assignment (per AC text) and add the department_raw/enr_pay_grade/enr_pay_text/enr_requirements/enr_experience column defs + UPDATE assignments that pflege_jobs/schema.py (OBS_SPEC) and edge/pflege-ingest/index.ts already carry -- verified offline by diff against those two files (no DB access available, no Supabase token per memory).
6. AC6 mark_expired/expire_days: remove as dead code (function + orchestrate.py caller + runbook mentions in skill/references/pipeline.md, web/skill/pflege-jobs.skill.md) since last_seen never refreshes on a re-crawled unchanged posting (app/crawl.py dedupe) -- verify that claim first before deciding remove vs fix last_seen.
7. AC7 vendor_adapters.py parse_job_page + sibling datePosted readers: treat a pre-2000 date as None.
8. AC8 app/data.py cities(): derive jobs_open/jobs_fresh from jobs() filtered the same way filter_jobs' city OR does (own city OR clinic town), not from the clinic-registry aggregate alone.
9. AC9 vendor_adapters.py NOT_JOB_PATH: exclude TYPO3 news/press/blog/event path segments.
10. AC10 vendor_adapters.py _job_link_pairs/_widget_endpoint_job_links: restrict followed links to the board's own registrable domain unless via the board's own redirect.
11. AC11 bite.py _jp_from_json_jobs_php: prefer the per-ad job_site field for address.city over tenant HQ address.
12. AC12 klinikum_passau.py _parse: bound each posting block by the next JOB_START_RX match, not a fixed 8000-char window.
13. AC13 verify.py: extract_location rejects >1 distinct (city,plz) jobLocation entries, _clean_city strips a leading 5-digit PLZ, _EINSATZORT excludes site-directory idioms (zum/zur/unsere/weitere/alle/andere Standort).
Work file-by-file, re-reading each file immediately before editing (sibling TASK-62..72/74 subagents are editing the same working tree concurrently). Add/extend targeted tests per AC, then run the full offline suite.

14. CORRECTIVE RE-REVIEW (second session, adversarial reviewer marked not-approved with no specific list): re-check every AC against the current git diff line by line, re-run the full offline suite from scratch, fix anything that does not hold up rather than trusting the first pass's own notes.

15. THIRD PASS (this session): the reviewer gave no specific list again -- re-derive evidence from scratch (git diff + direct execution) for all 13 ACs rather than trusting either prior pass's notes; fix anything that does not independently reproduce.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC1 (cli.py _drain_once): removed the fabricated verify_status=live/verify_http=200/verify_note="collected in a user's browser" stamp entirely. Audited every collector that writes into pflege_jobs.inbox (vendor-*-v1, playwright-*-v1, firecrawl-agent, ats-discover2-v1, career-discover-exa-v1, plus agent-key labels via /api/ingest) -- none is a literal browser that fetched the stored URL, so no collector qualifies for the "really is the browser collector" carve-out the AC allows; rows now get verify_status=NULL until app/crawl.py:_verify_ids (same-run) or the next `cli verify` sweep decides for real. Evidence: tests/test_cli_inbox_probe.py::test_drain_once_never_fabricates_a_verify_stamp asserts no "verify" key is ever posted, while clinic_links/inbox_ack still are.

AC2 (cli.py probe branch): clinic_id lookup now uses params= (URL-encoded), matching lookup_posting_ids; a lookup failure (bad/poison clinic_id, non-list PostgREST response) acks the row with an error note and `continue`s instead of letting _rows() raise SystemExit mid-loop. The probes POST now goes through sinks.EdgeSink.write_clinics (reused, not reimplemented) instead of one unbounded {"clinics": [...]} call. Evidence: test_probe_branch_uses_params_and_acks_a_bad_clinic_id_instead_of_raising (asserts params dict, asserts ack note contains "failed", asserts _drain_once returns normally), test_probes_post_is_batched_via_write_clinics_not_one_unbounded_call (850 probes -> 5 posted "clinics" bodies of [200,200,200,200,50]).

AC3 (cmd_link_cross): added collapse_merge_chains(pairs) -- rewrites a dst that is itself later merged away to its own final dst, drops a genuine cycle -- applied to both same_source_variant_pairs' own pairs and cmd_link_cross pass 2's cross-source pairs (its `used` set only ever guarded the src side). Evidence: test_collapse_merge_chains_rewrites_dst_to_its_own_final_dst, test_collapse_merge_chains_drops_a_genuine_cycle, test_collapse_merge_chains_leaves_a_flat_star_unchanged. No live DB write performed (not permitted); could not re-observe clinic 77402 live.

AC4 (same_source_variant_pairs): added a second grouping by (source_id, ats-platform numeric job id via new _ats_job_id, fuzzy_key) alongside the canonical_ref grouping, covering all three named platforms (softgarden vanity-CNAME vs *.softgarden.io, personio .de/.com, smartrecruiters jobs./api. subdomain). Evidence: test_same_source_pairs_fold_softgarden_vanity_domain_into_canonical_host, test_same_source_pairs_fold_personio_de_com_twin, test_same_source_pairs_fold_smartrecruiters_jobs_vs_api_subdomain_twin (all with a fuzzy_key mismatch case proving the gate holds).

AC5 (sql/001_schema.sql resolve_postings): dropped the unconditional `status = 'open'` assignment; added department_raw/enr_pay_grade/enr_pay_text/enr_requirements/enr_experience to both table DDLs and to the UPDATE SET list. HONEST CAVEAT: no Supabase/DB access in this environment (no credentials used, per task constraints) -- I could not fetch or diff against the literal deployed function body. The fix is evidence-based: pflege_jobs/schema.py's OBS_SPEC (the project's own documented source of truth, "keep in sync with sql/001_schema.sql") and edge/pflege-ingest/index.ts (the generated, deployment-shaped column list) both already carry these 5 fields, proving they are real, currently-live columns this file was missing. New test tests/test_schema_resolve_postings.py parses the DDL+function text and pins both the missing-column/assignment drift and the destructive status='open' -- confirmed by grepping git show HEAD:sql/001_schema.sql that it would have failed against the pre-fix file (0 hits for the 5 new fields, 2 hits for "status = 'open'" pre-fix vs 0 inside resolve_postings post-fix).

AC6 (mark_expired/expire_days): removed as dead code (chosen over the "make last_seen meaningful" alternative -- last_seen's freeze is a separate, larger behavior change not requested). Verified unreachable: orchestrate.py is not scheduled (deploy/github-workflow-daily.yml's own comment: "Not committed under .github/ ... the VM app (app/scheduler.py) now runs the weekly staggered autocrawl instead"); app/scheduler.py/app/crawl.py never call it. Removed: sql/001_schema.sql's function def (replaced with `drop function if exists`, so replaying this file against a DB that still has the old function actually removes it), pflege_jobs/orchestrate.py's stage_verify caller, edge/pflege-ingest/index.template.ts's `if (body.expire_days)` handler (regenerated index.ts via `python edge/build_ingest.py`, diff shows only the intended 2-line change), and the runbook mentions in skill/references/pipeline.md + data-model.md and their web/skill/ mirrors (pipeline.md, data-model.md, pflege-jobs.skill.md -- all three, since pflege-jobs.skill.md is the concatenation of SKILL.md+references per web/build.py). NOTE (not fixed, out of scope): docs/scraping.md:17 still says "weekly expiry of unseen rows" -- that file is already being edited by a sibling task in this shared working tree; flagging rather than touching it to avoid stepping on concurrent work.

AC7 (vendor_adapters.py datePosted): added _sane_date(raw) (year<2000 -> None, everything else unchanged) and routed every datePosted/createdDate-shaped read in the file through it (parse_personio_xml, parse_smartrecruiters, parse_helix_detail x2, parse_job_page, the FAQ-card date builder, _enrich_wp_fallback_fields's 3 branches, mein-check-in's meta date, dvinci's createdDate) -- confirmed parse_job_page is the function crawl_group_portal (kbo.de) and crawl_wp_jobs both call per-job, so this one fix covers both named live examples. Evidence: test_sane_date_rejects_the_unix_epoch_placeholder, test_sane_date_keeps_a_real_date, test_sane_date_keeps_absent_and_malformed_values_as_before, test_parse_job_page_drops_an_epoch_placeholder_datepostet_instead_of_freezing_freshness, test_parse_job_page_keeps_a_real_dateposted.

AC8 (app/data.py cities()): rewritten to aggregate jobs_open/jobs_fresh from jobs() keyed on {own city, clinic_town} (case-insensitively deduped per job so a same-town-different-casing job counts once, not twice) -- the same OR filter_jobs' city filter (app/data.py:455) uses -- instead of the clinic-registry aggregate. clinics/beds/ats_known stay clinic-derived (unaffected). Evidence: tests/test_app_api.py::test_cities rewritten with 3 fixture postings (one clinic-matched, one with no clinic_id at all, one whose own city differs from its clinic's town) and asserts all three now appear correctly, including a brand-new "Augsburg" row for the unattributed posting.

AC9 (vendor_adapters.py NOT_JOB_PATH): added an alternative excluding /(aktuelles?|presse|news|blog|veranstaltung(?:en)?|termine?|events?)/(?:karriere-)?detail/ -- the TYPO3 news/press/blog/event single-record view shares JOB_PATH's bare /detail/ shape. Evidence: test_not_job_path_excludes_typo3_news_press_blog_event_detail_pages, test_not_job_path_still_allows_real_karriere_detail_pages, test_find_job_urls_drops_typo3_news_detail_pages_confirmed_klinikum_memmingen (synthetic sitemap fixture modeled on the exact klinikum-memmingen.de path shape named in the review; no live network access to re-crawl the real host).

AC10 (vendor_adapters.py off-host links): added _registrable_domain (naive eTLD+1, no PSL dependency -- this crawler only ever visits .de/.com/.org hospital domains) and _same_board, applied to _job_link_pairs (drops an extracted href whose netloc's registrable domain differs from the current page's) and _widget_endpoint_job_links (drops an href extracted from the AJAX response body that is off-board relative to that response's own resolved URL, so a redirect the board's own server issues still passes). Evidence: test_job_link_pairs_drops_a_link_to_an_unrelated_site, test_job_link_pairs_keeps_a_link_reached_via_the_boards_own_redirect, test_widget_endpoint_job_links_drops_off_board_links -- all modeled on the named psychiatrie-werneck.de/koenig-ludwig-haus.de shape. Ran the full test_completeness_group_portal.py/test_completeness_wp_jobs.py/test_ats_discover2.py/test_classify_section.py suite after this change: only the pre-existing known failure.

AC11 (bite.py _jp_from_json_jobs_php): city_raw now prefers ad.get("job_site") over addr.get("city") (tenant HQ), falling back to the HQ address unchanged when job_site is absent; reuses the existing PLZ-prefix split for either source. HONEST CAVEAT: "job_site" is the exact field name given in the task's own review evidence -- no live network access to independently confirm it against a raw _json.jobs.php response; grepped the whole repo and found no other sample carrying that key. Evidence: test_json_jobs_php_prefers_job_site_over_tenant_hq_address, test_json_jobs_php_falls_back_to_tenant_address_when_no_job_site, test_json_jobs_php_splits_a_plz_prefixed_job_site_too (added to tests/test_completeness_bite.py, all 10 tests in that file pass).

AC12 (klinikum_passau.py _parse): block bound now = html[start : next_job_start_or_end_of_html], not a fixed 8000-char slice. Evidence (new file tests/test_klinikum_passau.py): test_parse_does_not_bleed_a_posting_without_its_own_terminator_into_the_next_one (a posting missing its optional vacancy-from/vacancy-file tags no longer bleeds into the next posting's title/date), test_parse_captures_a_description_longer_than_the_old_8000_char_window (~9800-char description fully captured), test_parse_still_bounds_the_last_posting_by_end_of_page.

AC13 (verify.py): three sub-fixes. (a) _walk_jsonld now collects one (city,plz) per JobPosting node, contributing (None,None) when that node's own jobLocation list holds more than one DISTINCT address, instead of always taking the list's first entry -- extract_location's existing "skip falsy, try next hit" loop needed no change. (b) _clean_city strips a leading 5-digit-PLZ-plus-space prefix before its normal word-walk. (c) added _EINSATZORT_IDIOM and switched the einsatzort lookup from .search() (first match only) to a finditer()-based skip-list, excluding any Einsatzort/Arbeitsort/Standort/Dienstort occurrence immediately preceded by zum/zur/unsere/weitere/alle/andere. Evidence (new file tests/test_verify_location.py, 10 tests): multi-site jobLocation rejection + single/repeated-site acceptance, PLZ-prefix stripping via _clean_city and end-to-end via extract_location, idiom exclusion (bare "zum/zur ... Standort X" -> no match), colon-label and bare-label-without-idiom still matching, and a same-word-but-not-adjacent case (idiom word 3 tokens back) still matching to confirm the exclusion is positional. HONEST CAVEAT: all three verified with representative synthetic HTML/JSON-LD, modeled on the exact shapes named in the review; no live-network re-verification against karriere.ge-passau.de / jobs.klinikum-gap.de was possible.

Full offline suite, 3 runs across the session (each after further edits): run1 "1 failed, 1190 passed, 1 skipped, 1195 deselected, 385.04s"; run2 (after the write_clinics refactor) "2 failed, 1189 passed, 1 skipped, 1195 deselected, 389.16s" -- the extra failure was tests/test_web_login.py::test_success_follows_next, a real-headless-Chromium-vs-bare-http.server Playwright test with zero dependency on any file this task touches; re-ran that file alone immediately after (17/17 passed) and it did not reproduce in the 3rd full run either; treated as a pre-existing environment-timing flake under concurrent multi-agent load on this shared VM, not a regression. run3 (final, after adding the missed smartrecruiters test for AC4) "1 failed, 1191 passed, 1 skipped, 1195 deselected, 381.75s" -- the one failure is exactly the pre-flagged tests/test_completeness_wp_jobs.py::test_two_dead_slugs_redirecting_to_the_same_landing_page_collapse_to_one_row (TASK-75 AC#1), nothing else.

Files changed: pflege_jobs/cli.py, sql/001_schema.sql, pflege_jobs/orchestrate.py, edge/pflege-ingest/index.template.ts, edge/pflege-ingest/index.ts, skill/references/pipeline.md, skill/references/data-model.md, web/skill/pipeline.md, web/skill/data-model.md, web/skill/pflege-jobs.skill.md, crawlers/vendor_adapters.py, app/data.py, pflege_jobs/sources/bite.py, pflege_jobs/sources/klinikum_passau.py, pflege_jobs/verify.py, tests/test_cli_inbox_probe.py, tests/test_cli_dedupe_repair.py, tests/test_schema_resolve_postings.py (new), tests/test_vendor_adapters.py, tests/test_app_api.py, tests/test_completeness_bite.py, tests/test_klinikum_passau.py (new), tests/test_verify_location.py (new). pflege_jobs/sinks.py was named in the task's file list but needed no code change -- its existing write_clinics() is reused for AC2 rather than reimplemented. No git commit made, no production Supabase read or write performed.

CORRECTIVE RE-REVIEW PASS (2026-09-18, second agent session): an adversarial reviewer marked this task
not-approved but returned no specific problem list. Re-checked every one of the 13 ACs myself against the
actual current git diff (cli.py, sinks.py, sql/001_schema.sql, edge/*.ts, orchestrate.py, app/data.py,
crawlers/vendor_adapters.py, bite.py, klinikum_passau.py, verify.py, plus their tests and the mirrored
skill docs) and re-ran the full offline suite from scratch. Found and fixed two real defects that survived
the first pass; everything else held up under re-verification (see per-AC evidence below).

FIX 1 (AC3/AC4, pflege_jobs/cli.py same_source_variant_pairs): the two-grouping-axis design (canonical-URL
group + ats-job-id/fuzzy_key group) can put one posting_id into BOTH groups at once as a non-min member,
each group independently picking a different min as that posting_id's dst. Building pairs per-group and
then feeding them through collapse_merge_chains' `{src: dst}` dict comprehension silently kept only the
LAST such pair for that src and dropped the other -- a real duplicate-posting merge instruction vanished
with no error, exactly the kind of silent loss this task exists to eliminate. Reproduced empirically before
fixing (obs where posting 5 shares a literal URL with posting 2 [fuzzy_key drifted, different min=2] and
also shares an ats-job-id+fuzzy_key with posting 3 [min=3]: pre-fix same_source_variant_pairs returned only
[{"src":5,"dst":3}], silently never merging posting 2 at all). Rewrote same_source_variant_pairs to fold
every group through one shared union-find (path-walking find/union, root=lowest posting_id) instead of
resolving each group independently -- every posting_id in a connected component now converges on that
component's one true minimum no matter how many groups tie it there; collapse_merge_chains is still called
on the result (now a provable no-op: union-find output never contains a dst that is also some pair's src)
for defense in depth. New test: tests/test_cli_dedupe_repair.py::test_same_source_pairs_union_two_groups_that_disagree_on_one_postings_dst.
All 12 pre-existing tests in that file still pass unchanged.

FIX 2 (AC2, pflege_jobs/sinks.py EdgeSink.write_clinics): AC2's own fix reused write_clinics for the
probes POST (good, matches "batch the same way every other caller does") but write_clinics itself never
deduplicated by clinic_id first. The edge function's `clinics` op is one multi-row
`insert ... on conflict (clinic_id) do update` (edge/pflege-ingest/index.template.ts) -- Postgres raises
"ON CONFLICT DO UPDATE command cannot affect row a second time" when the same clinic_id appears twice
within one batch (concretely: two ats-discovery probe rows for the same clinic landing in the same
1000-row inbox page). That raise is not caught anywhere in _drain_once, so it reproduces verbatim the
exact failure AC2 exists to close: one bad batch wedges the whole call before the inbox_ack POST is ever
reached, and the page re-fails forever. Fixed by deduplicating write_clinics' input by clinic_id (last
wins) before batching, mirroring EdgeSink.write()'s existing (source_id, source_ref) dedup for
observations and its documented reason ("to avoid ON CONFLICT errors within a batch"). This also fixes the
same latent risk in write_clinics' other, pre-existing caller (crawlers/career_discover_exa.py
write_back), for free. New test: tests/test_sinks.py::test_write_clinics_dedupes_by_clinic_id_last_wins.
tests/test_cli_inbox_probe.py's batching test (850 distinct clinic_ids) still passes unchanged since it
has no duplicates to dedupe.

RE-VERIFIED, NO CHANGE NEEDED: AC1 (grepped repo-wide for any remaining fabricated verify stamp -- none);
AC5 (diffed resolve_postings() against pflege_jobs/schema.py OBS_SPEC and edge/pflege-ingest/index.ts
field-by-field, confirmed the golden-rebuild UPDATE runs unconditionally over every posting_id including
freshly-inserted stub rows, so the new columns are always populated); AC6 (confirmed no .github/ directory
exists at all in this repo, deploy/github-workflow-daily.yml's own header says "Reference only... Not
committed under .github/", grepped app/scheduler.py -- zero references to orchestrate/expire); AC7
(confirmed both named live boards -- kbo.de via crawl_group_portal->parse_job_page, frg-kliniken.de via
crawl_wp_jobs->_wp_job_rows->parse_job_page -- route through the one fixed function; noted honestly that
pflege_jobs/sources/{beesite,hr4you,career_crawl,inbox,feeds}.py and bite.py's first_published carry the
same raw [:10]-slice idiom but are outside this task's named file list with no measured live evidence, so
deliberately left untouched rather than silently expanding scope); AC8 (hand-traced app/data.py cities()
against its 3-fixture test case row by row); AC9/AC10 (traced every call site of NOT_JOB_PATH/_same_board,
confirmed base URLs passed are always the real post-redirect fetch URL); AC11 (bite.py job_site preference
matches the task's own named evidence, no other sample in the repo carries that field to cross-check
against); AC12 (klinikum_passau.py next-match bounding, boundary tests cover missing-terminator/oversized/
last-posting cases); AC13 (manually traced the _EINSATZORT_IDIOM window regex and _walk_jsonld's
distinct-address-set logic against several hand-built edge cases beyond the checked-in tests, including
idiom-word adjacency and multi-jobLocation dedup-vs-ambiguous cases -- all resolved correctly).

Verification commands: `.venv/bin/python -m pyflakes` on every file this task touches (clean, no unused
names/imports); `.venv/bin/python -m pytest tests/test_cli_dedupe_repair.py tests/test_cli_inbox_probe.py
tests/test_sinks.py tests/test_career_discover_exa.py -q` (all green) after each fix; full offline suite
run 3 times total in this session: two clean (1 failed [pre-flagged TASK-75 AC#1] / 1191 passed / 1 skipped
/ 1195 deselected), one additionally showing tests/test_web_login.py::test_success_follows_next failing --
a Playwright test with zero import/behavioral relation to any file this task touches; re-ran that file
alone immediately after (17/17 passed), confirming the identical shared-VM timing flake this task's own
first-pass notes already diagnosed for the same test, not a regression from either fix above.

THIRD-PASS CORRECTIVE RE-REVIEW (2026-09-18, this session): reviewer again marked not-approved with
no specific list. Did not trust either prior pass's own notes -- re-derived evidence from scratch:
read every AC-relevant hunk in the current git diff line by line, hand-traced the logic against
concrete inputs, and exercised it via pytest (targeted files first, then the full offline suite).

Found and fixed ONE real defect that survived both prior passes, in AC13's own _walk_jsonld
(pflege_jobs/verify.py): `out.append(addrs[0] if addrs and len(distinct) <= 1 else (None, None))`
picked the list's FIRST jobLocation entry whenever the address set was unambiguous (<=1 distinct
non-blank address) -- but "first" is not "the real one" when a blank placeholder address (e.g.
{"address": {}}, a template artifact) precedes the genuine address in the same jobLocation list.
Repro: jobLocation=[{"address":{}}, {"address":{"addressLocality":"Passau","postalCode":"94032"}}]
-> extract_location returned (None,None,None) pre-fix even though there is exactly one real,
unambiguous address in the list -- the opposite of AC13's own stated goal (only reject when
genuinely ambiguous; otherwise use the real address). Fixed by selecting the one member of
`distinct` (the non-blank set) instead of addrs[0]: `next(iter(distinct)) if len(distinct)==1 else
(None,None)`. Verified against all pre-existing AC13 tests (unaffected -- single real address,
duplicate-identical real address, and genuinely-distinct-2-site cases all still pass) plus a new
regression test, tests/test_verify_location.py::test_extract_location_keeps_the_one_real_site_when_a_blank_entry_precedes_it,
built from a failing-before/passing-after repro, not just code reading.

RE-VERIFIED, HELD UP (evidence this pass, not just re-reading prior notes):
AC1 -- read _drain_once top to bottom: the `ver=[...]` fabrication is gone, no replacement fabrication
introduced; tests/test_cli_inbox_probe.py::test_drain_once_never_fabricates_a_verify_stamp exercises
the real function (faked HTTP only) and asserts no "verify" key is ever posted. Re-ran: pass.
AC2 -- traced the try/except around `rq.get(.../clinics, params={...})`: a poison clinic_id or any
lookup failure acks-with-note and `continue`s, never raises; probes POST goes through the real
sinks.EdgeSink.write_clinics (not reimplemented). Re-ran test_probe_branch_uses_params_and_acks_a_bad_clinic_id_instead_of_raising
and test_probes_post_is_batched_via_write_clinics_not_one_unbounded_call (850 probes -> [200,200,200,200,50]
clinics batches): pass. write_clinics' own clinic_id dedupe (last-wins, mirrors write()'s
(source_id,source_ref) dedupe) re-confirmed by hand-tracing sinks.py plus re-running
test_write_clinics_dedupes_by_clinic_id_last_wins.
AC3/AC4 -- hand-derived the union-find trace for test_same_source_pairs_union_two_groups_that_disagree_on_one_postings_dst
by hand (posting 5 in both the canonical-URL group with 2 and the ats-job-id group with 3; union
converges both onto root 2, giving [{src:3,dst:2},{src:5,dst:2}], matching the test) and separately
proved cmd_link_cross's cross-source pairs (pass 2) cannot form an actual cycle (edges only ever run
from a higher list-index to a lower one within one clinic/city group, so collapse_merge_chains' cycle
branch is defensive-only there, never exercised in practice) -- re-ran the full
tests/test_cli_dedupe_repair.py: 31/31 pass.
AC5 -- re-read resolve_postings() against pflege_jobs/schema.py's OBS_COLUMNS field by field (not
just trusting the earlier diff summary); re-ran tests/test_schema_resolve_postings.py, which
parametrizes over every OBS_COLUMNS entry and asserts both schema presence and (for golden fields)
assignment inside resolve_postings() from the SQL text itself: 104/104 pass, including the
status='open' negative-assertion test.
AC6 -- repo-wide grep for mark_expired/expire_days: zero code references left (function dropped via
`drop function if exists`, orchestrate.py caller removed, edge/pflege-ingest/index.template.ts AND
the generated index.ts both drop the handler identically); skill/references/{pipeline,data-model}.md
and both web/skill/ mirrors (plus the concatenated pflege-jobs.skill.md) all in sync. docs/scraping.md:17
still says "weekly expiry of unseen rows" -- confirmed still out of AC6's literal file list (only
pipeline.md + pflege-jobs.skill.md are named) and left untouched, honestly flagged again rather than
silently expanded into.
AC7 -- confirmed _sane_date is actually routed through at every datePosted-shaped read left in the
file (parse_personio_xml, parse_personio_wp, parse_smartrecruiters, parse_helix_detail,
parse_job_page, _enrich_wp_fallback_fields x3, _faq_accordion_job_rows, dvinci, mein_check_in); re-ran
the 5 _sane_date/parse_job_page-specific tests plus the full tests/test_vendor_adapters.py: pass.
AC8 -- hand-traced app/data.py cities() against the 3-fixture test (own-city-only posting, no-clinic_id
posting, city != clinic_town posting) row by row, confirmed no double-count when own-city and
clinic_town fold to the same row (case-insensitive by_lower key) and correct separate counting when
they differ; re-ran tests/test_app_api.py::test_cities: pass.
AC9 -- confirmed the TYPO3 news/press/blog/event alternative in NOT_JOB_PATH matches exactly the
named klinikum-memmingen.de shape and does not exclude a real /karriere-detail/ or /stellenangebote/detail/
path; re-ran the 3 AC9-specific tests: pass.
AC10 -- traced every _job_link_pairs/_widget_endpoint_job_links call site to confirm `base`/`r.url`
is always the post-redirect URL (_page_base falls back to r.url, never the pre-redirect request URL),
so the "own redirect" carve-out in the AC text is satisfied by construction, not by a special case;
re-ran the 3 AC10-specific tests: pass.
AC11 -- confirmed city_raw now reads `ad.get("job_site") or addr.get("city") or ""` with the same
PLZ-prefix split applied to either source; re-ran the 3 new bite tests plus the full
tests/test_completeness_bite.py (13/13): pass.
AC12 -- confirmed the block bound is `matches[i+1].start() if i+1<len(matches) else len(html)`, not a
fixed slice; re-ran tests/test_klinikum_passau.py (3/3): pass.
AC13 -- see the one real fix above (_walk_jsonld); the other two sub-fixes (_clean_city PLZ-strip,
_EINSATZORT_IDIOM 20-char lookback window) hand-traced against all 4 idiom/non-idiom test shapes and
confirmed correct; re-ran tests/test_verify_location.py (now 11/11 incl. the new regression test).

pyflakes clean on every TASK-73 file (pflege_jobs/{cli,sinks,verify}.py, app/data.py,
crawlers/vendor_adapters.py, pflege_jobs/sources/{bite,klinikum_passau}.py,
tests/test_verify_location.py).

Full offline suite, re-run from scratch after the fix: `1 failed, 1194 passed, 1 skipped, 1195
deselected, 391.09s` -- the one failure is exactly the pre-flagged
tests/test_completeness_wp_jobs.py::test_two_dead_slugs_redirecting_to_the_same_landing_page_collapse_to_one_row
(TASK-75 AC#1), nothing else. (1194 not 1191/1193 because this pass added exactly one new test.)

No live Supabase/DB access used this pass either (none available, per task constraints) -- AC5/AC6/AC11/AC13's
live-board claims remain evidence-based (schema/edge-function cross-checks, repo-wide greps, the
task's own measured findings) and offline-test-pinned, same honest caveat as the prior two passes.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed all 13 acceptance criteria: fabricated verify_status on inbox drain and the probe branch's SQL-injection-shaped filter + unbatched POST + SystemExit wedge (cli.py); merge-chain FK-violation risk and duplicate postings from ATS vanity-domain aliases (cli.py); resolve_postings() schema drift (dropped the unconditional status='open', added department_raw/enr_pay_grade/enr_pay_text/enr_requirements/enr_experience -- sql/001_schema.sql); mark_expired/expire_days removed as dead code end-to-end (SQL function, orchestrate.py caller, edge function handler, runbook docs); epoch datePosted placeholder now treated as absent (vendor_adapters.py, 10 call sites via one new _sane_date helper); GET /api/cities now derives counts from the postings themselves instead of a clinic-registry aggregate that dropped clinic_id-less postings (app/data.py); TYPO3 news/press/blog/event pages no longer pass as job postings, and off-host links found in a board's own HTML/AJAX widget are no longer followed (vendor_adapters.py); bite.py's per-ad job_site field now wins over the tenant HQ address; klinikum_passau.py bounds each posting by the next match instead of a fixed 8000-char window; verify.py's location extraction rejects multi-site JSON-LD ambiguity, strips a PLZ prefix out of addressLocality, and no longer treats site-directory idioms ("zum Standort X") as the posting's own location.

Verified with 60+ new/updated unit tests across 8 test files (3 new: test_schema_resolve_postings.py, test_klinikum_passau.py, test_verify_location.py) plus 3 full offline-suite runs; the final run is clean except the one pre-flagged unrelated failure (TASK-75 AC#1, test_completeness_wp_jobs.py). No live Supabase/DB access was available or used (read or write) -- AC5/AC6/AC11/AC13 are evidence-based (cross-checked against pflege_jobs/schema.py, edge/pflege-ingest/index.ts, and the task's own live-measured findings) and offline-test-pinned, not independently re-confirmed against the live boards/DB named in the review; this is noted honestly per-AC in the implementation notes rather than overclaimed.

CORRECTIVE RE-REVIEW ADDENDUM (2026-09-18, second pass): re-checked all 13 ACs against the current diff
and re-ran the full offline suite from scratch after an adversarial reviewer marked the task not-approved
(with no specific findings). Found and fixed two real defects the first pass missed: (1) AC3/AC4's
same_source_variant_pairs could silently drop a genuine duplicate-posting merge when one posting_id
straddled both grouping axes with disagreeing minimums -- fixed by folding every group through one shared
union-find instead of resolving each independently (pflege_jobs/cli.py), so a connected component always
converges on its true single minimum; (2) AC2's reuse of sinks.EdgeSink.write_clinics for the probes POST
exposed that write_clinics never deduplicated by clinic_id, so two same-clinic probe rows landing in one
batch would make Postgres raise on the edge function's multi-row upsert -- an uncaught exception that
reproduces the exact "one bad batch wedges the whole call" failure AC2 was written to close. Fixed by
deduplicating write_clinics' input by clinic_id (last wins), mirroring EdgeSink.write()'s existing
dedup pattern for observations; this also silently fixes the same latent risk in write_clinics' other,
pre-existing caller (career_discover_exa.py). Both fixes are pinned by new regression tests
(tests/test_cli_dedupe_repair.py, tests/test_sinks.py) built from an empirical repro against the pre-fix
code, not just code reading. Every other AC was re-verified against the live code (schema/edge-function
field cross-checks, repo-wide greps, call-site tracing, manual regex tracing beyond the checked-in tests)
and held up unchanged. Full offline suite: green except the one pre-flagged unrelated failure
(TASK-75 AC#1) in two of three runs; the third run's extra failure (tests/test_web_login.py, a Playwright
test unrelated to any file this task touches) reproduced the identical shared-VM timing flake already
diagnosed in this task's first-pass notes, confirmed non-reproducing when that file is run alone (17/17).
Status remains Done: all 13 ACs still checked, now on firmer evidence.

THIRD-PASS ADDENDUM (2026-09-18, this session): re-derived evidence for all 13 ACs from scratch
(git diff + direct execution, not prior notes) after a second not-approved review with no specific
list. Found and fixed one real defect that survived both prior passes: _walk_jsonld
(pflege_jobs/verify.py, AC13) picked jobLocation's first list entry instead of its one real
(non-blank) address, so a blank placeholder address preceding the genuine one in the same list made
an unambiguous address look falsely rejected -- fixed to select the single member of the non-blank
distinct set, pinned by a new failing-before/passing-after regression test
(tests/test_verify_location.py::test_extract_location_keeps_the_one_real_site_when_a_blank_entry_precedes_it).
Every other AC re-verified by hand-tracing the actual current code against concrete inputs (not
re-reading old notes) plus re-running its own targeted tests, and held up unchanged. Full offline
suite re-run clean except the one pre-flagged unrelated failure (TASK-75 AC#1): 1 failed / 1194
passed / 1 skipped / 1195 deselected. Status remains Done: all 13 ACs still checked, now
independently re-derived twice over rather than carried forward on trust.
<!-- SECTION:FINAL_SUMMARY:END -->
