"""TASK-120 AC#7: loads the 15-clinic blurb pilot's already-researched results (produced by a
background Workflow run 2026-09-23, cached at /tmp/task120_pilot_blurbs.json) into the new
clinic_blurbs table (app/runs.py) -- no re-research, this only persists what was already generated
and reviewed. Scaling to the rest of the registry is a separate pass (its own Workflow run), not this
script.

  set -a; source .env; set +a && .venv/bin/python tools/task120_seed_pilot_blurbs.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import runs as R  # noqa: E402

R.init()
pilot = json.load(open("/tmp/task120_pilot_blurbs.json", encoding="utf-8"))
for p in pilot:
    R.save_clinic_blurb(p["clinic_id"], {
        "text_de": p["paragraph_de"], "sources": p.get("sources") or [],
        "confidence": p.get("confidence"), "facts_used": p.get("facts_used") or []})
print(f"saved {len(pilot)} clinic_blurbs row(s): {[p['clinic_id'] for p in pilot]}")
