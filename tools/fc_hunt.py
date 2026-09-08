"""Firecrawl hunt: one agent run per clinic, N at a time, stop on anything suspicious.

Runs OUTSIDE the app's single worker queue so several agent jobs can be in flight: each clinic gets a
normal crawl_runs row (trigger='hunt') and app.crawl.execute(run_id) is called in a thread pool, so the
spend gate, kill switch, ledger, inbox posting and drain are exactly the production path.

  set -a; . ./.env; set +a
  .venv/bin/python tools/fc_hunt.py --candidates /tmp/fc_hunt_candidates.json --concurrency 3 --cap 60 --max-runs 8
  .venv/bin/python tools/fc_hunt.py --ids 27501,27502 --concurrency 2 --cap 60

Pre-check (free): the careers page is fetched with plain HTTP first; a page that says there are no
openings in the nursing section is skipped without spending a run (the Freyung lesson, run 35).
Suspicious = stop submitting new runs (in-flight ones finish): a run charged more than --max-charge
credits, a token delta above --max-tokens, an API/balance disagreement, an error, more than 60 rows
from one clinic, or three billable runs in a row that returned nothing.
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from app import crawl as CR  # noqa: E402
from app import runs as R  # noqa: E402
from pflege_jobs.sources import firecrawl_agent as FA  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
NO_JOBS = re.compile(r"(leider\s+)?(sind\s+)?(derzeit|aktuell|momentan|zur\s*zeit)\s+(sind\s+)?(in\s+diesem\s+bereich\s+)?keine\s+(offenen\s+)?stellen|keine\s+stellenangebote\s+(vorhanden|verf)", re.I)


def precheck(c):
    """('run', why) or ('skip', why). Free: one plain GET of the careers page (or website)."""
    url = (c.get("careers_url") or c.get("website") or "").strip()
    if not url:
        return "run", "no url to pre-check (agent must search)"
    try:
        r = requests.get(url, headers=UA, timeout=25, allow_redirects=True)
    except Exception as e:
        return "run", f"pre-check fetch failed ({type(e).__name__}); leaving it to the agent"
    text = re.sub(r"<[^>]+>", " ", r.text or "")
    if r.status_code >= 400:
        return "run", f"careers page HTTP {r.status_code}; agent may find the board elsewhere"
    if NO_JOBS.search(text):
        return "skip", "careers page says there are no openings right now"
    return "run", f"careers page HTTP {r.status_code}, {len(text)//1000} kB"


def pools():
    d = FA.credits(historical=False) if "historical" in FA.credits.__code__.co_varnames else FA.credits()
    return {"credits": d.get("remaining"), "tokens": d.get("tokens_remaining"), "runs_today": d.get("agent_runs_today"), "free_left": d.get("free_runs_left_today")}


def run_one(c, cap):
    rid = R.create_run("clinic", c["clinic_id"], "firecrawl", {"max_credits": cap}, [c["clinic_id"]], trigger="hunt")
    t0 = time.time()
    CR.execute(rid)
    run = R.get_run(rid)
    log = run.get("log") or []
    charged = next((l for l in log if "charging" in l), "")
    tok = re.search(r"tokens delta (-?\d+|None)", charged)
    return {"clinic_id": c["clinic_id"], "name": c["name"], "run_id": rid, "status": run.get("status"), "rows": run.get("n_rows"), "new": run.get("n_new"),
            "credits": run.get("credits_used"), "tokens_delta": (tok.group(1) if tok else None), "error": run.get("error"),
            "disagree": any("DISAGREE" in l for l in log), "gate": next((l for l in log if "spend gate" in l or "refused" in l or "skipped" in l), ""),
            "notes": next((l[l.find("notes:"):][:160] for l in log if "notes:" in l), ""), "secs": round(time.time() - t0)}


def suspicious(res, a, zero_streak):
    if res["error"]:
        return f"run {res['run_id']} error: {res['error'][:120]}"
    if res["disagree"]:
        return f"run {res['run_id']}: API creditsUsed and balance delta disagree"
    if (res["credits"] or 0) > a.max_charge:
        return f"run {res['run_id']} charged {res['credits']} credits (> {a.max_charge})"
    try:
        if res["tokens_delta"] not in (None, "None") and abs(int(res["tokens_delta"])) > a.max_tokens:
            return f"run {res['run_id']} token delta {res['tokens_delta']} (> {a.max_tokens})"
    except ValueError:
        pass
    if (res["rows"] or 0) > 60:
        return f"run {res['run_id']} returned {res['rows']} rows from one clinic"
    if zero_streak >= 3:
        return "three billable runs in a row returned nothing"
    return None


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
                futures[ex.submit(run_one, c, a.cap)] = c
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
