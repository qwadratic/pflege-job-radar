# Pipeline runbook (repo: __REPO_URL__)

Env (`.env`): SUPABASE_URL, SUPABASE_ANON_KEY, PFLEGE_INGEST_URL (…/functions/v1/pflege-ingest), PFLEGE_INGEST_SECRET.
Install: `pip install requests pytest --break-system-packages`; tests: `python -m pytest -q tests`.

## Refresh (same as backfill, idempotent)
```bash
set -a; . ./.env; set +a
python -m pflege_jobs.cli run --sink edge --details clinic        # pull all slices → descriptions for clinic rows → upsert → resolve
# afterwards expire stale rows (7 days):
curl -X POST "$PFLEGE_INGEST_URL" -H "Authorization: Bearer $SUPABASE_ANON_KEY" -H "x-ingest-secret: $PFLEGE_INGEST_SECRET" -H "Content-Type: application/json" -d '{"expire_days":7,"crawl_run":{"source_id":30,"notes":"daily"}}'
```
Verify liveness after every refresh: `python -m pflege_jobs.cli verify --workers 6` (Arbeitsagentur rows via the
details endpoint, career-site rows via HTTP GET + title check; 8 workers trigger 429s — use ≤6). Results land in
`postings.verify_status/verify_http/verified_at/verify_note`; `gone` expires the posting.

Steps individually: `counts` (live per-slice totals, no writes) · `pull --out data/raw.json` · `details --only clinic` · `load --sink edge|csv|sql|staging` · `renormalize` (re-run classification from stored payloads after a rule change, no crawl).

## Arbeitsagentur specifics
Search `…/pc/v6/jobs` (params: berufsfeld|was, wo=Bayern, angebotsart 1/4/34, zeitarbeit=false, pav=false, size=100, page). Total in `maxErgebnisse`, rows in `ergebnisliste`. Details `…/pc/v4/jobdetails/{base64(refnr)}` → `stellenangebotsBeschreibung`. Header `X-API-Key: jobboerse-jobsuche`. Slices in `config.AA_SLICES` (2 Berufsfelder × Arbeit/Ausbildung/Praktikum + keyword slices for Hebamme, OTA, ATA, PDL, Stationsleitung, Praxisanleiter, Pflegeexperte, Werkstudent).

## Career-site snapshot import (source employer_ats)
`python -m pflege_jobs.cli load-board --csv data/board_snapshot_<date>.csv --sink edge` — maps the pflege-board CSV
(clinic, city, department, job_title, qualification, pay_grade, pay_text, housing, housing_quote, employment_type,
requirements_must, experience_required, job_url) to observations; identity = job_url; lat/lon from the city table
built from Arbeitsagentur rows. Linking to Arbeitsagentur postings uses fuzzy_key (title | employer | city).

## Career-site crawler (live employer_ats rows)
`python data/run_crawl.py 0 0 120 "Klinikum Bayreuth|RoMed Kliniken"` runs `pflege_jobs/sources/career_crawl.py` over seeds in
`data/registry/top20_seeds.json` (career URL, extra list pages, sitemaps, hosts, KeZ, town, bavaria_only_operator). Strategy:
listing-first — job links = anchors with a gender marker "(m/w/d)" or detail-URL patterns, plus sitemap <loc>s; each detail page
parsed via schema.org JobPosting JSON-LD (softgarden, d.vinci, rexx, mein-check-in…) or heuristically (h1 + "Bewerben");
Bavaria filter on addressRegion / PLZ / town list drops non-Bavarian sites of chains (Rhön: 18 dropped). robots.txt honoured,
≤ 0.25 s between requests. Known blockers: JS-only lists (UKR concludis, Kliniken Nordoberpfalz, Aschaffenburg, Passau);
KWM rate-limits after ~30 pages. Load with `EdgeSink`, then `clinic_links` from the seed KeZ and `verify` = live (fetched now).

