-- pflege_jobs schema v0.1 — observations (per source) -> golden postings (precedence-resolved)
create schema if not exists pflege_jobs;

-- 1. Sources + precedence. Lower precedence number = more authoritative field value.
create table if not exists pflege_jobs.sources (
  source_id   smallint primary key,
  code        text unique not null,
  name        text not null,
  kind        text not null check (kind in ('registry','employer_ats','public_api','aggregator')),
  precedence  smallint not null,
  base_url    text
);

-- 2. Role taxonomy + inferred default grades (research mapping; grade is a default, not observed).
create table if not exists pflege_jobs.role_classes (
  role_class        text primary key,
  label_de          text not null,
  label_en          text not null,
  is_pflege         boolean not null default true,
  sort_order        smallint not null,
  tvoed_p_grade     text,
  tv_l_kr_grade     text,
  avr_caritas_grade text,
  grade_note        text
);

-- 3. Employers (identity = normalized name; conservative, no fuzzy merge).
create table if not exists pflege_jobs.employers (
  employer_id            bigserial primary key,
  name_norm              text unique not null,
  name_display           text not null,
  employer_class         text not null default 'unknown' check (employer_class in ('clinic','non_clinic','unknown')),
  class_rule             text,
  class_source           text not null default 'keyword_rule' check (class_source in ('keyword_rule','registry','manual')),
  aa_kundennummer_hashes text[] not null default '{}',
  website                text,
  clinic_type            text,
  first_seen             timestamptz not null default now(),
  last_seen              timestamptz not null default now()
);

-- 4. Clinic registry (phase 2: Bayerischer Krankenhausplan KeZ). Empty in v0.1, schema ready.
create table if not exists pflege_jobs.clinics (
  clinic_id        text primary key,
  name             text not null,
  operator         text,
  traegerart       text check (traegerart in ('oeffentlich','freigemeinnuetzig','privat')),
  town             text, plz text, landkreis text, regierungsbezirk text,
  versorgungsstufe text, beds int, website text, careers_url text, ats_type text,
  employer_id      bigint references pflege_jobs.employers(employer_id)
);

-- 5. Golden postings (one row per real posting; fields resolved from observations).
create table if not exists pflege_jobs.postings (
  posting_id          bigserial primary key,
  fuzzy_key           text not null,
  title               text,
  employer_id         bigint references pflege_jobs.employers(employer_id),
  offer_kind          text, hauptberuf text, alle_berufe text[],
  role_class          text references pflege_jobs.role_classes(role_class),
  role_rule           text, qualification_hint text, department_hint text,
  city text, plz text, region text, lat double precision, lon double precision,
  in_bavaria          boolean, n_locations int,
  employment_types    text[], shift_night_weekend boolean, homeoffice boolean, quereinstieg boolean,
  contract            text, fixed_term_months int, start_date date,
  salary_min numeric, salary_max numeric, salary_unit text, salary_note text,
  first_published     date, last_modified timestamptz, valid_until date,
  external_url        text, description text,
  enr_housing boolean, enr_housing_evidence text, enr_tariff text, enr_contact_emails text[],
  enr_language_req text, enr_bonus boolean, enr_childcare boolean, enr_anerkennung_mentioned boolean,
  first_seen          timestamptz, last_seen timestamptz,
  status              text not null default 'open' check (status in ('open','expired')),
  n_observations      int not null default 0,
  provenance          jsonb,          -- {field: source_code that supplied the value}
  updated_at          timestamptz not null default now()
);
create index if not exists postings_fuzzy_idx on pflege_jobs.postings(fuzzy_key);
create index if not exists postings_employer_idx on pflege_jobs.postings(employer_id);
create index if not exists postings_role_idx on pflege_jobs.postings(role_class);
create index if not exists postings_city_idx on pflege_jobs.postings(city);
create index if not exists postings_status_idx on pflege_jobs.postings(status);

-- 6. Raw per-source observations (append/upsert; identity = source_id + source_ref).
create table if not exists pflege_jobs.posting_observations (
  observation_id      bigserial primary key,
  source_id           smallint not null references pflege_jobs.sources(source_id),
  source_ref          text not null,
  source_url          text,
  posting_id          bigint references pflege_jobs.postings(posting_id),
  observed_at         timestamptz not null default now(),
  title               text,
  employer_name       text, employer_name_norm text,
  employer_id         bigint references pflege_jobs.employers(employer_id),
  employer_class      text, employer_class_rule text, aa_kundennummer_hash text,
  offer_kind          text, hauptberuf text, alle_berufe text[],
  role_class          text, role_rule text, qualification_hint text, department_hint text,
  city text, plz text, region text, lat double precision, lon double precision,
  in_bavaria          boolean, n_locations int, locations jsonb,
  employment_types    text[], shift_night_weekend boolean, homeoffice boolean, quereinstieg boolean,
  contract            text, fixed_term_months int, start_date date,
  salary_min numeric, salary_max numeric, salary_unit text, salary_note text,
  first_published     date, last_modified timestamptz, valid_until date,
  external_url        text, description text,
  enr_housing boolean, enr_housing_evidence text, enr_tariff text, enr_contact_emails text[],
  enr_language_req text, enr_bonus boolean, enr_childcare boolean, enr_anerkennung_mentioned boolean,
  details_fetched_at  timestamptz, details_error text,
  fuzzy_key           text, content_hash text,
  payload             jsonb,
  unique (source_id, source_ref)
);
create index if not exists obs_posting_idx on pflege_jobs.posting_observations(posting_id);
create index if not exists obs_fuzzy_idx on pflege_jobs.posting_observations(fuzzy_key);
create index if not exists obs_employer_norm_idx on pflege_jobs.posting_observations(employer_name_norm);

