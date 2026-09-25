---
id: TASK-128
title: >-
  Firecrawl fallback rung: stop paying for the LLM /v2/agent endpoint to also
  classify/extract -- use a plain scrape/crawl fetch and run the SAME
  classify.py/registry.py pipeline every adapter already uses
status: Done
assignee: []
created_date: '2026-09-23 10:26'
updated_date: '2026-09-24 01:24'
labels: []
dependencies: []
priority: medium
ordinal: 128000
---

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Firecrawl integration gains a raw-fetch path (POST /v2/scrape or /v2/crawl, no schema/prompt) for walled/JS-only boards -- returns page HTML/markdown, same shape a normal HTTP or Playwright fetch would, no LLM reasoning step
- [x] #2 That raw content is fed through the existing discovery+classify pipeline (the same JOB_PATH/JOB_TEXT link discovery + pflege_jobs.classify.classify_role/classify_employer + pflege_jobs.registry.Matcher every other adapter uses) instead of pflege_jobs/sources/firecrawl_agent.py's JOBS_SCHEMA prompt inventing title/city/department/role itself
- [x] #3 Cost comparison recorded: credits spent per board on the new raw-fetch path vs the current run_jobs_agent() /v2/agent path, on at least 3 real walled boards
- [x] #4 Decide and document whether this can run as a standing fallback rung (like Playwright) rather than the current budget-gated special case -- if credits run out, only the Firecrawl rung errors, every other adapter keeps running unaffected, same failure shape as today's spend_gate/kill_switch
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Ivan's idea, 2026-09-23: today's ONLY Firecrawl integration is pflege_jobs/sources/firecrawl_agent.py's run_jobs_agent() -> POST /v2/agent (app/crawl.py's mode='firecrawl' dispatch, docs/firecrawl.md), an LLM-driven job that reads the prompt+JOBS_SCHEMA and does its OWN extraction/classification (title, city, department, requirements, tariff, employment_type, seniority -- jobs_to_inbox_rows() in the same file maps that straight to inbox rows) -- structurally duplicating what classify.py/registry.py already do for every other adapter, while ALSO paying to defeat the bot-wall. A 27-credit example run is on record (docs/firecrawl.md). Ivan's proposal: split the two concerns -- pay Firecrawl only to get past the wall (a cheap raw scrape/crawl of the page, no schema/prompt/LLM reasoning at all), then run that HTML/markdown through the exact same structural pipeline (link discovery -> classify_role/classify_employer -> Matcher) every other adapter already uses. Since Firecrawl would then behave structurally like any other adapter (just another expensive-per-page fetch rung, not a smart end-to-end extractor), it could stay always-enabled as a standing fallback rung rather than the current explicit budget-gated mode='firecrawl' special case -- running out of credits would just mean that ONE rung starts erroring, same as any other paid API limit, while every other adapter keeps working exactly as today.

