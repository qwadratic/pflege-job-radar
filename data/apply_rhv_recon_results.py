"""TASK-148 AC#2: apply board_found verdicts from data/registry/rhv_recon_results.jsonl (the Firecrawl
recon batch, data/discover_rhv_recon.py) to the live pflege_jobs.clinics registry -- careers_url (+
ats_type when the agent fingerprinted a known vendor) only, every other CLINIC_SPEC column carried
through unchanged from the live row (same pattern as tools/apply_registry_corrections.py).

  python data/apply_rhv_recon_results.py --dry-run
  python data/apply_rhv_recon_results.py --push
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests  # noqa: E402
from pflege_jobs.schema import CLINIC_SPEC  # noqa: E402

RESULTS = "data/registry/rhv_recon_results.jsonl"


def board_found_rows():
    seen = {}
    for line in open(RESULTS, encoding="utf-8"):
        r = json.loads(line)
        if r.get("verdict") == "board_found" and r.get("careers_url"):
            seen[r["clinic_id"]] = r          # last write wins if a clinic_id appears twice
    return list(seen.values())


def live_clinics(url, key):
    r = requests.get(url + "/rest/v1/clinics?select=*&clinic_id=like.RH*&limit=1000",
                      headers={"apikey": key, "Accept-Profile": "pflege_jobs"}, timeout=60)
    r.raise_for_status()
    return {c["clinic_id"]: c for c in r.json()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    found = board_found_rows()
    print(f"{len(found)} board_found rows with a careers_url in {RESULTS}")

    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"]
    live = live_clinics(url, key)

    out = []
    for r in found:
        base = live.get(r["clinic_id"])
        if not base:
            print(f"  skip {r['clinic_id']}: not found live"); continue
        row = {k: base.get(k) for k, _ in CLINIC_SPEC}
        row["careers_url"] = r["careers_url"]
        if r.get("ats_type"):
            row["ats_type"] = r["ats_type"]
        out.append(row)

    if a.dry_run:
        print("\n--dry-run: nothing written; sample:")
        for row in out[:8]:
            print(f"  {row['clinic_id']:<8} {row['name'][:40]:<40} ats={row['ats_type'] or '-':<12} {row['careers_url']}")
        return

    if not a.push:
        print("pass --dry-run or --push"); return

    ing = {"Authorization": "Bearer " + key, "apikey": key,
           "x-ingest-secret": os.environ["PFLEGE_INGEST_SECRET"], "Content-Type": "application/json"}
    n = 0
    for i in range(0, len(out), 50):
        r = requests.post(os.environ["PFLEGE_INGEST_URL"], headers=ing,
                          data=json.dumps({"clinics": out[i:i + 50]}, ensure_ascii=False).encode("utf-8"), timeout=180)
        if r.status_code != 200:
            print("  push failed", r.status_code, r.text[:200]); continue
        n += r.json().get("clinics", 0) or 0
    print(f"pushed {n}/{len(out)} rows to Supabase clinics")


if __name__ == "__main__":
    main()
