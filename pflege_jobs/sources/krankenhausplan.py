"""Bayerischer Krankenhausplan (StMGP, PDF) -> clinics registry rows.
Source: data/registry/krankenhausplan_2026.pdf, the 51. Fortschreibung (Stand 1.1.2026), from www.stmgp.bayern.de.
Only Teil II Abschnitt A (Plankrankenhäuser, per Regierungsbezirk) is parsed. KeZ = 5-digit site key = clinic_id.
Usage: python -m pflege_jobs.sources.krankenhausplan data/registry/krankenhausplan_2026.pdf out.csv (inspection dump;
the registry build is tools/registry_build.py)."""
import csv
import re
import sys

import pdfplumber

BEZIRKE = ["Oberbayern", "Niederbayern", "Oberpfalz", "Oberfranken", "Mittelfranken", "Unterfranken", "Schwaben"]
TRAEGER = {"Ö": "oeffentlich", "Fg": "freigemeinnuetzig", "P": "privat"}
VST = {"I": "Grundversorgung (I)", "II": "Schwerpunkt (II)", "III": "Maximalversorgung (III)", "F": "Fachkrankenhaus"}


def _dehyphen(t):
    """Line-break hyphens in pdfplumber's cell text: 'Aichach-\\nFriedberg' -> 'Aichach-Friedberg', 'psychia-\\ntrie' ->
    'psychiatrie'. Only at a line break: a hyphen inside a line is text ('für Kinder- und', 18002/26204 -- TASK-183
    found this rule turning it into 'Kinderund' after the line breaks had already become spaces)."""
    t = re.sub(r"(\w)-\n(?=[a-zäöüß])", r"\1", t)
    return re.sub(r"(\w)-\n(?=[A-ZÄÖÜ])", r"\1-", t)


def _clean(c):
    return re.sub(r"\s+", " ", _dehyphen(c or "")).strip()


STATUS = {"plan-kh": "Plan-KH", "vertrags-kh": "Vertrags-KH", "hs-klinik": "HS-Klinik", "bedarfsfeststellung": "Bedarfsfeststellung"}


def _status(cell):
    """The Status column wraps mid-word ('Vertra gs-KH', 'Bedarf sfests t.'). Canonical values only; anything else is
    kept verbatim so a new status is visible, not silently mapped."""
    t = re.sub(r"[\s\-]", "", (cell or "")).lower().replace("feststt", "feststellung").replace("festst.", "feststellung")
    for k, v in STATUS.items():
        if t.startswith(k.replace("-", "")):
            return v
    return _clean(cell)


def _paragraphs(words):
    """Words of a 'Krankenhaus / Standort' cell -> its paragraphs as text.

    pdfplumber words with keep_blank_chars=True, so a run carries the PDF's own space characters: a line whose source
    text went on after a space ends in one, a line broken inside a compound ('kbo-Heckscher-' / 'Klinikum') or after
    a bracketing dash ('Klinikum Nürnberg -' / 'Betriebsstätte Nord-') does not, and ''.join puts the lines back
    together exactly as typed. One line per 3pt band of `top`: 26103 mixes two font subsets whose tops differ by
    0.8pt ('Kinderk' + 'linik'); runs that touch (gap < 1.5pt) are one word. A paragraph break is a line gap > 14pt:
    lines sit 9.7pt apart, the paragraphs of the 2026 plan (name, Standort, Träger, the EIN-Krankenhaus note) 17.7-29pt
    (measured over all 401 rows, TASK-183). Blank-only runs are the PDF's empty lines between paragraphs."""
    lines = []
    for w in sorted((w for w in words if w["text"].strip()), key=lambda w: w["top"]):
        if lines and w["top"] - lines[-1][0]["top"] < 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    paras, prev = [], None
    for line in lines:
        line.sort(key=lambda w: w["x0"])
        text = line[0]["text"]
        for a, b in zip(line, line[1:]):
            text += ("" if b["x0"] - a["x1"] < 1.5 else " ") + b["text"]
        if prev is None or line[0]["top"] - prev > 14:
            paras.append("")
        paras[-1] += text
        prev = line[0]["top"]
    return [_clean(p) for p in paras]


