---
id: decision-3
title: Relabel LA-Regio Kliniken Landshut (26108) careers_url to /stellenportal and ats_type to typo3_jobs
date: '2026-09-09 00:00'
status: applied
---
**Update 2026-09-09 (main session):** applied via the direct project host + `SUPABASE_SECRET_KEY`
pattern (see decision-2's update -- same fix, this session's workflow agents don't have that
credential/pattern in their own context, only the main session does). `careers_url` set to
`https://www.la-regio-kliniken.de/stellenportal`, `ats_type` to `typo3_jobs`. Re-ran the adapter for
real: `GET /api/crawl/estimate?clinic_id=26108` now reports `board_rows:91`. A real batch ingest run
(15 self_hosted clinics incl. this one) fetched 91 rows here, but the whole batch's inbox POST then
hit a NEW, previously-undocumented wall: `PostgREST 400 "inbox: daily limit reached for this
client"` -- a per-day write quota on the ingest edge function, separate from Firecrawl credits. All
265 fetched rows across the batch are saved locally (`crawl_output/run_74.jsonl`) and were NOT lost,
just not yet ingested; retry once the quota resets (window not yet confirmed -- worth instrumenting
before the next large batch). Closed as far as the routing/registry fix goes; the daily inbox quota
is a new, separate open item.


## Context

clinic_id 26108 (LA-Regio Kliniken Landshut, 862 beds -- the single biggest clinic in the whole
registry) is labelled `ats_type='self_hosted'` with `careers_url='https://www.la-regio-kliniken.de/karriere-ausbildung'`.
`self_hosted` has no `ADAPTERS` entry (until this commit -- see `crawlers/routing.py`), so this
board reported `route_reason: "no adapter for self_hosted"` and was Firecrawl-only, and the
`careers_url` is a hub page one hop above the real listing, so even the new generic route would
have found nothing there.

Live-checked 2026-09-09 with the adapter's own UA: plain server-rendered TYPO3
(`<meta name="generator" content="TYPO3 CMS">`), not bot-walled -- careers hub, listing, detail
pages and `robots.txt` all returned HTTP 200 on first try; `robots.txt` is `Disallow:` (allow-all);
no sitemap (`/sitemap.xml` 404). The hub page `/karriere-ausbildung` links to `/stellenportal`,
which lists 91 unique `/stellenanzeige/<slug>` detail links, server-rendered, no pagination.

Dry-run of the existing `crawlers.vendor_adapters.crawl_wp_jobs` with `careers_url` swapped to
`/stellenportal` (no code change): 91 rows, 91 unique URLs, ~41 nursing-ish titles, e.g.
"Pflegefachkraft (m/w/d) Geriatrie", "Pflegefachkräfte (m/w/d) für unsere Intensivstationen",
"Gesundheits- und Krankenpfleger (w/m/d) für die Intensivstation", "Hebammen (m/w/d)". With the
current hub `careers_url` it returns 0: the hub page has no `JOB_PATH`-matching links, and there
is no sitemap to fall back to.

Same class of bug as decision-2 (66301): registry facts stale, not a parser gap. No dedicated
adapter needed -- `crawl_wp_jobs` already parses this board 91/91 once pointed at the listing.

## Decision

Blocked, not applied. Intended change:

```sql
UPDATE pflege_jobs.clinics
SET careers_url = 'https://www.la-regio-kliniken.de/stellenportal',
    ats_type = 'typo3_jobs'
WHERE clinic_id = '26108';
```

`typo3_jobs` already routes to `crawlers.vendor_adapters:crawl_wp_jobs` (`crawlers/routing.py`
`ADAPTERS`), same as 56 sibling rows.

Not applied because this session's Supabase credential has no write grant. Correcting
decision-2's note: `SUPABASE_SECRET_KEY` (with `Accept-Profile: pflege_jobs`) *does* work for
reads -- `crawlers.routing.load(os.environ["SUPABASE_SECRET_KEY"])` returned all 407 clinics this
session, including a fresh read confirming clinic 26108's current bad values. A `PATCH` to
`/rest/v1/clinics?clinic_id=eq.26108` with that same key returned:

```
401 {"code":"42501","message":"permission denied for table clinics"}
```

So this key is read-only (or the write path needs a different role/header this session didn't
have). Write access remains genuinely blocked, not just untested.

## Consequences

Until this row is updated, clinic 26108 (862 beds, the largest in the registry) keeps reporting
`route_reason: "no adapter for self_hosted"` and is Firecrawl-routed even though
`crawlers.vendor_adapters.crawl_wp_jobs` can already read its real listing for free. Whoever has a
write-capable Supabase credential should apply the `UPDATE` above and re-run
`GET /api/crawl/estimate?clinic_id=26108` to confirm `board_rows` ~91.

Separately (not blocked, applied this commit): `crawlers/routing.py` now routes all
`ats_type=self_hosted` boards through `crawl_wp_jobs` by default (see routing.py `ADAPTERS`
comment and `docs/scraping.md`), so once this row's `ats_type` becomes `typo3_jobs` it keeps
working either way -- the row fix mainly matters for the `careers_url` hub-to-listing hop, which
the generic route does not do (see `backlog/decisions/decision-3` note above: hub-hop was probed
and rejected as a generic fix, only this one board benefited).
