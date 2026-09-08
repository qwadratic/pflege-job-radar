"""GET /api/coverage -- per-adapter coverage table for the Clawl page.

One row per adapter key (every vendor label in crawlers.vendor_adapters.VENDORS plus the seeded/external routes
softgarden, bite, pi_asp, umantis, wp_jobs) and a synthetic 'firecrawl' row for every clinic that has no working
adapter (fetch == 'firecrawl': walled host, no label, no careers_url, unknown vendor). A clinic counts under its
ats_type label (or under the default wp_jobs route when it is unlabelled but routable); the Firecrawl row
overlaps with labelled-but-unroutable clinics on purpose, so `totals` counts each clinic once instead of summing
the rows. Reads the in-memory snapshot, the local SQLite run log and the routing plan; the one network call is
the Firecrawl balance for the `firecrawl` row (credits + Extract tokens + free agent runs left today), tolerant
of failure (keys stay None). credits_7d / tokens_7d are what the app's own runs moved per the local ledger.
"""
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from . import data as D
from . import runs as R

router = APIRouter()

EXTRA_KEYS = ("softgarden", "bite", "pi_asp", "umantis", "wp_jobs")
FIRECRAWL = "firecrawl"
_ERR_RE = re.compile(r"(\d+)\s+error")


def adapter_keys():
    """Vendor adapters + seeded/external routes, in a stable order; 'firecrawl' is appended by the endpoint."""
    try:
        from crawlers.vendor_adapters import VENDORS
        keys = list(VENDORS)
    except Exception:                                 # optional deps of the adapters must never break the API
        keys = []
    for k in EXTRA_KEYS:
        if k not in keys:
            keys.append(k)
    return keys


def _known_adapters():
    try:
        from crawlers.routing import ADAPTERS
        return set(ADAPTERS)
    except Exception:
        return set()


def _plan(clinics):
    try:
        from crawlers.routing import plan
        return plan(clinics)
    except Exception:
        return {}, []


def _empty():
    return {"clinics_labelled": 0, "clinics_routable": 0, "boards": 0, "clinics_with_jobs": 0, "open_jobs": 0, "fresh_jobs": 0}


def _add(acc, c):
    acc["clinics_labelled"] += 1
    acc["clinics_routable"] += int(bool(c.get("routable")) and not c.get("walled"))
    acc["clinics_with_jobs"] += int((c.get("jobs_open") or 0) > 0)
    acc["open_jobs"] += int(c.get("jobs_open") or 0)
    acc["fresh_jobs"] += int(c.get("jobs_fresh") or 0)


def _pct(acc):
    n = acc["clinics_labelled"]
    return round(100.0 * acc["clinics_with_jobs"] / n, 1) if n else 0.0


def _errors(run):
    m = _ERR_RE.search(run.get("error") or "")
    return int(m.group(1)) if m else (1 if run.get("error") else 0)


def _firecrawl_account():
    """{'credits_remaining', 'credits_plan', 'tokens_remaining', 'tokens_plan', 'free_runs_left_today',
    'free_runs_per_day', 'agent_runs_today'} from FA.credits(); every value None when the API is unreachable."""
    keys = ("tokens_remaining", "tokens_plan", "free_runs_left_today", "free_runs_per_day", "agent_runs_today")
    try:
        from pflege_jobs.sources import firecrawl_agent as FA
        fc = FA.credits(timeout=10, historical=False) or {}
    except Exception:
        fc = {}
    out = {k: fc.get(k) for k in keys}
    out["credits_remaining"], out["credits_plan"] = fc.get("remaining"), fc.get("plan")
    return out


def _last_run(runs, key, members):
    """Most recent run that targeted this adapter (scope ats_type) or touched one of its clinics."""
    for r in runs:                                    # list_runs() is newest first
        ids = r.get("clinic_ids") or []
        hit = (r.get("scope") == "ats_type" and key in [v.strip() for v in (r.get("value") or "").split(",")]) \
            or (members and any(cid in members for cid in ids))
        if hit:
            return {"run_id": r["run_id"], "at": r.get("finished_at") or r.get("started_at") or r.get("queued_at"),
                    "status": r.get("status"), "rows": r.get("n_rows") or 0, "new": r.get("n_new") or 0, "errors": _errors(r)}
    return None


