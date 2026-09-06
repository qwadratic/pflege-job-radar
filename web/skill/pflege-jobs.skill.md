---
name: pflege-jobs
description: Query, interpret, refresh and match against the pflege_jobs dataset — open, experienced-level nursing ("Pflege") job postings at hospital sites in Bavaria (Krankenhausplan KeZ registry, hospital career sites read by ATS adapters or the Firecrawl agent; app API at pflege-board.exe.xyz/api, public Supabase REST schema pflege_jobs). Use whenever the user mentions Pflege jobs, Pflegestellen, nursing postings, Bavarian clinics / Kliniken / Krankenhäuser, KeZ, the pflege_jobs schema, the pflege-board dashboard, crawling a clinic's career site, matching a CV or candidate to openings, or refreshing the job data — even if they only say "the jobs data", "the board", "how many postings".
---

# pflege-jobs

**Data rule: every answer, count, list or match comes from the app API (`/api`) or the Supabase REST API
(`pflege_jobs` schema) and nothing else.** Never read job boards or clinic sites to answer; those are
ingestion sources handled by crawls you can *trigger* (`POST /api/crawl`). The only outbound link you hand
to a user is a posting's `source_url`.

Read `references/api.md` before querying, `references/data-model.md` before interpreting fields,
`references/pipeline.md` before crawling or redeploying. Human docs: `/docs/overview.md`, `/docs/scraping.md`,
`/docs/api.md`, `/docs/performance.md`, `/docs/agents.md` on the board host.

## Where things are

| thing | value |
|---|---|
| app API | `https://pflege-board.exe.xyz/api` (JSON; stats, facets, clinics, jobs, search, cv, crawl, runs, settings) |
| Supabase project | `klkxfvieaxpjlplloljn`, schema `pflege_jobs`, REST `https://klkxfvieaxpjlplloljn.supabase.co/rest/v1/` + header `Accept-Profile: pflege_jobs` |
| anon key (public, read-only) | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imtsa3hmdmllYXhwamxwbGxvbGpuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM5MjEwOTgsImV4cCI6MjA4OTQ5NzA5OH0.S0ED1qBUyRDP0YSDVBQ0s_L5_tKdu4jsPsLmyUo1YCk` |
| board | `https://pflege-board.exe.xyz` (clinics → jobs; filters live in the URL hash) |
| ingest endpoint (write, secret) | `https://klkxfvieaxpjlplloljn.supabase.co/functions/v1/pflege-ingest` |
| postings | `v_postings` (read), `postings` (+description), `posting_observations` (evidence) |
| registry | `clinics` / `v_clinics` / `v_clinic_portals` — Krankenhausplan Bayern 2026, 407 sites (KeZ, Träger, Stufe, Bezirk, Betten, Fachrichtungen, careers_url, ats_type) |
| taxonomy / patterns | `/api/taxonomy` (code → label), `/api/settings` → `patterns` (every regex the classifier uses; editable) |
| helper script | `scripts/query.py` (PostgREST paging → json/csv/md) |
| source code | https://github.com/qwadratic/pflege-job-radar |

Scale: **do not hard-code numbers — call `GET /api/stats`** (open_jobs, fresh_jobs, clinics, clinics_with_jobs,
clinics_routable, last_crawl, firecrawl credits). History: before the 2026-09-06 purge the dataset held 7,637
open postings from four sources; now only hospital career sites count.

Three sources, precedence when they disagree: `krankenhausplan` (10, identity only) > `employer_ats` (20,
adapters) = `firecrawl_agent` (25, agent-read career sites). `clinics.ats_type` names the adapter; ~175 of 407 sites
are labelled, `dvinci` (11) has no adapter, ~230 sites are unlabeled → `routable=false` with a `route_reason`.
Shared boards (Schön 7, kbo 9, Südostbayern 3, RHÖN 2) are fetched once and spread by link-clinics: a per-site
count is a lower bound for group members.

## Decide what the user needs

