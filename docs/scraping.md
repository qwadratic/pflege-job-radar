# Scraping & ATS-Strategien

Goal: read every hospital's own career board. No job boards, no labour agency.

## Challenges → solutions

| challenge | what happens | solution |
|---|---|---|
| Every site looks different | 407 sites, ~15 vendors "wearing different CSS" | **fingerprint the ATS once** (`clinics.ats_type`), then call the vendor's known endpoint |
| Discovery is expensive | finding the board costs more than reading it | discovery runs once per clinic; result stored in `careers_url` + `ats_type`; crawl needs no discovery |
| Shared boards | kbo 9 sites → 1 board; Schön Klinik 7 → 1 rexx board. Per-site loop made 490 jobs into 1,319 rows | **board is the unit of work**: `crawlers/routing.py` groups by exact `careers_url`; link-clinics spreads rows across the group |
| Silent truncation | rexx returns exactly 100 rows, no pager | page with `?start=N`; a round number is a bug |
| Walled hosts | Helios answers datacenter IPs with 403 | flagged `walled` in routing; Firecrawl agent or P&I backend (`pi_asp`) instead |
| JS-only portals | d.vinci, München Klinik JobFinder, Oracle Cloud | Playwright where it works (`career_browser.py`), else Firecrawl agent |
| Jobs as PDF flyers | Passau | Firecrawl agent with explicit instruction to open PDFs |
| Non-Bavarian sites of chains | Rhön, Schön, Paracelsus list nationwide | Bavaria filter: addressRegion / PLZ ranges / registry town list; `bavaria_only_operator=false` needs positive evidence |
| Dead ads | boards keep old ads | `verify`: HTTP re-fetch → live / gone / blocked / error; gone → expired; weekly expiry of unseen rows |
| Same job, several titles | one ward posts 3 variants | separate postings by design; cross-source merge only by fuzzy_key (title + employer + PLZ) |
| Partial upserts erase columns | ingest assigns every column; omitted key = NULL | always send full clinic rows (`registry.full_clinic_rows()`); `ats_type`/`careers_url` use `coalesce(nullif(new,''), old)` |
| PDF name/town split | 2026 edition dropped the "Träger" line | keep proven 2025 names, sync structured columns (beds, Fachrichtungen, Stufe, Träger) from 2026 |

## Adapters per vendor

| vendor | sites | how it is read | module |
|---|---|---|---|
| softgarden | 56 | `jobs.feed.json` (no browser) | `pflege_jobs/sources/softgarden.py` |
| typo3_jobs | 50 | `wp-sitemap-posts-jobs-N.xml` / job sitemap → `<h1>` + text | `crawlers/vendor_adapters.py:crawl_wp_jobs` |
| bite (+bite_jobs) | 31 | loader script → 40-hex key → `POST jobs.b-ite.com/api/v1/postings/search` | `pflege_jobs/sources/bite.py` |
| rexx | 24 | `/stellenangebote.html?start=N`, JSON-LD on detail | `crawlers/vendor_adapters.py:crawl_rexx` |
| pi_asp | 16 | P&I bewerber-web, Playwright render+click | `pflege_jobs/sources/pi_asp.py` |
| dvinci | 13 | public GET `<tenant>.dvinci-easy.com/jobPublication/list.json` | `crawlers/vendor_adapters.py:crawl_dvinci` |
| mein-check-in | 13 | `/<tenant>/overview` → `/position-<id>` | `crawlers/vendor_adapters.py:crawl_mein_check_in` |
| umantis | 13 | `recruitingapp-N.de.umantis.com/Jobs/1` server-rendered | `crawlers/portals.py:parse_umantis` |
| concludis | 8 | sitemap → detail | `crawl_wp_jobs` |
| talention | 6 | `POST /talention/api/3.2/job` | `crawl_wp_jobs` / `feeds.py` |
| helix | 5 | `<tenant>.helixjobs.com/<unit>/joblist` | `crawl_helix` |
| personio | 5 | `<slug>.jobs.personio.de/xml` | `crawl_personio` |
| oracle | 4 | sitemap walk (SPA sites → portals.py) | `crawl_wp_jobs` / `portals.py` |
| smartrecruiters | 2 | `api.smartrecruiters.com/v1/companies/<id>/postings` | `crawl_smartrecruiters` |
| group portals | kbo, Schön, RHÖN, Südostbayern | one board, many sites | `vendor_adapters.py:GROUP_PORTALS` |

