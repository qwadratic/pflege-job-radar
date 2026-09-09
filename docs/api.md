# API

Two doors. **App API** (`/api`, same host as the board, JSON, no auth for reads) for the UI and agents; **PostgREST** (Supabase, read-only, anon key) for bulk/raw access.

## App API (`https://pflege-board.exe.xyz/api`)

### Overview
| method | path | what |
|---|---|---|
| GET | `/api/stats` | open/fresh jobs, clinic counts, last crawl, Firecrawl credits, next autocrawl |
| GET | `/api/facets` | every filter value with counts (cities, bezirke, ATS, Träger, Stufe, Fachrichtungen, roles, departments …) |
| GET | `/api/clinics` | clinic table with job counts, fetch route (adapter / Firecrawl), last scrape |
| GET | `/api/cities` | one row per town: Bezirk, Landkreis, hospitals, open / fresh jobs |
| GET | `/api/plan` | the post-processed Krankenhausplan (every registry column) + `pdf_url` |
| GET | `/api/clinics/{kez}` | one clinic + jobs + runs + career profile |
| GET | `/api/jobs` | postings (v_postings + clinic columns) |
| GET | `/api/jobs/{id}` | full posting incl. description, observations |
| GET | `/api/search?q=` | fuzzy search across clinics, jobs, cities |
| POST | `/api/cv` | upload CV → profile + ranked matches |
| POST | `/api/crawl` | start a scrape: `target{scope,values}`, mode, credits, fetch_details |
| GET | `/api/crawl/plan` | preview of a target: hospitals, boards, via adapter / Firecrawl, est. credits |
| GET | `/api/crawl/estimate?clinic_id=` | free, pre-parse read of one clinic's board: how many titles look Pflegedienst, before running any real crawl. Honest about not knowing -- `confidence: none` if there's no adapter route at all (would need a paid Firecrawl probe), `low` if too many titles carry no nursing/non-nursing signal to say for sure (mixed-department board, or titles that need the full description read) |
| GET | `/api/crawl/runs`, `/api/crawl/runs/{id}` | run status + log |
| POST | `/api/crawl/runs/{id}/cancel` | best-effort: a queued run never starts; a running one stops at the next board/Firecrawl-clinic boundary (no hard kill mid-request) → updated run row |
| POST | `/api/clinics/{kez}/refetch-career` | Firecrawl discovery of the career portal |
| GET/POST/PUT/DELETE | `/api/schedules[/{id}]`, `POST /api/schedules/{id}/run-now` | cron / preset schedules with target, mode, budget, enabled |
| GET/POST | `/api/mechanics`, `/api/mechanics/{id}/try`, `/api/mechanics/{id}/test` | the ten rule mechanics: explanation, source, patterns, try-it, run tests |
| GET/PUT | `/api/settings`, `/api/settings/patterns` | patterns.json (every regex), Firecrawl default budget |
| GET | `/api/billing` | spend report over a window: series (hour/day buckets), totals, by kind, runs, Firecrawl pools, Exa |
| GET | `/api/coverage` | per-adapter coverage breakdown (routable, boards, open/fresh jobs, last run), owner-only |
| GET | `/api/firecrawl/credits` | account balance + token pool + spend, owner-only |
| GET | `/api/firecrawl/prompts?clinic_id=` | the live jobs/career prompt templates + schemas, rendered for a real hospital when given one, else generic placeholder text; read-only, no credits, owner-only |
| GET/POST | `/api/campaign` | reingest-campaign routine state: safety_level, stopped, history; owner-only |
| GET | `/api/taxonomy`, `/api/ontology`, `/api/docs` | taxonomy.json, ontology.json, docs index |

List responses: `{"total": N, "rows": [...]}`; `limit`/`offset` page; comma lists for multi-value filters.

