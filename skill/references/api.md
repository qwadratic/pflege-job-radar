# API reference

## A. App API — `https://pflege-board.exe.xyz/api` (JSON)

Board reads are open. The ops reads (`/settings`, `/coverage`, `/billing`, `/hunter`, `/inbox`,
`/firecrawl/*`, `/crawl/*`, `/schedules*`, `/campaign`) and every write need an owner session
(`POST /api/auth/login`) or an **agent key** in `X-Api-Key`, and answer 401 `application/problem+json`
otherwise. An agent key carries scopes; a wrong scope is 403 with a `"scope"` field naming the one to ask
for. Start at `GET /api/agent/manifest` (public) — it lists every scope, every route an agent key can open,
what each one costs and whether it touches the network.

| scope | unlocks |
|---|---|
| `read:board` | jobs, clinics, cities, facets, taxonomy, ontology, search, plan, stats (already public) |
| `read:ops` | `/crawl/runs`, `/crawl/plan`, `/crawl/estimate`, `/schedules*`, `/coverage`, `/inbox` |
| `write:crawl` | `POST /crawl` with `mode:"adapter"` only, `POST /crawl/runs/{id}/cancel`, `POST /inbox/drain` |
| `spend:firecrawl` | lifts `mode` to `auto`/`firecrawl`, unlocks `POST /clinics/{kez}/refetch-career` |
| `write:ingest:{posting,clinic,link,verify}` | `POST /ingest`, one scope per envelope family |

