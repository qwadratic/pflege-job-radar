"""Read model: everything the UI reads comes from one in-memory snapshot of Supabase (postings, clinics,
per-site aggregates, routing, facets). Rebuilt on start, after every crawl, and when older than TTL.
The snapshot is small (a few thousand rows), so filtering happens in Python — see docs/performance.md
for what to move into Postgres once DDL is possible."""
import csv
import json
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import HTTPException

from . import config as A
from . import runs as R

JOB_COLS = ("posting_id,title,role_class,role_label,department_hint,department_raw,qualification_hint,employer,employer_id,employer_class,"
            "clinic_id,clinic_name,regierungsbezirk,versorgungsstufe,traegerart,clinic_beds,clinic_match_rule,city,plz,lat,lon,employment_types,contract,"
            "start_date,first_published,first_seen,last_seen,status,verify_status,verified_at,external_url,source_codes,n_observations,"
            "enr_housing,enr_tariff,enr_pay_grade,enr_contact_emails,enr_bonus,enr_childcare,provenance")
TTL = 600

# Personal data, member-and-up only. enr_contact_emails is scraped off the job ad and is regularly a named
# individual's work address (`stefan.wur-zer@kno.ag`), not a role mailbox -- GET /api/jobs is public and
# unauthenticated, so until 2026-09-10 an anonymous caller could pull the lot in one page (431 addresses in
# a single limit=2000 call, proven live). It is also exactly what a paying customer pays for, so it is
# redacted below `member`, not removed.
#
# Nothing else in JOB_COLS is personal or secret: employer/clinic/city/plz/lat/lon describe the hospital,
# external_url is the public ad, and clinic_match_rule / source_codes / n_observations / provenance /
# verify_status describe how this row was derived -- pipeline internals the board publishes on purpose
# (GET /api/ontology, /docs/api.md, skill/SKILL.md). The clinics select ("*") carries no contact-ish column
# at all: name, town, operator, landkreis, website, careers_url, beds, ats_type, board, route_reason,
# last_crawl_* -- public register data plus routing state, no e-mail, no phone, no person.
MEMBER_ONLY_JOB_FIELDS = ("enr_contact_emails",)

# Nulling the field alone did not redact anything: the same address is usually still in the ad body.
# GET /api/jobs/{id} returns the `postings` row, which carries `description` plus enr_requirements /
# enr_experience / enr_housing_evidence (docs/api.md:264) -- 569 of the 572 postings that have an address
# in enr_contact_emails carry it in the description text too, so an anonymous caller read it there instead.
# Below `member` every free-text value in the row therefore has e-mail-shaped substrings replaced.
#
# WHAT THIS CANNOT CATCH, and is not claimed to:
#   * obfuscated spellings -- "vorname.name (at) klinik-x.de", "name[at]klinik[punkt]de", an address split
#     across lines or written into an image;
#   * phone and fax numbers, which are personal data on exactly the same footing and are left in the text;
#   * a named individual plus a postal address in prose ("Bewerbungen an Frau Dr. Wurzer, Personalabteilung,
#     Musterweg 3"), which identifies a person without an address at all;
#   * anything the ad publishes on a linked page rather than in the body.
# It is a pattern match on one shape, not a personal-data filter. The real fix is not scraping the body
# into a public row; this only closes the hole that the redacted field left wide open.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
EMAIL_MASK = "[e-mail redacted: sign in]"


def _scrub_emails(v):
    """Every string in a value (also inside lists/dicts, e.g. `observations` and `provenance`) with the
    e-mail-shaped substrings masked. The `"@" in v` test is what keeps this cheap on a 2000-row page: only
    a string that could possibly contain an address is handed to the regex."""
    if isinstance(v, str):
        return EMAIL_RE.sub(EMAIL_MASK, v) if "@" in v else v
    if isinstance(v, list):
        return [_scrub_emails(x) for x in v]
    if isinstance(v, dict):
        return {k: _scrub_emails(x) for k, x in v.items()}
    return v


def redact(rows, role):
    """Rows as `role` may see them: owner/customer unchanged, anyone else gets MEMBER_ONLY_JOB_FIELDS as
    null and every other value scrubbed of e-mail addresses (see above). Copies every row -- these are the
    shared snapshot objects, mutating them would redact the cache itself for the owner too. Null, not
    dropped: the key stays part of the published row shape, so `fields=enr_contact_emails` is still a known
    field rather than a 400. Clinic rows carry none of these fields, so GET /api/clinics passes through with
    the scrub only (app/main.py:_list_response is shared)."""
    if role in ("owner", "customer"):
        return rows
    return [{k: (None if k in MEMBER_ONLY_JOB_FIELDS else _scrub_emails(v)) for k, v in r.items()} for r in rows]