1. **Numbers or lists** → `GET /api/jobs` / `GET /api/clinics` (filters in `references/api.md`), or PostgREST
   `v_postings` for bulk. Default: `status=open`, `verify=live`, hospital-linked. Say which filters you used.
2. **A specific posting** → `GET /api/jobs/{id}` (description, `enr_*`, observations) — link `source_url`.
3. **Which clinics / structure** ("Oberbayern", "Maximalversorger", "öffentlich", "> 500 Betten", "with INN+CHI")
   → `GET /api/clinics?...` (`regierungsbezirk`, `versorgungsstufe`, `traegerart`, `beds_min/max`, `size`, `fach`, `has_jobs`, `routable`).
4. **Fuzzy / typo search** → `GET /api/search?q=` (clinics, jobs, cities).
5. **Candidate ↔ posting** → `POST /api/cv` (file or `{"text":…}`) → `matches[]` with `score` and `why[]`. Anonymise; never send names elsewhere.
6. **Fresh data for a clinic / city / bezirk** → `POST /api/crawl {"scope","value","mode":"auto","max_credits":40}` → poll
   `GET /api/crawl/runs/{run_id}` → re-query. `mode=firecrawl` costs credits (see `/api/stats.firecrawl`).
7. **Unknown career portal** → `POST /api/clinics/{kez}/refetch-career` (Firecrawl discovery: portal, ATS, filters, categories).
8. **Rule change** (new keyword, new department pattern) → `GET /api/settings` → edit `patterns` → `PUT /api/settings/patterns`; then `python -m pflege_jobs.cli renormalize` for stored rows.
9. **"Is X a clinic?" / wrong class** → `employers.class_rule`; manual override SQL in `references/data-model.md`.

## Interpretation rules

- `employer_class`: `clinic` = linked to a KeZ or keyword-clinic; `unknown` = **unclassified, not "not a hospital"**; `non_clinic` = Altenhilfe/ambulant/agency.
- The DB is **experienced-nursing-only**: `nicht_pflege`, `ausbildung`, `werkstudent_praktikum` are refused at ingest (`patterns.json.excluded_role_classes`). `pflegehelfer` is included. `OP-Fachkraft` = `fachpflege`; `MFA`, `Stationsassistenz` = not nursing.
- `role_class`, `department_hint`, `qualification_hint` come from title/department text; the rule that fired is in `role_rule`. Null hint = not stated, not none.
- `enr_*` exist only where a description was fetched; `enr_housing=false` = not mentioned, null = no text.
- `status=open` = seen in the latest crawl of its board; `verify_status`: `live` (re-fetched, title found), `gone` (→ expired), `blocked` (bot wall), `error` (JS page / 5xx). Never call `gone` open.
- `fresh` = `first_published`/`first_seen` within 7 days. `source_codes` ∈ {employer_ats, firecrawl_agent}; `provenance` = which source supplied each field.
- `routable=false` clinics may have jobs we cannot see — say so; offer a Firecrawl crawl.
- Count clinics by `clinic_id`, never by employer name.

## Query recipes

```bash
B=https://pflege-board.exe.xyz/api
curl "$B/stats"                                                     # headline numbers
curl "$B/jobs?department_hint=Intensiv%2FIMC&regierungsbezirk=Oberbayern&verify=live&limit=1"   # total in body
curl "$B/clinics?size=L,XL&traegerart=oeffentlich&has_jobs=0"      # big public sites without visible jobs
curl "$B/search?q=klinkum%20augsbrg"                                # fuzzy
curl -F file=@cv.pdf "$B/cv"                                        # CV match
# PostgREST bulk
REST=https://klkxfvieaxpjlplloljn.supabase.co/rest/v1; H='-H apikey:'"$ANON"' -H Accept-Profile:pflege_jobs'
curl "$REST/v_postings?employer_class=eq.clinic&status=eq.open&verify_status=eq.live&select=title,employer,city,source_url&limit=1000&offset=0" $H
curl -I "$REST/postings?select=posting_id&status=eq.open" $H -H 'Prefer: count=exact'      # Content-Range: 0-0/N
```

