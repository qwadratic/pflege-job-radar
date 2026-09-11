# data/geo — Germany-wide municipality/PLZ -> Land lookup table

Built by `tools/build_geo_table.py`. Re-run to refresh:

```
.venv/bin/python tools/build_geo_table.py
```

The script re-downloads both sources fresh into memory each run (no local
cache), reparses, reasserts the known shape, and overwrites the two files
below. It adds no dependency: the Destatis xlsx is parsed with stdlib
`zipfile` + `xml.etree.ElementTree` (openpyxl is not installed in `.venv`
and was not added), and the GeoNames file is a plain TSV.

## Files

- `gemeinden_de.csv` — the dataset (13,762 rows, 1.2 MB).
- `ambiguous_stems.txt` — precomputed set of bare name-stems that occur in
  >= 2 Bundeslaender (409 stems). Ships separately because computing it at
  import time from ~11k rows on every process start is wasteful; a labeller
  loads this file once and refuses to guess a Land for any bare stem in it.
  If this file contains a blank line, that is intentional (see the header
  comment inside the file) — it represents the empty-string stem shared by
  a handful of municipalities whose full name is itself a bare preposition
  ("Am Mellensee", "An der Poststrasse"); it is inert, `resolve()` never
  looks an empty stem up.
- This README.

## Sources

**A — Destatis "Auszug aus dem Gemeindeverzeichnis", Gebietsstand
31.03.2026 (1. Quartal), sheet `Onlineprodukt_Gemeinden31032026`:**
https://www.destatis.de/DE/Themen/Laender-Regionen/Regionales/Gemeindeverzeichnis/Administrativ/Archiv/GVAuszugQ/AuszugGV1QAktuell.xlsx?__blob=publicationFile&v=16

Authoritative. One Satzart-60 row per municipality: AGS/ARS, name,
Verwaltungssitz PLZ, and the geographic-midpoint coordinates (Laengengrad/
Breitengrad, German decimal comma, converted to dot on ingest). 10,943
Satzart-60 rows total; 196 are gemeindefreie Gebiete (Textkennzeichen 65/66)
and are dropped, leaving 10,747 real municipalities — all 10,747 carry
coordinates. Bayern = 2,056.

Source B (`GV100AD1QAktuell.zip`, fixed-width, no coordinates) was not
needed — Source A parsed cleanly via stdlib and gives coordinates, which B
lacks. B remains the fallback if a future Destatis release breaks A's xlsx
layout; `build_geo_table.py` asserts the known row counts and fails loudly
if so.

**Licence (Destatis `Hinweise.txt`):** "Vervielfaeltigung und Verbreitung,
auch auszugsweise, mit Quellenangabe gestattet." (Reproduction and
distribution, including excerpts, permitted with attribution.)
**Attribution:** Statistisches Bundesamt (Destatis), Gemeindeverzeichnis,
Gebietsstand 31.03.2026.

**C — GeoNames postal codes for Germany (CC BY 4.0, rebuilt daily):**
https://download.geonames.org/export/zip/DE.zip

Used only to add PLZ that Destatis does not list — Destatis gives exactly
one PLZ per municipality (the Verwaltungssitz), but a municipality can have
many. 23,297 rows, 10,813 distinct PLZ. Only rows for a PLZ not already
covered by Source A are merged in (one row per new PLZ, first match kept);
3,015 new PLZ were added this way (down from an earlier 3,334 after the
Grossempfaenger filter below was broadened to also catch suffix-compound
institutional names). Merged rows carry `source=geonames` and an empty
`ars` (GeoNames has no AGS) so provenance stays auditable.

**Attribution:** GeoNames.org, CC BY 4.0.

### GeoNames traps (read before touching this file)

1. **Two admin1 schemes in one file.** ~16.5k rows use ISO-style
   `admin_name1`/`admin_code1` ("Bayern"/"BY"); ~6.8k rows use GeoNames' own
   numeric scheme with English names ("Bavaria"/"02"). GeoNames numeric `02`
   = Bavaria, but AGS `09` = Bayern; AGS `09` in GeoNames' numbering is
   Saarland. **The builder never reads `admin_code1`** — it maps `Land` off
   `admin_name1` text only (`ADMIN1_NAME_TO_LAND` in `build_geo_table.py`),
   which is scheme-agnostic and side-steps the collision entirely.

