"""Read model: everything the UI reads comes from one in-memory snapshot of Supabase (postings, clinics,
per-site aggregates, routing, facets). Rebuilt on start, after every crawl, and when older than TTL.
The snapshot is small (a few thousand rows), so filtering happens in Python — see docs/performance.md
for what to move into Postgres once DDL is possible."""
import csv
import json
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from . import config as A
from . import runs as R

JOB_COLS = ("posting_id,title,role_class,role_label,department_hint,department_raw,qualification_hint,employer,employer_id,employer_class,"
            "clinic_id,clinic_name,regierungsbezirk,versorgungsstufe,traegerart,clinic_beds,clinic_match_rule,city,plz,lat,lon,employment_types,contract,"
            "start_date,first_published,first_seen,last_seen,status,verify_status,verified_at,external_url,source_codes,n_observations,"
            "enr_housing,enr_tariff,enr_pay_grade,enr_contact_emails,enr_bonus,enr_childcare,provenance")
TTL = 600

_lock = threading.RLock()
_ready = threading.Event()          # set once the first snapshot exists
_snap = {"at": 0.0, "jobs": [], "clinics": [], "by_clinic": {}, "facets": {}, "taxonomy": {}, "loading": False, "error": None}


def _load_json(path, fallback_name):
    for p in (path, A.FALLBACK_DIR / fallback_name):
        try:
            return json.loads(open(p, encoding="utf-8").read())
        except Exception:
            continue
    return {}


def taxonomy():
    return _load_json(A.TAXONOMY_PATH, "taxonomy.json")


def size_bucket(beds, tax=None):
    tax = tax or taxonomy()
    for b in tax.get("size_buckets") or _DEFAULT_SIZES:
        lo, hi = b.get("min", 0), b.get("max")
        if beds is None:
            return None
        if beds >= lo and (hi is None or beds <= hi):
            return b["key"]
    return None


_DEFAULT_SIZES = [{"key": "S", "label": "< 100 Betten", "max": 99}, {"key": "M", "label": "100–299", "min": 100, "max": 299},
                  {"key": "L", "label": "300–799", "min": 300, "max": 799}, {"key": "XL", "label": "800+", "min": 800}]


def _fresh(job, cutoff):
    """Published (or, when the source gives no date, first seen) within the last FRESH_DAYS."""
    v = job.get("first_published") or job.get("first_seen") or ""
    return v[:10] >= cutoff


def _routing(clinics):
    try:
        from crawlers.routing import plan
        boards, unroutable = plan(clinics)
    except Exception as e:                                       # never let a routing bug hide the registry
        return {c["clinic_id"]: {"routable": False, "route_reason": f"routing error: {str(e)[:60]}", "walled": False, "board": None, "vendor": None} for c in clinics}
    info = {}
    for url, b in boards.items():
        for c in b["clinics"]:
            info[c["clinic_id"]] = {"routable": not b["walled"], "route_reason": "walled host (bot wall)" if b["walled"] else "adapter " + (b["adapter"] or ""),
                                    "walled": b["walled"], "board": b["url"], "vendor": b["vendor"], "board_shared": len(b["clinics"])}
    for c, why in unroutable:
        info[c["clinic_id"]] = {"routable": False, "route_reason": why, "walled": False, "board": None, "vendor": None, "board_shared": 0}
    return info