## Headless-browser adapter (JS portals)
`pflege_jobs/sources/career_browser.py` (Playwright/Chromium; `pip install playwright && playwright install chromium`) for seeds in
`data/registry/js_seeds.json` with `browser: true`: accepts cookie consent, scrolls, clicks "mehr laden", collects job anchors from
page + iframes + JSON XHR payloads; details via requests (JSON-LD) or rendered when `browser_details: true`. Run
`python data/run_browser_crawl.py "Klinikum St. Marien Amberg|LA-REGIO Kliniken (Klinikum Landshut)" 60`. Works: Talention, LA-REGIO,
Amberg, Aschaffenburg (jobs. subdomain). Still blank: München Klinik JobFinder (consent-gated, undocumented API), UKR (B-ITE widget),
Helios (bot wall also for headless Chromium), Josefinum, DONAUISAR, Passau (postings as PDF flyers). Sandbox note: `ignore_https_errors`
is required behind a TLS-inspecting proxy.

## ATS census + B-ITE adapter (the "detect → dedicated adapter" pattern)
`python data/ats_census.py 0 429` — for every klinikradar facility: profile → `props.hospital.website` → homepage → up to 3 career
links → fingerprint (B-ITE widget, softgarden, rexx, umantis, mein-check-in, d.vinci, concludis, Talention, Personio, Oracle…).
Result in `data/registry/ats_census.json`, written to `clinics.ats_type/careers_url/website`. 2026-09-06: 429 scanned,
B-ITE 28, softgarden 49, TYPO3-native 22, rexx 18, umantis 16, mein-check-in 14, d.vinci 10, concludis 6.
B-ITE recipe (`pflege_jobs/sources/bite.py`): page has `<script …static.b-ite.com/jobs-api/loader-v1…>` + `data-bite-jobs-api-listing="{customer}:{listing}"`
→ `GET cs-assets.b-ite.com/{customer}/jobs-api/{listing}.min.js` (contains the 40-hex `key`) → `POST jobs.b-ite.com/api/v1/postings/search`
`{"key",…,"locale":"de","page":{"num":1000}}` → structured postings (address with PLZ/lat/lon, dates, employmentType, custom.berufsgruppe)
→ `GET <url>/raw` for the description. Run `python data/run_bite.py` (checkpointed in `data/bite_done.json`). Listings can span a whole
operator (BG Kliniken 397 postings nationwide, Diakoneo 219): rows are attributed per posting via `employer.name` + city → registry
match; only rows with positive Bavaria evidence (PLZ/town) are kept. `listing == "niiid"` is B-ITE's chatbot product — not supported.

## softgarden adapter
`python data/run_softgarden.py 100` (checkpoint `data/softgarden_done.json`): for each census facility with `ats=softgarden`,
`pflege_jobs/sources/softgarden.py` finds the portal host on the career page (`*.softgarden.io`, `jobdb.softgarden.de`, or a custom domain with
`/job/<id>/` links), then the generic crawler lists `/de/vacancies` + `/sitemap.xml` and parses JSON-LD on job pages. 2026-09-06: 25 portals,
198 Pflege/Bavaria rows (Dritter Orden 44, Kulmbach 30, GeBO 18, Passauer Wolf 17, Hescuro 16, Josefinum 12). Not covered: clinics whose page only
embeds a softgarden badge (no host found), `jobdb.softgarden.de` legacy portals, and nationwide operators (Paracelsus: 259 links, first 100 non-Bavarian).

## rexx / d.vinci / mein-check-in / umantis adapters
`python data/run_ats.py <rexx|dvinci|mein-check-in|umantis> [budget]` (checkpoint `data/ats_<ats>_done.json`); seed builders in
`pflege_jobs/sources/ats_seeds.py`:
- rexx: `<host>/stellenangebote.html` + `?page=2..6` + sitemap; JSON-LD on details. 2026-09-06: 8 portals → 69 rows.
- d.vinci: host from `*.dvinci-hr.com|*.dvinci-easy.com|/de/jobs` links → `/de/jobs`, `/de/jobs/iframe`, `?page=`; JSON-LD (may have
  null address → seed town). 5 portals → 113 rows (Fürth 36, Bamberg 35, RoMed 28, Neumarkt 11).
