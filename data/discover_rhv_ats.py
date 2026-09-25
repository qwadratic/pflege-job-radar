"""TASK-148 AC#2: free (no paid API) careers_url/ats_type discovery for the RH* (Reha/Vorsorge)
cohort, reusing crawlers.ats_discover2's fingerprinting untouched -- it already does exactly this
(homepage sitemap + "Bewerben" button probing) for any clinic row with a website but no ats_type.
Kept as a separate driver rather than editing ats_discover2.py: main() there has no cohort filter,
and this scopes the run to the 229 freshly-pushed RH rows so it stays auditable per-task.

  python data/discover_rhv_ats.py --report
  python data/discover_rhv_ats.py                 # both angles, all RH* unlabeled-with-website rows
  python data/discover_rhv_ats.py --limit 20
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crawlers.ats_discover2 import CID, OUT, discover_one, load_clinics  # noqa: E402


def rh_cohort(clinics):
    return [c for c in clinics if c["clinic_id"].startswith("RH") and not (c.get("ats_type") or "").strip()
            and (c.get("careers_url") or c.get("website"))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--angle", default="bewerben,sitemap")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    clinics = load_clinics()
    rh = [c for c in clinics if c["clinic_id"].startswith("RH")]
    todo = rh_cohort(clinics)
    if a.report:
        print(f"{len(rh)} RH clinics, {len(todo)} unlabeled with a website/careers_url to probe")
        print("  no website at all:", sum(1 for c in rh if not (c.get("careers_url") or c.get("website"))))
        return

    if a.limit:
        todo = todo[:a.limit]
    angles = set(a.angle.split(","))
    print(f"{len(todo)} RH sites to probe, angles={sorted(angles)}")

    rows, hits, stats = [], 0, Counter()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(discover_one, c, angles) for c in todo]
        for i, f in enumerate(as_completed(futs), 1):
            try:
                c, hit, tried = f.result()
            except Exception as e:
                print("  worker failed:", str(e)[:80]); continue
            stats["probed"] += 1
            if hit and hit.get("ats"):
                hits += 1; stats["ats:" + hit["ats"]] += 1; stats["via:" + hit["angle"]] += 1
                print(f"  [{i:3}/{len(todo)}] {c['name'][:44]:<44} -> {hit['ats']:<16} ({hit['angle']})")
            elif hit and hit.get("careers_url"):
                stats["careers_only"] += 1
                print(f"  [{i:3}/{len(todo)}] {c['name'][:44]:<44} -> careers_url only ({hit['angle']})")
            if hit:
                rows.append({"kind": "probe", "source_host": urlparse(hit.get("careers_url") or hit.get("apply_url") or "").netloc,
                             "source_url": hit.get("apply_url") or hit.get("careers_url") or (c.get("careers_url") or c.get("website")),
                             "collector": "rhv-ats-discover-v1", "client_id": CID,
                             "payload": {"probe": "ats_discovery", "clinic_id": c["clinic_id"],
                                         "clinic_name": c["name"], "employer": c["name"],
                                         "ats": hit.get("ats"), "apply_url": hit.get("apply_url"),
                                         "careers_url": hit.get("careers_url"),
                                         "angle": hit["angle"], "evidence": hit.get("evidence"),
                                         "n_job_urls": hit.get("n_job_urls")}})

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "rhv_ats_discover_%s.jsonl" % time.strftime("%Y%m%dT%H%M%S"))
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nprobed {stats['probed']} sites in {time.time()-t0:.0f}s: {hits} ATS identified, "
          f"{stats['careers_only']} careers_url only -> {path}")
    for k, v in sorted(stats.items()):
        if k.startswith(("ats:", "via:")):
            print(f"   {k:<28} {v}")
    unresolved = len(todo) - hits - stats["careers_only"]
    print(f"unresolved (candidates for Firecrawl recon): {unresolved}")


if __name__ == "__main__":
    main()
