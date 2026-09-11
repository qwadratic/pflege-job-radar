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
`/docs/api.md`, `/docs/performance.md` on the board host (Docs tab).

**Work API-level.** Everything above (`/api/*`, PostgREST, `POST /api/crawl`) is the interface. Do not open
`/` or `/pro` in a browser, screenshot it, or drive it as a UI to get an answer — every number, list or
match it shows comes from the same API you already have. Load the board frontend only when a human
explicitly asks you to look at the frontend itself (a UI bug, a layout question, "does the Pro page render
right") — not as a way to read data.

## Where things are

| thing | value |
|---|---|
| app API | `https://pflege-board.exe.xyz/api` (JSON; stats, facets, clinics, cities, plan, jobs, search, cv, crawl + plan, runs, schedules, mechanics, settings) |
| Supabase project | `klkxfvieaxpjlplloljn`, schema `pflege_jobs`, REST `https://klkxfvieaxpjlplloljn.supabase.co/rest/v1/` + header `Accept-Profile: pflege_jobs` |
| anon key (published on purpose) | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imtsa3hmdmllYXhwamxwbGxvbGpuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM5MjEwOTgsImV4cCI6MjA4OTQ5NzA5OH0.S0ED1qBUyRDP0YSDVBQ0s_L5_tKdu4jsPsLmyUo1YCk` |
| board | `https://pflege-board.exe.xyz` (clinics → jobs; filters live in the URL hash) |
| board, Pro dashboard | `https://pflege-board.exe.xyz/pro` (+ Plan, Scrape/schedules/runs, Docs, Settings) |
| ingest endpoint (write, secret) | `https://klkxfvieaxpjlplloljn.supabase.co/functions/v1/pflege-ingest` |
| postings | `v_postings` (read), `postings` (+description), `posting_observations` (evidence) |
| registry | `clinics` / `v_clinics` / `v_clinic_portals` — Krankenhausplan Bayern 2026, 407 sites (KeZ, Träger, Stufe, Bezirk, Betten, Fachrichtungen, careers_url, ats_type) |
| taxonomy / patterns | `/api/taxonomy` (code → label), `/api/settings` → `patterns` (every regex the classifier uses; editable) |
| helper script | `skill/scripts/query.py` (PostgREST paging → json/csv/md; no personal-data column or filter; served copy at `web/skill/query.py`) |
| source code | https://github.com/qwadratic/pflege-job-radar |

**What that key can and cannot do** (audited against `sql/*.sql` and the live project 2026-09-10, re-checked
against `sql/*.sql` and a throwaway replay 2026-09-11 — it is
`role: anon`, `exp` 2036, and this file is served to anyone at `/skill/SKILL.md`, so treat it as world-known):

- **CAN read every table in schema `pflege_jobs`, not only the seven with a `public_read` policy.**
  `sql/001_schema.sql:257` grants `select on all tables` to `anon` and `:260` grants it on future tables too;
  the RLS policies at `:253-254` are `for select … using (true)`, so they narrow nothing. `pflege_jobs.inbox`
  has no RLS at all (`sql/010_inbox.sql:24-32`) and is readable with this key — raw crawler payloads,
  `collector`, `client_id`, `process_note`. Verified live 2026-09-10; re-verified 2026-09-11 by replaying
  `sql/001` + `sql/010` into a throwaway Postgres and reading all of it back as role `anon`.
- **CAN read the recruiter e-mail addresses the app API redacts, and the addresses in the ad text too.**
  Below a member session the app nulls `enr_contact_emails` (`app/data.py:37`) *and* masks every e-mail-shaped
  substring in the free text it returns (`app/data.py:53-70`) — because 569 of the 572 postings that carry an
  address in that column carry the same address in `description` (`tests/test_auth.py:588-589`). PostgREST
  applies neither: with this key `v_postings.enr_contact_emails`, `postings.description`,
  `postings.enr_housing_evidence` and `posting_observations.payload` come back verbatim. **The app-side
  redaction is a control on the app door only; PostgREST is a second, un-redacted door and this key opens it.**
  `scripts/query.py` no longer selects that column and its `--email` filter was removed on 2026-09-11, so the
  published helper stops handing it over — that is a change of what we *advertise*, not of what the key *can
  do*. Closing it means a grant change: `sql/011_PENDING_anon_scope.sql` is written, replayed against a
  throwaway Postgres, **and deliberately not applied** — it takes the app off the anon key first, which is
  Ivan's call (see that file's header).
