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
Coverage = what the adapters and the agent can read: 58 sites are `fetch=firecrawl` (no adapter match, mostly `ats_type=self_hosted`/`coveto`). dvinci has an adapter now (`crawl_dvinci`). `unknown` employers are honest. Descriptions exist only where a detail page was fetched.
