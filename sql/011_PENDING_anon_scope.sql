-- 011 -- PENDING. NOT APPLIED. Applying this is Ivan's call, not an agent's.
--
-- Nothing in this file has been run against the live project. It was written and replayed against a
-- throwaway PostgreSQL 16 (docker, sql/001 + sql/010 only) on 2026-09-11; the live database was never
-- touched. There is no Supabase access token in this environment anyway (sql/010_inbox.sql:6-8).
--
-- WHAT IT CLOSES
--   The app redacts recruiter e-mail addresses below a `member` session (app/data.py:37 nulls
--   enr_contact_emails, app/data.py:53-70 masks e-mail-shaped substrings in every free-text value).
--   That is a control on the app door only. The anon JWT is published at skill/SKILL.md:29 and
--   GET /skill/SKILL.md is ungated, so the key is world-known -- and it reads the same rows straight
--   off PostgREST with no redaction at all, because sql/001_schema.sql:257 grants
--   `select on all tables in schema pflege_jobs` to anon and :260 grants it on every future table too.
--   The RLS policies at sql/001_schema.sql:253-254 are `for select ... using (true)` and narrow nothing.
--   pflege_jobs.inbox has no RLS at all (sql/010_inbox.sql:24-32), so raw crawler payloads plus
--   `collector` and `client_id` are readable with the same key.
--
--   After this migration anon/authenticated hold column-level SELECT on every relation in the schema
--   except the columns in `blocked` below, and inbox is RLS-on with an INSERT-only policy.
--
-- WHAT IT DOES NOT CLOSE -- read this before deciding it is enough:
--   * The key still reads every other column of every table: employers, clinics, crawl_runs,
--     posting_observations, v_*. This scopes the key, it does not gate it. Reads stay unmetered and
--     unauthenticated.
--   * `blocked` includes the free-text columns, not just enr_contact_emails, because nulling the field
--     alone redacts nothing: 569 of the 572 postings that carry an address in enr_contact_emails carry
--     the same address in `description` (tests/test_auth.py:588-589, app/data.py:39-41). Removing
--     enr_contact_emails and leaving `description` readable would be a change that looks like a fix and
--     is not one. If Ivan wants the narrow version, cut the array down to enr_contact_emails -- and
--     then do not describe the hole as closed.
--   * Obfuscated addresses, phone/fax numbers and "Bewerbungen an Frau Dr. X, Personalabteilung" in
--     prose are personal data on the same footing and survive both doors (app/data.py:43-51).
--
-- PRECONDITIONS -- what breaks the moment this is applied, with the line that breaks:
--   1. The app server authenticates to PostgREST with the ANON key and nothing else
--      (app/config.py:32 + :40, "no service-role/secret key anywhere in this codebase").
--      app/data.py:155 selects JOB_COLS -- which includes enr_contact_emails -- from v_postings, so the
--      snapshot build answers 42501 and the whole board goes dark, not just the e-mail column.
--      app/data.py:406 selects `*` from postings, same. The paying-customer feature the redaction was
--      written to preserve (app/data.py:28) dies with it.
--      => The app must first read through a key that is not `anon` (service_role, or a scoped agent
--         key/role of its own). That change is not in this file; it is the actual decision.
--   2. app/data.py:439 and :457 read pflege_jobs.inbox with the anon key. RLS-on with no SELECT policy
--      returns zero rows (not an error), so the Pro dashboard's inbox panel silently empties until the
--      app moves off the anon key as in (1).
--   3. crawlers/load_crawl_output.py:36 POSTs to /rest/v1/inbox with the anon key. The
--      inbox_anon_insert policy below is there to keep exactly that path working. It preserves today's
--      behaviour; it does not widen it. Whatever table-level INSERT grant makes that POST work was not
--      made by any file in sql/ (sql/010_inbox.sql:29-31) and is not re-granted here -- if that grant
--      is ever revoked, the policy alone will not bring the path back.
--   4. `alter default privileges ... revoke` only cancels a matching `alter default privileges ... grant`
--      made by the SAME role. If sql/001 was executed as a different role than the one running this
--      file, line 260 of 001 is still in force and the revoke is a silent no-op -- run it as that role,
--      or add `for role <owner>`. Check afterwards with:
--        select defaclrole::regrole, defaclacl from pg_default_acl d
--          join pg_namespace n on n.oid = d.defaclnamespace where n.nspname = 'pflege_jobs';
--
-- OPEN QUESTION this file deliberately does not answer: whether to keep publishing a shared anon key at
-- all, or move agents onto scoped per-agent keys and stop. Scoping the key is not the same as deciding
-- to have one.