`/settings*`, `/hunter*`, `/scheduler*`, `/autocrawl/tick`, `/campaign`, `/schedules` writes,
`/mechanics/*/try|test`, `/billing*`, `/autopilot*` and `PUT /auth/password` are owner-session
only: no scope opens them, and a key there gets 401, not 403. `/stripe*` is not scopable either, but it is
not owner-gated: all three routes answer without any session — `GET /stripe/status`, `POST /stripe/checkout`,
`POST /stripe/webhook` (which authenticates with Stripe's own signature). The manifest's `session_only` list
is the generated version of this paragraph; read that, not this line, when it matters.

`validate_only: true` on `POST /crawl` and `POST /ingest` validates and writes nothing. `Idempotency-Key`
on `POST /crawl`, `POST /inbox/drain` and `POST /ingest` makes a retry replay the first answer instead of
spending twice (in flight → 409, same key with a different body → 422).

| method | path | returns |
|---|---|---|
| GET | `/stats` | `{open_jobs, fresh_jobs, clinics, clinics_active, clinics_with_jobs, clinics_with_ats, clinics_routable, active_runs, last_crawl{at,status}, firecrawl{remaining,plan}, next_autocrawl, snapshot_at, snapshot_error}` — an agent key counts as anonymous here, so `firecrawl` is only `{remaining, plan}`; the spend fields (`used_period`, `period_end`, `spent_by_app`, token pools) are added for an owner session only |
| GET | `/facets` | `{cities[{v,n}], job_cities, regierungsbezirk, landkreis, ats_type, traegerart, versorgungsstufe, status, fachrichtungen[{v,label,n}], size[{v,label,n}], role_class[{v,label,n}], department_hint, employment_types, contract, enr_tariff, verify_status, beds{min,max}, size_buckets}` |
| GET | `/clinics` | `{total, limit, offset, next_offset, rows[clinic]}` — filters: `q, city, regierungsbezirk, landkreis, ats_type, fetch, routable, traegerart, versorgungsstufe, status, fach, beds_min, beds_max, size, has_jobs, sort, limit, offset` (`routable=1|0`) |
| GET | `/cities?q=` | `[{city, regierungsbezirk, landkreis, clinics, jobs_open, jobs_fresh, ats_known}]` |
| GET | `/plan?q=&regierungsbezirk=&sort=` | `{total, limit, offset, next_offset, rows[every clinics.csv column], pdf_url, source, source_url}` — the Krankenhausplan as a table |
| GET | `/clinics/{kez}` | clinic + `jobs[]` + `runs[]` + `career_profile` |
| GET | `/jobs` | `{total, limit, offset, next_offset, rows[job]}` — filters: `clinic_id, q, role_class, department_hint, city, regierungsbezirk, employment_types, contract, housing, fresh_days, verify, sort, limit, offset` |
| GET | `/jobs/{id}` | job + `description`, `enr_*`, `observations[{source_code, source_url, observed_at}]` |
| GET | `/search?q=` | `{clinics[{clinic_id,name,town,score}], jobs[{posting_id,title,employer,city,clinic_id,score}], cities[]}` |
| POST | `/cv` | multipart `file` (pdf/docx/txt) or JSON `{"text"}` → `{profile{roles,departments,qualifications,cities,experience_years,languages,skills,keywords}, matches[job+score+why[]], used_llm}` |
| GET | `/crawl/plan?scope=&values=a,b&mode=` | `{clinics, boards, via_adapter, via_firecrawl, walled, est_credits, sample[]}` |
| GET | `/crawl/estimate?clinic_id=` | `{clinic_id, board_rows, definite_pflege, ambiguous, definite_excluded, confidence: none\|low\|high, note}` — free, title-only read of one clinic's board before running a real crawl. `confidence: none` + `board_rows: null` means no adapter route (would need a paid Firecrawl probe to know at all); `low` means too many titles have no nursing/non-nursing signal either way (`classify_role`'s own `no_pflege_token` case) to trust the count -- the real number is only known after a full crawl reads descriptions/department labels. Owner-only. |
| GET | `/firecrawl/prompts?clinic_id=` | `{model, default_max_credits, jobs:{prompt, schema}, career:{prompt, schema}}` — the live Firecrawl prompt templates (`pflege_jobs/sources/firecrawl_agent.py`), rendered for a real hospital when `clinic_id` is given, else generic placeholder text. Read-only, no network, no credits. Owner-only. |
| POST | `/crawl` | `{"target":{"scope":"all|regierungsbezirk|city|clinic|ats_type","values":[…]},"mode":"auto|adapter|firecrawl","max_credits":40,"fetch_details":false}` → `{run_id}` |
| GET | `/crawl/runs?limit=` / `/crawl/runs/{id}` | `[{run_id, started_at, finished_at, scope, value, mode, status, n_rows, n_new, credits_used, log_tail, clinic_ids}]` / + `log[]` |
| POST | `/crawl/runs/{id}/cancel` | → updated run row. `status=queued` → cancelled immediately, never runs. `status=running` → sets `cancel_requested`; `execute()` polls it between boards/Firecrawl clinics and stops there (best-effort, no hard kill mid-request -- whatever finished before the check is still ingested, final `status=cancelled`). 409 if already `done\|failed\|cancelled`. |
| POST | `/clinics/{kez}/refetch-career` | `{"max_credits":40}` → `{run_id}`; result in `career_profile` + `clinics.careers_url/ats_type` |
| GET/POST/PUT/DELETE | `/schedules[/{id}]` | `{id, name, enabled, preset (weekly_staggered|daily|weekdays|hourly|custom), cron, stagger_days, target, mode, max_credits, fetch_details, last_run_at, next_run_at, human}`; `POST /schedules/{id}/run-now` |
| GET | `/mechanics` | `[{id, title{de,en}, description{de,en}, stage, patterns_section, functions[{name,source,doc}], inputs[{name,label,example}], test_file, n_tests}]` |
| POST | `/mechanics/{id}/try`, `/mechanics/{id}/test` | `{inputs}` → `{result, rule}` · → `{passed, failed, output}` |
| GET | `/settings` | `{patterns, patterns_path, scheduler, firecrawl{default_max_credits, weekly_budget, eur_per_credit, max_eur_unknown_clinic, kill_switch_pct, reserve_credits, enabled, spent_7d}, hunter, feature_flags, feature_flags_info, feature_status_notes, agent_key}` — owner session only, no scope opens it |
| PUT | `/settings/patterns` | save (every `re` must compile; `config.reload()`) |
| GET | `/taxonomy`, `/ontology`, `/docs` | taxonomy.json, ontology.json, docs index |
| GET | `/agent/manifest` | `{scopes[], routes[{method,path,scope,scopes[],side_effects,cost,public}], public[{method,path}], session_only[{method,path,role}], envelope_types[{type,scope,kind,target}], auth{header,mint,idempotency_header,dry_run}, paging{envelope[],max_page_size,routes[],note,ndjson,stability}, note, links{}}` — public, generated from `app/auth.py`'s `AGENT_ROUTES` + `required_role()`, so it cannot drift from what the middleware enforces. Every `/api` route the app serves is in exactly one of the three lists: `routes` = a scope opens it (`scope` is `null` where the scope depends on the body — `POST /crawl`, `POST /ingest` — read `scopes[]` then; `public: true` means nothing gates it today and the scope is only for attribution), `public` = no session and no key needed, `session_only` = gated by `role` (`owner`/`member`) with no scope that opens it (401, not 403). HTML pages are not listed — see `/docs/auth.md` |
| GET | `/ingest/schemas` | `{envelope, types{…JSON Schema}}` generated from `pflege_jobs/schema.py` — public |
| POST | `/ingest` | one envelope or `{"events":[…]}` → `{accepted, total, validate_only, results[{id,type,status,inbox_id\|problem}]}`; 202 when every item came out the same way, 207 when they did not |
| GET | `/schedules/{id}/preview?day=` | `{schedule_id, target, mode, stagger_days, day, slice[], clinics, boards, via_adapter, via_firecrawl, est_credits, credits_left, next_run_at}` — the stagger slice a firing would take, without firing it |

