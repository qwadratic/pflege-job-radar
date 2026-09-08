-- 010: pflege_jobs.inbox, documented for the record.
--
-- This table already exists live and has been receiving rows since before this file was written --
-- none of sql/001, 002, 008 or 009 create it (009:30-31 is the only prior mention, a DELETE against
-- rows from retired sources). This is a reconstruction from the live table (columns, cardinality,
-- and the query shapes pflege_jobs/cli.py + web/collect.html actually run against it), NOT a DDL
-- that has been applied -- there is no Supabase access token in this environment, so CREATE TABLE
-- IF NOT EXISTS below is inert if run against the live DB (table already there) and is otherwise
-- untested. Row shape is documented in docs/overview.md ("inbox -- raw crawler rows..."); this file
-- is the schema, not a second copy of that description.
--
-- Row shape, one entry per crawler/collector post:
--   {"kind":"jobposting|listing|probe", "source_host":"...", "source_url":"...",
--    "payload":{...}, "collector":"vendor-adapters-...|ats-discover2-v1|firecrawl-agent|...",
--    "client_id":"..."}
-- processed_at/process_note are set by pflege_jobs/cli.py:_drain_once via the pflege-ingest
-- inbox_ack op once a row has been turned into an observation (or skipped/acked with a reason).
--
-- source_url is intentionally NOT unique: as of 2026-09-06 the live table has 2841 distinct
-- source_url values across 4297 rows -- the same board gets re-posted by every clinic label that
-- shares it (e.g. a group portal fetched once per clinic), and de-duplication happens at the
-- posting_observations layer (source_id, source_ref), not here.
--
-- RLS: deliberately NOT enabled here. sql/001's RLS block names seven tables (sources,
-- role_classes, employers, clinics, postings, posting_observations, crawl_runs) and inbox has never
-- been one of them. A plain POST {SUPABASE_URL}/rest/v1/inbox with just the anon key works today
-- (confirmed live 2026-09-08, tools/kindt_healthcheck.sh's probe-row check) -- if inbox has RLS off
-- and a plain table-level grant, enabling RLS here (with no matching policy) would start rejecting
-- every such POST with 42501. Whatever grant currently lets anon INSERT here was not made by any
-- file in sql/ either; it is not reconstructed below because it cannot be verified without an
-- access token. Do not add `alter table pflege_jobs.inbox enable row level security` to this file
-- for that reason.

create table if not exists pflege_jobs.inbox (
  inbox_id      bigint generated always as identity primary key,
  kind          text not null,                 -- 'jobposting' | 'listing' | 'probe'
  collector     text,                          -- e.g. 'vendor-adapters-default', 'ats-discover2-v1'
  client_id     text,
  source_host   text,
  source_url    text,                          -- not unique -- see note above
  payload       jsonb,
  received_at   timestamptz not null default now(),
  processed_at  timestamptz,                   -- null = still queued for pflege_jobs.cli:cmd_inbox
  process_note  text
);

-- cmd_inbox's drain query is `select * from inbox where processed_at is null order by inbox_id limit 1000`.
-- A partial index keeps that query cheap regardless of how large the processed tail grows.
create index if not exists inbox_unprocessed_idx on pflege_jobs.inbox (inbox_id) where processed_at is null;
create index if not exists inbox_received_at_idx on pflege_jobs.inbox (received_at);
create index if not exists inbox_source_host_idx on pflege_jobs.inbox (source_host);

-- PURGE: there is no delete path today (REST DELETE returns 42501 for the anon-level key this
-- environment has, and the pflege-ingest edge function's op list had no inbox delete). The
-- inbox_purge op added to edge/pflege-ingest/index.template.ts (rendered into index.ts by
-- edge/build_ingest.py) is the caller-side counterpart to the guarded query below -- same WHERE
-- clause, same hard-capped LIMIT, run over the function's own SUPABASE_DB_URL. It is committed but
-- not deployed: deploying an edge function needs the same access token this environment lacks.
--
-- Retention policy the op encodes (guards live in the op's SQL, not in whatever a caller sends):
--   DELETE   collector='t' AND process_note='discarded: rate-limit probe'   (1201 rows as of 2026-09-06 --
--            junk from a probe run that hit a rate limit; pure noise, no downstream reference)
--   KEEP     process_note like 'ats set:%'                                  (46 rows as of 2026-09-06 --
--            the only record of how each clinics.ats_type/careers_url was set; never delete, even
--            if a caller's kinds/collectors/notes_like filter would otherwise match)
--   NEVER    processed_at is null                                          (still queued for cmd_inbox)
--
-- The same query, for a manual psql run once a token/DB password is available:
--   with victims as (
--     select inbox_id from pflege_jobs.inbox
--      where processed_at is not null
--        and process_note not like 'ats set:%'
--        and collector = 't' and process_note = 'discarded: rate-limit probe'   -- the junk cited above
--      order by inbox_id
--      limit 1201
--   ), deleted as (delete from pflege_jobs.inbox where inbox_id in (select inbox_id from victims) returning 1)
--   select count(*) from deleted;
--
-- Verification
--   select collector, kind, process_note, count(*) from pflege_jobs.inbox
--    where processed_at is not null group by 1,2,3 order by 4 desc limit 20;
--   select count(*) from pflege_jobs.inbox where processed_at is null;  -- must be unchanged by any purge call
