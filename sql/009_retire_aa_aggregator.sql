-- 009: retire the Bundesagentur für Arbeit (30, arbeitsagentur) and aggregator (40: Indeed, StepStone,
-- medi-karriere) sources. Only hospital career sites remain: employer_ats (20) and the new Firecrawl
-- agent source (25) that extracts the same career sites where no adapter / a bot wall exists.
--
-- Executed 2026-09-06 through PostgREST (service role; DML only, ids computed client-side) — this file is
-- the SQL equivalent for the record and for a psql re-run. Idempotent.

begin;

-- 0. the new source (kind employer_ats: same precedence tier as the career-site adapters)
insert into pflege_jobs.sources(source_id, code, name, kind, precedence, base_url)
values (25, 'firecrawl_agent', 'Firecrawl agent (career site, LLM-extracted)', 'employer_ats', 2, 'https://api.firecrawl.dev/v2/agent')
on conflict (source_id) do update set code=excluded.code, name=excluded.name, kind=excluded.kind, precedence=excluded.precedence, base_url=excluded.base_url;

-- 1. audit row: what is about to go
insert into pflege_jobs.crawl_runs (source_id, started_at, finished_at, n_expired, slice_counts, notes)
select null, now(), now(),
       (select count(*) from pflege_jobs.posting_observations where source_id in (30, 40)),
       (select coalesce(jsonb_object_agg(source_id::text, n), '{}'::jsonb)
          from (select source_id, count(*) n from pflege_jobs.posting_observations where source_id in (30, 40) group by 1) s),
       'migration 009: retired arbeitsagentur (30) + aggregator (40) observations';

-- 2. observations of the retired sources
delete from pflege_jobs.posting_observations where source_id in (30, 40);

-- 3. postings that only those sources had seen are now orphans
delete from pflege_jobs.postings p
 where not exists (select 1 from pflege_jobs.posting_observations o where o.posting_id = p.posting_id);

-- 4. inbox rows that came from aggregators / the egress crawler
delete from pflege_jobs.inbox
 where source_host ~* 'stepstone|indeed|medi-karriere|kununu|jobware|monster|glassdoor'
    or collector ~* 'egress|indeed|stepstone';

-- 5. employers left with nothing
delete from pflege_jobs.employers e
 where not exists (select 1 from pflege_jobs.postings p where p.employer_id = e.employer_id)
   and not exists (select 1 from pflege_jobs.posting_observations o where o.employer_id = e.employer_id)
   and not exists (select 1 from pflege_jobs.clinics c where c.employer_id = e.employer_id);

-- 6. rebuild golden fields / provenance / n_observations from what is left
select * from pflege_jobs.resolve_postings();

-- 7. the source rows themselves
delete from pflege_jobs.sources where source_id in (30, 40);

commit;

-- DDL — run when an access token / DB password is available (PostgREST cannot):
-- alter table pflege_jobs.sources drop constraint if exists sources_kind_check;
-- alter table pflege_jobs.sources add constraint sources_kind_check check (kind in ('registry','employer_ats'));
-- alter table pflege_jobs.employers drop column if exists aa_kundennummer_hashes;      -- optional; pipeline still sends '{}'
-- alter table pflege_jobs.posting_observations drop column if exists aa_kundennummer_hash;  -- optional
--
-- Verification
--   select source_id, count(*) from pflege_jobs.posting_observations group by 1;   -- only 20 (and 25 once the agent ran)
--   select count(*) from pflege_jobs.postings p where not exists (select 1 from pflege_jobs.posting_observations o where o.posting_id = p.posting_id);  -- 0
