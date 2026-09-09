"""Compare what our adapter finds for one clinic against what Firecrawl finds, and explain every
mismatch: a Firecrawl-only row is either a real adapter gap, or something our classify pipeline
would correctly filter anyway (role_class in patterns.json's excluded_role_classes) -- and every
"correctly filtered" verdict is printed with its rule, so it can be checked, not just asserted.

This calls the real adapter (free) and, if the budget allows, a real Firecrawl agent run (spends
credits, ledger-tracked via R.add_usage exactly like a normal crawl) -- it does NOT go through
spend_gate()'s "adapter covers it" refusal (the whole point is to check that refusal is correct),
but it keeps every other safety floor: reserve_credits, MIN_VIABLE_CAP, and it will not run at all
if the budget is too thin.

Usage: python tools/compare_adapter_fc.py <clinic_id> [--max-credits 80]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import config as A                      # noqa: E402
from app import crawl as CR                      # noqa: E402
from app import data as D                        # noqa: E402
from app import hunter as HU                      # noqa: E402  (MAX_CREDITS_RE, the same cap-ladder regex)
from app import runs as R                        # noqa: E402
from app import settings as ST                    # noqa: E402
from pflege_jobs import classify as CL           # noqa: E402
from pflege_jobs import config as C              # noqa: E402
from pflege_jobs.cli import canonical_ref        # noqa: E402
from pflege_jobs.sources import firecrawl_agent as FA  # noqa: E402


# adapter_rows() used to be a private copy of the board-walk; it now lives in app/crawl.py as
# raw_board_rows() (shared with GET /api/crawl/estimate too). Kept as a thin alias so the rest of this
# file (and its docstrings/log lines) don't need to change.
adapter_rows = CR.raw_board_rows


def fc_rows(clinic, max_credits, log=print):
    """None, reason  ->  budget too thin, did not spend. rows, None  ->  ran, ledger-charged."""
    fc = FA.credits(tokens=False, historical=False) or {}
    remaining = fc.get("remaining")
    cfg = {**ST.FIRECRAWL_DEFAULT, **(R.get_setting("firecrawl") or {})}
    reserve = int(cfg.get("reserve_credits") or 150)
    budget_cap = (remaining - reserve) if isinstance(remaining, int) else 10 ** 9
    cap = min(int(max_credits), max(0, budget_cap))
    if cap < CR.MIN_VIABLE_CAP:
        return None, f"budget too thin for a viable attempt ({cap} credits available, need >= {CR.MIN_VIABLE_CAP})"
    # Mirror app/hunter.py's cap ladder: one retry at a higher cap after an UNBILLED "Agent reached max
    # credits" refusal -- a big board (900+ beds) can need more than a modest first attempt.
    escalate_cap = min(200, max(0, budget_cap))
    try:
        res = FA.run_jobs_agent(clinic, max_credits=cap, log=log)
    except FA.AgentFailed as e:
        R.add_usage("jobs", clinic["clinic_id"], e.credits_used, job_id=getattr(e, "job_id", None))
        if not HU.MAX_CREDITS_RE.search(str(e)) or escalate_cap <= cap:
            return None, f"firecrawl agent failed: {str(e)[:200]}"
        log(f"unbilled 'Agent reached max credits' at cap {cap} -> one retry at {escalate_cap}")
        try:
            res = FA.run_jobs_agent(clinic, max_credits=escalate_cap, log=log)
        except FA.AgentFailed as e2:
            R.add_usage("jobs", clinic["clinic_id"], e2.credits_used, job_id=getattr(e2, "job_id", None))
            return None, f"firecrawl agent failed even at cap {escalate_cap}: {str(e2)[:200]}"
    R.add_usage("jobs", clinic["clinic_id"], res["credits_used"], job_id=res.get("job_id"), tokens=res.get("tokens_delta"))
    log(f"firecrawl: {res['credits_used']} credits charged, {len(res['rows'])} rows")
    out = []
    for r in res["rows"]:
        pl = r.get("payload") or {}
        out.append({"title": pl.get("title") or r.get("title"), "url": r.get("source_url") or r.get("source_ref")})
    return [r for r in out if r["title"] and r["url"]], None


def keys(rows):
    """(canonical_url, normalized_title) pairs used for matching -- either one agreeing counts as the same posting."""
    return {(canonical_ref(r["url"]), CL.norm_text(r["title"])) for r in rows}


def match(row, other_rows):
    k = (canonical_ref(row["url"]), CL.norm_text(row["title"]))
    return any(k[0] == ok[0] or k[1] == ok[1] for ok in keys(other_rows))


def _selftest():
    """python tools/compare_adapter_fc.py --selftest -- no network, no clinic needed."""
    a = [{"title": "Pflegefachkraft (m/w/d)", "url": "https://x.de/jobs/1?ref=abc"}]
    f_same_url = [{"title": "PFLEGEFACHKRAFT M/W/D", "url": "https://x.de/jobs/1?ref=xyz"}]     # same page, different tracking param
    f_new = [{"title": "Pflegehelfer (m/w/d)", "url": "https://x.de/jobs/2"}]                    # different job, excluded role
    assert match(a[0], f_same_url), "same canonical URL must match despite a differing query param"
    assert not match(a[0], f_new), "a different job must not match"
    role_class, rule = CL.classify_role(f_new[0]["title"])
    assert role_class == "pflegehelfer" and role_class in C.EXCLUDED_ROLE_CLASSES, (role_class, rule)
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clinic_id", nargs="?")
    ap.add_argument("--max-credits", type=int, default=80)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        _selftest(); return
    if not a.clinic_id:
        ap.error("clinic_id is required unless --selftest")
    D.refresh()                     # D.clinic() reads the in-memory snapshot, empty until refreshed once
    clinic = D.clinic(a.clinic_id)
    if not clinic:
        print(f"unknown clinic_id {a.clinic_id}"); return
    print(f"=== {clinic['name']} ({clinic.get('town')}) -- {clinic.get('beds')} beds, ats_type={clinic.get('ats_type')} ===")

    a_rows = adapter_rows(clinic)
    print(f"adapter: {len(a_rows)} rows")
    f_rows, err = fc_rows(clinic, a.max_credits)
    if err:
        print(f"firecrawl: SKIPPED -- {err}")
        return

    gaps, filtered = [], []
    for r in f_rows:
        if match(r, a_rows):
            continue
        role_class, rule = CL.classify_role(r["title"])
        if role_class in C.EXCLUDED_ROLE_CLASSES:
            filtered.append({**r, "role_class": role_class, "rule": rule})
        else:
            gaps.append({**r, "role_class": role_class, "rule": rule})
    # The adapter's raw board scrape is PRE-classify (every job on the board, doctors/trainees/cleaners
    # included) while f_rows is Firecrawl's OWN already-nursing-only answer -- comparing them raw would
    # call every non-nursing adapter row a "miss". Classify the adapter side the same way so "extra" only
    # ever means a real, explainable discrepancy, on both sides symmetrically.
    fc_gap = []
    for r in a_rows:
        if match(r, f_rows):
            continue
        role_class, rule = CL.classify_role(r["title"])
        if role_class in C.EXCLUDED_ROLE_CLASSES:
            continue                      # our own pipeline would drop this too -- not a real discrepancy
        fc_gap.append({**r, "role_class": role_class, "rule": rule})

    print(f"\nmatched: {len(f_rows) - len(gaps) - len(filtered)}")
    print(f"\n--- ADAPTER GAP: Firecrawl found it, our pipeline would keep it, adapter never fetched it ({len(gaps)}) ---")
    for r in gaps:
        print(f"  {r['title']}  ({r['role_class']}:{r['rule']})\n    {r['url']}")
    print(f"\n--- CORRECTLY FILTERED (Firecrawl side): Firecrawl found it, classify_role would exclude it ({len(filtered)}) ---")
    for r in filtered:
        print(f"  {r['title']}  ({r['role_class']}:{r['rule']})\n    {r['url']}")
    print(f"\n--- FIRECRAWL GAP: adapter found a real nursing role, Firecrawl's own agent missed it ({len(fc_gap)}) ---")
    for r in fc_gap:
        print(f"  {r['title']}  ({r['role_class']}:{r['rule']})\n    {r['url']}")

    out_path = A.DATA_DIR / f"compare_{clinic['clinic_id']}.json"
    out_path.write_text(json.dumps({"clinic": clinic["name"], "clinic_id": clinic["clinic_id"], "adapter_rows": len(a_rows),
                                     "firecrawl_rows": len(f_rows), "gaps": gaps, "correctly_filtered": filtered, "firecrawl_gap": fc_gap}, ensure_ascii=False, indent=1))
    print(f"\nsaved: {out_path}")


if __name__ == "__main__":
    main()