### Query patterns
```bash
B=https://pflege-board.exe.xyz/api

# header numbers
curl "$B/stats"

# all filter values
curl "$B/facets"

# clinics: by city list, bezirk, beds range, size bucket, Fachrichtung, ATS, Träger, with jobs only
curl "$B/clinics?city=München,Augsburg&has_jobs=1&sort=-jobs_open"
curl "$B/clinics?regierungsbezirk=Oberbayern&beds_min=300&beds_max=800"
curl "$B/clinics?size=L,XL&fach=INN,CHI&traegerart=oeffentlich"
curl "$B/clinics?ats_type=softgarden&fetch=adapter"
curl "$B/clinics?fetch=firecrawl"                # no adapter → Firecrawl would read it (route_reason says why)
curl "$B/clinics?q=rexx"                         # q also matches badge values: ATS, Bezirk, Landkreis, codes, status, size
curl "$B/clinics?q=klinikum%20nürnberg"

# one clinic, drill down
curl "$B/clinics/16104"

# cities and the plan table
curl "$B/cities?q=regens"
curl "$B/plan?regierungsbezirk=Oberpfalz&sort=-beds"

# jobs: at a clinic, fresh only, by role/department/city/employment, housing, verified
curl "$B/jobs?clinic_id=16104"
curl "$B/jobs?fresh_days=7&sort=-first_published"
curl "$B/jobs?role_class=fachpflege&department_hint=Intensiv%2FIMC&city=München,Freising"
curl "$B/jobs?regierungsbezirk=Schwaben&employment_types=teilzeit&housing=1&verify=live"
curl "$B/jobs?q=OP%20Pflege&limit=50&offset=0"
curl "$B/jobs/2466"

# fuzzy search (typos ok, all fields)
curl "$B/search?q=klinkum%20augsbrg%20intensiv"

# CV match (pdf / docx / txt) or plain text
curl -F file=@lebenslauf.pdf "$B/cv"
curl -H 'Content-Type: application/json' -d '{"text":"Pflegefachkraft, 6 Jahre Intensivstation, München, B2"}' "$B/cv"

# scrape: target = {scope: all|regierungsbezirk|city|clinic|ats_type, values[]}; mode auto|adapter|firecrawl; credit cap
curl "$B/crawl/plan?scope=city&values=Regensburg,Straubing&mode=auto"          # preview first
curl -H 'Content-Type: application/json' -d '{"target":{"scope":"clinic","values":["16104"]},"mode":"auto","max_credits":40,"fetch_details":false}' "$B/crawl"
curl -H 'Content-Type: application/json' -d '{"target":{"scope":"ats_type","values":["softgarden"]},"mode":"adapter"}' "$B/crawl"
curl "$B/crawl/runs?limit=20"; curl "$B/crawl/runs/12"

# schedules: presets weekly_staggered | daily | weekdays | hourly | custom (cron)
curl "$B/schedules"
curl -H 'Content-Type: application/json' -d '{"name":"Oberpfalz nightly","preset":"custom","cron":"15 2 * * *","target":{"scope":"regierungsbezirk","values":["Oberpfalz"]},"mode":"adapter","enabled":true}' "$B/schedules"
curl -X PUT -H 'Content-Type: application/json' -d '{"enabled":false}' "$B/schedules/2"
curl -X POST "$B/schedules/2/run-now"; curl -X DELETE "$B/schedules/2"

# discover / refresh the career portal of a clinic (Firecrawl agent)
curl -H 'Content-Type: application/json' -d '{"max_credits":40}' "$B/clinics/16104/refetch-career"

# mechanics: explanation + source + patterns per rule; try one; run its tests
curl "$B/mechanics"
curl -H 'Content-Type: application/json' -d '{"title":"OP-Fachkraft (m/w/d)"}' "$B/mechanics/role_class/try"
curl -X POST "$B/mechanics/clinic_link/test"

# settings: patterns (all regexes), Firecrawl default budget
curl "$B/settings"
curl -X PUT -H 'Content-Type: application/json' -d @patterns.json "$B/settings/patterns"
```

### Billing (`GET /api/billing`)
Spend / usage report from the local ledger (`crawl_runs` + `firecrawl_usage` in `data/app.sqlite`), the Firecrawl account (`FA.credits()`) and the Exa seed cache. No auth.

