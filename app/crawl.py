"""Executes crawl runs. One run = a scope (clinic / city / regierungsbezirk / board / job / all) crawled in one mode:

  adapter    the board's vendor adapter (crawlers.vendor_adapters, or the seeded modules softgarden / bite / pi_asp /
             umantis via career_crawl.Crawler). The board, not the clinic, is the unit of work (shared boards fetched once).
  firecrawl  pflege_jobs.sources.firecrawl_agent.run_jobs_agent — LLM-driven, credit-capped; for walled / unlabelled sites.
  auto       adapter where routable and not walled, else firecrawl while the weekly credit budget allows.

Rows flow through the same intake as every other crawler: inbox rows -> pflege_jobs.inbox -> `cli inbox`;
observations (seeded adapters produce those directly) -> EdgeSink + clinic_links. New postings are verified by URL.
"""
import hashlib
import json
import os
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

from . import config as A
from . import data as D
from . import runs as R

VENDOR_KINDS = {}                   # filled lazily from crawlers.routing.ADAPTERS
POLITE_SLEEP = 1.0


def _log(run_id):
    def f(*parts):
        R.log(run_id, " ".join(str(p) for p in parts))
    return f


def _budget_left():
    fc = R.get_setting("firecrawl") or {}
    legacy = R.get_setting("schedule") or {}
    weekly = int(fc.get("weekly_budget") or legacy.get("firecrawl_weekly_budget") or 100)
    return weekly - R.usage_total(days=7)


def _clinics_for_scope(scope, value):
    from . import targets as T
    return T.clinics_for(T.parse({"scope": scope, "value": value}))


def plan_for(scope, value, mode, max_credits, target=None):
    """What a run would do — also used by POST /api/crawl to validate before queueing.
    Accepts either scope+value (comma-separated values allowed) or a target dict."""
    from . import targets as T
    from crawlers.routing import plan as route
    tgt = target or T.parse({"scope": scope, "value": value})
    clinics = T.clinics_for(tgt)
    adapter, fire, skipped = [], [], []
    for c in clinics:
        can_adapter = c.get("routable") and not c.get("walled")
        if mode == "adapter":
            (adapter if can_adapter else skipped).append(c)
        elif mode == "firecrawl":
            fire.append(c)
        else:
            (adapter if can_adapter else fire).append(c)
    try:
        boards = len(route(adapter)[0]) if adapter else 0
    except Exception:
        boards = len({(c.get("board") or c.get("careers_url") or c["clinic_id"]).lower() for c in adapter})
    walled = sum(1 for c in clinics if c.get("walled"))
    return {"target": tgt, "clinics": clinics, "adapter": adapter, "firecrawl": fire, "skipped": skipped, "boards": boards, "walled": walled,
            "credits_needed": len(fire) * int(max_credits or 0), "credits_left": _budget_left()}


# --- adapters -------------------------------------------------------------------------------
def _boards(clinics):
    from crawlers.routing import plan
    boards, _ = plan(clinics)
    return boards


