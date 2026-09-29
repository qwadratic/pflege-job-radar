---
id: TASK-152
title: >-
  Redesign verify: passive last_seen decay + on-demand check-on-click, replace
  most proactive per-posting probing
status: To Do
assignee: []
created_date: '2026-09-24 11:14'
updated_date: '2026-09-25 00:10'
labels:
  - verify-freshness
dependencies: []
ordinal: 152000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's design, 2026-09-24. Core idea: stop treating verification as 'proactively re-fetch every posting's own URL on a schedule' (today's pflege_jobs.verify.verify_all/_run_verify, a daily 05:17 UTC sweep that HTTP/render/Firecrawl-fetches EVERY open posting's external_url and checks the title text against the page body) -- and replace most of it with two cheaper mechanisms:

1) PASSIVE DECAY from the nightly crawl's own re-collection. Every crawl re-walks a board and finds mostly-duplicate postings (already known -- their observation just re-upserts, last_seen advances to now) plus some genuinely new ones (fresh last_seen). A posting the board did NOT relist this run gets no new observation, so its last_seen simply stops advancing and ages. Once last_seen is older than a freshness window (Ivan's suggestion: ~1 week), the posting is excluded from the public listing automatically -- no active URL fetch needed to reach that verdict, it is inferred from absence-of-relisting alone.

2) ON-DEMAND check at the moment someone actually wants the original URL (a click, or an API request for it) on a posting that's gotten a bit old (Ivan's suggestion: ~3 days since last_seen, so before the passive decay window would have caught it) -- check the URL live right then, and if it is gone: do not hand back the URL, return an error ('sorry, this posting has expired'), and immediately mark it gone/excluded from the listing even though it is younger than the 1-week decay window. Never delete the row -- it stays in the DB (historical record), it is only excluded from the public listing (Ivan: 'в обьюхе просто такие вакансии не показывались' -- filtered out of the API response, not the table).

Ivan's explicit framing of what does NOT need active checking: adapters are assumed to produce good URLs; an occasional bad URL from a real adapter bug is a small problem. The real problem worth catching is a BOARD-LEVEL YIELD COLLAPSE (yesterday 500 postings from a board, today 0) -- a different, board-scoped signal, not a per-posting one. This redesign is about per-posting liveness only; the yield-collapse detector is a separate, related concern (see AC#5).

Current-state facts, verified live 2026-09-24 (read before designing, several of Ivan's assumptions about the current system need checking against what actually exists):
- postings.last_seen already exists and already advances on every re-observation via resolve_postings() -- the raw signal this design needs is already being collected, nothing new to instrument there.
- GET /api/jobs does NOT filter by verify_status by default today (app/data.py:filter_jobs -- the verify_status filter is opt-in via ?verify=, only applied when a caller explicitly passes it). So today, an unverified or even a verify_status='error'/'blocked' posting is NOT hidden from the default public listing at all -- only status != 'open' hides something (status flips to closed via the ingest function's 'verify' op, only when verify_status='gone', which currently only ever gets set by the active per-posting probe). There is NO time-based decay mechanism today.
- The daily active sweep (schedule id 2, 'Daily status re-verification (all postings)', cron 17 5 * * *, mode='verify', scope='all') genuinely re-checks every open posting's own page every single day regardless of age -- this is the expensive mechanism the decay design would mostly replace.
- No redirect-through-our-own-domain endpoint exists today -- web/index.template.html:988 (pageJob) links the frontend straight to external_url/source_url. Mechanism 2 (check-on-click) is not implementable as-is; it needs a new endpoint (e.g. GET /go/{posting_id} or similar) that the frontend/API consumers link through instead of the raw external_url, does the live check, then 302-redirects on success or errors with a clear message on failure.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Decision recorded on the decay window (Ivan's suggestion: 1 week) and the on-demand trigger age (Ivan's suggestion: 3 days) -- with reasoning for the specific numbers, not just adopting the suggestion verbatim: check what fraction of currently-live postings would fall outside a 1-week window under NORMAL (non-broken) crawl cadence, so the window doesn't accidentally hide postings from boards that are only crawled every N days by design (stagger_days in app/schedules.py)
- [ ] #2 Decision recorded on whether a single missed re-crawl is enough to start the decay clock, or whether it should require N consecutive misses -- a board that's temporarily down for one night (real transient failures happen, confirmed this session e.g. augencentrum.de's CleanTalk block) must not silently start aging every one of its postings toward removal from one bad night
- [ ] #3 Decision recorded on the relationship to today's active per-posting probe (pflege_jobs.verify.verify_all/decide, the GONE_MARKERS/title-token logic): fully retired in favor of decay + on-demand, kept as a lighter/less-frequent fallback, or narrowed to only the on-demand check's code path (same decide() function, different caller/cadence)
- [ ] #4 GET /go/{posting_id} (or equivalent) implemented: does the live on-demand check (reusing pflege_jobs.verify's existing decide()/verify_url logic) when the posting is old enough to warrant it, redirects to the real URL on success, returns a clear 'this posting has expired' error and marks the posting gone+excluded on failure; the frontend (web/index.template.html:988 and any other place external_url is linked directly) is updated to link through this endpoint instead of the raw URL
- [ ] #5 Passive decay implemented: a posting whose last_seen exceeds the decided window (and meets the decided miss-count threshold) is excluded from GET /api/jobs's default listing without needing an active fetch -- verified live with a real aged posting
- [ ] #6 The board-level yield-collapse detector (yesterday 500, today 0) is EITHER scoped as a separate follow-up task with its own AC (if it does not already exist) or confirmed to already exist somewhere (e.g. within crawl_issues kind='empty'/TASK-72's silent-zero-yield work) and cross-referenced here rather than silently assumed solved by this redesign
- [ ] #7 Once the default GET /api/jobs behavior actually changes (verify_status filtering becomes the default rather than opt-in via ?verify=), the 'Pflege Hire: WA Harness' agent/session is notified with the exact before/after API contract so its bot tools can be updated to match -- reachable via SendMessage to that session name; if unreachable when this ships, Ivan does it manually and this AC is checked off by him, not silently skipped
<!-- AC:END -->
