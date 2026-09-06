# Registry

`clinics.csv` — every hospital site in the Bavarian *Krankenhausplan*, keyed by **KeZ** (5-digit
Kennziffer). This is the identity backbone: a posting counts as "at a hospital" when it resolves to a KeZ.

Current edition: **2026, 51. Fortschreibung** (`beds`, `fachrichtungen`, `versorgungsstufe`,
`traegerart`, `status` synced from it; see below).

## Regenerating

The source PDF (~3.6 MB) is not committed. Fetch it from the ministry, then parse:

```bash
curl -o krankenhausplan_2026.pdf \
  'https://www.stmgp.bayern.de/wp-content/uploads/2026/02/51.-Fortschreibung-Krankenhausplan-des-Freistaates-Bayern-Stand-01012026.pdf'
python -m pflege_jobs.sources.krankenhausplan krankenhausplan_2026.pdf out.csv
python data/sync_krankenhausplan_2026.py --dry-run
```

## Why the sync is partial, on purpose

The 51. Fortschreibung dropped the literal `Träger` line that used to separate the operator inside the
"Krankenhaus / Standort" cell, making the name/town split positional and ambiguous — a site label is then
indistinguishable from a town:

```
München Klinik        ← name
Schwabing             ← site label, or town?
München Klinik gGmbH  ← operator
```

Measured against the previous, trusted parse, the free-text split agreed on only **~28 % of names**, while
the structured columns agreed on **91–100 %**. So the sync takes numbers and enums from the new edition and
**keeps the established names/towns**. The four genuinely new sites were transcribed by hand
(`NEW_2026_OVERRIDES`). Sites that leave the plan are marked `status='nicht_mehr_im_plan'`, never deleted —
existing postings still reference them.

A proper fix is to parse with word coordinates rather than line order; until then, partial-but-verified
beats complete-but-wrong.

## Columns owned elsewhere

`ats_type` and `careers_url` are filled by ATS discovery, not by the PDF. Anything writing this table must
send **complete rows** (`registry.full_clinic_rows()`): the ingest upsert assigns every column it receives,
and an omitted key arrives as NULL — so a partial push silently erases data. This has happened twice.
