"""TASK-175 one-off: explain the registry's current deviations from the Krankenhausplan 2026 parse with the
hand edits data/registry/clinics.csv recorded in git, as pflege_jobs.corrections rows (tools/ledger.py shape).
Writes a JSON file only; inserting it is Ivan's call.

Input: the report of tools/registry_build.py. For every UNEXPLAINED Krankenhausplan deviation (clinic, field,
DB value) the CSV's git history says where the DB value came from:
  * a later commit set it (a hand edit)  -> the commit and its backlog task (HAND_EDIT_TASK) explain it;
  * the first registry build set it      -> data/sync_krankenhausplan_2026.py kept the 2025 PDF's reading of
    name/town/operator because the 2026 cell no longer splits (no 'Träger' line since the 51. Fortschreibung).
Either way the row is a parse_error (the DB holds the source's text, our parser misreads the cell) only if
the 2026 PDF's own Krankenhaus/Standort cell still states the DB value word for word. Everything else --
other fields, a DB value the committed CSV never had, a value the 2026 cell no longer states -- goes to
`unexplained` with the reason: those are the registry build's proposals, not explanations.

  .venv/bin/python tools/registry_build.py --report deviations.json
  .venv/bin/python data/backfill_registry_corrections.py deviations.json backfill.json
"""
import collections
import csv
import io
import json
import os
import re
import subprocess
import sys

import pdfplumber

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pflege_jobs.sources import krankenhausplan as K  # noqa: E402

CSV = "data/registry/clinics.csv"
PDF_2026 = os.path.join(ROOT, "data", "registry", "krankenhausplan_2026.pdf")
PDF_2025 = os.path.join(ROOT, "data", "registry", "krankenhausplan_2025.pdf")
FIRST = "391d82f"          # first commit: the CSV as data/sync_krankenhausplan_2026.py wrote it
CELL_FIELDS = ("name", "town", "operator")
# The backlog task behind each commit that hand-edited name/town/operator (read in each task's notes):
HAND_EDIT_TASK = {
    "38287cc": "TASK-57",    # 46170 Bruderwald Vertrags-KH twin: name/operator re-read, partial -> ok
    "a5c01d6": "TASK-86",    # AC#2: 47503's mashed name/town/operator row repaired
    "14cacc4": "TASK-131",   # all 29 parse_quality='partial' rows corrected from their own 2026 PDF cell
}
BY = "backfill from the git history of data/registry/clinics.csv, claude session 663542db (TASK-175)"