Multi-value filters are comma lists (`city=München,Augsburg`, `fach=INN,CHI`, `size=L,XL`). `sort` = column or `-column`.
`/clinics` and `/jobs` also take `fields=a,b,c` (sparse projection; an unknown name is a 400) and answer
`Accept: application/x-ndjson` with one JSON object per line instead of the `{total, rows}` envelope.

**Paging — `/clinics`, `/jobs`, `/plan`, `/autopilot/*`.** Envelope is
`{total, limit, offset, next_offset, rows}`. **No maximum page size**: `?limit=999999` returns every matching
row (2725 open postings today), and `limit` in the response is always what you asked for — the server never
substitutes a smaller one. Until 2026-09-11 it clamped to 2000 and echoed `"limit": 2000`, so a truncated
sweep looked exactly like a satisfied one; if you cached that behaviour, drop it. **`next_offset` is the
end-of-list signal**: the offset to request next, `null` when this page reached the end. Do not infer the end
from `len(rows) < limit` — a `total` that is an exact multiple of `limit` ends on a full page. `limit=0` gives
zero rows and the `total`; a negative `limit`/`offset` is a 400. With `Accept: application/x-ndjson` the
envelope is gone, so the same numbers arrive in the `Content-Range` header: `rows <offset>-<last>/<total>`
(`rows */<total>` when empty). `GET /agent/manifest → paging` publishes all of this, generated from
`app/data.py`.

`offset` is a position, not a cursor. `/clinics` and `/jobs` come from a snapshot rebuilt when older than
600s and after every crawl, so a multi-page sweep that spans a rebuild can skip a row or return one twice —
rows that shift ahead of your offset are not detected. Either take the whole list in one call (that is what
the absent maximum is for), or bracket the sweep with `GET /stats → snapshot_at` and redo it if that value
moved.
Clinic row: `clinic_id, name, town, operator, landkreis, regierungsbezirk, versorgungsstufe, traegerart, beds, day_places, fachrichtungen[], status, website, careers_url, ats_type, fetch, fetch_label, routable, route_reason, walled, jobs_open, jobs_fresh, jobs_live, last_crawl_at, last_crawl_status, last_crawl_mode, career_profile`.
Job row: `v_postings` columns (below) + `fresh`.

