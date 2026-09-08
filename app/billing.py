"""GET /api/billing -- spend / usage report over a time window, built from the local ledger.

Sources (read-only):
  crawl_runs        one row per triggered crawl (credits_used, n_new, status, mode, trigger, clinic_ids)
  firecrawl_usage   one row per Firecrawl agent call (kind jobs|career, credits = balance delta, tokens = Extract-token delta)
  hunt_meta         '<day>/refills' counters written by app/hunter.py (auto-reloads it detected); absent -> 0
  FA.credits()      pools + billing period (network; every failure tolerated, keys stay None)
  data/registry/exa_career_seeds.json   Exa search cache with costDollars per clinic (no timestamps -> period total)

Rules: a run's usd = credits * price_per_credit (settings firecrawl.eur_per_credit, USD-derived Hobby pricing);
'free' = a Firecrawl run charged 0 credits with status done (Firecrawl's daily free agent runs); a ledger row without
a crawl_runs row is free when it carries 0 credits / 0 tokens inside the day's free allowance (first FREE_RUNS_PER_DAY
submissions of the UTC day). Buckets are UTC and continuous (empty buckets included).
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from . import config as A
from . import runs as R

router = APIRouter()

WINDOWS = ("today", "24h", "7d", "30d", "period", "custom")
GRANULARITIES = ("auto", "hour", "day")
RUNS_CAP = 500
HOUR_WINDOW_MAX = timedelta(hours=48)     # auto granularity: hour up to 48 h, day beyond
EXA_CACHE = "exa_career_seeds.json"


# --- time helpers -------------------------------------------------------------------------------
def _now():
    return datetime.now(timezone.utc)


def _parse(s):
    """ISO string (date-only, 'Z', naive = UTC) -> aware UTC datetime; None when unparsable."""
    if not s:
        return None
    if isinstance(s, datetime):
        d = s
    else:
        s = str(s).strip()
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            try:
                d = datetime.strptime(s[:10], "%Y-%m-%d")
            except ValueError:
                return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(d):
    return d.isoformat(timespec="seconds") if d else None


def _floor(d, gran):
    d = d.replace(minute=0, second=0, microsecond=0)
    return d.replace(hour=0) if gran == "day" else d


def _step(gran):
    return timedelta(days=1) if gran == "day" else timedelta(hours=1)


def _day(d):
    return d.strftime("%Y-%m-%d")


def _price():
    try:
        from . import settings as ST
        return float(ST.get_firecrawl().get("eur_per_credit") or 0.0053)
    except Exception:
        return 0.0053


def _free_runs_per_day():
    try:
        from pflege_jobs.sources import firecrawl_agent as FA
        return int(FA.FREE_RUNS_PER_DAY)
    except Exception:
        return 5


def _pools():
    """FA.credits() with everything tolerated; {} when the module or the API is unreachable."""
    try:
        from pflege_jobs.sources import firecrawl_agent as FA
        return FA.credits(timeout=10) or {}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


def resolve_window(key, frm=None, to=None, granularity="auto", fc=None, now=None):
    """-> (from, to, granularity, note). 'period' = the Firecrawl billing period (fc = FA.credits()); when the API
    gave no period the window falls back to the last 30 days and note says so."""
    now = now or _now()
    note = None
    if key == "today":
        f, t = now.replace(hour=0, minute=0, second=0, microsecond=0), now
    elif key == "24h":
        f, t = now - timedelta(hours=24), now
    elif key == "7d":
        f, t = now - timedelta(days=7), now
    elif key == "30d":
        f, t = now - timedelta(days=30), now
    elif key == "period":
        fc = fc or {}
        f = _parse(fc.get("period_start") or fc.get("hist_period_start"))
        t = _parse(fc.get("period_end") or fc.get("hist_period_end")) or now
        if not f:
            f, t, note = now - timedelta(days=30), now, "billing period unknown (Firecrawl API unreachable); showing the last 30 days"
        t = min(t, now)
    elif key == "custom":
        f, t = _parse(frm), _parse(to) or now
        if not f:
            raise HTTPException(400, "custom window needs from=ISO (and optional to=ISO)")
        if t < f:
            raise HTTPException(400, "to must not be before from")
    else:
        raise HTTPException(400, f"window must be one of {', '.join(WINDOWS)}")
    if granularity not in GRANULARITIES:
        raise HTTPException(400, f"granularity must be one of {', '.join(GRANULARITIES)}")
    if granularity == "auto":
        granularity = "hour" if (t - f) <= HOUR_WINDOW_MAX else "day"
    return f, t, granularity, note


# --- ledger reads ---------------------------------------------------------------------------------
def _rows(sql, args=()):
    with R._lock, R.db() as c:
        try:
            return [dict(r) for r in c.execute(sql, args).fetchall()]
        except sqlite3.OperationalError:            # table not there yet (fresh DB, older process)
            return []


def _run_at(r):
    """The instant a run is booked at: cost lands when it finishes; queued / running runs sit at their start."""
    return _parse(r.get("finished_at") or r.get("started_at") or r.get("queued_at"))


def _allowance_ranks(ledger, free_per_day):
    """id -> True when the row is one of the day's first free_per_day accepted submissions (rows with a job id)."""
    per_day = {}
    for l in sorted(ledger, key=lambda l: (l["at"] or "", l["id"])):
        if l.get("job_id") and l.get("kind") in ("jobs", "career") and l["at"]:
            per_day.setdefault(_day(l["at"]), []).append(l["id"])
    return {i for ids in per_day.values() for i in ids[:free_per_day]}