def compute():
    snap = D.snapshot()
    clinics = snap.get("clinics") or []
    keys = adapter_keys()
    known = _known_adapters() | set(keys)
    boards, unroutable = _plan(clinics)
    runs = R.list_runs(limit=300)
    credits_7d = R.usage_total(days=7)
    tokens_7d = R.tokens_total(days=7)
    account = _firecrawl_account()

    acc = {k: _empty() for k in keys}
    members = defaultdict(set)
    for c in clinics:
        cid = c.get("clinic_id")
        label = (c.get("ats_type") or "").strip()
        if not label and c.get("routable") and c.get("vendor"):
            label = c["vendor"]                       # unlabelled board routed through the default adapter
        if label in known:
            if label not in acc:
                acc[label] = _empty()               # an ats_type the routing knows but the spec list does not (e.g. bite_jobs)
            _add(acc[label], c)
            members[label].add(cid)
        if c.get("fetch") == FIRECRAWL:
            acc.setdefault(FIRECRAWL, _empty())
            _add(acc[FIRECRAWL], c)
            members[FIRECRAWL].add(cid)
    acc.setdefault(FIRECRAWL, _empty())

    board_count = Counter()
    for b in boards.values():
        if not b.get("walled"):
            board_count[b.get("vendor")] += 1
    fc_boards = {(c.get("careers_url") or "").strip().lower() for c in clinics if c.get("fetch") == FIRECRAWL and (c.get("careers_url") or "").strip()}

    rows = []
    for key, a in acc.items():
        a["boards"] = len(fc_boards) if key == FIRECRAWL else board_count.get(key, 0)
        row = {"adapter": key, **a, "coverage_pct": _pct(a), "last_run": _last_run(runs, key, members.get(key) or set())}
        if key == FIRECRAWL:
            row["credits_7d"] = credits_7d
            row["tokens_7d"] = tokens_7d
            row.update(account)
        rows.append(row)
    rows.sort(key=lambda r: (-r["clinics_labelled"], r["adapter"] == FIRECRAWL, r["adapter"]))

    totals = _empty()
    for c in clinics:                                 # each clinic once (rows overlap: label vs. Firecrawl fallback)
        _add(totals, c)
    totals["boards"] = sum(1 for b in boards.values() if not b.get("walled")) + len(fc_boards)
    totals["coverage_pct"] = _pct(totals)
    totals["credits_7d"] = credits_7d
    totals["tokens_7d"] = tokens_7d
    totals["free_runs_left_today"] = account.get("free_runs_left_today")
    jobs = snap.get("jobs") or []
    orphan = [j for j in jobs if not j.get("clinic_id")]
    unattributed = {"open_jobs": len(orphan), "fresh_jobs": sum(1 for j in orphan if j.get("fresh"))}
    totals["open_jobs_incl_unattributed"] = totals["open_jobs"] + unattributed["open_jobs"]      # == /api/stats open_jobs
    totals["fresh_jobs_incl_unattributed"] = totals["fresh_jobs"] + unattributed["fresh_jobs"]

    why = Counter(reason for _, reason in unroutable)
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rows": rows, "totals": totals, "unattributed": unattributed,
            "unroutable": [{"reason": k, "count": n} for k, n in sorted(why.items(), key=lambda kv: (-kv[1], kv[0]))]}


@router.get("/coverage")
def api_coverage():
    return compute()


# --- GET /api/billing/clinics: spend per clinic in a window (Kosten je Klinik) --------------------------------------
# Same booking rules as app/billing.py (duplicated on purpose: that module's helpers are private and in flux):
# a run is booked at finished_at | started_at | queued_at; usd = credits * settings firecrawl.eur_per_credit; a run is
# attributed to a clinic when it targets exactly one clinic; multi-clinic Firecrawl runs are split by the
# firecrawl_usage ledger rows (each carries clinic_id, credits, tokens); ledger rows without a run row count as one
# run of their clinic. Postings gained (n_new) are only attributable for single-clinic runs.
CLINIC_WINDOWS = ("today", "24h", "7d", "30d", "period", "custom")
DEFAULT_USD_PER_CREDIT = 0.0053


def _bc_parse(s):
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


def _bc_now():
    return datetime.now(timezone.utc)


def _bc_window(key, frm=None, to=None):
    """-> (from, to, note) in UTC; 'period' asks the Firecrawl API for the billing period and falls back to 30 days."""
    now = _bc_now()
    note = None
    if key == "today":
        f, t = now.replace(hour=0, minute=0, second=0, microsecond=0), now
    elif key in ("24h", "7d", "30d"):
        f, t = now - {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}[key], now
    elif key == "period":
        try:
            from pflege_jobs.sources import firecrawl_agent as FA
            fc = FA.credits(timeout=10) or {}
        except Exception:
            fc = {}
        f = _bc_parse(fc.get("period_start") or fc.get("hist_period_start"))
        t = min(_bc_parse(fc.get("period_end") or fc.get("hist_period_end")) or now, now)
        if not f:
            f, t, note = now - timedelta(days=30), now, "billing period unknown (Firecrawl API unreachable); showing the last 30 days"
    elif key == "custom":
        f, t = _bc_parse(frm), _bc_parse(to) or now
        if not f:
            raise HTTPException(400, "custom window needs from=ISO (and optional to=ISO)")
        if t < f:
            raise HTTPException(400, "to must not be before from")
    else:
        raise HTTPException(400, f"window must be one of {', '.join(CLINIC_WINDOWS)}")
    return f, t, note


def _bc_price():
    try:
        from . import settings as ST
        return float(ST.get_firecrawl().get("eur_per_credit") or DEFAULT_USD_PER_CREDIT)
    except Exception:
        return DEFAULT_USD_PER_CREDIT


