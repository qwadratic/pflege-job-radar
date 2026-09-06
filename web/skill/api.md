# API reference

## A. App API — `https://pflege-board.exe.xyz/api` (JSON, no auth for reads)

| method | path | returns |
|---|---|---|
| GET | `/stats` | `{open_jobs, fresh_jobs, clinics, clinics_with_jobs, clinics_routable, last_crawl{at,status}, firecrawl{remaining,plan,used_period,period_end,spent_by_app}, next_autocrawl}` |
| GET | `/facets` | `{cities[{v,n}], regierungsbezirk, landkreis, ats_type, traegerart, versorgungsstufe, status, fachrichtungen[{v,label,n}], role_class[{v,label,n}], department_hint, employment_types, contract, enr_tariff, beds{min,max}, size_buckets}` |
| GET | `/clinics` | `{total, rows[clinic]}` — filters: `q, city, regierungsbezirk, landkreis, ats_type, fetch, traegerart, versorgungsstufe, status, fach, beds_min, beds_max, size, has_jobs, sort, limit, offset` |
| GET | `/cities?q=` | `[{city, regierungsbezirk, landkreis, clinics, jobs_open, jobs_fresh, ats_known}]` |
| GET | `/plan?q=&regierungsbezirk=&sort=` | `{rows[every clinics.csv column], pdf_url, source, source_url}` — the Krankenhausplan as a table |
| GET | `/clinics/{kez}` | clinic + `jobs[]` + `runs[]` + `career_profile` |
| GET | `/jobs` | `{total, rows[job]}` — filters: `clinic_id, q, role_class, department_hint, city, regierungsbezirk, employment_types, contract, housing, fresh_days, verify, sort, limit, offset` |
| GET | `/jobs/{id}` | job + `description`, `enr_*`, `observations[{source_code, source_url, observed_at}]` |
| GET | `/search?q=` | `{clinics[{clinic_id,name,town,score}], jobs[{posting_id,title,employer,city,clinic_id,score}], cities[]}` |
| POST | `/cv` | multipart `file` (pdf/docx/txt) or JSON `{"text"}` → `{profile{roles,departments,qualifications,cities,experience_years,languages,skills,keywords}, matches[job+score+why[]], used_llm}` |
| GET | `/crawl/plan?scope=&values=a,b&mode=` | `{clinics, boards, via_adapter, via_firecrawl, walled, est_credits, sample[]}` |
| POST | `/crawl` | `{"target":{"scope":"all|regierungsbezirk|city|clinic|ats_type","values":[…]},"mode":"auto|adapter|firecrawl","max_credits":40,"fetch_details":false}` → `{run_id}` |
| GET | `/crawl/runs?limit=` / `/crawl/runs/{id}` | `[{run_id, started_at, finished_at, scope, value, mode, status, n_rows, n_new, credits_used, log_tail, clinic_ids}]` / + `log[]` |
| POST | `/clinics/{kez}/refetch-career` | `{"max_credits":40}` → `{run_id}`; result in `career_profile` + `clinics.careers_url/ats_type` |
| GET/POST/PUT/DELETE | `/schedules[/{id}]` | `{id, name, enabled, preset (weekly_staggered|daily|weekdays|hourly|custom), cron, stagger_days, target, mode, max_credits, fetch_details, last_run_at, next_run_at, human}`; `POST /schedules/{id}/run-now` |
| GET | `/mechanics` | `[{id, title{de,en}, description{de,en}, stage, patterns_section, functions[{name,source,doc}], inputs[{name,label,example}], test_file, n_tests}]` |
| POST | `/mechanics/{id}/try`, `/mechanics/{id}/test` | `{inputs}` → `{result, rule}` · → `{passed, failed, output}` |
| GET | `/settings` | `{patterns, firecrawl{default_max_credits}}` |
| PUT | `/settings/patterns` | save (every `re` must compile; `config.reload()`) |
| GET | `/taxonomy`, `/ontology`, `/docs` | taxonomy.json, ontology.json, docs index |

Multi-value filters are comma lists (`city=München,Augsburg`, `fach=INN,CHI`, `size=L,XL`). `sort` = column or `-column`.
Clinic row: `clinic_id, name, town, operator, landkreis, regierungsbezirk, versorgungsstufe, traegerart, beds, day_places, fachrichtungen[], status, website, careers_url, ats_type, fetch, fetch_label, routable, route_reason, walled, jobs_open, jobs_fresh, jobs_live, last_crawl_at, last_crawl_status, last_crawl_mode, career_profile`.
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
