#!/usr/bin/env python3
"""Build data/geo/gemeinden_de.csv — a Germany-wide municipality/PLZ -> Land
lookup table, plus data/geo/ambiguous_stems.txt (the precomputed set of bare
name-stems that are NOT safe to guess a Land from).

Sources (see data/geo/README.md for URLs, licence, and refresh cadence):
  A. Destatis "Auszug aus dem Gemeindeverzeichnis" (xlsx) — authoritative,
     one row per municipality, its Verwaltungssitz PLZ, and coordinates.
  C. GeoNames DE.zip — supplements PLZ that Destatis does not carry (a
     municipality can have many PLZ; Destatis only gives the seat's PLZ).

No external dependency is added: openpyxl is not installed in .venv (checked
at the top of main()), so the xlsx is parsed as what it actually is — a zip
of XML — via stdlib zipfile + xml.etree. GeoNames is a plain TSV.

Re-run any time: `python tools/build_geo_table.py`. It re-downloads both
sources fresh into a temp dir and rewrites the two output files.
"""
import csv
import io
import re
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "data" / "geo"
OUT_CSV = OUT_DIR / "gemeinden_de.csv"
OUT_STEMS = OUT_DIR / "ambiguous_stems.txt"
OUT_README = OUT_DIR / "README.md"

DESTATIS_URL = (
    "https://www.destatis.de/DE/Themen/Laender-Regionen/Regionales/"
    "Gemeindeverzeichnis/Administrativ/Archiv/GVAuszugQ/"
    "AuszugGV1QAktuell.xlsx?__blob=publicationFile&v=16"
)
GEONAMES_URL = "https://download.geonames.org/export/zip/DE.zip"

# AGS/ARS "Land" key (2-digit, column C of the Destatis sheet) -> standard
# DE Bundesland abbreviation. This is the official numbering, unrelated to
# GeoNames' own numeric admin1 codes (see ADMIN1_NAME_TO_LAND below).
AGS_LAND = {
    "01": "SH", "02": "HH", "03": "NI", "04": "HB", "05": "NW", "06": "HE",
    "07": "RP", "08": "BW", "09": "BY", "10": "SL", "11": "BE", "12": "BB",
    "13": "MV", "14": "SN", "15": "ST", "16": "TH",
}
XLSX_SHEET_PREFIX = "Onlineprodukt_Gemeinden"  # sheet name changes per quarter

# GeoNames mixes two admin1 schemes in the same file (ISO abbrev + German
# name, and GeoNames' own numeric code + English name). We deliberately key
# off admin_name1 text for BOTH schemes and never touch admin_code1 — the
# numeric scheme is NOT the AGS Land key (GeoNames numeric 02 = Bavaria,
# but AGS 09 = Bayern; AGS 09 in GeoNames' own numbering is Saarland).
ADMIN1_NAME_TO_LAND = {
    "bayern": "BY", "bavaria": "BY",
    "rheinland-pfalz": "RP",
    "nordrhein-westfalen": "NW",
    "niedersachsen": "NI", "lower saxony": "NI",
    "baden-württemberg": "BW",
    "sachsen-anhalt": "ST", "saxony-anhalt": "ST",
    "brandenburg": "BB",
    "schleswig-holstein": "SH",
    "thüringen": "TH", "thuringia": "TH",
    "mecklenburg-vorpommern": "MV", "mecklenburg-western pomerania": "MV",
    "hessen": "HE",
    "sachsen": "SN", "saxony": "SN",
    "hamburg": "HH",
    "land berlin": "BE", "berlin": "BE",
    "bremen": "HB",
    "saarland": "SL",
}

