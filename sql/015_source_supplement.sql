-- 015 -- TASK-431: reason code for "a field the primary source does not carry, filled from a second official source".
--
-- tools/fill_clinic_plz.py fills clinics.plz (0 of 651) from the Krankenhausverzeichnis 2024 (Statistische Aemter) and
-- records every PLZ in pflege_jobs.corrections. None of the codes of sql/013 and sql/014 fits: the plan PDF has no PLZ
-- (not parse_error), the DB does not differ from a source (not source_outdated / source_error), the site is not new
-- (not not_in_source). After the change the DB equals the second source, so overrides_source is false.
--
-- NOT APPLIED by the session that wrote it (no DB writes in TASK-431 step 1): Ivan runs it before `fill_clinic_plz.py --apply`.

insert into pflege_jobs.correction_reasons (code, applies_to, overrides_source, description) values
  ('source_supplement', 'clinics', false,
   'A field the primary source does not carry (e.g. the PLZ of a Krankenhausplan site), filled from a second official source (Krankenhausverzeichnis).')
  on conflict (code, applies_to) do nothing;
