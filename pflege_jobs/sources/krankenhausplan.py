"""Bayerischer Krankenhausplan (StMGP, PDF) -> clinics registry rows.
Source: https://www.stmgp.bayern.de/wp-content/uploads/2025/02/bayerischer-krankenhausplan-2025.pdf (Stand 1.1.2025)
Only Teil II Abschnitt A (Plankrankenhäuser, per Regierungsbezirk) is parsed. KeZ = 5-digit site key = clinic_id.
Usage: python -m pflege_jobs.sources.krankenhausplan data/registry/krankenhausplan_2026.pdf out.csv [towns.csv]
(optional 3rd arg: a CSV with a `town` column -- e.g. the current clinics.csv -- used for town recovery)"""
import csv
import re
import sys

import pdfplumber

BEZIRKE = ["Oberbayern", "Niederbayern", "Oberpfalz", "Oberfranken", "Mittelfranken", "Unterfranken", "Schwaben"]
TRAEGER = {"Ö": "oeffentlich", "Fg": "freigemeinnuetzig", "P": "privat"}
VST = {"I": "Grundversorgung (I)", "II": "Schwerpunkt (II)", "III": "Maximalversorgung (III)", "F": "Fachkrankenhaus"}


def _dehyphen(t):
    """PDF line-break hyphens: 'Heckscher- Klinikum' -> 'Heckscher-Klinikum', 'psychia- trie' -> 'psychiatrie'."""
    t = re.sub(r"(\w)- (?=[a-zäöüß])", r"\1", t)
    return re.sub(r"(\w)- (?=[A-ZÄÖÜ])", r"\1-", t)


def _clean(c):
    return _dehyphen(re.sub(r"\s+", " ", (c or "").replace("\n", " ")).strip())


STATUS = {"plan-kh": "Plan-KH", "vertrags-kh": "Vertrags-KH", "hs-klinik": "HS-Klinik", "bedarfsfeststellung": "Bedarfsfeststellung"}


def _status(cell):
    """PDF cells break mid-word ('Vertra gs-KH', 'Bedarf sfests t.', 'icPhlaen - KH' = 'Plan-KH' with a wrapped
    footnote). Canonical values only; anything else is kept verbatim so a new status is visible, not silently mapped."""
    t = re.sub(r"[\s\-]", "", (cell or "")).lower().replace("feststt", "feststellung").replace("festst.", "feststellung")
    t = re.sub(r"(gen|aft|ie)$", "", t)                  # wrapped footnote fragments glued to the cell
    if t == "icphlaenkh":                               # column-interleaved 'Plan-KH'
        t = "plankh"
    for k, v in STATUS.items():
        if t.startswith(k.replace("-", "")):
            return v
    return _clean(cell)


KNOWN_TOWNS = set()   # optional: normalized AA city names for town recovery (set by parse())


def _norm_town(t):
    t = re.sub(r"\s+", " ", t.replace("a.d.", "an der").replace("a. d.", "an der").replace("a. d. ", "an der ").replace("i.d.", "in der").replace(" b. ", " bei ")).strip().lower()
    return t


def _recover_town(name_words, town_line):
    """Try suffixes of name+townline (1-4 words) against known towns: 'Bad Neustadt a.d. Saale' etc."""
    words = name_words + ([town_line] if town_line else [])
    for n in (5, 4, 3, 2, 1):
        if len(words) >= n:
            cand = re.sub(r"-\s+", "-", " ".join(words[-n:])).rstrip("-")
            for variant in (cand, re.sub(r"\ba\.\s*(d\.)?\s*", "an der ", cand)):
                if _norm_town(variant) in KNOWN_TOWNS:
                    return variant, words[:-n]
    return None, None


LEGAL_TAIL = re.compile(r"(GmbH|gGmbH|mbH|AG|KG|OHG|e\.\s?V\.|KdöR|K\.d\.ö\.R\.|Stiftung|Kommunalunternehmen|"
                        r"Freistaat Bayern|Bezirk\b|Landkreis\b|Stadt\b|Zweckverband|gemeinnützige)", re.I)


