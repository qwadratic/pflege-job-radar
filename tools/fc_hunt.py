"""Firecrawl hunt, one-shot: one agent run per clinic from a hand-picked candidate list, N at a time, stop on anything
suspicious. Thin wrapper over app/hunter.py (precheck, run_one, suspicious, pools live there now); the resilient,
stateful, forever-running variant is `python -m app.hunter` (docs/firecrawl.md §6).

  set -a; . ./.env; set +a
  .venv/bin/python tools/fc_hunt.py --candidates /tmp/fc_hunt_candidates.json --concurrency 3 --cap 60 --max-runs 8
  .venv/bin/python tools/fc_hunt.py --ids 27501,27502 --concurrency 2 --cap 60

Each clinic gets a normal crawl_runs row (trigger='hunt') and app.crawl.execute(run_id) runs in a thread pool, so the
spend gate, kill switch, ledger, inbox posting and drain are exactly the production path. Pre-check (free): a careers
page that says there are no openings is skipped without spending a run. Suspicious = stop submitting (in-flight runs
finish): a run charged more than --max-charge, a token delta above --max-tokens, an API/balance disagreement, an
error, more than 60 rows from one clinic, or three billable runs in a row that returned nothing.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from app import runs as R  # noqa: E402
from app.hunter import pools, precheck, run_one, suspicious  # noqa: E402,F401  (re-exported for older imports)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", help="json list from the orchestrator (clinic_id, name, careers_url, website, ...)")
    ap.add_argument("--ids", help="comma-separated clinic_ids (looked up in --candidates or the live API)")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--cap", type=int, default=60, help="maxCredits per run")
    ap.add_argument("--max-runs", type=int, default=100)
    ap.add_argument("--max-charge", type=int, default=90, help="stop if one run charges more than this")
    ap.add_argument("--max-tokens", type=int, default=1500, help="stop if one run moves more tokens than this")
    ap.add_argument("--no-precheck", action="store_true")
    a = ap.parse_args()
    cands = json.load(open(a.candidates)) if a.candidates else []
    if a.ids:
        want = [x.strip() for x in a.ids.split(",") if x.strip()]
        by = {c["clinic_id"]: c for c in cands}
        cands = [by[w] if w in by else requests.get(f"http://localhost:8501/api/clinics/{w}", timeout=30).json() for w in want]
    cands = cands[: a.max_runs]
    R.init()
    print("pools before:", pools(), flush=True)
    results, skipped, stop_reason, zero_streak = [], [], None, 0
    with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        futures = {}
        queue = list(cands)
        while (queue or futures) and not stop_reason:
            while queue and len(futures) < a.concurrency:
                c = queue.pop(0)
                verdict, why = ("run", "pre-check off") if a.no_precheck else precheck(c)
                if verdict == "skip":
                    skipped.append({"clinic_id": c["clinic_id"], "name": c["name"], "why": why})
                    print(f"skip {c['clinic_id']} {c['name'][:40]}: {why}", flush=True)
                    continue
                print(f"submit {c['clinic_id']} {c['name'][:40]} (cap {a.cap}) -- {why}", flush=True)
                futures[ex.submit(run_one, c, a.cap, "hunt")] = c
            if not futures:
                break
            done = next(as_completed(list(futures)))
            c = futures.pop(done)
            try:
                res = done.result()
            except Exception as e:
                res = {"clinic_id": c["clinic_id"], "name": c["name"], "run_id": None, "status": "exception", "rows": 0, "new": 0, "credits": 0,
                       "tokens_delta": None, "error": f"{type(e).__name__}: {str(e)[:160]}", "disagree": False, "gate": "", "notes": "", "secs": 0}
            results.append(res)
            billable = (res["credits"] or 0) > 0
            zero_streak = zero_streak + 1 if billable and not res["rows"] else (0 if res["rows"] else zero_streak)
            print(json.dumps({k: res[k] for k in ("clinic_id", "run_id", "status", "rows", "new", "credits", "tokens_delta", "secs")}), (res["gate"] or res["notes"])[:140], flush=True)
            stop_reason = suspicious(res, a, zero_streak)
            if stop_reason:
                print(f"STOP: {stop_reason} -- letting {len(futures)} in-flight run(s) finish, submitting no more", flush=True)
        for f in as_completed(list(futures)):
            try:
                res = f.result(); results.append(res)
                print(json.dumps({k: res[k] for k in ("clinic_id", "run_id", "status", "rows", "new", "credits", "tokens_delta", "secs")}), flush=True)
            except Exception as e:
                print("in-flight run failed:", e, flush=True)
    print("pools after:", pools(), flush=True)
    tot = {"runs": len(results), "rows": sum(r["rows"] or 0 for r in results), "new": sum(r["new"] or 0 for r in results), "credits": sum(r["credits"] or 0 for r in results), "skipped": len(skipped), "stop": stop_reason}
    print("SUMMARY", json.dumps(tot), flush=True)
    json.dump({"results": results, "skipped": skipped, "stop": stop_reason, "totals": tot}, open("/tmp/fc_hunt_last.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