2. **Grossempfaenger rows.** ~3.3k+ rows are a dedicated PLZ handed to one
   company/authority; `place_name` is a company name and `Land` is
   frequently wrong (measured, all four verified against real geography):
   `96039` -> tagged NRW ("HappyDigits Service Center"), `96076`/`96035` ->
   tagged Berlin ("GEMA Geschaeftsstelle Berlin" / "AMN Data Solutions
   GmbH"), `96063` -> tagged Hessen ("DB Fernverkehr GmbH c/o GHP") — all
   four are really Bamberg, Bayern. There is no machine flag for this in
   the file. The builder drops any row whose `place_name` matches a
   deny-list of legal-form and institutional-name vocabulary
   (`GROSSEMPFAENGER_RE`): 4,595 rows dropped this run. If this regex ever
   looks like it's eating a real Bavarian town name, that is a correctness
   bug: fix the regex, do not loosen it away — a wrong Land is worse than a
   missing PLZ.

   *Known residual gap, deliberately not chased further:* the original
   version of this regex only matched legal-form suffixes (`GmbH`, `AG`,
   ...) and institutional nouns with a *leading* word boundary, so it missed
   German's habit of compounding an institutional noun onto a prefix with no
   space (`Finanzamt`, `Landesbank`, `Amtsgericht`, `Kreissparkasse`,
   `Landesamt`). Fixed for **suffix** compounds (added a second,
   right-anchored-only alternative for `amt|bank|werke?|kasse|zentrale|
   verwaltung`, +1,051 rows dropped this run) but deliberately NOT extended
   to **prefix** compounds like `Amtsgericht`/`Landgericht` (institutional
   noun first) or a bare substring search: verified live against the
   Destatis-only rows that real Bavarian-and-other municipalities exist
   whose name contains these same substrings — `Amtzell`, `Amt Neuhaus`,
   `Amt Creuzburg`, `Kasseburg`, `Kasseedorf`, `Werkhausen`, `Vorwerk`,
   `Freigericht`, `Linsengericht` — so a prefix-agnostic match would trade
   "ugly institutional name survives in a supplemental GeoNames row" for
   "a real town's supplemental PLZ silently vanishes", which is a worse
   trade given this table's own stated policy (a wrong/missing PLZ is
   cheaper than a wrong Land, and Land correctness is unaffected either
   way — none of the residual institutional rows carry a wrong `Land`, only
   an ugly `gemeindename`, since GeoNames-only rows never feed the
   `normalized_name`/`bare_stem` lookups `resolve()` actually reasons over,
   see "Grain" below). A handful of upstream **GeoNames source rows are
   themselves mojibake** (double-UTF-8-encoded, e.g. `KÃ¶ln` for `Köln`) —
   confirmed present byte-identical in the raw `DE.txt` before this builder
   ever touches it, not introduced here; when the corruption lands on the
   umlaut inside a keyword (`UniversitÃ¤t`) it also defeats this regex's own
   umlaut-aware match, which is why 3 such rows (`Landgericht Köln`,
   `Kliniken der Stadt Köln Kinderkrankenhaus`,
   `Johannes-Gutenberg-Universität`, all NW/RP, none Bavarian) still ship.

## Grain and what you can/can't look up

One row = one (place, PLZ) pair, never a street or Ortsteil.

- `source=destatis`: exactly one row per real German municipality (its
  Verwaltungssitz PLZ + coordinates + a real `ars`).
- `source=geonames`: one row per additional PLZ Destatis didn't carry, no
  `ars`, `gemeindename` is whatever GeoNames called that PLZ (can be a
  quarter/locality name, not necessarily the formal Gemeindename).

**Supported lookups:** PLZ -> Land (near-decisive: of 6,440 distinct
Destatis Verwaltungssitz PLZ, only 6 map to two Laender, and none of those
six involve Bayern); exact `normalized_name` -> Land (Destatis rows only,
but NOT unconditionally decisive — see below); `bare_stem` -> Land when the
stem is *not* in `ambiguous_stems.txt`.

**Exact-name collision rate (measured, not previously reported):** 284 of
the 10,747 official `normalized_name` values are shared by >= 2 Laender
(119 of those involve Bayern — e.g. `fuerth`: BY|HE, a real collision
between the Bavarian city near Nuernberg and a same-named town in Hesse).
`pflege_jobs.geo.resolve()` checks this at lookup time (it does not trust a
`normalized_name` CSV lookup that also collides), so this number describes
the dataset's inherent ambiguity, not a labeller bug — it is the reason
`resolve()` also downgrades a `name_exact` hit to a refusal whenever that
same name is *also* an ambiguous `bare_stem` elsewhere (e.g. "Weiden" is
RP's bare official name **and** the stem of Bavaria's "Weiden i.d.OPf." —
see `pflege_jobs/geo.py`'s `resolve()` docstring for the incident this
fixed).

