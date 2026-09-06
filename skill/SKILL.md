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
| source code | __REPO_URL__ (public) · technical docs https://pflege-board.exe.xyz/docs.html |

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
