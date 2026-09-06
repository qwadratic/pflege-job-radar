"""Link postings to Krankenhausplan sites (clinic_id = KeZ).

Deterministic, rule-recorded, conservative (no match beats a wrong match):
  R1 exact      normalized employer name == normalized site name            score 1.0
  R2 operator   normalized employer name == normalized operator, unique site or site town == posting city   0.95 / 0.9
  R3 tokens     significant-token Jaccard >= 0.6 between employer and site name AND town == city          0.8
  R4 tokens_op  same against operator name AND town == city                                              0.75
  R5 loose      Jaccard >= 0.5 with town == city and the site is the only candidate in that town          0.6
Never links when >1 candidate survives a rule. Manual overrides: set postings.clinic_match_rule='manual' (untouched by re-runs).
"""
import re
from collections import defaultdict

from .schema import CLINIC_SPEC

from .classify import employer_norm, norm_text

STOP = {"klinik", "kliniken", "klinikum", "krankenhaus", "gmbh", "ggmbh", "ag", "kg", "ev", "e", "v", "gku", "aör", "aoer",
        "stiftung", "gemeinnützige", "gemeinnuetzige", "und", "der", "des", "die", "für", "fuer", "im", "am", "an", "in", "von", "st", "sankt",
        "fachklinik", "fachkliniken", "gesundheit", "medizinisches", "zentrum", "personalabteilung", "bereich", "campus", "standort", "haus", "recht", "rechts" if False else "recht", "stadt", "landkreis", "kreis", "bezirk", "des", "öffentlichen", "anstalt", "körperschaft"}
CITY_ALIASES = {"münchen": {"münchen", "muenchen", "munich"}, "nürnberg": {"nürnberg", "nuernberg"}}


ALIASES = {"universitätsklinikum": {"universität"}, "uniklinikum": {"universität"}, "uniklinik": {"universität"},
           "lmu": {"ludwig", "maximilians"}, "tum": {"technischen"}, "technische": {"technischen"}, "fau": {"friedrich", "alexander"},
           "adör": set(), "aör": set(), "anstalt": set(), "öffentlichen": set(), "rechts": {"rechts"}, "betriebsstätte": set(), "gku": set(), "ku": set()}


def toks(s):
    s = norm_text(s or "")
    s = re.sub(r"[^\wäöüß ]", " ", s)
    out = set()
    for t in s.split():
        if len(t) <= 2 and t not in ("ku",) or t in STOP: continue
        if t in ALIASES: out |= ALIASES[t]
        else: out.add(t)
    return out


KINDS = {"klinik", "kliniken", "klinikum", "krankenhaus", "krankenhäuser", "kreisklinik", "kreiskliniken", "kreiskrankenhaus", "fachklinik",
         "bezirksklinikum", "bezirkskrankenhaus", "universitätsklinikum", "uniklinikum", "hospital", "spital", "klinikverbund"}


def kinds(s):
    return {t for t in re.sub(r"[^\wäöüß ]", " ", norm_text(s or "")).split() if t in KINDS}


def overlap(a, b):
    """overlap coefficient: |A∩B| / min(|A|,|B|) — robust to long legal suffixes on either side."""
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def city_key(c):
    c = norm_text(c or "")
    c = re.sub(r"^\d{5}\s+", "", c)                # '82467 Garmisch-Partenkirchen'
    c = re.sub(r"\s*(,|\().*$", "", c)            # 'Landshut, Isar' -> 'landshut'
    c = re.sub(r"\b(an der|am|im|bei|a\.d\.|i\.d\.)\b.*$", "", c).strip()
    for k, al in CITY_ALIASES.items():
        if c in al: return k
    return c.split()[0] if c else ""


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


# Explicit tie-breaks for multi-site operators where bed counts mislead (HS-Kliniken have no Plan beds).
SITE_PREFERENCE = {("16291", "16292"): "16291",                     # TUM: Rechts der Isar over Deutsches Herzzentrum
                   ("16290", "16291", "16292"): "16290"}            # generic "Klinikum der Universität München": LMU (largest)


def _pick_site(cands):
    ids = tuple(sorted(x["clinic_id"] for x in cands))
    pref = SITE_PREFERENCE.get(ids)
    if pref: return next(x for x in cands if x["clinic_id"] == pref)
    return max(cands, key=lambda x: x.get("beds") or 0)