## Answer format

Lead with the number and the filter ("312 live nursing jobs at 87 Bavarian hospital sites, ICU, Oberbayern").
Cite `source_url` per posting. Flag `last_seen` > 7 days, `verify_status` ≠ live, and clinics with `routable=false`.

## Pitfalls

- `v_postings.source_url`/`source_codes` are correlated subqueries: no `count=exact` over the whole view (500); count on `postings`.
- PostgREST caps at 1000 rows — page. The app API pages with `limit/offset` and returns `total`.
- Same clinic, same title several times = different wards; do not dedupe by title.
- Firecrawl credits are finite (`/api/stats.firecrawl.remaining`); always pass `max_credits`.
- Never write with the anon key; the ingest secret is never sent anywhere but the ingest function.


---

# API reference

## A. App API — `https://pflege-board.exe.xyz/api` (JSON, no auth for reads)

| method | path | returns |
|---|---|---|
| GET | `/stats` | `{open_jobs, fresh_jobs, clinics, clinics_with_jobs, clinics_routable, last_crawl{at,status}, firecrawl{remaining,plan,used_period,period_end,spent_by_app}, next_autocrawl}` |
| GET | `/facets` | `{cities[{v,n}], regierungsbezirk, landkreis, ats_type, traegerart, versorgungsstufe, status, fachrichtungen[{v,label,n}], role_class[{v,label,n}], department_hint, employment_types, contract, enr_tariff, beds{min,max}, size_buckets}` |
| GET | `/clinics` | `{total, rows[clinic]}` — filters: `q, city, regierungsbezirk, landkreis, ats_type, traegerart, versorgungsstufe, status, fach, beds_min, beds_max, size, has_jobs, routable, sort, limit, offset` |
| GET | `/clinics/{kez}` | clinic + `jobs[]` + `runs[]` + `career_profile` |
| GET | `/jobs` | `{total, rows[job]}` — filters: `clinic_id, q, role_class, department_hint, city, regierungsbezirk, employment_types, contract, housing, fresh_days, verify, sort, limit, offset` |
| GET | `/jobs/{id}` | job + `description`, `enr_*`, `observations[{source_code, source_url, observed_at}]` |
| GET | `/search?q=` | `{clinics[{clinic_id,name,town,score}], jobs[{posting_id,title,employer,city,clinic_id,score}], cities[]}` |
| POST | `/cv` | multipart `file` (pdf/docx/txt) or JSON `{"text"}` → `{profile{roles,departments,qualifications,cities,experience_years,languages,skills,keywords}, matches[job+score+why[]], used_llm}` |
| POST | `/crawl` | `{"scope":"clinic|city|regierungsbezirk|job|board|all","value":…,"mode":"auto|adapter|firecrawl","max_credits":40}` → `{run_id}` |
| GET | `/crawl/runs?limit=` / `/crawl/runs/{id}` | `[{run_id, started_at, finished_at, scope, value, mode, status, n_rows, n_new, credits_used, log_tail, clinic_ids}]` / + `log[]` |
| POST | `/clinics/{kez}/refetch-career` | `{"max_credits":40}` → `{run_id}`; result in `career_profile` + `clinics.careers_url/ats_type` |
| GET | `/settings` | `{patterns, schedule{enabled,weekday,hour,batches,mode,firecrawl_weekly_budget}, firecrawl{default_max_credits}}` |
| PUT | `/settings/patterns`, `/settings/schedule` | save (patterns: every `re` must compile) |
| GET | `/taxonomy`, `/ontology`, `/docs` | taxonomy.json, ontology.json, docs index |

Multi-value filters are comma lists (`city=München,Augsburg`, `fach=INN,CHI`, `size=L,XL`). `sort` = column or `-column`.
Clinic row: `clinic_id, name, town, operator, landkreis, regierungsbezirk, versorgungsstufe, traegerart, beds, day_places, fachrichtungen[], status, website, careers_url, ats_type, routable, route_reason, walled, jobs_open, jobs_fresh, jobs_live, last_crawl_at, last_crawl_status, last_crawl_mode, career_profile`.
Job row: `v_postings` columns (below) + `fresh`.

