"""TASK-120 AC#7 scale-up: loads the 292-clinic blurb batch (background Workflow run 2026-09-23,
extending the reviewed 15-clinic pilot to every remaining clinic that already has a Maps photo)
into clinic_blurbs -- no re-research, just persists what was already generated. 1 of 293 requested
clinics (56407) failed its structured-output retry cap in that run and is not in this batch; left for
a separate retry, not guessed at here.

  set -a; source .env; set +a && .venv/bin/python tools/task120_seed_scaled_blurbs.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import runs as R  # noqa: E402

R.init()
results = json.load(open("/tmp/task120_scaled_blurbs.json", encoding="utf-8"))
before = len(R.clinic_blurbs_map())
for p in results:
    R.save_clinic_blurb(p["clinic_id"], {
        "text_de": p["paragraph_de"], "sources": p.get("sources") or [],
        "confidence": p.get("confidence"), "facts_used": p.get("facts_used") or []})
after = len(R.clinic_blurbs_map())
print(f"saved {len(results)} clinic_blurbs row(s); table had {before}, now has {after}")