Query: `window=today|24h|7d|30d|period|custom` (default `today`; `period` = the Firecrawl billing period, falls back to 30 d with `window.note` when the API is down), `from=ISO&to=ISO` for `custom` (date-only accepted, naive = UTC, `to` defaults to now), `granularity=auto|hour|day` (`auto`: hour up to 48 h, day beyond). Buckets are UTC, one per hour/day from floor(from) to floor(to) inclusive, empty buckets included. All numbers except `exa_*`, `pools` and `hist` are scoped to the window.

```bash
curl "$B/billing?window=7d"
curl "$B/billing?window=custom&from=2026-09-01&to=2026-09-08T00:00:00Z&granularity=day"
```

```json
{"window": {"key": "7d", "from": "2026-09-01T09:00:00+00:00", "to": "2026-09-08T09:00:00+00:00", "granularity": "day", "note": null},
 "price_per_credit": 0.0053, "currency": "USD",
 "series": [{"t": "2026-09-08T00:00:00+00:00", "credits_billable": 151, "credits_free": 0, "tokens": 1890, "runs": 11, "runs_free": 5, "new_postings": 66, "usd": 0.8003}],
 "totals": {"credits": 151, "credits_billable": 151, "credits_free": 0, "tokens": 1890, "usd": 0.8003, "runs": 39, "runs_free": 6, "runs_billable": 3,
            "runs_failed": 7, "runs_adapter": 23, "new_postings": 73, "cost_per_posting_usd": 0.011, "refills": 0, "exa_usd": 0.644, "exa_searches": 92},
 "by_kind": [{"kind": "firecrawl_jobs", "runs": 16, "credits": 151, "usd": 0.8003}, {"kind": "firecrawl_career", "runs": 0, "credits": 0, "usd": 0.0},
             {"kind": "adapter", "runs": 23, "credits": 0, "usd": 0.0}, {"kind": "exa", "runs": 92, "credits": null, "usd": 0.644}],
 "runs": [{"run_id": 39, "at": "2026-09-08T08:01:47+00:00", "clinic_id": "27501", "clinic": "Kreiskrankenhaus Rotthalmünster", "clinics": 1, "scope": "clinic",
           "value": "27501", "mode": "firecrawl", "trigger": "hunt", "status": "done", "credits": 77, "tokens": 1155, "rows": 11, "new": 11, "free": false, "usd": 0.4081, "error": null}],
 "pools": {"credits_remaining": 228, "credits_plan": 8000, "tokens_remaining": 3420, "tokens_plan": 120000, "period_start": "2026-08-19T20:01:50.000Z",
           "period_end": "2026-09-19T20:01:50.000Z", "free_runs_left_today": 0, "free_runs_per_day": 5, "agent_runs_today": 10, "error": null},
 "hist": {"credits_used_period": 222, "tokens_used_period": 3330},
 "exa_note": "cache has no timestamps: total over all 92 cached searches (file updated 2026-09-06T18:05:17+00:00), not scoped to the window"}
```

