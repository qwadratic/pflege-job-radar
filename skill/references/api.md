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
| `clinics` | KeZ registry from Krankenhausplan 2025: name, town, operator, landkreis, regierungsbezirk, status, versorgungsstufe, traegerart, beds, fachrichtungen | structure |
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
