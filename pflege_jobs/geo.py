"""pflege_jobs.geo -- municipality/PLZ -> Bundesland labeller. Unwired (see report); nothing here
is imported by the live pipeline yet.

WHY THIS EXISTS (do not re-derive, the numbers were measured against real data):

pflege_jobs.sources.career_crawl.in_bavaria() (career_crawl.py:83-100) is a boolean guess built on
two polluted inputs:

1. BAV_PLZ, a single PLZ-range regex (`^(63[7-9]\\d\\d|8[0-7]\\d{3}|881[3-7]\\d|89[2-5]\\d\\d|9[0-7]\\d{3})$`),
   overreaches by 45/2516 matched PLZ (1.8%) into three real non-Bavarian clusters plus one foreign
   exclave -- all real hospital towns, all measured: 89xxx Heidenheim/Giengen (Baden-Wuerttemberg,
   Klinikum Heidenheim is a real hospital there), 97xxx Wertheim/Bad Mergentheim (Baden-Wuerttemberg,
   Bad Mergentheim is a real clinic town), 965xx Sonneberg (Thueringen), and 87491 Jungholz -- an
   Austrian exclave that only has a German PLZ because it is reachable by road solely through
   Bavaria.
2. `towns`, built at pflege_jobs/cli.py:370 from data/registry/clinics.csv's PDF-parse-mangled town
   column, carries junk tokens ("klinik", "hof", "berg", "landau", "friedberg", "neustadt",
   "weilheim", "gmbh & co. kg", ...) that in_bavaria's first-token match (career_crawl.py:98-99) then
   matches against ANY city string starting with that word. Combined with a Bavarian-town regex that
   is a strict subset check, not a Land check, this makes today's code return True for five real,
   verified non-Bavarian cities: 'Neustadt an der Weinstrasse' (Rheinland-Pfalz), 'Landau in der
   Pfalz' (Rheinland-Pfalz), 'Friedberg (Hessen)' (Hessen), 'Weilheim an der Teck'
   (Baden-Wuerttemberg), 'Hof, Westfalen' (Nordrhein-Westfalen), and 'Klinik Hohe Mark Oberursel'
   (Hessen -- matches only because "klinik" sits in the polluted towns set).

Both bugs share one root cause: guessing a Land from a bare town name without ever checking whether
that name is ambiguous across Laender. This module replaces the guess with a lookup against
data/geo/gemeinden_de.csv (built by tools/build_geo_table.py from Destatis + GeoNames, see
data/geo/README.md) plus data/geo/ambiguous_stems.txt, a precomputed set of 407 bare name-stems that
occur in >= 2 Bundeslaender. If a bare stem is ambiguous, resolve() refuses to guess and says which
Laender it could be, instead of picking one.

Nothing in the algorithm below is Bavaria-specific -- no "BY" or "09" literal in a branch. Bavaria is
just a value the CALLER filters resolve() on (`if geo.resolve(...).land == "BY"`); adding a filter
for e.g. Baden-Wuerttemberg needs zero code change here, only a different value on the caller side.

Every branch returns a distinct `rule` string so a reader (human or agent) can tell from the string
alone which branch fired and why -- same convention as pflege_jobs.classify (role_rule, e.g.
"pflegefachkraft:dauernachtwache") and pflege_jobs.sources.inbox's malformed-scalar handling.
"""
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "geo"
_CSV_PATH = _DATA_DIR / "gemeinden_de.csv"
_AMBIG_PATH = _DATA_DIR / "ambiguous_stems.txt"

UNKNOWN = "UNKNOWN"  # explicit Land-unknown sentinel. Never None (None-means-two-things is the bug
                      # this module exists to avoid): a caller who forgets to check .land == UNKNOWN
                      # can't accidentally read it as falsy-but-valid the way None often gets read.

# AGS/ISO 2-letter Land code -> official German name. `land` on Geo is one of these 16 keys, or
# UNKNOWN. Picked the 2-letter code over the AGS Land-key digits (e.g. "09") because it is what
# career_crawl's own region regex and real-world `addressRegion`/"BY" strings already use.
LAND_NAMES = {
    "BW": "Baden-Württemberg", "BY": "Bayern", "BE": "Berlin", "BB": "Brandenburg",
    "HB": "Bremen", "HH": "Hamburg", "HE": "Hessen", "MV": "Mecklenburg-Vorpommern",
    "NI": "Niedersachsen", "NW": "Nordrhein-Westfalen", "RP": "Rheinland-Pfalz",
    "SL": "Saarland", "SN": "Sachsen", "ST": "Sachsen-Anhalt", "SH": "Schleswig-Holstein",
    "TH": "Thüringen",
}