def _bc_rows(sql, args=()):
    with R._lock, R.db() as c:
        try:
            return [dict(r) for r in c.execute(sql, args).fetchall()]
        except sqlite3.OperationalError:
            return []


def billing_clinics(window="today", frm=None, to=None, limit=30):
    f, t, note = _bc_window(window, frm, to)
    price = _bc_price()
    limit = max(1, min(int(limit or 30), 500))

    runs = []
    for r in _bc_rows("select * from crawl_runs order by run_id desc"):
        at = _bc_parse(r.get("finished_at") or r.get("started_at") or r.get("queued_at"))
        if at and f <= at <= t:
            r["_at"] = at
            runs.append(r)
    run_ids = {r["run_id"] for r in runs}
    by_run = {}
    orphans = []
    for l in _bc_rows("select * from firecrawl_usage order by id"):
        l["_at"] = _bc_parse(l.get("at"))
        if l.get("run_id") in run_ids:
            by_run.setdefault(l["run_id"], []).append(l)
        elif l.get("clinic_id") and l["_at"] and f <= l["_at"] <= t:
            orphans.append(l)

    acc = {}

    def bucket(cid):
        cid = str(cid)
        return acc.setdefault(cid, {"clinic_id": cid, "runs": 0, "runs_free": 0, "credits": 0, "tokens": 0, "new_postings": 0, "last_run_at": None, "_runs": set()})

    def touch(b, at, run_id):
        if run_id is not None and run_id in b["_runs"]:
            return
        b["_runs"].add(run_id if run_id is not None else object())
        b["runs"] += 1
        iso = at.isoformat(timespec="seconds")
        if not b["last_run_at"] or iso > b["last_run_at"]:
            b["last_run_at"] = iso

    for r in runs:
        lrows = by_run.get(r["run_id"], [])
        credits = int(r.get("credits_used") or 0)
        try:
            cids = r["clinic_ids"] if isinstance(r.get("clinic_ids"), list) else json.loads(r.get("clinic_ids") or "[]")
        except Exception:
            cids = []
        cid = cids[0] if len(cids) == 1 else (str(r.get("value")) if r.get("scope") == "clinic" and len(cids) <= 1 and r.get("value") else None)
        is_fc = r.get("mode") == "firecrawl" or bool(lrows)
        free = bool(is_fc and r.get("status") == "done" and credits == 0)
        if cid:
            b = bucket(cid)
            touch(b, r["_at"], r["run_id"])
            b["credits"] += credits
            b["tokens"] += sum(int(l["tokens"]) for l in lrows if isinstance(l.get("tokens"), int))
            b["new_postings"] += int(r.get("n_new") or 0)
            b["runs_free"] += int(free)
        else:                                          # multi-clinic run: split by the ledger rows that name a clinic
            for l in lrows:
                if not l.get("clinic_id"):
                    continue
                b = bucket(l["clinic_id"])
                touch(b, r["_at"], r["run_id"])
                b["credits"] += int(l.get("credits") or 0)
                b["tokens"] += int(l["tokens"]) if isinstance(l.get("tokens"), int) else 0
    for l in orphans:
        b = bucket(l["clinic_id"])
        touch(b, l["_at"], None)
        b["credits"] += int(l.get("credits") or 0)
        b["tokens"] += int(l["tokens"]) if isinstance(l.get("tokens"), int) else 0

    rows = []
    tot = {"clinics": 0, "runs": 0, "runs_free": 0, "credits": 0, "tokens": 0, "usd": 0.0, "new_postings": 0, "open_jobs": 0}
    for cid, b in acc.items():
        c = D.clinic(cid) or {}
        b.pop("_runs", None)
        b["clinic"] = c.get("name")
        b["town"] = c.get("town")
        b["open_jobs"] = int(c.get("jobs_open") or 0)
        b["usd"] = round(b["credits"] * price, 4)
        b["cost_per_posting_usd"] = round(b["usd"] / b["new_postings"], 4) if b["new_postings"] else None
        rows.append(b)
        tot["clinics"] += 1
        for k in ("runs", "runs_free", "credits", "tokens", "new_postings", "open_jobs"):
            tot[k] += b[k]
        tot["usd"] = round(tot["usd"] + b["usd"], 4)
    rows.sort(key=lambda b: (b["usd"], b["credits"], b["runs"], b["last_run_at"] or ""), reverse=True)
    tot["cost_per_posting_usd"] = round(tot["usd"] / tot["new_postings"], 4) if tot["new_postings"] else None
    return {"window": {"key": window, "from": f.isoformat(timespec="seconds"), "to": t.isoformat(timespec="seconds"), "note": note},
            "price_per_credit": price, "currency": "USD", "limit": limit, "rows": rows[:limit], "totals": tot}


@router.get("/billing/clinics")
def api_billing_clinics(window: str = "today", frm: str = Query(None, alias="from"), to: str = None, limit: int = 30):
    return billing_clinics(window, frm, to, limit)
