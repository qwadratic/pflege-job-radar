-- 012 -- TASK-105: expose enr_requirements, enr_language_req, enr_experience on pflege_jobs.v_postings.
--
-- classify.enrich_description() already extracts all three from the posting body and every live
-- adapter writes them onto pflege_jobs.postings -- this view is the one relation the app and
-- candidate-clinic matching (app/autopilot/matching.py) ever read, and it drops all three today
-- (confirmed live 2026-09-22: "select enr_requirements from v_postings" -> PostgREST 42703).
--
-- Written from pg_get_viewdef('pflege_jobs.v_postings', true) run live 2026-09-22 (Ivan, via
-- `supabase db query ... --linked`), NOT from sql/001_schema.sql's own view definition -- the live
-- view has drifted from 001 by more than the enr_pay_grade gap TASK-105 originally named (linked_towns
-- CTE, the employer_class CASE expression, the clinics join, verify_status/source_codes/source_url --
-- none of that is in 001 at all). This migration reproduces the live shape verbatim and only appends
-- the 3 new columns at the end, which is the one change CREATE OR REPLACE VIEW allows without dropping
-- and recreating the view (dropping it would cascade to whatever already depends on it). Reconciling
-- 001 itself to match live is a separate, larger job (TASK-94's own schema-drift finding) -- not
-- attempted here.
--
-- Anon-scope decision (Ivan, 2026-09-22, TASK-105 AC#4): enr_requirements and enr_experience stay
-- readable by anon -- sql/011_PENDING_anon_scope.sql (still not applied) was updated in the same round
-- to drop them from its own block-list, since the app server itself has no key but anon and needs both
-- for matching. enr_language_req was never planned to be blocked.
--
-- Apply with the same tool used to read the live definition:
--   supabase db query -f sql/012_task105_requirements_fields.sql --linked --project-ref "$SUPABASE_PROJECT_REF"

begin;

create or replace view pflege_jobs.v_postings as
  with linked_towns as (
    select postings.employer_id,
           array_agg(distinct lower(split_part(coalesce(postings.city, ''::text), ','::text, 1))) as towns
      from pflege_jobs.postings
     where postings.clinic_id is not null and postings.employer_id is not null
     group by postings.employer_id
  )
  select p.posting_id,
         p.title,
         p.role_class,
         rc.label_de as role_label,
         rc.is_pflege,
         p.qualification_hint,
         p.department_hint,
         p.department_raw,
         p.offer_kind,
         p.hauptberuf,
         e.employer_id,
         e.name_display as employer,
         case
           when p.clinic_id is not null then 'clinic'::text
           when e.employer_class = 'clinic'::text and lt.employer_id is not null
                and not (lower(split_part(coalesce(p.city, ''::text), ','::text, 1)) = any (lt.towns)) then 'unknown'::text
           else e.employer_class
         end as employer_class,
         e.employer_class as employer_class_raw,
         e.class_rule as employer_class_rule,
         p.clinic_id,
         c.name as clinic_name,
         c.operator as clinic_operator,
         c.regierungsbezirk,
         c.landkreis as clinic_landkreis,
         c.versorgungsstufe,
         c.traegerart,
         c.beds as clinic_beds,
         c.status as clinic_status,
         c.ats_type as clinic_ats,
         p.clinic_match_rule,
         p.city,
         p.plz,
         p.lat,
         p.lon,
         p.in_bavaria,
         p.employment_types,
         p.shift_night_weekend,
         p.contract,
         p.fixed_term_months,
         p.start_date,
         p.salary_min,
         p.salary_max,
         p.salary_unit,
         p.first_published,
         p.last_modified,
         p.first_seen,
         p.last_seen,
         p.status,
         p.verify_status,
         p.verify_http,
         p.verified_at,
         p.external_url,
         p.enr_housing,
         p.enr_tariff,
         p.enr_pay_grade,
         p.enr_contact_emails,
         p.enr_bonus,
         p.enr_childcare,
         p.n_observations,
         p.provenance,
         (select array_agg(distinct s.code)
            from pflege_jobs.posting_observations o
            join pflege_jobs.sources s using (source_id)
           where o.posting_id = p.posting_id) as source_codes,
         (select o.source_url
            from pflege_jobs.posting_observations o
           where o.posting_id = p.posting_id
           order by (select s.precedence from pflege_jobs.sources s where s.source_id = o.source_id)
           limit 1) as source_url,
         -- TASK-105: the 3 new columns, appended at the end (the only position CREATE OR REPLACE VIEW allows).
         p.enr_requirements,
         p.enr_language_req,
         p.enr_experience
    from pflege_jobs.postings p
    left join pflege_jobs.employers e on e.employer_id = p.employer_id
    left join linked_towns lt on lt.employer_id = e.employer_id
    left join pflege_jobs.clinics c on c.clinic_id = p.clinic_id
    left join pflege_jobs.role_classes rc on rc.role_class = p.role_class;

commit;