def _split_name_block(cell):
    """'Name / Standort / [Träger] / <Trägername> / EIN-Krankenhaus ...' -> name, town, operator.

    Two layouts in the wild: up to the 50. Fortschreibung (2025) a literal 'Träger' line separates
    the operator; from the 51. (2026) that separator is gone and the block is purely positional
    (name lines, then the town, then the operator lines). Detect the separator, and when it is
    absent fall back to splitting on the town line — which _split_after_town resolves below.
    """
    lines = [l.strip() for l in (cell or "").split("\n") if l.strip()]
    name, town, operator = [], None, []
    has_sep = "Träger" in lines
    mode = "name"
    for l in lines:
        if l == "Träger":
            mode = "traeger"; continue
        if l.startswith("EIN-Krankenhaus") or l.startswith("Sinne des KHG") or re.match(r"^[\d, ]+$", l):
            mode = "skip"; continue
        if mode == "name":
            name.append(l)
        elif mode == "traeger":
            operator.append(l)
    if not has_sep and len(name) >= 2:
        # positional layout. Re-join lines the PDF wrapped mid-word first ("kbo-Heckscher-" +
        # "Klinikum Ingolstadt"), otherwise the town search below sees fragments, not lines.
        merged = []
        for l in name:
            if merged and merged[-1].endswith("-"):
                merged[-1] = merged[-1] + l
            else:
                merged.append(l)
        name = merged
        joined = [_dehyphen(x) for x in name]
        cut = None
        for i in range(1, len(joined)):
            cand = joined[i]
            if _norm_town(cand) in KNOWN_TOWNS and not LEGAL_TAIL.search(cand):
                cut = i; break
        if cut is None:                      # no known town: operator starts at the first legal-form line
            for i in range(1, len(joined)):
                if LEGAL_TAIL.search(joined[i]):
                    cut = max(1, i - 1) if _norm_town(joined[i - 1]) in KNOWN_TOWNS else i
                    break
        if cut is not None and joined:
            standort = joined[cut] if _norm_town(joined[cut]) in KNOWN_TOWNS else None
            if standort:
                # The line between name and operator is the *Standort*. For multi-site operators it
                # is a site label that happens to be a place ("München Klinik" / "Schwabing"), and
                # the site belongs in the name; the actual town then comes from the operator line's
                # leading place ("Ingolstadt Danuvius Klinik GmbH") or from the Landkreis column.
                name, operator, town = name[:cut], name[cut + 1:], standort
                if operator:
                    lead = _dehyphen(operator[0])
                    for n_words in (3, 2, 1):
                        head = " ".join(lead.split()[:n_words])
                        if _norm_town(head) in KNOWN_TOWNS and not LEGAL_TAIL.search(head):
                            # operator line starts with the real town -> Standort was a site label
                            name = name + [standort]
                            town = head
                            # the town prefixes the operator's own name ("München Klinik gGmbH"):
                            # keep the full operator string, it is not a duplicate of the town
                            break
            else:
                name, operator = name[:cut], name[cut:]
    # university hospitals: no 'Träger' line; the last line is 'Freistaat Bayern'
    if name and name[-1] == "Freistaat Bayern":
        operator = ["Freistaat Bayern"]; name = name[:-1]
    # town = last line of name block that looks like a place (no digits, <= 4 words); repair hyphen/wrap splits via known towns
    if len(name) >= 2 and not re.search(r"\d", name[-1]) and len(name[-1].split()) <= 4:
        town_line = name[-1]; head = " ".join(name[:-1]).split()
        rec, rest = _recover_town(head, town_line)
        if rec:
            town = rec; name = [" ".join(rest)]
        elif head and head[-1].endswith("-") and (head[-1] + town_line) in " ".join(head):
            town = head[-1] + town_line; name = [" ".join(head[:-1])]
        else:
            town = town_line; name = [" ".join(head).rstrip("- ")]
    elif len(name) == 1 and operator == ["Freistaat Bayern"]:
        for t in ("München", "Regensburg", "Erlangen", "Würzburg", "Augsburg"):
            if t in name[0]: town = t
    name_s, op_s = _dehyphen(" ".join(name)), _dehyphen(" ".join(operator))
    quality = "ok"
    if not operator:                       # Vertrags-KH layout: no 'Träger' separator; try to split legal form
        m = re.search(r"^(.*?)\s+((?:\S+\s+){0,4}\S+\s+(?:GmbH|gGmbH|AG|KG|e\.V\.|KdöR|K\.d\.ö\.R\.|Stiftung).*)$", name_s)
        if m: name_s, op_s = m.group(1), m.group(2)
        quality = "partial"
        if town and re.fullmatch(r"(GmbH|gGmbH|AG|KG|e\.V\.|KdöR)", town): town = None
        w = name_s.split()
        if len(w) >= 2 and w[-1] == w[-2]: name_s = " ".join(w[:-1])   # 'Adula-Klinik Oberstdorf Oberstdorf'
        if not town and len(w) >= 2 and not re.search(r"\d", w[-1]): town = w[-1]
    return name_s, town, op_s or None, quality