- **CANNOT update or delete anything.** No `update`/`delete` grant exists for `anon` in `sql/*.sql`, RLS on
  the seven policy tables is select-only, and a live `DELETE /rest/v1/inbox` with this key answers
  `42501 permission denied for table inbox`. Verified live.
- **INSERT into `pflege_jobs.inbox` is not ruled out.** `sql/010_inbox.sql:24-32` records that RLS is off on
  that table and that *something outside `sql/`* grants `anon` an INSERT there (confirmed live 2026-09-08).
  If that grant is on the `anon` DB role, this key carries it, and one unauthenticated POST becomes an inbox
  row that the drain turns into a `posting_observation`. Proving it means writing a junk row into
  production, which this audit refused to do — so: **assume it is writable until someone proves otherwise.**
- **No quota protection.** Reads are unmetered and unauthenticated; anyone can page the whole board (2.6k
  postings, 7.4k inbox rows) as often as they like and spend the project's egress.
- Rotating it is not a fix on its own: the point of publishing it is that agents can read the dataset. What
  it must not be able to do is write.
- **Open, for Ivan:** keep publishing one shared anon key at all, or move agents onto scoped per-agent keys
  and stop. `sql/011_PENDING_anon_scope.sql` only narrows what the shared key reaches; it does not answer
  that question, and nothing in this file should be read as if it had been answered.

