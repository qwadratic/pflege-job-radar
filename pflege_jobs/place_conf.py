"""Place match with a degree of confidence (TASK-431.9, an experiment). ADDITIVE: no existing module imports this one and it
changes none of pflege_jobs.geo, mechanics, verify, app.data or link-clinics; it only reads their public helpers.

Purpose (Ivan, 2026-10-06): the PLZ work is about attribution -- a posting must be matched to the right clinic with a DEGREE of
confidence, not yes/no. Three parts:

  1. a city in any spelling ends at a municipality and its PLZ         Gazetteer.resolve / Gazetteer.plz_info
  2. every version of a place carries a weight, and a value that repeats on ONE board whatever each posting's own place is
     is a board value, not a place of the posting                       POSTING_WEIGHT / CLINIC_WEIGHT / board_stamps
  3. (posting, clinic) -> category agree | agree_weak | disagree | unknown and a confidence in [0, 1]       match

The weights are written down before they were run on the population and are NOT fitted to it: each is a judgement about how
directly the source states the place, with the one-line reason beside it. Nothing here caps, falls back silently or narrows
defensively: a rule that decided is named in `Resolution.rule`, an unresolved string stays `unresolved`, a posting with no own
place stays `unknown`.
"""
import csv
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pflege_jobs import geo

_GEO = Path(__file__).resolve().parent.parent / "data" / "geo"

# ---------------------------------------------------------------------------------------------------------------------------
# D. weights. One line of reason each; tests/test_place_conf.py pins the numbers.
# ---------------------------------------------------------------------------------------------------------------------------
POSTING_WEIGHT = {
    "jsonld_address": 0.90,         # JSON-LD jobLocation read off the posting's own page: the employer states PLZ and place per posting
    "structured_plz_city": 0.85,    # the adapter's address field with PLZ and place (ATS API address, bite address, softgarden location)
    "structured_city": 0.65,        # the adapter's place without a PLZ: an ATS location name, sometimes the seed town (see seed_stamp)
    "text_einsatzort": 0.60,        # 'Einsatzort: X' / 'Arbeitsort: X' in the text: labelled, but free text and sometimes a different site
    "text_plz_ort": 0.50,           # '86156 Augsburg' in the text: the first address on a page is often the head office or the applicant address
    "title_or_url_city": 0.35,      # a place in the title or the URL slug: the job is advertised under that name, not necessarily located there
    "text_mention": 0.20,           # a bare place name somewhere in the description (sister sites, 'Region X', commuting tips)
    "seed_stamp": 0.10,             # the crawler stamped the seed clinic's own town (payload city_source 'seed'): says where we looked, not where the job is
    "board_stamp": 0.05,            # a value that repeats on every posting of a board whatever the posting says (kbo.de: Munich head office, TASK-68)
}
POSTING_WHY = {
    "jsonld_address": "Structured data the employer publishes for this one posting.",
    "structured_plz_city": "A parsed address field: PLZ and place agree with each other far more often than a bare place string does.",
    "structured_city": "A place name with no PLZ to check it against, and the field some adapters fill with the seed town.",
    "text_einsatzort": "A label in prose: the writer means it, but the extractor can take a sentence fragment for a place.",
    "text_plz_ort": "A postal address somewhere on the page: the footer or the application address as often as the workplace.",
    "title_or_url_city": "Advertised under a place name is weaker than located in it (regional pool ads, 'Region X').",
    "text_mention": "A name in running text carries no statement about where the job is.",
    "seed_stamp": "Written by the crawler from the registry, so it repeats the clinic and cannot confirm it.",
    "board_stamp": "A constant of the board, identical on postings that are in different places.",
}
STAMP_KINDS = {"seed_stamp", "board_stamp"}
# an unlabelled address or a bare name in the text can support a place but never decide against one: the first PLZ on a page is as often the
# head office, the footer or the application address as the workplace (pflege_jobs.verify.TRUSTED_LOC says the same for the same job)
CONFIRM_ONLY = {"text_plz_ort", "text_mention"}