2026-09-23 recon-then-scrape redesign (Ivan's idea, in reply to the first live AC#3 test):

First live test (clinic 18713 Simssee Klinik, max_credits=20, crawl_via_scrape() alone seeded from
the registry's careers_url): aborted at cap, 0 rows -- the raw BFS walked into clinical-department
subpages instead of the job board (confirmed the wall itself is real: plain HTTP -> 403 on /karriere/
and /sitemap*.xml). Root cause: a dumb per-page walker has no way to tell "wrong entry URL" from "slow
board", so it just burns the whole cap wandering.

Real production cost baselines pulled from hunt_state/firecrawl_usage/crawl_runs (no new agent spend
needed for these three): 18713 agent path 97 credits -> 6 rows (run 124, 2026-09-22); 27501
Kreiskrankenhaus Rotthalmünster 77 credits -> 11 rows, 11 new (run 39); 77801 Klinik Mindelheim /
Klinikverbund Allgäu 112 credits -> 2 rows, 2 new (hunt_state, day 2026-09-08).

Ivan's fix: don't run the naive raw walk blind. Spend ONE cheap recon agent call first with a
DIFFERENT prompt/schema (not JOBS_SCHEMA) whose job is to find the crawl strategy or say why there
isn't one -- exactly the three outcomes Ivan asked for. Implemented by extending the EXISTING
run_career_agent()/CAREER_SCHEMA (it already surveyed the site structurally; it just didn't carry a
verdict) rather than inventing a new prompt from scratch:

- CAREER_SCHEMA gained `verdict` (enum board_found|not_a_board|not_clinic_specific, required),
  `crawl_strategy` (only when board_found: concrete steps -- entry URL(s), pagination mechanism,
  whether listing links go straight to detail pages, any JSON/XML/RSS feed -- for a PLAIN page-by-page
  fetcher, no JS, no reasoning) and `alternative_locations` (only when verdict != board_found: where
  else to check -- parent operator portal, group HR site, different subdomain).
- _career_prompt() rewritten to ask for exactly this 3-way verdict instead of just "find the portal".
- run_career_agent() normalizes an unrecognised/missing verdict to not_a_board (mirrors the existing
  ats_vendor->"other" normalization already in this function) -- conservative default: never assume a
  board exists and spend scrape credits on a guess.
- New pflege_jobs/sources/firecrawl_agent.discover_then_scrape(clinic, towns, max_credits_recon,
  max_credits_scrape, ...): calls run_career_agent() first; verdict != board_found returns immediately
  (rows=[], scrape_credits=0, profile.alternative_locations surfaced) -- crawl_via_scrape() is never
  even called, zero scrape spend on a dead end; verdict == board_found reseeds crawl_via_scrape()'s
  entry point at the recon's own portal_url (not the registry's possibly-wrong careers_url guess) and
  runs the existing cheap per-page walk from there. Returns combined {verdict, profile, rows, stats,
  recon_credits, scrape_credits, credits_used}.

Tests: tests/test_firecrawl_agent.py -- test_career_schema_has_verdict_strategy_and_alternatives,
test_career_prompt_asks_for_a_crawl_strategy_and_the_three_verdicts,
test_run_career_agent_passes_through_a_recognised_verdict,
test_run_career_agent_normalizes_an_unrecognised_verdict_to_not_a_board,
test_discover_then_scrape_seeds_the_scrape_at_the_recon_portal_url,
test_discover_then_scrape_stops_at_not_a_board_verdict_no_scrape_spent,
test_discover_then_scrape_stops_at_not_clinic_specific_verdict. 39/39 passed
(tests/test_firecrawl_agent.py); no regressions in tests/test_career_crawl_section.py,
tests/test_crawl_board_retry.py, tests/test_firecrawl_hooks.py (132 passed total). Mutation-tested 3
distinct breaks (verdict!=board_found flipped to ==, normalization fallback flipped to board_found,
entry-URL override dropped) -- all 3 went red, all restored byte-identical from a /tmp copy (never
git checkout/stash), re-verified green.

app/crawl.py's refetch_career() (the only other caller of run_career_agent(), already wired to
auto-write ats_type/careers_url back to Supabase when a clinic had none) needed no change: it already
guards the write with new_url.startswith("http"), and the prompt now tells the agent to leave
portal_url/careers_url empty on a non-board_found verdict, so a "not a board" recon still can't write
garbage into the registry.

Not yet done: a live test of discover_then_scrape() itself (would need a fresh Firecrawl agent-credit
approval from Ivan, separate from the raw-scrape-only spend already used on 18713 today). AC#3 still
needs the full 3-board comparison; AC#4's standing-fallback decision still open pending that data.

2026-09-23 recon executed live + registry write applied (Ivan approved both):

