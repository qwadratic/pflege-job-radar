-- Incremental migration for the 2026-09-18 crawler review (TASK-73 AC5/AC6, TASK-74 AC3).
-- sql/001_schema.sql uses "create table if not exists" so it cannot add columns to an
-- already-existing table -- this file does that part, then reapplies the two changed functions.
-- Safe to run once; every statement is idempotent (IF NOT EXISTS / OR REPLACE / IF EXISTS).

alter table pflege_jobs.postings
  add column if not exists department_raw text,
  add column if not exists city_override text,
  add column if not exists plz_override text,
  add column if not exists enr_pay_grade text,
  add column if not exists enr_pay_text text,
  add column if not exists enr_requirements text,
  add column if not exists enr_experience text;

alter table pflege_jobs.posting_observations
  add column if not exists department_raw text,
  add column if not exists enr_pay_grade text,
  add column if not exists enr_pay_text text,
  add column if not exists enr_requirements text,
  add column if not exists enr_experience text;

-- 9. Resolver: link observations -> postings, then rebuild golden fields by precedence.
-- Linking rule (documented, deterministic):
--   a) observation already linked -> keep.
--   b) else if exactly ONE posting shares fuzzy_key AND that posting has no observation from the
--      same source -> link (cross-source dedupe).
--   c) else create a new posting.
-- Field rule: for each field take the non-null value from the observation with the lowest
-- source precedence; ties -> most recent observed_at. first_seen=min, last_seen=max.
create or replace function pflege_jobs.resolve_postings() returns table(linked int, created int, refreshed int)
language plpgsql as $$
declare r record; cand bigint; n_cand int; n_link int := 0; n_new int := 0; n_ref int := 0;
begin
  -- employer ids
  update pflege_jobs.posting_observations o set employer_id = e.employer_id
    from pflege_jobs.employers e
   where o.employer_id is null and o.employer_name_norm = e.name_norm;

  for r in select observation_id, source_id, fuzzy_key from pflege_jobs.posting_observations
            where posting_id is null order by observation_id loop
    cand := null; n_cand := 0;
    select count(*), min(p.posting_id) into n_cand, cand
      from pflege_jobs.postings p
     where p.fuzzy_key = r.fuzzy_key
       and not exists (select 1 from pflege_jobs.posting_observations x
                        where x.posting_id = p.posting_id and x.source_id = r.source_id);
    if n_cand = 1 then
      update pflege_jobs.posting_observations set posting_id = cand where observation_id = r.observation_id;
      n_link := n_link + 1;
    else
      insert into pflege_jobs.postings(fuzzy_key) values (r.fuzzy_key) returning posting_id into cand;
      update pflege_jobs.posting_observations set posting_id = cand where observation_id = r.observation_id;
      n_new := n_new + 1;
    end if;
  end loop;

  -- golden rebuild
  with obs as (
    select o.posting_id, s.code, s.precedence, o.observed_at,
           jsonb_strip_nulls(to_jsonb(o) - 'observation_id' - 'source_id' - 'source_ref' - 'source_url'
             - 'posting_id' - 'observed_at' - 'payload' - 'locations' - 'content_hash' - 'fuzzy_key'
             - 'details_fetched_at' - 'details_error' - 'employer_name' - 'employer_name_norm'
             - 'employer_class' - 'employer_class_rule' - 'aa_kundennummer_hash') as f
      from pflege_jobs.posting_observations o join pflege_jobs.sources s using (source_id)
  ), merged as (
    select posting_id,
           pflege_jobs.jsonb_merge_agg(f order by precedence desc, observed_at asc) as g,
           pflege_jobs.jsonb_merge_agg((select jsonb_object_agg(k, code) from jsonb_each(f) e(k, v))
                                       order by precedence desc, observed_at asc) as prov,
           min(observed_at) fs, max(observed_at) ls, count(*) n
      from obs group by posting_id
  )
  update pflege_jobs.postings p set
    title = g->>'title', employer_id = (g->>'employer_id')::bigint,
    offer_kind = g->>'offer_kind', hauptberuf = g->>'hauptberuf',
    alle_berufe = (select array_agg(x) from jsonb_array_elements_text(coalesce(g->'alle_berufe','[]'::jsonb)) x),
    role_class = g->>'role_class', role_rule = g->>'role_rule',
    qualification_hint = g->>'qualification_hint', department_hint = g->>'department_hint',
    department_raw = g->>'department_raw',
    -- city/plz are the only two golden fields with a manual-correction escape hatch: every other
    -- field here is fully recomputed from posting_observations with nothing to check first, so a
    -- plain PATCH straight onto postings.city/plz used to be silently undone by the very next crawl
    -- (tools/reverify_and_clean.py apply --write-city, TASK-74 AC3, 2026-09-16). No crawler writes
    -- *_override -- only that tool does -- so an ordinary re-crawl leaves it untouched.
    city = coalesce(p.city_override, g->>'city'), plz = coalesce(p.plz_override, g->>'plz'), region = g->>'region',
    lat = (g->>'lat')::double precision, lon = (g->>'lon')::double precision,
    in_bavaria = (g->>'in_bavaria')::boolean, n_locations = (g->>'n_locations')::int,
    employment_types = (select array_agg(x) from jsonb_array_elements_text(coalesce(g->'employment_types','[]'::jsonb)) x),
    shift_night_weekend = (g->>'shift_night_weekend')::boolean, homeoffice = (g->>'homeoffice')::boolean,
    quereinstieg = (g->>'quereinstieg')::boolean,
    contract = g->>'contract', fixed_term_months = (g->>'fixed_term_months')::int,
    start_date = (g->>'start_date')::date,
    salary_min = (g->>'salary_min')::numeric, salary_max = (g->>'salary_max')::numeric,
    salary_unit = g->>'salary_unit', salary_note = g->>'salary_note',
    first_published = (g->>'first_published')::date, last_modified = (g->>'last_modified')::timestamptz,
    valid_until = (g->>'valid_until')::date, external_url = g->>'external_url', description = g->>'description',
    enr_pay_grade = g->>'enr_pay_grade', enr_pay_text = g->>'enr_pay_text',
    enr_requirements = g->>'enr_requirements', enr_experience = g->>'enr_experience',
    enr_housing = (g->>'enr_housing')::boolean, enr_housing_evidence = g->>'enr_housing_evidence',
    enr_tariff = g->>'enr_tariff',
    enr_contact_emails = (select array_agg(x) from jsonb_array_elements_text(coalesce(g->'enr_contact_emails','[]'::jsonb)) x),
    enr_language_req = g->>'enr_language_req', enr_bonus = (g->>'enr_bonus')::boolean,
    enr_childcare = (g->>'enr_childcare')::boolean, enr_anerkennung_mentioned = (g->>'enr_anerkennung_mentioned')::boolean,
    first_seen = least(coalesce(p.first_seen, m.fs), m.fs), last_seen = m.ls,
    -- status is deliberately left untouched here: this resolver runs for every posting that has ANY
    -- observation, including ones a prior mark_expired() already closed, and it must not silently
    -- reopen them just because their old observations are still on file (TASK-73 AC5/AC6; the
    -- unconditional status='open' this file carried until 2026-09-18 would have reopened all 202
    -- postings expired at the time). A brand-new posting still starts 'open' via the column default.
    n_observations = m.n, provenance = m.prov, updated_at = now()
  from merged m where p.posting_id = m.posting_id;
  get diagnostics n_ref = row_count;
  return query select n_link, n_new, n_ref;
end $$;

-- 10. mark_expired(p_days)/expire_days was dead code (TASK-73 AC6): its only caller was
-- pflege_jobs/orchestrate.py's stage_verify, and orchestrate.py itself is not scheduled anywhere
-- (deploy/github-workflow-daily.yml is reference-only; app/scheduler.py runs the real crawls and
-- never calls it). It was also unsafe to schedule as-is: app/crawl.py's inbox dedupe drops every
-- re-crawled URL already on file, so last_seen freezes at first sighting and firing this would have
-- expired postings that still verify live. Dropped rather than left to rot; the real expiry signal
-- is the daily verify pass writing verify_status='gone'.
drop function if exists pflege_jobs.mark_expired(int);