# GeoNames' DE.txt bakes in ~3.5k "Grossempfaenger" rows: a dedicated PLZ
# handed to one company/authority, whose place_name is a company name and
# whose Land is frequently wrong (measured examples: 96039/96076/96035/96063
# all really Bamberg/Bayern, tagged NW/Berlin/Berlin/Hessen). There is no
# machine flag for this in the file, so we deny-list by legal-form and
# institutional-name vocabulary. Verified against the 4 documented examples
# (all match) and manually spot-checked: every hit inspected during dataset
# construction was a genuine company/authority name, never a real town.
#
# Two groups: legal-form suffixes (GmbH, AG, ...) that are always
# space-separated in practice, kept `\b`-anchored on both sides; and
# German institutional NOUNS (Amt, Bank, Werk, Kasse, Zentrale, Verwaltung,
# Sparkasse, Universitaet) that German famously compounds onto a prefix with
# no space at all ("Finanzamt", "Landesbank", "Stadtwerke", "Kreissparkasse",
# "Bundeszentrale") -- a leading `\b` misses every one of those (found live:
# 240+ residual institutional rows survived the original suffix-\b-anchored
# version, e.g. "Amtsgericht München", "BayernLB Bayerische Landesbank").
# The second group therefore only requires a word boundary on the RIGHT
# (the compound's end), not the left, deliberately over-matching since this
# regex only ever drops a supplemental GeoNames PLZ row, never a Destatis
# municipality -- a dropped real town name here costs a missing PLZ, not a
# wrong Land (same "drop more, not less" policy as the rest of this table).
GROSSEMPFAENGER_RE = re.compile(
    r"\b(GmbH|mbH|AG|KG|SE|e\.V\.|OHG|GbR|Co\.|& Co|Service ?Center|"
    r"Geschäftsstelle|Vertrieb|Versicherung|Fabrik|"
    r"Consulting|Solutions|Systems|Software|Logistik|Versand|Handels?|Holding|"
    r"Beteiligung|Datenverarbeitung|International|Deutschland\b|"
    r"c/o|Corp\.|Inc\.|Ltd\.|Kaufhaus|Warenhaus|Filiale|Niederlassung|"
    r"Universit(?:ä|ae)t|Kaserne)\b"
    r"|[\wäöüß]*(?:amt|bank|werke?|kasse|zentrale|verwaltung)\b",
    re.IGNORECASE,
)

UMLAUT_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

# Any qualifier telling you WHICH river/region a same-named town sits on:
# "an der Ruhr", "in Holstein", "a.d.Isar", "a.Main", "i.OB", "b.Coburg",
# "ob der Tauber" ... One regex for the whole an/am/in/im/a./i./b./bei/ob
# family (with or without the following "der"/"dem"), because Destatis uses
# this family of German prepositions interchangeably for the same purpose.
# "ob" added after a live bug (Rothenburg ob der Tauber, BY, was not
# collapsing to the same "rothenburg" stem as Sachsen's Rothenburg/O.L.,
# so the two never got flagged as ambiguous) -- see pflege_jobs/geo.py's
# precedence-fix comment for the full incident. A bare short town name
# (Berg, Bogen, Anger, Amberg, Illertissen, ...) is untouched because the
# prefix token must be immediately followed by "." or whitespace, not by
# more letters of the same word.
QUALIFIER_RE = re.compile(
    r"\b(a|am|an|i|im|in|b|bei|ob)(\.\s*|\s+)(d(er|em)?(\.\s*|\s+))?[\wäöüß.\-]+"
)


def normalize_name(raw: str) -> str:
    """lowercase + umlaut/eszett fold + drop the Destatis administrative-
    status tail (everything after the first comma: ", Stadt", ", St", ", M",
    ", GKSt", and the same family of honorifics — "Kreisstadt",
    "Hansestadt", "Universitätsstadt", etc. — Destatis always puts these
    after a comma, so cutting at the first comma generalises the spec's 4
    named examples to the full ~30-word vocabulary observed in the file
    without hardcoding each one)."""
    s = raw.split(",", 1)[0]
    s = s.lower().translate(UMLAUT_FOLD)
    return re.sub(r"\s{2,}", " ", s).strip()


