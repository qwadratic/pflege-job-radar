# Top-100 Bavarian clinic coverage audit, 2026-09-21

Ten agents audited the 100 largest Bavarian hospitals by beds, one clinic at a time, comparing our
database against what each adapter returns live and against what is really published on the
hospital's own board. Read-only: no writes, no Firecrawl credits, no source edits.

Findings are filed as TASK-80 through TASK-91. This file is the evidence behind them.

# Bavarian Top-100 Clinic Coverage Audit — Consolidated

Scope: 100 largest Bavarian hospitals by beds (39,897 beds total), each audited live against our DB, our adapter output, and the hospital's real board. No Firecrawl credits spent, no writes, no source edits by any auditor.

---

## 1. Headline numbers

| Verdict | Clinics | Beds | Our open rows | Live nursing vacancies | Nursing vacancies not visible under the right clinic | Junk + duplicate rows held |
|---|---:|---:|---:|---:|---:|---:|
| complete | 52 | 22,413 | 735 | 591 | 10 | 98 |
| partial | 22 | 8,641 | 272 | 313 | 82 | 57 |
| misattributed | 17 | 5,698 | 130 | 170 | 195 | 31 |
| gap | 3 | 1,276 | 15 | 30 | 30 | 15 |
| empty_ok | 4 | 1,320 | 2 | 0 | 0 | 2 |
| blocked | 2 | 549 | 11 | unknown | unknown | 3 |
| **Total** | **100** | **39,897** | **1,165** | **~1,104** | **317** | **206** |

Split of the 317:
- **106 nursing vacancies are absent from the database entirely** (never ingested, or dropped after the adapter returned them).
- **211 are in the database but on the wrong `clinic_id` or on `clinic_id = NULL`** — no crawling needed to recover them.

Split of the 206 inflating rows:
- **86 duplicate rows** — same vendor job stored 2–3× under different URL shapes.
- **120 non-vacancy rows** — news articles, category index pages, marketing landing pages, one PDF asset, one medical-glossary entry, plus stale rows that the board dropped and nothing retired.

**Net real coverage: ~959 genuine, correctly-attributed nursing postings against ~1,072 live** (1,104 minus ≥32 confirmed double-counts between clinics sharing a board). Roughly **87% of postings, but only 66% of clinics are clean** — and the failures cluster in the largest hospitals.

**17 of the 100 clinics show zero open postings (5,718 beds).** Only 4 of those 17 are genuinely empty. The other 13 (5 of them >300 beds, one 862 beds) have live vacancies sitting under a sibling `clinic_id`.

**Counting `open_postings_now` is not a coverage signal.** Proven repeatedly: MK Schwabing shows 15 postings and holds zero real ones; Memmingen shows 40 and holds 9; Kempten shows 36 and holds ~9 distinct Kempten jobs; BKH Augsburg shows the correct 5 while its adapter returns 0 rows today and is one reconcile from dropping to zero.

---

## 2. Root causes ranked by postings cost

### M1 — Shared-board attribution collapse. 21 clinics, 7,599 beds, 163 postings
The dominant defect by a factor of two. Several registry clinics share one `careers_url`; the whole board binds to one of them. Four distinct mechanisms, four different fixes:

| Sub-mechanism | Location | Clinics | Postings |
|---|---|---|---|
| `c = b["clinics"][0]` binds the entire board to the first clinic in the group | `app/crawl.py:695` (grouping at `crawlers/routing.py:150`) | 26108, 76110, 67705, 18801(+3 siblings at 0), 17701 | ~39 |
| `R1_exact` returns on a unique employer-name hit with **no town gate**, unlike its own `R1_exact_town` sibling two lines below | `pflege_jobs/registry.py:142` | 46203, 46204, 47802, 27705 | 22 |
| Row inherits the **seed clinic's town/name** when the job page carries no city; `R0_board_name`/`R0_board_town` then match it back to the seed | `app/crawl.py:545`, `pflege_jobs/registry.py:238` | 77401, 18501, 18301, 17101, 16107, 17704 | ~93 + 48 non-Bavarian rows wrongly stamped Bavarian |
| Matcher returns `None`, row lands `clinic_id=NULL` (176 such rows exist) | `pflege_jobs/registry.py:142` (name-suffix), `:157-164` (empty `et`) | 37202, 18201, 56410 | 11 |
| Tie between two clinics on one board resolved by **max beds**, permanently | `pflege_jobs/registry.py:91-95` `_pick_site` | 46103 | 28 |

Worst single case: **26108 LA-Regio Landshut, 862 beds, 0 postings** — its whole 34-vacancy board is filed under the 120-bed paediatric sibling 26103.

Every one of these boards publishes a per-posting location we are not reading: softgarden `jobLocation.address.addressLocality` / `streetAddress`, mein-check-in's town in the title, InnKlinikum's `Einsatzort:` cell, kbo's `jobSite` Solr facet, b-ite's `address.city`.

### M2 — Adapter returns ~0 rows against a live board. 13 clinics, ~4,000 beds, 63 missing + 21 at risk
Nine clinics where `compare_adapter_fc.py` returns 0–2 rows today. Four causes, all cheap:

- **Registry `careers_url` is a marketing page, not the board** (7 clinics): 66101 (9 missing), 16215 (10), 76201 (3), 77406 (5), 76203 (1), 56404, 27106 (points at a *different hospital*, bkh-landshut.de).
- **Sitemap discovery fails and `crawl_wp_jobs` silently degrades to "links on the careers homepage" and reports success** (`crawlers/vendor_adapters.py:522`, warning printed at `:550` to stderr, never recorded as a failure): ge-Passau serves `/sitemap-index.xml` with a hyphen (27501, 2 missing, 27 job URLs invisible); Altmühlfranken excludes the `stellenangebote` CPT from its sitemap but serves it at `/wp-json/wp/v2/stellenangebote` (57705, 10 missing, 6 rows returned where 53 exist); www.sana.de has no sitemap at all and 73 CMS marketing pages are returned instead (16233, 37202, 57408).
- **`crawl_wp_jobs` walks only the clinic's own host** while the vacancies live off-host: 56201 Waldkrankenhaus → jobs.malteser.de (2 rows returned vs 13 nursing live).
- **JS-rendered board never given a render rung**: jobs.bezirkskliniken-schwaben.de (Duet SPA, 76114/76203/77406 — but the data is present as an inline JSON model, see §3), jobs.klinikum-ab-alz.de (Knockout SPA, 66101).

At risk but currently reading correct: **66301 Würzburg Mitte** (site restructured, every stored URL 301s, `ats_type` is `''`, adapter returns 1 row vs 15 held — the next reconciliation retires all 15) and **76114 BKH Augsburg** (5 held are stale survivors, adapter 0 rows).

### M3 — `_txt()` crashes on a numeric JSON-LD field. 2 clinics, 1,066 beds, 17 postings
`crawlers/vendor_adapters.py:92` does `re.sub(r"<[^>]+>", " ", s or "")`. München Klinik emits `"postalCode":81545` as a JSON number → `TypeError: expected string or bytes-like object, got 'int'` in `parse_job_page` (`:597`) on the **first** posting page, aborting the whole board walk. Reproduced live. Both Munich clinics (16201 Schwabing, 16203 Neuperlach) hold zero real vacancies as a result; 16201's 15 "postings" are all marketing pages. One-line `str()` coercion.

### M4 — Non-vacancy pages ingested as postings. ~16 clinics, 120 rows
Two discovery paths and one classifier gap, compounding:
- `JOB_PATH` (`crawlers/vendor_adapters.py:466`) matches any URL containing `/jobs/` → 117 of 175 München Klinik adapter rows are content pages; and its `/(karriere-)?detail/[^/?#]` alternative matches any `/detail/` path → klinikum-msp.de's glossary entry `/patienten-besucher/glossar/detail/fusspflege` is stored as an open nursing posting.
- `pflege_jobs/sources/career_crawl.py:334-335` emits a link as a posting when `JOB_HREF` matches and the anchor text is >6 chars and not `LIST_NAV.fullmatch()`. `LIST_NAV` lists the bare word "pflege", so `"Pflegedienst"` and `"Ansprechpartner"` pass. Costs both directions: 12 junk rows at 56101, and at 36202 the category link is emitted as a posting so it is **never enqueued as a list page**, the BFS never reaches `/alle-stellenangebote`, and 9 of 14 real vacancies are never seen.
- `classify_role` falls back to `('sonstige_pflege','fallback')` for any title containing a Pflege token, and `sonstige_pflege` is **not** in `patterns.json:344` `excluded_role_classes`. Verified: `classify_role('PFLEGEN KÖNNEN.')` → `('sonstige_pflege','fallback')`. That is how 31 Memmingen news headlines became open postings.

### M5 — Duplicate rows from URL-shape variants. 11 clinics, 86 rows
Posting identity keys on the raw `external_url`. Bayreuth's "78 open postings" is really 39 (vanity host vs `*.softgarden.io`). dvinci `/de/jobs/<id>` vs `/de/jobs/<id>/<slug>`: Bamberg 5, Fürth 5, Neumarkt 5. Kempten stores the same umantis vacancy across 3 hosts (14 dupes). Also Diakoneo `/jobposting/` vs `/de/jobposting/` (5), helix `prj` under two board paths (2), Helios UUIDv4 vs UUIDv5 (3), Malteser slug change (3), Münchberg (2), medbo re-slug (3).

### M6 — Staleness masquerading as coverage. ≥12 clinics, ~13 postings
Rows last seen 2026-09-05 (16 days) while siblings were crawled 09-20/21: 36201 (21 rows), 66101 (7), 18811 (11 — 4 Gauting vacancies published 09-16 never ingested), 17302 (2), 76201, 77406, 76114, 76203, 16215, 67804, 47401, 56403. Adapter is healthy on every one of these; the board simply moved. **Nothing in the audit inputs exposed per-clinic `last_seen` age** — a clinic reads "complete" purely because its board happened not to move.

### M7 — Nothing closes a posting that left the board. All clinics
Verify checks URL liveness, not board membership. Every junk row found has `verify_status=live` and a recent `last_seen`. Münchberg's two `.io` duplicates are `status='open'` 15 days after their last observation. When an adapter silently drops to 0 rows nothing expires, so the DB keeps looking healthy — worse than reading empty.

### M8 — Classifier misses on in-policy roles. ~7 postings
- **Hygienefachkraft** is the recurring one: classifies as `apn_experte` (in-policy) yet 4 such rows across 36201, 76401 (×2) and 76301 are absent from the DB although the adapter returns them. **The drop happens after classify — unexplained, needs tracing.**
- `'OP Leitung (m/w/d)'` → `('nicht_pflege','no_pflege_token')` — the `leitung` rule requires a pflege token (18001).
- `'Onkologische Fachkraft (w/m/d)'` → `('nicht_pflege','no_pflege_token')` despite `section_labels` carrying workarea "Pflege- und Funktionsdienst" (18811, `pflege_jobs/classify.py:90`).
- `patterns.json:77` `medizinische/?r? fachangestellte` does not match the inflected "Medizinischen Fachangestellten" → MFA rows leak in (47701).

### M9 — Policy exclusion applied inconsistently. ~12 rows, not a bug, a decision
`pflegehelfer` is in `patterns.json:347` `excluded_role_classes` and correctly drops 10+ rows (Sana Hof, Helios München West ×2, GAP ×3, InnKlinikum ×2, Barmherzige, Günzburg, Ilmtal, Erler, Main-Spessart, Landsberg). But `app/crawl.py:518` enforces it **only for seeded-adapter observations**, so 27106 and 46401 currently hold open Pflegefachhelfer postings, and 17101 holds open `ausbildung` rows. Same policy, opposite outcome depending on code path. One decision, applied once.

### M10 — Filter-param-hidden rows (not JS, plain HTTP). 2 clinics, 8 postings
Kaufbeuren's default board page returns 11 links with **zero nursing**; the 16 Pflege rows only exist under `?selection3=3&page=N`. Barmherzige München serves 10 of 21 with no page links and an un-clickable pager; the 11 Pflege rows only appear under `?tx_oycimport_list[category]=15`. A crawler that fetches `careers_url` and walks anchors under-reports these by 50–70% and looks covered. Related: the Barmherzige Schwandorf board silently defaults to a single-location view (30 rows vs 129 with `?...[location]=all`).

---

## 3. Fixes ordered by postings recovered per unit of work

| # | Fix | Where | Recovers | Effort |
|---|---|---|---|---|
| 1 | Gate `R1_exact` on town the way `R1_exact_town` already is: when the posting's city is known and disagrees with the single candidate, fall through | `pflege_jobs/registry.py:142` | **22 postings, 4 clinics 0→correct, 1,208 beds** | one condition |
| 2 | Relink pass over existing rows — no crawler change. Today's Matcher already resolves 77401 correctly (`R2_operator_town`, 0.9) | offline job over `postings` | **~32 postings** (77401 ~25, 37202 6, 18201 1) | one script |
| 3 | `s = "" if s is None else str(s)` | `crawlers/vendor_adapters.py:92` | **17 postings, 2 Munich clinics, 1,066 beds** | one line |
| 4 | Attribute shared-board rows **per posting** from the board's own location field instead of `clinics[0]` | `app/crawl.py:695` + per-adapter location read | **~39 postings, 4 sibling clinics 0→correct** | medium |
| 5 | ~20 `clinics.careers_url` / `ats_type` registry edits (full list in §5) | `pflege_jobs.clinics` | **~34 postings** + unblocks 66301's 15 from decaying | one field each |
| 6 | Canonicalize `external_url` to the vendor job id before the dedup key (softgarden `<host>/job/<id>`, dvinci `/de/jobs/<id>`, umantis vacancy id, helix `prj`, b-ite `/de/` prefix) | posting identity key, one place | **86 duplicate rows collapse**; Bayreuth 78→39 | small, one place |
| 7 | Close a posting when it is absent from a board walk that **succeeded** — not only when its URL 404s | verify/reconcile path | retires ~30 stale-open rows; makes every count trustworthy | small |
| 8 | Require a posting-shaped signal (gender marker in anchor, or JSON-LD `JobPosting` on the fetched page) before emitting a row | `pflege_jobs/sources/career_crawl.py:334-335` | drops ~20 junk rows **and recovers 9 at 36202** by letting the BFS descend | small |
| 9 | Retro-purge rows matching `NOT_JOB_PATH`; tighten `JOB_PATH`'s `/detail/` alternative to require a job-ish parent segment | `crawlers/vendor_adapters.py:466,485` | **~100 junk rows** | small |
| 10 | Add `/sitemap-index.xml` to the candidate list; fall back to `/wp-json/wp/v2/<cpt>?per_page=100` when the CPT is missing from the sitemap | `crawlers/vendor_adapters.py:522` | **12 postings** (27501 2, 57705 10); 6→53 rows at Altmühlfranken | one string + one fallback |
| 11 | Parse the inline `{"RegionsViewModel":…,"Jobs":[…],"TotalJobsCount":57}` model on jobs.bezirkskliniken-schwaben.de — **no render, no Firecrawl needed** | new small adapter | **~11 postings across 76114 / 76203 / 77406** and the whole Schwaben operator | small, one adapter, 3+ clinics |
| 12 | Re-crawl the 12 clinics whose rows are 10–16 days stale | scheduling only | **~13 postings**, zero code | scheduling |
| 13 | Route `talention` to the working dedicated API adapter instead of `crawl_wp_jobs` | `crawlers/routing.py:51` → `pflege_jobs/sources/feeds.py:86` | 34→38 rows at 36301 | one line |
| 14 | Classifier: trace the Hygienefachkraft ingest drop; add an OP/Funktionsdienst leadership rule; let `section_labels` rescue a no-pflege-token title; widen the MFA pattern to `medizinische[nr]? fachangestellte[nr]?` | `pflege_jobs/classify.py`, `patterns.json:77` | **~7 postings**, plus precision | small |
| 15 | Decide `pflegehelfer` once and enforce it on **every** ingest path, not only seeded-adapter observations | `app/crawl.py:518` | ±12 rows either direction; makes counts comparable | product decision first |
| 16 | Tighten `_page_hosts_ok` to the registrable domain instead of the TLD (splitting the seed host on the first dot widens `kbo-iak.de` to any `.de` host) | `pflege_jobs/sources/career_crawl.py:286` | stops 108 group rows arriving under one 212-bed clinic's seed (TASK-79) | small |

**Two non-coverage tickets that cost the auditors real time and will cost production:**
- `app/data.py:351-355` swallows a `_build()` exception into `_snap["error"]`, keeps the stale/empty snapshot **and resets the TTL**. A transient `v_postings` 500 then surfaces everywhere as "unknown clinic_id \<id\>". Fired on ~1 in 4 adapter runs across three independent batches. This is a silent fallback of exactly the kind CLAUDE.md forbids, and it affects the live API, not just the CLI.
- `crawlers/vendor_adapters.py:550` prints "no job links in sitemap" to **stderr** and then returns a partial result reported as success. Three boards in this audit returned 2 / 6 / 73 rows where 27 / 53 / 1,122 exist. That log line is a ready-made failure signal that nothing records or alerts on.
- `crawl_wp_jobs` on the 778-job AMEOS board (18501) **did not terminate after 17 minutes** and was killed. Non-termination on a nationwide board is its own defect.

---

## 4. Do we parse every current source in full?

**No — 9 of 100 boards are provably not read in full today**, and the answer is verifiable per board, not a guess.

