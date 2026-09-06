# pflege-jobs — open nursing jobs at Bavarian hospitals

A pipeline and open dataset of **currently open nursing vacancies at hospitals in Bavaria**, rebuilt from
public sources, reconciled against the official state hospital register, and published as a read-only
REST API plus a static dashboard.

**Live:** <https://pflege-board.exe.xyz> · **How it works:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
· **For agents:** [`skill/SKILL.md`](skill/SKILL.md)

```bash
curl 'https://klkxfvieaxpjlplloljn.supabase.co/rest/v1/v_postings?select=title,employer,city,source_url&employer_class=eq.clinic&status=eq.open&limit=5' \
  -H "apikey: $SUPABASE_ANON_KEY" -H 'Accept-Profile: pflege_jobs'
```

## Why it exists

Counting open nursing jobs sounds easy and isn't. The same vacancy appears on the federal labour agency,
on Indeed, and on the hospital's own careers page under three different titles. "Klinikum X GmbH" might be
a hospital, a nursing home, or an agency reselling the ad. Operators run many sites, and a posting says only
"München". Job boards keep dead ads for weeks.

So the dataset is built around **identity and evidence**, not scraping volume:

- every hospital is a **KeZ** from the Bavarian *Krankenhausplan* (2026, 51. Fortschreibung) — 407 sites;
- every posting keeps each **observation** that produced it, so a merge can be explained or undone;
- sources have an explicit **precedence** (career site > labour agency > aggregator) applied field by field;
- every posting is **re-fetched** to confirm it still exists (`verify_status = live`).

Scope is deliberate: **experienced nursing roles only.** Trainees, working students, interns and
non-nursing roles are refused at ingest and were deleted from the database. `pflegehelfer` is kept — it is
a qualified occupation and the usual role for internationally-trained nurses awaiting recognition.

## Layout

```
pflege_jobs/        pipeline: classify, resolve, verify, CLI
  sources/          one module per input (arbeitsagentur, krankenhausplan, ATS vendors, …)
crawlers/           standalone crawlers, all writing the same inbox row shape
sql/                schema + migrations
edge/               Supabase edge functions (ingest)
web/                dashboard (single static file), docs page, agent skill, llms.txt
skill/              agent-facing documentation (Claude skill format)
docs/               ARCHITECTURE.md — the technical explainer, rendered to /docs.html
data/registry/      the hospital register (CSV, derived from the official PDF)
tests/              53 tests, no network required
```

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in keys
python -m pflege_jobs.cli inbox         # interpret raw crawler rows
python -m pflege_jobs.cli link-clinics  # attach postings to hospital sites (KeZ)
python -m pflege_jobs.cli link-cross    # merge the same job seen in several sources
python -m pflege_jobs.cli verify        # confirm postings are still live
python web/build.py                     # rebuild dashboard + docs page
pytest -q tests
```

## Data model in one paragraph

`inbox` holds raw crawler output. `postings` is one row per real-world vacancy;
`posting_observations` is one row per sighting of it — the audit trail. `employers` carries employer
identity and its classification (`clinic` / `unknown` / `non_clinic`; **unknown means unclassified, not
"not a hospital"**). `clinics` is the hospital register keyed by KeZ. Reads go through the `v_postings`
view. Full field reference: [`skill/data-model.md`](web/skill/data-model.md).

## Sources

| precedence | source | role |
|---|---|---|
| 1 | Krankenhausplan Bayern (StMGP) | hospital identity: KeZ, beds, departments, operator |
| 2 | hospital career sites / ATS vendors | authoritative text, richest detail |
| 3 | Bundesagentur für Arbeit Jobsuche API | broadest coverage, reliable dates |
| 4 | aggregators (Indeed, StepStone) | catches what the others miss |

Postings are public job advertisements. No personal data is collected.

## Licence

Code: MIT (see `LICENSE`). The underlying job advertisements belong to their respective employers; the
`source_url` on every row links to the original.
