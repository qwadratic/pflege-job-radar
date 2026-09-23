"""TASK-86 AC#1: apply the reviewed, re-verified registry corrections in
backups/task86-registry-dryrun-2026-09-21.json to the LIVE pflege_jobs.clinics table, through the
sanctioned EdgeSink.write_clinics path (registry_lint runs inside it -- see pflege_jobs/sinks.py).

Excludes clinic_id 56404 (Klinik Hallerwiese Nuernberg / Diakoneo): its current ats_type='bite'
already yields 29 live postings against a SHARED Diakoneo group career page, and TASK-103 already
asks for a registry-scope decision on the whole Diakoneo/SUAVIA/Augustinum operator family --
changing this one clinic's routing ahead of that decision risks disrupting a board that is not
obviously broken today, for an operator this repo has already flagged as needing a scope call first.
Left for TASK-103, not guessed at here.

  set -a; source .env; set +a && .venv/bin/python tools/task86_apply_registry_corrections.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config as A  # noqa: E402
from pflege_jobs.sinks import EdgeSink  # noqa: E402

EXCLUDE = {"56404"}

report = json.load(open("backups/task86-registry-dryrun-2026-09-21.json", encoding="utf-8"))
rows = [c["edgesink_write_clinics_payload"] for c in report["live_corrections"] if c["edgesink_write_clinics_payload"]["clinic_id"] not in EXCLUDE]
ids = [r["clinic_id"] for r in rows]
print(f"applying {len(rows)} row(s) (excluded {sorted(EXCLUDE)}): {ids}")

n = EdgeSink().write_clinics(rows)
print(f"clinics upserted: {n}")

check = A.rest_get("clinics", {"select": "clinic_id,careers_url,ats_type", "clinic_id": "in.(" + ",".join(ids) + ")"})
by_id = {c["clinic_id"]: c for c in check}
for r in rows:
    live = by_id.get(r["clinic_id"], {})
    ok = live.get("careers_url") == r["careers_url"]
    print(f"  {r['clinic_id']}: careers_url {'OK' if ok else 'MISMATCH'} -> {live.get('careers_url')!r}, ats_type={live.get('ats_type')!r}")
