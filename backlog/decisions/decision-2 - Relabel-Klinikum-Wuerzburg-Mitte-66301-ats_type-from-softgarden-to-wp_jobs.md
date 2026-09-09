---
id: decision-2
title: Relabel Klinikum Würzburg Mitte (66301) ats_type from softgarden to wp_jobs
date: '2026-09-09 00:00'
status: applied
---
**Update 2026-09-09 (main session):** not actually blocked -- the workflow agent tried `SUPABASE_URL`
(the keyless read proxy, `supabase.int.exe.xyz`), which never accepts writes regardless of key. The
established pattern this session (`data/apply_008.py`, `data/repair_split_merged.py`) is the DIRECT
project host `https://klkxfvieaxpjlplloljn.supabase.co` + `SUPABASE_SECRET_KEY` + `Content-Profile:
pflege_jobs` header -- that works fine for writes. Applied: `ats_type` set to `''` (empty, the
convention sibling wp_jobs-fallback rows use, confirmed via a live read first) via a direct PATCH.
Re-ran the adapter for real afterward: `GET /api/crawl/estimate?clinic_id=66301` now reports
`board_rows:23`; a real `POST /api/crawl` ingest run landed 12 new, verified-live postings (5 -> 17
open jobs for this clinic). Closed.

## Context

clinic_id 66301 (Klinikum Würzburg Mitte gGmbH, 647 beds) is labelled `ats_type='softgarden'` in the
`clinics` table, but its live careers page
(https://www.kwm-klinikum.de/beruf-chancen/stellenanzeigen/) has zero softgarden markers -- the site
was re-platformed to a self-hosted CMS (Contao-style, `<base href>`, own
`/stellenanzeigen/pflege-und-funktionsdienst/` category page, `?job=<uuid>` detail pages).
`softgarden.find_host()` returns `(None, None)` for this URL, so the current route ends in
`_seed_obs -> {"error": "no softgarden host found"}` -- `board_rows:0, confidence:none` in
`GET /api/crawl/estimate?clinic_id=66301` is a wrong label, not "no jobs".

Verified live (2026-09-09): with `ats_type` routed to `crawl_wp_jobs` instead, and after fixing the
two shared-helper bugs this session found (`<base href>` resolution, a11y-hidden `<h1>` title
false-positive -- see `crawlers/vendor_adapters.py` changes same commit), the adapter returns 25
real postings with correct titles from the nursing category page, e.g. "Pflegefachkraft (m/w/d) für
die Wochenbettstation", "Pflegefachkräfte (m/w/d) für unsere Teams der Intensivstationen".
`data/registry/top20_seeds.json` already points this clinic's career URL at the correct
`pflege-und-funktionsdienst` category page -- only the DB label is wrong.

## Decision

Blocked, not applied. Intended change: `UPDATE clinics SET ats_type = 'wp_jobs' WHERE clinic_id =
66301;` (or `''`, whichever this table's convention uses for "route through the generic wp_jobs
fallback" -- check sibling rows before applying). `careers_url` needs no change.

Not applied because this session has no working Supabase credential: `.env`'s `SUPABASE_ANON_KEY`
is the literal placeholder string `implicit`, and the present `SUPABASE_SECRET_KEY` returns
`PGRST205 (table 'clinics' not found in schema cache)` against `SUPABASE_URL` -- either wrong
project/schema or a key without access, not a live DB credential. Matches
`memory/pflege-board-state-2026-09.md`: "no Supabase token" as of this session.

## Consequences

Until this row is updated, clinic 66301 (647 beds) keeps reporting `board_rows:0, confidence:none`
in the crawl estimate and is silently excluded from real coverage, even though `crawlers.vendor_adapters.crawl_wp_jobs`
can now fetch it correctly. No code change is needed beyond what's already in this commit --
whoever has a working Supabase credential can apply the one-row `UPDATE` above and re-run the estimate to confirm `board_rows > 20`.