def _build():
    t0 = time.time()
    jobs = A.rest_get_all("v_postings", {"select": JOB_COLS, "status": "eq.open", "order": "posting_id"})
    clinics = A.rest_get_all("clinics", {"select": "*", "order": "clinic_id"})
    tax = taxonomy()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=A.FRESH_DAYS)).strftime("%Y-%m-%d")
    agg = defaultdict(lambda: {"jobs_open": 0, "jobs_fresh": 0, "jobs_live": 0})
    for j in jobs:
        j["fresh"] = _fresh(j, cutoff)
        cid = j.get("clinic_id")
        if cid:
            a = agg[cid]
            a["jobs_open"] += 1
            a["jobs_fresh"] += int(j["fresh"])
            a["jobs_live"] += int(j.get("verify_status") == "live")
    routing = _routing(clinics)
    last = R.last_run_per_clinic()
    profiles = R.career_profiles()
    for c in clinics:
        c["fachrichtungen"] = [x for x in (c.get("fachrichtungen") or "").replace(",", "|").split("|") if x]
        c["size"] = size_bucket(c.get("beds"), tax)
        c.update(agg.get(c["clinic_id"], {"jobs_open": 0, "jobs_fresh": 0, "jobs_live": 0}))
        c.update(routing.get(c["clinic_id"], {"routable": False, "route_reason": "unknown", "walled": False, "board": None, "vendor": None}))
        lr = last.get(c["clinic_id"]) or {}
        c["last_crawl_at"], c["last_crawl_status"], c["last_crawl_mode"] = lr.get("at"), lr.get("status"), lr.get("mode")
        c["career_profile"] = profiles.get(c["clinic_id"])
        c["ats_type"] = (c.get("ats_type") or "").strip()
    by_clinic = {c["clinic_id"]: c for c in clinics}
    for j in jobs:
        c = by_clinic.get(j.get("clinic_id") or "")
        j["clinic_town"] = c["town"] if c else None
        j["clinic_size"] = c["size"] if c else None
    snap = {"at": time.time(), "jobs": jobs, "clinics": clinics, "by_clinic": by_clinic, "taxonomy": tax,
            "facets": _facets(jobs, clinics, tax), "built_in": round(time.time() - t0, 1), "error": None, "loading": False}
    return snap


def _count(items, key, label=None, split=False):
    c = Counter()
    for it in items:
        v = it.get(key)
        if split and isinstance(v, list):
            for x in v:
                c[x] += 1
        elif v not in (None, ""):
            c[v] += 1
    out = [{"v": k, "n": n} for k, n in sorted(c.items(), key=lambda kv: (-kv[1], str(kv[0])))]
    if label:
        for o in out:
            o["label"] = label.get(o["v"], o["v"])
    return out


def _facets(jobs, clinics, tax):
    fach = tax.get("fachrichtungen") or {}
    beds = [c["beds"] for c in clinics if isinstance(c.get("beds"), int)]
    ats_labels = {k: (v.get("label") if isinstance(v, dict) else v) for k, v in (tax.get("ats_types") or {}).items()}
    ats = _count(clinics, "ats_type", ats_labels)
    n_blank = sum(1 for c in clinics if not c.get("ats_type"))
    if n_blank:
        ats.append({"v": "", "n": n_blank, "label": ats_labels.get("", "unbekannt")})
    role_labels = {j["role_class"]: j.get("role_label") for j in jobs if j.get("role_class")}
    role_labels.update(tax.get("role_class") or {})
    return {
        "cities": _count(clinics, "town"), "job_cities": _count(jobs, "city"),
        "regierungsbezirk": _count(clinics, "regierungsbezirk"), "landkreis": _count(clinics, "landkreis"),
        "ats_type": ats, "traegerart": _count(clinics, "traegerart", tax.get("traegerart")),
        "versorgungsstufe": _count(clinics, "versorgungsstufe", tax.get("versorgungsstufe")),
        "status": _count(clinics, "status", tax.get("status")),
        "fachrichtungen": _count(clinics, "fachrichtungen", fach, split=True),
        "size": _count(clinics, "size", {b["key"]: b.get("label") for b in (tax.get("size_buckets") or _DEFAULT_SIZES)}),
        "role_class": _count(jobs, "role_class", role_labels), "department_hint": _count(jobs, "department_hint"),
        "employment_types": _count(jobs, "employment_types", split=True), "contract": _count(jobs, "contract"),
        "enr_tariff": _count(jobs, "enr_tariff"), "verify_status": _count(jobs, "verify_status"),
        "beds": {"min": min(beds) if beds else 0, "max": max(beds) if beds else 0},
        "size_buckets": tax.get("size_buckets") or _DEFAULT_SIZES,
    }