Live run_career_agent() on 18713 (max_credits=30): 22 credits (billable, run #6 today -- the 5 free
daily runs were already used up by this session's earlier tests), verdict not_clinic_specific. Real
finding: Simssee Klinik's own domain has no board at all -- its Karriere page just links out to the
shared "Gesundheitswelt Chiemgau AG" portal (karriere.gesundheitswelt.de). alternative_locations gave
that exact URL.

Follow-up investigation (free, plain HTTP, no more Firecrawl): karriere.gesundheitswelt.de is NOT
walled (200 OK) and is rexx-powered (copyright meta tag). Its own listing page exposes a
`filter[client_id][]=<id>` query selecting one of 8 legal entities under the AG (client_id 3 = Simssee
Klinik GmbH, 6 = Klinik St. Irmingard GmbH -- both of which are registry clinics, 18713 and 18721 --
plus 6 non-hospital entities: two Reha/Gesundheitszentrum GmbHs, a thermal spa/wellness resort, and
others, all out of registry scope today). Found and fixed a real contamination risk in the existing
crawl_rexx() (crawlers/vendor_adapters.py): its "canonical fallback" (re-fetches the bare
/stellenangebote.html when careers_url carries a query, to close a coverage gap on cosmetic skin
variants like ?search_mode=...) would have silently merged the OTHER 6 entities' jobs back in for any
filter[client_id]-scoped URL, defeating the whole point of the filter -- gated the fallback to skip
when the query contains "filter[" (tests/test_completeness_rexx.py, mutation-tested).

Also found and fixed a compatibility conflict with TASK-99 (2026-09-22, already Done): its
VENDOR_ACCOUNT_POOLS entry for these same two clinics (added when both shared ONE unfiltered URL, to
widen board_clinic_ids so the Matcher's town rule could sort postings between them) became actively
harmful now that each clinic's own fetch is already precise by construction -- removed the entry
(tests/test_vendor_account_pools.py pins account_pool_for("18713"/"18721") == None now; see the
comment left on TASK-99 for the full story).

Live verification (free HTTP, crawl_rexx() against the real filtered URLs): 18713 -> 15 jobs, 6
certified-nursing (Bereichsleitung Pflege, Pflegefachkraft Akut Orthopädie, 2x Pflegefachkräfte
variants, 2x Dauernachtwachen), zero contamination from other entities. 18721 -> 11 jobs, 1
certified-nursing (Pflegefachkraft/Altenpfleger Nachtdienst), zero contamination.

Registry write (Ivan approved 2026-09-23, pushed via EdgeSink.write_clinics same as TASK-131):
  18713 careers_url -> https://karriere.gesundheitswelt.de/stellenangebote.html?filter[client_id][]=3, ats_type -> rexx
  18721 careers_url -> https://karriere.gesundheitswelt.de/stellenangebote.html?filter[client_id][]=6, ats_type -> rexx
Confirmed via live REST read before/after. Note: 18721's PRE-push live values (careers_url already
bare karriere.gesundheitswelt.de/stellenangebote.html, ats_type wp_jobs) differed from the local CSV
snapshot (stale department-page URL) -- consistent with TASK-80's known CSV-vs-live drift, not
investigated further here.

Architecture takeaway (Ivan, 2026-09-23): this recon-then-fix workflow is meant to run ONCE per
stuck clinic, not on a schedule -- its output (verdict + crawl_strategy/alternative_locations) feeds
a human/coding decision (existing adapter fits, as here -- zero new code, zero ongoing Firecrawl
cost; or a bespoke adapter keyed to the discovered strategy when the site is genuinely walled and no
vendor fits). discover_then_scrape()'s auto-chained scrape rung remains useful as a cheap one-off
fallback when hand-coding an adapter isn't worth it for a low-volume board, not as the primary path.

New backlog task opened for the broader implication Ivan drew from this same investigation (the AG's
6 non-hospital entities -- Reha centres etc. -- sharing this board are real employers with real
nursing vacancies, currently out of scope entirely): see TASK creation below.

AC#3 status: 2 real boards now have concrete before/after (Simssee: Firecrawl agent 6 raw rows/22-97
credits vs the fixed adapter's 15 rows/6 nursing, free, ongoing; St. Irmingard was never tried via
Firecrawl at all, adapter alone found 11 rows/1 nursing, free). Third comparator board (27501 or
77801, both have real recorded agent-path costs already) still not run through the new path.