INT64 = 2 ** 63
# Not a cap on anything anyone asked for: it is the range a SQLite INTEGER can hold, and every id this parser
# returns ends up bound into one. `?candidate_id=99999999999999999999` reached sqlite3 as a Python int and
# raised `OverflowError: Python int too large to convert to SQLite INTEGER` -- a 500 on three autopilot
# (route, parameter) pairs, measured 2026-09-11. A value outside the range cannot equal any stored row, so
# the honest answer is a 400 naming the parameter, not an exception echoed back. A caller that means
# "everything" writes ?limit=999999; the bound never truncates a result set, it rejects a value.


def int_param(p, name, default=None):
    """One place every numeric query parameter is read. `?limit=abc` used to reach a bare int() and answer
    500 with the ValueError text echoed back to an anonymous caller -- 9 (route, param) pairs did, across
    GET /api/jobs, /api/clinics and /api/plan. Absent or `?limit=` with nothing after it -> `default` (the
    previous `int(x or 0)` semantics); anything else that is not an integer is a 400 naming the parameter and
    the value. `?beds_min=%20` is in the second group, not the first: the callers test `p.get(name)` for
    truthiness before calling, and " " is truthy, so returning `default` there put None into a comparison."""
    v = p.get(name)
    if v is None or v == "":
        return default
    try:
        n = int(str(v).strip())
    except ValueError:
        raise HTTPException(400, f"{name} must be an integer, got {str(v)[:60]!r}")
    if not -INT64 <= n < INT64:
        raise HTTPException(400, f"{name} must fit in a 64-bit integer, got {str(v)[:60]!r}")
    return n


PAGING = {
    "envelope": ["total", "limit", "offset", "next_offset", "rows"],
    "max_page_size": None,
    "routes": ["GET /api/jobs", "GET /api/clinics", "GET /api/plan", "GET /api/autopilot/*"],
    "note": "`limit` is always the limit the caller asked for; there is no server maximum, so ?limit=999999 "
            "returns every matching row. `next_offset` is the offset to ask for next, or null when this page "
            "reached the end of the list -- that null is the only 'that was everything' signal, do not infer "
            "it from len(rows) == limit. Until 2026-09-11 limit was silently clamped to 2000 and the clamp "
            "was echoed back as if it were the request.",
    "ndjson": "Accept: application/x-ndjson drops the envelope, so the same information is in the "
              "Content-Range response header: `rows <offset>-<last>/<total>` (`rows */<total>` for an empty page).",
    "stability": "offset paging over a snapshot that is rebuilt every 600s and after every crawl: rows can "
                 "shift between two calls of one sweep, so a sweep can miss or repeat rows. GET /api/stats -> "
                 "snapshot_at changes when the list underneath changed; compare it across the sweep.",
}


def page(rows, p, default_limit):
    """One page of an already-materialised list, plus the envelope every paged route shares.

    NO MAXIMUM PAGE SIZE. Until 2026-09-11 this was `min(limit, 2000)` and then reported `"limit": 2000` --
    `GET /api/jobs?limit=999999` answered 2000 of 2725 open postings (measured live) and named the truncation
    as if 2000 had been the request; the NDJSON path dropped the envelope entirely, so a streaming caller got
    exactly 2000 lines and no signal at all. A cap reported as success is what CLAUDE.md forbids.

    Why serve it rather than 400 on a maximum: the ceiling protected nothing. filter_jobs() / plan_rows() /
    autopilot's db.rows() all build the whole list before this function sees it, so cutting it here saved no
    query and no memory -- only the bytes the caller explicitly asked for. And `?limit=999999` is already how
    this API spells "everything" (see int_param above). If a page size ever does need a ceiling, it belongs
    here as a 400 naming the maximum, never as a smaller `limit` echoed back.

    `next_offset` is the resume point, None once the page reached the end. It is the field a sweeping agent
    tests; `len(rows) < limit` is not the same test (a filter whose total is an exact multiple of limit ends
    on a full page). It is not a cursor -- see PAGING["stability"]."""
    limit = int_param(p, "limit", default_limit)
    offset = int_param(p, "offset", 0)
    # Clamping these was the same lie in miniature: `?limit=0` returned one row called "limit": 1, and
    # `?offset=-5` silently became page one. A nonsense value is the caller's mistake, said out loud.
    if limit < 0:
        raise HTTPException(400, f"limit must be >= 0, got {limit}")
    if offset < 0:
        raise HTTPException(400, f"offset must be >= 0, got {offset}")
    out = rows[offset:offset + limit]
    nxt = offset + len(out)
    return {"total": len(rows), "limit": limit, "offset": offset,
            "next_offset": nxt if nxt < len(rows) else None, "rows": out}


