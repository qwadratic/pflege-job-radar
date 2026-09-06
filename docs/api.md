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
| GET | `/api/crawl/runs`, `/api/crawl/runs/{id}` | run status + log |
| POST | `/api/clinics/{kez}/refetch-career` | Firecrawl discovery of the career portal |
| GET/POST/PUT/DELETE | `/api/schedules[/{id}]`, `POST /api/schedules/{id}/run-now` | cron / preset schedules with target, mode, budget, enabled |
| GET/POST | `/api/mechanics`, `/api/mechanics/{id}/try`, `/api/mechanics/{id}/test` | the ten rule mechanics: explanation, source, patterns, try-it, run tests |
| GET/PUT | `/api/settings`, `/api/settings/patterns` | patterns.json (every regex), Firecrawl default budget |
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