2026-09-24: AC#3 completed -- real cost comparison across 3 boards (Ivan: "просто делай", proceeded
without further per-board asks per that instruction).

| board | raw-fetch path (crawl_via_scrape, no LLM) | current run_jobs_agent() (/v2/agent, real recorded cost) |
|---|---|---|
| 18713 Simssee Klinik | 20 credits, 0 rows (wandered into unrelated department pages -- wrong entry URL, board isn't even on this domain, see TASK-128's earlier notes) | 97 credits (billable) / 0 credits (free-allowance run) -> 6 raw rows |
| 27501 Kreiskrankenhaus Rotthalmünster | 1 credit, 0 rows (job_links_found: 0 -- the listing page fetched fine but the generic link-discovery heuristic found nothing usable on it) | 77 credits -> 11 rows, 11 new |
| 77801 Klinik Mindelheim (Klinikverbund Allgäu) | 1 credit, 0 rows (same job_links_found: 0 shape) | 112 credits -> 2 rows, 2 new |

Raw-fetch-only (crawl_via_scrape, the generic career_crawl.Crawler heuristic backed by Firecrawl
instead of a plain HTTP GET) found ZERO usable rows on all 3 real boards tested -- either by wandering
off-target (Simssee, no recon) or by finding nothing at all on the seed page itself (the other two).
The LLM agent succeeded on all 3 (6/11/2 rows respectively), at real cost (1-112 credits depending on
board and free-allowance timing).

Side finding while testing 77801: crawlers.routing.plan() already classifies this clinic as
NOT walled (walled: [], routed to the free crawl_wp_jobs adapter, ats_type=wp_jobs) -- a plain
`requests.get` of https://karriere.klinikverbund-allgaeu.de/ from this sandbox returns the full real
page (462KB, real karriere-detail job links) with no wall at all. Firecrawl's OWN scrape of the exact
same URL returned a stripped 144KB app-shell with zero job links -- i.e. Firecrawl's crawling
infrastructure appears to be the one getting a reduced/blocked response here, not this sandbox's plain
HTTP client (ironic, given Firecrawl exists specifically to get past such blocks). Running the existing
free crawl_wp_jobs adapter directly against this board found 85 rows but 0 classified as nursing --
that is a SEPARATE, real, currently-open question (role classification or title-extraction gap on this
specific board) unrelated to Firecrawl/TASK-128's scope; not investigated further here, flagging for a
follow-up task if the raw title data (still being pulled) confirms a real bug rather than a genuinely
non-nursing 85-row batch.

