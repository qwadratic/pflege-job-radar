# Filters removed from the adapters (2026-09-09)

Ivan's instruction: remove every content filter inside the adapters, keep the labels, and write down what the deleted code encoded. Each item below is a claim the rewrite must mark **CONFIRMED** or **REFUTED**. Line numbers are as of deletion time (working tree before this pass). Counts are "kept before / listed total" measured live this session over plain HTTP, no DB writes.

Terminology: a *label* is a field written onto the row (`role_class`, `in_bavaria`, `section_labels`, `office_raw`, `department`). A *decision* is a `continue`/drop/re-fetch based on such a field. Labels stayed; decisions went.

## A. Fetch-time pre-narrowing (decided what to fetch)

1. **Personio office narrowing** — `crawlers/vendor_adapters.py:crawl_personio` ~172-194. Dropped jobs whose `office_raw`/`org` token-overlapped less than 0.5 with the seeded clinic's name. Possible real fact: Personio's XML feed is group-wide (Bergman Clinics publishes one feed for every site nationwide) and `office` names the site. Safety default: the 0.5 overlap threshold and the assumption that a site's job never lists a sibling office. Evidence: Bergman Hofgartenklinik Aschaffenburg 2/56 kept before, 56 after; 0 Bavarian rows were among the 54 dropped, so the narrowing lost nothing Bavarian here. Question: *is `office_raw` alone enough to attribute a group-feed row to a site, or does the rewrite need a group→sites map?*

2. **Personio department narrowing** — `crawl_personio` ~195-205 plus the `_section_keep()` helper (~93-108). Computed a board-wide nursing label from `recruitingCategory`/`department` via `section.pick_nursing_category` and dropped rows whose department did not match. Possible real fact: Personio departments are free text chosen per tenant, so "Pflege" appears under many spellings. Safety default: picking one nursing category per board and discarding the rest. Evidence: `department` still on every row; no Bavarian loss measured. Question: *does the department label discriminate nursing better than `classify_role` on the title, or is it redundant?*

3. **Helix `category[]` re-fetch** — `crawl_helix` ~365-373 with `HELIX_CATEGORY` regex and `_helix_nursing_query()` (~330-343). Parsed the Berufsfeld fieldset for a nursing category hash and re-fetched the list narrowed to it. Possible real fact: Helix boards expose a category filter whose hash is stable per board. Safety default: that the nursing bucket contains all nursing jobs (bite.py item 9 shows buckets miss postings). Evidence: no per-job category field ever existed, so nothing was labelled; unfiltered list now taken. Question: *does any Helix board file nursing jobs outside the Pflege bucket?*

4. **mein-check-in sidebar group narrowing** — `crawl_mein_check_in` ~751-754, 764. Built `pid_group` only for the nursing sidebar group and skipped positions outside it. Possible real fact: mein-check-in's sidebar groups are the operator's own department taxonomy (Pflegedienst, Ärztlicher Dienst, MTD/Funktionsdienst). Safety default: that "Pflegedienst" is the only relevant group. Evidence: kna-online Klinik Eichstätt 4/29 before, 29 after; dropped groups were `Ärztlicher Dienst` and `Medizinisch-Technischer Dienst / Funktionsdienst`; `section_labels` now populated for every group. Question: *are sidebar groups reliable enough to serve as the department label, and does any nursing role sit outside Pflegedienst?*

5. **pi_asp title prefilter** — `pflege_jobs/sources/pi_asp.py:37-38`. Skipped clicking a listed title when `classify_role(title, "")` said `nicht_pflege`, before the Playwright detail fetch. Possible real fact: pi_asp lists titles only and each detail costs a browser click, so title-only classification was a cost shortcut. Safety default: trusting title-only classification without body text. Evidence: Helios 19/44 and Sana 48/90 titles were previously never fetched. Question: *how often does title-only `nicht_pflege` flip once the body is read, and is the click cost worth it?*

6. **WordPress nursing-section start URL** — `_wp_nursing_section_url` used by `crawl_wp_jobs` (~496). Traced end to end: it only chooses where discovery starts; the full sitemap walk still runs. Not a filter, no change. Listed so the rewrite does not re-suspect it. Question: *none — confirm it stays a discovery hint.*

## B. Post-fetch drops (decided what to keep)