CLINIC_WEIGHT = {
    "imprint_plz": 0.95,            # the clinic's own imprint or address page states the PLZ (TASK-431.7 verdicts, quote and URL kept)
    "rhv_id": 0.95,                 # RHV site id: the Reha directory row IS the site (exact id, street and PLZ of the official list)
    "khv_domain": 0.90,             # KHV site picked by the clinic's own web domain: the clinic names that very site
    "dk_source": 0.90,              # the PLZ in the address of the Diakoneo list's own source text
    "khv_only_site": 0.85,          # the one KHV site in the municipality: identity by place, not by name
    "khv_one_plz": 0.70,            # several KHV sites in the municipality, all with one PLZ: the PLZ is safe, the site is not
    "klinikradar": 0.60,            # a third-party directory page: independent but unofficial
    "khv_name_overlap": 0.60,       # KHV site picked by name tokens only; 3 of 94 such picks were another operator's site (TASK-431.7)
    "posting_modal": 0.50,          # the modal PLZ of the clinic's own linked postings: circular where a link is wrong
    "registry_town": 0.80,          # clinics.town from the Krankenhausplan: a municipality, sometimes a district or a site name
    "registry_landkreis": 0.90,     # clinics.landkreis from the Krankenhausplan: administrative, rarely wrong
    "khv_other_site": 0.40,         # a further KHV site of the same municipality: a possible other PLZ of the clinic, not stated for it
}
CLINIC_WHY = {
    "imprint_plz": "First-hand and addressed to the public; quote and URL are kept in the corrections row.",
    "rhv_id": "The directory row has the clinic's own id, so no matching step can go wrong.",
    "khv_domain": "A domain match is specific; a wrong one would need two sites to share a web address.",
    "dk_source": "Read from the address line of the list that created the clinic row.",
    "khv_only_site": "Right as a place whenever the registry town is right; the clinic may be a different one of that town.",
    "khv_one_plz": "The PLZ cannot be wrong inside that municipality, only the choice of site.",
    "klinikradar": "An aggregator, checked against the KHV in 324 of 325 cases but not an authority.",
    "khv_name_overlap": "Token overlap picks the wrong operator's site now and then.",
    "posting_modal": "The postings' PLZ is the value being tested here, so it cannot also be the oracle.",
    "registry_town": "The registry town is a municipality for most clinics and a part of one for some.",
    "registry_landkreis": "A Kreis is large and stable; the Krankenhausplan lists clinics under it.",
    "khv_other_site": "The directory lists the site; whether it belongs to this clinic is not stated.",
}

# how far a rule that read a spelling is trusted (the factor multiplies the claim whose PLZ could not settle the place)
RULE_FACTOR = {
    "exact": 1.0, "ascii_fold": 0.95, "qualifier": 0.95, "stem": 0.90, "bavaria_prior": 0.90, "district_of": 0.85,
    "geonames_place": 0.80, "head_token": 0.60, "tail_token": 0.60, "multi": 0.50, "plz": 1.0,
}
# agreement level between two places and what it is worth
LEVEL_FACTOR = {"plz": 1.0, "municipality": 0.8, "kreis": 0.35, "none": 0.0}

# ---------------------------------------------------------------------------------------------------------------------------
# C. spelling -> municipality
# ---------------------------------------------------------------------------------------------------------------------------
LAND_OF_AGS = {"01": "SH", "02": "HH", "03": "NI", "04": "HB", "05": "NW", "06": "HE", "07": "RP", "08": "BW", "09": "BY", "10": "SL",
               "11": "BE", "12": "BB", "13": "MV", "14": "SN", "15": "ST", "16": "TH"}
RB_WORDS = {"oberbayern": "1", "obb": "1", "niederbayern": "2", "nb": "2", "oberpfalz": "3", "opf": "3", "oberfranken": "4", "ofr": "4",
            "mittelfranken": "5", "mfr": "5", "unterfranken": "6", "ufr": "6", "schwaben": "7"}
BAVARIA_WORDS = {"bayern", "bavaria", "freistaat bayern", "by"}
NON_PLACE = {"deutschland", "deutschlandweit", "bundesweit", "ganz deutschland", "germany", "online", "remote", "homeoffice", "home office",
             "mobil", "flexibel", "bayern", "nicht angegeben", "n/a", "na", "keine angabe", "-", "--"}


CONNECTORS = {"in", "im", "der", "dem", "den", "a", "an", "am", "i", "ob", "o", "d", "bei", "b", "vor", "auf", "id", "ad", "od"}
# English and other exonyms of Bavarian cities (alternate names of the same municipality)
EXONYMS = {"munich": "München", "nuremberg": "Nürnberg", "ratisbon": "Regensburg", "ingolstadt": "Ingolstadt"}
LEGAL_FORM = re.compile(r"\s+(?:g?gmbh(?: & co\.? kg)?|mbh|ag|kg|kgaa|e\.?\s?v\.?|se|stiftung)\s*$", re.I)
_REGION_RE = re.compile(r"^(?P<rest>.*?)(?P<sep>[,/(\-–]|\s)\s*\(?(?P<word>freistaat bayern|bayern|bavaria|deutschland|germany|oberbayern|niederbayern|"
                        r"oberpfalz|oberfranken|mittelfranken|unterfranken|schwaben|obb\.?|opf\.?|ofr\.?|mfr\.?|ufr\.?)\)?\s*$", re.I)
# a region word as the official short name writes it ('Weiden i.d.OPf.', 'Haag i.OB'), for the qualifier match
QUAL_ALIAS = {"oberpfalz": "opf", "oberbayern": "ob", "niederbayern": "nb", "schwaben": "schw", "unterfranken": "ufr", "mittelfranken": "mfr",
              "oberfranken": "ofr"}


