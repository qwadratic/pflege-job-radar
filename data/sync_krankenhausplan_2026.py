"""Merge the Krankenhausplan 2026 (51. Fortschreibung) into the clinics registry.

Why a merge and not a replace: the 2026 PDF dropped the literal "Träger" line that separated the
operator inside the Krankenhaus/Standort cell, so the name/town split is now positional and
ambiguous — a site suffix ("Schön Klinik" / "München Harlaching") is indistinguishable from a town.
Measured against the trusted 2025 parse, the free-text split agrees on only ~25% of names, while the
*structured* columns agree on 91-100%:

    beds 91% · day_places 95% · fachrichtungen 94% · versorgungsstufe 98%
    traegerart 99% · regierungsbezirk 100% · status 93%   (landkreis 33%, also text-ish)

So this script takes the numbers and enums from 2026 (they are the point of a new Fortschreibung)
and keeps the 2025 name/town for sites that already exist. Genuinely new KeZ get the 2026 parse as
their best available record and are flagged parse_quality='new_2026_unverified' so a human can
eyeball them. Sites that vanished from the plan are marked status='nicht_mehr_im_plan' rather than
deleted, because postings still link to them.

  python data/sync_krankenhausplan_2026.py --dry-run
  python data/sync_krankenhausplan_2026.py            # writes CSV + pushes to Supabase
"""
import argparse
import csv
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pflege_jobs.sources import krankenhausplan as K   # noqa: E402
from pflege_jobs.schema import CLINIC_SPEC             # noqa: E402

PROJECT = os.environ.get("SUPABASE_PROJECT_URL", "https://klkxfvieaxpjlplloljn.supabase.co")

# The four genuinely new KeZ, read off the PDF by hand. The positional name/town split is unreliable
# (see docstring) and four rows are cheaper to verify than to guess; each is transcribed from the
# Krankenhaus/Standort cell of the 51. Fortschreibung.
NEW_2026_OVERRIDES = {
    "16107": {"name": "Zentrum für psychische Gesundheit (ZPG) Ingolstadt", "town": "Ingolstadt",
              "operator": "kbo-Donau-Altmühl-Kliniken gGmbH", "landkreis": "Kreisfreie Stadt Ingolstadt"},
    "16307": {"name": "Augenklinik Rosenheim", "town": "Rosenheim",
              "operator": "Augenklinik Rosenheim Betriebs-GmbH & Co. KG", "landkreis": "Kreisfreie Stadt Rosenheim"},
    "26108": {"name": "LA-Regio Kliniken Landshut", "town": "Landshut",
              "operator": "LA-Regio Kliniken gKU, AdöR des Landkreises und der Stadt Landshut",
              "landkreis": "Kreisfreie Stadt Landshut"},
    "27706": {"name": "AMEOS Klinikum Inntal", "town": "Simbach am Inn",
              "operator": "AMEOS Klinikum Inntal GmbH", "landkreis": "Rottal-Inn"},
}
CSV = "data/registry/clinics.csv"
PDF = "data/registry/krankenhausplan_2026.pdf"

# Columns the new Fortschreibung is authoritative for (structured, high agreement).
TAKE_FROM_2026 = ["beds", "day_places", "fachrichtungen", "versorgungsstufe",
                  "traegerart", "regierungsbezirk", "status", "source"]
# Columns owned by other pipelines. "Never touched" means we must still SEND them: the edge upsert
# assigns every column of its recordset, and an omitted column arrives as NULL, so leaving them out
# erases them. Always resolve them from the live DB (falling back to the CSV) before pushing.
KEEP = ["ats_type", "careers_url", "website"]


