# Data model and rules

## Tables (schema `pflege_jobs`)
- `sources(source_id, code, kind, precedence)` — 10 krankenhosplan(1), 20 employer_ats(2), 30 arbeitsagentur(3), 40 aggregator(4). Lower = wins.
- `employers(employer_id, name_norm UNIQUE, name_display, employer_class, class_rule, class_source, aa_kundennummer_hashes[])`
  name_norm = lowercase, legal forms stripped (GmbH, gGmbH, e.V., KG, AG, Stiftung…). Conservative: "Klinikum X" and "Klinikum X Personalabteilung" stay separate.
- `clinics(clinic_id=KeZ, name, town, operator, landkreis, regierungsbezirk, status, versorgungsstufe, traegerart, beds, day_places, fachrichtungen, parse_quality)` — Bayerischer Krankenhausplan 2025 (StMGP PDF), 403 sites. `postings.clinic_id/clinic_match_rule/clinic_match_score` link postings to sites.
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