## B. PostgREST — `https://klkxfvieaxpjlplloljn.supabase.co/rest/v1/<relation>`
Headers on every call: `apikey: <anon>` and `Accept-Profile: pflege_jobs`. Reads only (RLS).

| relation | rows | use |
|---|---|---|
| `v_postings` | one per posting, joined with employer, role label, clinic columns, `source_codes`, `source_url` | default for lists/counts |
| `v_stats` | employer_class × role_class × status → n | quick aggregates |
| `postings` | golden record incl. `description`, `provenance`, `enr_housing_evidence`, `enr_requirements`, `enr_experience`, `salary_*` | detail view |
| `posting_observations` | raw per-source rows, `payload` jsonb = original record | audits, re-classification |
| `employers` | `employer_class`, `class_rule`, `class_source` | who is a clinic |
| `clinics` | KeZ registry: name, town, operator, landkreis, regierungsbezirk, status, versorgungsstufe, traegerart, beds, day_places, fachrichtungen, website, careers_url, ats_type | structure |
| `v_clinics` | one per site with `open_pflege_postings`, `open_pflege_live`, `employer_names[]` | per-site counts |
| `v_clinic_portals` | clinic → website, careers_url, ats_type, has_live_site_source, open_pflege_live | which portal / ATS |
| `inbox` | anon-writable intake (`kind`, `source_host`, `source_url`, `payload`, `collector`, `client_id`) | submit crawler rows |
| `role_classes`, `sources`, `crawl_runs` | taxonomy + default grades, provenance, monitoring | labels, freshness |

### v_postings columns
`posting_id, title, role_class, role_label, is_pflege, qualification_hint, department_hint, department_raw, offer_kind, hauptberuf,
employer_id, employer, employer_class, employer_class_raw, employer_class_rule, clinic_id, clinic_name, clinic_operator, regierungsbezirk,
clinic_landkreis, versorgungsstufe, traegerart, clinic_beds, clinic_status, clinic_ats, clinic_match_rule, city, plz, lat, lon, in_bavaria,
employment_types[], shift_night_weekend, contract, fixed_term_months, start_date, salary_min, salary_max, salary_unit, first_published,
last_modified, first_seen, last_seen, status, verify_status, verify_http, verified_at, external_url, source_url, source_codes[],
n_observations, provenance, enr_housing, enr_tariff, enr_pay_grade, enr_contact_emails[], enr_bonus, enr_childcare`

### Enums
- employer_class: clinic | unknown | non_clinic
- role_class: pflegefachkraft, fachpflege, pflegehelfer, praxisanleitung, leitung, apn_experte, hebamme, ota_ata, sonstige_pflege (refused: ausbildung, werkstudent_praktikum, nicht_pflege)
- qualification_hint: GuK | GKiK | Altenpflege | generalistisch | null
- department_hint: Intensiv/IMC, Anästhesie, OP, Notaufnahme, Psychiatrie, Pädiatrie/Neonatologie, Geburtshilfe, Onkologie, Kardiologie, Neurologie, Geriatrie, Dialyse/Nephrologie, Chirurgie/Orthopädie, Innere Medizin, Reha, Springerpool, Ambulanz/Tagesklinik, null
- contract: UNBEFRISTET | BEFRISTET | null · employment_types: vollzeit, teilzeit, minijob
- enr_tariff: TVöD | TV-L | AVR Caritas | AVR Diakonie | Haustarif | AVR (unspecified) | null · enr_pay_grade: P7…P16, KR7…, EG13 | null
- status: open | expired · verify_status: live | gone | blocked | error | null
- regierungsbezirk: Oberbayern | Niederbayern | Oberpfalz | Oberfranken | Mittelfranken | Unterfranken | Schwaben
- versorgungsstufe: Grundversorgung (I) | Schwerpunkt (II) | Maximalversorgung (III) | Fachkrankenhaus | -
- traegerart: oeffentlich | freigemeinnuetzig | privat · clinic status: Plan-KH | Vertrags-KH | HS-Klinik | Bedarfsfeststellung | nicht_mehr_im_plan
- source_codes: employer_ats | firecrawl_agent (array; `source_codes=cs.{employer_ats}`)
- fachrichtungen (clinics, `|`-separated): AUG CHI GUG GYN HCH HNO HUG INN KCH KIN KJP MKG NCH NEU NUK PSO PSY SON STR URO HD — labels in `/api/taxonomy`

