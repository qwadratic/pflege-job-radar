"""TASK-148 AC#2 (Firecrawl leg): for RH* (Reha) clinics the free ats_discover2 pass could not resolve
(no careers_url found at all, only a bare website), run the Firecrawl career-recon agent
(pflege_jobs.sources.firecrawl_agent.run_career_agent) to find the real careers_url/ats_vendor.

Recon only (verdict + careers_url + ats_vendor), never the full LLM job-extraction agent (run_jobs_agent)
-- that's TASK-128's whole point: pay once to find the board, then crawl it for free with the normal
adapter/classify.py pipeline like every other clinic.

max_credits/call and --credit-budget (total) are genuine cost controls, not invented caps: measured live
2026-09-24 on a 10-clinic smoke test -- max_credits=15 refused every single call (0/10 completed, "Agent
reached max credits"), max_credits=40 completed 4/5 at 21-33 credits each (avg ~26/success). At that
rate the full 168-clinic cohort would cost ~4300-4700 credits -- essentially this billing period's ENTIRE
remaining balance (4619/5000 as of 2026-09-24), leaving nothing for the daily scheduled Firecrawl-mode
crawls on walled Krankenhausplan clinics that already depend on this same pool. --credit-budget stops
the batch (not a single request) once cumulative spend crosses it, printed as a real truncation, not a
silent stop -- run again with --skip to continue a later slice once more budget is available/approved.

Threaded (ThreadPoolExecutor), matching crawlers/ats_discover2.py's and career_discover_exa.py's own
convention for this exact shape of per-clinic network-bound work.

  python data/discover_rhv_recon.py --report                                  # cohort size, no network
  python data/discover_rhv_recon.py --limit 5                                 # smoke test
  python data/discover_rhv_recon.py --credit-budget 2000 --workers 5          # a real batch, stops at budget
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2  # noqa: E402
from pflege_jobs.sources.firecrawl_agent import run_career_agent, ats_type_for  # noqa: E402

OUT_DEFAULT = "data/registry/rhv_recon_results.jsonl"


def cohort():
    conn = psycopg2.connect(os.environ["SUPABASE_DB_POOLER_URL"])
    cur = conn.cursor()
    cur.execute("""select clinic_id, name, town, website from pflege_jobs.clinics
                   where clinic_id like 'RH%' and (careers_url is null or careers_url='')
                     and website is not null and website != '' order by clinic_id""")
    rows = [{"clinic_id": r[0], "name": r[1], "town": r[2], "website": r[3]} for r in cur.fetchall()]
    conn.close()
    return rows


def _one(c, max_credits):
    try:
        r = run_career_agent(c, max_credits=max_credits, log=lambda *a_: None)
    except Exception as e:
        return {"clinic_id": c["clinic_id"], "name": c["name"], "error": str(e)[:200], "credits_used": 0}
    p = r["profile"]
    return {"clinic_id": c["clinic_id"], "name": c["name"], "website": c["website"],
             "verdict": p.get("verdict"), "careers_url": p.get("careers_url"), "portal_url": p.get("portal_url"),
             "ats_vendor": p.get("ats_vendor"), "ats_type": ats_type_for(p),
             "nursing_job_count": p.get("nursing_job_count"), "credits_used": r["credits_used"] or 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip", type=int, default=0, help="skip the first N (for resuming a batch)")
    ap.add_argument("--max-credits", type=int, default=40)
    ap.add_argument("--credit-budget", type=int, default=0, help="stop enqueueing new calls once cumulative spend passes this (0 = no budget cap)")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--out", default=OUT_DEFAULT)
    a = ap.parse_args()

    todo = cohort()
    print(f"RH clinics with a website but no careers_url: {len(todo)}")
    if a.report:
        return
    if a.skip:
        todo = todo[a.skip:]
    if a.limit:
        todo = todo[:a.limit]
    print(f"running recon on up to {len(todo)} (max_credits={a.max_credits}/call, "
          f"budget={a.credit_budget or 'none'}, workers={a.workers})")

    results = []
    spent = 0
    lock = threading.Lock()
    stop = threading.Event()
    t0 = time.time()
    n_done = 0

    def worker(c):
        if stop.is_set():
            return None
        return _one(c, a.max_credits)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(worker, c): c for c in todo}
        for fut in as_completed(futs):
            c = futs[fut]
            r = fut.result()
            if r is None:
                continue
            n_done += 1
            with lock:
                spent += r["credits_used"]
                results.append(r)
                if a.credit_budget and spent >= a.credit_budget and not stop.is_set():
                    stop.set()
                    print(f"  TRUNCATED: credit budget {a.credit_budget} reached at {spent} spent, "
                          f"{n_done}/{len(todo)} processed -- not a silent stop, re-run with --skip {a.skip + n_done} to continue")
            tag = r.get("error") or r.get("verdict") or "-"
            print(f"  [{n_done:3}/{len(todo)}] {c['clinic_id']} {c['name'][:40]:<40} -> {tag:<40} "
                  f"credits={r['credits_used']} (total {spent})")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    found = sum(1 for r in results if r.get("verdict") == "board_found")
    print(f"\n{len(results)} processed in {time.time()-t0:.0f}s, {found} board_found, "
          f"{spent} credits spent -> appended to {a.out}")


if __name__ == "__main__":
    main()
