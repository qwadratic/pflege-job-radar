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
Every row an adapter finds is stored raw in the local queue (`pflege_jobs/inbox_db.py`) — nothing is filtered at crawl time — and `python -m pflege_jobs.cli inbox` is where classification, Bavaria/host gating, matching and conversion happen before anything reaches Postgres. That is the *storage-time* boundary. One layer earlier, at *discovery* time, each adapter still has to decide which URLs even become row candidates in the first place — see below for who owns that decision.

## Who owns which decision (TASK-123)

Three layers, three different questions, never mixed:

- **Crawl layer** (this file's adapters — `crawlers/vendor_adapters.py`, `crawlers/portals.py`, `pflege_jobs/sources/*.py`): is this URL a *candidate posting page at all*? Decided by URL/page shape wherever a shape exists (`JOB_PATH`/`JOB_HREF`/`UMANTIS_ROW`/... families) — content is read only when shape gives no answer (below).
- **`pflege_jobs/classify.py`** (`classify_role` / `classify_employer`): is it Pflege, and which role (real nursing role / pflegehelfer / ausbildung / nicht_pflege)?
- **`pflege_jobs/registry.py`**'s `Matcher`: which clinic does this posting belong to?

A new content-based filtering rule belongs in `classify.py`/`registry.py`, never a new regex in a crawler module — the crawl layer's own gates below exist only because some boards give it no URL/page-shape signal to work with at all (an accordion/FAQ-shaped listing with no separate detail page, or a detail URL indistinguishable from a category page).

**Content-based accept/reject checks found in the crawl layer**, catalogued 2026-09-23 (grep for every `re.compile` in the three locations above, then read each call site):

| signal | file:line | what it gates | classification |
|---|---|---|---|
| `GENDER_MARKER` (imported as `GENDER` in vendor_adapters.py, `JOB_TEXT` in career_crawl.py) | `pflege_jobs/posting_signal.py:16` — ~15 call sites in `crawlers/vendor_adapters.py`'s `crawl_wp_jobs`/`_wp_job_rows` family, plus `pflege_jobs/sources/career_crawl.py:332,402,564` | "does this title/anchor text carry a German job-posting gender marker (`(m/w/d)`, `:in`, `Pfleger/in`, ...)" — the only signal available on boards with no separate detail page (FAQ/accordion/title-only listings) or no distinguishing detail-URL shape | **content-guessing (b).** Was two independently-drifted copies before TASK-123 (vendor_adapters' had a bare-slash suffix form career_crawl's never got; career_crawl's had a bare-unparenthesized `m/w/d` form vendor_adapters' never got) — reconciled into one shared, unioned regex. Every prior gap (klinik-steger.de's colon form, barmherzige-bieten-zukunft.de's bare-slash form, ~30 postings) was found by live-counting candidate links against stored rows, never by unit-testing the regex alone — TASK-123 AC3 repeated that method on 2 fresh boards (WolfartKlinik: 15 candidates, 10 correctly kept, 0 false negatives; kbo-IAK München-Ost/umantis: found a real gap, but root-caused to umantis' own session-pinned `CompanyID` scoping, not this regex — spun off as TASK-125). Most call sites recurse into a page's own links instead of dropping outright when the check fails (`_wp_job_rows`, career_crawl.py's list-page queue) — a hard drop only happens when recursion also finds no further candidates |
| `NOT_JOB_TITLE_RX` | `crawlers/vendor_adapters.py:725`, used at line 936 | rejects a small fixed list of boilerplate titles (Impressum, Datenschutzerklärung, ...) a mis-parsed page can surface as a fake "title" | low-risk (a) — none of these strings is ever a real job title |
| `_TAXONOMY_NAME_DENYLIST` | `pflege_jobs/sources/bite.py:57` | skips a tenant custom-field name that looks like a thumbnail/image slug when hunting for a department taxonomy | not a reject gate at all — documented no-op fallback: an unmatched field simply isn't used as a taxonomy hint, every posting is still classified exactly as before |
| `BERUF_AUSB` | `pflege_jobs/sources/bite.py:35`, used at line 183 | feeds an `"AUSBILDUNG"` hint into `classify_role` | correctly placed already — a hint into the classifier, not a crawl-layer filter; the actual accept/reject decision stays classify.py's |
| `GM` | `pflege_jobs/sources/pi_asp.py:46`, used at lines 104/126 | third, independent, narrower copy of the same "gendered posting text" signal (a Playwright locator filter) | **content-guessing (b)**, out of TASK-123 AC2's named scope (only GENDER/JOB_TEXT) — narrower than the reconciled `GENDER_MARKER` (only the parenthetical form). pi_asp.py is TASK-39's board (1/3 AC, not yet audited this way) — follow-up, not fixed here |

Everything else that gates discovery (`JOB_PATH`/`JOB_PATH_LOOSE`/`NOT_JOB_PATH`/`SLUG_GENDER_RX` in vendor_adapters.py; `LINK_OK`/`JOB_HREF`/`LINK_BAD`/`PAGINATE`/`UNAMBIGUOUS_DETAIL_HREF` in career_crawl.py; `UMANTIS_ROW` in portals.py) matches URL shape or generic pagination/nav-link text, not posting content — structural (a), out of this audit's (b) category.

## Routing table

`python -m crawlers.routing` → for each clinic: `routable` (vendor label + careers_url + adapter exists), `route_reason` otherwise, `walled`.
```
routable            396 clinics -> 221 boards (74 shared, 178 fetches saved)
walled boards          1
not routable            8
   no careers_url                         7
   no adapter for coveto                  1
```
`self_hosted` (47 sites, discovery found no vendor fingerprint) routes through the generic `crawl_wp_jobs`
reader, same as unlabelled boards — a live probe found 7 of the 14 largest self_hosted boards yield real
postings at zero Firecrawl cost (`backlog/decisions/decision-3`).
The app shows this per clinic as **Fetch via**: the adapter name, or *Firecrawl* when no adapter exists (`route_reason` says why). Everything is scrapeable; the difference is cost. The Clawl page (`#/clawl`) previews a target (`GET /api/crawl/plan`: hospitals, boards, via adapter / via Firecrawl, estimated credits) before you start it, and holds the schedules.

## Firecrawl agent fallback

Used only when an adapter cannot: walled host, no label, dvinci, dead site, or mode *firecrawl* chosen on the Clawl page. Every call is capped (`maxCredits`, default 40) and logged (`firecrawl_usage`); credits spent/remaining are in the header and `/api/stats`.

**Jobs agent** (`run_jobs_agent(clinic)`): `urls=[careers_url or website]`, prompt = "open the career portal of <name> (<town>), list every open nursing/Pflege vacancy for this site, follow pagination and filters, open PDFs if listings are PDFs, return only Bavarian locations"; schema:
```json
{"portal_url":"…","jobs":[{"title":"","url":"","city":"","plz":"","department":"","employment_type":"","contract":"","start_date":"","published":"","description_excerpt":"","requirements":"","tariff":"","contact_email":""}],"notes":""}
```
Rows land in the queue with `collector=firecrawl-agent` → source 25 → normal classify / link / verify path (a run's own rows go to the local SQLite queue; the webhook's go to `pflege_jobs.inbox`, and one drain reads both).

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