### Filter syntax
`col=eq.x`, `col=in.(a,b)`, `col=ilike.*text*`, `col=is.true`, `col=not.is.null`, `col=gte.2026-08-01`, arrays `employment_types=cs.{vollzeit}`,
OR `or=(title.ilike.*intensiv*,department_hint.eq.Intensiv%2FIMC)`, `order=first_published.desc`, `limit=1000&offset=0` (max 1000/call),
exact count: `Prefer: count=exact` → `Content-Range: 0-999/N`. URL-encode `/` in values.

### Examples
- Live clinic nursing posts (default): `v_postings?employer_class=eq.clinic&status=eq.open&verify_status=eq.live`
- Sites with live posts in Oberbayern: `v_clinics?regierungsbezirk=eq.Oberbayern&open_pflege_live=gt.0&order=open_pflege_live.desc`
- Leadership roles: `v_postings?employer_class=eq.clinic&role_class=eq.leitung`
- Munich, full-time, last 14 days: `v_postings?city=ilike.*münchen*&employment_types=cs.{vollzeit}&first_published=gte.<date>`
- Big sites with INN: `clinics?beds=gte.500&fachrichtungen=ilike.*INN*`
- One posting with text: `postings?posting_id=eq.123&select=title,description,provenance,enr_housing_evidence`
- Evidence: `posting_observations?posting_id=eq.123&select=source_id,source_ref,source_url,observed_at,payload`


---

# Data model and rules