def snapshot(force=False, wait=180):
    """Current snapshot; builds synchronously when empty (or waits for the build in flight),
    refreshes in the background when stale."""
    with _lock:
        stale = time.time() - _snap["at"] > TTL
        empty = not _snap["clinics"]
        loading = _snap["loading"]
    if force or (empty and not loading):
        refresh()
    elif empty and loading:
        _ready.wait(wait)
    elif stale and not loading:
        threading.Thread(target=refresh, daemon=True).start()
    return _snap


def refresh():
    global _snap
    with _lock:
        if _snap["loading"]:
            return _snap
        _snap["loading"] = True
    try:
        new = _build()
        with _lock:
            _snap = new
        _ready.set()
    except Exception as e:
        with _lock:
            _snap["loading"] = False
            _snap["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            _snap["at"] = time.time() - TTL + 60          # retry in a minute, keep serving what we have
    return _snap


def clinics():
    return snapshot()["clinics"]


def jobs():
    return snapshot()["jobs"]


def clinic(clinic_id):
    return snapshot()["by_clinic"].get(str(clinic_id))


def registry_csv_rows():
    with open(A.CLINICS_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def towns():
    from pflege_jobs.classify import norm_text
    return {norm_text(c["town"]) for c in clinics() if c.get("town")}


# --- filtering ------------------------------------------------------------------------------
def _split(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _q_match(q, *fields):
    q = (q or "").lower().strip()
    if not q:
        return True
    hay = " ".join(str(f) for f in fields if f).lower()
    return all(tok in hay for tok in q.split())


def filter_clinics(p):
    rows = clinics()
    city = {c.lower() for c in _split(p.get("city"))}
    fach = set(_split(p.get("fach")))
    size = set(_split(p.get("size")))
    ats = _split(p.get("ats_type"))
    for key in ("regierungsbezirk", "landkreis", "traegerart", "versorgungsstufe", "status"):
        vals = set(_split(p.get(key)))
        if vals:
            rows = [c for c in rows if c.get(key) in vals]
    if ats:
        want = set(ats)
        blank = "" in want or "unknown" in want or "none" in want
        rows = [c for c in rows if c.get("ats_type") in want or (blank and not c.get("ats_type"))]
    if city:
        rows = [c for c in rows if (c.get("town") or "").lower() in city]
    if fach:
        rows = [c for c in rows if fach & set(c.get("fachrichtungen") or [])]
    if size:
        rows = [c for c in rows if c.get("size") in size]
    if p.get("beds_min"):
        rows = [c for c in rows if (c.get("beds") or 0) >= int(p["beds_min"])]
    if p.get("beds_max"):
        rows = [c for c in rows if (c.get("beds") or 0) <= int(p["beds_max"])]
    if p.get("has_jobs") in ("1", "true"):
        rows = [c for c in rows if c["jobs_open"] > 0]
    if p.get("routable") in ("1", "true"):
        rows = [c for c in rows if c["routable"]]
    elif p.get("routable") in ("0", "false"):
        rows = [c for c in rows if not c["routable"]]
    if p.get("q"):
        rows = [c for c in rows if _q_match(p["q"], c["name"], c.get("town"), c.get("operator"), c.get("landkreis"), c.get("clinic_id"))]
    sort = p.get("sort") or "-jobs_open"
    desc = sort.startswith("-")
    key = sort.lstrip("-+")
    if key not in ("jobs_open", "jobs_fresh", "beds", "name", "town", "regierungsbezirk", "ats_type", "last_crawl_at"):
        key = "jobs_open"
    rows = sorted(rows, key=lambda c: ((c.get(key) is None), c.get(key) if not isinstance(c.get(key), str) else c.get(key).lower()), reverse=desc)
    if desc:                                                    # None last in both directions
        rows = [c for c in rows if c.get(key) is not None] + [c for c in rows if c.get(key) is None]
    return rows


def filter_jobs(p):
    rows = jobs()
    if p.get("clinic_id"):
        ids = set(_split(p["clinic_id"]))
        rows = [j for j in rows if j.get("clinic_id") in ids]
    for key in ("role_class", "department_hint", "regierungsbezirk", "contract", "enr_tariff", "versorgungsstufe", "traegerart", "qualification_hint"):
        vals = set(_split(p.get(key)))
        if vals:
            rows = [j for j in rows if j.get(key) in vals]
    city = {c.lower() for c in _split(p.get("city"))}
    if city:
        rows = [j for j in rows if (j.get("city") or "").lower() in city or (j.get("clinic_town") or "").lower() in city]
    et = set(_split(p.get("employment_types")))
    if et:
        rows = [j for j in rows if et & set(j.get("employment_types") or [])]
    if p.get("housing") in ("1", "true"):
        rows = [j for j in rows if j.get("enr_housing")]
    if p.get("verify"):
        vs = set(_split(p["verify"]))
        rows = [j for j in rows if j.get("verify_status") in vs]
    if p.get("fresh_days"):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(p["fresh_days"]))).strftime("%Y-%m-%d")
        rows = [j for j in rows if _fresh(j, cutoff)]
    if p.get("size"):
        sz = set(_split(p["size"]))
        rows = [j for j in rows if j.get("clinic_size") in sz]
    if p.get("q"):
        rows = [j for j in rows if _q_match(p["q"], j.get("title"), j.get("employer"), j.get("city"), j.get("department_raw"), j.get("clinic_name"))]
    sort = p.get("sort") or "-first_published"
    desc = sort.startswith("-")
    key = sort.lstrip("-+")
    if key not in ("first_published", "first_seen", "last_seen", "title", "city", "employer", "role_class"):
        key = "first_published"
    rows = sorted(rows, key=lambda j: (j.get(key) is None, j.get(key) or ""), reverse=desc)
    if desc:
        rows = [j for j in rows if j.get(key) is not None] + [j for j in rows if j.get(key) is None]
    return rows


def job_detail(posting_id):
    full = A.rest_get("postings", {"select": "*", "posting_id": f"eq.{int(posting_id)}"})
    if not full:
        return None
    base = next((j for j in jobs() if j["posting_id"] == int(posting_id)), None) or {}
    row = {**base, **{k: v for k, v in full[0].items() if k not in ("fuzzy_key",)}}
    obs = A.rest_get("posting_observations", {"select": "source_id,source_url,observed_at,sources(code)", "posting_id": f"eq.{int(posting_id)}", "order": "observed_at.desc"})
    row["observations"] = [{"source_code": (o.get("sources") or {}).get("code"), "source_id": o["source_id"], "source_url": o["source_url"], "observed_at": o["observed_at"]} for o in obs]
    if not row.get("source_url"):
        row["source_url"] = next((o["source_url"] for o in row["observations"] if o.get("source_url")), row.get("external_url"))
    c = clinic(row.get("clinic_id") or "")
    if c:
        row["clinic"] = {k: c.get(k) for k in ("clinic_id", "name", "town", "regierungsbezirk", "ats_type", "beds", "size", "careers_url", "website")}
    return row


def stats():
    s = snapshot()
    js, cs = s["jobs"], s["clinics"]
    last = R.last_finished_run()
    return {"open_jobs": len(js), "fresh_jobs": sum(1 for j in js if j.get("fresh")), "clinics": len(cs),
            "clinics_active": sum(1 for c in cs if c.get("status") != "nicht_mehr_im_plan"),
            "clinics_with_jobs": sum(1 for c in cs if c["jobs_open"] > 0), "clinics_routable": sum(1 for c in cs if c["routable"]),
            "clinics_with_ats": sum(1 for c in cs if c.get("ats_type")),
            "last_crawl": {"at": last["finished_at"], "status": last["status"], "run_id": last["run_id"]} if last else {"at": None, "status": None},
            "snapshot_at": datetime.fromtimestamp(s["at"], timezone.utc).isoformat(timespec="seconds") if s["at"] else None,
            "snapshot_error": s.get("error")}