- mein-check-in: `mein-check-in.de/<slug>/overview` → `/position-<id>` pages, heuristic parse; town from title ("am BKH Passau") →
  `Einsatzort:` label → seed town; category links "(n)" dropped. 8 portals → 69 rows.
- umantis: `recruitingapp-<n>.de.umantis.com/Jobs/1?CompanyID=…` — list is JS-paged; only 1 row. Needs a browser or their JSON. Low priority (16 small sites).
Nationwide operators (Schön, Rhön, Paracelsus…) get `bavaria_only_operator=false`: rows need positive PLZ/town evidence.

## Walled portals — what actually works (tested 2026-09-06)
| target | sandbox / Netlify / Cloudflare | headless Chromium | **Claude web_fetch (Anthropic egress)** | other |
|---|---|---|---|---|
| helios-gesundheit.de (www) | 403 Akamai, even robots.txt | 403 | **200**, job pages carry JSON-LD | Helios' real ATS is **P&I bewerber-web on pi-asp.de** — not walled, GWT-RPC encrypted, but renderable: `pflege_jobs/sources/pi_asp.py` (companyEid 1134 München West, 1135 Perlach, 1130 Dachau/Indersdorf) |
| stepstone.de | tarpit/503 | – | **200**, 25 cards/page, `?page=N` | – |
| de.indeed.com | 403 "Security Check" | – | listing content visible via Claude web_search | – |

`crawlers/claude_egress.py` = Messages API + `web_fetch` server tool → parse returned markdown (StepStone cards, JSON-LD lines,
"(m/w/d)" links) → `pflege_jobs.inbox` → `cli inbox`. Needs `ANTHROPIC_API_KEY`; ~1 call per page. Modes: `stepstone 1-58`,
`helios-detail <urls>`, `url <urls>`. Aggregator rows get precedence 4 and are only useful for employers with no other source.

StepStone worked example (2026-09-06, in-chat via Claude fetch): `jobs/pflegefachkraft/in-augsburg` page 1 → 16 cards → 12 loaded as
aggregator rows, 5 linked to registry sites (Klinik Vincentinum ×3, Hessing Kliniken ×5 after the leadership-gate fix) — both sites had no
site source before. Chains/agencies (Korian, Pacura, BeneVit, von Caprivi) land as non_clinic/unknown and are ignored by the default view.
Per-city StepStone pages (`/jobs/pflegefachkraft/in-<stadt>`) are the efficient unit: 13 pages for Augsburg, 58 for all of Bayern.

### Exa as egress (`crawlers/exa_egress.py`, tested in-chat 2026-09-06)
Exa reads StepStone listings **with working `?page=N`** and detail pages (full text: TVöD grade, Betriebswohnungen, Kita → enrichment).
The Exa API `/contents` (`livecrawl:"always"`, `extras.links`) also returns page links, so cards get their real detail URL (slug-token
match) and detail pages yield the apply link for ATS discovery. `stepstone-matrix` there = cities × keywords × pages (EXA_PAGES=3).

### ATS discovery from StepStone
During `claude_egress.py stepstone`, cards whose employer maps to a registry site that has no ATS label **or** no live site source
(`v_clinic_portals`) get their StepStone detail page fetched (1 extra call each, cap `EGRESS_DISCOVER_MAX=40`, disable with
`EGRESS_DISCOVER=0`); all non-StepStone links are fingerprinted against the ATS host list (softgarden, SmartRecruiters, B-ITE, P&I,
d.vinci, rexx, umantis, concludis, Talention, Personio, mein-check-in, HELIX, Oracle, Workday, SuccessFactors, onlyfy, Interamt…).
Result rows go to `inbox` as `kind='probe'`; `cli inbox` writes `clinics.ats_type` + `careers_url` (only when empty or different),
so the next daily run routes that site to its adapter.