def _cell(words, name_x1, status_x1):
    """Words of one table row from the name column's left edge to the page edge -> (name, town, operator, status).

    The 51. Fortschreibung (2026) prints name, Standort and Träger as separate paragraphs of the 'Krankenhaus /
    Standort' cell, an 'EIN-Krankenhaus im Sinne des KHG mit <KeZ>' note as a 4th; a cell of any other shape fails
    loudly (older editions are not read: the 50. Fortschreibung sets 3 cells without paragraph gaps; in the 49.
    pdfplumber's table finder cuts KeZ 18802's row into sub-rows). A word belongs to the column it starts in: long
    words run past the name column's edge into the Status column, where pdfplumber's cell text cut them
    ('Berufsgenossenschaftl' + 'icPhlaen', 18007) -- TASK-183."""
    paras = _paragraphs([w for w in words if w["x0"] < name_x1])
    if not (len(paras) == 3 or len(paras) == 4 and paras[3].startswith("EIN-Krankenhaus")):
        raise ValueError(f"name cell is not name / Standort / Träger [/ EIN-Krankenhaus] paragraphs: {paras}")
    status = _status(" ".join(w["text"] for w in words if name_x1 <= w["x0"] < status_x1))
    return paras[0], paras[1], paras[2], status


def _int(cell):
    """'1.020' -> 1020. The PDF prints counts >= 1000 with a German thousands dot; a bare isdigit() check
    turned every such cell into None, i.e. exactly the biggest hospitals lost their beds (TASK-167).
    '-' / '' -> None; anything else non-numeric raises, so a new cell format is loud, not silently NULL."""
    t = _clean(cell)
    if t in ("", "-"):
        return None
    if not re.fullmatch(r"\d{1,3}(\.\d{3})*", t):
        raise ValueError(f"unexpected count cell {cell!r}")
    return int(t.replace(".", ""))


def _edition(pdf):
    """Read 'Stand: 1. Januar <year> (<n>. Fortschreibung)' off the cover so `source` is never stale."""
    head = (pdf.pages[0].extract_text() or "") + (pdf.pages[1].extract_text() or "" if len(pdf.pages) > 1 else "")
    y = re.search(r"Stand:\s*1\.\s*Januar\s*(\d{4})", head)
    n = re.search(r"\((\d+)\.\s*Fortschreibung\)", head)
    if y and n:
        return "Krankenhausplan Bayern %s (%s. Fortschreibung), StMGP" % (y.group(1), n.group(1))
    return "Krankenhausplan Bayern, StMGP"


def _bezirk(text):
    """Regierungsbezirk heading of a table page: its first line, or its second when the page also opens
    the part -- the first Oberbayern page starts 'Teil II Abschnitt A - Plankrankenhäuser' (TASK-178:
    reading only line 1 skipped that page, and with it 16101 Klinikum Ingolstadt and 16102)."""
    return next((line.strip() for line in text.split("\n", 2)[:2] if line.strip() in BEZIRKE), None)


def _carry_landkreis(rows):
    """The 51. Fortschreibung (2026) prints the Landkreis only on the first row of each Landkreis group and
    '-' or nothing on the rows below it (281 of 401 rows; the 2025 edition left 12 blank). A blank row takes
    the name of the row above, but only inside its own group -- the KeZ's first three digits, which map to
    exactly one Landkreis name in both editions (95 groups in 2026). A blank row that opens a group has no
    name to take and fails loudly (TASK-175: the blanks read as 260 DB deviations)."""
    last = None
    for r in rows:
        if r["landkreis"] in ("", "-"):
            if not last or last[0] != r["clinic_id"][:3]:
                raise ValueError(f"KeZ {r['clinic_id']}: blank Landkreis cell and no row above it in its group")
            r["landkreis"] = last[1]
        else:
            last = (r["clinic_id"][:3], r["landkreis"])
    return rows


