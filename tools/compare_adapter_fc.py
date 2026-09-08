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

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import config as A                      # noqa: E402
from app import crawl as CR                      # noqa: E402
from app import data as D                        # noqa: E402
from app import runs as R                        # noqa: E402
from app import settings as ST                    # noqa: E402
from pflege_jobs import classify as CL           # noqa: E402
from pflege_jobs import config as C              # noqa: E402
from pflege_jobs.cli import canonical_ref        # noqa: E402
from pflege_jobs.sources import firecrawl_agent as FA  # noqa: E402


def adapter_rows(clinic):
    """Fresh, free: exactly what execute() would ingest right now, before classify."""
    boards = CR._boards([clinic])
    session = requests.Session()
    out = []
    for _url, b in boards.items():
        if b["kind"] == "vendor":
            for r in CR._vendor_rows(b, clinic, session, lambda *_: None):
                pl = r.get("payload") or {}
                out.append({"title": pl.get("title"), "url": r.get("source_url")})
        else:
            obs, _st = CR._seed_obs(b, clinic, D.towns(), lambda *_: None)
            for o in obs:
                out.append({"title": o.get("title"), "url": o.get("source_ref") or o.get("source_url")})
    return [r for r in out if r["title"] and r["url"]]


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
    res = FA.run_jobs_agent(clinic, max_credits=cap, log=log)
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

    gaps, filtered, extra = [], [], []
    for r in f_rows:
        if match(r, a_rows):
            continue
        role_class, rule = CL.classify_role(r["title"])
        if role_class in C.EXCLUDED_ROLE_CLASSES:
            filtered.append({**r, "role_class": role_class, "rule": rule})
        else:
            gaps.append({**r, "role_class": role_class, "rule": rule})
    for r in a_rows:
        if not match(r, f_rows):
            extra.append(r)

    print(f"\nmatched: {len(f_rows) - len(gaps) - len(filtered)}")
    print(f"\n--- ADAPTER GAP: Firecrawl found it, our pipeline would keep it, adapter never fetched it ({len(gaps)}) ---")
    for r in gaps:
        print(f"  {r['title']}  ({r['role_class']}:{r['rule']})\n    {r['url']}")
    print(f"\n--- CORRECTLY FILTERED: Firecrawl found it, classify_role would exclude it ({len(filtered)}) ---")
    for r in filtered:
        print(f"  {r['title']}  ({r['role_class']}:{r['rule']})\n    {r['url']}")
    print(f"\n--- ADAPTER EXTRA: adapter found it, Firecrawl didn't ({len(extra)}) ---")
    for r in extra:
        print(f"  {r['title']}\n    {r['url']}")

    out_path = A.DATA_DIR / f"compare_{clinic['clinic_id']}.json"
    out_path.write_text(json.dumps({"clinic": clinic["name"], "clinic_id": clinic["clinic_id"], "adapter_rows": len(a_rows),
                                     "firecrawl_rows": len(f_rows), "gaps": gaps, "correctly_filtered": filtered, "adapter_extra": extra}, ensure_ascii=False, indent=1))
    print(f"\nsaved: {out_path}")


if __name__ == "__main__":
    main()
