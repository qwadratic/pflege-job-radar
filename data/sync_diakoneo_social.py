"""Diakoneo KdöR elder-care/disability-care facilities (Pflegeheime, Wohnen-fuer-Menschen-mit-
Behinderung, ambulante Pflegedienste) -> candidate clinics registry rows.

TASK-103 AC#1/AC#2/AC#3: jobs.diakoneo.de is Diakoneo's company-wide job board and posts openings
for facilities the Krankenhausplan-Bayern-only registry never covers (Diakoneo also runs the
hospitals it DOES have registered -- 56103 Ansbach, 56404/56406 Nuernberg, 56501 Schwabach -- but
those already have their own dedicated careers boards/adapters). Checked live 2026-09-24: every one
of the 26 unmatched jobs.diakoneo.de postings (role_class pflegehelfer/pflegefachkraft/leitung, i.e.
genuinely nursing-relevant, not e.g. IT/Hauswirtschaft) is for a town where Diakoneo runs a named,
addressed Pflegeheim or a Wohnen-fuer-Menschen-mit-Behinderung home per diakoneo.de's own facility
pages (diakoneo.de/senioren/pflegeheime, diakoneo.de/menschen-mit-behinderung/wohnen/*) -- not a
missing Krankenhausplan hospital row (only the single Ansbach "Pneumologische Akutstation" posting
plausibly duplicates existing clinic 56103/Rangauklinik; left unmatched here on purpose, since
attributing it would require a Matcher content-rule change, out of this task's scope).

No clean bulk government dataset exists for Bavaria's Diakonie-style elder/disability-care homes the
way RHV (Statistische Aemter) covers Reha (checked live: Bayern's own "Pflegefinder" at
stmgp.bayern.de/pflege/pflegefinder/ is an interactive Pflegeboerse search UI with no bulk export,
not a structured dataset) -- so this is a small, manually curated list (13 facilities) sourced
directly from diakoneo.de's own site, each row's source URL recorded in NOTE_SOURCES below. Same
reuse-the-clinics-table-as-is decision as data/sync_rhv_reha.py, for the same reason (nothing
downstream branches on Krankenhausplan vs Reha vs this category; only the discriminator `status`
value needs to be distinct, set here to "Sonstige Pflege-/Sozialeinrichtung").

operator is set to the literal string "DIAKONEO KdöR" -- the exact operator text already on file for
clinics 56404/56406 (Nuernberg) -- on purpose: employer_norm() folds both to the same key, so once
more than one clinic shares that operator, pflege_jobs.registry.Matcher's R2_operator rule stops
matching unconditionally (its len(by_op)==1 branch has no town check) and correctly falls through to
R2_operator_town, which resolves each posting by its own town. This is what actually closes the gap;
adding rows with a differently-worded operator string would leave the postings unmatched.

clinic_id: "DK" + a 2-digit sequence (DK01..DK13, alphabetical by town) -- guarantees zero collision
with 5-digit Krankenhausplan KeZ and RH<id> Reha ids, and reads as Diakoneo-specific in logs/URLs.

  python data/sync_diakoneo_social.py --dry-run     # print the 13 rows, write nothing
  python data/sync_diakoneo_social.py               # write data/registry/diakoneo_social_bavaria.csv
  python data/sync_diakoneo_social.py --push        # push the CSV rows to Supabase via the ingest edge function
"""
import argparse
import csv
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pflege_jobs.schema import CLINIC_SPEC  # noqa: E402

OUT_CSV = "data/registry/diakoneo_social_bavaria.csv"
OPERATOR = "DIAKONEO KdöR"
STATUS = "Sonstige Pflege-/Sozialeinrichtung"
SRC_PFLEGEHEIME = "https://www.diakoneo.de/senioren/pflegeheime"
SRC_WOHNEN_ANSBACH = "https://www.diakoneo.de/menschen-mit-behinderung/wohnen/region-ansbach"
SRC_WOHNEN_DINKELSBUEHL = "https://www.diakoneo.de/menschen-mit-behinderung/wohnen/wohnen-fuer-menschen-mit-behinderung-region-dinkelsbuehl"
SRC_WOHNEN_WEISSENBURG = "https://www.diakoneo.de/menschen-mit-behinderung/wohnen/region-weissenburg-gunzenhausen"
SRC_STEIN = "https://www.diakoneo.de/senioren/ambulanter-pflegedienst/stein"