def history():
    """{(clinic_id, field): [(sha, date, subject, value), ...]} -- the value at every commit that changed it."""
    log = subprocess.run(["git", "-C", ROOT, "log", "--follow", "--reverse", "--format=%h|%aI|%s", "--", CSV],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    out = {}
    for line in log:
        sha, date, subject = line.split("|", 2)
        text = subprocess.run(["git", "-C", ROOT, "show", f"{sha}:{CSV}"], capture_output=True, text=True, check=True).stdout
        for r in csv.DictReader(io.StringIO(text)):
            for f, v in r.items():
                h = out.setdefault((r["clinic_id"], f), [])
                if not h or h[-1][3] != v:
                    h.append((sha, date, subject, v))
    return out


def cells(pdf_path):
    """{KeZ: the raw Krankenhaus/Standort cell}, over the same pages parse() reads."""
    out = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if "KeZ" not in text[:400]:
                continue
            if "Abschnitt B" in text[:200] or text.split("\n", 1)[0].strip() == "Berufsfachschulen":
                break
            for t in page.extract_tables():
                for r in t:
                    if r and len(r) >= 13 and re.fullmatch(r"\d{5}", K._clean(r[1])):
                        out[K._clean(r[1])] = r[2]
    return out


def _words(cell):
    """The words of the cell's name/town/operator block in order, as (word, starts a line, ends a line). The block
    ends where parse() stops reading it ('EIN-Krankenhaus im Sinne des KHG mit <KeZ list>'). A line ending in a
    hyphen glued to its word runs on into the next line: 'Martha-' + 'Maria' -> 'Martha-Maria', 'G.-' + 'Walther'
    -> 'G.-Walther', 'psychia-' + 'trie' -> 'psychiatrie'; a free-standing ' -' is a dash and stays a word."""
    lines = []
    for line in (x.strip() for x in (cell or "").split("\n") if x.strip()):
        if line.startswith(("EIN-Krankenhaus", "Sinne des KHG")) or re.fullmatch(r"[\d, ]+", line):
            break
        if lines and re.search(r"\S-$", lines[-1]) and re.match(r"[^\W\d_]", line):
            lines[-1] = lines[-1][:-1] + line if line[0].islower() else lines[-1] + line
        else:
            lines.append(line)
    return [(w, i == 0, i == len(line.split()) - 1) for line in lines for i, w in enumerate(line.split())]


def states(cell, field, value):
    """-> the (cut, full) word pairs when the cell states `value` as its `field`, word for word, else None: whole
    lines, the name at the head of the block, the operator at its tail, the town anywhere (a town also turns up
    inside names, so 'Klinik Neustadt a.d. Aisch' is no evidence for the town 'Neustadt'). A word that ends a
    line may be cut short: pdfplumber clips a word wider than the column ('Gesundheitseinrichtun' for
    'Gesundheitseinrichtungen'; the tail lands in the Status cell, which _status strips as 'gen')."""
    want, words = (value or "").split(), _words(cell)
    starts = range(len(words) - len(want) + 1 if want else 0)
    for i in {"name": [0], "operator": [len(words) - len(want)]}.get(field, starts):
        run = words[i:i + len(want)]
        if run and len(run) == len(want) and run[0][1] and run[-1][2] and all(
                w == v or (end and len(w) >= 4 and v.startswith(w)) for (w, _, end), v in zip(run, want)):
            return [(w, v) for (w, _, _), v in zip(run, want) if w != v]
    return None


def origin(events, db):
    """(sha, date, subject) of the commit whose clinics.csv value is the DB value now; None when the last
    committed value is something else, i.e. the DB was changed directly."""
    if events and events[-1][3] == ("" if db is None else str(db)):
        return events[-1][:3]
    return None


def explain(d, events, cell, reading_2025):
    """-> (corrections row, None) or (None, why it stays unexplained)."""
    cid, f, db = d["id"], d["field"], d["db"]
    o = origin(events, db)
    if not o:
        return None, f"not the value clinics.csv last committed ({events[-1][3] if events else None!r}): set in the DB directly"
    sha, date, subject = o
    if f not in CELL_FIELDS:
        return None, f"value from {sha} ({date[:10]}), but {f} is not read from the Krankenhaus/Standort cell"
    cut, shown = states(cell, f, db), " / ".join((cell or "").split("\n"))
    if cut is None and sha == FIRST:
        return None, f"value from {sha} ({date[:10]}), but the 2026 cell does not state it word for word: {shown!r}"
    ev = [f"git {sha} {date} {CSV}: {f} = {db!r} ({subject})",
          f"krankenhausplan_2026.pdf KeZ {cid} Krankenhaus/Standort cell (lines split by ' / '): {shown!r}; "
          + ("it does not state the value word for word" if cut is None else "it states the value word for word"
             + (f", save words cut at the column edge: {', '.join(f'{w!r} = {v!r}' for w, v in cut)}" if cut else "")),
          f"krankenhausplan.parse (2026): {f} = {d['source_value']!r}"]
    if sha == FIRST:
        return {"at": date, "table": "clinics", "id": cid, "field": f, "old": d["source_value"], "new": db,
                "code": "parse_error",
                "reason": (f"Our 2026 parse misreads this cell (positional name/town/operator layout since the 51. "
                           f"Fortschreibung dropped the 'Träger' line); the DB keeps the {f} the 2025 PDF gave, which "
                           f"the 2026 cell still states (data/sync_krankenhausplan_2026.py kept 2025 name/town/operator)."),
                "evidence": ev + [f"krankenhausplan.parse (2025): {f} = {reading_2025!r}"],
                "task": "TASK-175", "by": BY}, None
    task = HAND_EDIT_TASK[sha]      # a hand edit from a commit with no known task fails loudly
    old = events[-2][3]
    return {"at": date, "table": "clinics", "id": cid, "field": f, "old": old, "new": db, "code": "parse_error",
            "reason": f"Our parse garbled this cell's {f}; corrected by hand from the 2026 PDF's own cell in {sha} ({date[:10]}, {task}).",
            "evidence": ev + [f"git {sha}: {f} {old!r} -> {db!r}", f"backlog {task}"],
            "task": task, "by": BY}, None


def triage(d):
    """What the registry build's proposal (DB := source value) would do for an unexplained deviation."""
    if d["db"] is None:
        return "fill: the DB lacks the source value"
    squash = lambda v: re.sub(r"[\s\-]", "", str(v)).lower()   # noqa: E731
    if d["source_value"] is not None and squash(d["db"]) == squash(d["source_value"]):
        return "repair: the DB value is the source value with stray spaces/hyphens/case"
    return "review: the source and the DB name different things, or the parse is wrong -- decide by hand"


def main():
    report, out = sys.argv[1], sys.argv[2]
    devs = [d for d in json.load(open(report, encoding="utf-8"))["deviations"]
            if d["source"] == "krankenhausplan_2026" and not d["explained"]]
    hist, cell = history(), cells(PDF_2026)
    p25 = {r["clinic_id"]: r for r in K.parse(PDF_2025)}   # raises since TASK-183: the 2025 layout is not read
    rows, unexplained = [], []
    for d in devs:
        row, why = explain(d, hist.get((d["id"], d["field"])), cell.get(d["id"]), (p25.get(d["id"]) or {}).get(d["field"]))
        if row:
            rows.append(row)
        else:
            unexplained.append({**d, "why": why, "triage": triage(d), "reading_2025": (p25.get(d["id"]) or {}).get(d["field"])})
    json.dump({"rows": rows, "unexplained": unexplained}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{len(devs)} unexplained deviations -> {len(rows)} corrections rows, {len(unexplained)} unexplained -> {out}")
    print("unexplained by triage:", dict(collections.Counter(u["triage"] for u in unexplained)))


if __name__ == "__main__":
    main()
