# Pipeline runbook (repo: https://github.com/qwadratic/pflege-job-radar)

Env (`.env`): SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SECRET_KEY, PFLEGE_INGEST_URL, PFLEGE_INGEST_SECRET, FIRECRAWL_API_KEY; optional LLM_API_BASE, LLM_MODEL.
Install: `python -m venv .venv && .venv/bin/pip install -r requirements.txt`; tests: `.venv/bin/python -m pytest -q tests`.

## Flow
```
clinics.careers_url + ats_type ──routing──▶ board list ──adapter──▶ inbox rows (jsonl) ──cli inbox──▶ observations
                                   │                                                            │
                                   └─ not routable / walled / dvinci ──Firecrawl agent──▶ inbox  ▼
                                                                                   resolve → postings → link-clinics → link-cross → verify → expire
```
One row shape for every crawler (`{kind, source_host, source_url, payload{title,org,loc[],url,description}, collector, client_id}`), one loader.

## From the app (preferred)
- `POST /api/crawl {"scope":"clinic|city|regierungsbezirk|job|board|all","value":…,"mode":"auto|adapter|firecrawl","max_credits":40}` → background worker: routing → adapters (or agent) → inbox → `cli inbox` → `link-clinics` → `link-cross` → verify of the new postings → cache refresh. Poll `GET /api/crawl/runs/{id}`.
- Weekly autocrawl (Settings → schedule): boards hashed into `batches` daily groups so not everything runs at once; Firecrawl only within `firecrawl_weekly_budget`.
- `POST /api/clinics/{kez}/refetch-career` → Firecrawl discovery → `career_profiles` + `clinics.careers_url/ats_type`.

## CLI (batch)
```bash
set -a; . ./.env; set +a
python -m crawlers.routing                       # coverage report; --plan = one JSON line per board
python crawlers/vendor_adapters.py [vendor]      # rexx, mein-check-in, personio, smartrecruiters, helix, concludis, typo3_jobs, talention, oracle → crawl_vendors/*.jsonl
python data/run_softgarden.py 100 ; python data/run_bite.py ; python data/run_pi_all.py ; python data/run_ats.py umantis 60
python crawlers/load_crawl_output.py crawl_vendors   # inbox → cli inbox → link-cross, prints deltas
python -m pflege_jobs.cli inbox | link-clinics | link-cross | verify --workers 5 | renormalize
python -m pflege_jobs.orchestrate --stages ats,browser,inbox,link,verify,publish   # the daily job (.github/workflows/daily.yml)
curl -X POST "$PFLEGE_INGEST_URL" -H "Authorization: Bearer $SUPABASE_ANON_KEY" -H "x-ingest-secret: $PFLEGE_INGEST_SECRET" -H 'Content-Type: application/json' -d '{"expire_days":7}'
```
Verify: ≤ 6 workers (8 trigger 429s). Only `gone` expires a posting.

## Adapters (how each vendor is read)
softgarden `jobs.feed.json` · B-ITE loader → key → `POST jobs.b-ite.com/api/v1/postings/search` · rexx `/stellenangebote.html?start=N` · umantis `/Jobs/1` server-rendered · mein-check-in `/<tenant>/overview` · typo3_jobs/concludis/talention/oracle job sitemap → detail HTML · personio `<slug>.jobs.personio.de/xml` · smartrecruiters public JSON · helix `/joblist` · pi_asp (Helios' P&I backend) Playwright · group portals (kbo, Schön, RHÖN, Südostbayern) once per board.
Shared boards: routing groups by exact `careers_url`; the board is the unit of work. Walled hosts (Helios www) are flagged, not crawled.
Not covered: dvinci (11, JS list), ~230 unlabeled sites → Firecrawl agent / refetch-career. Details and next steps: `/docs/scraping.md`.

## Firecrawl agent (`pflege_jobs/sources/firecrawl_agent.py`)
`POST https://api.firecrawl.dev/v2/agent {urls:[careers_url|website], prompt, schema, maxCredits}` → poll `GET /v2/agent/{id}` → rows with `collector=firecrawl-agent` (source 25). Two prompts/schemas: jobs (list every open nursing vacancy of the site, Bavarian locations only, follow pagination, open PDFs) and career discovery (portal URL, ATS vendor, filters + values, categories, job count, listing type). Every call capped; `creditsUsed` logged to `firecrawl_usage`; credits in `/api/stats`.

## Registry (Krankenhausplan)
`python -m pflege_jobs.sources.krankenhausplan data/registry/krankenhausplan_2026.pdf out.csv` → `python data/sync_krankenhausplan_2026.py [--dry-run]` (structured columns from 2026, names/towns from the trusted parse; sites leaving the plan → `nicht_mehr_im_plan`). Legend → `data/registry/taxonomy.json`. `python -m pflege_jobs.cli link-clinics` after every load.

## Adding an adapter
Write a function returning inbox rows for a clinic row (`crawlers/vendor_adapters.py` style) or a seeded module (`pflege_jobs/sources/<x>.py`), register it in `crawlers/routing.py:ADAPTERS` (a test pins every label to an importable callable), add the label to `taxonomy.json.ats_types`.

## Ingest endpoint
POST JSON `{employers?, observations?, verify?, clinics?, clinic_links?, merges?, inbox_ack?, resolve?, expire_days?, assets?, crawl_run?}` with `Authorization: Bearer <anon>` + `x-ingest-secret`; ≤ 500 rows per key. Column lists rendered from `pflege_jobs/schema.py` by `python edge/build_ingest.py` — edit spec, rebuild, redeploy (needs a Supabase access token; not available on 2026-09-06).

## Deploy
```bash
python web/build.py                          # web/index.html, web/llms.txt, web/skill/* (+ bundle)
sudo systemctl restart pflege-web            # deploy/pflege-web.service: .venv/bin/uvicorn app.main:app --port 8501
curl -s localhost:8501/api/stats
```
Port 8501 is the VM's default proxy port → https://pflege-board.exe.xyz. Build with the public project URL + anon key, not an internal proxy URL.