UMLAUT_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

# Extra English/ceremonial names for the SAME 16 Laender, symmetric on purpose (nothing Bavaria-only):
# every Land gets its common English translation and, where real, its constitutional long form
# ("Freistaat ...", "Freie[ und] Hansestadt ..."). Folded through _fold() below the same way as the
# official names, so "Bavaria", "Free State of Bavaria" and "Freistaat Bayern" all resolve exactly
# like "Bayern" does -- required per spec ("Bayern vs BY vs Freistaat Bayern vs Bavaria").
_REGION_EXTRA_NAMES = {
    "BW": ["Baden-Wuerttemberg"],
    "BY": ["Bavaria", "Freistaat Bayern", "Free State of Bavaria"],
    "HB": ["Free Hanseatic City of Bremen", "Freie Hansestadt Bremen"],
    "HH": ["Free and Hanseatic City of Hamburg", "Freie und Hansestadt Hamburg"],
    "HE": ["Hesse"],
    "MV": ["Mecklenburg-Western Pomerania"],
    "NI": ["Lower Saxony"],
    "NW": ["North Rhine-Westphalia"],
    "RP": ["Rhineland-Palatinate"],
    "SN": ["Saxony", "Freistaat Sachsen"],
    "ST": ["Saxony-Anhalt"],
    "TH": ["Thuringia", "Freistaat Thüringen"],
}


def _fold(s: str) -> str:
    """lowercase + umlaut/eszett fold + collapse whitespace. Same alphabet as the CSV's
    normalized_name/bare_stem columns so an input string and a CSV value compare equal."""
    s = (s or "").strip().lower().translate(UMLAUT_FOLD)
    return re.sub(r"\s+", " ", s)


def _region_code(region: str) -> Optional[str]:
    """A bare 2-letter code ("BY", "by", " BY ") is checked separately from the folded-name table:
    it is exact-length-2 and uppercase-exact against LAND_NAMES' own keys, never fuzzy-folded (folding
    a 2-letter code the same way as a name risks a coincidental collision with some other 2-letter
    input)."""
    r = (region or "").strip()
    if len(r) == 2 and r.upper() in LAND_NAMES:
        return r.upper()
    return _REGION_ALIASES.get(_fold(r))


# region-string -> Land code, built once at import from LAND_NAMES + _REGION_EXTRA_NAMES above.
_REGION_ALIASES = {}
for _code, _official in LAND_NAMES.items():
    _REGION_ALIASES[_fold(_official)] = _code
for _code, _names in _REGION_EXTRA_NAMES.items():
    for _n in _names:
        _REGION_ALIASES[_fold(_n)] = _code
del _code, _official, _names, _n

# --- Same normalize_name()/bare_stem() as tools/build_geo_table.py --------------------------------
# Duplicated, not imported: tools/ is a script directory, not a package, and importing across that
# boundary is more fragile than 15 lines kept in sync. If build_geo_table.py's normalization ever
# changes, this must change with it or CSV lookups silently stop matching -- both are short and
# comment-linked so that's a visible diff, not a hidden one.
# "ob" added alongside tools/build_geo_table.py's copy (Rothenburg ob der Tauber bugfix, see
# resolve()'s name/stem-ambiguity comment below) -- keep both copies in sync.
QUALIFIER_RE = re.compile(r"\b(a|am|an|i|im|in|b|bei|ob)(\.\s*|\s+)(d(er|em)?(\.\s*|\s+))?[\wäöüß.\-]+")


def normalize_name(raw: str) -> str:
    s = (raw or "").split(",", 1)[0]
    s = s.lower().translate(UMLAUT_FOLD)
    return re.sub(r"\s{2,}", " ", s).strip()


def bare_stem(normalized: str) -> str:
    s = re.sub(r"\([^)]*\)", "", normalized)
    s = QUALIFIER_RE.sub("", s)
    s = re.sub(r"/\s*\S*", "", s)
    return re.sub(r"\s{2,}", " ", s).strip().strip(",").strip("-").strip()


