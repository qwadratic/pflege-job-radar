# Pflege-Stellen Bayern — how it works

An open dataset of **open nursing jobs at Bavarian hospitals**, rebuilt daily from public sources and
published as a read-only REST API plus a dashboard.

The hard part is not fetching job ads. It is answering *"how many nursing jobs are open at Bavarian
hospitals?"* with a number you can defend — which means knowing what a hospital **is**, noticing when
two sources describe the same job, and refusing to count things that aren't nursing.

---

## 1. The shape of the problem

| Question | Why it is not trivial |
|---|---|
| What is a hospital? | "Klinikum Musterstadt GmbH" on a job board may be a hospital, a nursing home, or a staffing agency reselling the same vacancy. |
| Which hospital? | Operators run many sites. "München Klinik" is five hospitals; a posting says only "München". |
| Is this the same job? | The same vacancy appears on the labour agency, on Indeed, and on the hospital's own careers page, with three different titles. |
| Is it still open? | Job boards keep dead ads for weeks. |
| Is it nursing? | "Pflege" also matches nursing-home care, paramedics, and medical assistants. |

Everything below exists to answer one of those five.

---

## 2. Ground truth: the state hospital plan

Bavaria publishes the **Krankenhausplan** — the official register of every planned hospital site, as a
~300-page PDF. Each site has a **KeZ** (Kennziffer), a stable 5-digit identifier, plus beds, departments,
operator, and level of care.

That register is the backbone: a posting is "at a hospital" when we can tie it to a **KeZ**, not when its
employer name merely looks hospital-ish.

```
data/registry/krankenhausplan_2026.pdf   →  pflege_jobs/sources/krankenhausplan.py  →  clinics (407 rows)
```

**Parsing caveat, worth knowing.** The 2026 edition (51. Fortschreibung) dropped the literal `Träger`
line that used to separate the operator inside the "Krankenhaus / Standort" cell. The block became
positional, and a site suffix is then indistinguishable from a town:

```
München Klinik        ← name
Schwabing             ← site label, or town?
München Klinik gGmbH  ← operator
```

Measured against the previous edition, the free-text split agreed on only **~28 % of names** — while the
*structured* columns agreed on **91–100 %**. So the sync takes beds/departments/level-of-care/ownership
from the new edition and **keeps the proven names**, rather than trusting a heuristic across the board.
Four genuinely new sites were transcribed by hand. Sites that vanish are marked
`status='nicht_mehr_im_plan'`, never deleted — postings still point at them.

> Lesson that generalises: when a new input format degrades one field and not others, sync the fields
> you can verify and quarantine the rest. Don't let one bad column overwrite a good table.

---

## 3. Sources, and why there are four

```
 precedence  source            what it is                              why we keep it
 ─────────── ───────────────── ─────────────────────────────────────── ─────────────────────────────
 1           krankenhausplan   official site register (KeZ, beds)      identity, not postings
 2           employer_ats      hospital career sites / ATS vendors     authoritative, richest text
 3           arbeitsagentur    federal labour agency Jobsuche API      broadest, structured, dated
 4           aggregator        Indeed, StepStone                       catches what the others miss
```

Lower number wins when sources disagree. A hospital's own careers page beats a job board about salary
and description; the labour agency is trusted for publication dates and contract type.

**Why not just crawl career sites?** Because they don't cover the field. Measured on the live dataset:
career sites reach ~1,470 of ~2,250 open hospital postings. The rest exist only on the labour agency or
an aggregator — mostly at hospitals whose ATS we haven't identified yet. Aggregators stay until their
*unique* contribution falls below ~5 %, which is a measurement, not an opinion.

### ATS vendors

Hospital careers pages are mostly a handful of vendors wearing different CSS. Identifying the vendor turns
"scrape a website" into "call a known endpoint":

`softgarden` · `personio` · `d.vinci` · `concludis` · `umantis` · `smartrecruiters` · `b-ite` ·
`typo3_jobs` · `helix` · `talention` · `oracle` · `pi_asp`

Two findings that saved a lot of work:

- **umantis and softgarden need no browser.** They server-render `/Jobs/1` and expose `jobs.feed.json`.
  Plain HTTP is faster and far more reliable than Playwright.
- **Group portals are one board, many hospitals.** The kbo group's nine sites share a single job board
  → one fetch yields 112 postings for nine hospitals, instead of nine crawls returning nothing.

---

## 4. Pipeline

```
   crawlers/*.py ─┐
                  ├─→  inbox  ─→  classify  ─→  resolve  ─→  postings + posting_observations
   sources/*.py  ─┘    (raw)      (role,        (identity)        (merged, deduplicated)
                                   employer)
                                       │                                │
                                       ▼                                ▼
                                  drop non-nursing              link to KeZ  →  clinics
```

**inbox** — every crawler writes the same row shape, whatever the site:

```json
{"kind":"jobposting","source_host":"…","source_url":"…",
 "payload":{"title":"…","org":"…","loc":["…"],"description":"…"},
 "collector":"…","client_id":"…"}
```

