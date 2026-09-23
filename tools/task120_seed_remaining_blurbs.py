"""TASK-120 completion pass: loads the 100-clinic blurb batch (background Workflow run
wf_95d99235-9e5, 2026-09-23) covering every clinic that still had no clinic_blurbs row after the
earlier 15-pilot + 292-scaled batches -- including clinics with no photo on file, per Ivan: "клиника
с блербом без фото - это норм". No re-research, just persists what was already generated. Brings
clinic_blurbs to full 407/407 registry coverage.

  set -a; source .env; set +a && .venv/bin/python tools/task120_seed_remaining_blurbs.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import runs as R  # noqa: E402

R.init()
results = json.load(open("/tmp/task120_remaining_blurbs.json", encoding="utf-8"))
before = len(R.clinic_blurbs_map())
for p in results:
    R.save_clinic_blurb(p["clinic_id"], {
        "text_de": p["paragraph_de"], "sources": p.get("sources") or [],
        "confidence": p.get("confidence"), "facts_used": p.get("facts_used") or []})
after = len(R.clinic_blurbs_map())
print(f"saved {len(results)} clinic_blurbs row(s); table had {before}, now has {after}")