_LAND_OF_WORD = {geo.fold_town(n): c for c, n in geo.LAND_NAMES.items()}
_LAND_OF_WORD.update({"holstein": "SH", "westerwald": "RP", "taunus": "HE", "odenwald": "HE", "oder": "BB", "main": "HE"})
LAND_WORDS = {"baden-wurttemberg", "thuringen", "sachsen", "hessen", "niedersachsen", "nordrhein-westfalen", "rheinland-pfalz", "saarland", "berlin",
              "brandenburg", "bremen", "hamburg", "mecklenburg-vorpommern", "sachsen-anhalt", "schleswig-holstein", "nordoberpfalz", "allgau", "franken"}


def _strip_diacritics(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


EXONYMS_FOLDED = {k: v for k, v in EXONYMS.items() if k != v.lower()}


def _prep(s):
    """The text of a place string, tidied: NBSP and ZWSP out, 'St.'/'Sankt' one spelling, runs of blanks one."""
    s = unicodedata.normalize("NFKC", str(s)).replace("​", "").strip(" \t\r\n,;|.-–")
    s = re.sub(r"\b(?:sankt|st)\b\.?\s*(?=[A-Za-zÄÖÜäöü])", "Sankt ", s, flags=re.I)
    return re.sub(r"\s+", " ", s)


def _fold1(s):          # ä -> ae: the spelling the registry and Destatis use
    return geo.fold_town(_prep(s))


def _fold2(s):          # ä -> a: the spelling of a form field that lost its umlaut keys
    return geo.fold_town(_strip_diacritics(_prep(s).lower().replace("ß", "ss")))


def clean_plz(v):
    """A German PLZ is exactly five digits. A one-item list (some JSON-LD) is unwrapped, a list of two values, a dash, a bare 0 or a
    4-digit number is no PLZ -- there is no zero-padding here: a Bavarian PLZ starts at 63."""
    if isinstance(v, (list, tuple)):
        if len(v) != 1:
            return None
        v = v[0]
    if v is None:
        return None
    s = str(v).strip()
    return s if re.fullmatch(r"\d{5}", s) and s != "00000" else None


@dataclass(frozen=True)
class Muni:
    ars: str
    name: str
    land: str
    kreis: str
    plz: str            # the seat PLZ of the Destatis table, the main PLZ of the municipality
    lat: Optional[float] = None
    lon: Optional[float] = None

    @property
    def rb(self):
        return self.ars[2]


@dataclass(frozen=True)
class PlzInfo:
    valid: bool                      # the table knows this PLZ at all
    kreise: frozenset                # Kreis keys (ARS[:5]) it lies in; empty when only the Land is known
    munis: tuple                     # municipalities (Muni) it is known to belong to
    land: frozenset


@dataclass(frozen=True)
class Resolution:
    status: str                      # unique | ambiguous | multi | outside_bavaria | non_place | unresolved
    munis: tuple                     # Muni objects
    rule: Optional[str]              # which branch decided
    kreise: frozenset = frozenset()
    plz_main: Optional[str] = None   # seat PLZ of the municipality when unique
    plzs: tuple = ()                 # every PLZ the table knows for it, seat first
    plz_consistent: Optional[bool] = None   # a PLZ was given: does it lie in a Kreis of the candidates?
    n_candidates: int = 0
    raw: Optional[str] = None


_UNRESOLVED = lambda raw, why="none": Resolution("unresolved", (), why, raw=raw)  # noqa: E731


class Gazetteer:
    """data/geo/gemeinden_de.csv (Destatis municipalities with their seat PLZ, plus the PLZ GeoNames adds) and, when given, the
    GeoNames postal-code file DE.txt (CC BY 4.0, never committed): every PLZ with its Kreis, and the place names that are Ortsteile."""

    def __init__(self, csv_path=None, geonames=None):
        self.munis, self.by_ars = [], {}
        self.full1, self.full2, self.stem1 = {}, {}, {}
        self._plz = {}                       # plz -> {"kreise": set, "munis": set(ars), "land": set}
        self._place = {}                     # fold1(GeoNames place name) -> set((plz, kreis5))
        self.plz_of_muni = {}                # ars -> [plz] (seat first)
        self.by_kreis = {}
        self._sig = {}
        self.alias = {}
        self._load_destatis(csv_path or (_GEO / "gemeinden_de.csv"))
        if geonames:
            self._load_geonames(geonames)
        self._finish()
        self._load_aliases(_GEO / "clinic_town_overrides.json")

    def _load_aliases(self, path):
        """data/geo/clinic_town_overrides.json: the spellings and parts of towns the registry uses ('München-Flughafen' is Oberding,
        'Augsburg-Göggingen' is Augsburg) are alternate names of the municipality they name."""
        import json
        by_name = {m.name: m for m in self.munis if m.land == "BY"}
        self.alias = {}
        for k, v in json.load(open(path, encoding="utf-8")).items():
            if not k.startswith("_") and v in by_name:
                self.alias.setdefault(_fold1(k), []).append(by_name[v])

    # -- loading
    def _load_destatis(self, path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                plz = r["plz"]
                if r["source"] == "destatis":
                    m = Muni(r["ars"], r["gemeindename"], r["land"], r["ars"][:5], plz,
                             float(r["lat"]) if r["lat"] else None, float(r["lon"]) if r["lon"] else None)
                    self.munis.append(m)
                    self.by_ars[m.ars] = m
                    self.by_kreis.setdefault(m.kreis, []).append(m)
                    p = self._plz.setdefault(plz, {"kreise": set(), "munis": set(), "land": set()})
                    p["kreise"].add(m.kreis)
                    p["munis"].add(m.ars)
                    p["land"].add(m.land)
                    self.plz_of_muni.setdefault(m.ars, []).insert(0, plz)
                else:   # a GeoNames-only PLZ the table added: the Land is all it knows (the place is an institution name)
                    p = self._plz.setdefault(plz, {"kreise": set(), "munis": set(), "land": set()})
                    p["land"].add(r["land"])

    def _load_geonames(self, path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.reader(f, delimiter="\t"):
                plz, place, kreis5 = r[1], r[2], r[8]
                if not (re.fullmatch(r"\d{5}", plz) and re.fullmatch(r"\d{5}", kreis5)):
                    continue
                p = self._plz.setdefault(plz, {"kreise": set(), "munis": set(), "land": set()})
                p["kreise"].add(kreis5)
                p["land"].add(LAND_OF_AGS[kreis5[:2]])
                self._place.setdefault(_fold1(place), set()).add((plz, kreis5))

    def _finish(self):
        mk = {}
        for m in self.munis:
            norm = geo.normalize_name(m.name)
            stem = geo.bare_stem(norm)
            mk[m.ars] = (_fold1(norm), _fold1(stem) if stem else None)
            for idx, key in ((self.full1, mk[m.ars][0]), (self.full2, _fold2(m.name.split(",", 1)[0]))):
                idx.setdefault(key, []).append(m)
            if stem:
                self.stem1.setdefault(mk[m.ars][1], []).append(m)
            if norm.startswith("markt "):                          # 'Indersdorf' for 'Markt Indersdorf'
                self.stem1.setdefault(_fold1(geo.bare_stem(norm[6:])), []).append(m)
        # a municipality of a kreisfreie Stadt (Destatis ars ends 0000000) is its whole Kreis; PLZ -> municipality by name inside the Kreis
        kfree = {k: ms[0] for k, ms in self.by_kreis.items() if len(ms) == 1 and ms[0].ars.endswith("0000000")}
        for place, hits in self._place.items():
            for plz, kreis5 in hits:
                cands = [kfree[kreis5]] if kreis5 in kfree else [m for m in self.by_kreis.get(kreis5, ()) if place in mk[m.ars]]
                for m in cands:
                    self._plz[plz]["munis"].add(m.ars)
                    lst = self.plz_of_muni.setdefault(m.ars, [m.plz])
                    if plz not in lst:
                        lst.append(plz)

    # -- PLZ
    def plz_info(self, plz):
        p = self._plz.get(clean_plz(plz) or "")
        if not p:
            return PlzInfo(False, frozenset(), (), frozenset())
        return PlzInfo(True, frozenset(p["kreise"]), tuple(sorted((self.by_ars[a] for a in p["munis"]), key=lambda m: m.ars)), frozenset(p["land"]))

    # -- city
    def _region_hints(self, s):
        """(text without trailing region/country words, set of Regierungsbezirk digits, Land code or None). A word after a bare blank
        is cut only when the word before it is no connector: 'Weiden in der Oberpfalz' keeps its 'Oberpfalz'."""
        rbs, land = set(), None
        while True:
            m = _REGION_RE.match(s)
            if not m or not m.group("rest").strip(" ,/-("):
                break
            rest, word = m.group("rest").strip(), re.sub(r"\.$", "", m.group("word").lower())
            if re.fullmatch(r"\s*", m.group("sep")) and re.sub(r"[.]", "", rest.split()[-1].lower()) in CONNECTORS:
                break
            word = _strip_diacritics(word)
            if word in RB_WORDS:
                rbs.add(RB_WORDS[word])
            if word not in ("deutschland", "germany"):
                land = "BY"
            s = rest.strip(" ,/-(")
        return s, rbs, land

    def _lookup_name(self, text):
        k1, k2 = _fold1(text), _fold2(text)
        if k1 in self.alias:
            return list(self.alias[k1]), "exact"
        if k1 in self.full1:
            return list(self.full1[k1]), "exact"
        if k2 in self.full2:
            return list(self.full2[k2]), "ascii_fold"
        return [], None

    def _qualifier_lookup(self, text):
        """'Neuburg/Donau', 'Kempten (Allgäu)', 'Weiden in der Oberpfalz', 'München (Aubing)': the stem, then the qualifier against the
        official names of that stem; a qualifier no official name carries is a district of the stem municipality ('district_of')."""
        m = re.match(r"^\s*([^()/]+?)\s*(?:\(([^)]*)\)|/\s*(.+)|\s(?:in der|in|im|i\.\s*d\.|i\.|a\.\s*d\.|an der|am|a\.|bei|b\.|ob der|o\.\s*d\.)\s*(.+))\s*$", text, re.I)
        stem_txt, qual = (m.group(1), m.group(2) or m.group(3) or m.group(4) or "") if m else (text, "")
        cands = self.stem1.get(_fold1(stem_txt), [])
        if not cands:
            return [], None
        if qual:
            q = _fold1(qual)
            in_land = [c for c in cands if c.land == _LAND_OF_WORD.get(q)] if q in _LAND_OF_WORD else []
            if in_land:                                     # 'Neustadt in Sachsen': the qualifier names the Land, not a Bavarian town
                return in_land, "qualifier"
            alts = {q, QUAL_ALIAS.get(q, q)}
            hit = [c for c in cands if any(a and (_fold1(c.name.split(",", 1)[0]).endswith(a) or a in _fold1(geo.normalize_name(c.name))) for a in alts)]
            if hit:
                return hit, "qualifier"
            by = [c for c in cands if c.land == "BY"] or cands
            if len({c.ars for c in by}) == 1:
                return by, "district_of"
        return list(cands), "stem"

    def _tokens_lookup(self, text):
        text = LEGAL_FORM.sub("", text)
        toks = re.findall(r"[^\W\d_]+(?:[.'][^\W\d_]+)*", text)
        if len(toks) < 2:
            return [], None
        for n in range(len(toks) - 1, 0, -1):                       # the head: 'München Süd', 'Augsburg Göggingen'
            ms, rule = self._lookup_name(" ".join(toks[:n]))
            if ms:
                return ms, "head_token"
            ms = self.stem1.get(_fold1(" ".join(toks[:n])), [])
            if ms:
                return list(ms), "head_token"
        for n in range(len(toks) - 1, 0, -1):                       # the tail: 'Krankenhaus Barmherzige Brüder Regensburg'
            ms, rule = self._lookup_name(" ".join(toks[-n:]))
            if ms:
                return ms, "tail_token"
            ms = self.stem1.get(_fold1(" ".join(toks[-n:])), [])
            if ms:
                return list(ms), "tail_token"
        return [], None

    def _geonames_place(self, text):
        hits = self._place.get(_fold1(text), set())
        if not hits:
            return [], None, frozenset()
        kreise = frozenset(k for _, k in hits)
        ms = []
        for k in kreise:
            cs = self.by_kreis.get(k, [])
            if len(cs) == 1 and cs[0].ars.endswith("0000000"):
                ms.append(cs[0])
        return ms, "geonames_place", kreise

    def _resolve_one(self, text, rbs, land, plz):
        ms, rule = self._lookup_name(text)
        if ms and _fold1(text) == _fold1(geo.bare_stem(geo.normalize_name(text))):     # a bare name is also the stem of 'Weiden i.d.OPf.'
            ms = ms + [m for m in self.stem1.get(_fold1(text), []) if m not in ms]
        if not ms:
            ms, rule = self._qualifier_lookup(text)
        if not ms:
            ms, rule = self._tokens_lookup(text)
        if not ms:
            gms, grule, gk = self._geonames_place(text)
            if gk:
                if not gms:         # an Ortsteil: the Kreis is known, the municipality is not
                    return Resolution("kreis_only" if all(k.startswith("09") for k in gk) else "outside_bavaria", (), grule, kreise=gk,
                                      n_candidates=len(gk), raw=text)
                return Resolution("unique" if len({m.ars for m in gms}) == 1 else "ambiguous", tuple(gms), grule, kreise=gk,
                                  plz_main=gms[0].plz if len(gms) == 1 else None, n_candidates=len(gms), raw=text)
            return _UNRESOLVED(text)
        ms = list({m.ars: m for m in ms}.values())
        if rbs:
            narrowed = [m for m in ms if m.land == "BY" and m.rb in rbs]
            ms = narrowed or ms
        if land == "BY":
            ms = [m for m in ms if m.land == "BY"] or ms
        consistent = None
        info = self.plz_info(plz) if plz else None
        if info and info.valid and info.kreise and len(ms) >= 1:
            narrowed = [m for m in ms if m.kreis in info.kreise]
            consistent = bool(narrowed)
            if narrowed:
                ms = narrowed
        by = [m for m in ms if m.land == "BY"]
        if len(by) >= 1 and len(by) < len(ms):                       # Bavarian and elsewhere: the corpus is Bavarian, said by the rule
            ms, rule = by, "bavaria_prior"
        if not any(m.land == "BY" for m in ms):
            return Resolution("outside_bavaria", tuple(sorted(ms, key=lambda m: m.ars)), rule, kreise=frozenset(m.kreis for m in ms),
                               plz_consistent=consistent, n_candidates=len(ms), raw=text)
        ms = tuple(sorted(ms, key=lambda m: m.ars))
        uniq = len(ms) == 1
        return Resolution("unique" if uniq else "ambiguous", ms, rule, kreise=frozenset(m.kreis for m in ms),
                          plz_main=ms[0].plz if uniq else None, plzs=tuple(self.plz_of_muni.get(ms[0].ars, [ms[0].plz])) if uniq else (),
                          plz_consistent=consistent, n_candidates=len(ms), raw=text)

    def resolve(self, raw, plz=None) -> Resolution:
        """A city string in any spelling -> the municipality (Destatis AGS, seat PLZ, every known PLZ). `plz`, when given, narrows
        a name several municipalities share to those in the PLZ's Kreis. A leading '97688 Bad Kissingen' gives its own PLZ."""
        if raw is None:
            return Resolution("non_place", (), "empty", raw=None)
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if len(raw) == 1 else ", ".join(map(str, raw))
        s = _prep(raw)
        m = re.match(r"^(\d{5})\s+(\D.*)$", s)
        if m:
            plz, s = plz or m.group(1), m.group(2)
        low = _strip_diacritics(s.lower())
        if not s or low in NON_PLACE:
            return Resolution("non_place", (), "non_place", raw=raw)
        if low in EXONYMS_FOLDED:
            s = EXONYMS_FOLDED[low]
        if low in LAND_WORDS:
            return Resolution("region", (), "land_name", raw=raw)
        if self._lookup_name(s)[0]:                     # 'Markt Schwaben' is a municipality before 'Schwaben' is a region
            s, rbs, land = s, set(), None
        else:
            s, rbs, land = self._region_hints(s)
        if not s or _strip_diacritics(s.lower()) in NON_PLACE:
            return Resolution("non_place", (), "non_place", raw=raw)
        parts = list(dict.fromkeys(p.strip() for p in re.split(r"\s*[;|]\s*|\s*,\s*|\s+(?:und|&)\s+", s) if p.strip()))
        if len(parts) > 1:                              # a list of places ('Günzburg, Krumbach'), or one place said twice
            ok = [r for r in (self._resolve_one(p, rbs, land, plz) for p in parts) if r.status in ("unique", "ambiguous", "outside_bavaria")]
            ms = tuple(sorted({m.ars: m for r in ok for m in r.munis}.values(), key=lambda m: m.ars))
            if len(ms) > 1 and len(ok) >= 2:
                return Resolution("multi", ms, "multi", kreise=frozenset(m.kreis for m in ms), n_candidates=len(ms), raw=raw)
            if len(ok) >= 1 and len(ms) == len(ok[0].munis):
                return Resolution(**{**ok[0].__dict__, "raw": raw})
        return Resolution(**{**self._resolve_one(s, rbs, land, plz).__dict__, "raw": raw})

    def claim_signature(self, c):
        """place_signature of a Claim, with its settled municipality / Kreis; memoised (the same strings come back thousands of times)."""
        key = (c.plz, c.city, c.ars, c.kreis)
        hit = self._sig.get(key)
        if hit is None:
            if c.ars or c.kreis:
                m = self.by_ars.get(c.ars) if c.ars else None
                kreise = {m.kreis} if m else ({c.kreis} if c.kreis else set())
                hit = (None, None, {m.ars} if m else set(), kreise, 1, 1.0, False, {LAND_OF_AGS[k[:2]] for k in kreise})
            else:
                hit = self.place_signature(c.plz, c.city)
            self._sig[key] = hit
        return hit

    def place_signature(self, plz, city):
        """-> (valid plz or None, Resolution or None, municipalities (ARS set), Kreise (ARS[:5] set), number of candidate municipalities,
        factor of the rule that read the city, PLZ and city name different Kreise, Laender). The PLZ decides when the table knows its Kreis;
        a city name decides what the PLZ cannot (the municipality inside a Landkreis, or the whole place when the PLZ is unknown)."""
        p = clean_plz(plz)
        info = self.plz_info(p) if p else None
        pv = p if info and info.valid else None
        res = self.resolve(city, p) if city else None
        city_ok = res is not None and res.status in ("unique", "ambiguous", "multi", "outside_bavaria", "kreis_only")
        munis, kreise, n, factor, conflict = set(), set(), 1, 1.0, False
        cm, ck = ({m.ars for m in res.munis}, set(res.kreise)) if city_ok else (set(), set())
        if pv and info.kreise:
            kreise, munis = set(info.kreise), {m.ars for m in info.munis}
            if city_ok and ck and not (ck & kreise):
                conflict = True                                  # PLZ and city disagree: both stay as possible places (OR)
                munis, kreise = munis | cm, kreise | ck
                n, factor = max(res.n_candidates, 1), RULE_FACTOR.get(res.rule, 0.6)
            elif city_ok and not munis:
                munis = {a for a in cm if self.by_ars[a].kreis in kreise}
            elif city_ok and munis & cm:
                munis = munis & cm
        elif city_ok:
            munis, kreise = cm, ck
            n, factor = max(res.n_candidates, 1), RULE_FACTOR.get(res.rule, 0.6)
        lands = {self.by_ars[a].land for a in munis if a in self.by_ars} | {LAND_OF_AGS[k[:2]] for k in kreise}
        if pv and not lands:
            lands = set(info.land)
        return pv, res, munis, kreise, n, factor, conflict, lands


# ---------------------------------------------------------------------------------------------------------------------------
# text: the places a description names
# ---------------------------------------------------------------------------------------------------------------------------
def text_places(text):
    """[(kind, plz or None, city)] from running text, reusing the extractors pflege_jobs.verify already trusts for the same job:
    'Einsatzort: X' labels (text_einsatzort) and '12345 Ort' pairs (text_plz_ort). A bare place name is not read from prose here."""
    from pflege_jobs import verify as V
    out, seen = [], set()
    t = re.sub(r"[ \t]+", " ", str(text or ""))
    for m in V._EINSATZORT.finditer(t):
        if V._EINSATZORT_IDIOM.search(t[max(0, m.start() - 20):m.start()]):
            continue
        c = V._clean_city(m.group(1))
        if c and ("text_einsatzort", None, c) not in seen:
            seen.add(("text_einsatzort", None, c))
            out.append(("text_einsatzort", None, c))
    for m in V._PLZ_ORT.finditer(t):
        c = V._clean_city(m.group(2))
        if c and ("text_plz_ort", m.group(1), c) not in seen:
            seen.add(("text_plz_ort", m.group(1), c))
            out.append(("text_plz_ort", m.group(1), c))
    return out


# ---------------------------------------------------------------------------------------------------------------------------
# D. the board stamp
# ---------------------------------------------------------------------------------------------------------------------------
def board_stamps(gaz, rows, n_board_municipalities=None, detail=None):
    """rows: the postings of ONE board, each {'plz', 'city', 'own': [{'city', 'plz'}]} -- the board's structured place, and the places
    the posting names independently of it (its text, title, URL). A value is a board value when it repeats on at least two postings and
      stamp    more than half of ALL the carriers name another place of their own, one in another Kreis (kbo.de: 80538 Muenchen, TASK-68)
      suspect  no such evidence, but the value is the ONLY place on a board whose clinics span two or more municipalities
    Returns {value: 'stamp' | 'suspect'}; the value is the PLZ, or 'city:<AGS>' for a row with no PLZ. `detail`, a dict, receives
    {value: (carriers, carriers with an independent place, of those the ones that name another Kreis)} for every repeating value."""
    def key_of(r):
        p = clean_plz(r.get("plz"))
        if p:
            return p
        res = gaz.resolve(r.get("city")) if r.get("city") else None
        return f"city:{res.munis[0].ars}" if res and res.status == "unique" else None

    carriers = {}
    for r in rows:
        k = key_of(r)
        if k:
            carriers.setdefault(k, []).append(r)
    placed = sum(len(v) for v in carriers.values())
    out = {}
    for k, rs in carriers.items():
        if len(rs) < 2:
            continue
        sample = rs[0]
        _, _, _, kreise, _, _, _, _ = gaz.place_signature(sample.get("plz"), sample.get("city"))
        indep = contra = 0
        for r in rs:
            sigs = [gaz.place_signature(o.get("plz"), o.get("city")) for o in r.get("own") or []]
            sigs = [s for s in sigs if s[3] and s[4] == 1 and s[5] >= 0.9]      # a place read one way only: no token scan, no ambiguous name
            if not sigs:
                continue
            indep += 1
            if kreise and all(not (s[3] & kreise) for s in sigs):
                contra += 1
        if detail is not None:
            detail[k] = (len(rs), indep, contra)
        if contra * 2 > len(rs):
            out[k] = "stamp"
        elif not indep and len(carriers) == 1 and (n_board_municipalities or 0) >= 2 and len(rs) == placed:
            out[k] = "suspect"
    return out


# ---------------------------------------------------------------------------------------------------------------------------
# E. the match
# ---------------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Claim:
    kind: str
    plz: Optional[str] = None
    city: Optional[str] = None
    note: str = ""
    ars: Optional[str] = None        # a municipality already settled by the caller (the registry town with its Landkreis, geo.clinic_centroid)
    kreis: Optional[str] = None      # a Kreis key (ARS[:5]) with no place name (the registry Landkreis)


@dataclass
class Match:
    category: str                    # agree | agree_weak | disagree | unknown
    level: str                       # plz | municipality | kreis | none | unknown
    confidence: float
    best: Optional[tuple] = None     # (posting claim, clinic claim) that gave the level
    conflict: bool = False           # another own claim of the posting says the opposite
    detail: str = ""


def _level(a, b):
    """Level of agreement of two place signatures (Gazetteer.place_signature): same PLZ, same municipality, same Kreis, none (both are
    placed and share nothing), unknown (one of them is not placed)."""
    ap, _, am, ak, _, _, _, al = a
    bp, _, bm, bk, _, _, _, bl = b
    if ap and bp and ap == bp:
        return "plz"
    if am & bm:
        return "municipality"
    if ak & bk:
        return "kreis"
    if (ak and bk) or (al and bl and not (al & bl)):
        return "none"
    return "unknown"


_RANK = {"plz": 3, "municipality": 2, "kreis": 1, "none": 0, "unknown": -1}
_VERDICT = {"plz": "agree", "municipality": "agree", "kreis": "agree_weak", "none": "disagree", "unknown": "unknown"}


def match(gaz, posting_claims, clinic_claims) -> Match:
    """Confidence that a posting is at the place of a clinic. Score of one pair = weight(posting claim) x weight(clinic claim) x
    LEVEL_FACTOR[level] x resolution factors of the two readings / candidate municipalities of each. The CATEGORY follows the strongest
    resolved claim the posting makes about itself (not a stamp, not confirm-only); when it has none, a confirm-only claim that agrees gives
    agree_weak, anything else is unknown. A stamp is no evidence of where the job is."""
    psig = [(c, gaz.claim_signature(c)) for c in posting_claims]
    csig = [(c, gaz.claim_signature(c)) for c in clinic_claims]
    psig = [(c, s) for c, s in psig if s[2] or s[3] or s[7]]
    csig = [(c, s) for c, s in csig if s[2] or s[3] or s[7]]

    def score(pc, ps, cc, cs, lvl):
        return POSTING_WEIGHT[pc.kind] * CLINIC_WEIGHT[cc.kind] * LEVEL_FACTOR[lvl] * ps[5] * cs[5] / (ps[4] * cs[4])

    pairs = [(pc, ps, cc, cs, _level(ps, cs)) for pc, ps in psig for cc, cs in csig]
    conf = max((score(*p) for p in pairs), default=0.0)
    if not csig:
        return Match("unknown", "unknown", conf, detail="no place of the clinic")
    pool = [c for c, _ in psig if c.kind not in STAMP_KINDS and c.kind not in CONFIRM_ONLY]
    if not pool:
        sup = [p for p in pairs if p[0].kind in CONFIRM_ONLY and p[4] in ("plz", "municipality")]
        if sup:
            b = max(sup, key=lambda p: (_RANK[p[4]], score(*p)))
            return Match("agree_weak", b[4], score(*b), (b[0], b[2]), detail="confirm-only evidence")
        return Match("unknown", "unknown", conf, detail="no own place of the posting")
    top = max(POSTING_WEIGHT[c.kind] for c in pool)
    decisive = [c for c in pool if POSTING_WEIGHT[c.kind] == top]
    agree = {"agree", "agree_weak"}

    def best_of(c):
        return max((p for p in pairs if p[0] is c), key=lambda p: (_RANK[p[4]], score(*p)))

    per_claim = {id(c): best_of(c) for c in decisive}
    best = max(per_claim.values(), key=lambda p: (_RANK[p[4]], score(*p)))
    cat = _VERDICT[best[4]]
    n_agree = sum(1 for p in per_claim.values() if _VERDICT[p[4]] in agree)
    # equally strong readings of the posting's place that disagree with each other (the stored city says Bamberg, the adapter says
    # Forchheim): one reading agreeing is agree, but only n_agree / n of the confidence
    share = n_agree / len(decisive) if cat in agree else 1.0
    conflict = (cat in agree and n_agree < len(decisive)) or (cat != "unknown" and any(
        (_VERDICT[best_of(o)[4]] in agree) != (cat in agree) for o in pool if o not in decisive))
    own_conf = max((score(*p) for p in pairs if p[0] in pool), default=0.0)
    return Match(cat, best[4], own_conf * share if cat != "disagree" else 0.0, (best[0], best[2]), conflict,
                 "own claim not comparable with any place of the clinic" if cat == "unknown" else "")
