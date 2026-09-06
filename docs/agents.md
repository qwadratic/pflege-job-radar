# Für Agenten

Dataset: open, **experienced-level nursing jobs at Bavarian hospital sites** (Krankenhausplan KeZ). Answer only from this API; the web proof of a posting is its `source_url`.

## Rules
1. Read from `/api/*` (this host) or PostgREST `v_postings` — never from job boards or clinic sites.
2. Default filter = `status=open`, `verify=live`, hospital-linked (`clinic_id` set). Say which filters you used.
3. `employer_class=unknown` means **unclassified**, not "not a hospital".
4. The database is experienced-only: no trainees, students, interns, non-nursing. `pflegehelfer` is included.
5. Count clinics by `clinic_id`, never by employer name. Shared boards: a per-site count is a lower bound for group members.
6. `status=open` = seen in the last crawl; `verify_status=live` = re-fetched. Never call a `gone` posting open.
7. A clinic with `routable=false` and no Firecrawl run may have jobs we cannot see — say so.

## Filters (meaning)
| filter | on | values |
|---|---|---|
| `city` | clinics: `town`, jobs: `city` | comma list, exact after normalisation; use `/api/facets` for the list |
| `regierungsbezirk` | both | Oberbayern, Niederbayern, Oberpfalz, Oberfranken, Mittelfranken, Unterfranken, Schwaben |
| `landkreis` | clinics | from facets |
| `ats_type` | clinics | softgarden, bite, rexx, umantis, mein-check-in, typo3_jobs, dvinci, pi_asp, concludis, oracle, personio, smartrecruiters, talention, helix, `""` |
| `traegerart` | both | oeffentlich, freigemeinnuetzig, privat |
| `versorgungsstufe` | both | Grundversorgung (I), Schwerpunkt (II), Maximalversorgung (III), Fachkrankenhaus, `-` |
| `status` (clinics) | clinics | Plan-KH, Vertrags-KH, HS-Klinik, Bedarfsfeststellung, nicht_mehr_im_plan |
| `fach` | clinics | Fachrichtungen codes (INN, CHI, PSY …), comma list, any-of |
| `beds_min`/`beds_max`, `size` | clinics | integers; S/M/L/XL |
| `has_jobs`, `routable` | clinics | 1/0 |
| `role_class` | jobs | pflegefachkraft, fachpflege, pflegehelfer, praxisanleitung, leitung, apn_experte, hebamme, ota_ata, sonstige_pflege |
| `department_hint` | jobs | Intensiv/IMC, Anästhesie, OP, Notaufnahme, Psychiatrie, Pädiatrie/Neonatologie, Geburtshilfe, Onkologie, Kardiologie, Neurologie, Geriatrie, Dialyse/Nephrologie, Chirurgie/Orthopädie, Innere Medizin, Reha, Springerpool, Ambulanz/Tagesklinik |
| `employment_types` | jobs | vollzeit, teilzeit, minijob |
| `contract` | jobs | UNBEFRISTET, BEFRISTET |
| `housing` | jobs | 1 = housing mentioned in the text |
| `fresh_days` | jobs | N days on `first_published`/`first_seen` |
| `verify` | jobs | live, gone, blocked, error |
| `q` | both | substring; use `/api/search` for fuzzy |

## Endpoints (short)
`GET /api/stats` · `GET /api/facets` · `GET /api/clinics` · `GET /api/clinics/{kez}` · `GET /api/jobs` · `GET /api/jobs/{id}` · `GET /api/search?q=` · `POST /api/cv` · `POST /api/crawl` · `GET /api/crawl/runs[/{id}]` · `POST /api/clinics/{kez}/refetch-career` · `GET/PUT /api/settings…` · `GET /api/taxonomy|ontology|docs`. Full examples: [api.md](api.md).

## Patterns
- "How many open ICU jobs in Oberbayern?" → `GET /api/jobs?department_hint=Intensiv%2FIMC&regierungsbezirk=Oberbayern&verify=live&limit=1` → read `total`.
- "Which large public hospitals in Schwaben have no jobs visible?" → `GET /api/clinics?regierungsbezirk=Schwaben&traegerart=oeffentlich&size=L,XL&has_jobs=0` and check `routable`/`route_reason`.
- "Refresh clinic X" → `POST /api/crawl {"scope":"clinic","value":"<kez>","mode":"auto"}` → poll `GET /api/crawl/runs/{run_id}` until `status` is `done`/`failed` → re-query.
- "Find the career portal of X" → `POST /api/clinics/<kez>/refetch-career` (costs Firecrawl credits, cap with `max_credits`).
- "Match this CV" → `POST /api/cv` (file or `{"text":…}`) → use `matches[].why` to explain.
- Bulk / raw → PostgREST: `v_postings?employer_class=eq.clinic&status=eq.open&verify_status=eq.live` with `apikey` + `Accept-Profile: pflege_jobs`, paging 1000.

## Answer format
Lead with the number and the filter ("312 live nursing jobs at 87 Bavarian hospital sites, Oberbayern, ICU"). Cite `source_url` for individual postings. Flag `last_seen` older than 7 days and clinics with `routable=false`.

Machine-readable skill: `/skill/SKILL.md` · bundle `/skill/pflege-jobs.skill.md` · `/llms.txt`.
