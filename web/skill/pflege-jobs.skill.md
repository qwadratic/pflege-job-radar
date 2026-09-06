---
name: pflege-jobs
description: Query, interpret, refresh and match against the pflege_jobs dataset — open nursing ("Pflege") job postings at clinics in Bavaria (Supabase schema pflege_jobs, public REST API, dashboard at pflege-board.exe.xyz, multi-source pipeline). Use this skill whenever the user mentions Pflege jobs, Pflegestellen, nursing postings, Bavarian clinics / Kliniken / Krankenhäuser, the pflege_jobs schema, the pflege-stellen-bayern dashboard, matching nursing candidates to openings, clinic hiring contacts, or refreshing/backfilling the job data — even if they only say "the jobs data", "the dashboard", "how many postings", or ask which clinics offer housing/TVöD.
---

# pflege-jobs

**Data source rule: every answer, count, list or match comes from our Supabase REST API
(`pflege_jobs` schema) and nothing else.** Do not query the Arbeitsagentur API, pflege-board.exe.xyz,
job boards or clinic websites to answer a question. Those are *ingestion* sources handled by the
pipeline (`references/pipeline.md`) — if the data looks stale, run the pipeline, then answer from the API.
The only outbound link you hand to a user is a posting's `source_url` (its web proof).

One dataset, three doors: REST API (read), pipeline CLI (write/refresh), dashboard (humans).
Read `references/api.md` before writing any query, `references/data-model.md` before interpreting
fields, `references/pipeline.md` before refreshing data or redeploying.

## Where things are

| thing | value |
|---|---|
| Supabase project | `klkxfvieaxpjlplloljn` (name "real-estate-monitor"), schema `pflege_jobs` |
| REST base | `https://klkxfvieaxpjlplloljn.supabase.co/rest/v1/` + header `Accept-Profile: pflege_jobs` |
| anon key (public, read-only) | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imtsa3hmdmllYXhwamxwbGxvbGpuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM5MjEwOTgsImV4cCI6MjA4OTQ5NzA5OH0.S0ED1qBUyRDP0YSDVBQ0s_L5_tKdu4jsPsLmyUo1YCk` |
| dashboard | `https://pflege-board.exe.xyz` (filters live in the URL) |
| ingest endpoint (write, secret required) | `https://klkxfvieaxpjlplloljn.supabase.co/functions/v1/pflege-ingest` |
| source of truth for postings | `v_postings` view; raw per-source rows in `posting_observations` |
| helper script | `scripts/query.py` (filters → JSON/CSV/markdown, handles paging) |
| registry | `clinics` / `v_clinics` — **Krankenhausplan Bayern 2026 (51. Fortschreibung)**, 407 sites (KeZ, Träger, Versorgungsstufe, Regierungsbezirk, Betten, Fachrichtungen) |
| source code | https://github.com/qwadratic/pflege-job-radar (public) · technical docs https://pflege-board.exe.xyz/docs.html |

Scale (2026-09-06): **7,637 open postings** in Bavaria; **2,633 at clinic-classified employers**,
spread over **242 Krankenhausplan sites**; 2,165 of them carry a `clinic_id`.
By source for those clinic postings: career sites reach 1,650 (uniquely supply 1,193) · Arbeitsagentur 998
(uniquely 600) · aggregators 541 (uniquely 337). No single source is complete.

Four sources, precedence 1→4 when they disagree: `krankenhausplan` (registry, identity only) >
`employer_ats` (clinic career sites / ATS vendors) > `arbeitsagentur` (Jobsuche API) > `aggregator`
(Indeed, StepStone). `clinics.ats_type` says which career-site adapter applies — 175 of 407 sites are
labelled: softgarden 38, typo3_jobs 29, bite 24+3, rexx 17, umantis 16, mein-check-in 12, dvinci 11,
pi_asp 8, concludis 7, oracle 4, personio 3, talention/helix/smartrecruiters 1 each.
**Every labelled vendor has a working adapter except `dvinci` (11 sites)**, whose board is JS-rendered.
The 232 unlabelled sites are the larger coverage gap.

Careful with shared boards: several operators point all their sites at one job board (Schön Klinik 7
sites, kbo 9, Kliniken Südostbayern 3, RHÖN 2). The crawler fetches such a board once and attributes it
to one site; the rest of the group gets its postings through employer/town matching. So a per-site
posting count is a lower bound for group members, not a statement that they are not hiring.

Clinic identity: postings carry `clinic_id` = KeZ from the **Bayerischer Krankenhausplan 2026**
(407 sites in `clinics`; `clinic_match_rule` says how it matched, `R6_ambiguous_sites:…` means the
operator has several sites in that town and the largest/preferred one was chosen). Count clinics by
`clinic_id` (fall back to `employer_id` when null); never by employer name. `v_clinics` gives per-site
counts, Regierungsbezirk, Versorgungsstufe, Träger, beds.

## Decide what the user needs