def parse(pdf_path):
    pdf = pdfplumber.open(pdf_path)
    source = _edition(pdf)
    rows, bezirk = [], None
    for page in pdf.pages:
        text = page.extract_text() or ""
        head = text.split("\n", 1)[0].strip()
        bezirk = _bezirk(text) or bezirk
        if not bezirk or "KeZ" not in text[:400]:
            continue
        if "Abschnitt B" in text[:200] or "Berufsfachschulen" == head:
            break
        for table in page.find_tables():
            for row, r in zip(table.rows, table.extract()):
                if not r or len(r) < 13 or not re.fullmatch(r"\d{5}", _clean(r[1])):
                    continue
                n, s = row.cells[2], row.cells[3]
                words = page.crop((n[0], n[1], page.width, n[3])).extract_words(
                    x_tolerance=1.5, y_tolerance=0.5, keep_blank_chars=True)
                name, town, operator, status = _cell(words, n[2], s[2])
                vst = _clean(r[4]); tr = _clean(r[5])
                beds = _int(r[6]); places = _int(r[7]); fach = _clean(r[12])
                rows.append({
                    "clinic_id": _clean(r[1]), "name": name, "town": town, "operator": operator,
                    "landkreis": _clean(r[0]), "regierungsbezirk": bezirk,
                    "status": status, "versorgungsstufe": VST.get(vst, vst or None), "traegerart": TRAEGER.get(tr, tr or None),
                    "beds": beds, "day_places": places,
                    "fachrichtungen": fach.replace(" ", "").replace(",", "|") if fach and fach != "-" else None,
                    "parse_quality": "ok", "source": source,
                })
    return _carry_landkreis(rows)


_LEGAL_WORD = re.compile(r"\b(GmbH|gGmbH|mbH|AG|KG|OHG|Stiftung|KdöR|Zweckverband)\b")


def validate(rows):
    """Error-rate report for a parse() result, independent of each row's own `parse_quality` flag (TASK-136).
    Runs the SAME implausibility check TASK-133 proposes at registry load time, but here -- at extraction time --
    so a corrupted Fortschreibung is caught before it reaches the registry. Returns a summary dict; does not raise.
    _LEGAL_WORD requires the legal-form token as its own capitalized word, so the "stadt" inside an ordinary town
    name ("Ingolstadt", "Neustadt", "Immenstadt") is not one.
    """
    town_missing = [r["clinic_id"] for r in rows if not r.get("town")]
    town_bad = [r["clinic_id"] for r in rows if r.get("town") and
                (_LEGAL_WORD.search(r["town"]) or re.search(r"\d", r["town"]) or len(r["town"].split()) > 5)]
    quality_partial = [r["clinic_id"] for r in rows if r.get("parse_quality") != "ok"]
    return {"total": len(rows), "town_missing": town_missing, "town_implausible": town_bad,
            "parse_quality_partial": quality_partial,
            "error_rate": round(len(set(town_missing) | set(town_bad) | set(quality_partial)) / len(rows), 3) if rows else 0.0}


if __name__ == "__main__":
    rows = parse(sys.argv[1])
    with open(sys.argv[2], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    report = validate(rows)
    print(f"validate: {report['error_rate']*100:.1f}% error rate "
          f"({len(report['town_missing'])} no town, {len(report['town_implausible'])} implausible town, "
          f"{len(report['parse_quality_partial'])} parse_quality!=ok) -- flagged ids:",
          sorted(set(report["town_missing"]) | set(report["town_implausible"]) | set(report["parse_quality_partial"])))
    print(f"{len(rows)} sites; per Bezirk:", {b: sum(1 for r in rows if r['regierungsbezirk'] == b) for b in BEZIRKE})