def _clinic_name(cid):
    try:
        from . import data as D
        c = D.clinic(cid)
        return c.get("name") if c else None
    except Exception:
        return None


def _exa():
    """Exa spend from the career-seed cache. The cache stores raw responses per clinic without timestamps, so it is
    a period total (whole cache), never a series."""
    path = A.DATA_DIR / "registry" / EXA_CACHE
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except Exception:
        return {"usd": 0.0, "searches": 0, "note": "no Exa cache file; Exa spend unknown (0)"}
    usd, n = 0.0, 0
    for per_clinic in (d.values() if isinstance(d, dict) else []):
        if not isinstance(per_clinic, dict):
            continue
        for resp in per_clinic.values():
            if not isinstance(resp, dict):
                continue
            cost = resp.get("cost") or resp.get("costDollars") or {}
            total = cost.get("total") if isinstance(cost, dict) else cost
            n += 1
            usd += float(total or 0)
    return {"usd": round(usd, 4), "searches": n,
            "note": f"cache has no timestamps: total over all {n} cached searches (file updated {_iso(mtime)}), not scoped to the window"}


def _refills(f, t):
    """Sum of hunt_meta '<day>/refills' over the UTC days the window touches; 0 when the table / keys are absent."""
    days, d = [], f.replace(hour=0, minute=0, second=0, microsecond=0)
    while d <= t:
        days.append(_day(d))
        d += timedelta(days=1)
    total = 0
    for row in _rows("select key, value from hunt_meta where key like '%/refills'"):
        day = row["key"].split("/", 1)[0]
        if day in days:
            try:
                total += int(json.loads(row["value"]))
            except (ValueError, TypeError):
                pass
    return total