## Browser collector (walled portals: Helios, StepStone, Indeed)
Server-side access is impossible from datacenter IPs (Akamai/Cloudflare walls; Netlify functions are blocked too — probe at
`/.netlify/functions/probe?u=…`). CORS forbids reading those sites from our page. So: **bookmarklet** (`web/collector.js`, page
`/collect.html`): the user opens the listing/detail page in their browser, clicks the bookmark; it extracts JSON-LD JobPostings +
"(m/w/d)" links from the DOM and opens `/collect.html#<base64>` which POSTs rows to `pflege_jobs.inbox` (anon INSERT allowed there,
and only there; 2,000 rows/day/client trigger). `python -m pflege_jobs.cli inbox` normalises jobposting rows → observations
(employer sites → employer_ats, StepStone/Indeed → aggregator precedence 4), links to registry, marks verify=live ("collected in a
user's browser"), acks the inbox. Agents can POST to the inbox directly (see collect.html).

## Registry portal table
`v_clinic_portals` = clinic × website × careers_url × ats_type × has_live_site_source × open_pflege_live (407 sites; website 300,
careers_url 269, ats 139, live site source 56).

## Feed/API adapters (`pflege_jobs/sources/feeds.py`, seeds `data/registry/feed_seeds.json`, `python data/run_feeds.py`)
- Personio: `https://<slug>.jobs.personio.de|com/xml?language=de` — XML positions (name, office, department, schedule, jobDescription).
- SmartRecruiters: `https://api.smartrecruiters.com/v1/companies/<Company>/postings?limit=100` + `…/postings/<id>` for the job ad text;
  location.region 'BY' / city → Bavaria; site_map city→employer for group listings (Artemed SE covers Tutzing, Feldafing, Berg,
  Vincentinum Augsburg, München Süd/Mitte — the sites whose own widget is B-ITE "niiid").
- Talention: `POST https://<host>/talention/api/3.2/job` with `{"pagination":{"max":50,"offset":N},"sorter":{"sort":"createdDate","order":"desc"}}`
  → results (title, url, "PLZ Ort" or free-text location, enabledDate). Used for Kliniken Nordoberpfalz and Dr. Ebel.
- HELIX (Perbility, `*.helixjobs.com/<site>/joblist`): server-rendered, `jobad?prj=` links with JSON-LD → generic crawler (`helix_seeds.json`).
- umantis: St. Vinzenz Pfronten renders 19 jobs only in a browser (`career_browser`); recruitingapp-5545/5610 listings are empty.
Not supported: Oracle Recruiting Cloud (4 sites; the Oracle app is not linked from the crawled pages), Klinikverbund Allgäu (no job list found).

## Effective employer class (view)
`v_postings.employer_class` = `clinic` when the posting is linked to a registry site; `unknown` when the employer runs registry sites
but this posting is in a town without one (operator listings mixing Altenhilfe); otherwise the employer's keyword class (`employer_class_raw`).

## Pflege-only storage
`sinks.only_pflege()` drops `nicht_pflege` rows in every sink (`keep_non_pflege=True` to override); `pflege_jobs.purge_nicht_pflege()`
removed the 299 stored ones (postings + raw observations) on 2026-09-06. Classification still happens on every row so the gate is auditable
(`role_rule`), but nothing non-nursing is persisted.

## Arbeitsagentur duplicate policy (after every load)
`select pflege_jobs.supersede_aa_at_covered_sites();` — at registry sites that have a **live career-site source** (`v_site_coverage.has_live_site_source`:
crawl/B-ITE/softgarden/rexx/d.vinci/mein-check-in rows), Arbeitsagentur-only postings are set `status='expired'` with
`verify_note='superseded: site has a live career-site source (AA duplicate policy)'`. AA remains the source of truth for the other sites.
2026-09-06: 56 covered sites, 179 AA-only rows superseded. Reversible (note kept; a newer AA observation reopens nothing by itself).

## Dedupe (`python -m pflege_jobs.cli link-cross [--dry-run]`, run after every load)
1. Same-source URL variants: observations whose `canonical_ref(source_ref)` is equal (SmartRecruiters slug stripped, query/fragment/
   trailing slash dropped) are merged — 155 pairs on 2026-09-06 (board snapshot vs API URLs).
2. Cross-source: within the same `clinic_id` + city, postings from different sources with similar titles (Jaccard ≥ 0.6, or overlap
   ≥ 0.9 with ≥ 3 shared tokens) are merged into the most authoritative one (employer_ats > arbeitsagentur > aggregator);
   observations moved, earliest first_seen kept. Worked example: StepStone "Needle Nurse (m/w/d) in Teilzeit" (Klinik Vincentinum)
   → one posting with 3 observations: SmartRecruiters API + board snapshot + StepStone; published 2026-08-17 comes from the ATS.

## Clinic registry (Krankenhausplan)
`python -m pflege_jobs.sources.krankenhausplan data/registry/krankenhausplan_2025.pdf data/registry/clinics.csv data/raw.json`
parses Teil II Abschnitt A of the StMGP PDF (407 sites, KeZ). `python -m pflege_jobs.cli link-clinics [--dry-run]` links postings
(rules R1 exact name, R2 operator, R3/R4 token overlap with town, R5 loose, R6 ambiguous multi-site → preferred/largest site) and
pushes `clinics` + `clinic_links`. Re-run after every refresh; rows with `clinic_match_rule='manual'` are never touched.

## Adding a source (employer ATS, aggregator)
Write `pflege_jobs/sources/<name>.py` that yields the same observation dict (see `arbeitsagentur.to_observation`), set `source_id` to the `sources` row, load with `EdgeSink`. Precedence handles field conflicts; fuzzy_key handles cross-source linking.

## Ingest endpoint
POST JSON `{employers?, observations?, resolve?, expire_days?, crawl_run?}` with `Authorization: Bearer <anon>` + `x-ingest-secret`. Batches ≤200 rows. Returns counts. Column lists are rendered from `pflege_jobs/schema.py` into the function by `python edge/build_ingest.py` — edit the spec, rebuild, redeploy. (The staging-table fallback was removed in migration 007.)

## Dashboard redeploy (no Netlify, no repo needed)

The dashboard is served straight off this VM by systemd, so "deploy" is just a rebuild:

```bash
set -a; . web/.env.build; set +a     # public project URL + anon key + REPO_URL
python web/build.py                   # -> web/index.html, web/docs.html, web/llms.txt, web/skill/*
```

`systemd` unit `pflege-web` runs `busybox httpd -p 8501 -h web/` (port 8501 is the VM's default
proxy port, so https://pflege-board.exe.xyz maps to it with no port suffix). Nothing to upload.

```bash
systemctl status pflege-web      # is it up?
sudo systemctl restart pflege-web
```

**Build with the public config, not the VM proxy.** `.env` points `SUPABASE_URL` at
`supabase.int.exe.xyz` with `apikey: implicit` — that only resolves *inside* this VM. Building the
dashboard with it produces a page that works for us and returns 500 for every real visitor. Use
`web/.env.build` (project URL + real anon key), which is what the command above does.

Prose for the human docs page lives in `docs/ARCHITECTURE.md` and is rendered into `web/docs.html`
by the same build (`web/render_md.py`). The agent skill in `skill/` is copied to `web/skill/`.

## Monitoring plan
Daily cron (GitHub Actions or any scheduler) running the refresh above; alert when a slice's `maxErgebnisse` drops >30 %, when null-rate of role_class/city rises, or when HTTP errors > 2 %. `crawl_runs` holds the history.