1. **Numbers or lists** ("how many", "show me", "which clinics") → query `v_postings` / `v_stats`
   via REST. Default filter `employer_class=eq.clinic&is_pflege=eq.true&status=eq.open&verify_status=eq.live`
   unless the user asks otherwise. Say which filters you applied. Every row's `source_url` is its web
   proof (Arbeitsagentur detail page or the clinic's own posting); `verified_at` says when it was last
   confirmed reachable.
2. **A specific posting** → `postings?posting_id=eq.N` gives `description`, `provenance`,
   `enr_*` fields; link `source_url` (Arbeitsagentur) and `external_url` (employer ATS) if present.
3. **Candidate ↔ posting matching** → hard-filter on role_class / department_hint / city radius
   (lat, lon are present) / employment_types / enr_housing, then rank by freshness
   (`first_published`), specialization overlap, distance. Anonymize candidates: never send names.
4. **Fresh data / new source / rule change** → pipeline CLI (`references/pipeline.md`). Refresh is
   the same command as backfill; it is idempotent.
5. **"Is X a clinic?" / fix a wrong class** → check `employers.class_rule`; override with
   `class_source='manual'` (SQL in `references/data-model.md`). Manual overrides survive re-runs.
6. **Regional / structural questions** ("Oberbayern", "Maximalversorger", "öffentliche Träger") → filter
   `v_postings` on `regierungsbezirk`, `versorgungsstufe`, `traegerart`; per-site totals from `v_clinics`.
7. **Dashboard change** → edit `web/index.template.html`, run `python web/build.py`, done — the VM serves
   `web/` directly (systemd unit `pflege-web`, port 8501). Prose for the docs page lives in
   `docs/ARCHITECTURE.md` and is rendered into `web/docs.html` by the same build.

## Interpretation rules (get these right)

- `employer_class` (effective, per posting): `clinic` = linked to a Krankenhausplan site or keyword-classified
  clinic employer; `unknown` = no rule fired, a conflict, or an operator listing row in a town without one
  of its registry sites (e.g. Diakoneo Altenhilfe); `non_clinic` = Altenhilfe/ambulant/agency. Raw
  keyword class is `employer_class_raw`. **unknown is not non-clinic** — say "unclassified".
- `role_class` comes from title + Arbeitsagentur `hauptberuf`; the rule that fired is in `role_rule`.
  **The DB is experienced-nursing-only.** Three classes are refused at ingest *and* were deleted from the
  database on 2026-09-06 (1,322 postings): `nicht_pflege` (Rettungsdienst, MFA, physicians, logistics),
  `ausbildung` (Azubi/Schüler) and `werkstudent_praktikum` (Werkstudent/Praktikum/FSJ). They cannot come
  back — `config.EXCLUDED_ROLE_CLASSES` gates every sink. So `is_pflege` is always true, and you never
  need to filter out trainees. `pflegehelfer` **is** included: it is a qualified occupation and the usual
  role for internationally-trained nurses awaiting German recognition.
  Remaining classes: pflegefachkraft, fachpflege, pflegehelfer, praxisanleitung, leitung, apn_experte,
  hebamme, ota_ata, sonstige_pflege. Note `OP-Fachkraft` counts as `fachpflege` (it is OP nursing written
  without the word "Pflege"); `MFA` and `Stationsassistenz` are deliberately `nicht_pflege`.
- `department_hint` / `qualification_hint` are inferred from the title only; null means "not stated
  in title", not "none".
- `enr_*` (housing, tariff, contact emails, bonus, childcare, language) exist only where a
  description was fetched (clinic rows). `enr_housing=false` means "not mentioned in text";
  null means "no text fetched". Housing evidence phrase is in `postings.enr_housing_evidence`.
- Tariff grades in `role_classes` (P7/P8, KR7/KR8, AVR P7…) are **defaults inferred from the role**.
  `enr_tariff` is what the text says (TVöD / TV-L / AVR…); `enr_pay_grade` is an **explicitly stated**
  grade found in the text (P8, KR8, EG13 — 366 postings), `enr_pay_text` the sentence it came from.
  `enr_requirements` / `enr_experience` are text excerpts (requirements section, experience phrase).
- `department_raw` = department name as the source wrote it (career-site rows); `department_hint` =
  our 17-bucket classification from title/department. Prefer `department_raw` when present.
- `source_codes` lists which sources saw the posting; `n_observations > 1` = confirmed by both.
- `provenance` on `postings` = which source supplied each field. Precedence when sources disagree:
  registry (1) > employer ATS (2) > Arbeitsagentur (3) > aggregators (4); ties → most recent.
- `status='open'` means seen in the latest crawl; `verify_status` is the web check: `live` (page
  reachable and title found), `gone` (404/410 or "nicht mehr verfügbar" → status expired), `blocked`
  (bot wall, e.g. Helios — human can open it), `error` (JS-only page / 5xx). Prefer `live` rows; mention
  `blocked`/`error` as "listed, not machine-verifiable". Never call a `gone` posting open.
- Bavaria scope: Arbeitsagentur rows come from the Bundesland search (`wo=Bayern`, every location
  `region=BAYERN`); career-site rows come from a Bavaria-only board. No city list is involved.

## Query recipes (curl)

```bash
H='-H apikey:'"$ANON"' -H Accept-Profile:pflege_jobs'
# open ICU/IMC specialist-nurse posts at clinics with housing mentioned
curl "$REST/v_postings?select=title,employer,city,enr_tariff,source_url&employer_class=eq.clinic&is_pflege=eq.true&department_hint=eq.Intensiv%2FIMC&enr_housing=is.true&order=first_published.desc" $H
# counts per role at clinics
curl "$REST/v_stats?employer_class=eq.clinic&status=eq.open" $H
# clinics with a hiring contact email
curl "$REST/v_postings?select=employer,city,enr_contact_emails&employer_class=eq.clinic&enr_contact_emails=not.is.null" $H
# total count without rows
curl -I "$REST/v_postings?select=posting_id&employer_class=eq.clinic" $H -H 'Prefer: count=exact'   # Content-Range: 0-0/N
```
PostgREST caps at 1000 rows per call → page with `limit=1000&offset=N` (scripts/query.py does this).
Text arrays (`employment_types`, `enr_contact_emails`) filter with `cs.{vollzeit}`.

## Answer format

Lead with the number and the filter you used ("1,114 open nursing posts at 237 Bavarian clinics,
excluding Azubi/Werkstudent"). Cite `source_url` for individual postings. When listing employers,
show `employer_class` if any are `unknown`. Flag `last_seen` if older than 7 days.

## Pitfalls seen in practice

- Arbeitsagentur API: search works on **v6** (`ergebnisliste`), details on **v4**; other versions 403.
  `pav=false` drops private-recruiter reposts, `zeitarbeit=false` drops agencies — both intended.
- Reference numbers can arrive with leading spaces; the adapter trims. Never build URLs from raw refs.
- The same clinic often posts one generic title several times (different wards) — those are separate
  postings by design; do not "dedupe" them by title.
- Coverage: no single source is complete. Career sites see the most (1,633 of 2,617 open clinic
  postings) but miss the 232 sites without an adapter; the Arbeitsagentur uniquely supplies 600. Never
  imply the dataset is exhaustive for a clinic whose `ats_type` is null.
- `v_postings` builds `source_url` and `source_codes` with correlated subqueries. Selecting them for
  thousands of rows *and* asking for `Prefer: count=exact` can hit the statement timeout (HTTP 500).
  Page without an exact count, or count on the `postings` base table instead.
- Never send the ingest secret or write to `pflege_jobs` from the anon key; reads only.


---

# REST API (PostgREST, schema `pflege_jobs`)

Base: `https://klkxfvieaxpjlplloljn.supabase.co/rest/v1/<relation>`
Headers on every call: `apikey: <anon>` and `Accept-Profile: pflege_jobs`. Reads only (RLS).

## Relations

| relation | rows | use |
|---|---|---|
| `v_postings` | one per posting, joined with employer + role label | default for lists/counts |
| `v_stats` | employer_class × role_class × status → n | quick aggregates |
| `postings` | golden record incl. `description`, `provenance`, `enr_housing_evidence`, `salary_*`, `n_observations` | detail view |
| `posting_observations` | raw per-source rows, `payload` jsonb = original API record | audits, re-classification |
| `employers` | `employer_class`, `class_rule`, `class_source`, `aa_kundennummer_hashes` | who is a clinic |
| `clinics` | KeZ registry from Krankenhausplan 2026: name, town, operator, landkreis, regierungsbezirk, status, versorgungsstufe, traegerart, beds, fachrichtungen | structure |
| `v_clinics` | one row per site with open_pflege_postings, open_pflege_live, employer_names[] | "which clinics", counts per site |
| `v_clinic_portals` | clinic → website, careers_url, ats_type, has_live_site_source, open_pflege_live | "which portal / which ATS" |
| `inbox` | anon-writable intake for browser collectors (`kind`, `source_host`, `source_url`, `payload`, `collector`, `client_id`) | submit walled-site postings |
| `role_classes` | taxonomy + default grades (tvoed_p_grade, tv_l_kr_grade, avr_caritas_grade) | labels |
| `sources` | code, kind, precedence | provenance meaning |
| `crawl_runs` | monitoring log | freshness |

## v_postings columns
`posting_id, title, role_class, role_label, is_pflege, qualification_hint, department_hint, offer_kind,
hauptberuf, employer_id, employer, employer_class, employer_class_rule, city, plz, lat, lon, in_bavaria,
employment_types[], shift_night_weekend, contract, fixed_term_months, start_date, salary_min, salary_max,
salary_unit, first_published, last_modified, first_seen, last_seen, status, external_url, source_url,
enr_housing, enr_tariff, enr_pay_grade, enr_contact_emails[], enr_bonus, enr_childcare, department_raw, source_codes[], n_observations, provenance`
(`postings` additionally: description, enr_pay_text, enr_requirements, enr_experience, enr_housing_evidence, enr_language_req)

## Enums
- employer_class: clinic | unknown | non_clinic
- role_class: pflegefachkraft, fachpflege, pflegehelfer, praxisanleitung, leitung, apn_experte, hebamme, ota_ata, ausbildung, werkstudent_praktikum, sonstige_pflege, nicht_pflege
- qualification_hint: GuK | GKiK | Altenpflege | generalistisch | null
- department_hint: Intensiv/IMC, Anästhesie, OP, Notaufnahme, Psychiatrie, Pädiatrie/Neonatologie, Geburtshilfe, Onkologie, Kardiologie, Neurologie, Geriatrie, Dialyse/Nephrologie, Chirurgie/Orthopädie, Innere Medizin, Reha, Springerpool, Ambulanz/Tagesklinik, null
- offer_kind: ARBEIT | AUSBILDUNG | PRAKTIKUM_TRAINEE
- contract: UNBEFRISTET | BEFRISTET | null
- employment_types: vollzeit, teilzeit, minijob
- enr_tariff: TVöD | TV-L | AVR Caritas | AVR Diakonie | Haustarif | AVR (unspecified) | null
- status: open | expired
- verify_status: live | gone | blocked | error | null (web-liveness check, see SKILL.md)
- regierungsbezirk: Oberbayern | Niederbayern | Oberpfalz | Oberfranken | Mittelfranken | Unterfranken | Schwaben
- versorgungsstufe: Grundversorgung (I) | Schwerpunkt (II) | Maximalversorgung (III) | Fachkrankenhaus | - (Vertrags-KH / HS-Klinik outside the plan levels)
- traegerart: oeffentlich | freigemeinnuetzig | privat
- source_codes: arbeitsagentur | employer_ats (array; filter `source_codes=cs.{employer_ats}`; both → `n_observations=gt.1`)
- enr_pay_grade: normalized text grade e.g. P7, P8, P9, P12, KR8, EG13 (null = not stated)

## Filter syntax (PostgREST)
`col=eq.x`, `col=in.(a,b)`, `col=ilike.*text*`, `col=is.true`, `col=not.is.null`, `col=gte.2026-08-01`,
array contains `employment_types=cs.{vollzeit}`, OR: `or=(title.ilike.*intensiv*,department_hint.eq.Intensiv%2FIMC)`.
Order: `order=first_published.desc`. Paging: `limit=1000&offset=0` (max 1000/call).
Exact count: header `Prefer: count=exact`, read `Content-Range: 0-999/5520`.
URL-encode slashes in values (`Intensiv%2FIMC`).

## Examples
- Sites with open live nursing posts in Oberbayern: `v_clinics?regierungsbezirk=eq.Oberbayern&open_pflege_live=gt.0&order=open_pflege_live.desc`
- Web-verified clinic nursing posts (the default): `v_postings?employer_class=eq.clinic&is_pflege=eq.true&status=eq.open&verify_status=eq.live`
- Leadership roles at clinics: `v_postings?employer_class=eq.clinic&role_class=eq.leitung`
- Munich clinic posts, full-time, last 14 days: `v_postings?employer_class=eq.clinic&city=ilike.*münchen*&employment_types=cs.{vollzeit}&first_published=gte.<date>`
- Employers that are unclassified but post GuK roles: `v_postings?employer_class=eq.unknown&qualification_hint=eq.GuK&select=employer,city`
- One posting with text: `postings?posting_id=eq.123&select=title,description,provenance,enr_housing_evidence`
- Raw Arbeitsagentur record: `posting_observations?posting_id=eq.123&select=source_ref,source_url,payload`


---

# Data model and rules

## Tables (schema `pflege_jobs`)
- `sources(source_id, code, kind, precedence)` — 10 krankenhosplan(1), 20 employer_ats(2), 30 arbeitsagentur(3), 40 aggregator(4). Lower = wins.
- `employers(employer_id, name_norm UNIQUE, name_display, employer_class, class_rule, class_source, aa_kundennummer_hashes[])`
  name_norm = lowercase, legal forms stripped (GmbH, gGmbH, e.V., KG, AG, Stiftung…). Conservative: "Klinikum X" and "Klinikum X Personalabteilung" stay separate.
- `clinics(clinic_id=KeZ, name, town, operator, landkreis, regierungsbezirk, status, versorgungsstufe, traegerart, beds, day_places, fachrichtungen, parse_quality)` — Bayerischer Krankenhausplan 2026 (StMGP PDF), 407 sites. `postings.clinic_id/clinic_match_rule/clinic_match_score` link postings to sites.
- `posting_observations` — identity `(source_id, source_ref)`; every crawl upserts here. Carries extracted fields + `payload` jsonb + `fuzzy_key` + `content_hash` + `details_fetched_at`.
- `postings` — golden record, `fuzzy_key` (sha1 of normalized title | employer_norm | PLZ), `provenance` jsonb, `n_observations`, `first_seen/last_seen`, `status`.
- `role_classes` — taxonomy; grade columns are inferred defaults.
- `crawl_runs`, `assets` (dashboard HTML, skill text), `staging_rows` (fallback ingest path).

## resolve_postings()  (runs after every load)
1. Link: unlinked observation → if exactly one posting shares `fuzzy_key` AND that posting has no observation from the same source → link; otherwise create a new posting. Same-source rows never merge; ambiguous (>1 candidate) → new posting.
2. Fields: per field, first non-null value ordered by `precedence asc, observed_at desc`, implemented as a jsonb fold ordered `precedence desc, observed_at asc`. `first_seen=min`, `last_seen=max`, `provenance{field: source_code}`.
3. `mark_expired(p_days)` sets `status='expired'` where `last_seen < now() - p_days`.

## Classification (pflege_jobs/config.py in the repo; rule recorded in `*_rule` columns)
Employer: CLINIC_PATTERNS (klinik, krankenhaus, universitätsklinikum, bezirkskrankenhaus/kbo/medbo/gebo, chains: sana/helios/asklepios/schön/…, operators: sozialstiftung bamberg, schwesternschaft münchen, medical park, passauer wolf, kirinus, oberberg, thoraxzentrum…) vs NON_CLINIC_PATTERNS (altenhilfe, ambulant/pflegedienst/mvz, wohnen, verband, brand_nc, agentur, sonstige).
Conflict matrix: clinic + weak group (verband, sonstige) → clinic; clinic + strong group (altenhilfe, ambulant, wohnen, agentur, brand_nc) → unknown. No match → unknown.
Role: gate on a Pflege token in title+hauptberuf → `nicht_pflege` if NICHT_PFLEGE matches and the TITLE has no strong Pflege token → ordered rules: werkstudent_praktikum, ausbildung, hebamme, ota_ata, praxisanleitung, leitung, apn_experte, fachpflege, pflegehelfer, pflegefachkraft; fallback sonstige_pflege. `offer_kind` AUSBILDUNG / PRAKTIKUM_TRAINEE wins outright.
Leadership regex requires word starts (`(?<![a-zäöüß])leitung\b`), so "Pflegeüberleitung" and "Alltagsbegleiter" do not count.
Description enrichment: housing (Personalwohnung/Wohnraum/Wohnheim/Unterstützung bei der Wohnungssuche/Umzugskosten…), tariff, emails (regex), language level (A2–C1 near "Deutsch"), Willkommensprämie, Betriebskita, Anerkennung mention.

## Manual override (privileged SQL: Supabase MCP execute_sql or psql)
```sql
update pflege_jobs.employers set employer_class='clinic', class_rule='manual:<why>', class_source='manual'
 where name_display ilike 'Riedel & Pfeuffer%';
select * from pflege_jobs.resolve_postings();   -- postings.employer_id unchanged; v_postings reflects new class immediately
```
`class_source='manual'` is never overwritten by the keyword rule on later loads.

## Known limits
Single source (Arbeitsagentur) → ~half of operator-portal volume. `unknown` employers (~1.1k rows) are honest, mostly Altenhilfe/ambulant without name tokens. Regierungsbezirk not derived. Descriptions only for clinic rows.


---

# Pipeline runbook (repo: https://github.com/qwadratic/pflege-job-radar)

Env (`.env`): SUPABASE_URL, SUPABASE_ANON_KEY, PFLEGE_INGEST_URL (…/functions/v1/pflege-ingest), PFLEGE_INGEST_SECRET.
Install: `pip install requests pytest --break-system-packages`; tests: `python -m pytest -q tests`.

## Refresh (same as backfill, idempotent)
```bash
set -a; . ./.env; set +a
python -m pflege_jobs.cli run --sink edge --details clinic        # pull all slices → descriptions for clinic rows → upsert → resolve
# afterwards expire stale rows (7 days):
curl -X POST "$PFLEGE_INGEST_URL" -H "Authorization: Bearer $SUPABASE_ANON_KEY" -H "x-ingest-secret: $PFLEGE_INGEST_SECRET" -H "Content-Type: application/json" -d '{"expire_days":7,"crawl_run":{"source_id":30,"notes":"daily"}}'
```
Verify liveness after every refresh: `python -m pflege_jobs.cli verify --workers 6` (Arbeitsagentur rows via the
details endpoint, career-site rows via HTTP GET + title check; 8 workers trigger 429s — use ≤6). Results land in
`postings.verify_status/verify_http/verified_at/verify_note`; `gone` expires the posting.

Steps individually: `counts` (live per-slice totals, no writes) · `pull --out data/raw.json` · `details --only clinic` · `load --sink edge|csv|sql|staging` · `renormalize` (re-run classification from stored payloads after a rule change, no crawl).

## Arbeitsagentur specifics
Search `…/pc/v6/jobs` (params: berufsfeld|was, wo=Bayern, angebotsart 1/4/34, zeitarbeit=false, pav=false, size=100, page). Total in `maxErgebnisse`, rows in `ergebnisliste`. Details `…/pc/v4/jobdetails/{base64(refnr)}` → `stellenangebotsBeschreibung`. Header `X-API-Key: jobboerse-jobsuche`. Slices in `config.AA_SLICES` (2 Berufsfelder × Arbeit/Ausbildung/Praktikum + keyword slices for Hebamme, OTA, ATA, PDL, Stationsleitung, Praxisanleiter, Pflegeexperte, Werkstudent).

## Career-site snapshot import (source employer_ats)
`python -m pflege_jobs.cli load-board --csv data/board_snapshot_<date>.csv --sink edge` — maps the pflege-board CSV
(clinic, city, department, job_title, qualification, pay_grade, pay_text, housing, housing_quote, employment_type,
requirements_must, experience_required, job_url) to observations; identity = job_url; lat/lon from the city table
built from Arbeitsagentur rows. Linking to Arbeitsagentur postings uses fuzzy_key (title | employer | city).

## Career-site crawler (live employer_ats rows)
`python data/run_crawl.py 0 0 120 "Klinikum Bayreuth|RoMed Kliniken"` runs `pflege_jobs/sources/career_crawl.py` over seeds in
`data/registry/top20_seeds.json` (career URL, extra list pages, sitemaps, hosts, KeZ, town, bavaria_only_operator). Strategy:
listing-first — job links = anchors with a gender marker "(m/w/d)" or detail-URL patterns, plus sitemap <loc>s; each detail page
parsed via schema.org JobPosting JSON-LD (softgarden, d.vinci, rexx, mein-check-in…) or heuristically (h1 + "Bewerben");
Bavaria filter on addressRegion / PLZ / town list drops non-Bavarian sites of chains (Rhön: 18 dropped). robots.txt honoured,
≤ 0.25 s between requests. Known blockers: JS-only lists (UKR concludis, Kliniken Nordoberpfalz, Aschaffenburg, Passau);
KWM rate-limits after ~30 pages. Load with `EdgeSink`, then `clinic_links` from the seed KeZ and `verify` = live (fetched now).

## Headless-browser adapter (JS portals)
`pflege_jobs/sources/career_browser.py` (Playwright/Chromium; `pip install playwright && playwright install chromium`) for seeds in
`data/registry/js_seeds.json` with `browser: true`: accepts cookie consent, scrolls, clicks "mehr laden", collects job anchors from
page + iframes + JSON XHR payloads; details via requests (JSON-LD) or rendered when `browser_details: true`. Run
`python data/run_browser_crawl.py "Klinikum St. Marien Amberg|LA-REGIO Kliniken (Klinikum Landshut)" 60`. Works: Talention, LA-REGIO,
Amberg, Aschaffenburg (jobs. subdomain). Still blank: München Klinik JobFinder (consent-gated, undocumented API), UKR (B-ITE widget),
Helios (bot wall also for headless Chromium), Josefinum, DONAUISAR, Passau (postings as PDF flyers). Sandbox note: `ignore_https_errors`
is required behind a TLS-inspecting proxy.

## ATS census + B-ITE adapter (the "detect → dedicated adapter" pattern)
`python data/ats_census.py 0 429` — for every klinikradar facility: profile → `props.hospital.website` → homepage → up to 3 career
links → fingerprint (B-ITE widget, softgarden, rexx, umantis, mein-check-in, d.vinci, concludis, Talention, Personio, Oracle…).
Result in `data/registry/ats_census.json`, written to `clinics.ats_type/careers_url/website`. 2026-09-06: 429 scanned,
B-ITE 28, softgarden 49, TYPO3-native 22, rexx 18, umantis 16, mein-check-in 14, d.vinci 10, concludis 6.
B-ITE recipe (`pflege_jobs/sources/bite.py`): page has `<script …static.b-ite.com/jobs-api/loader-v1…>` + `data-bite-jobs-api-listing="{customer}:{listing}"`
→ `GET cs-assets.b-ite.com/{customer}/jobs-api/{listing}.min.js` (contains the 40-hex `key`) → `POST jobs.b-ite.com/api/v1/postings/search`
`{"key",…,"locale":"de","page":{"num":1000}}` → structured postings (address with PLZ/lat/lon, dates, employmentType, custom.berufsgruppe)
→ `GET <url>/raw` for the description. Run `python data/run_bite.py` (checkpointed in `data/bite_done.json`). Listings can span a whole
operator (BG Kliniken 397 postings nationwide, Diakoneo 219): rows are attributed per posting via `employer.name` + city → registry
match; only rows with positive Bavaria evidence (PLZ/town) are kept. `listing == "niiid"` is B-ITE's chatbot product — not supported.

## softgarden adapter
`python data/run_softgarden.py 100` (checkpoint `data/softgarden_done.json`): for each census facility with `ats=softgarden`,
`pflege_jobs/sources/softgarden.py` finds the portal host on the career page (`*.softgarden.io`, `jobdb.softgarden.de`, or a custom domain with
`/job/<id>/` links), then the generic crawler lists `/de/vacancies` + `/sitemap.xml` and parses JSON-LD on job pages. 2026-09-06: 25 portals,
198 Pflege/Bavaria rows (Dritter Orden 44, Kulmbach 30, GeBO 18, Passauer Wolf 17, Hescuro 16, Josefinum 12). Not covered: clinics whose page only
embeds a softgarden badge (no host found), `jobdb.softgarden.de` legacy portals, and nationwide operators (Paracelsus: 259 links, first 100 non-Bavarian).

## rexx / d.vinci / mein-check-in / umantis adapters
`python data/run_ats.py <rexx|dvinci|mein-check-in|umantis> [budget]` (checkpoint `data/ats_<ats>_done.json`); seed builders in
`pflege_jobs/sources/ats_seeds.py`:
- rexx: `<host>/stellenangebote.html` + `?page=2..6` + sitemap; JSON-LD on details. 2026-09-06: 8 portals → 69 rows.
- d.vinci: host from `*.dvinci-hr.com|*.dvinci-easy.com|/de/jobs` links → `/de/jobs`, `/de/jobs/iframe`, `?page=`; JSON-LD (may have
  null address → seed town). 5 portals → 113 rows (Fürth 36, Bamberg 35, RoMed 28, Neumarkt 11).
- mein-check-in: `mein-check-in.de/<slug>/overview` → `/position-<id>` pages, heuristic parse; town from title ("am BKH Passau") →
  `Einsatzort:` label → seed town; category links "(n)" dropped. 8 portals → 69 rows.
- umantis: `recruitingapp-<n>.de.umantis.com/Jobs/1?CompanyID=…` — list is JS-paged; only 1 row. Needs a browser or their JSON. Low priority (16 small sites).
Nationwide operators (Schön, Rhön, Paracelsus…) get `bavaria_only_operator=false`: rows need positive PLZ/town evidence.

## Walled portals — what actually works (tested 2026-09-06)
| target | sandbox / Netlify / Cloudflare | headless Chromium | **Claude web_fetch (Anthropic egress)** | other |
|---|---|---|---|---|
| helios-gesundheit.de (www) | 403 Akamai, even robots.txt | 403 | **200**, job pages carry JSON-LD | Helios' real ATS is **P&I bewerber-web on pi-asp.de** — not walled, GWT-RPC encrypted, but renderable: `pflege_jobs/sources/pi_asp.py` (companyEid 1134 München West, 1135 Perlach, 1130 Dachau/Indersdorf) |
| stepstone.de | tarpit/503 | – | **200**, 25 cards/page, `?page=N` | – |
| de.indeed.com | 403 "Security Check" | – | listing content visible via Claude web_search | – |

`crawlers/claude_egress.py` = Messages API + `web_fetch` server tool → parse returned markdown (StepStone cards, JSON-LD lines,
"(m/w/d)" links) → `pflege_jobs.inbox` → `cli inbox`. Needs `ANTHROPIC_API_KEY`; ~1 call per page. Modes: `stepstone 1-58`,
`helios-detail <urls>`, `url <urls>`. Aggregator rows get precedence 4 and are only useful for employers with no other source.

StepStone worked example (2026-09-06, in-chat via Claude fetch): `jobs/pflegefachkraft/in-augsburg` page 1 → 16 cards → 12 loaded as
aggregator rows, 5 linked to registry sites (Klinik Vincentinum ×3, Hessing Kliniken ×5 after the leadership-gate fix) — both sites had no
site source before. Chains/agencies (Korian, Pacura, BeneVit, von Caprivi) land as non_clinic/unknown and are ignored by the default view.
Per-city StepStone pages (`/jobs/pflegefachkraft/in-<stadt>`) are the efficient unit: 13 pages for Augsburg, 58 for all of Bayern.

### Exa as egress (`crawlers/exa_egress.py`, tested in-chat 2026-09-06)
Exa reads StepStone listings **with working `?page=N`** and detail pages (full text: TVöD grade, Betriebswohnungen, Kita → enrichment).
The Exa API `/contents` (`livecrawl:"always"`, `extras.links`) also returns page links, so cards get their real detail URL (slug-token
match) and detail pages yield the apply link for ATS discovery. `stepstone-matrix` there = cities × keywords × pages (EXA_PAGES=3).

### ATS discovery from StepStone
During `claude_egress.py stepstone`, cards whose employer maps to a registry site that has no ATS label **or** no live site source
(`v_clinic_portals`) get their StepStone detail page fetched (1 extra call each, cap `EGRESS_DISCOVER_MAX=40`, disable with
`EGRESS_DISCOVER=0`); all non-StepStone links are fingerprinted against the ATS host list (softgarden, SmartRecruiters, B-ITE, P&I,
d.vinci, rexx, umantis, concludis, Talention, Personio, mein-check-in, HELIX, Oracle, Workday, SuccessFactors, onlyfy, Interamt…).
Result rows go to `inbox` as `kind='probe'`; `cli inbox` writes `clinics.ats_type` + `careers_url` (only when empty or different),
so the next daily run routes that site to its adapter.

## Browser collector (walled portals: Helios, StepStone, Indeed)
Server-side access is impossible from datacenter IPs (Akamai/Cloudflare walls; Netlify functions are blocked too — probe at
`/.netlify/functions/probe?u=…`). CORS forbids reading those sites from our page. So: **bookmarklet** (`web/collector.js`, page
`/collect.html`): the user opens the listing/detail page in their browser, clicks the bookmark; it extracts JSON-LD JobPostings +
"(m/w/d)" links from the DOM and opens `/collect.html#<base64>` which POSTs rows to `pflege_jobs.inbox` (anon INSERT allowed there,
and only there; 2,000 rows/day/client trigger). `python -m pflege_jobs.cli inbox` normalises jobposting rows → observations
(employer sites → employer_ats, StepStone/Indeed → aggregator precedence 4), links to registry, marks verify=live ("collected in a
user's browser"), acks the inbox. Agents can POST to the inbox directly (see collect.html).

## Registry portal table
`v_clinic_portals` = clinic × website × careers_url × ats_type × has_live_site_source × open_pflege_live (407 sites; website 300,
careers_url 269, ats 139, live site source 56).

## Feed/API adapters (`pflege_jobs/sources/feeds.py`, seeds `data/registry/feed_seeds.json`, `python data/run_feeds.py`)
- Personio: `https://<slug>.jobs.personio.de|com/xml?language=de` — XML positions (name, office, department, schedule, jobDescription).
- SmartRecruiters: `https://api.smartrecruiters.com/v1/companies/<Company>/postings?limit=100` + `…/postings/<id>` for the job ad text;
  location.region 'BY' / city → Bavaria; site_map city→employer for group listings (Artemed SE covers Tutzing, Feldafing, Berg,
  Vincentinum Augsburg, München Süd/Mitte — the sites whose own widget is B-ITE "niiid").
- Talention: `POST https://<host>/talention/api/3.2/job` with `{"pagination":{"max":50,"offset":N},"sorter":{"sort":"createdDate","order":"desc"}}`
  → results (title, url, "PLZ Ort" or free-text location, enabledDate). Used for Kliniken Nordoberpfalz and Dr. Ebel.
- HELIX (Perbility, `*.helixjobs.com/<site>/joblist`): server-rendered, `jobad?prj=` links with JSON-LD → generic crawler (`helix_seeds.json`).
- umantis: St. Vinzenz Pfronten renders 19 jobs only in a browser (`career_browser`); recruitingapp-5545/5610 listings are empty.
Not supported: Oracle Recruiting Cloud (4 sites; the Oracle app is not linked from the crawled pages), Klinikverbund Allgäu (no job list found).

## Effective employer class (view)
`v_postings.employer_class` = `clinic` when the posting is linked to a registry site; `unknown` when the employer runs registry sites
but this posting is in a town without one (operator listings mixing Altenhilfe); otherwise the employer's keyword class (`employer_class_raw`).

## Pflege-only storage
`sinks.only_pflege()` drops `nicht_pflege` rows in every sink (`keep_non_pflege=True` to override); `pflege_jobs.purge_nicht_pflege()`
removed the 299 stored ones (postings + raw observations) on 2026-09-06. Classification still happens on every row so the gate is auditable
(`role_rule`), but nothing non-nursing is persisted.

## Arbeitsagentur duplicate policy (after every load)
`select pflege_jobs.supersede_aa_at_covered_sites();` — at registry sites that have a **live career-site source** (`v_site_coverage.has_live_site_source`:
crawl/B-ITE/softgarden/rexx/d.vinci/mein-check-in rows), Arbeitsagentur-only postings are set `status='expired'` with
`verify_note='superseded: site has a live career-site source (AA duplicate policy)'`. AA remains the source of truth for the other sites.
2026-09-06: 56 covered sites, 179 AA-only rows superseded. Reversible (note kept; a newer AA observation reopens nothing by itself).

## Dedupe (`python -m pflege_jobs.cli link-cross [--dry-run]`, run after every load)
1. Same-source URL variants: observations whose `canonical_ref(source_ref)` is equal (SmartRecruiters slug stripped, query/fragment/
   trailing slash dropped) are merged — 155 pairs on 2026-09-06 (board snapshot vs API URLs).
2. Cross-source: within the same `clinic_id` + city, postings from different sources with similar titles (Jaccard ≥ 0.6, or overlap
   ≥ 0.9 with ≥ 3 shared tokens) are merged into the most authoritative one (employer_ats > arbeitsagentur > aggregator);
   observations moved, earliest first_seen kept. Worked example: StepStone "Needle Nurse (m/w/d) in Teilzeit" (Klinik Vincentinum)
   → one posting with 3 observations: SmartRecruiters API + board snapshot + StepStone; published 2026-08-17 comes from the ATS.

## Clinic registry (Krankenhausplan)
`python -m pflege_jobs.sources.krankenhausplan data/registry/krankenhausplan_2025.pdf data/registry/clinics.csv data/raw.json`
parses Teil II Abschnitt A of the StMGP PDF (407 sites, KeZ). `python -m pflege_jobs.cli link-clinics [--dry-run]` links postings
(rules R1 exact name, R2 operator, R3/R4 token overlap with town, R5 loose, R6 ambiguous multi-site → preferred/largest site) and
pushes `clinics` + `clinic_links`. Re-run after every refresh; rows with `clinic_match_rule='manual'` are never touched.

## Adding a source (employer ATS, aggregator)
Write `pflege_jobs/sources/<name>.py` that yields the same observation dict (see `arbeitsagentur.to_observation`), set `source_id` to the `sources` row, load with `EdgeSink`. Precedence handles field conflicts; fuzzy_key handles cross-source linking.

## Ingest endpoint
POST JSON `{employers?, observations?, resolve?, expire_days?, crawl_run?}` with `Authorization: Bearer <anon>` + `x-ingest-secret`. Batches ≤200 rows. Returns counts. Column lists are rendered from `pflege_jobs/schema.py` into the function by `python edge/build_ingest.py` — edit the spec, rebuild, redeploy. (The staging-table fallback was removed in migration 007.)

## Dashboard redeploy (no Netlify, no repo needed)

The dashboard is served straight off this VM by systemd, so "deploy" is just a rebuild:

```bash
set -a; . web/.env.build; set +a     # public project URL + anon key + REPO_URL
python web/build.py                   # -> web/index.html, web/docs.html, web/llms.txt, web/skill/*
```

`systemd` unit `pflege-web` runs `busybox httpd -p 8501 -h web/` (port 8501 is the VM's default
proxy port, so https://pflege-board.exe.xyz maps to it with no port suffix). Nothing to upload.

```bash
systemctl status pflege-web      # is it up?
sudo systemctl restart pflege-web
```

**Build with the public config, not the VM proxy.** `.env` points `SUPABASE_URL` at
`supabase.int.exe.xyz` with `apikey: implicit` — that only resolves *inside* this VM. Building the
dashboard with it produces a page that works for us and returns 500 for every real visitor. Use
`web/.env.build` (project URL + real anon key), which is what the command above does.

Prose for the human docs page lives in `docs/ARCHITECTURE.md` and is rendered into `web/docs.html`
by the same build (`web/render_md.py`). The agent skill in `skill/` is copied to `web/skill/`.

## Monitoring plan
Daily cron (GitHub Actions or any scheduler) running the refresh above; alert when a slice's `maxErgebnisse` drops >30 %, when null-rate of role_class/city rises, or when HTTP errors > 2 %. `crawl_runs` holds the history.