All adapters emit the inbox row shape; one loader (`crawlers/load_crawl_output.py` / backend worker) ingests everything.

## Routing table

`python -m crawlers.routing` → for each clinic: `routable` (vendor label + careers_url + adapter exists), `route_reason` otherwise, `walled`.
```
routable            352 clinics -> 205 boards (62 shared, 147 fetches saved)
walled boards          1
not routable           55
   no adapter for self_hosted            47
   no careers_url                         7
   no adapter for coveto                  1
```
The app shows this per clinic as **Fetch via**: the adapter name, or *Firecrawl* when no adapter exists (`route_reason` says why). Everything is scrapeable; the difference is cost. The Clawl page (`#/clawl`) previews a target (`GET /api/crawl/plan`: hospitals, boards, via adapter / via Firecrawl, estimated credits) before you start it, and holds the schedules.

## Firecrawl agent fallback

Used only when an adapter cannot: walled host, no label, dvinci, dead site, or mode *firecrawl* chosen on the Clawl page. Every call is capped (`maxCredits`, default 40) and logged (`firecrawl_usage`); credits spent/remaining are in the header and `/api/stats`.

**Jobs agent** (`run_jobs_agent(clinic)`): `urls=[careers_url or website]`, prompt = "open the career portal of <name> (<town>), list every open nursing/Pflege vacancy for this site, follow pagination and filters, open PDFs if listings are PDFs, return only Bavarian locations"; schema:
```json
{"portal_url":"…","jobs":[{"title":"","url":"","city":"","plz":"","department":"","employment_type":"","contract":"","start_date":"","published":"","description_excerpt":"","requirements":"","tariff":"","contact_email":""}],"notes":""}
```
Rows land in `inbox` with `collector=firecrawl-agent` → source 25 → normal classify / link / verify path.

**Refetch-career agent** (`run_career_agent(clinic)`, button "Karriereseite neu ermitteln"): find the career portal from the website, name the ATS vendor, list the portal's filters with their values and job categories, count visible jobs, say whether the list is HTML / JS / PDF. Schema:
```json
{"careers_url":"","portal_url":"","ats_vendor":"","listing_type":"html|js|pdf|none","job_count_visible":0,
 "filters":[{"name":"Berufsgruppe","values":["Pflege","Ärzte"]}],"categories":["…"],"notes":""}
```
Result → `career_profiles` (shown on the clinic page) and, when found, `clinics.careers_url` / `ats_type` (so routing improves next time).

## Still unscraped, and the plan

| gap | sites | next step |
|---|---|---|
| unlabeled (`ats_type=""`) | ~113 (106 have a careers_url) | refetch-career agent in weekly batches (≈15 credits each) → label → adapter; Playwright fingerprint on the 106 with a URL |
| dvinci | done | adapter shipped (`crawl_dvinci`, public `jobPublication/list.json`); 13 clinics now routable |
| Helios (wall) | 3 | `pi_asp` covers München West/Perlach/Dachau; keep |
| München Klinik JobFinder | 5 | consent-gated XHR API → capture with Playwright, replay with requests |
| UKR (B-ITE widget) | 1 | B-ITE key extraction from widget loader → `bite.py` |
| Josefinum, DONAUISAR | 2 | Firecrawl agent, then fingerprint |
| Passau (PDF flyers) | 1 | Firecrawl agent with PDF instruction |
| umantis JS-paged lists | few | `/Jobs/All` first, Playwright otherwise |

Kill rule per adapter: 3 runs with 0 rows at a site that had rows before, or 2× 403/429 → mark walled, route to Firecrawl (noted in `crawl_runs.notes`).

## Schedules

Any number of schedules (`/api/schedules`, Clawl page): preset `weekly_staggered` (03:00 daily, boards spread over 7 days by hash of the board URL), `daily`, `weekdays`, `hourly`, or a custom cron; each with a target (all · Bezirk · city · hospital · ATS vendor), mode, credit cap and an on/off switch. Subsets can be scheduled independently (e.g. rexx boards nightly, Firecrawl discovery for unlabeled Oberpfalz sites weekly within budget).
