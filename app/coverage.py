"""GET /api/coverage -- per-adapter coverage table for the Clawl page.

One row per adapter key (every vendor label in crawlers.vendor_adapters.VENDORS plus the seeded/external routes
softgarden, bite, pi_asp, umantis, wp_jobs) and a synthetic 'firecrawl' row for every clinic that has no working
adapter (fetch == 'firecrawl': walled host, no label, no careers_url, unknown vendor). A clinic counts under its
ats_type label (or under the default wp_jobs route when it is unlabelled but routable); the Firecrawl row
overlaps with labelled-but-unroutable clinics on purpose, so `totals` counts each clinic once instead of summing
the rows. Reads the in-memory snapshot, the local SQLite run log and the routing plan; the one network call is
the Firecrawl balance for the `firecrawl` row (credits + Extract tokens + free agent runs left today), tolerant
of failure (keys stay None).
"""
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter

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
            row.update(account)
        rows.append(row)
    rows.sort(key=lambda r: (-r["clinics_labelled"], r["adapter"] == FIRECRAWL, r["adapter"]))

    totals = _empty()
    for c in clinics:                                 # each clinic once (rows overlap: label vs. Firecrawl fallback)
        _add(totals, c)
    totals["boards"] = sum(1 for b in boards.values() if not b.get("walled")) + len(fc_boards)
    totals["coverage_pct"] = _pct(totals)
    totals["credits_7d"] = credits_7d
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