**Proven full** (adapter row count matched an independent enumeration of the board exactly): Roth 9/9, Kliniken Südostbayern rexx 51/51, Schön Klinik 292/292, BG/b-ite Murnau 377/377, Asklepios 1398/1398, mein-check-in Landshut 22/22, Amberg bite 47/47, Straubing 27/27, Neumarkt dvinci 44/44, Traunstein 51/51, BEESITE Ansbach 44/44 (= the board's own "44 Treffer"), Bayreuth softgarden 114/114 (= feed `numberOfItems`), Deggendorf b-ite 28/28 (= `page.total`), Landsberg b-ite 22/22, Passau 17/17, Coburg P&I 78/78, Dachau pi-asp 33/33. Plus Nürnberg 106 server-side, Würzburg Mitte 51 anchors, Rhön 327 across 4 pages.

**Proven not full**: 66101 (0 rows vs 62), 66301 (1 vs 51), 76301 (0 — tooling artifact, see §5 caveat), 76201 (2 vs 16 Pflege), 77406 (0 vs 57), 76114 (0 vs 57), 76203 (0 vs 57), 56201 (2 vs 31), 27501 (2 vs 27), 57705 (6 vs 53), 16233/37202/57408 (73/85/88 CMS pages vs an Oracle board of 1,122), 16201/16203 (walk aborts on the first posting page).

**Unknown**: 47601 and 67601 — helios-gesundheit.de returns Akamai `Access Denied` to plain curl *and* to a real Chromium render from this IP, exactly as `crawlers/routing.py:90` `WALLED` documents. No Helios count can be trusted without non-datacenter egress or a Firecrawl rung.

---

## 5. Flagged for human recheck — disagreements and low confidence

**Auditor disagreements:**
1. **TASK-77's softgarden `find_host()` claim does not reproduce.** Batch 5 called `find_host` directly: it resolves `gebo-med.softgarden.io` and `klinikum-bayreuth.softgarden.io` correctly. Batch 10 confirms bezirkskliniken-schwaben *is* broken but for a different reason (registered `careers_url` is the wrong host + the board is a Duet SPA whose data is nonetheless in the inline JSON). TASK-77's diagnosis needs rewriting before anyone implements it.
2. **46101 vs 46103 count the same dvinci board and disagree**: 46101 says 25 nursing, 46103 says 28. Same 126-job board. One of the two counts is wrong.
3. **46103's "28 missing" may be zero real loss** — its own auditor notes no nursing posting on the board currently names Michelsberg. The risk is an empty clinic page, not lost vacancies. Do not fund a `_pick_site` change on that number alone.
4. **46201 vs 46204 double-count**: 46204's 4 Hohe Warte rows are a subset of 46201's 36.

**Low/medium confidence — recheck before acting:**

| Clinic | Beds | Why |
|---|---:|---|
| 47601 Helios Kronach | 282 | **blocked**, Akamai 403 to curl and render. `live_count` genuinely unknown, not estimated |
| 67601 Helios Erlenbach | 267 | **blocked**, same. Also 18 Dachau/Markt Indersdorf postings were filed under it (now expired) |
| 76301 Kempten | 510 | `adapter_count=0` is a **tooling artifact** of single-clinic planning (`routing.py:145` defaults empty `ats_type` to `wp_jobs`); the full-registry crawl inherits `umantis` from siblings at `:156`. Do not read it as a live break. The 36→~9 deflation is real |
| 46204 Hohe Warte | 316 | 4 is the auditor's Hohe-Warte share of a 114-item operator feed; 13 feed items mention Hohe Warte, so the true figure may be higher |
| 18402 kbo München-Ost | 750 | medium — the umantis board showed 3 vacancies at audit time; the 108 kbo.de rows are a different board entirely |
| 56404 Hallerwiese | 279 | medium — the 90419/90431/90429/90441 PLZ split between the hospital and Diakoneo's other Nürnberg sites is inferred |
| 57705 Gunzenhausen | 210 | medium — 13 nursing out of a 53-row WP REST payload, not independently enumerated |
| 19002 Weilheim | 220 | medium — both rows carry `city='Schongau'` on a board shared with 19001. Read the two detail pages; if Schongau, Weilheim becomes `empty_ok` |
| 17101 InnKlinikum | 407 | auditor explicitly will not stand behind `live_count=15` — the Altötting/Mühldorf split is inferred from contact postcodes (84503 vs 84453), not a labelled field |
| 37202 Sana Cham | 235 | `live_count=6` rests on the landing page agreeing exactly with the 6 CX_4025 rows already in our DB; the Oracle REST paths tried returned 404 |

**One corrupted registry row found, worth a table-wide scan:** clinic 47503 has `name = "Bezirksklinik Rehau Rehau Taeger KU Gesundheitseinrichtungen des Bezirks Oberfranken"` and `town = "(GeBO)"` — a whole CSV line mashed into the name field. It can neither match nor be matched against, and is a plausible contributor to the GeBO misrouting. Scan for other rows whose `town` contains punctuation or whose `name` exceeds a sane length.

**Batch input files were stale against the live registry** for at least 46203, 76110, 56201, 26108, 27204, 18601, 27501 — all showed `careers_url: null` where the live `clinics` row has a URL. Regenerate whatever produced `/tmp/top100_batch*.json` before any re-audit, or the next pass will chase phantom `no_board` verdicts.

---

## 6. Ready-to-apply registry corrections (`clinic_id` → `careers_url`)

**Registry row currently has no usable board (fills a null or replaces a marketing page / wrong hospital / single job-detail page):**

| clinic_id | New `careers_url` | Note |
|---|---|---|
| 66101 | `https://jobs.klinikum-ab-alz.de/Jobs` | replaces `klinikum-ab-alz.de/karriere/`; needs a render rung or adapter (Knockout SPA, 62 jobs) |
| 27106 | `https://www.dik-karriere.de/stellenangebote` | current value points at **a different hospital** (bkh-landshut.de) |
| 16201, 16203 | `https://www.muenchen-klinik.de/stellenmarkt/` | replaces `/jobs/`; full list inline as `var allJobs` (57 jobs), no render needed |
| 16215 | `https://www.rotkreuzklinikum-muenchen.de/stellenangebote/` | current value is the *association's* board (18 elderly-care jobs, 0 hospital jobs) |
| 56404 | `https://www.diakoneo.de/karriere/stellenportal/stellen-in-der-pflege` | current `/karriere/` page carries no b-ite widget |
| 76201 | `https://www.kliniken-oal-kf.de/karriere/karriereportal/stellenangebote?selection3=3` | walk `&selection1=1&page=N` until 0 detail links |
| 16214 | `https://karriere-barmherzige-muenchen.de/stellenangebote?tx_oycimport_list%5Bcategory%5D=15` | union the 4 `?…[schedule]=1..4` variants for all 11 Pflege rows |
| 36202 | `https://csj.de/beruf-und-karriere/stellenangebote/alle-stellenangebote` | the one page carrying all 27 job links |
| 77406, 76203, 76114 | `https://jobs.bezirkskliniken-schwaben.de/Jobs` | all three currently null or pointed at the CMS; one adapter serves the whole operator |
| 66301 | `https://www.kwm-klinikum.de/beruf-chancen/stellenanzeigen/uebersicht-aller-stellen.html` | + `ats_type` is `''`; 51 server-rendered anchors, no JS |
| 57408 | `https://jobs.sana.de/de/sites/CX_4025/requisitions?selectedOrganizationsFacet=300000012363401` | + `ats_type=oracle` |
| 37202 | `https://jobs.sana.de/de/sites/CX_4025/requisitions` | + `ats_type=oracle` (currently `typo3_jobs` → 85 marketing pages stored as postings) |
| 16233 | `https://jobs.sana.de/de/sites/CX_4025/` | read via `hcmRestApi/.../findReqs;siteNumber=CX_4025` on `fa-eycl-saasfaeuraprod1.fa.ocs.oraclecloud.com` (jobs.sana.de 302s these to 404) |
| 56201 | `https://jobs.malteser.de/de/job-offer-list/` | vacancies are off-host from waldkrankenhaus.de |
| 18402 | `https://recruitingapp-5656.de.umantis.com/Jobs/1?CompanyID=22&Reset=G` | replaces the kbo.de group-CMS walk |
| 18712 | `https://kbo.de/karriere/jobboerse?tx_solr%5Bfilter%5D%5B0%5D=jobSite%3Akbo-Inn-Salzach-Klinikum+Wasserburg+am+Inn` | current value has zero job links; today's 0 is right by luck |
| 16107 | `https://kbo.de/karriere/jobboerse?tx_solr%5Bfilter%5D%5B0%5D=jobLocation%3Akbo-Donau-Altm%C3%BChl-Kliniken` | carry the facet value as the row's city |
| 17704 | `https://kbo.de/karriere/jobs` | + read the `jobSite` facet per job |
| 27501 | `https://karriere.ge-passau.de/stellen/` | |
| 77901 | `https://dongku.de/stellenangebote/` | current value is a now-delisted single job-detail page |
| 47601 | needs a board URL | current value is a single job-detail UUID page; **no working replacement found** (Akamai-walled, no `pi_seeds.json` entry) |

**Cosmetic / confirmations only (current value works or is harmless):** 17401 `https://helios-gesundheit.pi-asp.de/bewerber-web/?companyEid=1130`; 16207 same with `companyEid=1134`; 56202 `https://jobs.bezirkskliniken-mfr.de/index.php?ac=search_result`; 17801 `https://www.mein-check-in.de/klinikum-freising/overview`; 46101 `https://sozialstiftung-bamberg.dvinci-easy.com/de/jobs` (bypasses the consent-manager gate on the registered page); 46203 `https://gebo-med.softgarden.io/de/vacancies`; 47501 `https://jobs.kliniken-hochfranken.de/jobs.feed.json`; 46201 `https://karriere.klinikum-bayreuth.de/jobs.feed.json`; 47701 `https://klinikumkulmbach.softgarden.io/de/vacancies`; 56410 `https://karriere.klinikum-nuernberg.de/freie-stellen/aktuelle-stellenangebote/`; 27204 `https://www.frg-kliniken.de/beruf-karriere/aktuelle-stellenangebote`; 76301 `https://karriere.klinikverbund-allgaeu.de/` (+ set `ats_type='umantis'`, its 3 siblings already have it); 26108 `https://www.la-regio-kliniken.de/stellenportal` (already set — the problem is that 26103 has it too).

**Registry lint worth adding**: `careers_url` must not match a known job-detail-page shape (`/job/<uuid>/`, `/stellenangebote/<slug>/<slug>/`). It would have caught 47601 and 77901 before either produced junk rows.

---

## 7. Two numbers that are not in any dashboard and should be

1. **Per-clinic `last_seen` age.** Twelve clinics read "complete" or "partial" purely because their board did or did not move during a 16-day gap. Freshness is invisible in every count this audit was handed.
2. **Adapter rows returned vs. the board's own self-reported total.** Most boards publish it (`page.total`, `numberOfItems`, `TotalJobsCount`, `X-WP-Total`, "44 Treffer", "Derzeit gibt es 57 offene Stellen"). Where we compared, the match was exact 17 times and a hard failure 9 times. That single ratio is the cheapest possible "are we parsing this source in full" alarm, and it needs no oracle and no credits.

---

## Appendix: per-clinic verdicts

### 36201 Krankenhaus Barmherzige Brüder Regensburg (985 beds) — **complete**
- ours 21 / adapter 61 / live 23 (confidence: high)
- root cause: Board https://www.barmherzige-regensburg.de/karriere/offene-stellen-bewerbung.html renders all jobs server-side on one page and self-reports '53 passende Stelle(n) gefunden'; 24 sit in the 'Pflege- und Funktionsdienst' category and we hold 21 of them — the only true miss is 'Hygienefachkraft (m/w/d)' (classify_role returns ('apn_experte','apn_experte:hygienefachkraft'), i.e. NOT excluded, and the adapter does return the row), plus 'MFA und GUKP für Aufnahmezentrum 1' which classify correctly rejects as ('nicht_pflege','no_pflege_token').
- fix: Re-crawl: all 21 open rows have last_seen 2026-09-05 (16 days stale). Investigate why the adapter-visible 'Hygienefachkraft (m/w/d)' row (detail/j/hygienefachkraft-m/w/d-126084243.html) never became a posting despite a non-excluded role_class.

### 56410 Klinikum Nürnberg - Betriebsstätte Süd (957 beds) — **partial**
- ours 31 / adapter 61 / live 48 (confidence: high)
- root cause: https://karriere.klinikum-nuernberg.de/freie-stellen/aktuelle-stellenangebote/ lists 106 jobs server-side, 48 nursing; our DB holds 43 of those 48 but sprayed across four ids — 56410:30, 57401 (Lauf):6, 57403 (Altdorf):3, clinic_id NULL:4 — while Campus Nord (56401) holds just 1, so Nord postings are being filed under Süd. Five nursing rows are absent from the DB entirely even though app/crawl.py:140 raw_board_rows() returns them today: /jobs/21298 and /jobs/21299 'Pflegefachhelfer (m/w/d)', /jobs/10865 'Pflegefachhelfer/in Campus Süd', /jobs/317 'Pflegefachkraft und Pflegefachhelfer/in Springerpool Campus Nord', /jobs/21827 MFA/OTA Urologie. classify_role('Pflegefachhelfer (m/w/d)') returns ('pflegehelfer','pflegehelfer:pflegefachhelfer'), which is not in EXCLUDED_ROLE_CLASSES, so the drop happens after classify, not in it.
- fix: Trace the ingest path for the 4 Pflegefachhelfer rows the adapter already returns; and resolve the Nord/Süd (56401/56410) split plus the 4 clinic_id=NULL Nürnberg rows.
- discovered careers_url: https://karriere.klinikum-nuernberg.de/freie-stellen/aktuelle-stellenangebote/

### 46101 Klinikum Bamberg - Betriebsstätte am Bruderwald (911 beds) — **complete**
- ours 35 / adapter 127 / live 25 (confidence: high)
- root cause: dvinci board https://sozialstiftung-bamberg.dvinci-easy.com/de/jobs lists 126 jobs, 25 of them nursing after excluding Ausbildung/Praktikum; a job-id diff of our 35 rows against the board leaves exactly one uncovered nursing-adjacent row (id 52600 'Betreuungskraft §43b (m/w/d) Tagespflege', a care-home support role). Our 35 rows cover only 30 distinct dvinci job ids: 5 jobs are stored twice, once as /de/jobs/<id> and once as /de/jobs/<id>/<slug> (e.g. 52664, 52655, 52667, 52256, 52093), so posting identity is keyed on the raw external_url string rather than the vendor job id.
- fix: Normalize external_url (strip the slug tail / canonicalize to the vendor job id) before the posting dedup key, then merge the 5 duplicate pairs.

### 26108 LA-Regio Kliniken Landshut (862 beds) — **misattributed**
- ours 0 / adapter 81 / live 34 (confidence: high)
- root cause: All 35 open postings scraped from https://www.la-regio-kliniken.de/stellenportal (81 job links, 34 real nursing) are in the DB but filed under clinic_id 26103 'Kinderkrankenhaus St. Marien Landshut' (120 beds), not 26108 (862 beds) — both clinics carry the identical careers_url 'https://www.la-regio-kliniken.de/stellenportal' in pflege_jobs.clinics, and the matcher resolves the shared board to the wrong sibling. Verified: postings?external_url=like.*la-regio-kliniken* returns 35 rows, 0 under 26108, and Landshut-city open postings split 26103:35 / 26107:7 / 26108:0. Note the batch file's `careers_url: null` for 26108 is stale — the live DB row does have one.
- fix: Split the shared la-regio-kliniken.de board by per-posting site/department so the ~34 adult-care rows land on 26108 and only the paediatric ones on 26103; or make the matcher prefer the largest-bed clinic when two registry rows share a careers_url.
- discovered careers_url: https://www.la-regio-kliniken.de/stellenportal

### 36290 Universitätsklinikum Regensburg (839 beds) — **complete**
- ours 20 / adapter 53 / live 16 (confidence: high)
- root cause: bite board jobs.ukr.de returns 53 rows to the adapter and every nursing row on it is in our DB (GuK Intensiv/stationär/OTA/ATA, Pflegefachkräfte, Pflegefachpersonen IMC-Stroke/FlexCare/interprofessionell, komm. stv. Pflegerische Leitung, Praxisanleiter, Sitzwache, 2x Fachweiterbildung DKG). We hold 20 > the 16 strictly-nursing live rows because we also keep the two Fachweiterbildung rows and three rows last_seen 2026-09-05 that are no longer on the board ('Gesundheits- & Krankenpfleger (m/w/d)', 'Gesundheits- & Kinderkrankenpfleger (m/w/d)', 'Operationstechnischer Assistent (m/w/d)').
- fix: Expire the three 2026-09-05 rows that no longer appear on jobs.ukr.de; no coverage work needed.

### 56301 Klinikum Fürth (771 beds) — **complete**
- ours 42 / adapter 67 / live 30 (confidence: high)
- root cause: dvinci board https://jobs.klinikum-fuerth.de/de/jobs lists 67 jobs, 30 nursing; a job-id diff of our rows against the board yields MISSING nursing = 0. Same duplicate-key defect as Bamberg: our 42 rows cover only 37 distinct dvinci ids — 5 jobs (10932, 11005, 11036, 11074, 11085) are stored twice, once as /de/jobs/<id> and once as /de/jobs/<id>/<slug>.
- fix: Same fix as 46101: canonicalize external_url to the vendor job id before the dedup key.

### 18402 kbo-Isar-Amper-Klinikum München-Ost (750 beds) — **partial**
- ours 1 / adapter 108 / live 3 (confidence: medium)
- root cause: 18402 routes to 'adapter crawlers.vendor_adapters:crawl_wp_jobs' on board https://kbo-iak.de/kbo-karriere/stellenangebote-pflege (board_shared=11), and all 108 rows raw_board_rows() returns are on host kbo.de — the shared kbo group CMS, whose jobs are Garmisch-Partenkirchen / Agatharied / Landsberg / Wolfratshausen / Wasserburg (sibling kbo clinics). Zero rows are on kbo-iak.de or on recruitingapp-5656.de.umantis.com, which is the board the registered careers_url actually links to; confirmed by posting_observations: 39 observations with source_url like kbo.de carry cities München/Garmisch/Landsberg/Agatharied/Wolfratshausen and none carry Haar or Taufkirchen. The real kbo-IAK board (umantis CompanyID=22) currently shows 3 vacancies, all nursing: /Vacancies/3303 and /Vacancies/3304 'Pflegefachhelfer (m/w/d)' (Haar §63 StGB) and /Vacancies/704 (Taufkirchen) — we hold only 704. Mechanism for the host drift is the TLD widening in pflege_jobs/sources/career_crawl.py:286 (_page_hosts_ok splits the seed host on the first dot, so 'kbo-iak.de' widens to any '.de' host).
- fix: Point 18402 at the umantis board (recruitingapp-5656.de.umantis.com/Jobs/1?CompanyID=22) instead of the kbo.de group CMS, and tighten _page_hosts_ok to the registrable domain rather than the TLD (this is TASK-79).
- discovered careers_url: https://recruitingapp-5656.de.umantis.com/Jobs/1?CompanyID=22&Reset=G

### 67308 RHÖN-KLINIKUM Campus Bad Neustadt a.d. Saale (750 beds) — **complete**
- ours 19 / adapter 327 / live 17 (confidence: high)
- root cause: rexx portal bewerberportal.rhoen-klinikum-ag.com paginates ?start=0/100/200/300 for 327 group-wide jobs; filtering the listing's own location column gives 62 at 'Bad Neustadt an der Saale', of which 17 are nursing. All 17 are in our DB plus two more the location regex missed ('GKP (m/w/d) Intermediate Care-Station Chirurgie', 'Pflegekraft als Arztassistenz'), and all 19 rows carry city='Bad Neustadt an der Saale' — no cross-campus leakage from Frankfurt (Oder)/Marburg/Bad Berka/Gießen.

### 66101 Klinikum Aschaffenburg-Alzenau - Standort Aschaffenburg (731 beds) — **partial**
- ours 7 / adapter 0 / live 16 (confidence: high)
- root cause: The real board is a Knockout SPA at https://jobs.klinikum-ab-alz.de/Jobs — raw curl returns only template placeholders (href="/Job/{{Id}}") and the text 'Derzeit gibt es 62 offene Stelle/n.'; a Playwright render (crawlers/portals.py:69 fetch_page) resolves 62 real /Job/<id> anchors, ~16 of them real nursing vacancies. The registry careers_url is the wrong page (https://klinikum-ab-alz.de/karriere/), and the adapter logs 'crawlers/vendor_adapters.py:550 [wp_jobs] find_job_urls: no job links in sitemap for https://klinikum-ab-alz.de (621 sitemap urls seen)' then returns 0 rows. The 7 postings we do hold all have last_seen 2026-09-05 and came from an earlier paid run, so this board has been dark for 16 days. Missing include /Job/3415 Pflegefachkraft Spät-/Wochenenddienste, /Job/3444 stellv. Stationsleitung, /Job/3440 Pflegefachhelfer Geriatrische Reha, /Job/3146 GuK/Altenpfleger Geriatrische Reha, /Job/2978 Pflegekraft Intensiv/IMC, /Job/3358 Pflegefachkraft NME, /Job/3382 MFA/OTA/Pflegefachkraft, /Job/3230 Zentrale Praxisanleiter.
- fix: Set clinics.careers_url for 66101 to https://jobs.klinikum-ab-alz.de/Jobs and give it a render rung (fetch_page) or a vendor adapter — the sitemap route on klinikum-ab-alz.de can never work.
- discovered careers_url: https://jobs.klinikum-ab-alz.de/Jobs

### 46201 Klinikum Bayreuth (662 beds) — **complete**
- ours 78 / adapter 114 / live 36 (confidence: high)
- root cause: The softgarden board exposes a clean feed at https://karriere.klinikum-bayreuth.de/jobs.feed.json (numberOfItems 114, ~36 nursing) and a job-id diff shows MISSING nursing = 0. But our 78 open rows are only 39 distinct jobs: every job is stored twice, once under the vanity host (karriere.klinikum-bayreuth.de/jobs/<id>/<slug>/, last_seen 2026-09-18) and once under the vendor host (klinikum-bayreuth.softgarden.io/job/<id>?jobDbPVId=…&l=de, last_seen 2026-09-08) — 39 distinct titles, 78 distinct external_urls. Every public count for Bayreuth is therefore inflated 2x.
- fix: Canonicalize softgarden URLs to <host>/job/<id> (drop jobDbPVId/l params and the vanity-host alias) in the posting identity key, then merge the 39 duplicate pairs.
- discovered careers_url: https://karriere.klinikum-bayreuth.de/jobs.feed.json

### 16202 München Klinik Harlaching (660 beds) — **complete**
- ours 24 / adapter 175 / live 22 (confidence: high)
- root cause: Live board https://www.muenchen-klinik.de/jobs/pflege/stellenangebote/ lists exactly 22 nursing vacancies and all 22 are held; the 2 extra rows are stale (e.g. .../pflegefachkraft-schmerzmanagement-pain-nurse-...-stellennummer-43499/ now returns HTTP 404) and were never retired. Separately, 117 of the adapter's 175 rows are not postings at all because JOB_PATH (crawlers/vendor_adapters.py:466) matches any URL containing /jobs/, so content pages like /jobs/pflege/gehalt/ and /jobs/pflege/das-one-minute-wonder/ are ingested as vacancies.
- fix: Add a NOT_JOB_PATH / URL-shape rule for muenchen-klinik.de that only accepts /stellenmarkt/aktuelles-stellenangebot/stellenangebot/<slug>-stellennummer-<n>/, and retire postings whose external_url 404s.

### 26201 Klinikum Passau (660 beds) — **complete**
- ours 4 / adapter 17 / live 4 (confidence: high)
- root cause: The bespoke klinikum_passau adapter returns all 17 postings the board renders on https://www.klinikum-passau.de/beruf-karriere/offene-stellen (7 groups, hand-counted); the only nursing ones are jobid 2763 (Dialyse GKP), 2762 (MFA/MTR oder Pflegefachmann) plus the 2 Berufsfachschule Ausbildungsplätze 2641/2643 — all four are in our DB. Passau genuinely runs a tiny board.

### 36301 Klinikum Weiden (649 beds) — **complete**
- ours 12 / adapter 34 / live 13 (confidence: high)
- root cause: POST https://kliniken-nordoberpfalz.talention.com/talention/api/3.2/job returns 38 jobs for the whole Kliniken-Nordoberpfalz group, 13 of them nursing (excluding 2 Ausbildung entries dropped by patterns.json excluded_role_classes); 12 are at Weiden and all 12 are held, the 13th (id 523502300 Pflegepädagogen, location 'Neustadt a. d. Waldnaab') belongs to a sibling site. Routing is however wrong: crawlers/routing.py:51 maps 'talention' to crawlers.vendor_adapters:crawl_wp_jobs even though a working dedicated API adapter exists at pflege_jobs/sources/feeds.py:86 — the generic sitemap walk recovered only 34 of 38.
- fix: Point routing.py:51 'talention' at pflege_jobs.sources.feeds:talention (the /talention/api/3.2/job endpoint) instead of crawl_wp_jobs.

### 66301 Klinikum Würzburg Mitte (647 beds) — **complete**
- ours 15 / adapter 1 / live 15 (confidence: high)
- root cause: Holdings are currently exact (all 15 'Pflege- & Funktionsdienst' entries on https://www.kwm-klinikum.de/beruf-chancen/stellenanzeigen/uebersicht-aller-stellen.html are in the DB, 51 jobs on the board total), but the adapter is dead: the site was restructured so every stored external_url .../stellenanzeigen/pflege-und-funktionsdienst/details/?job=<uuid> now 301s to .../uebersicht-aller-stellen/details/?job=<uuid>, clinics.ats_type for 66301 is the empty string (not 'softgarden' as the batch registry claims — kwm-klinikum.de contains zero 'softgarden' references), so it falls through to crawl_wp_jobs which logs '[wp_jobs] find_job_urls: no job links in sitemap for https://www.kwm-klinikum.de (0 sitemap urls seen)' (crawlers/vendor_adapters.py:550) and yields 1 row. These 15 postings will decay on the next reconciliation.
- fix: Set clinics.careers_url for 66301 to https://www.kwm-klinikum.de/beruf-chancen/stellenanzeigen/uebersicht-aller-stellen.html and add a small adapter parsing the server-rendered <a class="job ..." href=".../details/?job=<uuid>" data-category-name="..."> blocks on that page (51 anchors, no JS needed).

### 66204 Leopoldina Krankenhaus der Stadt Schweinfurt (640 beds) — **complete**
- ours 23 / adapter 134 / live 22 (confidence: high)
- root cause: https://leopoldina.softgarden.io/de/vacancies lists 67 jobs; 22 qualify as nursing once the 8 Auszubildende and 1 Pflegefachhelfer entries are removed (patterns.json excluded_role_classes = ausbildung/pflegehelfer/werkstudent_praktikum/nicht_pflege) and all 22 are held. The 23rd DB row (job/63758084 'Duale Studenten Pflege B. Sc.') is gone from the board, superseded by job/67334410 which we also hold. Note the softgarden primary path is dead on this tenant: https://leopoldina.softgarden.io/jobs.feed.json returns 404 (it returns 200 on jobs.pkd.de), so fetch_feed() (pflege_jobs/sources/softgarden.py:104) falls back to the BFS path — which happens to work here.
- fix: Retire job/63758084; make fetch_feed() record the 404 loudly instead of silently dropping to BFS so dead-feed tenants are visible.

### 36209 Bezirksklinikum Regensburg (medbo) (623 beds) — **complete**
- ours 12 / adapter 67 / live 6 (confidence: high)
- root cause: All 6 Regensburg-site nursing postings on https://www.medbo.de/karriere/jobsmedbo (49 /jobsmedbo/detail/ jobs group-wide) are held, but our 12 is double-counted: 3 rows are stale re-slugged duplicates (e.g. 'umschulung-quereinstieg-zur-pflegefachfrau-w-m-d-zum-pflegefachmann-w-m-d-medbo-pflegeschulen-regensburg' no longer on the board alongside its live successor) and 3 rows are not postings at all — /karriere/pflege-expertin, /karriere/schuelerin/ausbildungen/ausbildung-heilerziehungspfleger-w/m/d and /bildungswelt/.../praxisanleiterin-pflege are marketing/landing pages that crawl_wp_jobs (crawlers/vendor_adapters.py:1081) accepted via its heading heuristics; 19 of its 67 rows have no /detail/ segment at all. Three further medbo nursing jobs sit at sibling sites (Wöllershof, KJP Weiden, Parsberg) and the Parsberg one is filed under 36209 under its old slug.
- fix: Restrict medbo ingestion to URLs matching /karriere/jobsmedbo/detail/ and dedupe/retire the 3 superseded slugs.

### 16301 RoMed Klinikum Rosenheim (622 beds) — **complete**
- ours 20 / adapter 74 / live 20 (confidence: high)
- root cause: The dvinci adapter returns all 74 jobs on https://romed-jobs.de/de/jobs/, of which 30 are nursing after removing the 7 Ausbildung entries. All 30 job ids are in the DB, correctly split by site: 20 under 16301 Rosenheim, 5 under 18702 Bad Aibling, 4 under 18701 Wasserburg, 3 under 18715 Prien. Nothing missing for Rosenheim.

### 16213 Klinikum Dritter Orden, München-Nymphenburg (574 beds) — **complete**
- ours 20 / adapter 67 / live 20 (confidence: high)
- root cause: https://dritter-orden.softgarden.io/de/vacancies lists 33 jobs; the 20 we hold are exactly the qualifying set (17 nursing + 2 Praxisanleiter + 1 MTL Blutbank). The only two nursing-adjacent entries not held are job/66521248 and job/66521628, both 'Auszubildende', dropped on purpose by patterns.json excluded_role_classes. Same dead-feed caveat as Leopoldina: https://dritter-orden.softgarden.io/jobs.feed.json returns 404 and the BFS fallback carries it.

### 27105 Bezirksklinikum Mainkofen (562 beds) — **complete**
- ours 5 / adapter 41 / live 4 (confidence: high)
- root cause: The mein-check-in adapter returns all 41 positions on https://www.mein-check-in.de/mainkofen/x/stellenangebote; the only nursing ones are position-430614, -490271, -504279 and -505464, all four held. The 5th DB row ('Pflegedienst (5)', https://www.mein-check-in.de/mainkofen/x/stellenangebote/15269) is a category listing page, not a posting. Minor attribution note: position-505464 is explicitly 'am BKH Passau', a different site, filed under Mainkofen.
- fix: Drop /x/stellenangebote/<digits> category URLs in the mein-check-in adapter — only /position-<id> is a posting.

### 46301 Sana Klinikum Coburg (550 beds) — **complete**
- ours 32 / adapter 78 / live 32 (confidence: high)
- root cause: Counted live with a headless Chromium render of https://logaallin.regiomed-kliniken.de/bewerber-web/?companyEid=* (the P&I LOGA GWT board the registry's pi_asp adapter uses): 78 listings, 33 of them Coburg-located nursing, one of which ('Station 42 Chirurgische Intensivstation') is duplicated by the board itself — 32 distinct, and we hold exactly 32. The adapter returns the full 78. The 3 Lichtenfels nursing rows (Altenpfleger, Hebamme Kreißsaal, Medizinpädagoge BFS Pflege) correctly belong to the sibling clinic. Note the WAF blocks default-UA headless traffic ('Web Page Blocked! Attack ID 20000051'); a realistic user_agent on the browser context is required.
- fix: Pin a realistic user_agent in pflege_jobs/sources/pi_asp.py's browser context (it already sets UA — worth asserting it, since the bare default is hard-blocked by this host's WAF).

### 18901 Klinikum Traunstein (548 beds) — **complete**
- ours 8 / adapter 51 / live 8 (confidence: high)
- root cause: No defect. jobs.kliniken-suedostbayern.de/stellenangebote.html is one group-wide rexx board (51 job links, 12 in-policy nursing); the adapter reads all 51 and the site split is correct: 8 -> 18901 Traunstein, 3 -> 18902 Trostberg (OTA/PFK Operationsdienst, PFK Innere Medizin, PFK Akutgeriatrie), 1 -> 17201 Bad Reichenhall (GKP Pneumologie). Verified with a DB query filtered on external_url=like.https://jobs.kliniken-suedostbayern.de/* which returns exactly 12 open rows.

### 16203 München Klinik Neuperlach (545 beds) — **gap**
- ours 0 / adapter 175 / live 7 (confidence: high)
- root cause: crawlers/vendor_adapters.py:92 `_txt()` does re.sub on `s or ""` and München Klinik's JobPosting JSON-LD emits `"postalCode":81545` as a JSON number, so parse_job_page (vendor_adapters.py:597) raises `TypeError: expected string or bytes-like object, got 'int'` on the FIRST real posting page and aborts the whole board walk -- reproduced live just now, crash URL https://www.muenchen-klinik.de/stellenmarkt/aktuelles-stellenangebot/stellenangebot/elektrofachkraft-w-m-d-fuer-starkstrom-stellennummer-44875/. Compounding it, the registry careers_url https://www.muenchen-klinik.de/jobs/ is a marketing landing page with no job links; the real board is /stellenmarkt/, which ships the full list inline as `var allJobs = [...]` (57 jobs, 22 tagged family "Pflege", 7 of them at Neuperlach).
- fix: Coerce to str in _txt() (crawlers/vendor_adapters.py:92, e.g. `s = "" if s is None else str(s)`) so a numeric JSON-LD field cannot abort a board walk; and repoint careers_url for 16201/16203 to https://www.muenchen-klinik.de/stellenmarkt/ (or parse the inline `var allJobs` JSON directly -- it carries title, location and per-job link, no render needed).
- discovered careers_url: https://www.muenchen-klinik.de/stellenmarkt/

### 47701 Klinikum Kulmbach (540 beds) — **partial**
- ours 11 / adapter 75 / live 12 (confidence: high)
- root cause: Two in-policy nursing vacancies are missing entirely from the DB (not under another clinic_id, not closed -- checked all 11 rows for 47701, all `open`, and neither job id appears): https://klinikumkulmbach.softgarden.io/job/52865964 'Pflegefachkraft (m/w/d) oder Pflegefachhelfer (m/w/d) für unsere Station S1 (Akutgeriatrie)' and /job/54322908 '... für Pflegestationen der Pulmologie'. Both classify as `pflegefachkraft` (verified via CL.classify_role), so this is a discovery gap, not the Pflegehelfer policy exclusion. Likely cause: this tenant's https://klinikumkulmbach.softgarden.io/jobs.feed.json returns 404 (confirmed), so pflege_jobs/sources/softgarden.py:fetch_feed returns (None, None) and the crawl falls back to BFS over the /de/vacancies HTML, which is incomplete. The 2 pure 'Pflegefachhelfer' postings (/job/49667118, /job/63039629) are correctly dropped -- pflegehelfer is in patterns.json:347 excluded_role_classes. One row we DO hold is an MFA ('Medizinischen Fachangestellten (m/w/d) für den ambulanten OP/Station 11'), admitted only because the nicht_pflege regex `medizinische/?r? fachangestellte` (patterns.json:77) does not match the inflected 'Medizinischen Fachangestellten'.
- fix: Make the softgarden path fall back to parsing the /de/vacancies HTML for all /job/<id> anchors (37 are present in the static HTML, no render needed) when jobs.feed.json 404s, instead of a generic BFS; and widen the nicht_pflege MFA pattern to `medizinische[nr]? fachangestellte[nr]?`.
- discovered careers_url: https://klinikumkulmbach.softgarden.io/de/vacancies

### 36101 Klinikum St. Marien Amberg (535 beds) — **complete**
- ours 14 / adapter 47 / live 14 (confidence: high)
- root cause: No defect. karriere.klinikum-amberg.de (bite_jobs) exposes 47 jobposting links in the static HTML of https://www.klinikum-amberg.de/karriere/offene_stellen.php; adapter reads all 47; 13 are real nursing (Intensiv, Kinderintensiv, Anästhesie, Endoskopie, Palliativ x2, Kinderstation, Praxisanleiter, Notfallpflege, Pädiatrie x2, stv. Stationsleitung, Kinderkrankenpflege FWB) plus 1 MFA (ZNA) -- and those are exactly the 14 rows we hold. Titles and URLs match one-for-one.

### 16201 München Klinik Schwabing (521 beds) — **gap**
- ours 15 / adapter 175 / live 10 (confidence: high)
- root cause: All 15 open rows we hold for Schwabing are marketing/topic pages, not postings: /jobs/pflege/, /jobs/intensivpflege/, /jobs/ota/, /jobs/pflege/gehalt/, /jobs/pflege/tag-der-pflege/, /notfall/, /kip/ -- zero real vacancies captured. Same root cause as 16203: the int-postalCode TypeError at crawlers/vendor_adapters.py:92 kills the walk before any /stellenmarkt/ posting is parsed, so only the landing-page links survive. They pass ingest because pflege_jobs/classify.py falls back to role_class `sonstige_pflege` (rule `fallback`) for any title carrying a Pflege token -- verified: classify_role('PFLEGEN KÖNNEN.') -> ('sonstige_pflege','fallback') -- and sonstige_pflege is not in patterns.json:344 excluded_role_classes. The real board has 10 Pflege-family vacancies at Schwabing.
- fix: Fix _txt() as above, repoint careers_url to /stellenmarkt/, then purge the 15 marketing rows; longer term, stop `sonstige_pflege:fallback` from admitting rows whose URL was never a job-detail URL.
- discovered careers_url: https://www.muenchen-klinik.de/stellenmarkt/

### 18712 kbo-Inn-Salzach-Klinikum Wasserburg am Inn (518 beds) — **empty_ok**
- ours 0 / adapter 108 / live 0 (confidence: high)
- root cause: The Wasserburg site genuinely has no open nursing vacancy right now: the kbo job board filtered to this site (https://kbo.de/karriere/jobboerse?tx_solr[filter][0]=jobSite:kbo-Inn-Salzach-Klinikum+Wasserburg+am+Inn) reports '12 Stellenangebote' and all 12 are Ausbildung (Pflegefachfrau, Pflegefachhelfer, MFA, Hauswirtschafter, Kaufmann), Bundesfreiwilligendienst, Erzieher-Anerkennungsjahr, Berufspraktikum, a Facharzt and a Lehrkraft/Pflegepädagoge -- zero in-policy nursing. BUT the 0 is right by luck, not by design: registry careers_url https://kbo-isk.de/karriere contains no job links at all (only a link out to kbo.de), and the adapter compensates by walking the ENTIRE kbo group board (108 rows: Garmisch, Landsberg, Agatharied, München...), whose rows land with clinic_id=null -- 14 such open kbo.de rows exist today, none for Wasserburg. So a future Wasserburg vacancy would be ingested unattributed, not under 18712.
- fix: Set careers_url for 18712 to the jobSite-filtered kbo URL so rows can be attributed to the site instead of falling into the 176 null-clinic pool.
- discovered careers_url: https://kbo.de/karriere/jobboerse?tx_solr%5Bfilter%5D%5B0%5D=jobSite%3Akbo-Inn-Salzach-Klinikum+Wasserburg+am+Inn

### 76301 Klinikum Kempten (510 beds) — **misattributed**
- ours 36 / adapter 0 / live 10 (confidence: medium)
- root cause: The 36 open rows under 76301 are ~10 real Kempten nursing vacancies inflated 3.6x. Breakdown: 36 rows -> 22 distinct job ids (14 duplicate rows, because the same posting is stored once per host across karriere.klinikverbund-allgaeu.de (25), karriere-im.klinikverbund-allgaeu.de (9) and recruitingapp-5556.de.umantis.com (2)); 7 of those rows have the numeric job id as their title ('1581', '2619', '2128', '1531', '1586', '2245', '2324') because title extraction on the karriere-im. host fails; and several belong to sibling sites, not Kempten (Ottobeuren 'Pflegefachkraft / Gesundheits- und Krankenpflegerin', Mindelheim-Ottobeuren 'PFK mit Weiterbildung', Mindelheim 'MFA oder PFK Funktionsabteilung', Immenstadt 'PFK oder Notfallsanitäter ZINA', Immenstadt-Oberstdorf 'PFK/NFS/ATA') plus 6 /fuer-berufseinsteiger/ausbildung/ pages. One genuine miss: 'Hygienefachkraft (m/w/d)' Kempten is on the board and nowhere in the DB (checked title=ilike.*Hygienefachkraft* across all clinics). adapter_count=0 is an artifact of tools/compare_adapter_fc.py planning a single clinic: 76301's registry ats_type is '' so crawlers/routing.py:145 defaults it to wp_jobs -> crawl_wp_jobs sitemaps klinikverbund-allgaeu.de and logs 'no job links in sitemap (544 sitemap urls seen)'; in a full-registry crawl the shared board picks up ats_type=umantis from siblings 78001/78002/78003 (routing.py:156) and works, which is why we have 36 rows.
- fix: Set ats_type='umantis' on clinic 76301 (its 3 siblings on the same careers_url already have it) so a single-clinic plan does not silently route to wp_jobs; dedupe by umantis vacancy id across the 3 hosts; and reattribute the Ottobeuren/Mindelheim/Immenstadt/Oberstdorf rows from the karriere-detail/<Location>/ path segment.
- discovered careers_url: https://karriere.klinikverbund-allgaeu.de/

### 76401 Klinikum Memmingen (500 beds) — **partial**
- ours 40 / adapter 36 / live 11 (confidence: high)
- root cause: Of the 40 open rows we hold, only 9 are real postings; 31 are NEWS ARTICLES from https://www.klinikum-memmingen.de/aktuelles/detail/* ('Pflegepuppe gut in Haiti angekommen', 'Klasse 13 beim Pflegesymposium', 'Pflegemanagement-Award 2022', ...), all with verify_status=live and last_seen 2026-09-13/17. They were admitted because classify falls back to role_class `sonstige_pflege` (rule `fallback`) on any headline containing a Pflege token -- verified: classify_role('Klinikum Memmingen begrüßt ausländische Pflegekräfte') -> ('sonstige_pflege','fallback'). Discovery is already fixed: NOT_JOB_PATH at crawlers/vendor_adapters.py:485 blocks /(aktuelles|presse|news|...)/detail/ and today's adapter returns exactly the 36 real board links -- nothing retro-purged the pre-existing rows, and the verify pass keeps them open because the URLs still return 200. Separately, 2 real in-policy vacancies are missing: both 'Hygienefachkraft (m/w/d)' postings (10. Sept and 24. Aug) -- Hygienefachkraft classifies as apn_experte (in-policy) and neither is in the DB.
- fix: Close every posting whose external_url matches NOT_JOB_PATH (crawlers/vendor_adapters.py:485) rather than leaving verify to keep them alive; make verify demote a posting that no longer appears in the board walk, not just one that 404s; and investigate why the 2 Hygienefachkraft rows from the 36-row adapter output never reached ingest.

### 37301 Klinikum Neumarkt (485 beds) — **complete**
- ours 13 / adapter 44 / live 7 (confidence: high)
- root cause: Coverage is complete -- all 7 in-policy nursing vacancies on klinikum-neumarkt.dvinci-hr.com (jobs 90513, 90735, 90773, 90784, 90789, 90791, 90824 out of 44 total board jobs) are held. The 13 rows are 8 distinct ids: 5 postings are stored twice, once as the bare URL /de/jobs/<id> and once as the slugged /de/jobs/<id>/<slug>, so canonicalisation does not strip the dvinci slug. The 8th distinct row is 'Reinigungskraft (m/w/d) für das Haus für Pflege und Soziales in Parsberg' (job 90766), which classify_role today correctly returns as ('nicht_pflege','nicht_pflege:reinigung') -- a historical row kept open by the verify pass.
- fix: Canonicalise dvinci URLs to /de/jobs/<id> (drop the slug) before dedupe, and re-run classify over existing open rows so titles that are now nicht_pflege get closed.

### 26301 Barmherzige Brüder Klinikum St. Elisabeth, Straubing (475 beds) — **complete**
- ours 6 / adapter 27 / live 6 (confidence: high)
- root cause: No defect. The board lists 27 detail links; the 6 in-policy nursing vacancies (Examinierte PFK Springerpool, Pflegefachfrau/-mann, PFF/PFM Anästhesie/ATA, PFF/PFM Intensivstation, PFK für den OP, PFK Palliativstation) are exactly the 6 rows we hold. The remaining Pflege-ish board entries are correctly excluded by policy: 'Pflegefachhelferin (1 jährige Ausbildung abgeschlossen)' -> ('ausbildung','ausbildung:ausbildung'), Ausbildung PFF/PFH/OTA/ATA -> ausbildung, 'Pflegestudierende/r' -> ausbildung, MFA rows -> nicht_pflege.

### 27106 DONAUISAR Klinikum Deggendorf (465 beds) — **complete**
- ours 8 / adapter 28 / live 9 (confidence: high)
- root cause: Adapter is healthy: b-ite tenant 'donauisar-klinikum-deggendorf-dingolfing-landau' returns page.total=28 and our adapter returns 28 rows, of which 8 nursing rows land on 27106; the 9th (b-ite id for 'Pflegefachkräfte (w/m/d) für die Pneumologie am Standort Deggendorf') is filed under sibling clinic 27904 because b-ite's address.city for that posting says 'Landau' even though the title says Deggendorf (posting_id 6947, city='Landau', clinic_id='27904'). Separately the registry careers_url is flatly wrong: it points at https://www.bkh-landshut.de/stellenangebote/ (title 'Jobs im BKH Landshut', 0 occurrences of 'DONAUISAR' or 'Deggendorf' in that page) — a different hospital entirely.
- fix: Set clinic 27106 careers_url to https://www.dik-karriere.de/stellenangebote (b-ite widget, data-bite-jobs-api-listing="donauisar-klinikum-deggendorf-dingolfing-landau:main-listing"); today it points at BKH Landshut. Optionally prefer the title's 'am Standort X' over address.city when the two disagree, to stop the Pneumologie row landing on 27904.
- discovered careers_url: https://www.dik-karriere.de/stellenangebote

### 46401 Sana Klinikum Hof (465 beds) — **complete**
- ours 16 / adapter 86 / live 17 (confidence: high)
- root cause: Oracle Recruiting Cloud site CX_4025 (API host fa-eycl-saasfaeuraprod1.fa.ocs.oraclecloud.com, TotalJobsCount=1122 group-wide) has 46 requisitions with PrimaryLocation 'Hof, Bayern, Deutschland', of which 17 are nursing; we hold 16. The single missing row is requisition 5721->no, it is req 3307 'Pflegefachhelfer (m/w/d) Normalstation', dropped on purpose because classify_role() returns role_class 'pflegehelfer' which sits in EXCLUDED_ROLE_CLASSES (pflege_jobs/patterns.json:347).
- fix: None for coverage. If helper-level roles should be in scope, remove 'pflegehelfer' from excluded_role_classes in pflege_jobs/patterns.json:347 — that is a product decision, not a crawl bug.

### 17401 HELIOS Amper-Klinikum Dachau (435 beds) — **complete**
- ours 18 / adapter 33 / live 18 (confidence: high)
- root cause: Rendered https://helios-gesundheit.pi-asp.de/bewerber-web/?companyEid=1130 with chromium: 33 unique postings, exactly 18 of them nursing (OTA, ATA, 12x Pflegefachperson, Fachkrankenpflege Notfallpflege, Fachpflege Onkologie, 2x Stationsleitung, 1x stellv. Stationsleitung). Our 18 open postings match that set one-for-one. Adapter returns 33 rows = the whole board.
- fix: None. Cosmetic only: the registry website field for 17401 is the truncated string 'http://www.helios-gesundheit' (no TLD) and careers_url is null — the pi_asp seed carries companyEid=1130 so nothing breaks, but the registry row is misleading.
- discovered careers_url: https://helios-gesundheit.pi-asp.de/bewerber-web/?companyEid=1130

### 76201 Klinikum Kaufbeuren (434 beds) — **partial**
- ours 4 / adapter 2 / live 6 (confidence: high)
- root cause: Registered careers_url https://www.kliniken-oal-kf.de/jobs is not the job board, so with ats_type=null the run falls through to crawl_wp_jobs, which logs '[wp_jobs] find_job_urls: no job links in sitemap for https://www.kliniken-oal-kf.de (0 sitemap urls seen)' and yields 2 rows. The real board is https://www.kliniken-oal-kf.de/karriere/karriereportal/stellenangebote, and its DEFAULT page hides nursing entirely: the unfiltered page 1 returns 11 detail links, none of them Pflege. Only ?selection3=3 (Tätigkeitsbereich = Pflege- und Funktionsdienst) exposes them, paginated 10 per page via &page=N — union over pages = 16 Pflege rows group-wide, 6 of them at Standort Kaufbeuren (?selection1=1&selection3=3). We hold 4, of which 3 are still live and 1 (s057dea0c… pflegepaedagoge-lehrkraft-fuer-pflegeberufe) is gone from the board but still status=open (last_seen 2026-09-05). Titles are also broken: every stored title is the URL slug ('s6ac9b143 a57a 4e18 88ed 79a0e54ae58e pflegefachkraft mwd fuer die kin…'), i.e. the title fell back to the slug instead of the page heading.
- fix: Set 76201 careers_url to https://www.kliniken-oal-kf.de/karriere/karriereportal/stellenangebote and walk ?selection1=1&selection3=3&page=N until a page returns 0 detail links (pagination markup is <ul class="pagination"> with ?…&page=N hrefs). Also fix title extraction for this board — read the <a> text / detail-page <h1> rather than slugifying the href.
- discovered careers_url: https://www.kliniken-oal-kf.de/karriere/karriereportal/stellenangebote?selection3=3

### 77406 Bezirkskrankenhaus Günzburg (422 beds) — **partial**
- ours 6 / adapter 0 / live 10 (confidence: high)
- root cause: careers_url is null and ats_type=self_hosted, so tools/compare_adapter_fc.py 77406 returns 'adapter: 0 rows'. The board at https://jobs.bezirkskliniken-schwaben.de/Jobs renders every row client-side from a Mustache template — the only job hrefs in the served HTML are the literal templates href="/Job/{{Id}}" and href="//{{PortalUrl}}/Job/{{Id}}", so an anchor-based walk finds 0 job links (verified: 'distinct job ids 0' on a plain curl). The data is nonetheless fully present in the same plain-HTML response as an inline JSON model {"RegionsViewModel":…,"Jobs":[…],"TotalJobsCount":57} passed to new JobList(...). Filtering that model to jobProfiles=Pflegedienst + Location=Günzburg gives 12 rows, 10 real nursing vacancies (excluding 2 Bundesfreiwilligendienst). We hold 6, of which 5 are still live and 1 (Job/268914, Forensik) is no longer on the board but still status=open; last_seen for all six is 2026-09-08. Missing live rows: 270777 stellv. Stationsleitung neuro Frühreha, 269301 Pflegefachhelfer Pflegeheim, 270795 Pflegefachkräfte neurologische Klinik, 271004 Hauptamtliche Praxisanleitung Neurologie, 67928 Aushilfskräfte in der Pflege. Four of those five classify into kept classes (leitung / pflegefachkraft / praxisanleitung / sonstige_pflege), so this is a fetch gap, not a policy exclusion.
- fix: Register careers_url https://jobs.bezirkskliniken-schwaben.de/Jobs for 77406 and add an adapter that extracts the inline JSON model (regex on '{"RegionsViewModel"' + brace matching, then read model['Jobs'] and model['TotalJobsCount'] as the end signal, job URL = https://jobs.bezirkskliniken-schwaben.de/Job/{Id}). No render or Firecrawl rung needed. This is the operator-wide fix — the same model also covers Augsburg (76114) and Kaufbeuren (76203).
- discovered careers_url: https://jobs.bezirkskliniken-schwaben.de/Jobs?jobProfiles=Pflegedienst

### 18001 Klinikum Garmisch-Partenkirchen (415 beds) — **partial**
- ours 26 / adapter 56 / live 30 (confidence: high)
- root cause: The b-ite adapter is healthy — it returns all 56 board rows, and 26 of them reach us. The delta is classification, not crawling. (a) 'OP Leitung (m/w/d)' is a genuine miss: classify_role('OP Leitung (m/w/d)','') -> ('nicht_pflege','no_pflege_token'), so OP nursing leadership is discarded because the title carries no 'Pflege' token. (b) 3 rows — 'Pflegefachhelfer (m/w/d)', '… Innere Medizin in Murnau', '… Intensivstation' — are dropped on purpose via role_class 'pflegehelfer' in EXCLUDED_ROLE_CLASSES (pflege_jobs/patterns.json:347); none of their URLs exist in postings at any status. Counterpoint on the same board: 'Organisationsassistent (m/w/d)' also classifies nicht_pflege yet we hold both of them as open nursing postings, so this board shows the classifier both over- and under-including.
- fix: Add an OP/Funktionsdienst leadership rule so 'OP Leitung' / 'Leitung OP' hits role_class 'leitung' instead of nicht_pflege (pflege_jobs/patterns.json, leitung rule currently needs a pflege token). Separately decide whether Pflegefachhelfer is in scope; today 3 GAP rows are excluded by patterns.json:347.

### 16207 HELIOS Klinikum München West (412 beds) — **complete**
- ours 25 / adapter 45 / live 27 (confidence: high)
- root cause: Rendered https://helios-gesundheit.pi-asp.de/bewerber-web/?companyEid=1134: 46 unique postings, 27 nursing. We hold 25 — exactly the 25 experienced-nursing rows. The 2 not held are 'Pflegeassistenten (m/w/d)' and 'Pflegefachhelfer (m/w/d)', both classify_role -> 'pflegehelfer', an EXCLUDED_ROLE_CLASS (pflege_jobs/patterns.json:347); neither title exists in postings for city=München at any status. Last crawl 2026-09-18. Adapter 45 rows vs 46 unique rendered titles is a 1-row delta inside normal board churn.
- fix: None for coverage. Only actionable if helper-level roles become in scope (patterns.json:347).
- discovered careers_url: https://helios-gesundheit.pi-asp.de/bewerber-web/?companyEid=1134

### 17101 InnKlinikum Altötting (407 beds) — **misattributed**
- ours 29 / adapter 100 / live 15 (confidence: high)
- root cause: Three separate problems, all from the generic career_crawl path (ats_type is null, wp_jobs logs '0 sitemap urls seen', adapter then returns 100 raw rows). (1) MISATTRIBUTION: the board's own Pflege page https://www.innklinikum.de/stellenangebote-pflegeberufe lists 16 nursing postings split by contact address — 7 at Vinzenz-von-Paul-Str. 10, 84503 Altötting and 9 at Krankenhausstr. 1, 84453 Mühldorf. Clinic 18301 'InnKlinikum Mühldorf am Inn' holds 0 open postings; every Mühldorf job sits under 17101 with city='Altötting'. (2) NON-VACANCY POLLUTION: 5 of our 29 open rows are not job postings at all — https://www.innklinikum.de/ueber-uns/pflege ('Das Team der Pflege - 24 Stunden für Sie da'), /patienten-besucher/wartezeiten ('Wartezeiten in der Notaufnahme'), /stellenangebote-pflegeberufe (the listing page itself), /medizin-standorte/inncare-tagespflege-und-kurzzeitpflege, plus an off-domain https://inncare-pflegezentren.de/karriere/ row. Both content pages classify as ('sonstige_pflege','fallback'), which is not excluded, so any page mentioning Pflege is ingestible. (3) TRAINEE LEAK: rows like 'Ausbildung zum Anästhesietechnischen Assistent (m|w|d)' are open in our DB although classify_role returns ('ausbildung','ausbildung:ausbildung') and 'ausbildung' is in EXCLUDED_ROLE_CLASSES — the exclusion did not apply on this ingest path. Positive finding: all 24 of our real job URLs are still live on the board; only 3 Pflege-page rows are absent (2 Pflegefachhelfer = policy, 1 MFA = correctly non-nursing).
- fix: Split 17101/18301 by the posting's own contact address (84503 Altötting vs 84453 Mühldorf a. Inn) instead of defaulting to the seed town; require a detail-page URL shape (…/stellenangebote-detailansicht/…) before ingesting, which alone removes all 4 content pages; and apply sinks.filter's EXCLUDED_ROLE_CLASSES on whatever path let the 'ausbildung' rows through.
- discovered careers_url: https://www.innklinikum.de/stellenangebote-pflegeberufe

### 16214 Krankenhaus Barmherzige Brüder München (404 beds) — **partial**
- ours 5 / adapter 16 / live 11 (confidence: high)
- root cause: Worst gap in this batch: 5 held vs 11 real. The board is a TYPO3 'OycImport' vacancy list; https://karriere-barmherzige-muenchen.de/stellenangebote reports '21 Stellenangebote zu Ihrer Suche' and 'Pflege- und Funktionsdienst 11' in its facet counts, but serves only 10 <a href="…/stellenanzeige/…"> anchors and emits NO page links — the 'mehr Stellenangebote anzeigen' control is <button class="button pager-execute hide">, so ?page=2, ?limit=50 and every other URL guess return the identical 10 anchors, and a Playwright render cannot click it (cookie overlay .waconcookiemanagement intercepts pointer events). The remaining rows are only reachable through the facet params ?tx_oycimport_list[category]=15 (Pflege) and ?tx_oycimport_list[schedule]=1..4; the union of those gives all 11 Pflege rows. Missing from us: mit-zusatzverguetung-pflegefachkraft-im-springerpool, …-springerpool-ueberwiegend-nachtdienst, pflegefachkraft-fuer-intensivstation-in-voll-teilzeit, pflegefachkraft-oder-altenpfleger-fuer-sapv-am-nymphenburger-schloss, pflegefachkraft-ota-als-stellvertretende-leitung-op-pflege (all experienced nursing, none policy-excluded), plus pflegefachhelfer-als-stationshilfe-fuer-intensivstation (policy). Adapter returns 16 of the 21 board rows.
- fix: Set 16214 careers_url to https://karriere-barmherzige-muenchen.de/stellenangebote?tx_oycimport_list%5Bcategory%5D=15 and, for completeness, union the four ?tx_oycimport_list[schedule]=1..4 variants — that yields all 11 Pflege rows from plain HTTP with no render and no Firecrawl.
- discovered careers_url: https://karriere-barmherzige-muenchen.de/stellenangebote?tx_oycimport_list%5Bcategory%5D=15

### 17901 Klinikum Fürstenfeldbruck (380 beds) — **complete**
- ours 6 / adapter 35 / live 6 (confidence: high)
- root cause: https://www.klinikum-ffb.de/ausbildung-karriere/stellenangebote/ serves all 15 vacancies as plain anchors (the ?portfolioCats=N filter changes nothing — same 15 on every value), 6 of which are nursing. Set-diffed our 6 open external_urls against the board: ours-not-on-board = 0, board-not-in-ours = 9 and all 9 are correctly non-nursing (Küche, Hausmeister, 2x Oberarzt Radiologie, Oberarzt Geriatrie, MTRA, 2x Empfang/Patientenaufnahme, Werkstudent). Last crawl 2026-09-11.

### 56102 Bezirksklinikum Ansbach (377 beds) — **complete**
- ours 3 / adapter 44 / live 3 (confidence: high)
- root cause: No defect. The BEESITE board at https://jobs.bezirkskliniken-mfr.de/index.php?ac=search_result is JS-rendered but the adapter reads it in full (adapter: 44 rows == the board's own 'Suchergebnis: 44 Treffer'); Ansbach (Feuchtwanger Str. 38) carries exactly 3 nursing vacancies (jobad id 613, 932, 935) and we hold all 3.

### 56202 Klinikum am Europakanal (363 beds) — **complete**
- ours 7 / adapter -1 / live 7 (confidence: high)
- root cause: No defect, and the registry careers_url=null is harmless: the clinic is served by the shared operator board jobs.bezirkskliniken-mfr.de, whose 7 Erlangen (Am Europakanal 71) nursing ads (jobad id 745, 809, 858, 880, 884, 898, 968) are all held. All 14 nursing ads on that 44-row board are captured and split correctly across 5 clinic_ids (56102, 56202, 57407 x2, 57605, 57707) -- this board is the cleanest one in the batch.
- discovered careers_url: https://jobs.bezirkskliniken-mfr.de/index.php?ac=search_result

### 56101 ANregiomed Klinikum Ansbach (360 beds) — **complete**
- ours 58 / adapter -1 / live 20 (confidence: high)
- root cause: Nursing coverage is fine (19 of 20 live nursing ads held; only umantis Vacancy 565 'Krankenpflegehelfer & Altenpflegehelfer (m/w/d)' is missing), but our_count=58 is inflated roughly 3x: 12 of the 58 open rows are not jobs at all -- news articles and career-nav pages such as https://www.anregiomed.de/aktuelles/neuigkeiten/detail/mediroth-spendet-reanimationspuppe/ and .../stellenangebote-bewerbung/ansprechpartner/. Root cause reproduced: pflege_jobs/sources/career_crawl.py:334-335 classifies a link as a posting when JOB_HREF matches the href and the anchor text is >6 chars and does not LIST_NAV.fullmatch(); JOB_HREF (career_crawl.py:30) matches both '/detail/' and '/stellenangebot', and LIST_NAV.fullmatch('Ansprechpartner') is False, so nav and news links are stored as postings. The remaining 27 rows are real vacancies that are simply not nursing (Elektroniker, Controlling, Headhunter, Krankenhaushygieniker).
- fix: In pflege_jobs/sources/career_crawl.py:334-335, require a posting-shaped signal beyond JOB_HREF+anchor-length -- e.g. demand JOB_TEXT (a gender marker) in the anchor, or a JSON-LD JobPosting on the fetched detail page -- before emitting a row; that alone drops all 12 junk rows here and the 2 category rows at clinic 36202.

### 18201 Krankenhaus Agatharied (350 beds) — **misattributed**
- ours 8 / adapter 20 / live 9 (confidence: high)
- root cause: The 9th nursing vacancy, 'OP-Fachkraefte (m/w/d) fuer das AOZ Holzkirchen' (https://karriere.khagatharied.de/OP-Fachkraefte-mwd-fuer-das-AOZ-Holzkirchen-de-j649.html), IS in the database (posting_id 10146) but with clinic_id=NULL, so it sits in the 176 unattributed rows instead of under 18201. Cause: the rexx board sets employer name_norm='krankenhaus agatharied jobportal' (employers.employer_id=14473), which is not in Matcher.by_name (registry name is 'Krankenhaus Agatharied'), so R1_exact at pflege_jobs/registry.py:142 misses; matching then falls to the token rules which are scoped to same_town at pflege_jobs/registry.py:154 (same_town = self._by_town(ck)), and city='Holzkirchen' has zero registry clinics, so no candidate exists and the row stays NULL. The other 8 rows match only because their city is 'Hausham'. Adapter itself is healthy: 20 rows == all 20 links on the live board.
- fix: Strip board-title suffixes such as ' Jobportal'/' Karriere' in employer_norm (pflege_jobs/registry.py) so 'krankenhaus agatharied jobportal' normalises onto the registry name; alternatively let the token rules fall back to the seed clinic's board pool when the posting's own town has no registry entry, which would attach satellite sites (AOZ Holzkirchen) to their parent.

### 46203 Bezirkskrankenhaus Bayreuth (339 beds) — **misattributed**
- ours 0 / adapter 51 / live 4 (confidence: high)
- root cause: Not a crawl failure -- the adapter returns 51 rows and the GeBO board is fully read. All of it lands on the wrong clinic: every gebo-med.softgarden.io posting in the database carries clinic_id=46110 with clinic_match_rule='R1_exact', including the four whose own city column says 'Bayreuth' (Pflegefachkraft Forensische Psychiatrie, Pflegefachkraft Heilpaedagogische Station, Pflegefachkraft Akutstationaerer Bereich, Mitarbeiter im Pflege- und Erziehungsdienst KJP). Clinic 46110 is 'Tagesklinik fuer KJP am Klinikum Bamberg', town=Bamberg, beds=0. Root cause: pflege_jobs/registry.py:142 -- `if len(c) == 1: return c[0]["clinic_id"], "R1_exact", 1.0` -- returns on a unique employer-name hit with no city gate at all, unlike its own R1_exact_town sibling two lines below (registry.py:143-146) which does check _town_match. So a 339-bed Bayreuth hospital shows 0 postings while a 0-bed Bamberg day clinic absorbs the whole operator board. Note the batch file listed careers_url=null for this clinic; the live registry actually has https://www.gebo-med.de/karriere (ats_type=softgarden), so the batch snapshot is stale.
- fix: Gate R1_exact on town the same way R1_exact_town already is: at pflege_jobs/registry.py:142, when the posting's city is known and disagrees with the single candidate's town, do not return -- fall through to the town/token rules. That single change reassigns the four Bayreuth GeBO rows from 46110 to 46203.
- discovered careers_url: https://gebo-med.softgarden.io/de/vacancies

### 17801 Klinikum Freising (335 beds) — **complete**
- ours 15 / adapter -1 / live 12 (confidence: high)
- root cause: No coverage defect. The mein-check-in board (reached via https://www.mein-check-in.de/klinikum-freising/overview; the registry URL https://www.klinikum-freising.de/karriere/stellenangebote.php only links onward to it) lists 32 positions, of which 12 are real nursing vacancies -- and we hold all 12. The 3 extra rows are apprenticeship landing pages (position-206858 Pflegefachfrau/-mann, position-206855 OTA, position-251751 ATA, all 'Ausbildungsbeginn September 2027'), which is over-capture, not a gap. One borderline row, position-262631 'OP-Schleusenkraft', is not held; it is an OP sluice attendant rather than a nursing role.
- discovered careers_url: https://www.mein-check-in.de/klinikum-freising/overview

### 17501 Kreisklinik Ebersberg (328 beds) — **complete**
- ours 13 / adapter -1 / live 11 (confidence: high)
- root cause: No coverage defect. The rexx board https://jobs.klinik-ebe.de/stellenangebote.html serves all 24 job links in plain HTML; 11 of them are nursing (j79, j80, j409, j605, j860, j883, j888, j895, j900, j903, j906) and we hold all 11. The 2 remaining rows are over-capture of adjacent roles: 'Stationssekretaer/in (w/m/d) Station 2C' (j905, admin) and 'Pflegepaedagoge oder Lehrer fuer Pflegeberufe' (j891, teaching).

### 76114 Bezirkskrankenhaus Augsburg (326 beds) — **complete**
- ours 5 / adapter 0 / live 5 (confidence: high)
- root cause: The stored data is currently right -- the live board shows exactly 5 Augsburg-located nursing vacancies (Job/270013, 270014, 270015, 270016, 270617) and we hold precisely those 5 -- but the adapter is dead: `compare_adapter_fc.py 76114 --max-credits 0` returns 'adapter: 0 rows'. The registry careers_url https://www.bezirkskliniken-schwaben.de/ausbildung-karriere/stellenangebote-bewerbung does link onward to jobs.bezirkskliniken-schwaben.de/Jobs, but that board is a client-rendered Duet SPA (only /Content/js/main.js and /Scripts/libs/duet/duet.js in the served HTML): `curl https://jobs.bezirkskliniken-schwaben.de/ | grep -c '/Job/'` gives 0, while a Playwright render yields all 57 job anchors and the page text 'Derzeit gibt es 57 offene Stellen.' So the 5 rows we hold are stale survivors of an earlier successful run and will expire on the next reconcile. Board-wide this is already costing siblings: 8 live nursing ads are absent from the database (Job/263309, 267790, 269301, 270777, 270795, 271004, 271398, 67928 -- Guenzburg/Donauwoerth/Kaufbeuren/Kempten) and 2 held rows are stale (Job/262246, and Job/268914 which now 404s to the generic Karriereportal page).
- fix: Route jobs.bezirkskliniken-schwaben.de through the render rung (crawlers/portals.py fetch_page) instead of a plain fetch -- a single render of https://jobs.bezirkskliniken-schwaben.de/ returns all 57 jobs with their Ort column, which is also what is needed to split them correctly across 76114/76203/77406 and the other Schwaben sites.
- discovered careers_url: https://jobs.bezirkskliniken-schwaben.de/

### 36202 Krankenhaus St. Josef Regensburg (325 beds) — **partial**
- ours 10 / adapter 17 / live 14 (confidence: high)
- root cause: We hold 10 open rows but only 5 are real nursing vacancies; the live board has 14. Missing nursing ads include 'Akademisch qualifizierte Pflegefachkraft (Bachelor)', 'Examinierte Pflegefachkraft fuer das Delir- und Demenzmanagement', 'Examinierte Pflegefachkraft fuer unser Wundmanagement', 'Pflegefachkraft fuer unseren flexiblen Springerpool', 'Pflegefachkraft Schwerpunkt Nachtdienst', 'Stellvertretende pflegerische Leitung ambulantes Zentrum', and three OTA/ATA+Pflegefachkraft combined roles. Of the 5 non-nursing rows, 2 are category index pages stored as postings (https://csj.de/beruf-und-karriere/stellenangebote/pflegedienst and .../funktionsdienst) and 3 are /aus-und-weiterbildung/ausbildung/ apprenticeship landing pages. Same root cause as clinic 56101: at pflege_jobs/sources/career_crawl.py:334-335 a category link is classified as a job because JOB_HREF (career_crawl.py:30) matches '/stellenangebot' in the href and LIST_NAV.fullmatch('Pflegedienst') is False (LIST_NAV at career_crawl.py:32 lists the bare word 'pflege', which does not fullmatch 'Pflegedienst'). Because the link is emitted as a posting it is never enqueued as a list page, so the BFS never descends into https://csj.de/beruf-und-karriere/stellenangebote/alle-stellenangebote, the one page that carries all 27 job links. The run also logs '[wp_jobs] find_job_urls: no job links in sitemap for https://csj.de (0 sitemap urls seen)', so the sitemap path gives no second chance.
- fix: Fix the misclassification at pflege_jobs/sources/career_crawl.py:334-335 so a link whose anchor text carries no gender marker and whose target is a listing page is enqueued as a list page rather than emitted as a posting; the CSJ jobs are plain server-rendered HTML under /beruf-und-karriere/berufsfelder/alle-stellenangebote/<dienst>/... and need no render rung once the crawler reaches the index.
- discovered careers_url: https://csj.de/beruf-und-karriere/stellenangebote/alle-stellenangebote

### 46204 Klinik Hohe Warte (316 beds) — **misattributed**
- ours 0 / adapter 114 / live 4 (confidence: medium)
- root cause: Not a crawl failure -- the adapter returns 114 rows, the full softgarden DataFeed at https://karriere.klinikum-bayreuth.de/jobs.feed.json (numberOfItems: 114). All 78 open postings from that feed sit under clinic_id=46201 (Klinikum Bayreuth, 662 beds) with clinic_match_rule='R1_exact', and 46204 gets zero. Clinics 46201 and 46204 are both Klinikum Bayreuth GmbH and share the identical careers_url, so the single employer name on the feed resolves to 46201 at pflege_jobs/registry.py:142 and the Betriebsstaette never wins a row. At least 4 held postings are explicitly Hohe Warte work: 'Pflegefachkraefte fuer die Klinik fuer Querschnittgelaehmte', 'Pflegefachkraft fuer die Klinik fuer Schaedel-Hirn-Verletzte/Neurorehabilitation Phase B', 'Stellvertretende Stationsleitung Station Schaedel-Hirn-Verletzte/Neuroreha Phase B Station 02b' (its jobLocation streetAddress is literally 'Hohe Warte 8'), and 'Pflegefachkraefte fuer die Urologie'. 13 of the 114 feed items mention Hohe Warte, so the true split may be a little higher; the exact boundary is why confidence is medium, not high.
- fix: Use the feed's own jobLocation.address.streetAddress (the softgarden feed already carries 'Hohe Warte 8' vs 'Preuschwitzer Strasse') to split postings between 46201 and 46204 when two clinics share a careers_url, rather than letting R1_exact collapse the whole feed onto one clinic_id.

### 18801 Klinikum Starnberg (308 beds) — **partial**
- ours 42 / adapter 106 / live 43 (confidence: high)
- root cause: The softgarden feed https://starnberger-kliniken-karriereportalmein-check-in.career.softgarden.de/jobs.feed.json is group-wide (106 items across Starnberg, Penzberg, Seefeld, Herrsching, Wolfratshausen) and app/crawl.py:695 seeds a shared board with `c = b["clinics"][0]`, so all 42 rows land on 18801 while siblings 19003 Penzberg / 18803 Herrsching / 18804 Seefeld -- which share the identical careers_url -- each hold 0 (PostgREST count=exact returned */0 for all three); separately 2 live nursing rows are absent from the DB (`Staatl-anerkannte-Hygienefachkraft-m-w-d-`, `Medizinisches-Fachpersonal-m-w-d-GKP-oder-MFA-fuer-unser-Herzkatheterlabor`) and 1 DB row is a false positive (`Famulatur-in-der-Zentralen-Notaufnahme-am-Klinikum-Penzberg`, a medical-student clerkship).
- fix: Split feed rows onto sibling clinic_ids by item.jobLocation.address.addressLocality (Penzberg->19003, Herrsching->18803, Seefeld->18804) instead of binding the whole board to clinics[0]; re-crawl to pick up the 2 missing rows; exclude 'Famulatur' from the nursing classifier.

### 76110 Fachklinik KJF Josefinum (306 beds) — **misattributed**
- ours 0 / adapter 55 / live 3 (confidence: high)
- root cause: Josefinum's 5 nursing postings exist but are filed under clinic_id 18006 'KJF Klinik Hochried, Murnau' (22 beds), which shares the identical careers_url https://www.josefinum.de/ueber-uns/karriere/offene-stellen/ -- app/crawl.py:695 `c = b["clinics"][0]` binds every row of a shared board to the first clinic in the group, so a 306-bed Augsburg hospital reads as 0 and a 22-bed Murnau clinic carries its jobs; the live board (https://josefinum.softgarden.io/de/vacancies, 27 jobs) has exactly 3 open nursing vacancies (64877711 Pflegefachkraft im Kreissaal, 65211086 Pflegefachkraft ZNA, 66506933 Pflegefachperson Paediatrie/Chirurgie) and all 3 are captured, plus 2 stale rows (Pflegepaedagoge, PWS/Schlaflabor) no longer on the board.
- fix: Attribute shared-board rows per posting (softgarden feed carries jobLocation) rather than to clinics[0]; move the 3 live Josefinum rows from 18006 to 76110 and close the 2 stale ones. Note the batch file's `careers_url: null` for 76110 is stale -- the live registry has the URL.

### 47901 Klinikum Fichtelgebirge Marktredwitz (300 beds) — **complete**
- ours 4 / adapter 18 / live 4 (confidence: high)
- root cause: https://karriere.klinikum-fichtelgebirge.de/de/stellenangebote/ lists 18 jobs; exactly 4 are open nursing vacancies (64003448 Freigestellte Praxisanleitung, 61999269 Angestellte/r Pflege -initiativ-, 48897958 GuK bzw. Pflegefachkraefte, 58009528 Pflegefachkraft Notaufnahme) and we hold all 4 with matching softgarden.io job IDs; the other Pflege-category row is an MFA post and two are Ausbildung. Note the tenant serves no /jobs.feed.json (404) so the adapter uses the server-rendered listing, which works.

### 56403 Krankenhaus Martha-Maria Nürnberg (300 beds) — **partial**
- ours 6 / adapter 28 / live 6 (confidence: high)
- root cause: The TYPO3/solr facet https://kh-nuernberg.martha-maria.de/karriere-ausbildung/stellenangebote?tx_solr[filter][0]=jobgroup:Pflege reports '6 Ergebnisse'; we hold 5 of those 6 plus an Ausbildung row, and miss `sz-nuernberg.martha-maria.de/stellenangebote/detailansicht/pflegefachhelfer-in-m-w-d-5`. This is staleness, not a broken adapter: a live raw_board_rows() run returns 28 rows and does include 'Pflegefachhelfer/in (m/w/d)'.
- fix: Re-run the crawl for 56403 -- the adapter already sees the missing Pflegefachhelfer row.

### 57408 Krankenhaus Rummelsberg, Schwarzenbruck (300 beds) — **partial**
- ours 10 / adapter 88 / live 12 (confidence: high)
- root cause: The registered careers_url https://www.sana.de/rummelsberg/karriere is a TYPO3 teaser page that links only 15 of the 30 requisitions on the real board; the real board is the Oracle Recruiting SPA jobs.sana.de/de/sites/CX_4025 (org facet 300000012363401 = 30 reqs, verified via the hcmRestApi findReqs endpoint). Routing sends it to crawl_wp_jobs, which logs '[wp_jobs] find_job_urls: no job links in sitemap for https://www.sana.de (12000 sitemap urls seen)' (crawlers/vendor_adapters.py:550) and then walks www.sana.de group-wide for 88 unscoped rows. Missing nursing reqs: 6698 Stellv. Bereichsleitung Querschnittzentrum, 4189 Stellv. Leitung Operationssaal, 5168 Leitung Neurourologie, 2043 Freigestellter Praxisanleiter. Two of our 10 rows are stale (/job/3599, /job/422 are no longer on the board).
- fix: Re-register 57408 as ats_type=oracle with careers_url https://jobs.sana.de/de/sites/CX_4025/requisitions?selectedOrganizationsFacet=300000012363401 and read it via the hcmRestApi findReqs XHR (siteNumber=CX_4025) instead of the www.sana.de teaser page.
- discovered careers_url: https://jobs.sana.de/de/sites/CX_4025/requisitions?selectedOrganizationsFacet=300000012363401

### 18501 AMEOS Klinikum St. Elisabeth Neuburg (298 beds) — **misattributed**
- ours 53 / adapter -1 / live 5 (confidence: high)
- root cause: careers_url is the group-wide https://karriere.ameos.eu/offene-stellen/ (778 jobs nationwide; region 'AMEOS Sued' = 101, of which 32 are Neuburg an der Donau and exactly 5 are nursing: 11638, 11917, 9873, 9606, 9882). All 5 real Neuburg rows are captured, but 48 of our 53 rows are jobs at other AMEOS sites -- Neustadt i.H. 8, Luebeck 7, Osnabrueck 6, Stassfurt 4, Vogtsburg 3, Petershagen 3, Ueckermuende 3, Oldenburg i.H. 2, Einsiedeln (CH) 2, Winterlingen 2, plus Kiel, Magdeburg, Ratzeburg, Haldensleben, Brunnen (CH), Messstetten, Aschersleben, Simbach -- and every one of them is stamped city='Neuburg/Donau' because a crawler with no per-posting location falls back to the seed clinic's town (app/crawl.py:545 comment, TASK-59a), while pflege_jobs/sources/ats_seeds.py:16 NATIONWIDE matches 'ameos' so bavaria_only_operator is False and nothing filters them out. Adapter run did not finish: crawl_wp_jobs on this 778-job board was still walking after 17 minutes (sitemap yields 0 urls, so it page-walks) and I killed it.
- fix: Read the per-posting location from the AMEOS listing slug/detail page and drop or re-attribute the 48 non-Bavarian rows; keep only the 5 Neuburg ones under 18501. Also bound the wp_jobs page-walk on this board -- it does not terminate in a usable time.

### 67705 Bezirkskrankenhaus Lohr am Main (295 beds) — **misattributed**
- ours 0 / adapter 7 / live 1 (confidence: high)
- root cause: The board has exactly 7 posts total (https://karriere.bezirkskrankenhaus-lohr.de/wp-json/wp/v2/jobs?per_page=100 returns 7) of which 1 is an open nursing vacancy ('Pflegefachkraft (m/w/d) in Lohr a.Main, Aschaffenburg oder fuer unseren Springerpool') plus 1 Ausbildung. That one posting IS in our DB but under clinic_id 66105 'Psychiatrische Klinik Aschaffenburg des BKH Lohr am Main' (50 beds), which shares the identical careers_url -- again app/crawl.py:695 `c = b["clinics"][0]`. 67705 has zero posting rows of any status. Secondary: registry ats_type='concludis' is wrong (the board is a WordPress `jobs` custom post type); it only works because crawl_concludis falls through to crawl_wp_jobs at crawlers/vendor_adapters.py:232.
- fix: Attribute shared-board rows per posting instead of to clinics[0], moving the Lohr row from 66105 to 67705; correct ats_type 67705/66105 from concludis to wp_jobs.

### 56201 Waldkrankenhaus St. Marien, Erlangen (290 beds) — **partial**
- ours 17 / adapter 2 / live 13 (confidence: high)
- root cause: The adapter is live-broken: raw_board_rows() returns only 2 rows, both Ausbildung landing pages on waldkrankenhaus.de, and logs '[wp_jobs] find_job_urls: no job links in sitemap for https://www.waldkrankenhaus.de (164 sitemap urls seen)' (crawlers/vendor_adapters.py:550) -- crawl_wp_jobs walks only the clinic's own host, but every vacancy lives off-host on jobs.malteser.de (31 job-detail links on https://www.waldkrankenhaus.de/karriere/unsere-stellenangebote.html, 13 of them nursing). Our 17 rows are frozen relics of an earlier crawl: 12 unique requisition IDs still live, 3 duplicate rows of a req whose slug changed (IDs 1622, 1625, 11702 each stored twice), and 2 stale (5179 delisted; 6664 now points at an Ausbildung-MFA posting). Missing: `Pflegefachhelfer-m-w-d-9668.html`.
- fix: Let crawl_wp_jobs follow the off-host job links already present on the careers page, or re-register careers_url to the jobs.malteser.de list scoped to the Erlangen entity; key posting identity on the trailing requisition ID rather than the full slug URL so a slug change does not create a second row.
- discovered careers_url: https://jobs.malteser.de/de/job-offer-list/

### 67804 Bezirkskranken-haus Werneck (290 beds) — **partial**
- ours 7 / adapter 21 / live 9 (confidence: high)
- root cause: https://bezirk-unterfranken.helixjobs.com/bkhwerneck/joblist lists 21 jobs, 9 of them open nursing vacancies. We hold only 5 distinct ones: 2 of our 7 rows are duplicates -- requisitions prj=2618P775 and prj=2618P779 are each stored twice, once as /bkhwerneck/jobad?prj=... and once as /KPPPM/jobad?prj=..., because posting identity keys on external_url rather than the prj requisition ID. Missing 4, all satellite sites on the same board: 2618P660 Pflegefachkraefte Muennerstadt, 2618P680 OP-Pflegefachkraft Muennerstadt, 2618P880 Pflegefachkraefte PHR Bad Brueckenau, 2618P932 Pflegefachkraft im Nachtdienst Bad Brueckenau. The adapter finds all 21 today, so this is ingest staleness, not a broken reader.
- fix: Re-run the crawl for 67804 to pick up the 4 Muennerstadt/Bad Brueckenau rows; dedupe on the helix `prj` parameter so the same requisition under two board paths collapses to one posting.

### 17701 Klinikum Landkreis Erding (288 beds) — **partial**
- ours 12 / adapter 30 / live 12 (confidence: high)
- root cause: Three separate defects on a board we otherwise read well (https://www.mein-check-in.de/klinikum-erding/x/stellenangebote/pflege): (1) missing position-443161 'Pflegefachhelfer (m/w/d) - fuer Menschen mit Demenz'; (2) one of our 12 rows is not a job at all -- external_url https://www.mein-check-in.de/klinikum-erding/x/stellenangebote/pflege stored with title 'Pflege / Funktionsdienst / OP und Anaesthesie (15)', i.e. the category listing page itself ingested as a posting; (3) position-482672 'Pflegefachkraefte (m/w/d) - Innere Medizin und Onkologie in Dorfen' is filed under 17701 although it belongs to sibling 17702 'Klinikum Landkreis Erding -Aussenstelle Dorfen-', which shares the careers_url and holds 0 open postings. Adapter returns 30 rows, so the missing vacancy is reachable.
- fix: Reject mein-check-in `/x/stellenangebote/<category>` URLs as postings (they are listing pages); re-crawl to add position-443161; route the 'in Dorfen' posting to 17702.

### 18717 Schön Klinik Bad Aibling (287 beds) — **empty_ok**
- ours 0 / adapter 292 / live 0 (confidence: high)
- root cause: Board is the group-wide rexx board; paged all 3 pages of https://jobs.schoen-klinik.de/stellenangebote.html?start=0|100|200 (292 unique -j<id> jobs, location column parsed per row) and Bad Aibling has exactly 12 open jobs — Arzt/Oberarzt, 3x MTRA/MTA-F, Physiotherapeut, Leitung Sprachtherapie, Betriebstechniker, Servicekraft, 2x Praktikant Psychologie — zero nursing, so 0 held is the correct result.

### 47601 HELIOS Frankenwaldklinik Kronach (282 beds) — **blocked**
- ours 9 / adapter -1 / live -1 (confidence: low)
- root cause: helios-gesundheit.de returns Akamai 'Access Denied' (ref #18.348e1402…) to plain curl AND to a Playwright chromium render from this IP, exactly as crawlers/routing.py:90 WALLED documents, so live truth is uncountable; separately our 9 open rows are only 6 distinct titles — 3 jobs are duplicated across two external_url families (UUIDv4 set last_seen 2026-09-08 vs UUIDv5 set last_seen 2026-09-17/18), and compare_adapter_fc.py hit the 600 s timeout on this clinic.
- fix: Dedupe the v4/v5 Helios UUID URL families onto one posting (the v5 ids are name-derived, not read off the board), and give Helios a non-datacenter egress or a Firecrawl rung before any Helios count can be trusted.

### 16215 Rotkreuzklinikum München, Betriebsstätte Nymphenburger Straße (280 beds) — **partial**
- ours 5 / adapter 18 / live 15 (confidence: high)
- root cause: Registry careers_url points at the owning association's page (schwesternschaft-muenchen.de/stellenangebote/index.php) whose concludis loader declares setJobBoard '36' — board 36 is the association's Altenpflege homes and Berufsfachschulen (18 jobs in Grünwald/Lindau/Lindenberg/Würzburg, zero hospital jobs), which is exactly the 18 rows the adapter returns; the hospital's own board is declared as setMultiJobBoard '6|18|24|30' on rotkreuzklinikum-muenchen.de/stellenangebote/ and https://swmbrk.concludis.de/prj/lst/?b=6 lists 32 jobs / 15 real nursing vacancies, of which we hold only 5 (all last_seen 2026-09-05, from an older ?b=0 crawl).
- fix: Repoint clinic 16215 careers_url to https://www.rotkreuzklinikum-muenchen.de/stellenangebote/ and make concludis_widget() (crawlers/vendor_adapters.py:1659-1662, CONCLUDIS_BOARD) also read setMultiJobBoard and fan out over each pipe-separated board id in crawl_concludis_widget's /prj/lst/?b=… fetch (line 1671).
- discovered careers_url: https://www.rotkreuzklinikum-muenchen.de/stellenangebote/

### 56404 Klinik Hallerwiese Nürnberg (279 beds) — **complete**
- ours 29 / adapter 5 / live 13 (confidence: medium)
- root cause: All 13 real Hallerwiese nursing vacancies (the b-ite postings at PLZ 90419 — ATA/Anästhesie, Kinderkrankenpflege, Hebamme, OTA/GuK, chirurg./internist. Station, Gynäkologie, Intensiv, Pädiatrie x2, Neonatologie, Pflegehilfskraft, Leitung Anästhesie-Funktionsdienst) are held, but the 29 rows are inflated three ways: 5 duplicate rows from a URL-shape change (jobs.diakoneo.de/jobposting/<id> on 2026-09-05 vs /de/jobposting/<id> on 2026-09-18, both still status=open), 1 non-job landing page ingested as a posting ('Deine Zukunft bei Diakoneo: Pflege mit Herz und Haltung', external_url = the listing page itself), and ~8 rows belonging to Diakoneo's other Nürnberg sites (PLZ 90431 Sozialstation, 90429 Wohnstift Hallerwiese, 90441 Kita) filed under the hospital's clinic_id; separately the adapter only finds 5 rows because the registry careers_url https://www.diakoneo.de/karriere/ carries no b-ite widget — data-bite-jobs-api-listing="diakoniewerk-schwaebisch-hall:lp-pflegeberufe-listing" lives on /karriere/stellenportal/stellen-in-der-pflege.
- fix: Repoint careers_url to the page that actually carries the b-ite widget, canonicalise the /de/ URL variant so the 5 duplicate pairs collapse, drop the listing-page row, and split the non-90419 Nürnberg postings onto their own Diakoneo sites.
- discovered careers_url: https://www.diakoneo.de/karriere/stellenportal/stellen-in-der-pflege

### 47802 Bezirksklinikum Obermain (278 beds) — **misattributed**
- ours 0 / adapter 51 / live 2 (confidence: high)
- root cause: Both Ebensfeld nursing vacancies on the shared GeBO softgarden board (https://gebo-med.softgarden.io/de/vacancies job 65704226 'Qualifizierte Mitarbeiter für den Pflegedienst der aufsuchenden Pflege' and job 66707893 'Pflegefachhelfer', both JSON-LD PLZ 96250 Ebensfeld) ARE in the database as postings 5632 and 12573 with city='Ebensfeld' — but clinic_id='46110', the 0-bed 'Tagesklinik für KJP am Klinikum Bamberg' sibling under the same GeBO operator; posting_observations for 5632 show the employer_name flipping from the correct 'Bezirksklinikum Obermain' to the seed-copied 'Tagesklinik für KJP am Klinikum Bamberg - Betriebsstätte am Bruderwald-' on later rows, and the Matcher's R1_exact (pflege_jobs/registry.py:141) then matched that copied name straight back to 46110 — the circular seed-name match employer_inherited was added to block (registry.py:117-130) but which is not set on this path.
- fix: Set employer_inherited=True on shared-board rows whose employer_name is the seed clinic's own registry name, so R1_exact cannot fire, and let city ('Ebensfeld') carry the attribution.

### 47801 Sana Klinikum Lichtenfels (276 beds) — **complete**
- ours 3 / adapter 78 / live 3 (confidence: high)
- root cause: Rendered the P&I LOGA GWT board (https://logaallin.regiomed-kliniken.de/bewerber-web/?companyEid=%2a) with Playwright: 78 rows, of which 9 are Lichtenfels — 4 Ärztlicher Dienst, 1 Therapie, and exactly the 3 we hold (Hebamme/Kreißsaal, Altenpfleger/Pflegedienst, Medizinpädagoge/Schulpersonal); cross-checked the Sana Oracle site CX_4025 (all 1122 requisitions paged) and its only Lichtenfels rows are Reinigungskraft and Sanitätshausfachverkäufer, so no second nursing board exists.

### 16107 Zentrum für psychische Gesundheit (ZPG) Ingolstadt (275 beds) — **misattributed**
- ours 0 / adapter 108 / live 1 (confidence: high)
- root cause: The kbo jobbörse facet jobSite='kbo-Donau-Altmühl-Kliniken | Ingolstadt | Krumenauerstraße 25' returns 4 jobs, of which 1 is nursing (https://kbo.de/karriere/jobs/-000130-dak13-2025-gesundheits-und-krankenpfleger-altenpfleger-fuer-gerontopsychiatrische-pflegestation-m-w-d); that posting IS in the database as posting_id 7401 but with city='München' and clinic_id='16264', clinic_match_rule='R0_board_town' — the kbo detail page carries no city at all (only a 'Standorte' nav), so the crawler fell back to the board's own town (München, kbo HQ) and board-town matching picked a Munich kbo site; the per-job location exists only as the jobboerse jobSite facet, which the adapter never reads.
- fix: Crawl the kbo board through its jobSite-faceted listing (kbo.de/karriere/jobboerse?tx_solr[filter][0]=jobSite:…) and carry the facet value as the row's city, instead of defaulting to the board host's town.
- discovered careers_url: https://kbo.de/karriere/jobboerse?tx_solr%5Bfilter%5D%5B0%5D=jobLocation%3Akbo-Donau-Altm%C3%BChl-Kliniken

### 18301 InnKlinikum Mühldorf am Inn (275 beds) — **misattributed**
- ours 0 / adapter 100 / live 10 (confidence: high)
- root cause: https://www.innklinikum.de/stellenangebote-uebersicht renders all 50 offers inline, each with its own 'Einsatzort:' field — 10 are Mühldorf nursing (9 Pflegedienst: Pflegefachhelfer, 6x GuK/Pflegefachmann incl. Springer-/Flexipool, Pflegefachhelfer Flexipool, Zentraler Praxisanleiter; plus 1 Funktionsdienst GuK Anästhesie/ATA) — yet every InnKlinikum row in the database sits on clinic_id 17101 (InnKlinikum Altötting) with city='Altötting' and clinic_match_rule='R0_board_name', because the shared board's rows inherit the seed clinic's town instead of reading the per-offer Einsatzort; the same query also shows 4 non-job pages ingested as open postings ('Wartezeiten in der Notaufnahme', 'Das Team der Pflege - 24 Stunden für Sie da', 'InnCare Tagespflege und Kurzzeitpflege', 'Stellenangebote Pflegeberufe - Innklinikum').
- fix: Parse the per-offer 'Einsatzort:' cell inside each div.accordion.joblistitem on the listing page and use it as the row's city, so Mühldorf rows stop inheriting Altötting; and stop ingesting the listing/marketing pages as postings.

### 27705 Kreiskrankenhaus Eggenfelden (275 beds) — **misattributed**
- ours 0 / adapter 39 / live 12 (confidence: high)
- root cause: https://karriere.rottalinnkliniken.de/offene-stellen/ lists 39 jobs, 13 in category 'Pflege- und Funktionsdienst' of which 12 are nursing (Urologie, Orthopädie, Unfallchirurgie, Gefäßchirurgie/Kardiologie, Kardiologie+Stroke Unit, Gastroenterologie, Notaufnahme, geriatrische Reha, Akutgeriatrie/Praxisanleitung, Springerpool, Pflegefachhelfer, stellv. Stationsleitung Geriatrie; excluding Zentralsterilisation) — all 12 are in the database, but under clinic_id 27701 'Psychosomatische Fachklinik Simbach am Inn' (190 beds), which cannot host urology/stroke-unit/trauma wards; posting 6437's observation carries employer_name='Psychosomatische Fachklinik Simbach am Inn' and city='Simbach am Inn' copied from the seed, and R1_exact (pflege_jobs/registry.py:141) matched that copied name back to 27701 — 27701 and 27705 share the identical careers_url in the registry.
- fix: Same root fix as 47802: mark seed-copied employer_name as employer_inherited so R1_exact is skipped on shared boards; for this board the sites carry no per-job location, so route by department (Eggenfelden is the acute house) or default the shared-board rows to the largest site rather than the smallest.

### 66202 Krankenhaus St. Josef Schweinfurt (272 beds) — **complete**
- ours 7 / adapter 21 / live 7 (confidence: high)
- root cause: https://karriere.josef.de/jobs.feed.json returns 21 JobPosting items, all Schweinfurt; the nursing ones are exactly Pflegefachkraft Intensivstation, OP, Springerpool, Akutgeriatrie, Palliativstation, Pflegefachkraft (m/w/d) and Praxisanleiter — the identical 7 we hold, with the Ausbildung Pflegefachfrau/-mann row correctly excluded.

### 57601 Kreisklinik Roth (270 beds) — **complete**
- ours 1 / adapter 9 / live 1 (confidence: high)
- root cause: The b-ite tenant kreisklinik-roth:main-listing reports page.total=9 and the adapter returns exactly 9; the only nursing vacancy is 'Pflegefachkraft fuer den Bereich Notaufnahme (m/w/d)' (karriere.kreisklinik-roth.de/jobposting/c04fcd09...) and we hold it. The other 8 are Ausbildung/FSJ/Famulatur/PJ, a Logopaede, an Oberarzt, and a Minijob Blutabnahme.

### 67601 HELIOS Klinik Erlenbach a. Main (267 beds) — **blocked**
- ours 2 / adapter -1 / live -1 (confidence: low)
- root cause: helios-gesundheit.de is Akamai-walled from this network - plain curl and a real chromium render via crawlers/portals.fetch_page both return 'Access Denied ... errors.edgesuite.net' (403), exactly the case crawlers/routing.py:90 WALLED already names; the documented fallback board helios-gesundheit.pi-asp.de answers but 67601 has no entry in data/registry/pi_seeds.json (only Dachau 1130, Muenchen West 1134, Perlach 1135), and the wildcard board companyEid=%2a rendered 'Aktuell finden Wartungsarbeiten statt' at audit time. Separately the registry careers_url is a single job-detail UUID page (https://www.helios-gesundheit.de/karriere/job/3bc89d91-.../), not a board. Also found: 18 postings filed under clinic_id 67601 are actually Dachau/Markt Indersdorf rows (posting_id 5721-5738, city='Dachau'/'Markt Indersdorf', /job-detail/ URLs) - now status=expired, but a live cross-clinic misattribution while they were open.
- fix: Add Erlenbach/Miltenberg to data/registry/pi_seeds.json with its companyEid so it routes to helios-gesundheit.pi-asp.de like the other three Helios houses, and replace the registry careers_url (a single job-detail page) with the board URL; separately investigate why 18 Dachau rows were attributed to 67601.

### 37601 St. Barbara Krankenhaus Schwandorf (267 beds) — **complete**
- ours 1 / adapter 32 / live 1 (confidence: high)
- root cause: barmherzige-bieten-zukunft.de carries 30 Schwandorf postings (confirmed against the location=all view, 129 group-wide) and only 3 sit in 'Pflege- und Funktionsdienst': one real vacancy ('Pflegefachkraft Intensivstation (VZ/TZ)', which we hold) plus two standing 'Initiativbewerbung' entries (Pflegefachkraft, Pflegefachhilfe) that are speculative-application placeholders, not vacancies.

### 26107 Bezirkskrankenhaus Landshut (260 beds) — **complete**
- ours 7 / adapter 22 / live 6 (confidence: high)
- root cause: Board section 'PFLEGE- UND ERZIEHUNGSDIENST (9)' on https://www.mein-check-in.de/bkh-landshut/x/stellenangebote holds 9 entries, 6 of which are real nursing vacancies (positions 275972, 388749, 536581, 88017, 448447, 121374) - we hold all 6; the other 3 (Alltagsbegleiter, Sitzwachen, Ex-In Genesungsbegleiter) are correctly not nursing. Our 7th row is junk: posting_id 5654, title 'PFLEGE- UND ERZIEHUNGSDIENST (10)', external_url .../x/stellenangebote/14293 - a category heading ingested as a posting (source_id 20, role_rule='fallback'), and crawl_mein_check_in (crawlers/vendor_adapters.py:1414) only emits position-<id> URLs, so it came from the generic career_crawl path before ats_type was set.
- fix: Retire posting_id 5654 and make the generic career_crawl path reject mein-check-in /x/stellenangebote/<id> category URLs as job links.

### 27204 Kreiskrankenhaus Freyung (260 beds) — **empty_ok**
- ours 0 / adapter 23 / live 0 (confidence: high)
- root cause: https://www.frg-kliniken.de/beruf-karriere/aktuelle-stellenangebote states verbatim under the 'Pflegedienst' heading: 'Leider sind derzeit in diesem Bereich keine Stellen zu besetzen.' The only nursing-adjacent entries are 2026 Ausbildungsplaetze (Pflegefachmann/-frau, OTA/ATA), an excluded role class. Note the batch file listed careers_url as null; the live registry row does have a working careers_url and ats_type=self_hosted, and the adapter reads it (23 rows).
- discovered careers_url: https://www.frg-kliniken.de/beruf-karriere/aktuelle-stellenangebote

### 77901 Donau-Ries Klinik Donauwoerth (255 beds) — **empty_ok**
- ours 2 / adapter 42 / live 0 (confidence: high)
- root cause: https://dongku.de/stellenangebote/ lists exactly 10 postings group-wide (page/2 is 404) and none is a nursing vacancy at the Donauwoerth clinic - the two Pflegefachkraft rows are Seniorenheim Monheim and Seniorenheim Wemding, the Stv. Stationsleitung ZNA is Stiftungskrankenhaus Noerdlingen, and the only Donauwoerth entry is an Oberarzt. Both rows we hold are non-postings: a news article (dongku.de/schulleben/neue-fuehrung-donau-ries-kliniken-stellen-pflegeschule-donauwoerth-neu-...) and a PDF asset (dongku.de/wp-content/uploads/gKU-GKP-alle-Haeuser.pdf), which the generic crawler (adapter: 42 rows off a 10-job board) picked up because the registry careers_url is a single, now-delisted job-detail page, not the board index.
- fix: Set clinics.careers_url for 77901 to https://dongku.de/stellenangebote/ and retire the two junk rows; exclude .pdf assets and non-/stellenangebote/ paths from career_crawl job-URL detection on this host.
- discovered careers_url: https://dongku.de/stellenangebote/

### 18811 Asklepios Lungenklinik Gauting (250 beds) — **partial**
- ours 11 / adapter 1398 / live 16 (confidence: high)
- root cause: Not a board gap: the adapter returns all 1398 Asklepios postings including all 21 Gauting rows, but every open posting under 18811 has last_seen=2026-09-05, so the 4 Gauting nursing vacancies published 2026-09-16 (Pflegefachperson mit Praxisanleitung; Pflegefachperson Fachweiterbildung Wundmanagement; Stationsleitung Langzeitbeatmung/FFR; Pflegefachperson/OTA/MFA fuer den OP - all verified live via POST https://www.asklepios.com/api/search, workarea 'Pflege- und Funktionsdienst') were never ingested; the 5th, 'Onkologische Fachkraft (w/m/d) fuer pneumologische Onkologie' (published 05.02.26), is a real classifier miss - classify_role returns ('nicht_pflege','no_pflege_token') at pflege_jobs/classify.py:90 because the title carries no pflege token.
- fix: Re-crawl 18811 (board is 16 days stale), and add an 'onkologische fachkraft' / 'X-Fachkraft + Pflege- und Funktionsdienst workarea' token to the pflegefachkraft or fachpflege pattern in pflege_jobs/patterns.json so section_labels can rescue titles with no pflege token.

### 18007 Berufsgenossenschaftliche Unfallklinik Murnau (240 beds) — **complete**
- ours 15 / adapter 377 / live 14 (confidence: high)
- root cause: b-ite tenant klinikverbund-gesetzlichen-unfallversicherung-kuv returns 377 postings and the adapter returns exactly 377; 31 are Murnau, of which 14 are real nursing vacancies and we hold all 14. Our 15th row, 'Arztsekretaer (m/w/d) Vorzimmer Station 34', is a non-nursing false positive. Correctly skipped: 'Ausbildung Pflegefachmann/-frau' and 'Praktikant (m/w/d) in der Pflege' (excluded_role_classes in pflege_jobs/patterns.json).
- fix: Optional precision fix: add 'arztsekretaer' to the nicht_pflege tokens in pflege_jobs/patterns.json.

### 18707 Schoen Klinik Vogtareuth (240 beds) — **complete**
- ours 6 / adapter 292 / live 6 (confidence: high)
- root cause: jobs.schoen-klinik.de paginated with ?start=N yields 292 unique -de-j<id>.html postings and crawl_rexx (crawlers/vendor_adapters.py:1313) returns exactly 292; 26 are Vogtareuth and 6 of those are nursing (j15060, j15611, j15687, j15696, j16146, j16227) - we hold all 6, matched one-for-one by URL. The remaining 20 are doctors, therapists, MTR, admin and one Sterilisationsassistent.

### 17201 Kreisklinik Bad Reichenhall (240 beds) — **complete**
- ours 1 / adapter 51 / live 1 (confidence: high)
- root cause: The shared Kliniken Suedostbayern rexx board carries 51 postings total and the adapter returns exactly 51; only one is a Bad Reichenhall nursing vacancy ('Gesundheits- und Krankenpfleger (w/m/d) Pneumologie', j1253) and we hold it. Across the whole KSOB board we hold 12 nursing rows (7 Traunstein, 3 Trostberg, 1 Bad Reichenhall, 1 shared) - the uncaptured 39 are Ausbildung, doctors, therapists, admin and technical, all correctly filtered. Minor multi-site issue: 'Freigestellte Praxisanleitung (w/m/d)' is advertised for Bad Reichenhall, Berchtesgaden, Traunstein and Trostberg but is filed only under Traunstein (18901).
- fix: Fan a rexx posting whose location line names several towns out to every matching clinic_id instead of only the first.

### 77401 Klinik Günzburg (240 beds) — **misattributed**
- ours 0 / adapter 52 / live 25 (confidence: high)
- root cause: All ~25 Günzburg nursing vacancies from the shared kliniken-gz-kru.de board are filed under sibling 77402 (Klinik Krumbach) — 23 of 77402's 29 open rows carry city=Günzburg — because pflege_jobs/registry.py:238 R0_board_name matched every row to the seed clinic's own name and its same_town_only guard only bites when the observation already carries a city (city was populated later); stored clinic_match_rule on those rows is literally 'R0_board_name'.
- fix: Re-run the Matcher over the 77402 open rows: today's code resolves ('Kreiskliniken Günzburg-Krumbach, AöR','Günzburg', board=[77401,77402]) to ('77401','R2_operator_town',0.9), so a relink pass moves the 23 Günzburg-city rows without any crawler change. Longer term, apply same_town_only in registry.py:238 against the geo-enriched city rather than the crawl-time city.

### 37202 Sana Kliniken - Krankenhaus Cham (235 beds) — **misattributed**
- ours 4 / adapter 85 / live 6 (confidence: high)
- root cause: The 6 real Cham nursing postings ARE in the DB (jobs.sana.de/de/sites/CX_4025/job/{3038,3040,3084,3088,3864,3897}, source_id 20) but with clinic_id NULL: Matcher.match('Krankenhaus Cham','Cham') returns None because pflege_jobs/registry.py:157-164 strips the city token, leaving et={} , then takes the `if x[key]` branch since registry name 'Sana Kliniken - Krankenhaus Cham' has _ntoks={'sana'}, so overlap(set(),{'sana'})=0 and the kind-only fallback is never reached. Separately the 4 rows we DO hold under 37202 are not vacancies at all — they are sana.de marketing pages (/karriere/beruf/pflege/, /karriere/berufseinstieg/ausbildung/gesundheit-pflege/) harvested by crawl_wp_jobs from registry careers_url https://www.sana.de/cham/karriere/ (ats_type=typo3_jobs); that adapter returns 85 such rows and never reaches the Oracle board.
- fix: Two fixes: (1) relink the 6 null-clinic CX_4025 rows to 37202 — pass board context, or let R3_tokens fall through to the kind-only branch when et is empty after city-token removal; (2) reregister 37202 as ats_type=oracle with careers_url https://jobs.sana.de/de/sites/CX_4025/requisitions (crawl_oracle already exists in routing.ADAPTERS) so the 4 marketing-page rows stop being created.
- discovered careers_url: https://jobs.sana.de/de/sites/CX_4025/requisitions

### 18716 Schön Klinik Roseneck (234 beds) — **complete**
- ours 4 / adapter 292 / live 4 (confidence: high)
- root cause: jobs.schoen-klinik.de full-text search for 'Prien' returns 16 postings for the site; the nursing ones are Pflegedienstleitung Roseneck, Pflegefachkraft Komplexstation Erwachsenenbereich, Pflegefachkraft Kinder-/Jugendstation, plus the borderline Erzieher-oder-Heilerziehungspfleger role — we hold exactly those 4. (FSJ in der Pflege and Co-Therapeut are not vacancies/not nursing.)

### 17302 Asklepios Stadtklinik Bad Tölz (230 beds) — **partial**
- ours 2 / adapter 1398 / live 4 (confidence: high)
- root cause: The Asklepios /api/search endpoint returns 1398 items today, 21 at Bad Tölz, 4 of them nursing at company='Asklepios Stadtklinik Bad Tölz'; we hold only 2. Missing: 'Pflegefachkraft / MFA / OTA (w/m/d) Endoskopie' and 'Pflegefachkraft / Operationstechnischer Assistent / MFA (w/m/d)'. Not a classifier drop (both classify as ota_ata, which is NOT in EXCLUDED_ROLE_CLASSES) and not a matcher drop (employer string hits R1_exact on 17302) — our two 17302 rows have last_seen 2026-09-05, i.e. the Asklepios board has not been re-ingested in 16 days while sibling clinics were crawled 2026-09-20/21.
- fix: Re-run the asklepios board ingest for 17302; crawl_asklepios (crawlers/vendor_adapters.py:1526) reads all 1398 rows today at zero Firecrawl cost, so a scheduled re-crawl closes this without any code change.

### 46103 Klinikum Bamberg - Betriebsstätte am Michelsberg- (225 beds) — **misattributed**
- ours 0 / adapter 127 / live 28 (confidence: high)
- root cause: 46101 (Bruderwald) and 46103 (Michelsberg) share careers_url https://www.sozialstiftung-bamberg.de/stellenangebote/, so crawlers/routing.py:150 groups them into one board; pflege_jobs/registry.py:91-95 _pick_site then resolves same-operator/same-town ties by max beds, and 46101 (911 beds) always beats 46103 (225). All ~28 nursing postings from the 126-job d'vinci board land on 46101 (35 open rows there, ~5 of them same-title duplicates). Note the registry careers_url page itself is unreadable by plain HTML fetch — the d'vinci widget is behind a consent manager (data-ccm-loader-src="https://sozialstiftung-bamberg.dvinci-easy.com/appo/public/jobWidgetLoader/f2382318-....js", no plain src) — but crawl_dvinci resolves the tenant and returns 127 rows, so this is attribution only, not a fetch gap.
- fix: Accept 46101 as the operator-board owner and mark 46103 as a satellite of it in the UI, OR split by the 'Standort Michelsberg' string that d'vinci already puts in job titles. No nursing posting on the board currently names Michelsberg, so nothing is actually lost today — the risk is that 46103 renders as an empty clinic page.
- discovered careers_url: https://sozialstiftung-bamberg.dvinci-easy.com/de/jobs

### 47401 Klinikum Forchheim (225 beds) — **partial**
- ours 10 / adapter -1 / live 11 (confidence: high)
- root cause: The board paginates across 3 pages (28 postings total: 13 + 10 + 5). We hold 10 of the 11 real nursing vacancies; missing is 'Pflegefachkraft (w/m/d) für unsere Pflegestationen' (page 2), which is absent from the DB entirely. Our 47401 rows last_seen 2026-09-11, and we already hold other page-2 items, so pagination is not the blocker — the posting was added after the last ingest. Separately 'Pflegefachhelfer (w/m/d) für unsere Pflegestationen' is on the board but is dropped on purpose (role_class 'pflegehelfer' is in EXCLUDED_ROLE_CLASSES, pflege_jobs/patterns.json:344).
- fix: Re-crawl 47401; if the miss persists after a fresh run, check that crawl_wp_jobs is walking /unser-klinikum/stellenangebote/page/2/ and /page/3/ rather than only page 1.

### 18902 Kreisklinik Trostberg (223 beds) — **complete**
- ours 3 / adapter -1 / live 3 (confidence: high)
- root cause: jobs.kliniken-suedostbayern.de carries 59 postings across the Kliniken Südostbayern group; exactly 3 are Trostberg-located nursing (OTA/Pflegefachkraft Operationsdienst, Pflegefachkraft Akutgeriatrie, Pflegefachkraft Innere Medizin) and we hold all 3. One extra multi-site role ('Freigestellte Praxisanleitung, Zentrale Praxisanleitung', listed for Traunstein/Trostberg/Bad Reichenhall/Berchtesgaden) is attributed to Traunstein, which is defensible.

### 18601 Ilmtalklinik Pfaffenhofen (220 beds) — **complete**
- ours 9 / adapter 29 / live 10 (confidence: high)
- root cause: The live b-ite board (customer 'ilmtalklinik', listing 'main-listing') has 29 postings, 10 of them nursing; we hold 9. The single miss is 'Pflegefachhelfer (m/w/d) für die Innere Medizin', which classify_role puts in role_class 'pflegehelfer' — an intentional product exclusion (pflege_jobs/patterns.json:344, asserted in tests/test_patterns.py:25). By our own intake policy this is a correct result, not a gap. Note the batch file lists careers_url as null, but the registry actually holds https://www.ilmtalkliniken.de/bei-uns-arbeiten/stellenangebote.php with ats_type=bite.
- fix: None for coverage. Optional: fix the stale null careers_url in whatever produced /tmp/top100_batch9.json.

### 19002 Krankenhaus Weilheim (220 beds) — **complete**
- ours 2 / adapter -1 / live 2 (confidence: medium)
- root cause: meinkrankenhaus2030.de/karriere/stellenboerse lists 17 postings total (verified non-paginated: ?page=2 returns the identical 17 links, and /pflege/jobs-und-ausbildung carries no separate listing). Only 2 are nursing — 'Gesundheits- und Krankenpfleger / OTA (w/m/d)' and 'Operations-Technischer-Assistent / OP-Pflegefachkräfte (w/m/d)' — and we hold both. Confidence is medium only on the site split: both our rows carry city='Schongau' while the board is shared with 19001 Krankenhaus Schongau (40 beds, 0 postings), so whether these two vacancies physically sit in Weilheim or Schongau is unverified.
- fix: Confirm from the two job detail pages which site each vacancy belongs to; if they are Schongau, move them to 19001 and Weilheim becomes empty_ok.

### 47501 Klinik Münchberg (220 beds) — **complete**
- ours 4 / adapter -1 / live 2 (confidence: high)
- root cause: jobs.kliniken-hochfranken.de/jobs.feed.json reports numberOfItems=13 with exactly 2 nursing vacancies (job ids 31746595 and 55401732); we hold 4 rows, which are those same 2 jobs stored twice under different hosts — the vanity host (first_seen 2026-09-05, last_seen 2026-09-18) and kliniken-hochfranken.softgarden.io (first_seen AND last_seen 2026-09-06). pflege_jobs/sources/softgarden.py:69 builds feed_hosts as [own_host, host], so a run that resolved the .io host created a second source_ref per job; nothing then closed those rows — they are still status='open' 15 days after their last observation.
- fix: Two separate fixes: (1) dedupe softgarden postings on the numeric job id rather than the full URL, since the vanity host and *.softgarden.io serve the same id; (2) add a close-out rule that flips a posting to closed when it is no longer observed on a board that WAS successfully fetched — the .io duplicates are only 'open' because they were never re-seen.
- discovered careers_url: https://jobs.kliniken-hochfranken.de/jobs.feed.json

### 67702 Klinikum Main-Spessart Lohr (220 beds) — **complete**
- ours 12 / adapter 12 / live 11 (confidence: high)
- root cause: Coverage is exact — all 11 real nursing postings on www.klinikum-msp.de/karriere/stellenboerse are captured (10 in the Pflegedienst area + Pflegepädagoge; Pflegefachhelfer w/m/d is dropped on purpose, pflegehelfer is in patterns.json excluded_role_classes). The 12th row is a false positive: 'Fußpflege - Klinikum Main-Spessart' at /patienten-besucher/glossar/detail/fusspflege, a medical glossary entry, admitted because JOB_PATH (crawlers/vendor_adapters.py:466) contains the alternative '/(karriere-)?detail/[^/?#]' which matches any /detail/ path on the host, and NOT_JOB_PATH does not exclude /glossar/.
- fix: Tighten the '/detail/' alternative in JOB_PATH to require a job-ish parent segment (e.g. stellen|jobs|karriere before /detail/), or add /glossar/ to NOT_JOB_PATH. Verified: JOB_PATH.search('.../glossar/detail/fusspflege') is True today.

### 56407 Klinik Dr. Erler, Nürnberg (220 beds) — **complete**
- ours 10 / adapter 10 / live 10 (confidence: high)
- root cause: Board has 25 postings; 10 are open nursing vacancies by our own definition and all 10 are in the DB, title for title, and the live adapter returns the same 10 (28 raw rows, 10 kept). The three Pflege-shaped postings we do NOT hold are excluded on purpose: 'Pflegefachhelfer (m/w/d)' (role_class pflegehelfer), 'Ausbildung als Pflegefachkraft 2027' (ausbildung), 'FSJ im Bereich Pflege' — pflegehelfer/ausbildung are both in pflege_jobs/patterns.json excluded_role_classes.

### 18101 Klinikum Landsberg am Lech (218 beds) — **complete**
- ours 6 / adapter 6 / live 6 (confidence: high)
- root cause: The b-ite API (POST https://jobs.b-ite.com/api/v1/postings/search, customer klinikum-landsberg-lech, key from cs-assets.b-ite.com/.../main-listing.min.js) reports total=22, of which exactly 6 are nursing after removing the 7 Ausbildung entries and the non-clinical roles. Our 6 rows are those 6, and the live adapter returns the same 22/6.

### 17704 kbo-Isar-Amper-Klinikum Taufkirchen (Vils) (212 beds) — **misattributed**
- ours 0 / adapter 108 / live 1 (confidence: high)
- root cause: The one real Taufkirchen (Vils) nursing vacancy on the kbo group board (https://kbo.de/karriere/jobs/704-pflegefachfrau-pflegefachmann-... , jobSite 'kbo-Klinik für Forensische Psychiatrie und Psychotherapie | Taufkirchen (Vils)') IS in our DB but stored with clinic_id=16264 and city='München'; the registered seed host kbo-iak.de is walked by crawl_wp_jobs which reaches kbo.de and returns all 108 group postings (33 nursing) with no per-posting jobSite attribution, so every kbo row lands on whichever kbo clinic_id the run seeded (16264/18104/17308) or on NULL (14 rows).
- fix: Read the jobSite/Location facet on each kbo.de job detail page and map it to the clinic_id instead of inheriting the seed clinic; kbo.de exposes it as a Solr facet (/karriere/jobboerse?tx_solr[filter][0]=jobSite:<name>), so attribution is a per-row lookup, not a guess. Separately: registry says ats_type=umantis but the live adapter runs typo3_jobs.
- discovered careers_url: https://kbo.de/karriere/jobs

### 57705 Klinikum Altmühlfranken Gunzenhausen (210 beds) — **gap**
- ours 0 / adapter 6 / live 13 (confidence: medium)
- root cause: crawlers/vendor_adapters.py:550 logs '[wp_jobs] find_job_urls: no job links in sitemap for https://karriere.klinikum-altmuehlfranken.de (9 sitemap urls seen)' — the site's sitemap_index.xml lists only page-sitemap.xml, so the 'stellenangebote' custom post type is invisible to find_job_urls and crawl_wp_jobs falls back to the 6 links rendered on the careers homepage (3 nursing). The full board is 53 postings (~13 real nursing) and is public at /wp-json/wp/v2/stellenangebote?per_page=100 (X-WP-Total: 53). Of those 13, we hold 3 in total and all 3 sit on sibling clinic_id 57701 (Weißenburg); 57705 holds zero.
- fix: For WordPress boards whose CPT is missing from the sitemap, fall back to /wp-json/wp/v2/<cpt>?per_page=100 (discoverable from /wp-json/wp/v2/types). That single fallback turns 6 rows into 53 here.

### 76203 Bezirkskrankenhaus Kaufbeuren (202 beds) — **partial**
- ours 3 / adapter 0 / live 3 (confidence: high)
- root cause: Adapter returns 0 rows: registry ats_type is an empty string and careers_url points at the CMS page www.bezirkskliniken-schwaben.de/ausbildung-karriere/stellenangebote-bewerbung, while the real board is jobs.bezirkskliniken-schwaben.de/Jobs — 57 postings in an inline Mustache view-model JSON ({"RegionsViewModel":...,"TotalJobsCount":57,"Jobs":[{"Id","Title","SubTitle","Location"}]}) readable by plain curl, no render needed. Consequence: our 3 rows are frozen Firecrawl leftovers — Job/262246 'Pflegefachkräfte für die neue Station ZP02' is no longer in the 57 and is still status=open, and live Kaufbeuren nursing Job/267790 'Gerontopsychiatrische Fachkraft (m/w/d)' is missing. Real current Kaufbeuren nursing: 267790, 271191, 244465.
- fix: Point careers_url at https://jobs.bezirkskliniken-schwaben.de/Jobs and parse the inline JobList model JSON, filtering Location=='Kaufbeuren' for this clinic_id (the same board serves Günzburg/Augsburg/Memmingen/Kempten/Donauwörth siblings).
- discovered careers_url: https://jobs.bezirkskliniken-schwaben.de/Jobs

### 27501 Kreiskrankenhaus Rotthalmünster (200 beds) — **partial**
- ours 6 / adapter 2 / live 8 (confidence: high)
- root cause: find_job_urls probes only /sitemap.xml, /wp-sitemap.xml and /sitemap_index.xml (crawlers/vendor_adapters.py:522) plus robots.txt; karriere.ge-passau.de has no robots.txt (404) and serves /sitemap-index.xml with a HYPHEN, so 0 sitemap URLs are seen and the Astro SPA index page (/stellen/ renders no job anchors even after a Playwright render) yields only 2 rows, 0 nursing. The 27 job URLs are all in https://karriere.ge-passau.de/sitemap-index.xml -> sitemap-0.xml. Missing at Standort Rotthalmünster: /stellen/operationstechnischer-assistent-mwd-1/ and /stellen/pflegep%C3%A4dagoge--lehrkraft-mwd/. The 6 rows we hold are stale Firecrawl leftovers and will expire on the next reconcile.
- fix: Add '/sitemap-index.xml' to the candidate list at crawlers/vendor_adapters.py:522 (one string). Also set the registry careers_url to https://karriere.ge-passau.de/stellen/ — the batch export had it as null.
- discovered careers_url: https://karriere.ge-passau.de/stellen/

### 27301 Caritas-Krankenhaus St. Lukas (200 beds) — **complete**
- ours 9 / adapter 7 / live 7 (confidence: high)
- root cause: All 7 real nursing vacancies on https://www.mein-check-in.de/csl-kelheim/ are captured and the live adapter also returns exactly 7. The extra 2 open rows are category listing pages stored as postings: /csl-kelheim/x/pflege/4773 ('Pflegedienst und pflegerische Funktionsbereiche (9)') and /csl-kelheim/pflege ('Pflegedienst & pflegerische Funktionsbereiche 9') — stale Firecrawl rows, not produced by today's adapter.
- fix: Purge the two /pflege category rows; optionally reject mein-check-in URLs that are not /position-<digits> at ingest.

### 27306 Asklepios Klinikum Bad Abbach (200 beds) — **complete**
- ours 4 / adapter 1398 / live 4 (confidence: high)
- root cause: Bad Abbach has 12 live postings (POST https://www.asklepios.com/api/search with filter locationIds=locationIds_QmFkIEFiYmFjaA==), of which exactly 4 are nursing (req 27828 Aufnahmestation, 27829 ASV Rheuma, 27075 Notaufnahme, 25622 Anästhesie/Intensivmedizin) — we hold those 4 and nothing else. Latent risk only: raw_board_rows for this clinic pulls the entire 1398-job Asklepios group board (466 nursing after classify) because careers_url is the group page www.asklepios.com/karriere/jobs; downstream location filtering is what keeps the DB correct (19 asklepios rows total, split 18811/27306/17302/37607).
- fix: None needed for coverage. If you want the crawl cheap and the attribution explicit, seed the per-location filter: POST /api/search with filter.locationIds for the clinic's town.

### 16233 Sana Klinik München (200 beds) — **complete**
- ours 1 / adapter 73 / live 1 (confidence: high)
- root cause: Sana's Oracle Recruiting Cloud site CX_4025 holds 1122 reqs group-wide; 7 have PrimaryLocation 'München, Bayern, Deutschland' and exactly 1 is nursing (req 5660 'OTA / Operationstechnischer Assistent (m/w/d) oder OP-Pflegefachkraft (m/w/d)') — which is the single row we hold. The count is right but the adapter is not: it logs '[wp_jobs] find_job_urls: no job links in sitemap for https://www.sana.de (0 sitemap urls seen)' and returns 73 CMS marketing pages, 6 of which classify as nursing ('Pflege bei Sana – Starte deine Karriere…', 'Pflegeausbildung bei Sana', …). The correct row came from Firecrawl, not the adapter.
- fix: Point the adapter at the Oracle REST feed instead of the CMS: GET https://fa-eycl-saasfaeuraprod1.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&finder=findReqs;siteNumber=CX_4025,limit=200,offset=N (jobs.sana.de 302s these to a 404; the fa.ocs.oraclecloud.com host answers 200). Filter PrimaryLocation for the clinic town.
- discovered careers_url: https://jobs.sana.de/de/sites/CX_4025/