# ==================== RARE CASES (hand-verified exceptions, not data) =============================
# Everything resolvable from the CSV lives in the CSV. This table exists ONLY for inputs that need a
# distinct, human-legible reason instead of a bare "not found" -- each entry earns its place:
#
# - 87491 (Jungholz): a German PLZ range assigned to Austrian territory (Tyrol), reachable only via
#   Bavaria. It is correctly ABSENT from gemeinden_de.csv (GeoNames' own row for it carries no
#   admin1/Land at all, so the builder's admin1-name lookup skipped it -- verified: `grep 87491`
#   against the raw GeoNames DE.txt shows blank admin columns). Without this entry, resolve() would
#   still return UNKNOWN for it (PLZ simply not found) -- which is already correct and already NOT
#   the old regex's false "True" -- but "not found" reads like missing data, not like "this PLZ is
#   deliberately not Germany". This entry upgrades that to an explicit, auditable reason.
# - The six multi-Land PLZ (14715, 17337, 19273, 19357, 65391, 69434) and the four measured
#   Grossempfaenger PLZ (96039, 96076, 96035, 96063) are DELIBERATELY NOT here: the first six are
#   handled generically (resolve() detects PLZ->multiple-Land at lookup time from the CSV itself, see
#   plz_ambiguous below -- no hardcoded list to keep in sync, and it would still catch a 7th PLZ if a
#   future Destatis refresh added one); the Grossempfaenger four are already absent from
#   gemeinden_de.csv (tools/build_geo_table.py's GROSSEMPFAENGER_RE dropped them, verified: none of
#   the four appear in the shipped CSV, while Bamberg's own real PLZ 96031/96047/96049 do, tagged BY)
#   so they already fall through to a correct not-PLZ-found the same way Jungholz would without its
#   entry above -- adding them here would just duplicate what "not found" already gets right.
# - Buesingen am Hochrhein (78266, German exclave inside Switzerland): checked, does NOT need an
#   entry. It is a normal, unambiguous BW row in the CSV (single municipality, single PLZ, no Land
#   collision) -- the "exclave" fact is geographic trivia, not a labelling hazard, because
#   Buesingen's German PLZ has exactly one valid Land answer and the CSV already has it.
# - Mangled bare_stem (data quality footnote, not a rare case here): 9 real municipalities are
#   literally named "Am ..."/"An der ..." (e.g. "Am Mellensee", "An der Poststrasse", "An der
#   Schmuecke, Stadt") -- QUALIFIER_RE, designed to strip a *qualifier suffix* like "...an der
#   Weinstrasse", instead consumes the ENTIRE name when the name itself starts with that preposition,
#   leaving bare_stem == "". None of the 9 are Bavarian (BB/MV/ST/TH), and none need a table entry:
#   resolve()'s bare-stem branch below treats an empty bare_stem as "no stem signal" and never matches
#   on it, so these municipalities still resolve correctly via the name_exact branch one step earlier
#   (their normalized_name, e.g. "am mellensee", is untouched by the comma-cut and matches fine).
RARE_CASE_PLZ = {
    "87491": "rare_case:jungholz_austrian_exclave",  # see comment block above
}


@dataclass(frozen=True)
class Geo:
    """Immutable resolve() result. land/land_name are UNKNOWN/None together, never one without the
    other. matched_name/lat/lon/plz/source describe the municipality row that decided it, when any
    row was actually matched (region-only and ambiguity/UNKNOWN exits leave them None)."""
    land: str
    land_name: Optional[str]
    rule: str
    matched_name: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    plz: Optional[str] = None
    source: Optional[str] = None


def _unknown(rule: str) -> Geo:
    return Geo(land=UNKNOWN, land_name=None, rule=rule)


# ==================== dataset, loaded once at import (see report for measured timing) =============
_PLZ_ROWS = {}    # plz -> list[row]  (row: land, gemeindename, normalized_name, bare_stem, lat, lon, source)
_NAME_ROWS = {}   # normalized_name -> list[row]  (destatis rows only, see data/geo/README.md)
_STEM_ROWS = {}   # bare_stem -> list[row]        (destatis rows only, same reason)
_AMBIGUOUS_STEMS = set()