class Matcher:
    def __init__(self, clinics):
        self.clinics = clinics
        self.by_name = defaultdict(list); self.by_op = defaultdict(list); self.by_town = defaultdict(list)
        for c in clinics:
            self.by_name[employer_norm(c["name"])].append(c)
            if c.get("operator"): self.by_op[employer_norm(c["operator"])].append(c)
            self.by_town[city_key(c.get("town"))].append(c)
            tk = set(city_key(c.get("town")).split("-")) | toks(c.get("town"))
            c["_ntoks"] = toks(c["name"]) - tk; c["_otoks"] = toks(c.get("operator")) - tk; c["_kinds"] = kinds(c["name"]) | kinds(c.get("operator"))

    def match(self, employer, city):
        en = employer_norm(employer or ""); et = toks(employer); ck = city_key(city)
        if not en: return None
        c = self.by_name.get(en, [])
        if len(c) == 1: return c[0]["clinic_id"], "R1_exact", 1.0
        if len(c) > 1:
            t = [x for x in c if city_key(x.get("town")) == ck]
            if len(t) == 1: return t[0]["clinic_id"], "R1_exact_town", 0.98
        c = self.by_op.get(en, [])
        if len(c) == 1: return c[0]["clinic_id"], "R2_operator", 0.95
        if len(c) > 1:
            t = [x for x in c if city_key(x.get("town")) == ck]
            if len(t) == 1: return t[0]["clinic_id"], "R2_operator_town", 0.9
            if len(t) > 1:
                top = _pick_site(t)
                return top["clinic_id"], "R6_ambiguous_sites:" + ",".join(sorted(x["clinic_id"] for x in t)), 0.5
        same_town = self.by_town.get(ck, []) if ck else []
        et = et - set(ck.split("-")); ek = kinds(employer)
        if not et and not ek: return None
        for rule, key, thr, score in (("R3_tokens", "_ntoks", 0.6, 0.8), ("R4_tokens_op", "_otoks", 0.6, 0.75)):
            cands = []
            for x in same_town:
                if x[key]:
                    o = overlap(et, x[key]); shared = len(et & x[key])
                    ok = o >= 0.8 or (o >= thr and shared >= 2)
                else:                                   # site name is just kind + town ("Klinikum Fürth"): need matching kind
                    ok = bool(ek & x["_kinds"]) and not et - ek  # employer carries no other distinguishing tokens
                    if not ok and ek & x["_kinds"] and len(et) <= 1: ok = True   # e.g. 'Klinikum Fürth Personalabteilung' (stopword) / 'Klinikum Fürth AöR'
                cands.append((ok, x))
            best = [x for ok, x in cands if ok]
            if len(best) == 1: return best[0]["clinic_id"], rule, score
            if len(best) > 1:
                # prefer the candidate whose own tokens are all covered (exact-er), else same-operator sites -> R6
                full = [x for x in best if x[key] <= et]
                if len(full) == 1: return full[0]["clinic_id"], rule + "_full", score - 0.05
                js = sorted(((jaccard(et, x[key]), x) for x in best), key=lambda t: -t[0])
                if js[0][0] - js[1][0] >= 0.1: return js[0][1]["clinic_id"], rule + "_bestj", score - 0.1
                ops = {employer_norm(x.get("operator") or x["name"]) for x in best}
                if len(ops) == 1:
                    top = _pick_site(best)
                    return top["clinic_id"], "R6_ambiguous_sites:" + ",".join(sorted(x["clinic_id"] for x in best)), 0.5
        cands = [(overlap(et, x["_ntoks"] | x["_otoks"]), x) for x in same_town]
        best = [x for j, x in cands if j >= 0.5]
        if len(best) == 1 and len(same_town) == 1: return best[0]["clinic_id"], "R5_loose", 0.6
        return None


def link_postings(postings, clinics):
    m = Matcher(clinics)
    out = []
    for p in postings:
        r = m.match(p.get("employer"), p.get("city"))
        if r:
            out.append({"posting_id": p["posting_id"], "clinic_id": r[0], "clinic_match_rule": r[1], "clinic_match_score": r[2]})
    return out

# Columns that ATS discovery owns but that also appear in the registry CSV.
DISCOVERY_OWNED = ("ats_type", "careers_url")


def full_clinic_rows(clinics, live):
    """Return complete clinic rows, ready to push, with discovery-owned columns preserved.

    The ingest upsert builds its recordset from a fixed column list and assigns *every* column, so a
    payload that omits a key sends NULL for it. Pushing `{clinic_id, name, ats_type}` therefore wipes
    beds, town, Fachrichtungen and the rest — this has bitten us twice. Always send the whole row.
    """
    by_id = {c.get("clinic_id"): c for c in (live or [])}
    out = []
    for c in clinics:
        cur = by_id.get(c.get("clinic_id")) or {}
        row = {k: c.get(k, cur.get(k)) for k, _ in CLINIC_SPEC}
        row["clinic_id"] = c.get("clinic_id")
        for f in DISCOVERY_OWNED:
            row[f] = (c.get(f) or "").strip() or (cur.get(f) or "")
        out.append(row)
    return out


def merge_discovered(clinics, live, fields=DISCOVERY_OWNED):
    """Fill discovery-owned columns from the DB so a registry push is never lossy.

    The ingest upsert assigns every column of its recordset, and json_to_recordset turns a *missing*
    key into NULL just like an empty string. So both "omit the column" and "send the CSV blank"
    erase whatever ATS discovery found. The only safe push sends an explicit value: the CSV's, or
    the one already stored. Mutates and returns `clinics`.
    """
    by_id = {c.get("clinic_id"): c for c in (live or [])}
    for c in clinics:
        cur = by_id.get(c.get("clinic_id")) or {}
        for f in fields:
            c[f] = (c.get(f) or "").strip() or (cur.get(f) or "")
    return clinics