Scale: **do not hard-code numbers — call `GET /api/stats`** (open_jobs, fresh_jobs, clinics, clinics_with_jobs,
clinics_routable, last_crawl). `firecrawl` in that response is `null` without a session (2026-09-10: the
operator's paid credit balance is not public); for spend planning read `credits_left` from
`GET /api/crawl/plan` (needs `read:ops`), or fire `POST /api/crawl` and read the 409 it answers when the
budget is short. History: before the 2026-09-06 purge the dataset held 7,637
open postings from four sources; now only hospital career sites count.

Three sources, precedence when they disagree: `krankenhausplan` (10, identity only) > `employer_ats` (20,
adapters) = `firecrawl_agent` (25, agent-read career sites). `clinics.ats_type` names the adapter; 294 of 407 sites
are labelled, `dvinci` (13) now has an adapter (`crawl_dvinci`) and is fully routable. 113 sites have no `ats_type`
label, but a generic `wp_jobs` fallback adapter routes many of those anyway — `ats_type=self_hosted` (47
sites, no vendor fingerprint) also routes through it now, so only 11 sites are actually
`fetch=firecrawl` (mostly `coveto` and a few unlabeled) → `routable=false` with a `route_reason`.
Shared boards (Schön 12, kbo — split across 5+ boards: kbo-iak 11, kbo-heckscher-klinikum 9, kbo-lmk 5, kbo-isk 4,
umantis 2, 33 kbo sites total, Südostbayern 4, RHÖN 3) are fetched once per board and spread by link-clinics: a
per-site count is a lower bound for group members.

## Rules

1. Read from `/api/*` or PostgREST `v_postings` — never from job boards or clinic sites.
2. Default filter = `status=open`, `verify=live`, hospital-linked (`clinic_id` set). Say which filters you used.
3. `employer_class=unknown` means **unclassified**, not "not a hospital".
4. Experienced-only database: no trainees, students, interns, non-nursing. `pflegehelfer` (assistants) is excluded too as of 2026-09-07 — only certified roles remain.
5. Count clinics by `clinic_id`, never by employer name. Shared boards: a per-site count is a lower bound for group members.
6. `status=open` = seen in the last scrape; `verify_status=live` = re-fetched. Never call a `gone` posting open.
7. A clinic with `fetch=firecrawl` (no adapter) and no Firecrawl run yet may have jobs we cannot see — say so.

## Filters (meaning)

| filter | on | values |
|---|---|---|
| `city` | clinics: `town`, jobs: `city` | comma list; `/api/facets` lists them |
| `regierungsbezirk` | both | Oberbayern, Niederbayern, Oberpfalz, Oberfranken, Mittelfranken, Unterfranken, Schwaben |
| `landkreis` | clinics | from facets |
| `ats_type` | clinics | softgarden, bite, rexx, umantis, mein-check-in, typo3_jobs, dvinci, pi_asp, concludis, oracle, personio, smartrecruiters, talention, helix, `""` |
| `fetch` | clinics | adapter · firecrawl (how the board would be read) |
| `traegerart` | both | oeffentlich, freigemeinnuetzig, privat |
| `versorgungsstufe` | both | Grundversorgung (I), Schwerpunkt (II), Maximalversorgung (III), Fachkrankenhaus, `-` |
| `status` (clinics) | clinics | Plan-KH, Vertrags-KH, HS-Klinik, Bedarfsfeststellung, nicht_mehr_im_plan |
| `fach` | clinics | Fachrichtungen codes (INN, CHI, PSY …), any-of; labels in `/api/taxonomy` |
| `beds_min`/`beds_max`, `size` | clinics | integers; S/M/L/XL |
| `has_jobs` | clinics | 1/0 |
| `q` | clinics | substring on name/operator/town/landkreis and badge values (ATS, Bezirk, codes, status, size); `/api/search` for fuzzy |
| `role_class` | jobs | pflegefachkraft, fachpflege, pflegehelfer, praxisanleitung, leitung, apn_experte, hebamme, ota_ata, sonstige_pflege |
| `department_hint` | jobs | Intensiv/IMC, Anästhesie, OP, Notaufnahme, Psychiatrie, Pädiatrie/Neonatologie, Geburtshilfe, Onkologie, Kardiologie, Neurologie, Geriatrie, Dialyse/Nephrologie, Chirurgie/Orthopädie, Innere Medizin, Reha, Springerpool, Ambulanz/Tagesklinik |
| `employment_types`, `contract`, `housing`, `fresh_days`, `verify` | jobs | vollzeit/teilzeit/minijob · UNBEFRISTET/BEFRISTET · 1 · N days · live/gone/blocked/error |

Terminology (TVöD, KeZ, GuK, Versorgungsstufe …): `/api/taxonomy` → `glossary` (DE/EN).

## Decide what the user needs

1. **Numbers or lists** → `GET /api/jobs` / `GET /api/clinics` (filters in `references/api.md`), or PostgREST
   `v_postings` for bulk. Default: `status=open`, `verify=live`, hospital-linked. Say which filters you used.
2. **A specific posting** → `GET /api/jobs/{id}` (description, `enr_*`, observations) — link `source_url`.
   `enr_contact_emails` is personal data: the app API returns `null` for it without an owner/customer session
   and masks e-mail addresses in the free text as well (`GET /api/jobs`, `/api/jobs/{id}`, `/api/clinics/{kez}`;
   `GET /api/agent/manifest` → `redacted` names the rule). A null there means *either* "no address on the ad"
   *or* "not yours to see" — do not report it as "no contact". **PostgREST is not redacted** (see the key
   block above): it will hand you the column and the raw `description`. Reading it there to answer a question
   the app door refused is routing around a control, not a clever query — ask for a session instead.
3. **Which clinics / structure** ("Oberbayern", "Maximalversorger", "öffentlich", "> 500 Betten", "with INN+CHI")
   → `GET /api/clinics?...` (`regierungsbezirk`, `versorgungsstufe`, `traegerart`, `beds_min/max`, `size`, `fach`, `has_jobs`, `routable`).
4. **Fuzzy / typo search** → `GET /api/search?q=` (clinics, jobs, cities).
5. **Candidate ↔ posting** → `POST /api/cv` (file or `{"text":…}`) → `matches[]` with `score` and `why[]`. Anonymise; never send names elsewhere.
6. **Fresh data for a clinic / city / bezirk / vendor** → preview `GET /api/crawl/plan?scope=&values=` then `POST /api/crawl {"target":{"scope":"clinic","values":["<kez>"]},"mode":"auto","max_credits":40}` → poll
   `GET /api/crawl/runs/{run_id}` → re-query. `mode=firecrawl` costs credits (see `credits_left` in `GET /api/crawl/plan`). Recurring → `POST /api/schedules` (preset or cron, target, mode, budget).
6b. **Cities / the plan itself** → `GET /api/cities`, `GET /api/plan` (every registry column, `pdf_url`).
7. **Unknown career portal** → `POST /api/clinics/{kez}/refetch-career` (Firecrawl discovery: portal, ATS, filters, categories).
8. **Rule change** (new keyword, new department pattern) → `GET /api/mechanics` (explanation + source + patterns per rule), test with `POST /api/mechanics/{id}/try`, edit `patterns` → `PUT /api/settings/patterns`, `POST /api/mechanics/{id}/test`; stored rows are re-classified on the next scrape.
9. **"Is X a clinic?" / wrong class** → `employers.class_rule`; manual override SQL in `references/data-model.md`.

## Interpretation rules

- `employer_class`: `clinic` = linked to a KeZ or keyword-clinic; `unknown` = **unclassified, not "not a hospital"**; `non_clinic` = Altenhilfe/ambulant/agency.
- The DB is **experienced-nursing-only**: `nicht_pflege`, `ausbildung`, `werkstudent_praktikum`, and (since 2026-09-07) `pflegehelfer` are refused at ingest (`patterns.json.excluded_role_classes`). `OP-Fachkraft` = `fachpflege`; `MFA`, `Stationsassistenz` = not nursing.
- `role_class`, `department_hint`, `qualification_hint` come from title/department text; the rule that fired is in `role_rule`. Null hint = not stated, not none.
- `enr_*` exist only where a description was fetched; `enr_housing=false` = not mentioned, null = no text.
- `status=open` = seen in the latest crawl of its board; `verify_status`: `live` (re-fetched, title found), `gone` (→ expired), `blocked` (bot wall), `error` (JS page / 5xx). Never call `gone` open.
- `fresh` = `first_published`/`first_seen` within 7 days. `source_codes` ∈ {employer_ats, firecrawl_agent}; `provenance` = which source supplied each field.
- `fetch=firecrawl` clinics (no adapter) may have jobs we cannot see until a Firecrawl scrape ran — say so.
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
Cite `source_url` per posting. Flag `last_seen` > 7 days, `verify_status` ≠ live, and clinics with `fetch=firecrawl` that were never scraped.

## Pitfalls

- `v_postings.source_url`/`source_codes` are correlated subqueries: no `count=exact` over the whole view (500); count on `postings`.
- PostgREST caps at 1000 rows — page. The app API pages with `limit/offset` and returns `total`.
- Same clinic, same title several times = different wards; do not dedupe by title.
- Firecrawl credits are finite (`credits_left` in `GET /api/crawl/plan`, `read:ops`; `/api/stats.firecrawl` is
  null without a session); always pass `max_credits`.
- Never write with the anon key; the ingest secret is never sent anywhere but the ingest function.
- `validate_only` is a **body** field. `?validate_only=true` in the query string is a 400 (it used to be
  ignored, and the write happened anyway while the answer said `validate_only: false`).


---

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


---

# Data model and rules

Graph: `/api/ontology` = `/docs/ontology.json`, rendered on the board's Docs page. Prose: `/docs/overview.md`.

The graph is the published entity list: 37 nodes, each with `entity` (the name to use), `identity`,
`fields` (every `source` is a `file:line` in this repo), `vocab`, `store`, and honest flags — `derived`,
`inferred`, `dead`, `populated: false`, `naming: "new"`, `rename_collision`. Read it before inventing a
name for something. `vocab` ids are top-level keys of `/api/taxonomy` (the vocabulary oracle);
`/api/facets` is the value-and-count oracle. Enumerations that live only in code are spelled out as a
field's `enum` instead, because they do not resolve against taxonomy.json. `tests/test_ontology.py`
keeps all of that tied to the code.

Five entities got their published name there and have none anywhere else: `clinic_view` (the clinic row
the API actually returns), `board` (the unit of work in a crawl — one careers_url, not one clinic),
`adapter`, `ingest_event` (an inbox row), `coverage_cell` (one row of the feature matrix). Two names
collide in code and are disambiguated there: `run` (SQLite queue) vs `pg_crawl_run` (Postgres batch log),
both tables named `crawl_runs`; and `firecrawl_campaign` (reingest) vs `ad_campaign` (autopilot ads).

## Tables (schema `pflege_jobs`)
- `sources(source_id, code, kind, precedence)` — 10 krankenhausplan(1), 20 employer_ats(2), 25 firecrawl_agent(2). Lower wins field by field. (30 arbeitsagentur and 40 aggregator were deleted on 2026-09-06 with their observations and the postings that had no other evidence.)
- `clinics(clinic_id=KeZ, name, town, operator, landkreis, regierungsbezirk, status, versorgungsstufe, traegerart, beds, day_places, fachrichtungen, parse_quality, source, website, careers_url, ats_type, employer_id)` — Krankenhausplan Bayern 2026, 407 sites (399 + 8 `nicht_mehr_im_plan`). `website/careers_url/ats_type` come from discovery (census, Firecrawl refetch-career), not the PDF. Anything writing this table must send **complete rows** (`registry.full_clinic_rows()`): the ingest upsert assigns every column it receives.
- `employers(employer_id, name_norm UNIQUE, name_display, employer_class, class_rule, class_source)` — name_norm = lowercase, legal forms stripped. Conservative: "Klinikum X" and "Klinikum X Personalabteilung" stay separate.
- `inbox(kind, source_host, source_url, payload, collector, client_id, process_note)` — raw crawler rows; `collector` starting with `firecrawl` → source 25, otherwise 20.
- `posting_observations` — identity `(source_id, source_ref)`; every crawl upserts here. Extracted fields + `payload` jsonb + `fuzzy_key` + `content_hash` + `details_fetched_at`.
- `postings` — golden record: `fuzzy_key` (sha1 of normalised title | employer_norm | PLZ), `clinic_id/clinic_match_rule/clinic_match_score`, `provenance` jsonb, `n_observations`, `first_seen/last_seen`, `status`, `verify_*`.
- `role_classes` — taxonomy; grade columns are inferred defaults.
- `crawl_runs` — one row per run/stage (`source_id`, counts, `slice_counts`, `notes`); the app mirrors its own runs here.
- App-side (SQLite `data/app.sqlite`): `crawl_runs`, `run_log`, `schedules`, `career_profiles(clinic_id, profile, fetched_at, credits_used)`, `settings`, `firecrawl_usage`.
- Rules: `pflege_jobs/mechanics.py` — ten mechanics (employer_class, role_class, qualification, department, enrichment, dedupe_key, clinic_link, bavaria_filter, verify_title, cv_profile), each with DE/EN explanation, source, patterns section, try-it and its own test file; `GET /api/mechanics`.

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
Intake gate: `excluded_role_classes` (nicht_pflege, ausbildung, werkstudent_praktikum, pflegehelfer — four since 2026-09-07, experienced nursing only) are refused by every sink (`sinks.only_pflege`). The three-value literal in `pflege_jobs/config.py:94` is the fallback for a missing key and is never reached; `pflege_jobs/patterns.json` is the value.
Enrichment (`enrichment.*`): housing, tariff, pay grade, contact emails, language level, bonus, childcare, recognition mention, requirements/experience excerpts.
`contact emails` → `enr_contact_emails`, personal data: member-and-up on the app API, still readable with the published anon key on PostgREST (SKILL.md key block, `sql/011_PENDING_anon_scope.sql`). Do not select it there.
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
Coverage = what the adapters and the agent can read: 11 sites are `fetch=firecrawl` (no adapter match, mostly `coveto` and a few unlabeled — `ats_type=self_hosted` now routes through the generic `wp_jobs` reader). dvinci has an adapter now (`crawl_dvinci`). `unknown` employers are honest. Descriptions exist only where a detail page was fetched.


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
- `GET /api/crawl/plan?scope=&values=` (preview) → `POST /api/crawl {"target":{"scope":"all|regierungsbezirk|city|clinic|ats_type","values":[…]},"mode":"auto|adapter|firecrawl","max_credits":40}` → background worker: routing → adapters (or agent) → inbox → `cli inbox` → `link-clinics` → `link-cross` → verify of the new postings → cache refresh. Poll `GET /api/crawl/runs/{id}`.
- Schedules (Clawl page, `/api/schedules`): presets `weekly_staggered` (boards hashed over 7 days so not everything runs at once), `daily`, `weekdays`, `hourly`, or a custom cron; each with its own target, mode, credit cap and on/off. Firecrawl only within the credit cap.
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
Not covered: 11 sites with no adapter match (`coveto` and a few unlabeled) → Firecrawl agent / refetch-career. (dvinci is now covered by `crawl_dvinci`; `ats_type=self_hosted` now routes through the generic `wp_jobs` reader.) Details and next steps: `/docs/scraping.md`.

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
python web/build.py                          # web/index.html, web/pro.html, web/skill/* (+ single-file bundle pflege-jobs.skill.md)
sudo systemctl restart pflege-web            # deploy/pflege-web.service: .venv/bin/uvicorn app.main:app --port 8501
curl -s localhost:8501/api/stats
```
Port 8501 is the VM's default proxy port → https://pflege-board.exe.xyz. Build with the public project URL + anon key, not an internal proxy URL.