def _load():
    with open(_CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["lat"] = float(row["lat"]) if row["lat"] else None
            row["lon"] = float(row["lon"]) if row["lon"] else None
            _PLZ_ROWS.setdefault(row["plz"], []).append(row)
            if row["source"] == "destatis":
                if row["normalized_name"]:
                    _NAME_ROWS.setdefault(row["normalized_name"], []).append(row)
                if row["bare_stem"]:
                    _STEM_ROWS.setdefault(row["bare_stem"], []).append(row)
    with open(_AMBIG_PATH, encoding="utf-8") as f:
        _AMBIGUOUS_STEMS.update(line.strip() for line in f if line.strip() and not line.startswith("#"))


_load()


def _lands(rows):
    return sorted({r["land"] for r in rows})


def _pick(rows):
    """Prefer a destatis row (real coordinates + AGS) over a geonames-only row for the returned
    matched_name/lat/lon; both carry the same Land when this is called (caller already checked)."""
    return next((r for r in rows if r["source"] == "destatis"), rows[0])


def _row_geo(row, land, rule):
    return Geo(land=land, land_name=LAND_NAMES[land], rule=rule, matched_name=row["gemeindename"],
               lat=row["lat"], lon=row["lon"], plz=row["plz"], source=row["source"])


def _scalar(v):
    """A malformed upstream row can carry a one-item list instead of a scalar per field (seen live
    2026-09-09, inbox_id 13576: {"plz": ["97318"], "city": ["Kitzingen"]}). Same defensive unwrap as
    inbox.jobposting_to_obs's local _scalar()."""
    return (v[0] if v else None) if isinstance(v, list) else v


def _clean_plz(plz):
    """A German PLZ is always 5 digits. A 4-digit string is accepted and zero-padded (a genuine,
    common export quirk: a leading zero silently dropped, e.g. "1067" for Dresden's 01067). Anything
    shorter is NOT padding-worthy -- it is truncated/malformed input (seen live: "603" for a
    Frankfurt PLZ, likely an upstream string-slicing bug), and zfill-ing it would manufacture a
    plausible-looking but fake 5-digit code instead of correctly reporting "no usable PLZ"."""
    plz = _scalar(plz)
    if plz is None:
        return None
    plz = str(plz).strip()
    if not plz or not plz.isdigit():
        return None
    if len(plz) == 5:
        return plz
    if len(plz) == 4:
        return plz.zfill(5)
    return None


def resolve(city=None, plz=None, region=None, *, text=None) -> Geo:
    """city/plz/region: the structured fields a job posting carries. text: an optional single raw
    location string, used ONLY when city/plz/region are all empty -- tried first as a region name
    (covers a bare "Bayern"/"Bavaria" string), then as a city name. Precedence below (region > PLZ >
    exact name > bare stem > UNKNOWN) mirrors the measured reliability of each signal -- see the
    module docstring and data/geo/README.md for the numbers behind each cutoff.

    WARNING for any future caller: `region` is checked FIRST and wins over PLZ/city whenever it
    decodes (see branch 1 below) -- correct only when `region` is a real, per-listing signal.
    Measured live 2026-09-09 against pflege_jobs.posting_observations: the stored `region` column is
    a hardcoded literal "BAYERN" written unconditionally by every one of its ~10 writer paths
    (feeds.py, inbox.py, career_crawl.py, bite.py, pi_asp.py, board_csv.py, firecrawl_agent.py,
    vendor_adapters.py, portals.py, app/crawl.py) -- it is NOT derived from the posting's real
    location. Passing that column's value as `resolve(..., region=...)` would make branch 1 fire on
    100% of rows and silently override every correct PLZ/city answer underneath it. Do not wire
    `posting_observations.region` into this parameter without first fixing (or bypassing) those
    writer paths -- that decision belongs to whoever wires this module in, not to this docstring."""
    city, plz, region = _scalar(city), _clean_plz(plz), _scalar(region)
    region = (region or "").strip() or None
    city = (city or "").strip() or None
    if not (city or plz or region) and text:
        text = str(text).strip()
        code = _region_code(text) if text else None
        if code:
            return Geo(land=code, land_name=LAND_NAMES[code], rule=f"region_exact_text:{code}")
        city = text or None

    # 1. explicit region string: near-total ground truth when it decodes to a known Land. If it
    #    doesn't decode (garbage/unexpected string), it is NOT treated as "definitely UNKNOWN" --
    #    it just carries no signal, and we fall through to PLZ/name the same as if it were absent.
    if region:
        code = _region_code(region)
        if code:
            return Geo(land=code, land_name=LAND_NAMES[code], rule=f"region_exact:{code}")

    # 2. PLZ: near-decisive (measured: 6 of 6440+ distinct PLZ straddle two Laender, none Bavarian).
    if plz:
        if plz in RARE_CASE_PLZ:
            return _unknown(RARE_CASE_PLZ[plz])
        rows = _PLZ_ROWS.get(plz)
        if rows:
            lands = _lands(rows)
            if len(lands) == 1:
                return _row_geo(_pick(rows), lands[0], f"plz_exact:{plz}")
            return _unknown(f"plz_ambiguous:{plz}:{'|'.join(lands)}")

    # 3. exact official municipality name (comma-tail stripped, e.g. "Neustadt, Stadt" -> "neustadt"
    #    already wouldn't apply here -- this branch matches the FULL normalized name, not the stem;
    #    it is deliberately checked before the bare-stem branch because a full match is stronger
    #    evidence than a stem match).
    #
    #    BUT: a full match is only stronger evidence when the QUERY carries no qualifier of its own
    #    (i.e. its normalized name IS already its own bare stem) AND that bare stem is ambiguous
    #    across Laender. Bug found live: bare "Weiden" is RP's bare official name (name_exact -> RP)
    #    *and*, with no qualifier stripped off the query, collides with the stem of BY's "Weiden
    #    i.d.OPf." (Kliniken Nordoberpfalz, a real, much bigger city) -- returning RP confidently was
    #    a wrong-Land bug, not a safe refusal. Same shape as bare "Heidenheim" (BY's bare name vs BW's
    #    "Heidenheim an der Brenz") and bare "Rothenburg" (SN's bare stem vs BY's "Rothenburg ob der
    #    Tauber"). The gate on name == stem matters: it must NOT fire when the caller's OWN string
    #    already supplied the disambiguating qualifier ("Weiden i.d.OPf.", "Rothenburg ob der
    #    Tauber", "Frankfurt am Main") -- in that case the qualifier IS real evidence and stripping it
    #    away just to compare stems would manufacture a false collision (verified live: without this
    #    gate, "Frankfurt am Main" wrongly became ambiguous against Frankfurt/Oder purely because its
    #    OWN stem collides, discarding the disambiguation the caller already gave us).
    if city:
        name = normalize_name(city)
        stem = bare_stem(name)
        query_has_no_qualifier = bool(stem) and stem == name
        stem_is_ambiguous = query_has_no_qualifier and stem in _AMBIGUOUS_STEMS
        rows = _NAME_ROWS.get(name)
        if rows and not stem_is_ambiguous:
            lands = _lands(rows)
            if len(lands) == 1:
                return _row_geo(_pick(rows), lands[0], f"name_exact:{name}")
            return _unknown(f"name_ambiguous:{name}:{'|'.join(lands)}")
        if rows and stem_is_ambiguous:
            lands = sorted(set(_lands(rows)) | set(_lands(_STEM_ROWS.get(stem, []))))
            return _unknown(f"name_stem_ambiguous:{name}:{'|'.join(lands)}")

        # 4. bare stem (river/region qualifier and parenthetical stripped) -- ONLY if that stem is
        #    NOT in the precomputed ambiguous set. This is the branch that replaces today's polluted
        #    first-token guess: 'Neustadt an der Weinstrasse' -> stem "neustadt" -> in
        #    ambiguous_stems.txt (8 Laender) -> UNKNOWN, never a guessed True.
        stem = bare_stem(name)
        if stem:
            if stem in _AMBIGUOUS_STEMS:
                candidate_rows = _STEM_ROWS.get(stem, [])
                lands = _lands(candidate_rows) or ["?"]
                return _unknown(f"stem_ambiguous:{stem}:{'|'.join(lands)}")
            rows = _STEM_ROWS.get(stem)
            if rows:
                lands = _lands(rows)
                if len(lands) == 1:
                    return _row_geo(_pick(rows), lands[0], f"stem_exact:{stem}")
                # In the dataset but not in the precomputed ambiguous file (stale file vs CSV) --
                # refuse rather than trust a possibly-outdated precomputation.
                return _unknown(f"stem_ambiguous:{stem}:{'|'.join(lands)}")

    if not (city or plz or region):
        return _unknown("no_signal")
    return _unknown("not_found")