AC#4 decision: raw-fetch-only (crawl_via_scrape alone, seeded from the registry's own careers_url
guess) is NOT reliable enough to become a standing, always-on fallback rung replacing run_jobs_agent()
-- 0/3 real boards produced a single usable row this way. The value that DID hold up today is the
RECON step (discover_then_scrape()'s run_career_agent() call, cheap, ~20-30 credits, one-time per
board): it correctly diagnosed Simssee's real problem (wrong domain entirely -- the actual board was on
a shared group portal, not walled at all, now served for free via the existing rexx adapter after a
registry fix, see TASK-99's follow-up and this task's own earlier notes). Recommendation: keep
discover_then_scrape() available as a deliberate, human/agent-triggered diagnostic tool per stuck
clinic (Ivan's own framing, 2026-09-23: "один раз для каждой клиники, которую мы затрудняемся сами
потыкать"), NOT as an automatic scheduled fallback in app/crawl.py's execute() path. Its real output
(verdict + crawl_strategy + alternative_locations) should drive one of: recognizing an existing vendor
adapter already fits (free, as happened for Simssee/St. Irmingard), writing a bespoke adapter keyed to
the discovered crawl_strategy (for a genuinely walled, otherwise-simple board), or accepting that
run_jobs_agent()'s LLM reasoning remains the only working automated option for a genuinely complex/
JS-heavy board (as measured today for Rotthalmünster and, pending its own investigation, Mindelheim).
run_jobs_agent() itself is NOT being retired or bypassed by this work -- it stays the fallback for
boards raw-fetch and recon both fail to unlock.

2026-09-24 correction: the "0/85 nursing, flagging for a follow-up" note above was WRONG -- my own
test artifact, not a real bug. I called crawl_wp_jobs() directly in isolation; role_class is never set
inside the raw adapter (crawlers.vendor_adapters.row() only sets kind/source_host/source_url/payload/
collector/client_id) -- classify_role() runs downstream, in app/crawl.py's own per-row pass (line
~202), which I bypassed by calling the adapter standalone. Confirmed directly: classify_role on the
actual titles returned ("Pflegefachkraft (m/w/d) für unsere neonatologische Intensivstation",
"Pflegefachfrau/-mann (m/w/d)", ...) correctly returns pflegefachkraft. No classifier bug on this
board. Klinik Mindelheim's board is fine and, per the same test, not walled -- the free crawl_wp_jobs
adapter already finds real nursing postings there. Real open question this leaves (moved to TASK-147,
since it's the same operator/board-duplication story as TASK-144): production's own hunt_state/
firecrawl_usage ledger shows a REAL historical Firecrawl run against 77801 (112 credits, 2026-09-08)
even though crawlers.routing.plan() classifies it walled:[] today and the free adapter clearly works --
worth checking there whether that was a stale registry state at the time, a manual one-off trigger, or
something routing 77801 to Firecrawl unnecessarily even now.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Built the raw-fetch rung (AC#1/#2): pflege_jobs/sources/firecrawl_agent.scrape() (POST /v2/scrape,
no schema/prompt, verified live at ~1 credit/page) + crawl_via_scrape() (wraps career_crawl.Crawler,
so classify_role/classify_employer/fuzzy_key/Matcher all run unchanged -- confirmed via
tests/test_firecrawl_agent.py's crawl_via_scrape tests). Later extended with discover_then_scrape()
(a cheap CAREER_SCHEMA recon call first, verdict board_found/not_a_board/not_clinic_specific decides
whether to spend any scrape credits at all -- Ivan's design, 2026-09-23/24).

AC#3: real cost comparison on 3 boards (Simssee 18713, Rotthalmünster 27501, Mindelheim 77801) --
raw-fetch-only found 0 usable rows on all 3 (wandered off-target once, found nothing on the seed page
twice); run_jobs_agent() succeeded on all 3 (6/11/2 rows) at 1-112 credits. Full table in this task's
implementation notes.

AC#4, decided with that evidence: raw-fetch-only does NOT become a standing fallback rung -- it isn't
reliable enough on its own. The recon step (discover_then_scrape) is the piece worth keeping, used
deliberately per stuck clinic (not as an automatic scheduled path) to decide whether an existing vendor
adapter already fits, a bespoke adapter is worth writing, or run_jobs_agent()'s LLM reasoning remains
the only working option for that board. run_jobs_agent() is unchanged and stays the real fallback.

Concrete side wins along the way (not this task's own ACs, but delivered from the same investigation):
clinics 18713/18721 (Gesundheitswelt Chiemgau) moved off Firecrawl entirely onto the free rexx adapter
via a corrected, filtered careers_url (TASK-99 follow-up); a latent contamination bug in crawl_rexx's
canonical-merge fallback fixed; TASK-119's 28 stuck postings resolved; TASK-124's real statement-timeout
cause found and fixed; TASK-144's title-corruption bug found and fixed; TASK-145/147 opened for the
two deeper threads (abstract employer model, fuzzy_key cross-host dedup) this work surfaced but
deliberately did not solve today.
<!-- SECTION:FINAL_SUMMARY:END -->
