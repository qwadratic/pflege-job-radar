"""TASK-120: applies the Haiku building-classifier's verdicts (background Workflow run 2026-09-23,
one clinic per file already on disk under data/clinic_photos/<id>/) to the clinic_photos table --
replaces the naive "just take maps.jpg" pick with the classifier's chosen real building/exterior (or
identifiable interior) photo, and clears a clinic's row entirely when nothing on disk qualifies.

  set -a; source .env; set +a && .venv/bin/python tools/task120_apply_photo_classifier.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import runs as R  # noqa: E402

VERDICTS = "/tmp/claude-1000/-home-exedev-repo/663542db-d7aa-555d-9da9-5b91aee05e27/tasks/wpd7cfn9n.output"
PHOTO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "clinic_photos")

R.init()
results = json.load(open(VERDICTS, encoding="utf-8"))["result"]["results"]
kept, cleared, missing = 0, 0, 0
for r in results:
    cid = r["clinic_id"]
    if r["chosen_file"] == "none":
        R.clear_clinic_photos(cid)
        cleared += 1
        continue
    path = os.path.join(PHOTO_DIR, cid, r["chosen_file"])
    if not os.path.isfile(path):
        print(f"  {cid}: chosen file {r['chosen_file']!r} not on disk, skipping ({r['reason'][:60]})")
        missing += 1
        continue
    R.clear_clinic_photos(cid)
    R.record_clinic_photo(cid, path, "classified")
    kept += 1

print(f"kept {kept}, cleared (no qualifying candidate) {cleared}, missing-file skipped {missing}, total {len(results)}")
print("clinic_photos rows now:", len(R.clinic_photos_map()))
