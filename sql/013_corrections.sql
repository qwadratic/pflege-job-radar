-- 013 -- TASK-180: hand-made data corrections live in the DB, each with a classified reason.
--
-- Ivan, 2026-09-29: every hand-made change must say why, when and how, with evidence that can be
-- re-checked -- and the reason must be in the database itself, classified, so it is always clear
-- where and why the DB deliberately differs from the state Krankenhausplan (or RHV) and where it
-- only fixes our own reading of it. Replaces data/ledger.jsonl (TASK-174); its 181 lines are copied
-- in by data/migrate_ledger_to_corrections.py.
--
-- One row per changed field (field '*' = whole row inserted). reason_code comes from
-- correction_reasons; the composite foreign key keeps a clinics reason off a postings row and
-- vice versa. overrides_source marks the codes where the DB knowingly differs from the primary source.
--
-- RLS on, no policies: same as every other pflege_jobs table, anon/authenticated read nothing;
-- the tools write through the pooler as postgres.
--
-- Applied 2026-09-29 by session 663542db over SUPABASE_DB_POOLER_URL (Ivan's go-ahead, same day).

begin;

create table pflege_jobs.correction_reasons (
  code text primary key,
  applies_to text not null check (applies_to in ('clinics', 'postings')),
  overrides_source boolean not null,
  description text not null,
  unique (code, applies_to)
);

insert into pflege_jobs.correction_reasons (code, applies_to, overrides_source, description) values
  ('parse_error', 'clinics', false,
   'Our parser misread the primary source (Krankenhausplan PDF, RHV XLSX); after the fix the DB equals the source.'),
  ('source_outdated', 'clinics', true,
   'The primary source is out of date (operator change, closure, rename); the DB knowingly differs from it.'),
  ('source_error', 'clinics', true,
   'The primary source itself is wrong, proven by a first-hand document (clinic website, Impressum, Handelsregister); the DB knowingly differs from it.'),
  ('not_in_source', 'clinics', false,
   'A site the primary source does not list (Reha from RHV, social facilities, private clinics), added by hand.'),
  ('board_location', 'clinics', false,
   'Where the site publishes its vacancies (careers_url, ats_type); the primary source has no such field.'),
  ('wrong_clinic', 'postings', false,
   'The matcher linked the posting to the wrong site, or to a site it does not belong to.'),
  ('duplicate', 'postings', false,
   'The same vacancy was stored twice (e.g. an old URL form); the copy is retired.'),
  ('not_a_vacancy', 'postings', false,
   'A stored row that is not a vacancy (a search-form link, a nationwide pool ad); retired.'),
  ('false_gone', 'postings', false,
   'Verification wrongly marked a live posting gone; reopened.'),
  ('role_misclassified', 'postings', false,
   'The role class was wrong (e.g. a non-nursing job classified as nursing); relabeled.');

create table pflege_jobs.corrections (
  id bigserial primary key,
  at timestamptz not null default now(),
  table_name text not null check (table_name in ('clinics', 'postings')),
  row_id text not null,
  field text not null,
  old_value jsonb,
  new_value jsonb,
  reason_code text not null,
  reason text not null check (btrim(reason) <> ''),
  evidence text[] not null check (cardinality(evidence) > 0),
  task text not null check (task ~ '^TASK-[0-9]+$'),
  made_by text not null check (btrim(made_by) <> ''),
  backup text,
  foreign key (reason_code, table_name) references pflege_jobs.correction_reasons (code, applies_to)
);

create index corrections_row on pflege_jobs.corrections (table_name, row_id);

alter table pflege_jobs.correction_reasons enable row level security;
alter table pflege_jobs.corrections enable row level security;

commit;