Rules: `usd = credits * price_per_credit` (`settings.firecrawl.eur_per_credit`, Hobby pricing, USD-derived -- label it USD/credit); a run is booked at `finished_at` (else `started_at` / `queued_at`); `free` = a Firecrawl run with status `done` and 0 credits (Firecrawl's 5 free daily agent runs; the ledger records the balance delta, so free runs carry 0 credits and `credits_free` is only non-zero when Firecrawl bills inside the allowance); `runs_billable` = credits > 0; `runs_failed` = status `failed`; `runs_adapter` = runs without Firecrawl; `new_postings` = `crawl_runs.n_new`; `cost_per_posting_usd = usd / new_postings` (null when 0); `refills` = `hunt_meta '<day>/refills'` summed over the window's days (0 when absent); `tokens` = Extract-token deltas from `firecrawl_usage`; `runs` newest first, at most 500 (totals count every run); ledger rows without a run row appear with `trigger: "ledger"` (free when 0 credits / 0 tokens inside the day's first 5 submissions). `by_kind.exa` and `totals.exa_*` are a whole-cache total (the cache has no timestamps; `exa_note` says so) and are not part of `totals.usd`. `pools` / `hist` come from `FA.credits()`; keys are null with `pools.error` set when the API is unreachable.

### Clinic row
`clinic_id, name, town, operator, landkreis, regierungsbezirk, versorgungsstufe, traegerart, beds, day_places, fachrichtungen[], status, website, careers_url, ats_type, fetch (adapter|firecrawl), fetch_label, routable, route_reason, walled, jobs_open, jobs_fresh, jobs_live, last_crawl_at, last_crawl_status, last_crawl_mode, career_profile`

### Job row
`posting_id, title, role_class, role_label, department_hint, department_raw, qualification_hint, employer, employer_class, clinic_id, clinic_name, regierungsbezirk, versorgungsstufe, traegerart, clinic_beds, city, plz, lat, lon, employment_types[], contract, start_date, first_published, first_seen, last_seen, status, verify_status, verified_at, source_url, external_url, source_codes[], n_observations, enr_housing, enr_tariff, enr_pay_grade, enr_contact_emails[], enr_bonus, enr_childcare, fresh`

### CV response
```json
{"profile":{"roles":["fachpflege"],"departments":["Intensiv/IMC"],"qualifications":["GuK"],"cities":["München"],
            "experience_years":6,"languages":["B2"],"skills":["Beatmung"],"keywords":["…"]},
 "matches":[{"posting_id":1,"title":"…","score":87,"why":["Intensiv/IMC","München","fachpflege"]}],"used_llm":false}
```

## PostgREST (public, read-only)

```
REST=https://klkxfvieaxpjlplloljn.supabase.co/rest/v1
H='-H apikey:<anon key, see /skill/SKILL.md> -H Accept-Profile:pflege_jobs'
```

| relation | use |
|---|---|
| `v_postings` | default: one row per posting with employer, role label, clinic columns, `source_codes`, `source_url` |
| `v_stats` | employer_class × role_class × status → n |
| `postings` | + `description`, `provenance`, `enr_housing_evidence`, `enr_requirements`, `enr_experience` |
| `posting_observations` | raw sightings, `payload` = original record |
| `clinics` / `v_clinics` / `v_clinic_portals` | register / + counts / + portal & ATS |
| `employers`, `role_classes`, `sources`, `crawl_runs` | lookups, monitoring |

Filter syntax: `col=eq.x` · `col=in.(a,b)` · `col=ilike.*text*` · `col=is.true` · `col=not.is.null` · `col=gte.2026-08-01` · arrays `employment_types=cs.{vollzeit}` · `or=(title.ilike.*intensiv*,department_hint.eq.Intensiv%2FIMC)` · `order=first_published.desc` · `limit=1000&offset=N` (max 1000/call) · count: `Prefer: count=exact` → `Content-Range: 0-999/N`. URL-encode `/` in values.

```bash
# default "clinic nursing jobs"
curl "$REST/v_postings?employer_class=eq.clinic&status=eq.open&verify_status=eq.live&select=title,employer,city,source_url&limit=50" $H
# per site
curl "$REST/v_clinics?regierungsbezirk=eq.Oberbayern&open_pflege_live=gt.0&order=open_pflege_live.desc" $H
# which portal / ATS
curl "$REST/v_clinic_portals?ats_type=eq.softgarden" $H
# one posting with text and provenance
curl "$REST/postings?posting_id=eq.123&select=title,description,provenance,enr_housing_evidence" $H
# count without rows
curl -I "$REST/postings?select=posting_id&status=eq.open" $H -H 'Prefer: count=exact'
```

Pitfalls: `v_postings.source_url`/`source_codes` are correlated subqueries — never combine `select=*` over thousands of rows with `count=exact` (statement timeout → 500); count on `postings`. Reads only; never write with the anon key; the ingest function needs `x-ingest-secret`.