def db(path, key, method="get", **kw):
    h = {"apikey": key, "Authorization": "Bearer " + key, "Accept-Profile": "pflege_jobs"}
    if method != "get":
        h["Content-Profile"] = "pflege_jobs"; h["Content-Type"] = "application/json"
    return getattr(requests, method)(PROJECT + "/rest/v1/" + path, headers=h, timeout=180, **kw)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); a = ap.parse_args()
    key = os.environ.get("SUPABASE_SECRET_KEY") or os.environ["SUPABASE_ANON_KEY"]

    cur = {c["clinic_id"]: c for c in db("clinics?select=*&limit=1000", key).json()}
    old = {r["clinic_id"]: r for r in csv.DictReader(open(CSV, encoding="utf-8"))}
    towns = {r["town"] for r in old.values() if r.get("town")}
    towns |= {r["city"] for r in db("postings?select=city&city=not.is.null&limit=100000", key).json() if r.get("city")}
    new = {r["clinic_id"]: r for r in K.parse(PDF, towns)}
    print(f"registry {len(cur)} in DB / {len(old)} in CSV; Krankenhausplan 2026 lists {len(new)}")

    added = sorted(set(new) - set(old))
    gone = sorted(set(old) - set(new))
    out, changed = [], 0
    for kez, n in new.items():
        base = dict(old.get(kez) or {})
        row = {k: (base.get(k) if base else None) for k, _ in CLINIC_SPEC}
        row["clinic_id"] = kez
        if base:                                     # existing site: refresh structured fields only
            for f in TAKE_FROM_2026:
                if (base.get(f) or "") != (n.get(f) or ""):
                    changed += 1
                row[f] = n.get(f)
            for f in KEEP:                           # preserve discovery-owned values from the DB
                row[f] = (cur.get(kez, {}) or {}).get(f) or base.get(f) or ""
        else:                                        # new KeZ: 2026 parse + hand-checked name/town
            for k, _ in CLINIC_SPEC:
                row[k] = n.get(k)
            ov = NEW_2026_OVERRIDES.get(kez)
            row.update(ov or {})
            row["parse_quality"] = "new_2026_verified" if ov else "new_2026_unverified"
            for f in KEEP:                           # new site: nothing discovered yet, but send ""
                row[f] = (cur.get(kez, {}) or {}).get(f) or ""
        out.append(row)
    for kez in gone:                                 # keep the row, mark it: postings still link here
        row = {k: (old[kez].get(k)) for k, _ in CLINIC_SPEC}
        row["clinic_id"] = kez
        row["status"] = "nicht_mehr_im_plan"
        row["source"] = (old[kez].get("source") or "") + " | nicht in 2026 (51. Fortschreibung)"
        for f in KEEP:
            row[f] = (cur.get(kez, {}) or {}).get(f) or old[kez].get(f) or ""
        out.append(row)

    print(f"  new KeZ           : {len(added)}  {added}")
    print(f"  no longer in plan : {len(gone)}  {gone}")
    print(f"  field updates     : {changed} across existing sites")
    print(f"  registry after    : {len(out)} rows")
    if a.dry_run:
        print("\n--dry-run: nothing written")
        for k in added:
            print(f"   NEW {k} {new[k]['name'][:44]:<44} {new[k]['town'] or '?':<18} beds={new[k]['beds']}")
        return

    with open(CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[k for k, _ in CLINIC_SPEC]); w.writeheader(); w.writerows(out)
    print(f"wrote {CSV}")

    ing = {"Authorization": "Bearer " + os.environ["SUPABASE_ANON_KEY"], "apikey": os.environ["SUPABASE_ANON_KEY"],
           "x-ingest-secret": os.environ["PFLEGE_INGEST_SECRET"], "Content-Type": "application/json"}
    import json
    n = 0
    for i in range(0, len(out), 50):
        r = requests.post(os.environ["PFLEGE_INGEST_URL"], headers=ing,
                          data=json.dumps({"clinics": out[i:i + 50]}, ensure_ascii=False).encode("utf-8"), timeout=180)
        if r.status_code != 200:
            print("  push failed", r.status_code, r.text[:160]); continue
        n += r.json().get("clinics", 0) or 0
    print("pushed to Supabase:", n)


if __name__ == "__main__":
    main()
