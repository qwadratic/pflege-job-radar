# API

Two doors. **App API** (`/api`, same host as the board, JSON) for the UI and agents; **PostgREST** (Supabase,
read-only, anon key) for bulk/raw access. Board reads are open, and so are the two self-describing reads
(`/agent/manifest`, `/ingest/schemas`) and the rule catalogue `/mechanics`; the ops reads (`/crawl*`,
`/schedules*`, `/coverage`, `/inbox`, `/billing*`, `/hunter*`, `/firecrawl/credits|prompts`, `/settings`,
`/campaign`, `/autopilot*`) and every write need an owner session or a scoped agent key — see "Agentic API"
below and docs/auth.md for the full matrix. `GET /api/agent/manifest` is that matrix as JSON, generated from
the middleware's own route table and `required_role()`, so it cannot drift from what is enforced.

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
| GET | `/api/mechanics`, `/api/mechanics/{id}` | the rule mechanics: explanation (de/en), rule source, patterns section, inputs, test file. **Public on purpose** — it is the documentation of how a row gets classified, and it is the same source the repo publishes |
| POST | `/api/mechanics/{id}/try`, `/api/mechanics/{id}/test` | run one rule on your input / run its test file; owner-only, `test` shells out to pytest |
| GET/PUT | `/api/settings`, `/api/settings/patterns` | patterns.json (every regex), Firecrawl default budget |
| GET | `/api/billing` | spend report over a window: series (hour/day buckets), totals, by kind, runs, Firecrawl pools, Exa |
| GET | `/api/coverage` | per-adapter coverage breakdown (routable, boards, open/fresh jobs, last run), owner-only |
| GET | `/api/firecrawl/credits` | account balance + token pool + spend, owner-only |
| GET | `/api/firecrawl/prompts?clinic_id=` | the live jobs/career prompt templates + schemas, rendered for a real hospital when given one, else generic placeholder text; read-only, no credits, owner-only |
| GET/POST | `/api/campaign` | reingest-campaign routine state: safety_level, stopped, history; owner-only |
| GET | `/api/taxonomy`, `/api/ontology`, `/api/docs` | taxonomy.json, ontology.json, docs index |
| POST | `/api/ingest` | the single ingestion path: one CloudEvents-shaped envelope, or `{"events":[…]}` |
| GET | `/api/ingest/schemas` | JSON Schema per envelope type, generated from `pflege_jobs/schema.py`; public |
| GET | `/api/agent/manifest` | scopes, the scoped routes with side effects and cost, plus `public` and `session_only`: every `/api` route the app serves, in exactly one of the three lists. Envelope types, `paging` (the read routes' page envelope and its end-of-list signal, generated from `app/data.py`), links; public |
| GET | `/api/schedules/{id}/preview?day=` | what a schedule would do on that day (the stagger slice) without firing it |

List responses: `{"total": N, "limit": L, "offset": O, "next_offset": O2|null, "rows": [...]}`; comma lists for
multi-value filters. `GET /api/jobs` and `GET /api/clinics` also take `fields=a,b,c` (sparse projection; an
unknown name is a 400) and answer `Accept: application/x-ndjson` with one JSON object per line instead of the
envelope.

### Paging

Applies to `GET /api/jobs`, `GET /api/clinics`, `GET /api/plan` and every `GET /api/autopilot/*` list — one
helper (`app/data.py:page`), one contract. `GET /api/agent/manifest` publishes it as `paging`, generated from
that module.

**There is no maximum page size.** `?limit=999999` returns every matching row (2725 open postings today).
`limit` in the response is always the limit you asked for; the server never substitutes a smaller one.
Until 2026-09-11 it did: `?limit=999999` served 2000 rows and reported `"limit": 2000`, so the truncation was
indistinguishable from a caller that had asked for 2000 — and the NDJSON path, which drops the envelope,
gave no signal at all. Default page size when `limit` is absent: 200 (`/api/jobs`), 500 (`/api/clinics`),
1000 (`/api/plan`), 100 (autopilot).

**`next_offset` is the end-of-list signal.** It is the offset to ask for next, or `null` when this page
reached the end. Use it; do not infer the end from `len(rows) < limit` — a filter whose `total` is an exact
multiple of `limit` ends on a full page. `limit=0` is legal (zero rows, just the `total`), a negative `limit`
or `offset` is a 400 naming the parameter, and an `offset` past the end is an empty page with
`next_offset: null`.

With `Accept: application/x-ndjson` the same numbers come back in the `Content-Range` response header:
`rows <offset>-<last>/<total>`, or `rows */<total>` for an empty page.

```bash
curl "$B/jobs?limit=999999" | jq '.total, .limit, (.rows|length), .next_offset'   # 2725 2725… no truncation
O=0; while [ "$O" != null ]; do curl -s "$B/jobs?limit=500&offset=$O" > page.$O.json
       O=$(jq -r .next_offset page.$O.json); done                                 # sweep, ends on null
curl -sD- -H 'Accept: application/x-ndjson' "$B/jobs?limit=500" | grep -i content-range   # rows 0-499/2725
```

**`offset` is a position, not a cursor, and the list underneath moves.** `/api/jobs` and `/api/clinics` are
served from an in-memory snapshot that is rebuilt when it is older than 600s and after every crawl, and the
filter re-sorts on each call. So a sweep that spans a rebuild **can** skip rows or return one twice: if three
rows are inserted ahead of your position, page two starts three rows later than page one ended. Nothing here
detects that for you. What you can do: compare `GET /api/stats → snapshot_at` before and after the sweep (it
changes exactly when the underlying list was rebuilt) and redo the sweep if it moved, or ask for everything in
one call — there is no maximum page size precisely so that a whole-list read is one consistent answer.
`/api/plan` reads the registry CSV and `/api/autopilot/*` read SQLite, so both have the same hazard against
their own writers rather than against the snapshot clock.

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

# schedules: presets weekly_staggered | daily | weekdays | hourly | custom (cron). Reads need read:ops,
# writes are owner-only -- a mode=firecrawl schedule is recurring spend.
curl -H "X-Api-Key: $K" "$B/schedules"
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

### Agentic API — scoped keys, ingest, dry runs, idempotency

Reads stay open. Everything owner-gated can also be opened by an **agent key** (`X-Api-Key`, minted with
`PUT /api/settings/agent-key`, owner session only) that carries the right scope. `GET /api/agent/manifest`
is the machine-readable version of this section, generated from `app/auth.py:AGENT_ROUTES` and
`required_role()` — the same table and the same function the middleware enforces — and it partitions every
`/api` route the app serves:

| list | what it means |
|---|---|
| `routes` | a scope opens it. Each entry carries `scope`/`scopes`, `side_effects`, `cost`, and `public: true` when the middleware does not gate it today (the board reads: the scope is for attribution, it closes nothing) |
| `public` | no session, no key, nothing — `/agent/manifest`, `/ingest/schemas`, `/mechanics*`, `/me`, `/flags`, `/docs`, the webhooks with their own secret |
| `session_only` | gated with `role` (`owner`/`member`) and **no scope opens it** — a key gets 401 there, not 403, because no scope would help |

HTML pages are gated too (`/pro`, `/deck`, `/autopilot`) but are not in the manifest; docs/auth.md has them.

| scope | unlocks |
|---|---|
| `read:board` | jobs, clinics, cities, facets, taxonomy, ontology, search, plan, stats — already public; the scope exists for quota and attribution, not to close anything |
| `read:ops` | `GET /api/crawl/runs`, `/api/crawl/plan`, `/api/crawl/estimate`, `/api/schedules*`, `/api/coverage`, `/api/inbox` |
| `write:crawl` | `POST /api/crawl` **with `mode:"adapter"` only**, `POST /api/crawl/runs/{id}/cancel`, `POST /api/inbox/drain` |
| `spend:firecrawl` | lifts `mode` to `auto`/`firecrawl`, unlocks `POST /api/clinics/{kez}/refetch-career` |
| `write:ingest:{posting,clinic,link,verify}` | `POST /api/ingest`, one scope per envelope family |

`mode:"auto"` needs `spend:firecrawl` too: auto routes every non-routable or walled clinic to Firecrawl.
`GET /api/crawl/estimate` is labelled `network` in the manifest — it walks the clinic's live board.

**Never scopable, owner session only, no exceptions:** `/api/settings*`, `/api/hunter*`, `/api/scheduler*`,
`/api/autocrawl/tick`, `/api/campaign`, `/api/schedules` writes, `/api/mechanics/*/try|test`, `/api/billing*`,
`/api/autopilot*`, `PUT /api/auth/password` — the manifest's `session_only` list is the
generated, complete version of this line; read that, not this paragraph, when it matters.

`/api/stripe*` is **not** scopable either, but it is not owner-gated: all three routes answer anonymously and
sit in the manifest's `public` list — `GET /api/stripe/status`, `POST /api/stripe/checkout` and
`POST /api/stripe/webhook` (which authenticates with Stripe's own signature). The gated one in
`app/stripe_gate.py` is `/api/postings/{id}/closed`, `role: member` (GET and POST). docs/auth.md has the matrix.

A key minted without `scopes=` carries every scope — the reach the single pre-scope key had. `label=` names a
key so several agents can hold different ones; minting the same label again replaces only that key, `rotate=true`
revokes all of them, `DELETE …/agent-key?label=` revokes one. A wrong scope is `403 application/problem+json`
with `"type": "…#insufficient_scope"` and a `"scope"` extension naming what to ask for.

```bash
curl -X PUT "$B/settings/agent-key?label=recon&scopes=read:board,read:ops"   # owner session; key shown once
curl -H "X-Api-Key: $K" "$B/agent/manifest"
curl -H "X-Api-Key: $K" "$B/jobs?fields=posting_id,title,city&limit=500"
curl -H "X-Api-Key: $K" -H 'Accept: application/x-ndjson' "$B/clinics?fields=clinic_id,name,careers_url"
curl -H "X-Api-Key: $K" "$B/schedules/1/preview?day=2026-09-10"
```

#### `POST /api/ingest`

One envelope shape for every ontology entity, CloudEvents field names, no new table:

```json
{"specversion":"1.0","id":"sg-36201-88413","source":"vendor-softgarden-v1","type":"posting.observed",
 "time":"2026-09-10T08:00:00Z","subject":"36201","data":{"source_url":"…","payload":{…}}}
```

`type` → `kind`, `source` → `collector`, `subject` → `payload.clinic_id`, `id` → `payload.event_id`,
`client_id` from the key's label. `posting.observed` / `listing.observed` / `probe.ats_discovery` become rows in
`pflege_jobs.inbox` (`sql/010_inbox.sql`) and are drained by `cli inbox`; `clinic.upserted`, `clinic_link.asserted`,
`posting.verified` and `crawl_run.finished` bypass the inbox and hit the edge ops directly, as `pflege_jobs/sinks.py`
does. `clinic.upserted` must carry all 17 `CLINIC_SPEC` columns — the edge upsert assigns every column, so an
omitted key writes NULL over what is stored; a partial payload is refused with 422 naming the missing columns
(build the row with `pflege_jobs/registry.py: full_clinic_rows` / `merge_discovered`).

Dedupe is `(source, id)` inside the request plus the existing `source_url` dedupe against rows already in the
inbox (`app/crawl.py:_post_inbox`) — `inbox.source_url` is deliberately not unique. Response is
`{accepted, total, validate_only, results:[{id, type, status, inbox_id|problem}]}` with **202** when every item
came out the same way and **207** when they did not. `inbox_id` is null: the inbox post is batched with
`Prefer: return=minimal`, so PostgREST hands back no ids — `status` is the per-item answer.

```bash
curl -H "X-Api-Key: $K" -H 'Content-Type: application/json' \
     -H 'Idempotency-Key: 4f1c…' -d @events.json "$B/ingest"
curl "$B/ingest/schemas"
```

#### Dry runs and idempotency

`validate_only: true` on `POST /api/ingest` and `POST /api/crawl` (AIP-163) runs the full auth, validation and
credit estimate and writes nothing: no inbox row, no run row, no server-generated ids. `POST /api/crawl` answers
with the `GET /api/crawl/plan` payload plus `"validate_only": true, "queued": false`.

`Idempotency-Key` on `POST /api/crawl`, `POST /api/inbox/drain` and `POST /api/ingest`: a replay after the first
call finished returns the stored response, a copy still in flight is **409**, the same key with a different body is
**422**. The key is stored in the local `idem` table (`app/runs.py`) with the request fingerprint. This is a spend
fix — before it, a retried `POST /api/crawl` queued a second paying run. `validate_only` never claims a key.

**Keys are per caller**, namespaced by the agent key's label (or the session e-mail): picking the same key
string as another agent gets you your own row, never its stored response and never a 409/422 from its
traffic. Only the caller that claimed a key can replay, conflict with, or release it.

### Billing (`GET /api/billing`)
Spend / usage report from the local ledger (`crawl_runs` + `firecrawl_usage` in `data/app.sqlite`), the Firecrawl account (`FA.credits()`) and the Exa seed cache. Owner session only — no scope opens it (it is in the manifest's `session_only`).

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

**This door is not redacted.** The app API nulls `enr_contact_emails` and masks e-mail addresses in free text
below a `member` session (app/data.py:37, :53-70). PostgREST does neither: the anon key is published at
/skill/SKILL.md, sql/001_schema.sql:257 grants it `select on all tables in schema pflege_jobs` (:260 on future
tables too, and the RLS policies at :253-254 are `using (true)`, so they narrow nothing), and `inbox` has no
RLS at all (sql/010_inbox.sql:24-32) — raw crawler payloads, `collector` and `client_id` included. So
`enr_contact_emails`, `postings.description` and `posting_observations.payload` are world-readable today.
A grant fix is written and **not applied**: `sql/011_PENDING_anon_scope.sql` (revoke the blanket, column-level
select without the personal-data columns, RLS on `inbox`, exact rollback in its footer). It takes the app off
the anon key first (app/config.py:32), which is why applying it is Ivan's call, not a deploy step.

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

Personal data: do not select `enr_contact_emails` here and do not mine `description` for the same addresses — `GET /api/jobs` with a session is the door that decides who may see them. `skill/scripts/query.py` dropped the column and its `--email` filter on 2026-09-11.

Pitfalls: `v_postings.source_url`/`source_codes` are correlated subqueries — never combine `select=*` over thousands of rows with `count=exact` (statement timeout → 500); count on `postings`. Reads only; never write with the anon key; the ingest function needs `x-ingest-secret`.
