"""Registry build and deviation report (TASK-175; TASK-180 AC#4). pflege_jobs.clinics IS the registry; the
Krankenhausplan 2026 PDF, the RHV 2024 XLSX (Reha) and the hand-collected Diakoneo list are its primary
sources; pflege_jobs.corrections says why the DB differs from them wherever it does.

  set -a; source .env; set +a
  .venv/bin/python tools/registry_build.py --report deviations.json [--proposals proposals.json]

Read-only: parses the sources, reads clinics + corrections in one read-only transaction and compares every
field a source states. A difference is EXPLAINED when the latest corrections row for that clinic and field
(else its '*' insert row) set exactly the value the DB holds, otherwise UNEXPLAINED. --proposals turns every
unexplained difference, every source row the DB lacks and every Krankenhausplan site the plan no longer lists
into a tools/apply_clinic_corrections.py file: the DB takes the source value. Nothing here writes; review the
file, then push it with apply_clinic_corrections.py (which records each change in pflege_jobs.corrections).
"""
import argparse
import collections
import csv
import datetime
import json
import os
import sys

from psycopg2.extras import RealDictCursor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from data import sync_rhv_reha as RHV                 # noqa: E402
from pflege_jobs.schema import CLINIC_SPEC            # noqa: E402
from pflege_jobs.sources import krankenhausplan as K  # noqa: E402
from tools import ledger as L                         # noqa: E402

PDF = os.path.join(ROOT, "data", "registry", "krankenhausplan_2026.pdf")
XLSX = os.path.join(ROOT, "data", "registry", "krankenhausverzeichnis_24.xlsx")
DIAKONEO = os.path.join(ROOT, "data", "registry", "diakoneo_social_bavaria.csv")
# The fields each source states. source/parse_quality are our own provenance notes and careers_url/ats_type
# belong to board discovery -- no source has them.
FIELDS = {
    "krankenhausplan_2026": ("name", "town", "operator", "landkreis", "regierungsbezirk", "status", "versorgungsstufe",
                             "traegerart", "beds", "day_places", "fachrichtungen"),
    "rhv_2024": ("name", "town", "operator", "landkreis", "regierungsbezirk", "status", "traegerart", "beds",
                 "fachrichtungen", "website"),
    "diakoneo": ("name", "town", "operator", "landkreis", "regierungsbezirk", "status", "traegerart", "website"),
}
# The DB rows each source covers: Krankenhausplan KeZ are bare digits, RHV ids "RH<n>", Diakoneo ids "DK<nn>".
FAMILY = {"krankenhausplan_2026": str.isdigit, "rhv_2024": lambda i: i.startswith("RH"),
          "diakoneo": lambda i: i.startswith("DK")}
GONE = "nicht_mehr_im_plan"
TASK = "TASK-175"


def _v(x):
    return None if x == "" else x


def read_db(conn):
    """-> ({clinic_id: row}, {(clinic_id, field): latest corrections row})."""
    with conn.cursor(cursor_factory=RealDictCursor) as q:
        q.execute("select * from pflege_jobs.clinics order by clinic_id")
        db = {r["clinic_id"]: dict(r) for r in q.fetchall()}
        q.execute("select id, row_id, field, new_value, reason_code, reason, task from pflege_jobs.corrections "
                  "where table_name = 'clinics' order by id")
        cor = {(c["row_id"], c["field"]): dict(c) for c in q.fetchall()}   # ascending id: the latest row wins
    return db, cor


def explanation(cor, cid, field, value):
    """The corrections row that explains the DB holding `value` in this field: the latest row for the field,
    or else the clinic's '*' insert row -- and only if it set exactly that value."""
    c = cor.get((cid, field))
    if c:
        return c if _v(c["new_value"]) == value else None
    c = cor.get((cid, "*"))
    return c if c and _v((c["new_value"] or {}).get(field)) == value else None


def _cor(c):
    return c and {k: c[k] for k in ("id", "reason_code", "task", "reason")}


def compare(name, rows, db, cor):
    """One source against the DB -> {deviations, missing_in_db, not_in_source}."""
    src = {r["clinic_id"]: r for r in rows}
    devs = []
    for cid, s in src.items():
        if cid not in db:
            continue
        for f in FIELDS[name]:
            sv, dv = _v(s.get(f)), _v(db[cid].get(f))
            if sv != dv:
                c = explanation(cor, cid, f, dv)
                devs.append({"source": name, "id": cid, "field": f, "db": dv, "source_value": sv,
                             "explained": c is not None, "correction": _cor(c)})
    missing = [{"source": name, "id": cid, "name": s.get("name"), "town": s.get("town")} for cid, s in src.items()
               if cid not in db]
    gone = []
    for cid, d in db.items():
        if FAMILY[name](cid) and cid not in src:
            c = cor.get((cid, "*"))
            gone.append({"source": name, "id": cid, "name": d["name"], "status": d["status"],
                         "explained": d["status"] == GONE or c is not None,
                         "why": f"status {GONE}" if d["status"] == GONE else _cor(c)})
    return {"deviations": devs, "missing_in_db": missing, "not_in_source": gone}