7. **career_crawl / career_browser three continues** — `pflege_jobs/sources/career_crawl.py:232-235`, `career_browser.py:118-121`. Dropped `nicht_pflege`, `in_bavaria is False`, and `in_bavaria is None` unless `bavaria_only_operator`. Possible real fact: none; pure policy. Safety default: all three, plus the `bavaria_only_operator` override that forced `in_bavaria=True`. Evidence: `dropped_*` counters had no reader in `app/` or `tests/`; `data/run_browser_crawl.py:14` still indexes them and will KeyError. Question: *is "unknown location" a drop, a keep, or a reason to enrich?*

8. **bite.py role/Bavaria drops and forced `in_bavaria`** — `pflege_jobs/sources/bite.py:310-320`. Dropped `nicht_pflege`, `in_bavaria False`, `in_bavaria None`; on the enrichment re-fetch path forced `in_bavaria=True` regardless of the computed value. Possible real fact: BITE detail pages carry the address, so `in_bavaria` can be computed honestly. Safety default: the forced True. Evidence: Amberg 17/46 before, 46 after; 29 labelled `nicht_pflege`, `in_bavaria` True for all 46 from the address, not the override. Garmisch 35/56 from the earlier swarm. Question: *was the forced `in_bavaria=True` ever correcting a real geocoding gap, or masking one?*

9. **bite.py section-signal narrowing** (tested by `tests/test_bite.py:130,153,166`). Candidate list narrowed to the matched taxonomy bucket, with a recovery path for nursing postings filed outside it. Possible real fact: BITE operators misfile nursing postings under other buckets (the recovery test exists because it happened). Evidence: three tests still pin the old behaviour and fail; they need the keep-with-label flip. Question: *how often is a nursing posting filed outside the Pflege bucket on BITE?*

10. **feeds.py personio** — `pflege_jobs/sources/feeds.py:49-51`. Same trio as item 7 plus forced True. Evidence: labels intact. Question: same as 7.

11. **feeds.py smartrecruiters region mismatch** — `feeds.py:65,67,83-84`. Dropped rows whose region string did not match, then `nicht_pflege`, then both `in_bavaria` cases. Possible real fact: SmartRecruiters exposes a structured `region` field that is more reliable than free-text city. Safety default: string equality on region. Question: *is SmartRecruiters `region` a usable Bavaria label on its own?*

12. **feeds.py talention** — `feeds.py:102,117-119`. `nicht_pflege` drop and `in_bavaria` drop with forced True. No board-specific fact. Question: same as 7.

13. **softgarden path in `app/crawl.py:_seed_obs`** — `app/crawl.py:324-331`. Three drops with `dropped_non_bavaria`/`dropped_unknown_loc`/`dropped_not_pflege` counters. Kept: `bavaria_only_operator` sets `in_bavaria=True` only when it was `None` (labelling). Evidence: Bayreuth 44/116 before, 116 after; 72 labelled `nicht_pflege`, all 116 `in_bavaria` True from address. Counters had no reader in `app/main.py`, `app/runs.py`, `web/`, `tests/`. Question: *should `bavaria_only_operator` remain a seed-level fact that fills unknown locations?*

## What still filters today

Left untouched on purpose. This is the next decision.

- `pflege_jobs/sinks.py:18 only_pflege` — drops every `role_class` in `config.EXCLUDED_ROLE_CLASSES` (`nicht_pflege`, `ausbildung`, `werkstudent_praktikum`) before any sink writes; `keep_non_pflege=True` bypasses.
- `pflege_jobs/cli.py:311-313` — inbox drain acks rows with an excluded `role_class` or `in_bavaria is False` as "skipped" and never converts them to observations.
- `app/crawl.py:439 _load_observations` (list comprehension at ~444) — same two conditions applied before DB load; `in_bavaria None` passes here.
- `sql/008_experienced_only.sql:49,53` — CHECK constraints reject `role_class` in (`ausbildung`, `werkstudent_praktikum`, `nicht_pflege`) at the DB, so a bypassed gate still fails on insert.
- `pflege_jobs/sources/firecrawl_agent.py:117-132` — prompt clauses tell the LLM to INCLUDE only certified nursing titles and EXCLUDE helpers, trainees, physicians, admin, and "any job outside Bavaria"; the filter is in the instruction, not in code.

Test gap at time of writing: `tests/test_bite.py:130,153,166` still assert the removed narrowing (items 8-9).
