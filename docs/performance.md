# Suche & Performance

Data is small (≈400 clinics, low thousands of postings). Speed problems come from *how* it is read, not from size.

## Now (app backend)
| layer | technique |
|---|---|
| server | v_postings + clinics + v_clinics loaded into memory on start / after each run (paged 1000, service key); TTL 10 min; `POST /api/refresh-cache` |
| facets | computed once per cache load, served from memory (`/api/facets`) |
| filtering | in-process on the cached lists; comma lists → sets; beds/fresh as integer compares |
| fuzzy search | `rapidfuzz` (`WRatio`/`partial_ratio`) over a pre-built index of `name+town+operator` and `title+employer+city+department`; top-k |
| CV match | regex profile from `patterns.json` → weighted score over cached jobs (role 40, department 25, city 20, qualification 10, skills 5) |
| state | SQLite `data/app.sqlite` (runs, logs, schedules, profiles, settings) |
| rules | `pflege_jobs/mechanics.py` registry; each mechanic's regexes are compiled once at load and after `config.reload()`; `/api/mechanics/{id}/test` runs one test file (≈0.1 s) |
| client | filters in URL hash; debounced inputs (200 ms); rendering only the visible page; facet counts from the API, never recomputed in the browser |

Limits: one process, one cache. Fine to ~50k postings. Above that, move filtering to Postgres (below).

## Next (PostgREST / Postgres) — needs DDL, blocked until a Supabase access token exists
```sql
-- 1. the joins the board asks for every time
create index on pflege_jobs.postings (clinic_id, status);
create index on pflege_jobs.postings (status, first_published desc);
create index on pflege_jobs.posting_observations (source_id, posting_id);

-- 2. fuzzy text
create extension if not exists pg_trgm;
create index on pflege_jobs.postings using gin (title gin_trgm_ops);
create index on pflege_jobs.employers using gin (name_display gin_trgm_ops);
create index on pflege_jobs.clinics using gin ((name || ' ' || town || ' ' || coalesce(operator,'')) gin_trgm_ops);

-- 3. full text
alter table pflege_jobs.postings add column fts tsvector
  generated always as (to_tsvector('german', coalesce(title,'') || ' ' || coalesce(description,''))) stored;
create index on pflege_jobs.postings using gin (fts);
--   query: postings?fts=fts(german).intensiv&status=eq.open

-- 4. stop the correlated subqueries in v_postings
alter table pflege_jobs.postings add column source_url text, add column source_codes text[];
-- fill in resolve_postings(); or rewrite the view with LATERAL joins:
--   left join lateral (select o.source_url from posting_observations o join sources s using(source_id)
--                      where o.posting_id=p.posting_id order by s.precedence limit 1) su on true

-- 5. facets server-side
create materialized view pflege_jobs.mv_facets as ... ; refresh after each run.
```
Rules of thumb: count on the base table (`postings`), never on the view with `count=exact`; `select=` only the columns the page shows; page with `limit/offset` ≤ 1000; `Prefer: count=planned` when an estimate is enough.

## Client
- URL = state (shareable, back button works).
- Virtualised table above ~500 rows (render window of 60).
- Chips filter on cached facet values; the city autocomplete searches the facet list, not the server.
- One fetch per view change, never per keystroke.