# --- report ---------------------------------------------------------------------------------------
def report(window="today", frm=None, to=None, granularity="auto"):
    fc = _pools()
    f, t, gran, note = resolve_window(window, frm, to, granularity, fc)
    price = _price()
    free_per_day = _free_runs_per_day()

    # crawl runs: everything is in the window when its booking instant is
    runs = []
    for r in _rows("select * from crawl_runs order by run_id desc"):
        at = _run_at(r)
        if at and f <= at <= t:
            r["_at"] = at
            runs.append(r)
    ids = {r["run_id"] for r in runs}
    # ledger: whole table (small), the allowance rank needs whole days
    ledger = _rows("select * from firecrawl_usage order by id")
    for l in ledger:
        l["at"] = _parse(l.get("at"))
    free_ids = _allowance_ranks(ledger, free_per_day)
    by_run = {}
    for l in ledger:
        if l.get("run_id") is not None:
            by_run.setdefault(l["run_id"], []).append(l)
    orphans = [l for l in ledger if l.get("run_id") not in ids and l["at"] and f <= l["at"] <= t]

    # buckets (continuous)
    series, order = {}, []
    b, last = _floor(f, gran), _floor(t, gran)
    while b <= last:
        k = _iso(b)
        series[k] = {"t": k, "credits_billable": 0, "credits_free": 0, "tokens": 0, "runs": 0, "runs_free": 0, "new_postings": 0, "usd": 0.0}
        order.append(k)
        b += _step(gran)

    def bucket(at):
        return series[_iso(_floor(at, gran))]

    tot = {"credits": 0, "credits_billable": 0, "credits_free": 0, "tokens": 0, "usd": 0.0, "runs": 0, "runs_free": 0, "runs_billable": 0,
           "runs_failed": 0, "runs_adapter": 0, "new_postings": 0}
    kinds = {k: {"kind": k, "runs": 0, "credits": 0, "usd": 0.0} for k in ("firecrawl_jobs", "firecrawl_career", "adapter", "exa")}
    out_runs = []
    for r in runs:
        lrows = by_run.get(r["run_id"], [])
        credits = int(r.get("credits_used") or 0)
        tokens = sum(int(l["tokens"]) for l in lrows if isinstance(l.get("tokens"), int))
        is_fc = r.get("mode") == "firecrawl" or bool(lrows)
        status = r.get("status")
        free = bool(is_fc and status == "done" and credits == 0)
        usd = round(credits * price, 4)
        new = int(r.get("n_new") or 0)
        bk = bucket(r["_at"])
        for d in (bk, tot):
            d["credits_billable" if not free else "credits_free"] += credits
            d["tokens"] += tokens
            d["runs"] += 1
            d["runs_free"] += int(free)
            d["new_postings"] += new
            d["usd"] = round(d["usd"] + usd, 4)
        tot["credits"] += credits
        tot["runs_billable"] += int(credits > 0)
        tot["runs_failed"] += int(status == "failed")
        if lrows:
            for l in lrows:
                k = "firecrawl_career" if l.get("kind") == "career" else "firecrawl_jobs"
                kinds[k]["runs"] += 1
                kinds[k]["credits"] += int(l.get("credits") or 0)
        elif is_fc:
            kinds["firecrawl_jobs"]["runs"] += 1
            kinds["firecrawl_jobs"]["credits"] += credits
        else:
            tot["runs_adapter"] += 1
            kinds["adapter"]["runs"] += 1
        try:
            cids = r.get("clinic_ids") if isinstance(r.get("clinic_ids"), list) else json.loads(r.get("clinic_ids") or "[]")
        except Exception:
            cids = []
        cid = cids[0] if len(cids) == 1 else (str(r.get("value")) if r.get("scope") == "clinic" and len(cids) <= 1 and r.get("value") else None)
        out_runs.append({"run_id": r["run_id"], "at": _iso(r["_at"]), "clinic_id": cid, "clinic": _clinic_name(cid) if cid else None,
                         "clinics": len(cids), "scope": r.get("scope"), "value": r.get("value"), "mode": r.get("mode"), "trigger": r.get("trigger"),
                         "status": status, "credits": credits, "tokens": tokens, "rows": int(r.get("n_rows") or 0), "new": new,
                         "free": free, "usd": usd, "error": r.get("error")})
    for l in orphans:                                       # ledger rows without a run row (manual charges, webhook-only)
        credits = int(l.get("credits") or 0)
        tokens = int(l["tokens"]) if isinstance(l.get("tokens"), int) else 0
        free = credits == 0 and tokens == 0 and l["id"] in free_ids
        usd = round(credits * price, 4)
        bk = bucket(l["at"])
        for d in (bk, tot):
            d["credits_billable" if not free else "credits_free"] += credits
            d["tokens"] += tokens
            d["runs"] += 1
            d["runs_free"] += int(free)
            d["usd"] = round(d["usd"] + usd, 4)
        tot["credits"] += credits
        tot["runs_billable"] += int(credits > 0)
        k = "firecrawl_career" if l.get("kind") == "career" else "firecrawl_jobs"
        kinds[k]["runs"] += 1
        kinds[k]["credits"] += credits
        out_runs.append({"run_id": l.get("run_id"), "ledger_id": l["id"], "at": _iso(l["at"]), "clinic_id": l.get("clinic_id"),
                         "clinic": _clinic_name(l.get("clinic_id")), "clinics": 1 if l.get("clinic_id") else 0, "scope": None, "value": None,
                         "mode": "firecrawl", "trigger": "ledger", "status": None, "credits": credits, "tokens": tokens, "rows": 0, "new": 0,
                         "free": free, "usd": usd, "error": None})
    out_runs.sort(key=lambda x: (x["at"] or "", x["run_id"] or 0), reverse=True)
    out_runs = out_runs[:RUNS_CAP]                          # newest first, capped (totals above count every run)

    exa = _exa()
    kinds["exa"].update({"runs": exa["searches"], "credits": None, "usd": exa["usd"]})
    for k in ("firecrawl_jobs", "firecrawl_career", "adapter"):
        kinds[k]["usd"] = round(kinds[k]["credits"] * price, 4)
    tot["cost_per_posting_usd"] = round(tot["usd"] / tot["new_postings"], 4) if tot["new_postings"] else None
    tot["refills"] = _refills(f, t)
    tot["exa_usd"] = exa["usd"]
    tot["exa_searches"] = exa["searches"]

    pools = {"credits_remaining": fc.get("remaining"), "credits_plan": fc.get("plan"), "tokens_remaining": fc.get("tokens_remaining"),
             "tokens_plan": fc.get("tokens_plan"), "period_start": fc.get("period_start") or fc.get("hist_period_start"),
             "period_end": fc.get("period_end") or fc.get("hist_period_end"), "free_runs_left_today": fc.get("free_runs_left_today"),
             "free_runs_per_day": free_per_day, "agent_runs_today": fc.get("agent_runs_today"), "error": fc.get("error")}
    return {"window": {"key": window, "from": _iso(f), "to": _iso(t), "granularity": gran, "note": note},
            "price_per_credit": price, "currency": "USD",
            "series": [series[k] for k in order], "totals": tot, "by_kind": list(kinds.values()), "runs": out_runs,
            "pools": pools, "hist": {"credits_used_period": fc.get("credits_used_hist"), "tokens_used_period": fc.get("tokens_used_hist")},
            "exa_note": exa["note"]}


@router.get("/billing")
def api_billing(window: str = "today", frm: str = Query(None, alias="from"), to: str = None, granularity: str = "auto"):
    return report(window, frm, to, granularity)