async def json_body(request):
    """One place every JSON request body is read, the sibling of int_param() above. `await request.json()`
    raises on a body that is not JSON, and the `body.get(...)` that always follows raises on a JSON scalar or
    list -- 41 (route, shape) pairs answered 500 with the Python exception echoed back on 2026-09-11, two of
    them (POST /api/auth/login, POST /api/auth/agent) to anonymous callers at the front door.

    An EMPTY body is `{}`, not a 400: several routes already documented it that way ("Body may carry
    {reason}") and every field they read is optional. Anything else that is not a JSON object is a 400 saying
    which shape arrived, so a caller sees its own mistake instead of a stack frame."""
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, f"body must be a JSON object, got {type(body).__name__}")
    return body


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
        # Display-only signal for a human sorting/scanning the clinic list -- a big hospital with very
        # few open postings is worth a human's second look, but this number is never read by any
        # automated decision (hunter/campaign/spend_gate do not import or sort on it): staffing ratios
        # genuinely vary by hospital type/specialty, so a low number here is a prompt to go check, not
        # evidence of anything on its own.
        c["jobs_per_100_beds"] = round(c["jobs_open"] / c["beds"] * 100, 1) if c.get("beds") else None
        c.update(routing.get(c["clinic_id"], {"routable": False, "route_reason": "unknown", "walled": False, "board": None, "vendor": None}))
        lr = last.get(c["clinic_id"]) or {}
        c["last_crawl_at"], c["last_crawl_status"], c["last_crawl_mode"] = lr.get("at"), lr.get("status"), lr.get("mode")
        c["career_profile"] = profiles.get(c["clinic_id"])
        c["ats_type"] = (c.get("ats_type") or "").strip()
        # how a scrape would reach this site: a vendor adapter, or the Firecrawl agent (everything is scrapable)
        via_adapter = bool(c.get("routable")) and not c.get("walled")
        c["fetch"] = "adapter" if via_adapter else "firecrawl"
        c["fetch_label"] = (c.get("vendor") or c["ats_type"]) if via_adapter else "Firecrawl"
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
        rows = [c for c in rows if (c.get("beds") or 0) >= int_param(p, "beds_min")]
    if p.get("beds_max"):
        rows = [c for c in rows if (c.get("beds") or 0) <= int_param(p, "beds_max")]
    if p.get("has_jobs") in ("1", "true"):
        rows = [c for c in rows if c["jobs_open"] > 0]
    if p.get("routable") in ("1", "true"):
        rows = [c for c in rows if c["routable"]]
    elif p.get("routable") in ("0", "false"):
        rows = [c for c in rows if not c["routable"]]
    if p.get("fetch") in ("adapter", "firecrawl"):
        rows = [c for c in rows if c.get("fetch") == p["fetch"]]
    if p.get("q"):
        tax = snapshot()["taxonomy"]
        ats_labels = {k: (v.get("label") if isinstance(v, dict) else v) for k, v in (tax.get("ats_types") or {}).items()}
        rows = [c for c in rows if _q_match(p["q"], c["name"], c.get("town"), c.get("operator"), c.get("landkreis"), c.get("clinic_id"),
                                            c.get("ats_type"), ats_labels.get(c.get("ats_type") or ""), c.get("fetch_label"), c.get("regierungsbezirk"),
                                            c.get("status"), c.get("versorgungsstufe"), c.get("traegerart"), c.get("size"),
                                            " ".join(c.get("fachrichtungen") or []))]
    sort = p.get("sort") or "-jobs_open"
    desc = sort.startswith("-")
    key = sort.lstrip("-+")
    if key not in ("jobs_open", "jobs_fresh", "beds", "name", "town", "regierungsbezirk", "ats_type", "last_crawl_at", "fetch_label", "landkreis", "versorgungsstufe", "jobs_per_100_beds"):
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
        days = int_param(p, "fresh_days")
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        except (OverflowError, ValueError):     # timedelta's own range, e.g. fresh_days=99999999999999999999
            raise HTTPException(400, f"fresh_days={days} is outside the range a date can express")
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


