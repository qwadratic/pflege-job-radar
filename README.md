# pflege-jobs — open nursing jobs at Bavarian hospitals

Every hospital site in the Bavarian **Krankenhausplan** (KeZ, beds, Fachrichtungen, Träger) and every open,
experienced-level nursing job found on that site's own career board — with the evidence per row and a
read-only API.

**Live:** <https://pflege-board.exe.xyz> (Pro-Dashboard: /pro) · **Docs:** [`docs/overview.md`](docs/overview.md) (ontology),
[`docs/scraping.md`](docs/scraping.md), [`docs/api.md`](docs/api.md), [`docs/performance.md`](docs/performance.md),
[`docs/whatsapp.md`](docs/whatsapp.md) (inbound leads)
· **Agents:** [`skill/SKILL.md`](skill/SKILL.md) (bundle served at `/skill/pflege-jobs.skill.md`)

```bash
curl 'https://pflege-board.exe.xyz/api/jobs?fresh_days=7&verify=live&limit=5'
```

## Sources

| precedence | source | role |
|---|---|---|
| 1 | Krankenhausplan Bayern 2026 (StMGP PDF) | identity: KeZ, beds, departments, operator |
| 2 | hospital career sites / ATS vendors (adapters) | the postings |
| 2 | Firecrawl agent on career sites | fallback for walled / unlabeled / JS-only sites |

No labour agency, no job boards. Scope: experienced nursing roles only (trainees, students, non-nursing refused at ingest).

## Layout

```
app/                FastAPI backend: /api, scrape worker, cron/preset schedules, CV match, Firecrawl, mechanics (port 8501)
  wa/               inbound WhatsApp harness: answers Pflege leads as Valentina, asking the question the board data says narrows the list most (docs/whatsapp.md, port 8502); WA_BRAIN=luna swaps in a Claude-driven brain with the same persona and gates (app/wa/luna/)
web/                two single-file SPAs: index.template.html (default at /, light: Hospitals → Jobs, Cities) and pro.template.html (/pro, dark: + Plan (the PDF as a table), Clawl (targets, schedules, runs), Docs (ontology graph), Settings (mechanics))
pflege_jobs/        pipeline: classify (patterns.json), resolve, link-clinics, verify, CLI; mechanics.py = the ten rules explained + testable
  sources/          one module per input (krankenhausplan, softgarden, bite, pi_asp, firecrawl_agent, …)
crawlers/           vendor adapters, routing (clinic → board → adapter), portals
sql/                schema + migrations
edge/               Supabase ingest function
docs/               concise docs + ontology.json (rendered in the app)
skill/              agent skill (Claude skill format), served at /skill/
data/registry/      clinics.csv, taxonomy.json, seeds
tests/              no network; one file per mechanic (tests/test_mech_<id>.py)
```

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                          # keys
set -a; . ./.env; set +a
.venv/bin/uvicorn app.main:app --port 8501    # board + API  (systemd unit: deploy/pflege-web.service)
.venv/bin/uvicorn app.wa.asgi:app --port 8502 # inbound WhatsApp harness (systemd unit: deploy/pflege-wa.service)

# pipeline pieces (the backend runs these for you; CLI for batch work)
python -m pflege_jobs.cli inbox         # raw crawler rows -> observations
python -m pflege_jobs.cli link-clinics  # postings -> KeZ
python -m pflege_jobs.cli link-cross    # merge the same job seen twice
python -m pflege_jobs.cli verify        # web-liveness
python web/build.py                     # fill config into web/index.html + web/pro.html + skill bundle
.venv/bin/python -m pytest -q tests
```

## Data model in one line

`clinics` (KeZ) ← `postings` (one per vacancy, resolved by precedence) ← `posting_observations` (one per sighting, the evidence);
`employers` = who wrote the ad; reads via `v_postings`. Graph: `docs/ontology.json`.

## Licence

Code: MIT (see `LICENSE`). Job advertisements belong to their employers; `source_url` links to the original.
