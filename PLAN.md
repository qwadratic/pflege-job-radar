# Plan — pflege_jobs

## State (2026-09-06, after the MVP rebuild)
- Sources: Krankenhausplan (10), career sites via adapters (20), Firecrawl agent (25). Arbeitsagentur + aggregators removed, data purged.
- App: FastAPI on :8501 (`app/`), two SPAs in `web/`: `index.template.html` (default at `/`, light: Hospitals → Jobs, Cities) and `pro.template.html` (`/pro`, dark, Proximata-derived tokens: + Plan table (PDF as searchable table), Scrape page (target form with preview, cron/preset schedules with on/off, runs), Docs (animated ontology graph + markdown), Settings (mechanics: explanation, source, patterns, try-it, per-mechanic tests; Firecrawl budget)).
- Registry: 407 sites (399 in the 2026 plan + 8 gone). Structured columns synced from the 2026 PDF; names/towns kept from the trusted 2025 parse (see docs/scraping.md).
- Schedules: default `weekly_staggered` (boards hashed over 7 days); any subset (Bezirk / city / hospital / vendor) can get its own cron. Adapters first, Firecrawl only where no adapter (credit-capped).
- Mechanics: `pflege_jobs/mechanics.py` registry, `tests/test_mech_*.py` one file each; `GET /api/mechanics` renders them.

## Open, by value
1. **Supabase access token** — blocks: edge-function redeploy (`coalesce` fix in ingest is live-tested locally only), DDL from `sql/008` (CHECK constraints) and everything in docs/performance.md (indexes, trigram, tsvector, LATERAL view).
2. **Coverage gap**: ~230 unlabeled sites (104 have a careers_url). Weekly refetch-career batches (Firecrawl) → labels → adapters. dvinci adapter (11 sites): probe JSON endpoints, else Playwright.
3. **Firecrawl credits**: 381 left in this period (8,000/month). Every call is capped; budget in Settings. Raise plan or wait for 2026-09-19 reset before bulk discovery.
4. **LLM for CV parsing**: gateway credits exhausted; CV match runs on patterns.json regexes. Set `LLM_API_BASE`/`LLM_MODEL` to upgrade.
5. München Klinik JobFinder (consent-gated XHR), UKR B-ITE widget key, Josefinum, DONAUISAR, Passau PDFs — see docs/scraping.md table.
6. Alerting: per-board row deltas > 30 %, null-rate of role_class/city, 403/429 twice → mark walled.
7. Candidate side beyond CV upload: saved profiles, alerts, outreach log (needs tables → token).

## Kill criteria per adapter
Disable a board when 3 consecutive runs return 0 rows where it had rows, or 2× 403/429 → `walled`, route to Firecrawl; noted in `crawl_runs.notes`.
