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
| source code | __REPO_URL__ |

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