def _seed_obs(board, c, towns, log):
    """Seeded vendors return observations (already classified). -> (observations, stats)"""
    vendor = board["vendor"]
    from pflege_jobs.sources.career_crawl import Crawler
    if vendor == "softgarden":
        from pflege_jobs.sources.softgarden import seed_for
        seed = seed_for({"name": c["name"], "career": c["careers_url"]}, c["clinic_id"], c.get("town"))
        if not seed:
            return [], {"error": "no softgarden host found on careers page"}
        return Crawler(towns, per_site_pages=150, list_pages=6, sleep=0.2, log=log).crawl(seed)
    if vendor in ("bite", "bite_jobs"):
        from pflege_jobs.sources import bite
        seed = {"name": c["name"], "kez": c["clinic_id"], "career": c["careers_url"], "bavaria_only_operator": True, "town": c.get("town")}
        rows, st = bite.crawl(seed, towns, log=log)
        for r in rows:
            r.setdefault("_kez", None)
        return rows, st
    if vendor == "umantis":
        from pflege_jobs.sources.ats_seeds import BUILDERS
        seed = BUILDERS["umantis"]({"name": c["name"], "career": c["careers_url"]}, c["clinic_id"], c.get("town"))
        if not seed:
            return [], {"error": "no umantis instance found on careers page"}
        return Crawler(towns, per_site_pages=150, list_pages=6, sleep=0.2, log=log).crawl(seed)
    if vendor == "pi_asp":
        try:
            import playwright  # noqa: F401
        except ImportError:
            return [], {"error": "P&I adapter needs Playwright (pip install playwright && playwright install chromium)"}
        from pflege_jobs.sources import pi_asp
        seeds = json.load(open(A.DATA_DIR / "registry" / "pi_seeds.json", encoding="utf-8"))
        mine = [s for s in seeds if (s.get("default") or {}).get("kez") == c["clinic_id"] or any((x or {}).get("kez") == c["clinic_id"] for x in (s.get("sites") or {}).values())]
        if not mine:
            return [], {"error": "no P&I seed for this clinic (data/registry/pi_seeds.json)"}
        rows, st = [], {}
        for s in mine:
            r, x = pi_asp.crawl(s, towns, max_items=80, log=log)
            rows += r; st.update(x)
        return rows, st
    return [], {"error": f"no seeded runner for {vendor}"}


def _vendor_rows(board, c, session, log):
    """Vendor adapters return inbox-shaped rows."""
    from crawlers import vendor_adapters as VA
    g = VA.group_portal_for(c)
    if g:
        rows = VA.crawl_group_portal(c, g, session=session)
    else:
        fn = VA.VENDORS.get(board["vendor"])
        if not fn:
            raise RuntimeError(f"no vendor adapter for {board['vendor']}")
        rows = fn(c, session=session)
    for r in rows:                                   # registry town beats an empty one
        locs = r["payload"].get("loc") or [{}]
        if c.get("town") and not any((l or {}).get("city") for l in locs):
            r["payload"]["loc"] = [{"city": c["town"], "plz": None, "region": "BAYERN"}]
    return rows


