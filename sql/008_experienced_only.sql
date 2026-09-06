-- 008: experienced nursing staff only.
--
-- Policy: the board serves qualified nurses who already hold the licence. Trainees (Ausbildung /
-- Azubi / duales Studium) and interns, working students and volunteers (Praktikum / Werkstudent /
-- FSJ / BFD / Hospitation) are out of scope, as are non-nursing roles.
--
-- The rule now lives in pflege_jobs/config.py:EXCLUDED_ROLE_CLASSES and is enforced at intake by
-- pflege_jobs.sinks.only_pflege(), so nothing new arrives. This migration removes what earlier runs
-- already stored and adds a constraint so it cannot come back through some other path.
--
-- Idempotent: safe to run repeatedly.

begin;

-- 1. What are we about to delete? (recorded in crawl_runs for auditability)
create temporary table _purge on commit drop as
  select posting_id, role_class, title from pflege_jobs.postings
   where role_class in ('ausbildung', 'werkstudent_praktikum', 'nicht_pflege');

insert into pflege_jobs.crawl_runs (source_id, started_at, finished_at, n_expired, slice_counts, notes)
select null, now(), now(), (select count(*) from _purge),
       (select coalesce(jsonb_object_agg(role_class, n), '{}'::jsonb)
          from (select role_class, count(*) as n from _purge group by 1) s),
       'migration 008: experienced-only policy — purged trainee/intern/non-nursing postings'
where exists (select 1 from _purge);

-- 2. Delete observations first (posting_observations.posting_id FKs postings), then the postings.
--    Observations of an excluded role are dropped outright: they describe a posting we do not serve.
delete from pflege_jobs.posting_observations o
 where o.posting_id in (select posting_id from _purge)
    or o.role_class in ('ausbildung', 'werkstudent_praktikum', 'nicht_pflege');

delete from pflege_jobs.postings p
 where p.posting_id in (select posting_id from _purge);

-- 3. Any posting left without observations is an orphan of the delete above.
delete from pflege_jobs.postings p
 where not exists (select 1 from pflege_jobs.posting_observations o where o.posting_id = p.posting_id);

-- 4. Employers that no longer have a single posting are dead weight in the filter lists.
delete from pflege_jobs.employers e
 where not exists (select 1 from pflege_jobs.postings p where p.employer_id = e.employer_id)
   and not exists (select 1 from pflege_jobs.posting_observations o where o.employer_id = e.employer_id)
   and not exists (select 1 from pflege_jobs.clinics c where c.employer_id = e.employer_id);

-- 5. Belt and braces: make the policy a database invariant, not just an application convention.
alter table pflege_jobs.postings drop constraint if exists postings_experienced_roles_only;
alter table pflege_jobs.postings add constraint postings_experienced_roles_only
  check (role_class is null or role_class not in ('ausbildung', 'werkstudent_praktikum', 'nicht_pflege'));

alter table pflege_jobs.posting_observations drop constraint if exists obs_experienced_roles_only;
alter table pflege_jobs.posting_observations add constraint obs_experienced_roles_only
  check (role_class is null or role_class not in ('ausbildung', 'werkstudent_praktikum', 'nicht_pflege'));

-- 6. Keep the taxonomy rows (role_classes is a reference table and resolve_postings joins it), but
--    mark them so the dashboard and any agent can see they are deliberately not served.
update pflege_jobs.role_classes
   set is_pflege = false,
       grade_note = coalesce(nullif(grade_note, ''), '') ||
                    case when coalesce(grade_note, '') = '' then '' else ' · ' end ||
                    'out of scope: board serves experienced staff only (migration 008)'
 where role_class in ('ausbildung', 'werkstudent_praktikum')
   and is_pflege is distinct from false;

commit;

-- Verification
--   select role_class, count(*) from pflege_jobs.postings group by 1 order by 2 desc;
--   -> ausbildung / werkstudent_praktikum / nicht_pflege must not appear.
