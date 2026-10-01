# Registry

The registry is the DB table `pflege_jobs.clinics` — every hospital site in the Bavarian *Krankenhausplan*,
keyed by **KeZ** (5-digit Kennziffer), plus the Reha/Vorsorge sites from RHV (`RH<n>`) and the Diakoneo
facilities (`DK<nn>`). It is the identity backbone: a posting counts as "at a hospital" when it resolves to a
clinic_id. There is no CSV copy of it any more (TASK-175); this directory holds its primary sources:

- `krankenhausplan_2026.pdf` — Krankenhausplan Bayern, 51. Fortschreibung (Stand 01.01.2026), not committed
- `krankenhausverzeichnis_24.xlsx` — RHV 2024, sheet `RHV_2024` (Reha/Vorsorge), parsed by `data/sync_rhv_reha.py`
- `diakoneo_social_bavaria.csv` — the hand-collected Diakoneo list (`data/sync_diakoneo_social.py`)

## Build and deviations

```bash
curl -o data/registry/krankenhausplan_2026.pdf \
  'https://www.stmgp.bayern.de/wp-content/uploads/2026/02/51.-Fortschreibung-Krankenhausplan-des-Freistaates-Bayern-Stand-01012026.pdf'
set -a; source .env; set +a
python tools/registry_build.py --report deviations.json --proposals proposals.json
```

Read-only. It parses the sources, compares every field they state with the DB and marks each difference
EXPLAINED (the latest `pflege_jobs.corrections` row for that clinic and field set exactly the DB value) or
UNEXPLAINED. The unexplained ones, source rows the DB lacks and plan sites the plan no longer lists
(→ `status='nicht_mehr_im_plan'`, never deleted: postings still reference them) become proposals for
`tools/apply_clinic_corrections.py`, which is the only way a clinics row changes and records each change with
its reason code.

## Why the 2026 names and towns are corrections

The 51. Fortschreibung dropped the literal `Träger` line that used to separate the operator inside the
"Krankenhaus / Standort" cell, making the name/town split positional and ambiguous — a site label is then
indistinguishable from a town:

```
München Klinik        ← name
Schwabing             ← site label, or town?
München Klinik gGmbH  ← operator
```

The registry keeps the names/towns/operators the 2025 edition gave wherever the 2026 cell still states them
word for word; each such field is explained by a `parse_error` correction (our parser misreads the cell, the DB
holds the source's text; backfill proposed in TASK-175). pdfplumber also clips words wider than the column (`Gesundheitseinrichtun`), whose tails land
in the Status cell. A proper fix is to parse with word coordinates rather than line order.

## Columns owned elsewhere

`ats_type` and `careers_url` are filled by ATS discovery, not by a source (`board_location` corrections).
Anything writing this table must send **complete rows** (`registry.full_clinic_rows()`): the ingest upsert
assigns every column it receives, and an omitted key arrives as NULL — so a partial push silently erases data.