-- 7. Crawl runs (monitoring).
create table if not exists pflege_jobs.crawl_runs (
  run_id       bigserial primary key,
  source_id    smallint references pflege_jobs.sources(source_id),
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  n_fetched    int, n_new int, n_updated int, n_expired int,
  slice_counts jsonb, notes text
);

-- 8. jsonb fold aggregate: later rows override earlier -> order by precedence DESC so best wins.
create or replace function pflege_jobs.jsonb_concat_sfunc(a jsonb, b jsonb) returns jsonb
  language sql immutable as $$ select coalesce(a,'{}'::jsonb) || coalesce(b,'{}'::jsonb) $$;
drop aggregate if exists pflege_jobs.jsonb_merge_agg(jsonb);
create aggregate pflege_jobs.jsonb_merge_agg(jsonb) (
  sfunc = pflege_jobs.jsonb_concat_sfunc, stype = jsonb, initcond = '{}'
);

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
    city = g->>'city', plz = g->>'plz', region = g->>'region',
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
    enr_housing = (g->>'enr_housing')::boolean, enr_housing_evidence = g->>'enr_housing_evidence',
    enr_tariff = g->>'enr_tariff',
    enr_contact_emails = (select array_agg(x) from jsonb_array_elements_text(coalesce(g->'enr_contact_emails','[]'::jsonb)) x),
    enr_language_req = g->>'enr_language_req', enr_bonus = (g->>'enr_bonus')::boolean,
    enr_childcare = (g->>'enr_childcare')::boolean, enr_anerkennung_mentioned = (g->>'enr_anerkennung_mentioned')::boolean,
    first_seen = least(coalesce(p.first_seen, m.fs), m.fs), last_seen = m.ls,
    status = 'open', n_observations = m.n, provenance = m.prov, updated_at = now()
  from merged m where p.posting_id = m.posting_id;
  get diagnostics n_ref = row_count;
  return query select n_link, n_new, n_ref;
end $$;

-- 10. Expiry: postings not observed for p_days -> expired (monitoring).
create or replace function pflege_jobs.mark_expired(p_days int default 7) returns int
language sql as $$
  with u as (update pflege_jobs.postings set status = 'expired', updated_at = now()
             where status = 'open' and last_seen < now() - make_interval(days => p_days) returning 1)
  select count(*)::int from u
$$;

-- 11. Read views for dashboard + agents.
create or replace view pflege_jobs.v_postings as
  select p.posting_id, p.title, p.role_class, rc.label_de as role_label, rc.is_pflege,
         p.qualification_hint, p.department_hint, p.offer_kind, p.hauptberuf,
         e.employer_id, e.name_display as employer, e.employer_class, e.class_rule as employer_class_rule,
         p.city, p.plz, p.lat, p.lon, p.in_bavaria,
         p.employment_types, p.shift_night_weekend, p.contract, p.fixed_term_months, p.start_date,
         p.salary_min, p.salary_max, p.salary_unit,
         p.first_published, p.last_modified, p.first_seen, p.last_seen, p.status,
         p.external_url, p.enr_housing, p.enr_tariff, p.enr_contact_emails, p.enr_bonus, p.enr_childcare,
         p.n_observations, p.provenance,
         (select o.source_url from pflege_jobs.posting_observations o where o.posting_id = p.posting_id
            order by (select precedence from pflege_jobs.sources s where s.source_id = o.source_id) limit 1) as source_url
    from pflege_jobs.postings p
    left join pflege_jobs.employers e on e.employer_id = p.employer_id
    left join pflege_jobs.role_classes rc on rc.role_class = p.role_class;

create or replace view pflege_jobs.v_stats as
  select employer_class, role_class, status, count(*) as n
    from pflege_jobs.v_postings group by 1,2,3;

-- 12. RLS: public read (postings are public data), writes only via service role / owner.
alter table pflege_jobs.sources enable row level security;
alter table pflege_jobs.role_classes enable row level security;
alter table pflege_jobs.employers enable row level security;
alter table pflege_jobs.clinics enable row level security;
alter table pflege_jobs.postings enable row level security;
alter table pflege_jobs.posting_observations enable row level security;
alter table pflege_jobs.crawl_runs enable row level security;
do $$ declare t text; begin
  foreach t in array array['sources','role_classes','employers','clinics','postings','posting_observations','crawl_runs'] loop
    execute format('drop policy if exists public_read on pflege_jobs.%I', t);
    execute format('create policy public_read on pflege_jobs.%I for select to anon, authenticated using (true)', t);
  end loop; end $$;
grant usage on schema pflege_jobs to anon, authenticated, service_role;
grant select on all tables in schema pflege_jobs to anon, authenticated;
grant all on all tables in schema pflege_jobs to service_role;
grant usage, select on all sequences in schema pflege_jobs to service_role;
alter default privileges in schema pflege_jobs grant select on tables to anon, authenticated;
alter default privileges in schema pflege_jobs grant all on tables to service_role;