def inbox_summary(recent=25):
    """Backlog visibility for GET /api/inbox: total row count (via rest_count -- 4297 rows is too
    much to page just to len() it), then the unprocessed rows themselves (small enough, ~445, to
    break down in Python since PostgREST has no group-by), plus the most recent rows for a log view."""
    total = A.rest_count("inbox")
    waiting = A.rest_get_all("inbox", {"select": "inbox_id,kind,collector,source_host,received_at", "processed_at": "is.null", "order": "inbox_id"})
    host_clinic = {}
    for c in clinics():
        for u in (c.get("board"), c.get("careers_url")):
            if not u:
                continue
            host = urlparse(u).netloc
            if host:
                host_clinic.setdefault(host, set()).add(c["name"])
    host_clinic = {h: ", ".join(sorted(v)) for h, v in host_clinic.items()}
    oldest_at = waiting[0]["received_at"] if waiting else None      # order=inbox_id ascending -> row 0 is the oldest
    oldest_age_s = None
    if oldest_at:
        try:
            oldest_age_s = int((datetime.now(timezone.utc) - datetime.fromisoformat(oldest_at.replace("Z", "+00:00"))).total_seconds())
        except Exception:
            oldest_age_s = None
    n = max(0, min(int(recent or 0), 200))
    recent_rows = A.rest_get("inbox", {"select": "inbox_id,kind,collector,source_host,source_url,received_at,processed_at,process_note",
                                       "order": "inbox_id.desc", "limit": n}) if n else []
    return {"total": total, "unprocessed": len(waiting), "by_kind": _count(waiting, "kind"),
            "waiting_by_collector": _count(waiting, "collector"), "waiting_by_host": _count(waiting, "source_host", label=host_clinic),
            "oldest_unprocessed_at": oldest_at, "oldest_unprocessed_age_s": oldest_age_s, "recent": recent_rows}


# --- cities / plan ----------------------------------------------------------------------------
def cities(q=None):
    """One row per registry town: hospitals, open + fresh jobs (via clinic_id), how many have a known ATS."""
    by = {}
    for c in clinics():
        t = (c.get("town") or "").strip()
        if not t:
            continue
        r = by.setdefault(t, {"city": t, "regierungsbezirk": c.get("regierungsbezirk"), "landkreis": c.get("landkreis"),
                              "clinics": 0, "jobs_open": 0, "jobs_fresh": 0, "ats_known": 0, "beds": 0})
        r["clinics"] += 1
        r["jobs_open"] += c["jobs_open"]
        r["jobs_fresh"] += c["jobs_fresh"]
        r["ats_known"] += int(bool(c.get("ats_type")))
        r["beds"] += c.get("beds") or 0
    rows = sorted(by.values(), key=lambda r: (-r["jobs_open"], -r["clinics"], r["city"]))
    if q:
        rows = [r for r in rows if _q_match(q, r["city"], r["regierungsbezirk"], r["landkreis"])]
    return rows


PLAN_SOURCE_URL = "https://www.stmgp.bayern.de/wp-content/uploads/2026/02/51.-Fortschreibung-Krankenhausplan-des-Freistaates-Bayern-Stand-01012026.pdf"
PLAN_COLS = ("clinic_id", "name", "town", "operator", "landkreis", "regierungsbezirk", "status", "versorgungsstufe", "traegerart",
             "beds", "day_places", "fachrichtungen", "parse_quality", "source", "website", "careers_url", "ats_type")


def plan_rows(p=None):
    """The post-processed Krankenhausplan (registry CSV, every column) as rows; DB-side aggregates added."""
    p = p or {}
    by = snapshot()["by_clinic"]
    rows = []
    for r in registry_csv_rows():
        row = {k: r.get(k) for k in PLAN_COLS}
        row["beds"] = int(r["beds"]) if (r.get("beds") or "").isdigit() else None
        row["day_places"] = int(r["day_places"]) if (r.get("day_places") or "").isdigit() else None
        row["fachrichtungen"] = [x for x in (r.get("fachrichtungen") or "").replace(",", "|").split("|") if x]
        live = by.get(r["clinic_id"]) or {}
        row["size"] = live.get("size") if live else size_bucket(row["beds"])
        row["jobs_open"] = live.get("jobs_open", 0)
        rows.append(row)
    for key in ("regierungsbezirk", "status", "traegerart", "versorgungsstufe"):
        vals = set(_split(p.get(key)))
        if vals:
            rows = [r for r in rows if r.get(key) in vals]
    if p.get("q"):
        rows = [r for r in rows if _q_match(p["q"], r["clinic_id"], r["name"], r["town"], r["operator"], r["landkreis"], r["regierungsbezirk"],
                                            r["status"], r["versorgungsstufe"], r["traegerart"], " ".join(r["fachrichtungen"]), r.get("ats_type"))]
    sort = p.get("sort") or "clinic_id"
    desc = sort.startswith("-")
    key = sort.lstrip("-+")
    if key not in PLAN_COLS + ("size", "jobs_open"):
        key = "clinic_id"
    rows = sorted(rows, key=lambda r: (r.get(key) is None, r.get(key) if not isinstance(r.get(key), str) else r.get(key).lower()), reverse=desc)
    return rows