### Ingestion envelope (`POST /ingest`)

```json
{"specversion":"1.0","id":"sg-36201-88413","source":"vendor-softgarden-v1","type":"posting.observed",
 "time":"2026-09-10T08:00:00Z","subject":"36201","data":{"source_url":"…","payload":{…}}}
```

`type` → `kind`, `source` → `collector`, `subject` → `payload.clinic_id`, `id` → `payload.event_id`.
`posting.observed` / `listing.observed` / `probe.ats_discovery` land in `inbox` and are drained by
`cli inbox`; `clinic.upserted`, `clinic_link.asserted`, `posting.verified`, `crawl_run.finished` go straight
to the edge ops. Dedupe is `(source, id)` in the request plus `source_url` against rows already in the inbox.
`clinic.upserted` must carry all 17 clinic columns — an omitted key writes NULL over what is stored — and a
partial payload is refused with 422 naming what is missing. `GET /ingest/schemas` has the JSON Schema per type.

## B. PostgREST — `https://klkxfvieaxpjlplloljn.supabase.co/rest/v1/<relation>`
Headers on every call: `apikey: <anon>` and `Accept-Profile: pflege_jobs`. Reads only — but not because of
RLS: the policies in `sql/001_schema.sql:253-254` are `using (true)` and narrow nothing, the read comes from
the blanket `grant select on all tables` at `:257`, and `inbox` has no RLS at all. **This door applies none of
the app API's redaction.** `enr_contact_emails` and the raw `description` come back verbatim here while
`/api/jobs` nulls and masks them below a member session. Personal data is a session question, not a transport
question: if the app refused it, PostgREST is not the answer — ask for a session. See `SKILL.md` (key block)
and `sql/011_PENDING_anon_scope.sql`, the written-but-unapplied grant fix.

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
| `inbox` | anon-writable **and anon-readable** intake (`kind`, `source_host`, `source_url`, `payload`, `collector`, `client_id`); no RLS, raw payloads included — `sql/011_PENDING_anon_scope.sql` would close the read side | submit crawler rows |
| `role_classes`, `sources`, `crawl_runs` | taxonomy + default grades, provenance, monitoring | labels, freshness |

### v_postings columns
`posting_id, title, role_class, role_label, is_pflege, qualification_hint, department_hint, department_raw, offer_kind, hauptberuf,
employer_id, employer, employer_class, employer_class_raw, employer_class_rule, clinic_id, clinic_name, clinic_operator, regierungsbezirk,
clinic_landkreis, versorgungsstufe, traegerart, clinic_beds, clinic_status, clinic_ats, clinic_match_rule, city, plz, lat, lon, in_bavaria,
employment_types[], shift_night_weekend, contract, fixed_term_months, start_date, salary_min, salary_max, salary_unit, first_published,
last_modified, first_seen, last_seen, status, verify_status, verify_http, verified_at, external_url, source_url, source_codes[],
n_observations, provenance, enr_housing, enr_tariff, enr_pay_grade, enr_contact_emails[], enr_bonus, enr_childcare`

`enr_contact_emails[]` is personal data and is **member-and-up on the app API only**; it is still selectable
here with the published key. Do not select it, and do not mine `description` for the same addresses — use
`GET /api/jobs` with a session. `scripts/query.py` dropped the column and its `--email` filter on 2026-09-11.

### Enums
- employer_class: clinic | unknown | non_clinic
- role_class: pflegefachkraft, fachpflege, pflegehelfer (legacy rows only — refused at ingest since 2026-09-07), praxisanleitung, leitung, apn_experte, hebamme, ota_ata, sonstige_pflege (refused: ausbildung, werkstudent_praktikum, nicht_pflege, pflegehelfer)
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
