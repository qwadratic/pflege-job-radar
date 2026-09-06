# Long-running plan — pflege_jobs

## Daily job (automated once the repo is pushed)
`.github/workflows/daily.yml` → `python -m pflege_jobs.orchestrate --stages all` (04:17 UTC, ~40–60 min, GitHub Actions free tier):
aa → ats (B-ITE, softgarden, rexx, d.vinci, mein-check-in, group portals, top-20 crawler) → browser (P&I Helios, Playwright seeds)
→ inbox → egress (StepStone, capped by EGRESS_MAX_PAGES=10/day ≈ 250 cards; raise when confident) → link (registry + cross-source)
→ verify (≤5 workers, then supersede AA at covered sites, expire 7 days) → publish (assets). Every stage logs to `crawl_runs`; stages
are independent, checkpointed and idempotent, so a failed stage just retries next day.
Secrets needed: SUPABASE_URL, SUPABASE_ANON_KEY, PFLEGE_INGEST_URL, PFLEGE_INGEST_SECRET, ANTHROPIC_API_KEY (egress only).

## Backlog, ordered by verified openings per hour of work
1. Personio + SmartRecruiters adapters (public JSON: `<slug>.jobs.personio.de/xml`, `api.smartrecruiters.com/v1/companies/<id>/postings`) — 4 sites, ~1 h
2. umantis via Playwright (16 sites) — list is JS-paged; `Jobs/<n>` + click — ~2 h
3. Egress volume: StepStone all 58 Bayern pages + per-city pages, Indeed parser — needs API key; ~1 h + cost
4. 257 census sites without ATS: second-pass discovery (sitemap.xml job URLs, "Stellenangebote" link text variants) — ~3 h, expect +20–40 sites
5. Helios München West/Perlach detail pages (JSON-LD) via egress for dates/descriptions — 1 h
6. Alerting: `v_run_health` view (last run per stage, null-rate of role_class/city, per-site count deltas > 30 %) + a Slack/mail hook — 2 h
7. Candidate side: `candidates`, `candidate_preferences`, `matches`, `outreach_log` + matcher (hard filters + weighted score) — 1 day
8. Clinic contacts registry (Pflegedirektion emails per site, from descriptions + career pages) — 1 day

## Kill criteria per adapter
Adapter is disabled (seed `enabled:false`) when 3 consecutive runs return 0 rows for a site that had >0 on Arbeitsagentur, or when the
host returns 403/429 twice — logged in `crawl_runs.notes`.

## Session 2026-09-06 — what changed

Done: Indeed + JS-portal crawlers (crawlers/portals.py), vendor ATS adapters (crawlers/vendor_adapters.py),
second-pass ATS discovery (crawlers/ats_discover2.py), experienced-only policy (config.EXCLUDED_ROLE_CLASSES
+ sql/008, applied via data/apply_008.py), Krankenhausplan 2026 sync (data/sync_krankenhausplan_2026.py),
dashboard served by systemd unit `pflege-web` on :8000.

## Still open, in priority order
1. **Edge function redeploy** — edge/pflege-ingest/index.ts has an un-deployed fix: `ats_type`/`careers_url`
   now use `coalesce(nullif(excluded.x,''), clinics.x)` so a registry push can no longer erase discovered
   ATS labels (this bug cost 6 labels once already; the client-side guard in cli.py currently covers it).
   Needs a Supabase **access token** (sbp_...) — the service-role key cannot deploy functions.
2. **DDL for sql/008** — the CHECK constraints (`postings_experienced_roles_only`) are still not applied;
   only the DELETEs ran. PostgREST cannot issue DDL. Needs the access token or the DB password.
3. **222 census sites still unlabeled** — of 407 clinics, 185 have an ATS. Next angles: Playwright on the
   ~58 sites where discovery found a careers_url but no vendor fingerprint (JS-rendered apply buttons).
4. **Aggregator retirement** — measure `unique clinic postings` per source each run; disable a source when
   its unique clinic contribution drops below ~5 %. Today: career sites 1,298 unique, AA 3,471, aggregators
   2,273 — but only 1,471 of 2,247 clinic postings are reachable from career sites, so not yet.
5. **Krankenhausplan name/town split** — the 51. Fortschreibung dropped the "Träger" separator line, so the
   free-text split agrees with the trusted 2025 parse on only ~28 % of names. Structured columns (beds,
   fachrichtungen, versorgungsstufe, traegerart: 91-100 %) are synced; names/towns are deliberately kept
   from 2025. Fix properly by parsing with word coordinates instead of line order.
6. ~~Verify pass~~ — done; only 10 were unverified (the inbox loader verifies inline), all now 'live'.
7. **Dashboard resilience** (done 2026-09-06) — the bare https://pflege-board.exe.xyz/ maps to the VM's
   *default* port 8501, but the dashboard had been put on 8000, so the URL 503'd. Unit now serves 8501.
   Separately the anon role hit statement timeouts (HTTP 500) on v_postings: `count=exact` was making
   PostgREST re-run the view's correlated subqueries (source_codes, source_url) over every row on each
   page. Now the total comes from the base table, source_url is resolved lazily (detail + CSV only),
   and pages retry once on 500. Proper fix is still server-side -> item 8.
8. **v_postings correlated subqueries** — `source_url` and `source_codes` are per-row subqueries
   (source_url additionally re-looks-up sources.precedence). They should become LATERAL joins or a
   materialised column. Needs DDL, so blocked on the same credential as items 1-2.