# (clinic_id, name, town, address, landkreis, regierungsbezirk, source_url)
FACILITIES = [
    ("DK01", "Diakoneo Wohnen Bruckberg", "Bruckberg", "Bernhard-Harleß-Str. 2, 91590 Bruckberg",
     "Landkreis Ansbach", "Mittelfranken", SRC_WOHNEN_ANSBACH),
    ("DK02", "Diakoneo Wohnheim für Menschen mit Behinderung Obernzenn", "Obernzenn",
     "Markersbacher Straße 2, 91619 Obernzenn", "Landkreis Neustadt a.d. Aisch-Bad Windsheim", "Mittelfranken",
     "https://www.obernzenn.de/informieren/von-a-bis-z/detailansicht/address/7405f219aeb3a941cef80cc4fc56b647/"),
    ("DK03", "Diakoneo Seniorenhof Büchenbach", "Büchenbach", "Németkérstraße 2, 91186 Büchenbach",
     "Landkreis Roth", "Mittelfranken", SRC_PFLEGEHEIME + "/region-roth"),
    ("DK04", "Diakoneo Laurentiushaus Lützelbuch", "Coburg", "Weiherstraße 9, 96450 Coburg",
     "Kreisfreie Stadt Coburg", "Oberfranken", SRC_PFLEGEHEIME + "/coburg"),
    ("DK05", "Diakoneo Wohnen Dinkelsbühl", "Dinkelsbühl", "Sonnenstraße, 91550 Dinkelsbühl",
     "Landkreis Ansbach", "Mittelfranken", SRC_WOHNEN_DINKELSBUEHL),
    ("DK06", "Diakoneo Bodelschwingh-Haus", "Erlangen", "Habichtstraße 14, 91056 Erlangen",
     "Kreisfreie Stadt Erlangen", "Mittelfranken", SRC_PFLEGEHEIME + "/erlangen"),
    ("DK07", "Diakoneo Kompetenzzentrum Forchheim", "Forchheim", "Sattlertorstr. 48 b, 91301 Forchheim",
     "Landkreis Forchheim", "Oberfranken", SRC_PFLEGEHEIME + "/forchheim"),
    ("DK08", "Diakoneo Haus an den Rangau Wiesen", "Bad Windsheim", "Erkenbrechtallee 20, 91438 Bad Windsheim",
     "Landkreis Neustadt a.d. Aisch-Bad Windsheim", "Mittelfranken", SRC_PFLEGEHEIME + "/region-neustadt-a-d-aisch-bad-windsheim"),
    ("DK09", "Diakoneo Wohnen Oettingen", "Oettingen", "86732 Oettingen in Bayern",
     "Landkreis Donau-Ries", "Schwaben", SRC_WOHNEN_WEISSENBURG),
    ("DK10", "Diakoneo Wohnen Polsingen", "Polsingen", "91738 Polsingen",
     "Landkreis Weißenburg-Gunzenhausen", "Mittelfranken", SRC_WOHNEN_WEISSENBURG),
    ("DK11", "Diakoneo Hans-Roser-Haus", "Roth", "Gartenstraße 30, 91154 Roth",
     "Landkreis Roth", "Mittelfranken", SRC_PFLEGEHEIME + "/region-roth"),
    ("DK12", "Diakoneo Seniorenzentrum Rothenburg", "Rothenburg ob der Tauber", "Oberer Kaiserweg 12, 91541 Rothenburg o. d. T.",
     "Landkreis Ansbach", "Mittelfranken", SRC_PFLEGEHEIME + "/rothenburg-o-d-t"),
    ("DK13", "Diakoneo Ambulanter Pflegedienst Stein", "Stein", "Martin-Luther-Platz 1, 90547 Stein",
     "Landkreis Fürth", "Mittelfranken", SRC_STEIN),
]


def rows():
    out = []
    for cid, name, town, address, landkreis, bezirk, src in FACILITIES:
        row = {k: "" for k, _ in CLINIC_SPEC}
        row["clinic_id"] = cid
        row["name"] = name
        row["town"] = town
        row["operator"] = OPERATOR
        row["landkreis"] = landkreis
        row["regierungsbezirk"] = bezirk
        row["status"] = STATUS
        row["parse_quality"] = "manual"
        row["source"] = f"Diakoneo Einrichtungsverzeichnis, {src} (Adresse: {address}, erhoben 2026-09-24)"
        row["website"] = "https://www.diakoneo.de"
        row["careers_url"] = ""
        row["ats_type"] = ""
        row["beds"] = ""
        row["day_places"] = ""
        row["fachrichtungen"] = ""
        row["versorgungsstufe"] = ""
        row["traegerart"] = "freigemeinnuetzig"
        out.append(row)
    return out


def _for_push(row):
    """CSV round-tripping turns Python "" into a string, and int4in("") on beds/day_places raises --
    same fix as sync_rhv_reha.py's _for_push, these two columns are always blank here (no bed-count
    source for this category), so null them unconditionally rather than repeating the CSV-int dance."""
    return {**row, "beds": None, "day_places": None}


def push(csv_rows):
    ing = {"Authorization": "Bearer " + os.environ["SUPABASE_ANON_KEY"], "apikey": os.environ["SUPABASE_ANON_KEY"],
           "x-ingest-secret": os.environ["PFLEGE_INGEST_SECRET"], "Content-Type": "application/json"}
    push_rows = [_for_push(r) for r in csv_rows]
    r = requests.post(os.environ["PFLEGE_INGEST_URL"], headers=ing,
                       data=json.dumps({"clinics": push_rows}, ensure_ascii=False).encode("utf-8"), timeout=180)
    if r.status_code != 200:
        print("  push failed", r.status_code, r.text[:300])
        return 0
    return r.json().get("clinics", 0) or 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true", help="push data/registry/diakoneo_social_bavaria.csv rows to Supabase")
    a = ap.parse_args()

    if a.push:
        csv_rows = list(csv.DictReader(open(OUT_CSV, encoding="utf-8")))
        n = push(csv_rows)
        print(f"pushed {n}/{len(csv_rows)} rows to Supabase clinics")
        return

    out = rows()
    print(f"Diakoneo social/elder-care facilities: {len(out)}")
    if a.dry_run:
        print("\n--dry-run: nothing written")
        for r in out:
            print(f"  {r['clinic_id']:<6} {r['name'][:40]:<40} {r['town']:<28} {r['source'][:70]}")
        return
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[k for k, _ in CLINIC_SPEC])
        w.writeheader()
        w.writerows(out)
    print(f"wrote {OUT_CSV} ({len(out)} rows) -- not pushed to Supabase")


if __name__ == "__main__":
    main()