begin;

-- 1 + 2. Drop anon's blanket SELECT, hand back column-level SELECT on everything except `blocked`.
-- Written as a catalog loop on purpose: sql/ has drifted from live (v_clinics and v_clinic_portals are
-- queried in docs/api.md:266 and skill/references/api.md:92-93 but are created by no file in sql/, and
-- the live v_postings carries clinic_id / verify_status / source_codes that sql/001:223 does not).
-- A hand-typed column list would silently drop whatever is live and unlisted; this cannot.
do $$
declare
  r record;
  cols text;
  blocked text[] := array[
    'enr_contact_emails',      -- the recruiter addresses themselves
    'description',             -- same addresses in prose in 569 of 572 rows
    'enr_housing_evidence', 'enr_requirements', 'enr_experience',   -- free-text excerpts of the same ad
    'payload'                  -- posting_observations.payload = the original record, ad body included
  ];
begin
  for r in
    select c.relname
      from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'pflege_jobs' and c.relkind in ('r', 'p', 'v', 'm')
     order by c.relname
  loop
    execute format('revoke select on pflege_jobs.%I from anon, authenticated', r.relname);
    continue when r.relname = 'inbox';   -- no anon read of raw crawler rows at all; RLS below is the second lock
    select string_agg(quote_ident(a.attname), ', ' order by a.attnum) into cols
      from pg_attribute a
     where a.attrelid = format('pflege_jobs.%I', r.relname)::regclass
       and a.attnum > 0 and not a.attisdropped and not (a.attname = any(blocked));
    if cols is null then
      raise exception 'pflege_jobs.% has no readable column left', r.relname;   -- fail loudly, do not skip
    end if;
    execute format('grant select (%s) on pflege_jobs.%I to anon, authenticated', cols, r.relname);
  end loop;
end $$;

-- Future tables must not inherit a blanket read (sql/001_schema.sql:260). See precondition 4.
alter default privileges in schema pflege_jobs revoke select on tables from anon, authenticated;

-- 3. RLS on inbox. No SELECT policy -> anon reads nothing even if a table-level grant reappears.
-- service_role bypasses RLS, so pflege_jobs/cli.py's drain (via the pflege-ingest function) is unaffected.
alter table pflege_jobs.inbox enable row level security;
drop policy if exists inbox_anon_insert on pflege_jobs.inbox;
create policy inbox_anon_insert on pflege_jobs.inbox for insert to anon, authenticated with check (true);

commit;

-- VERIFY (as anon, after applying):
--   set role anon;
--   select enr_contact_emails from pflege_jobs.v_postings limit 1;   -- expect 42501 permission denied
--   select posting_id, title from pflege_jobs.v_postings limit 1;    -- expect a row
--   select * from pflege_jobs.postings limit 1;                      -- expect 42501 (`*` needs every column)
--   select count(*) from pflege_jobs.inbox;                          -- expect 42501 (grant gone)
--   reset role;
-- and over REST, with the published key:
--   curl "$REST/v_postings?select=enr_contact_emails&limit=1" $H     -- expect {"code":"42501",...}
--   curl "$REST/v_postings?select=title,city&limit=1" $H             -- expect a row
--
-- ROLLBACK (exact inverse; revoking a table-level privilege also revokes the column-level grants on
-- that table, so the first statement clears the column ACLs this file wrote):
--   begin;
--   revoke select on all tables in schema pflege_jobs from anon, authenticated;
--   grant  select on all tables in schema pflege_jobs to   anon, authenticated;
--   alter default privileges in schema pflege_jobs grant select on tables to anon, authenticated;
--   drop policy if exists inbox_anon_insert on pflege_jobs.inbox;
--   alter table pflege_jobs.inbox disable row level security;
--   commit;
-- (`all tables in schema` covers tables and views; if a materialized view is ever added to this schema,
--  add `grant select on pflege_jobs.<matview> to anon, authenticated;` to the rollback by name.)
