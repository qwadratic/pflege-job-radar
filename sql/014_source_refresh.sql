-- 014 -- TASK-180: reason code for "a newer edition of the primary source changed the value".
--
-- sql/013 had no code for the case where the DB still holds last edition's value (e.g. an operator
-- from Krankenhausplan 2025) and the current edition (2026, 51. Fortschreibung) publishes a new one:
-- that is neither our misreading of the source (parse_error) nor a deliberate deviation from it.
-- After such a change the DB equals the source again, so overrides_source is false.
--
-- Applied 2026-09-30 by session 663542db over SUPABASE_DB_POOLER_URL (Ivan's go-ahead, same day).

insert into pflege_jobs.correction_reasons (code, applies_to, overrides_source, description) values
  ('source_refresh', 'clinics', false,
   'A newer edition of the primary source (e.g. the next Krankenhausplan Fortschreibung) changed the value; the DB takes it.');