# --- intake -----------------------------------------------------------------------------------
def _write_jsonl(run_id, rows):
    A.CRAWL_OUT.mkdir(parents=True, exist_ok=True)
    p = A.CRAWL_OUT / f"run_{run_id}.jsonl"
    with open(p, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def _post_inbox(rows, log):
    seen, uniq = set(), []
    for r in rows:
        if r.get("source_url") and r["source_url"] not in seen:
            seen.add(r["source_url"]); uniq.append(r)
    for i in range(0, len(uniq), 200):
        A.rest_post("inbox", uniq[i:i + 200])
    log(f"posted {len(uniq)} rows to inbox")
    return [r["source_url"] for r in uniq]


def _cli(args, log, timeout=1800):
    cmd = [A.PYTHON, "-m", "pflege_jobs.cli", *args]
    log("$ " + " ".join(cmd[2:]))
    p = subprocess.run(cmd, cwd=str(A.ROOT), capture_output=True, text=True, timeout=timeout, env=dict(os.environ))
    out = (p.stdout + p.stderr).strip()
    for line in out.splitlines()[-12:]:
        log("  " + line[:300])
    if p.returncode != 0:
        log(f"  rc={p.returncode}")
    return p.returncode


def _posting_ids_for_refs(refs):
    ids = {}
    for i in range(0, len(refs), 50):
        batch = refs[i:i + 50]
        q = ",".join('"' + s.replace('"', '\\"') + '"' for s in batch)
        try:
            for x in A.rest_get("posting_observations", {"select": "posting_id,source_ref", "source_ref": f"in.({q})"}):
                if x.get("posting_id"):
                    ids[x["source_ref"]] = x["posting_id"]
        except Exception as e:
            continue
    return ids


def _load_observations(obs, clinics_by_id, log):
    """Seeded-adapter observations -> EdgeSink + clinic links (mirrors cli inbox's post-load steps)."""
    from pflege_jobs.registry import Matcher
    from pflege_jobs.sinks import EdgeSink
    from pflege_jobs import config as C
    obs = [o for o in obs if o.get("role_class") not in C.EXCLUDED_ROLE_CLASSES and o.get("in_bavaria") is not False]
    if not obs:
        return {}, []
    m = Matcher([dict(c) for c in D.registry_csv_rows()])
    for o in obs:
        mt = m.match(o.get("employer_name"), o.get("city"))
        o["_kez"] = (mt[0] if mt else None) or o.get("_kez")
        o["_rule"] = (mt[1] if mt else None) or ("seed_kez" if o.get("_kez") else None)
        if o["_kez"]:
            o["employer_class"] = "clinic"; o["employer_class_rule"] = "registry_match|" + (o.get("employer_class_rule") or "")
    sink = EdgeSink(batch=200)
    st = sink.write(obs, resolve=True, log=lambda *_: None)
    log(f"ingested observations: {st}")
    ids = _posting_ids_for_refs([o["source_ref"] for o in obs])
    links = [{"posting_id": ids[o["source_ref"]], "clinic_id": o["_kez"], "clinic_match_rule": o["_rule"], "clinic_match_score": 0.9}
             for o in obs if o["source_ref"] in ids and o.get("_kez")]
    for i in range(0, len(links), 400):
        sink._post({"clinic_links": links[i:i + 400]})
    log(f"clinic links pushed: {len(links)}")
    return ids, obs


def _verify_ids(posting_ids, log):
    if not posting_ids:
        return {}
    from pflege_jobs.verify import verify_all
    from pflege_jobs.sinks import EdgeSink
    ids = ",".join(str(i) for i in sorted(set(posting_ids)))
    rows = []
    for i in range(0, len(posting_ids), 200):
        chunk = ",".join(str(x) for x in list(sorted(set(posting_ids)))[i:i + 200])
        rows += A.rest_get("v_postings", {"select": "posting_id,title,source_url,external_url", "posting_id": f"in.({chunk})"})
    res = verify_all(rows, workers=4, log=log)
    sink = EdgeSink(batch=400)
    for i in range(0, len(res), 400):
        sink._post({"verify": res[i:i + 400]})
    from collections import Counter
    c = Counter(x["verify_status"] for x in res)
    log(f"verified {len(res)} new postings: {dict(c)}")
    return dict(c)


# --- the run --------------------------------------------------------------------------------
def execute(run_id):
    run = R.get_run(run_id, with_log=False)
    log = _log(run_id)
    params = run.get("params") or {}
    scope, value, mode = run["scope"], run["value"], run["mode"]
    max_credits = int(params.get("max_credits") or 40)
    try:
        plan = plan_for(scope, value, mode, max_credits)
    except Exception as e:
        R.update_run(run_id, status="failed", finished_at=R.now(), error=str(e)[:300]); log(f"FAILED: {e}"); return
    clinics = plan["clinics"]
    R.update_run(run_id, clinic_ids=[c["clinic_id"] for c in clinics])
    if not clinics:
        R.update_run(run_id, status="failed", finished_at=R.now(), error="scope matched no clinic"); log("scope matched no clinic"); return
    log(f"scope {scope}={value!r}: {len(clinics)} clinics -> adapter {len(plan['adapter'])}, firecrawl {len(plan['firecrawl'])}, skipped {len(plan['skipped'])}")
    for c in plan["skipped"]:
        log(f"  skip {c['clinic_id']} {c['name'][:40]}: {c.get('route_reason')}")
    before_ids = {j["posting_id"] for j in D.jobs()}
    towns = D.towns()
    by_id = {c["clinic_id"]: c for c in clinics}
    inbox_rows, observations, credits_used, errors = [], [], 0, 0
    session = requests.Session()

    # job scope: re-check the posting first
    if scope == "job":
        try:
            from pflege_jobs.verify import verify_url
            from pflege_jobs.sinks import EdgeSink
            j = A.rest_get("v_postings", {"select": "posting_id,title,source_url,external_url", "posting_id": f"eq.{int(value.split(',')[0])}"})[0]
            st, code, note = verify_url(session, j.get("external_url") or j.get("source_url"), j.get("title"))
            EdgeSink()._post({"verify": [{"posting_id": j["posting_id"], "verify_status": st, "verify_http": code, "verified_at": R.now(), "verify_note": note}]})
            log(f"posting {value} re-checked: {st} ({code}) {note or ''}")
        except Exception as e:
            log(f"posting re-check failed: {str(e)[:120]}")

    # adapters, grouped by board
    if plan["adapter"]:
        boards = _boards(plan["adapter"])
        log(f"{len(boards)} board(s) to fetch")
        for url, b in boards.items():
            c = b["clinics"][0]
            names = ", ".join(x["name"][:30] for x in b["clinics"][:3]) + (" …" if len(b["clinics"]) > 3 else "")
            t0 = time.time()
            try:
                if b["kind"] == "vendor":
                    rows = _vendor_rows(b, c, session, log)
                    inbox_rows += rows
                    log(f"  {b['vendor']:<14} {url[:60]} -> {len(rows)} rows ({names}) {round(time.time() - t0)}s")
                else:
                    obs, st = _seed_obs(b, c, towns, log)
                    observations += obs
                    log(f"  {b['vendor']:<14} {url[:60]} -> {len(obs)} observations {json.dumps({k: v for k, v in (st or {}).items() if k in ('error', 'total', 'pflege', 'job_links_found', 'job_pages', 'shared')}, ensure_ascii=False)} ({names}) {round(time.time() - t0)}s")
                    if st and st.get("error"):
                        errors += 1
            except Exception as e:
                errors += 1
                log(f"  {b['vendor']:<14} {url[:60]} FAILED {type(e).__name__}: {str(e)[:160]}")
            time.sleep(POLITE_SLEEP)

    # firecrawl agent
    if plan["firecrawl"]:
        from pflege_jobs.sources import firecrawl_agent as FA
        for c in plan["firecrawl"]:
            left = _budget_left()
            if left < max_credits:
                errors += 1
                log(f"  firecrawl {c['clinic_id']} {c['name'][:40]}: skipped — weekly budget exhausted ({left} credits left, cap {max_credits})")
                continue
            try:
                res = FA.run_jobs_agent(c, max_credits=max_credits, log=log, session=session)
                credits_used += res["credits_used"]
                R.add_usage("jobs", c["clinic_id"], res["credits_used"], run_id)
                inbox_rows += res["rows"]
                log(f"  firecrawl {c['clinic_id']} {c['name'][:40]}: {len(res['rows'])} rows, {res['credits_used']} credits")
            except Exception as e:
                errors += 1
                log(f"  firecrawl {c['clinic_id']} {c['name'][:40]} FAILED {type(e).__name__}: {str(e)[:200]}")
            R.update_run(run_id, credits_used=credits_used)

    n_rows = len(inbox_rows) + len(observations)
    R.update_run(run_id, n_rows=n_rows)
    if inbox_rows:
        _write_jsonl(run_id, inbox_rows)
    refs = []
    try:
        if inbox_rows:
            refs += _post_inbox(inbox_rows, log)
            _cli(["inbox"], log)
        if observations:
            ids, obs = _load_observations(observations, by_id, log)
            refs += [o["source_ref"] for o in obs]
        if refs:
            _cli(["link-cross"], log)
            ids = _posting_ids_for_refs(refs)
            new_ids = [pid for pid in set(ids.values()) if pid not in before_ids]
            log(f"{len(ids)} postings touched, {len(new_ids)} new")
            if params.get("verify", True):
                _verify_ids(new_ids or list(set(ids.values()))[:200], log)
            R.update_run(run_id, n_new=len(new_ids))
    except Exception as e:
        errors += 1
        log(f"intake FAILED {type(e).__name__}: {str(e)[:300]}")
    status = "done" if not errors or n_rows else "failed"
    if errors and status == "done":
        log(f"finished with {errors} error(s)")
    R.update_run(run_id, status=status, finished_at=R.now(), error=(f"{errors} error(s), see log" if errors else None))
    try:
        D.refresh()                                  # after the final status, so last_crawl_* on the clinic rows is right
    except Exception as e:
        log(f"cache refresh failed: {e}")
    R.mirror_to_supabase(R.get_run(run_id, with_log=False))
    log(f"run finished: {status}, rows {n_rows}, credits {credits_used}")


def refetch_career(run_id):
    """Firecrawl career-discovery for one clinic; updates ats_type/careers_url when the agent found something better."""
    run = R.get_run(run_id, with_log=False)
    log = _log(run_id)
    cid = run["value"]
    c = D.clinic(cid)
    if not c:
        R.update_run(run_id, status="failed", finished_at=R.now(), error="unknown clinic"); return
    max_credits = int((run.get("params") or {}).get("max_credits") or 40)
    if _budget_left() < max_credits:
        R.update_run(run_id, status="failed", finished_at=R.now(), error="firecrawl budget exhausted"); log("weekly firecrawl budget exhausted"); return
    from pflege_jobs.sources import firecrawl_agent as FA
    try:
        res = FA.run_career_agent(c, max_credits=max_credits, log=log)
    except Exception as e:
        R.update_run(run_id, status="failed", finished_at=R.now(), error=str(e)[:300]); log(f"FAILED {e}"); return
    prof = res["profile"]
    R.add_usage("career", cid, res["credits_used"], run_id)
    R.save_career_profile(cid, prof, res["credits_used"], run_id)
    R.update_run(run_id, credits_used=res["credits_used"], n_rows=1)
    new_ats = FA.ats_type_for(prof)
    new_url = (prof.get("portal_url") or prof.get("careers_url") or "").strip()
    changed = {}
    if new_ats and new_ats != (c.get("ats_type") or ""):
        changed["ats_type"] = new_ats
    if new_url.startswith("http") and new_url.rstrip("/") != (c.get("careers_url") or "").rstrip("/") and not c.get("careers_url"):
        changed["careers_url"] = new_url
    if changed:
        try:
            from pflege_jobs.registry import full_clinic_rows
            from pflege_jobs.sinks import EdgeSink
            live = A.rest_get("clinics", {"select": "*", "clinic_id": f"eq.{cid}"})
            row = full_clinic_rows([{**(live[0] if live else {}), **changed, "clinic_id": cid}], live)[0]
            row["fachrichtungen"] = (live[0] if live else {}).get("fachrichtungen")   # keep the stored pipe-string form
            r = EdgeSink()._post({"clinics": [row]})
            log(f"clinic updated {changed} -> {r.get('clinics')}")
        except Exception as e:
            log(f"clinic update failed: {str(e)[:200]}")
    else:
        log("no ats_type/careers_url change" + (f" (agent vendor {prof.get('ats_vendor')})" if prof.get("ats_vendor") else ""))
    R.update_run(run_id, status="done", finished_at=R.now())
    try:
        D.refresh()
    except Exception:
        pass
    R.mirror_to_supabase(R.get_run(run_id, with_log=False))


def dispatch(run_id):
    run = R.get_run(run_id, with_log=False)
    if run["scope"] == "career":
        return refetch_career(run_id)
    return execute(run_id)