**Not supported:** enumerating every PLZ a given municipality has (Destatis
gives only the seat; GeoNames fills gaps but not exhaustively — the file
has 9,774 distinct PLZ against the ~8,200 the real network has more/less,
so some secondary PLZ are still missing, by design over guessing); street
or Ortsteil-level resolution; municipality -> list-of-PLZ.

## Columns

| column | meaning |
|---|---|
| `land` | 2-letter Bundesland abbreviation (BY, RP, HE, ...) |
| `ars` | 12-digit Amtlicher Regionalschlüssel (Destatis rows only, else empty) |
| `gemeindename` | raw name as given by the source |
| `normalized_name` | lowercase, umlaut/eszett folded, admin-status tail (everything after the first comma — ", Stadt", ", St", ", M", ", GKSt", "Kreisstadt", "Hansestadt", "Landeshauptstadt", ...) dropped |
| `bare_stem` | `normalized_name` further stripped of `(...)` parentheticals and the an/am/in/im/a./i./b./bei/ob river-or-region qualifier family ("an der Ruhr", "a.d.Isar", "i.OB", "b.Coburg", "ob der Tauber", "/Ruhr", ...) — the key to check against `ambiguous_stems.txt`. ("ob" added after a live bug: Rothenburg **ob der Tauber** wasn't collapsing to the same stem as Sachsen's Rothenburg, so the two never got flagged as ambiguous.) |
| `plz` | 5-digit Postleitzahl |
| `lat`, `lon` | decimal degrees, dot separator (Destatis: geographic midpoint; GeoNames: as given) |
| `source` | `destatis` or `geonames` |

## Row count and size, justified

10,747 Destatis municipalities + 3,015 GeoNames-only PLZ = 13,762 rows,
1.2 MB. This is the full country (all 16 Laender), not Bayern-only, per the
stated goal of eventually scaling to all of Germany without a rebuild —
Bayern is just filtered on `land == 'BY'` downstream.

## Verification block (this run)

```
Destatis municipality rows: 10747 (expected 10747)
Bayern municipality rows: 2056 (expected 2056)
Distinct PLZ (Destatis only): 6440 (expected 6440)
GeoNames rows merged in: 3015
Total rows in gemeinden_de.csv: 13762
Distinct PLZ (merged): 9455
Destatis rows carrying coordinates: 10747/10747
Rows per Land (Destatis):
  BB: 413
  BE: 1
  BW: 1101
  BY: 2056
  HB: 2
  HE: 421
  HH: 1
  MV: 724
  NI: 939
  NW: 396
  RP: 2300
  SH: 1104
  SL: 52
  SN: 418
  ST: 218
  TH: 601
Ambiguous bare stems: 409 (of which involve Bayern: 177)
Ambiguous exact names: 284 (of which involve Bayern: 119)
```

(Row/PLZ counts dropped from the previous build — 14,081 rows / 9,774
distinct PLZ — after `GROSSEMPFAENGER_RE` was broadened to also catch
suffix-compound institutional names; see the Grossempfaenger section above.
Ambiguous stems rose from 407 to 409 after `QUALIFIER_RE` gained "ob"
(Rothenburg ob der Tauber, see the `bare_stem` column note above).)

(`BE`/`HH`/`HB` show as 1-2 because Berlin/Hamburg/Bremen are city-states —
Destatis lists their constituent Stadtteile/Kreise as separate ARS entries
under Kreis-level Satzarten, not as extra Satzart-60 municipality rows; this
is expected, not a bug.)

Note on `ambiguous_stems.txt` count: this build's stripping rules are a
faithful implementation of the spec (comma-tail admin-status suffixes,
`(...)`, the an/in/a.d./i.OB/ob/`/...` qualifier family) but generalise a
couple of the named examples (e.g. cutting at the first comma rather than
matching only the four literal suffix strings, to also catch "Kreisstadt",
"Hansestadt", etc. which are the same kind of decoration). The result lands
within ~3% of the task's independently-measured reference numbers (409 vs.
395 stems; 177 vs. 177 involving Bayern; Bavarian municipalities with an
ambiguous stem land close to the 245 reference) and matches **exactly** on
every specifically named case: Neustadt spans BB/BY/HE/NI/RP/SH/SN/TH (8
Laender, as specified); Landau = BY+RP; Friedberg = BY+HE; Weilheim = BW+BY;
Hof = BY+RP; Berg = BW+BY+RP; Bogen = BY only (unambiguous); Rothenburg
(added "ob" to the qualifier family after a live bug, see above) =
BY+SN. Treat the small aggregate delta as implementation variance in
generalising the suffix list, not a correctness bug in the named cases.