def proposals(result, sources, db, today):
    """Every unexplained difference as a tools/apply_clinic_corrections.py file: the DB takes the source value."""
    out = {}
    for d in result["deviations"]:
        if d["explained"]:
            continue
        label, path = sources[d["source"]][1:]
        p = out.setdefault(d["id"], {"_why": {"code": "parse_error", "reason": "", "evidence": [
            f"{os.path.relpath(path, ROOT)} parsed {today}; pflege_jobs.clinics read {today}"], "task": TASK}})
        p[d["field"]] = d["source_value"]
        p["_why"]["evidence"].append(f"{label}: {d['field']} = {d['source_value']!r}; DB: {d['db']!r}")
        fields = [f for f in p if f != "_why"]
        p["_why"]["reason"] = (f"The DB differs from {label} in {', '.join(fields)} and no pflege_jobs.corrections row "
                               f"explains it; the DB takes the source value (registry build {today}).")
    for m in result["missing_in_db"]:
        rows, label, path = sources[m["source"]]
        s = next(r for r in rows if r["clinic_id"] == m["id"])
        out[m["id"]] = {**{c: _v(s.get(c)) for c, _ in CLINIC_SPEC if c != "clinic_id"}, "_insert": True,
                        "_why": {"code": "parse_error" if m["source"] == "krankenhausplan_2026" else "not_in_source",
                                 "reason": f"{label} lists this site, the DB does not; added from the source (registry build {today}).",
                                 "evidence": [f"{os.path.relpath(path, ROOT)} parsed {today}: {m['id']} {m['name']!r}, {m['town']!r}"],
                                 "task": TASK}}
    for g in result["not_in_source"]:
        if g["explained"] or g["source"] != "krankenhausplan_2026":
            continue
        label, path = sources[g["source"]][1:]
        out[g["id"]] = {"status": GONE, "source": f"{db[g['id']]['source'] or ''} | nicht in {label}",
                        "_why": {"code": "not_in_source",
                                 "reason": f"{label} no longer lists this site; the row stays (postings link to it) "
                                           f"and is marked {GONE} (registry build {today}).",
                                 "evidence": [f"{os.path.relpath(path, ROOT)} parsed {today}: no KeZ {g['id']}"],
                                 "task": TASK}}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True, help="deviation report (JSON) to write")
    ap.add_argument("--proposals", help="apply_clinic_corrections.py file to write for the unexplained differences")
    a = ap.parse_args()
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    conn = L.connect()
    conn.set_session(readonly=True)
    db, cor = read_db(conn)
    plan = K.parse(PDF, {r["town"] for r in db.values() if r.get("town")})
    sources = {"krankenhausplan_2026": (plan, plan[0]["source"], PDF),
               "rhv_2024": (RHV.parse(XLSX), RHV.SOURCE, XLSX),
               "diakoneo": (list(csv.DictReader(open(DIAKONEO, encoding="utf-8"))), "Diakoneo Einrichtungsverzeichnis", DIAKONEO)}
    result = {"deviations": [], "missing_in_db": [], "not_in_source": []}
    for name, (rows, _, _) in sources.items():
        for k, v in compare(name, rows, db, cor).items():
            result[k] += v
    in_no_source = sorted(cid for cid in db if not any(FAMILY[n](cid) for n in FAMILY))

    summary = collections.defaultdict(lambda: collections.defaultdict(lambda: {"explained": 0, "unexplained": 0}))
    for d in result["deviations"]:
        summary[d["source"]][d["field"]]["explained" if d["explained"] else "unexplained"] += 1
    report = {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
              "db_rows": len(db), "corrections_rows": len(cor),
              "sources": {n: {"label": lab, "file": os.path.relpath(p, ROOT), "rows": len(r)} for n, (r, lab, p) in sources.items()},
              "plan_parse_validate": K.validate(plan),
              "summary": summary, **result, "in_no_source": in_no_source}
    json.dump(report, open(a.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"DB {len(db)} clinics, {len(cor)} (clinic, field) corrections; sources: "
          + ", ".join(f"{n} {len(r)}" for n, (r, _, _) in sources.items()))
    print(f"{'source':22} {'field':18} {'explained':>9} {'unexplained':>11}")
    for n, fields in summary.items():
        for f, c in fields.items():
            print(f"{n:22} {f:18} {c['explained']:>9} {c['unexplained']:>11}")
    for key in ("missing_in_db", "not_in_source"):
        rows = result[key]
        print(f"{key}: {len(rows)}" + (f" ({sum(1 for r in rows if r['explained'])} explained)" if key == "not_in_source" else "")
              + (": " + ", ".join(r["id"] for r in rows) if rows else ""))
    print(f"in_no_source: {len(in_no_source)}" + (": " + ", ".join(in_no_source) if in_no_source else ""))
    print(f"report -> {a.report}")
    if a.proposals:
        props = proposals(result, sources, db, today)
        json.dump(props, open(a.proposals, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"proposals -> {a.proposals}: {len(props)} clinic(s), "
              f"{sum(len([f for f in p if not f.startswith('_')]) for p in props.values())} field value(s)")


if __name__ == "__main__":
    main()