Graph: `/docs/ontology.json` (rendered on the board's Docs page). Prose: `/docs/overview.md`.

## Tables (schema `pflege_jobs`)
- `sources(source_id, code, kind, precedence)` — 10 krankenhausplan(1), 20 employer_ats(2), 25 firecrawl_agent(2). Lower wins field by field. (30 arbeitsagentur and 40 aggregator were deleted on 2026-09-06 with their observations and the postings that had no other evidence.)
- `clinics(clinic_id=KeZ, name, town, operator, landkreis, regierungsbezirk, status, versorgungsstufe, traegerart, beds, day_places, fachrichtungen, parse_quality, source, website, careers_url, ats_type, employer_id)` — Krankenhausplan Bayern 2026, 407 sites (399 + 8 `nicht_mehr_im_plan`). `website/careers_url/ats_type` come from discovery (census, Firecrawl refetch-career), not the PDF. Anything writing this table must send **complete rows** (`registry.full_clinic_rows()`): the ingest upsert assigns every column it receives.
- `employers(employer_id, name_norm UNIQUE, name_display, employer_class, class_rule, class_source)` — name_norm = lowercase, legal forms stripped. Conservative: "Klinikum X" and "Klinikum X Personalabteilung" stay separate.
- `inbox(kind, source_host, source_url, payload, collector, client_id, process_note)` — raw crawler rows; `collector` starting with `firecrawl` → source 25, otherwise 20.
- `posting_observations` — identity `(source_id, source_ref)`; every crawl upserts here. Extracted fields + `payload` jsonb + `fuzzy_key` + `content_hash` + `details_fetched_at`.
- `postings` — golden record: `fuzzy_key` (sha1 of normalised title | employer_norm | PLZ), `clinic_id/clinic_match_rule/clinic_match_score`, `provenance` jsonb, `n_observations`, `first_seen/last_seen`, `status`, `verify_*`.
- `role_classes` — taxonomy; grade columns are inferred defaults.
- `crawl_runs` — one row per run/stage (`source_id`, counts, `slice_counts`, `notes`); the app mirrors its own runs here.
- App-side (SQLite `data/app.sqlite`): `crawl_runs`, `run_log`, `career_profiles(clinic_id, profile, fetched_at, credits_used)`, `settings`, `firecrawl_usage`.

## Files
- `pflege_jobs/patterns.json` — all regexes: `employer.clinic[]`, `employer.non_clinic[]`, `role.rules[]` (ordered), `qualification[]`, `department[]`, `enrichment.*`, `cv.*`, `excluded_role_classes`. Edit via `PUT /api/settings/patterns` (validated, hot-reloaded) or in the repo; `config.reload()`.
- `data/registry/taxonomy.json` — Fachrichtungen codes, Versorgungsstufe, Trägerart, status, size buckets, ats_type labels (`/api/taxonomy`).

## resolve_postings() (after every load)
1. Link: unlinked observation → if exactly one posting shares `fuzzy_key` AND that posting has no observation from the same source → link; otherwise new posting. Same-source rows never merge.
2. Fields: per field, first non-null ordered by `precedence asc, observed_at desc`. `first_seen=min`, `last_seen=max`, `provenance{field: source_code}`.
3. `mark_expired(p_days)` → `status='expired'` where `last_seen < now() - p_days`.

## link-clinics (postings → KeZ)
Six ordered rules: R1 exact name, R2 operator, R3/R4 token overlap + town, R5 loose, R6 operator with several sites in one town → preferred/largest site, rule stored as `R6_ambiguous_sites:…`. `clinic_match_rule='manual'` is never touched.

## link-cross (dedupe)
Same-source URL variants (canonical_ref) merge; cross-source within the same `clinic_id` + city when titles are similar (Jaccard ≥ 0.6 or overlap ≥ 0.9 with ≥ 3 shared tokens). Observations move, earliest `first_seen` kept.

## Classification (patterns.json; rule recorded in `*_rule` columns)
Employer: `employer.clinic` vs `employer.non_clinic` groups. Conflict matrix: clinic + weak group (verband, sonstige) → clinic; clinic + strong group (altenhilfe, ambulant, wohnen, agentur, brand_nc) → unknown; no match → unknown.
Role: `pflege_gate` token required → `nicht_pflege` if `role.nicht_pflege` matches and the title has no `strong_pflege` token → ordered `role.rules` (werkstudent_praktikum, ausbildung, hebamme, ota_ata, praxisanleitung, leitung, apn_experte, fachpflege, pflegehelfer, pflegefachkraft) → `fallback` sonstige_pflege. Leadership needs a word start (`(?<![a-zäöüß])leitung\b`).
Intake gate: `excluded_role_classes` (nicht_pflege, ausbildung, werkstudent_praktikum) are refused by every sink (`sinks.only_pflege`).
Enrichment (`enrichment.*`): housing, tariff, pay grade, contact emails, language level, bonus, childcare, recognition mention, requirements/experience excerpts.
CV (`cv.*`): experience years, language levels, skill tags → profile → score against jobs (role, department, city, qualification, skills).

## Verify
`verify_status`: live (200 + title tokens found) · gone (404/410 or "nicht mehr verfügbar" → `status=expired`) · blocked (401/403/429) · error (5xx, timeout, JS page). Only `gone` expires.

## Manual override (privileged SQL / service key)
```sql
update pflege_jobs.employers set employer_class='clinic', class_rule='manual:<why>', class_source='manual' where name_display ilike 'Riedel & Pfeuffer%';
select * from pflege_jobs.resolve_postings();
```
`class_source='manual'` survives every later load.

## Known limits
Coverage = what the adapters and the agent can read: ~230 sites are unlabeled (`routable=false`), dvinci has no adapter. `unknown` employers are honest. Descriptions exist only where a detail page was fetched.


---

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
