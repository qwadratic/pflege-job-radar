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
| softgarden | 38 | `jobs.feed.json` (no browser) | `pflege_jobs/sources/softgarden.py` |
| typo3_jobs | 29 | `wp-sitemap-posts-jobs-N.xml` / job sitemap → `<h1>` + text | `crawlers/vendor_adapters.py:crawl_wp_jobs` |
| bite (+bite_jobs) | 27 | loader script → 40-hex key → `POST jobs.b-ite.com/api/v1/postings/search` | `pflege_jobs/sources/bite.py` |
| rexx | 17 | `/stellenangebote.html?start=N`, JSON-LD on detail | `crawlers/vendor_adapters.py:crawl_rexx` |
| umantis | 16 | `recruitingapp-N.de.umantis.com/Jobs/1` server-rendered | `crawlers/portals.py:parse_umantis` |
| mein-check-in | 12 | `/<tenant>/overview` → `/position-<id>` | `crawlers/vendor_adapters.py:crawl_mein_check_in` |
| dvinci | 11 | **none** — JS list, no feed | Firecrawl agent |
| pi_asp | 8 | P&I bewerber-web, Playwright render+click | `pflege_jobs/sources/pi_asp.py` |
| concludis | 7 | sitemap → detail | `crawl_wp_jobs` |
| oracle | 4 | sitemap walk (SPA sites → portals.py) | `crawl_wp_jobs` / `portals.py` |
| personio | 3 | `<slug>.jobs.personio.de/xml` | `crawl_personio` |
| smartrecruiters | 1 | `api.smartrecruiters.com/v1/companies/<id>/postings` | `crawl_smartrecruiters` |
| talention | 1 | `POST /talention/api/3.2/job` | `crawl_wp_jobs` / `feeds.py` |
| helix | 1 | `<tenant>.helixjobs.com/<unit>/joblist` | `crawl_helix` |
| group portals | kbo, Schön, RHÖN, Südostbayern | one board, many sites | `vendor_adapters.py:GROUP_PORTALS` |

All adapters emit the inbox row shape; one loader (`crawlers/load_crawl_output.py` / backend worker) ingests everything.

## Routing table

`python -m crawlers.routing` → for each clinic: `routable` (vendor label + careers_url + adapter exists), `route_reason` otherwise, `walled`.
```
routable            161 clinics -> 101 boards (31 shared)
not routable        246
   no careers_url                    128
   careers_url but no vendor label   104
   no adapter for dvinci              11
```
The app shows this per clinic (`routable`, `route_reason`) and offers the Firecrawl fallback where `routable=false`.

## Firecrawl agent fallback

Used only when an adapter cannot: walled host, no label, dvinci, dead site, or a manual "Crawl → firecrawl" click. Every call is capped (`maxCredits`, default 40) and logged (`firecrawl_usage`); credits spent/remaining are in the header and `/api/stats`.

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
| unlabeled (`ats_type=""`) | ~230 (104 have a careers_url) | refetch-career agent in weekly batches (≈15 credits each) → label → adapter; Playwright fingerprint on the 104 with a URL |
| dvinci | 11 | try `/de/jobs.json` / `?format=json` probe; else Playwright list + JSON-LD detail; interim: Firecrawl |
| Helios (wall) | 3 | `pi_asp` covers München West/Perlach/Dachau; keep |
| München Klinik JobFinder | 5 | consent-gated XHR API → capture with Playwright, replay with requests |
| UKR (B-ITE widget) | 1 | B-ITE key extraction from widget loader → `bite.py` |
| Josefinum, DONAUISAR | 2 | Firecrawl agent, then fingerprint |
| Passau (PDF flyers) | 1 | Firecrawl agent with PDF instruction |
| umantis JS-paged lists | few | `/Jobs/All` first, Playwright otherwise |

Kill rule per adapter: 3 runs with 0 rows at a site that had rows before, or 2× 403/429 → mark walled, route to Firecrawl (noted in `crawl_runs.notes`).