One schema means one loader, and a new crawler needs zero pipeline changes.

**classify** — title + labour-agency occupation → one of 12 `role_class` values, and employer → hospital /
unclear / not-a-hospital. The rule that fired is stored (`role_rule`, `class_rule`), so a wrong answer is
debuggable instead of mysterious.

**resolve** — decides whether two observations are the same job. Same source: URL variants collapse.
Across sources: employer + city + normalised title. A merged posting keeps every observation, so
`n_observations > 1` means two independent sources saw it.

**link to KeZ** — six ordered rules, from exact name match down to operator+town. When an operator has
several sites in one town the rule records the ambiguity (`R6_ambiguous_sites:16201,16202,…`) rather than
silently picking one. ~2,200 of 2,600 open hospital postings carry a KeZ.

**verify** — fetches each posting's URL and checks it still resolves: `live`, `gone` (→ expired),
`blocked` (bot wall — a human can still open it), `error`. The dashboard defaults to `live`, so the
headline number means "we checked".

---

## 5. Scope: only real nursing roles, only experienced

The dataset deliberately **excludes** trainees, working students, interns, and non-nursing roles
(paramedics, medical assistants, physicians, logistics). These are not filtered at display time — they
are refused at ingest and were deleted from the database:

```python
EXCLUDED_ROLE_CLASSES = {"nicht_pflege", "ausbildung", "werkstudent_praktikum"}
```

`pflegehelfer` (nursing assistant) is **kept**: it is a qualified occupation, and it is what many
internationally-trained nurses work as while their German recognition is pending.

---

## 6. Storage

Postgres (Supabase), schema `pflege_jobs`:

| table | holds |
|---|---|
| `clinics` | the hospital register, keyed by KeZ |
| `employers` | employer identity + classification |
| `postings` | one row per *real-world vacancy* (merged) |
| `posting_observations` | one row per *sighting* — the audit trail |
| `inbox` | raw crawler output, before interpretation |
| `sources` | the four sources and their precedence |

The `postings` / `posting_observations` split is the important one: `postings` is the answer,
`posting_observations` is the evidence. Precedence is applied when building the former, and nothing is
thrown away, so a merge can always be explained — or undone.

Reads go through `v_postings`, which joins employer, role label and hospital register onto each posting.

---

## 7. Serving

The dashboard is a **single static HTML file** with no build step beyond config injection — no framework,
no bundler, no server. It fetches ~7,700 rows once, then filters, sorts, charts and maps entirely in the
browser. Filters live in the URL, so any view is shareable.

```
systemd: pflege-web.service → busybox httpd -p 8501 -h web/
```

**Two performance lessons** are baked into the loader:

1. `v_postings` computes `source_url` and `source_codes` with **correlated subqueries**. Asking PostgREST
   for `count=exact` on every page made it re-run those over the whole table each time — enough to blow the
   anon role's statement timeout and 500 the dashboard. The count now comes from the base table.
2. `source_url` is only needed in the detail dialog and the CSV export, so it is **resolved lazily** for
   the rows that actually need it, instead of for all 7,700 up front.

---

## 8. Two bugs worth remembering

**The upsert that erased a column.** The ingest function builds its recordset from a fixed column list and
assigns every column. A payload that *omits* a key therefore sends `NULL` — identical to sending a blank.
So "just send the columns I changed" silently wiped ATS labels; later, a partial push wiped beds, towns
and departments for all 407 hospitals. The fix is `full_clinic_rows()`: always send the complete row,
merging in what's already stored. Tests now assert both failure modes.

> If an API's write path can't distinguish *absent* from *empty*, never send partial rows.

**The stable-ID trap.** Indeed's anchor href is a rotating `/pagead/clk` redirect — using it as the
posting's identity would have created duplicates on every crawl. The stable key is the `data-jk`
attribute, so the source reference is built as `viewjob?jk=<data-jk>`.

---

## 9. Layout

```
pflege_jobs/         pipeline: classify, resolve, verify, CLI
  sources/           one module per input (arbeitsagentur, krankenhausplan, ATS vendors…)
crawlers/            standalone crawlers writing the inbox row shape
sql/                 schema + migrations
edge/                Supabase edge functions (ingest)
web/                 dashboard (single file), skill docs, llms.txt
skill/               agent-facing docs, Claude skill format
data/registry/       the hospital register (CSV + source PDF)
tests/               53 tests, no network
```

---

## 10. For agents

Machine-readable docs: [`/skill/SKILL.md`](/skill/SKILL.md) · single-file bundle
[`/skill/pflege-jobs.skill.md`](/skill/pflege-jobs.skill.md) · [`/llms.txt`](/llms.txt).

```bash
curl 'https://klkxfvieaxpjlplloljn.supabase.co/rest/v1/v_postings?select=title,employer,city,source_url&employer_class=eq.clinic&status=eq.open&limit=5' \
  -H "apikey: $ANON" -H 'Accept-Profile: pflege_jobs'
```

`Accept-Profile: pflege_jobs` is required. Max 1000 rows per call. Read-only.