def bare_stem(normalized: str) -> str:
    """Further strip "(...)" parentheticals, the an/in/a.d./i.OB/... river
    qualifier family, and "/..." forms, down to the bare town name used to
    detect cross-Land name collisions (Neustadt, Landau, Friedberg, ...)."""
    s = re.sub(r"\([^)]*\)", "", normalized)
    s = QUALIFIER_RE.sub("", s)
    s = re.sub(r"/\s*\S*", "", s)
    return re.sub(r"\s{2,}", " ", s).strip().strip(",").strip("-").strip()


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "pflege-board geo builder"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def parse_destatis(xlsx_bytes: bytes) -> list[dict]:
    """Parse the Destatis xlsx via stdlib zipfile + ElementTree (no
    openpyxl). Returns one dict per Satzart-60 municipality row, excluding
    the 196 gemeindefreie Gebiete (Textkennzeichen 65/66, per spec)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    z = zipfile.ZipFile(io.BytesIO(xlsx_bytes))

    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheet_el = None
    for s in wb.findall(".//m:sheets/m:sheet", ns):
        if s.get("name", "").startswith(XLSX_SHEET_PREFIX):
            sheet_el = s
            break
    if sheet_el is None:
        raise RuntimeError(
            f"Destatis xlsx: no sheet named '{XLSX_SHEET_PREFIX}*' found — "
            "upstream file shape changed, fix XLSX_SHEET_PREFIX."
        )
    rid = sheet_el.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = None
    for rel in rels:
        if rel.get("Id") == rid:
            target = rel.get("Target")
            break
    if target is None:
        raise RuntimeError("Destatis xlsx: could not resolve data sheet r:id — file shape changed.")
    sheet_path = f"xl/{target}"

    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
        shared = ["".join((t.text or "") for t in si.findall(".//m:t", ns)) for si in sst.findall("m:si", ns)]

    sheet = ET.fromstring(z.read(sheet_path))
    rows = sheet.find("m:sheetData", ns).findall("m:row", ns)

    def cell_val(c):
        t = c.get("t")
        v = c.find("m:v", ns)
        if v is None:
            return None
        val = v.text
        return shared[int(val)] if t == "s" else val

    col_re = re.compile(r"[A-Z]+")
    out = []
    for r in rows:
        cells = {}
        for c in r.findall("m:c", ns):
            ref = c.get("r")
            if not ref:
                continue
            cells[col_re.match(ref).group()] = cell_val(c)
        if cells.get("A") != "60":
            continue
        if cells.get("B") in ("65", "66"):  # gemeindefreie Gebiete
            continue
        land = AGS_LAND.get(cells.get("C"))
        name = cells.get("H")
        plz = cells.get("N")
        lon_raw, lat_raw = cells.get("O"), cells.get("P")
        if not (land and name and plz and lon_raw and lat_raw):
            continue
        out.append({
            "land": land,
            "ars": "".join(cells.get(k) or "" for k in "CDEFG"),
            "gemeindename": name,
            "plz": plz,
            "lat": lat_raw.replace(",", "."),
            "lon": lon_raw.replace(",", "."),
        })
    return out


def parse_geonames(zip_bytes: bytes, known_plz: set[str]) -> list[dict]:
    """Parse GeoNames DE.zip, drop Grossempfaenger rows, normalise the two
    admin1 schemes to a Land abbreviation, and keep only PLZ that Destatis
    does not already carry (one row per new PLZ, first match wins)."""
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    txt = z.read("DE.txt").decode("utf-8")

    seen_new_plz = set()
    out = []
    skipped_company = skipped_unmapped = skipped_known = 0
    for line in txt.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 11:
            continue
        plz, place, admin_name1 = parts[1], parts[2], parts[3]
        lat, lon = parts[9], parts[10]
        if GROSSEMPFAENGER_RE.search(place):
            skipped_company += 1
            continue
        land = ADMIN1_NAME_TO_LAND.get(admin_name1.strip().lower())
        if not land:
            skipped_unmapped += 1
            continue
        if plz in known_plz:
            skipped_known += 1
            continue
        if plz in seen_new_plz:
            continue  # keep first place_name per new PLZ; grain is PLZ->Land, not quarter list
        seen_new_plz.add(plz)
        out.append({
            "land": land, "ars": "", "gemeindename": place,
            "plz": plz, "lat": lat, "lon": lon,
        })
    print(
        f"  geonames: {skipped_company} dropped as Grossempfaenger-like, "
        f"{skipped_unmapped} dropped (unmapped admin1), "
        f"{skipped_known} skipped (PLZ already in Destatis), "
        f"{len(out)} new rows kept"
    )
    return out


def build():
    print("Checking openpyxl availability ...")
    try:
        import openpyxl  # noqa: F401
        print("  openpyxl IS importable, but this builder uses stdlib zipfile+ElementTree anyway "
              "(no dependency added; keeps a fresh venv able to re-run this script unmodified).")
    except ImportError:
        print("  openpyxl not installed — using stdlib zipfile + xml.etree, as instructed.")

    print(f"Downloading Destatis Source A: {DESTATIS_URL}")
    xlsx_bytes = fetch(DESTATIS_URL)
    print(f"  {len(xlsx_bytes):,} bytes")

    print(f"Downloading GeoNames Source C: {GEONAMES_URL}")
    geonames_bytes = fetch(GEONAMES_URL)
    print(f"  {len(geonames_bytes):,} bytes")

    print("Parsing Destatis xlsx (stdlib zipfile + xml.etree) ...")
    destatis_rows = parse_destatis(xlsx_bytes)

    # --- hard assertions against the measured, documented shape ---
    n_muni = len(destatis_rows)
    n_bayern = sum(1 for r in destatis_rows if r["land"] == "BY")
    assert n_muni == 10747, (
        f"Expected 10747 municipalities (excl. gemeindefreie Gebiete), got {n_muni}. "
        "Upstream Destatis file shape likely changed — re-verify column layout before proceeding."
    )
    assert n_bayern == 2056, (
        f"Expected 2056 Bavarian municipalities, got {n_bayern}. "
        "Upstream Destatis file shape likely changed — re-verify column layout before proceeding."
    )

    for r in destatis_rows:
        r["normalized_name"] = normalize_name(r["gemeindename"])
        r["bare_stem"] = bare_stem(r["normalized_name"])
        r["source"] = "destatis"

    # ambiguous bare stems, computed from Destatis only (authoritative land
    # per municipality) — GeoNames rows never feed this set.
    stem_lands = defaultdict(set)
    for r in destatis_rows:
        stem_lands[r["bare_stem"]].add(r["land"])
    ambiguous_stems = sorted(k for k, v in stem_lands.items() if len(v) >= 2)

    print("Parsing GeoNames DE.zip ...")
    known_plz = {r["plz"] for r in destatis_rows}
    geonames_rows = parse_geonames(geonames_bytes, known_plz)
    for r in geonames_rows:
        r["normalized_name"] = normalize_name(r["gemeindename"])
        r["bare_stem"] = bare_stem(r["normalized_name"])
        r["source"] = "geonames"

    all_rows = destatis_rows + geonames_rows

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = ["land", "ars", "gemeindename", "normalized_name", "bare_stem", "plz", "lat", "lon", "source"]
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r[k] for k in fields})

    with OUT_STEMS.open("w", encoding="utf-8") as f:
        f.write(
            "# Bare-name stems that occur in >=2 Bundeslaender in the Destatis\n"
            "# municipality list. The labeller MUST refuse to guess a Land from a\n"
            "# bare stem in this set (fall back to PLZ or an exact normalized_name\n"
            "# match instead). One stem per line, sorted, generated by\n"
            "# tools/build_geo_table.py — do not hand-edit.\n"
            "#\n"
            "# NOTE: if this list contains a blank line, that line represents the\n"
            "# empty-string stem shared by >=2 Laender' municipalities whose full\n"
            "# name IS a bare preposition (\"Am Mellensee\", \"An der Poststrasse\", ...\n"
            "# -- QUALIFIER_RE consumes the entire name, not just a suffix, when the\n"
            "# name itself starts with the preposition). It is inert by design: any\n"
            "# reader of this file (including pflege_jobs.geo._load) treats a blank\n"
            "# line as \"no stem\", and geo.resolve() never looks up an empty stem in\n"
            "# the first place (it gates on `if stem:` before consulting this set) --\n"
            "# those municipalities are already handled correctly one branch earlier,\n"
            "# via an exact normalized_name match. Do not skip it as a stray newline.\n"
        )
        for s in ambiguous_stems:
            f.write(s + "\n")

    # --- verification block ---
    by_land = defaultdict(int)
    for r in destatis_rows:
        by_land[r["land"]] += 1
    distinct_plz_destatis = len({r["plz"] for r in destatis_rows})
    distinct_plz_all = len({r["plz"] for r in all_rows})
    with_coords = sum(1 for r in destatis_rows if r["lat"] and r["lon"])
    ambiguous_by = sum(1 for s in ambiguous_stems if "BY" in stem_lands[s])

    # Exact-name collisions (finer grained than bare_stem: no qualifier stripping) -- surfaced here
    # because README documents normalized_name -> Land as a supported lookup, so its own hit-rate
    # deserves reporting alongside the stem numbers.
    name_lands = defaultdict(set)
    for r in destatis_rows:
        name_lands[r["normalized_name"]].add(r["land"])
    ambiguous_names = [k for k, v in name_lands.items() if len(v) >= 2]
    ambiguous_names_by = sum(1 for n in ambiguous_names if "BY" in name_lands[n])

    print("\n=== VERIFICATION ===")
    print(f"Destatis municipality rows: {n_muni} (expected 10747)")
    print(f"Bayern municipality rows: {n_bayern} (expected 2056)")
    print(f"Distinct PLZ (Destatis only): {distinct_plz_destatis} (expected 6440)")
    print(f"GeoNames rows merged in: {len(geonames_rows)}")
    print(f"Total rows in {OUT_CSV.name}: {len(all_rows)}")
    print(f"Distinct PLZ (merged): {distinct_plz_all}")
    print(f"Destatis rows carrying coordinates: {with_coords}/{n_muni}")
    print("Rows per Land (Destatis):")
    for land in sorted(by_land):
        print(f"  {land}: {by_land[land]}")
    print(f"Ambiguous bare stems: {len(ambiguous_stems)} (of which involve Bayern: {ambiguous_by})")
    print(f"Ambiguous exact names: {len(ambiguous_names)} (of which involve Bayern: {ambiguous_names_by})")
    print(f"\nWrote {OUT_CSV} ({OUT_CSV.stat().st_size:,} bytes)")
    print(f"Wrote {OUT_STEMS} ({OUT_STEMS.stat().st_size:,} bytes)")


if __name__ == "__main__":
    build()