def _edition(pdf):
    """Read 'Stand: 1. Januar <year> (<n>. Fortschreibung)' off the cover so `source` is never stale."""
    head = (pdf.pages[0].extract_text() or "") + (pdf.pages[1].extract_text() or "" if len(pdf.pages) > 1 else "")
    y = re.search(r"Stand:\s*1\.\s*Januar\s*(\d{4})", head)
    n = re.search(r"\((\d+)\.\s*Fortschreibung\)", head)
    if y and n:
        return "Krankenhausplan Bayern %s (%s. Fortschreibung), StMGP" % (y.group(1), n.group(1))
    return "Krankenhausplan Bayern, StMGP"


def parse(pdf_path, known_towns=None):
    if known_towns: KNOWN_TOWNS.update(_norm_town(t) for t in known_towns if t)
    pdf = pdfplumber.open(pdf_path)
    source = _edition(pdf)
    rows, bezirk = [], None
    for page in pdf.pages:
        text = page.extract_text() or ""
        head = text.split("\n", 1)[0].strip()
        if head in BEZIRKE:
            bezirk = head
        if not bezirk or "KeZ" not in text[:400]:
            continue
        if "Abschnitt B" in text[:200] or "Berufsfachschulen" == head:
            break
        for t in page.extract_tables():
            for r in t:
                if not r or len(r) < 13 or not re.fullmatch(r"\d{5}", _clean(r[1])):
                    continue
                name, town, operator, quality = _split_name_block(r[2])
                status = _status(r[3]); vst = _clean(r[4]); tr = _clean(r[5])
                beds = _clean(r[6]); places = _clean(r[7]); fach = _clean(r[12])
                rows.append({
                    "clinic_id": _clean(r[1]), "name": name, "town": town, "operator": operator,
                    "landkreis": _clean(r[0]), "regierungsbezirk": bezirk,
                    "status": status, "versorgungsstufe": VST.get(vst, vst or None), "traegerart": TRAEGER.get(tr, tr or None),
                    "beds": int(beds) if beds.isdigit() else None, "day_places": int(places) if places.isdigit() else None,
                    "fachrichtungen": fach.replace(" ", "").replace(",", "|") if fach and fach != "-" else None,
                    "parse_quality": quality, "source": source,
                })
    return rows


if __name__ == "__main__":
    towns = None
    if len(sys.argv) > 3:                       # optional CSV with a `town` column -> town recovery
        towns = {r.get("town") for r in csv.DictReader(open(sys.argv[3], encoding="utf-8"))}
    rows = parse(sys.argv[1], towns)
    with open(sys.argv[2], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print(f"{len(rows)} sites; per Bezirk:", {b: sum(1 for r in rows if r['regierungsbezirk'] == b) for b in BEZIRKE})
