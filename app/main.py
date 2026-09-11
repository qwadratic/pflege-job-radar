"""FastAPI entry point. `uvicorn app.main:app --port 8501`.

Serves two frontends (web/index.html = default light page at /, web/pro.html = full dashboard at /pro), the agent skill, docs, and the JSON API documented in docs/api.md.
"""
import json
import pathlib
import re
import threading
from datetime import datetime, timezone
from http.client import responses
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from pflege_jobs import schema as SCH               # OBS/CLINIC/LINK/VERIFY column specs, for GET /api/ingest/schemas

from . import auth as AU                            # SCOPES / AGENT_ROUTES / INGEST_SCOPE: the agent contract
from . import campaign as CAM
from . import config as A
from . import crawl as CR
from . import cv as CV
from . import data as D
from . import mechanics as ME
from . import runs as R
from . import scheduler as S
from . import schedules as SC
from . import search as SE
from . import settings as ST
from . import targets as T

try:                                                        # operator console for the recruiting funnel (docs/autopilot.md)
    from .autopilot.api import router as _autopilot_router
except Exception as _e:                                     # pragma: no cover - keeps the job board up if the PoC is broken
    _autopilot_router = None
    print("autopilot router not loaded:", _e)

app = FastAPI(title="pflege-board", version="1.0", docs_url="/api/openapi-ui", redoc_url=None, openapi_url="/api/openapi.json")
if _autopilot_router is not None:
    app.include_router(_autopilot_router, prefix="/api/autopilot", tags=["autopilot"])
from .coverage import router as _coverage_router                    # GET /api/coverage (Clawl page)
from .firecrawl_hooks import router as _firecrawl_router           # POST /api/firecrawl/webhook, spend gate, kill switch
app.include_router(_coverage_router, prefix="/api", tags=["coverage"])
app.include_router(_firecrawl_router, prefix="/api", tags=["firecrawl"])
from .hunter_api import router as _hunter_router                   # /api/hunter/* (resilient Firecrawl runner)
app.include_router(_hunter_router, prefix="/api", tags=["hunter"])
from .billing import router as _billing_router                     # GET /api/billing (spend report)
app.include_router(_billing_router, prefix="/api", tags=["billing"])
from .auth import router as _auth_router                            # GET /api/me, magic-link login (owner / tailnet / customer)
from .stripe_gate import router as _stripe_router                  # Stripe pay-per-closed-posting gate
app.include_router(_auth_router, prefix="/api", tags=["auth"])
app.include_router(_stripe_router, prefix="/api", tags=["stripe"])
from .auth import install as _install_auth                          # identity middleware: owner / tailnet / magic link / customer
_install_auth(app)

# --- security response headers ---------------------------------------------------------------
# No response carried CSP, X-Frame-Options or X-Content-Type-Options until 2026-09-10.
#
# The CSP below is NOT strict, and says so out loud: every page this app serves is a single built HTML file
# with its whole SPA in an inline <script> and its whole stylesheet in an inline <style> (web/*.template.html
# -> web/build.py), so 'unsafe-inline' in script-src/style-src is the difference between a policy that ships
# and a blank page. What it does buy, today:
#   * script-src pins the only two third-party script origins to cdnjs (with SRI on both tags,
#     web/pro.template.html) -- an injected <script src="https://evil/..."> no longer loads;
#   * connect-src 'self' means an injected script cannot exfiltrate the page's data to another origin;
#   * object-src 'none' + base-uri 'self' + form-action 'self' close the classic <object>/<base>/form
#     redirection tricks; frame-ancestors 'none' is the CSP form of the X-Frame-Options below.
# img-src is the other loose one, and for a reason that is in the page, not in this file: clinicPhoto()
# (web/index.template.html:357, web/pro.template.html) shows each hospital's own favicon/apple-touch-icon,
# read off that hospital's website -- 399 third-party origins that change as the register does. `https:`
# there is the honest value; an origin list built from clinics.csv is on the open list. Verified in Chromium
# against /?mock=1: with `img-src 'self' data:` every clinic mark fell back to its initials.
# The strict version (per-response nonce, or sha256 hashes of every inline block, and dropping
# 'unsafe-inline') needs the build to emit the hashes and the server to read them -- it is on the open list,
# not quietly claimed here.
CSP = ("default-src 'self'; "
       "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; "
       "img-src 'self' data: https:; "
       "connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")
SECURITY_HEADERS = {"Content-Security-Policy": CSP,
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY",                       # for the browsers that ignore frame-ancestors
                    "Referrer-Policy": "no-referrer"}


@app.middleware("http")
async def _security_headers(request, call_next):
    """Added after _install_auth, so it is the OUTER middleware: the 401 problem+json and the 303 to /login
    that AuthMiddleware returns itself get the headers too, not just the ones a route produced."""
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


@app.on_event("startup")
def _startup():
    R.start_worker(CR.dispatch)
    threading.Thread(target=D.refresh, daemon=True).start()
    S.start()


# --- errors: RFC 9457 problem details (docs/errors.md) ----------------------------------------
# The two handlers below see only a status code and a detail string, never which route raised, so the
# problem type is read off the detail text first and the status second. Anything unmatched stays
# "about:blank", the type RFC 9457 defines as "no semantics beyond the status code".
PROBLEM_BY_DETAIL = (("budget exhausted", "budget-exceeded"),          # app/main.py:263, :354
                     ("adapter covers it", "adapter-covers-it"),       # slug reserved; today only a plan reason, app/crawl.py:244
                     ("already queued or running", "run-in-progress"), # app/main.py:338
                     ("nothing to cancel", "run-in-progress"))         # app/main.py:319
PROBLEM_BY_STATUS = {400: "invalid-body", 401: "unauthenticated", 403: "insufficient_scope",
                     422: "invalid-body", 429: "quota-exceeded"}


def _problem_type(status, detail):
    d = detail.lower()
    words = d.split()
    if words[:1] == ["unknown"] and len(words) > 1:     # unknown clinic|posting|run|schedule|mechanic
        return "unknown-" + words[1]
    for needle, slug in PROBLEM_BY_DETAIL:
        if needle in d:
            return slug
    return PROBLEM_BY_STATUS.get(status, "about:blank")


def _problem(request, status, detail):
    """RFC 9457 body. `error` is the legacy key, kept for one release: web/index.template.html:237 and
    web/pro.template.html:397 read b.error and would show "HTTP 404" instead of the message without it."""
    slug = _problem_type(status, detail)
    body = {"type": slug if slug == "about:blank" else f"/docs/errors.md#{slug}",
            "title": responses.get(status, "Error") if slug == "about:blank" else slug.replace("_", " ").replace("-", " ").capitalize(),
            "status": status, "detail": detail, "instance": request.url.path, "error": detail}
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


@app.exception_handler(StarletteHTTPException)
async def _http_err(request, exc):
    return _problem(request, exc.status_code, str(exc.detail))


@app.exception_handler(Exception)
async def _any_err(request, exc):
    return _problem(request, 500, f"{type(exc).__name__}: {str(exc)[:300]}")


def _page(rows, p, default_limit):
    """`p` is the query string, not a pair of already-parsed numbers: limit/offset are read through
    D.int_param so a non-integer is a 400 naming the parameter instead of a 500 echoing ValueError.
    The body moved to app/data.py:page() so /api/jobs, /api/clinics, /api/plan and the autopilot lists
    (app/autopilot/api.py:_page) cannot drift apart on what `limit` and `next_offset` mean."""
    return D.page(rows, p, default_limit)


def _list_response(request, rows, default_limit):
    """Shared tail of GET /api/jobs and GET /api/clinics. Two things an agent needs that a browser doesn't:

    fields=a,b,c   sparse projection. A job row is ~35 columns; an agent scanning 500 of them should not
                   have to carry the enr_* block it never reads. An unknown name is a 400, not a silent
                   column of nulls.
    Accept: application/x-ndjson   drops the envelope and emits one JSON object per line, so a caller can
                   stream-parse without holding the page in memory. limit/offset still apply.

    D.redact() runs on the page (not on the whole filtered list) and before the projection, so a caller
    below `member` gets null for D.MEMBER_ONLY_JOB_FIELDS whether they ask for the field or not, and the
    unknown-field check above still sees the unredacted row shape."""
    p = dict(request.query_params)
    out = _page(rows, p, default_limit)
    out["rows"] = D.redact(out["rows"], request.state.identity["role"])
    fields = p.get("fields")
    if fields:
        keys = [f.strip() for f in fields.split(",") if f.strip()]
        unknown = [k for k in keys if rows and k not in rows[0]]
        if unknown:
            raise HTTPException(400, f"unknown field(s): {', '.join(unknown)}")
        out["rows"] = [{k: r.get(k) for k in keys} for r in out["rows"]]
        out["fields"] = keys
    if "application/x-ndjson" in (request.headers.get("accept") or ""):
        # Dropping the envelope dropped total/next_offset with it: a streaming caller asking for everything
        # got exactly `limit` lines and no way to tell a whole list from a page (proven 2026-09-11 against
        # 2725 open postings). Content-Range carries both, and is the shape the upstream PostgREST reads
        # this API documents already use (docs/api.md, "Supabase REST direct").
        n, off, total = len(out["rows"]), out["offset"], out["total"]
        rng = f"rows {off}-{off + n - 1}/{total}" if n else f"rows */{total}"
        return Response("\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in out["rows"]),
                        media_type="application/x-ndjson", headers={"Content-Range": rng})
    return out


# --- read -----------------------------------------------------------------------------------
@app.get("/api/stats")
def api_stats(request: Request):
    s = D.stats()
    # The full balance/spend breakdown is what GET /api/firecrawl/credits and GET /api/billing 401 for --
    # only the owner gets it here too. A customer gets remaining/plan, which is what the header credits pill
    # on /pro renders (web/pro.template.html:loadStats) -- /pro is a member page, so that is the same gate.
    # Anonymous gets null: this is the operator's paid scraping budget and how close it is to zero, no
    # public page reads it (web/index.template.html only uses clinics/open_jobs/fresh_jobs), and an agent
    # planning spend should read GET /api/crawl/plan's credits_left (read:ops) -- per target and current --
    # rather than the account balance. 2026-09-10 security pass; skill/SKILL.md says the same.
    role = request.state.identity["role"]
    if role == "owner":
        from pflege_jobs.sources import firecrawl_agent as FA
        fc = FA.credits()
        fc["spent_by_app"] = R.usage_total()
        fc["spent_by_app_7d"] = R.usage_total(days=7)
        fc["used_period"] = (fc["plan"] - fc["remaining"]) if isinstance(fc.get("plan"), int) and isinstance(fc.get("remaining"), int) else None
    elif role == "customer":
        from pflege_jobs.sources import firecrawl_agent as FA
        pub = FA.credits(tokens=False, historical=False)
        fc = {"remaining": pub.get("remaining"), "plan": pub.get("plan")}
    else:
        fc = None
    s["firecrawl"] = fc
    s["next_autocrawl"] = S.next_run_at()
    s["active_runs"] = R.active_run_count()
    return s


@app.get("/api/facets")
def api_facets():
    return D.snapshot()["facets"]


@app.get("/api/taxonomy")
def api_taxonomy():
    return D.taxonomy()


@app.get("/api/clinics")
def api_clinics(request: Request):
    return _list_response(request, D.filter_clinics(dict(request.query_params)), 500)


@app.get("/api/cities")
def api_cities(q: str = ""):
    return D.cities(q)


@app.get("/api/plan")
def api_plan(request: Request):
    p = dict(request.query_params)
    rows = D.plan_rows(p)
    pdf = A.DATA_DIR / "registry" / "krankenhausplan_2026.pdf"
    out = _page(rows, p, 1000)
    out.update({"pdf_url": "/docs/krankenhausplan_2026.pdf" if pdf.exists() else None, "source": rows[0]["source"] if rows else None,
                "source_url": D.PLAN_SOURCE_URL, "columns": list(D.PLAN_COLS) + ["size", "jobs_open"]})
    return out


@app.get("/api/clinics/{clinic_id}")
def api_clinic(clinic_id: str, request: Request):
    """`runs` is owner-only. This route published the same crawl-run rows GET /api/crawl/runs answers 401 for
    below owner (auth.OWNER_READ_PREFIXES covers "/api/crawl") -- per-run Firecrawl `credits_used`, the
    internal `error` string and the last three `run_log` lines -- to anonymous callers until 2026-09-11,
    because `jobs` on the line above went through D.redact() and this one never did.

    What a public clinic page needs from a crawl is *when it last ran*, and that is already on the clinic row
    itself: last_crawl_at / last_crawl_status / last_crawl_mode (app/data.py:183). So the key stays in the
    published shape (like D.redact's nulls) and the rows below owner are the empty list, not a trimmed row --
    there is no field on a run a non-owner was ever shown."""
    c = D.clinic(clinic_id)
    if not c:
        raise HTTPException(404, "unknown clinic")
    out = dict(c)
    out["jobs"] = D.redact(D.filter_jobs({"clinic_id": clinic_id, "sort": "-first_published"}), request.state.identity["role"])
    out["runs"] = ([r for r in R.list_runs(200)
                    if clinic_id in (r.get("clinic_ids") or []) or (r["scope"] in ("clinic", "career") and r["value"] == clinic_id)][:20]
                   if request.state.identity["role"] == "owner" else [])
    return out


@app.get("/api/jobs")
def api_jobs(request: Request):
    return _list_response(request, D.filter_jobs(dict(request.query_params)), 200)


@app.get("/api/jobs/{posting_id}")
def api_job(posting_id: int, request: Request):
    row = D.job_detail(posting_id)
    if not row:
        raise HTTPException(404, "unknown posting")
    return D.redact([row], request.state.identity["role"])[0]


@app.get("/api/search")
def api_search(q: str = "", limit: int = 15):
    return SE.search(q, limit=max(1, min(limit, 50)))


@app.post("/api/refresh-cache")
def api_refresh():
    D.refresh()
    return {"ok": True, "at": D.stats()["snapshot_at"]}


# --- CV -------------------------------------------------------------------------------------
@app.post("/api/cv")
async def api_cv(request: Request, file: Optional[UploadFile] = File(None), limit: int = 50):
    text = None
    blob, name = None, None
    ct = request.headers.get("content-type", "")
    if file is not None:
        blob = await file.read()
        name = file.filename
    elif ct.split(";")[0].strip().lower() in ("multipart/form-data", "application/x-www-form-urlencoded"):
        # The `file: UploadFile = File(None)` above makes FastAPI parse the FORM for any form content type,
        # which consumes the request stream; the `await request.body()` below then raised
        # `RuntimeError: Stream consumed` -> 500. That is a caller mistake (a form with no `file` part, or a
        # urlencoded body), so it answers 4xx and names the three shapes the route reads. It was the only
        # genuine crash left after ~197,000 fuzz requests, and it hit members and the owner alike.
        raise HTTPException(400, "POST /api/cv reads a multipart/form-data body with a file=<pdf|docx|txt> part, "
                                 'an application/json body {"text": "..."}, or the raw document as the body -- '
                                 f"this {ct.split(';')[0].strip() or 'form'} body carries no file part")
    else:
        body = await request.body()
        if "json" in ct:
            try:
                text = (json.loads(body or b"{}") or {}).get("text")
            except Exception:
                raise HTTPException(400, "invalid JSON body")
        else:
            blob, name = body, "cv.txt"
    if not text and not blob:
        raise HTTPException(400, "send multipart file=<pdf|docx|txt> or JSON {\"text\": ...}")
    if blob and len(blob) > 10 * 1024 * 1024:
        raise HTTPException(413, "file too large (10 MB max)")
    try:
        return CV.analyse(filename=name, blob=blob, text=text, limit=max(1, min(limit, 200)))
    except ValueError as e:
        raise HTTPException(422, str(e))


# --- crawl ----------------------------------------------------------------------------------
SCOPES = T.SCOPES
MODES = ("auto", "adapter", "firecrawl")


def _target_from_query(request: Request):
    p = dict(request.query_params)
    return T.parse({"scope": p.get("scope", "clinic"), "values": p.get("values", p.get("value", ""))})


def _plan_payload(target, p):
    """The GET /api/crawl/plan body. Also what POST /api/crawl returns for validate_only, so a dry run and
    a preview describe the same decision in the same words."""
    return {"target": target, "clinics": len(p["clinics"]), "boards": p["boards"], "via_adapter": len(p["adapter"]), "via_firecrawl": len(p["firecrawl"]),
            "walled": p["walled"], "est_credits": p["credits_needed"], "credits_left": p["credits_left"],
            "skipped": [{"clinic_id": c["clinic_id"], "reason": c.get("route_reason")} for c in p["skipped"]][:50],
            "sample": [c["name"] for c in p["clinics"][:8]],
            "adapter": [c["clinic_id"] for c in p["adapter"]], "firecrawl": [c["clinic_id"] for c in p["firecrawl"]], "credits_needed": p["credits_needed"]}


def _validate_only(request, body):
    """The AIP-163 dry-run switch. It is a BODY field on every route that has it, and a `validate_only` in
    the query string is now a loud 400 instead of a silent real write: `POST /api/ingest?validate_only=true`
    used to write the row and answer `"validate_only": false`, which is how probe row inbox_id 14043 reached
    the production inbox on 2026-09-09. Accepting it in the query as well was the other option and was
    rejected -- one spelling of a switch that decides "write or not" is worth more than a convenience."""
    if "validate_only" in request.query_params:
        raise HTTPException(400, 'validate_only is a body field, not a query parameter -- send {"validate_only": true} '
                                 "in the JSON body (the query parameter was ignored and the request would have written)")
    return bool((body or {}).get("validate_only"))


def _idem_caller(request):
    """Whose Idempotency-Key this is. The agent key's label identifies the key itself (app/settings.py
    replaces a key when the same label is minted again, so one label is one key); a browser caller is its
    session e-mail. Anything else is anonymous, which no write route reaches anyway."""
    agent = getattr(request.state, "agent", None)
    if agent:
        return "agent:" + (agent.get("label") or "")
    ident = AU.current(request)
    return "session:" + (ident.get("email") or ident.get("role") or "anonymous")


def _idempotent(request, body, run):
    """Idempotency-Key (AIP-155 / the Stripe header) on a write route. No header -> run() straight away.
    With one: the same key and the same body from the same caller replays the first call's response instead
    of doing the work twice, a copy still in flight is 409, and the same key with a different body is 422.
    Keys are namespaced per caller (R.idem_scope), so one caller can neither read back nor block another's.
    validate_only never claims a key -- it wrote nothing, so there is nothing to replay."""
    given = request.headers.get("idempotency-key")
    if not given or (body or {}).get("validate_only"):
        return run()
    key = R.idem_scope(_idem_caller(request), given)          # the row key; `given` is what the caller is told about
    state, stored = R.idem_begin(key, R.idem_fingerprint(request.method, request.url.path, body))
    if state == "replay":
        return stored
    if state == "in_flight":
        raise HTTPException(409, f"idempotency key {given} is already in flight")
    if state == "conflict":
        raise HTTPException(422, f"idempotency key {given} was already used for a different request body")
    try:
        out = run()
    except BaseException:
        R.idem_drop(key)
        raise
    R.idem_finish(key, out)
    return out


@app.post("/api/crawl")
async def api_crawl(request: Request):
    body = await D.json_body(request)
    dry_run = _validate_only(request, body)            # before any planning: a misplaced switch is a 400, not a run
    mode = body.get("mode", "auto")
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    try:
        target = T.parse(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    max_credits = int(body.get("max_credits") or ST.get_firecrawl()["default_max_credits"])
    if max_credits < 0 or max_credits > 500:
        raise HTTPException(400, "max_credits must be 0..500")
    try:
        plan = CR.plan_for(None, None, mode, max_credits, target=target)
    except Exception as e:
        raise HTTPException(400, str(e))
    if not plan["clinics"]:
        raise HTTPException(404, "target matched no hospital")
    # keyed on the actual plan, not the mode string: mode="auto" routes every non-routable/walled clinic to
    # Firecrawl too (crawl.py:plan_for), so {"scope":"all","mode":"auto"} used to skip both guards below.
    if plan["firecrawl"] and target["scope"] == "all":
        raise HTTPException(400, "refusing firecrawl for every hospital at once; use a narrower target")
    if plan["firecrawl"] and plan["credits_needed"] > plan["credits_left"]:
        raise HTTPException(409, f"firecrawl budget exhausted: need up to {plan['credits_needed']}, {plan['credits_left']} left this week")
    if dry_run:                                        # AIP-163 dry run: same auth, same validation, no run row
        return {**_plan_payload(target, plan), "validate_only": True, "queued": False}

    def queue():
        params = {"max_credits": max_credits, "deep": bool(body.get("fetch_details", body.get("deep"))), "verify": body.get("verify", True), "target": target}
        rid = R.create_run(target["scope"], T.value_string(target), mode, params, [c["clinic_id"] for c in plan["clinics"]], trigger="api")
        R.enqueue(rid)
        return {"run_id": rid, "queued": True, "clinics": len(plan["clinics"]), "boards": plan["boards"], "adapter": len(plan["adapter"]),
                "firecrawl": len(plan["firecrawl"]), "skipped": [{"clinic_id": c["clinic_id"], "reason": c.get("route_reason")} for c in plan["skipped"]][:50]}

    return _idempotent(request, body, queue)


@app.get("/api/crawl/plan")
def api_crawl_plan(request: Request, mode: str = "auto", max_credits: int = 40):
    try:
        target = _target_from_query(request)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    return _plan_payload(target, CR.plan_for(None, None, mode, max_credits, target=target))


@app.get("/api/crawl/estimate")
def api_crawl_estimate(clinic_id: str):
    """Free, pre-parse read of one clinic's board: how many titles look like Pflegedienst, before running
    any real crawl (docs/api.md). Honest about not knowing -- see CR.estimate_clinic()."""
    c = D.clinic(clinic_id)
    if not c:
        raise HTTPException(404, "unknown clinic")
    return CR.estimate_clinic(c)


@app.get("/api/crawl/runs")
def api_runs(limit: int = 50):
    return R.list_runs(max(1, min(limit, 500)))


@app.get("/api/crawl/runs/{run_id}")
def api_run(run_id: int):
    r = R.get_run(run_id)
    if not r:
        raise HTTPException(404, "unknown run")
    return r


@app.post("/api/crawl/runs/{run_id}/cancel")
def api_run_cancel(run_id: int):
    """Best-effort: a queued run never starts; a running one stops at the next board/Firecrawl-clinic
    boundary in app/crawl.py:execute() (there is no hard kill mid-request -- whatever finished before
    the check still gets ingested). Returns the updated run row."""
    r = R.get_run(run_id, with_log=False)
    if not r:
        raise HTTPException(404, "unknown run")
    if r["status"] not in ("queued", "running"):
        raise HTTPException(409, f"run is already {r['status']}, nothing to cancel")
    if r["status"] == "queued":
        R.update_run(run_id, status="cancelled", finished_at=R.now(), error="cancelled by operator (never started)")
    else:
        R.update_run(run_id, cancel_requested=1)
    return R.get_run(run_id, with_log=False)


@app.get("/api/inbox")
def api_inbox(recent: int = 25):
    return D.inbox_summary(recent=max(0, min(int(recent), 200)))


@app.post("/api/inbox/drain")
def api_inbox_drain(request: Request):
    """Queue a run that drains pflege_jobs.inbox via `cli inbox`. Guarded against active runs: a crawl's
    own `execute()` already calls `_cli(["inbox"])` at the end, and two `cli inbox` processes racing the
    same ack/offset-free pagination would double-process rows. Accepts Idempotency-Key."""
    if R.active_run_count():
        raise HTTPException(409, "a run is already queued or running; try again once it finishes")

    def queue():
        rid = R.create_run("inbox", "", "adapter", {}, [], trigger="api")
        R.enqueue(rid)
        return {"run_id": rid, "queued": True}

    return _idempotent(request, {}, queue)


# --- agentic ingest ---------------------------------------------------------------------------
# One envelope for every ontology entity, CloudEvents field names, no new table. Three of the seven types
# become an ordinary row in the existing pflege_jobs.inbox (sql/010_inbox.sql) and are drained by
# `cli inbox` like any crawler post; the other four hit the edge function's ops directly, exactly as
# pflege_jobs/sinks.py already does. Envelope -> inbox row: type -> kind, source -> collector, subject ->
# payload.clinic_id, id -> payload.event_id, data -> the row, client_id from the agent key's label.
INBOX_KIND = {"posting.observed": "jobposting", "listing.observed": "listing", "probe.ats_discovery": "probe"}
EDGE_OP = {"clinic.upserted": ("clinics", SCH.CLINIC_SPEC), "clinic_link.asserted": ("clinic_links", SCH.LINK_SPEC),
           "posting.verified": ("verify", SCH.VERIFY_SPEC), "crawl_run.finished": ("crawl_run", None)}
FULL_ROW_HINT = ("the edge upsert assigns every column of its recordset, so an omitted key writes NULL over "
                 "what is stored; build the row with pflege_jobs/registry.py full_clinic_rows/merge_discovered")


def _problem_body(status, detail, slug, **extra):
    """RFC 9457 body without a Response around it -- POST /api/ingest carries one of these per rejected
    item inside a 207, where the envelope status belongs to the batch and not to the item."""
    return {"type": f"/docs/errors.md#{slug}", "title": slug.replace("_", " ").replace("-", " ").capitalize(),
            "status": status, "detail": detail, "error": detail, **extra}


JSON_TYPE_OK = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
                "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
                "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
                "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
                "null": lambda v: v is None}


def _schema_errors(value, schema, path):
    """First violation of `schema` in `value`, as a sentence naming the field -- or None.

    Enough JSON Schema for the documents GET /api/ingest/schemas actually publishes: type (one name or a
    list of them), required, properties, items, enum. `jsonschema` is not a dependency of this project and
    five keywords do not justify adding one. The schemas checked here are the same objects the route
    serves, so the published contract and the check cannot drift; a published type this table does not know
    raises KeyError rather than passing silently, and
    tests/test_app_api.py:test_ingest_schemas_only_use_types_the_validator_knows makes that loud offline."""
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else list(types)
        if not any(JSON_TYPE_OK[t](value) for t in types):
            return f"{path} must be {' or '.join(types)}, got {type(value).__name__}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} must be one of: {', '.join(map(str, schema['enum']))}"
    if "maxLength" in schema and isinstance(value, str) and len(value) > schema["maxLength"]:
        return f"{path} must be at most {schema['maxLength']} characters, got {len(value)}"
    if isinstance(value, dict):
        for k in schema.get("required") or ():
            if k not in value:
                return f"{path}.{k} is required"
        for k, sub in (schema.get("properties") or {}).items():
            if k in value:
                err = _schema_errors(value[k], sub, f"{path}.{k}")
                if err:
                    return err
    if isinstance(value, list) and schema.get("items"):
        for i, item in enumerate(value):
            err = _schema_errors(item, schema["items"], f"{path}[{i}]")
            if err:
                return err
    return None


def _validate_event(e, scopes):
    """One envelope -> (row ready to write, None) or (None, problem body). `scopes` None means an owner
    session, which may write every type; a set means an agent key and is checked per event type, so a
    partly-scoped batch rejects the events it may not write instead of failing whole."""
    if not isinstance(e, dict):
        return None, _problem_body(400, "each event must be a JSON object", "invalid-body")
    typ = e.get("type")
    if not isinstance(typ, str):
        # Both membership tests below are dict lookups, so an unhashable `type` (a list or an object) raised
        # TypeError out of here -- and because that escaped _validate_event it took the WHOLE batch down with
        # a 500 instead of rejecting this one item. Typed first, so the item gets its 422 like any other.
        return None, _problem_body(422, f"envelope.type must be string, got {type(typ).__name__}; "
                                   "see GET /api/ingest/schemas", "invalid-body")
    if typ not in INBOX_KIND and typ not in EDGE_OP:
        return None, _problem_body(422, f"unknown envelope type {typ!r}; see GET /api/ingest/schemas", "invalid-body")
    missing = [f for f in ("id", "source", "type") if not str(e.get(f) or "").strip()]
    if missing:
        return None, _problem_body(422, "envelope is missing " + ", ".join(missing), "invalid-body")
    need = AU.INGEST_SCOPE[typ]
    if scopes is not None and need not in scopes:
        return None, _problem_body(403, f"this agent key does not carry the scope {need}", "insufficient_scope", scope=need)
    data = e.get("data")
    if not isinstance(data, dict):
        return None, _problem_body(422, "data must be a JSON object", "invalid-body")
    # The envelope against the schema GET /api/ingest/schemas publishes. The checks above answer first
    # because they say more (unknown type, which field is missing, which scope to ask for); this one is what
    # catches the nested shapes that used to reach the writer and 500 with the exception echoed back --
    # `id` as an object (unhashable in the dedupe key), `data.source_url` as a number or a list
    # (urlparse -> AttributeError), `data.payload` as a string or a list (dict() -> ValueError/TypeError).
    err = _schema_errors(e, ENVELOPE_SCHEMA, "envelope")
    if err:
        return None, _problem_body(422, err + "; see GET /api/ingest/schemas", "invalid-body")
    if typ in INBOX_KIND:
        url = data.get("source_url") or data.get("source_ref")
        if not url:
            return None, _problem_body(422, f"data.source_url is required for {typ}", "invalid-body")
        # required=[] because the line above already checked it, and more leniently: source_ref is accepted
        # as an alias for source_url, which the published `required` cannot express without anyOf.
        err = _schema_errors(data, dict(INBOX_DATA_SCHEMA, required=[]), "data")
        if err:
            return None, _problem_body(422, err + "; see GET /api/ingest/schemas", "invalid-body")
        payload = dict(data.get("payload") or {})
        payload["event_id"] = e["id"]
        if e.get("subject"):
            payload["clinic_id"] = e["subject"]
        if typ == "probe.ats_discovery":
            payload["probe"] = "ats_discovery"            # what pflege_jobs/cli.py's drain dispatches on
        return {"_op": "inbox", "kind": INBOX_KIND[typ], "collector": e["source"], "source_url": url,
                "source_host": data.get("source_host") or urlparse(url).netloc, "payload": payload}, None
    op, spec = EDGE_OP[typ]
    if spec is None:
        return {"_op": op, "row": data}, None
    absent = [c for c, _ in spec if c not in data]
    if absent:
        return None, _problem_body(422, f"{typ} must carry every column ({len(spec)} of them); missing "
                                   + ", ".join(absent) + " -- " + FULL_ROW_HINT, "invalid-body", missing=absent)
    err = _schema_errors(data, _spec_schema(spec), "data")      # column types, against the published schema
    if err:
        return None, _problem_body(422, err + "; see GET /api/ingest/schemas", "invalid-body")
    return {"_op": op, "row": {c: data.get(c) for c, _ in spec}}, None


@app.post("/api/ingest")
async def api_ingest(request: Request):
    """The single ingestion path. Body is one envelope or {"events": [...]}; `validate_only: true` runs the
    whole check and writes nothing; `Idempotency-Key` makes a retry replay the first answer.

    202 when every item came out the same way, 207 when they did not (some written, some duplicate, some
    rejected) -- the per-item `status` is the real answer either way. `inbox_id` is null: the inbox post
    reuses app/crawl.py's batched dedupe, which asks PostgREST for `return=minimal`."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(400, 'body must be an envelope or {"events": [...]}')
    if "events" in body and not isinstance(body["events"], list):
        raise HTTPException(400, f'"events" must be a list of envelopes, got {type(body["events"]).__name__}')
    events = body["events"] if isinstance(body.get("events"), list) else [body]
    if not events:
        raise HTTPException(400, "no events in the request")
    agent = getattr(request.state, "agent", None)
    scopes = set(agent.get("scopes") or ()) if agent else None      # None = owner session: every type
    client_id = (agent or {}).get("label") or "owner-session"
    validate_only = _validate_only(request, body)

    def ingest():
        results, todo, seen = [], [], set()
        for e in events:
            entry = {"id": (e or {}).get("id") if isinstance(e, dict) else None,
                     "type": (e or {}).get("type") if isinstance(e, dict) else None}
            row, problem = _validate_event(e, scopes)
            if problem:
                results.append({**entry, "status": "rejected", "problem": problem})
                continue
            key = (e["source"], e["id"])
            if key in seen:
                results.append({**entry, "status": "duplicate", "detail": "same (source, id) earlier in this batch"})
                continue
            seen.add(key)
            results.append({**entry, "status": "valid" if validate_only else "accepted", "inbox_id": None})
            todo.append((len(results) - 1, row))
        if not validate_only and todo:
            ops = {}
            for i, r in todo:
                ops.setdefault(r["_op"], []).append((i, r))
            inbox = ops.pop("inbox", [])
            sink = None
            if ops:
                from pflege_jobs.sinks import EdgeSink
                sink = EdgeSink()                    # built before anything is written: an unusable sink
            if inbox:                                # must not leave half a batch behind
                rows = [dict({k: v for k, v in r.items() if k != "_op"}, client_id=client_id) for _, r in inbox]
                posted = set(CR._post_inbox(rows, lambda *_: None))
                for i, r in inbox:
                    if r["source_url"] not in posted:
                        results[i].update(status="duplicate", detail="source_url already in the inbox")
            for op, items in ops.items():
                out = sink._post({op: ([r["row"] for _, r in items] if op != "crawl_run" else items[0][1]["row"])})
                for i, _ in items:
                    results[i]["result"] = {op: out.get(op)}
        accepted = sum(1 for r in results if r["status"] in ("accepted", "valid"))
        return {"accepted": accepted, "total": len(results), "validate_only": validate_only, "results": results}

    out = _idempotent(request, body, ingest)
    return JSONResponse(out, status_code=202 if out["accepted"] == out["total"] else 207)


# --- JSON Schema for the envelope types, rendered from pflege_jobs/schema.py the same way
# edge/build_ingest.py renders the Deno function, so the published contract and the columns the edge
# function actually inserts come from one place. ------------------------------------------------
PG_JSON_TYPE = {"text": "string", "smallint": "integer", "int": "integer", "bigint": "integer", "boolean": "boolean",
                "numeric": "number", "double precision": "number", "jsonb": "object", "timestamptz": "string", "date": "string"}
PG_JSON_FORMAT = {"timestamptz": "date-time", "date": "date"}


def _column_schema(t):
    if t.endswith("[]"):
        return {"type": "array", "items": _column_schema(t[:-2])}
    out = {"type": [PG_JSON_TYPE.get(t, "string"), "null"]}
    if t in PG_JSON_FORMAT:
        out["format"] = PG_JSON_FORMAT[t]
    return out


def _spec_schema(spec):
    """Every column required: the edge upsert assigns all of them, so an omitted key is a NULL write."""
    return {"type": "object", "properties": {c: _column_schema(t) for c, t in spec}, "required": [c for c, _ in spec]}


INBOX_DATA_SCHEMA = {"type": "object", "required": ["source_url"],
                     "properties": {"source_url": {"type": "string"}, "source_host": {"type": "string"},
                                    "source_ref": {"type": "string", "description": "accepted as an alias for "
                                                   "source_url when that key is absent"},
                                    "payload": {"type": "object", "description": "the crawler row itself (jsonb); "
                                                "pflege_jobs/cli.py's inbox drain parses it"}}}
# Module constant, not a literal inside the route: _validate_event() checks every incoming envelope against
# this exact object, so what POST /api/ingest enforces is what GET /api/ingest/schemas publishes.
# The envelope's two identifier fields are the (source, id) dedupe key and are copied verbatim into the row
# that is written -- `id` becomes payload.event_id, `source` becomes inbox.collector. Neither the jsonb nor
# the text column has a limit worth calling one (Postgres would take a megabyte), so the honest limit is a
# published one: it is stated in the schema GET /api/ingest/schemas serves and enforced by _schema_errors()
# as an ordinary 422, not silently truncated. Before 2026-09-11 a 200,000-character id was accepted (202,
# accepted 1/1) and written straight through.
ID_MAX = 255

ENVELOPE_SCHEMA = {"type": "object", "required": ["id", "source", "type", "data"],
                   "properties": {"specversion": {"type": "string", "description": "accepted and ignored; 1.0"},
                                  "id": {"type": "string", "maxLength": ID_MAX, "description":
                                         f"unique per source; (source, id) is the dedupe key; at most {ID_MAX} characters"},
                                  "source": {"type": "string", "maxLength": ID_MAX, "description":
                                             f"stored as inbox.collector; at most {ID_MAX} characters"},
                                  "type": {"enum": list(AU.INGEST_SCOPE)},
                                  "time": {"type": "string", "format": "date-time"},
                                  "subject": {"type": "string", "description": "clinic_id (5-digit KeZ)"},
                                  "dataschema": {"type": "string"}, "data": {"type": "object"}}}


@app.get("/api/ingest/schemas")
def api_ingest_schemas():
    """Public. The envelope plus one JSON Schema per type, generated from pflege_jobs/schema.py."""
    types = {}
    for typ, scope in AU.INGEST_SCOPE.items():
        if typ in INBOX_KIND:
            types[typ] = {"scope": scope, "target": f"pflege_jobs.inbox (kind={INBOX_KIND[typ]})", "data": INBOX_DATA_SCHEMA}
        else:
            op, spec = EDGE_OP[typ]
            types[typ] = {"scope": scope, "target": f"edge op {op}",
                          "data": _spec_schema(spec) if spec else {"type": "object"},
                          **({"note": FULL_ROW_HINT} if typ == "clinic.upserted" else {})}
    return {"envelope": ENVELOPE_SCHEMA, "types": types}


def gate_map():
    """{(METHOD, path template): required role or None} for every /api route this app serves, read off the
    app's own OpenAPI document and app/auth.py:required_role() -- the function the middleware itself calls.
    GET /api/agent/manifest is built from this, so the published contract cannot claim a gate the middleware
    does not enforce, or stay silent about one it does.

    The enumeration goes through openapi(), not app.routes: FastAPI wraps an included router in an
    _IncludedRouter object that carries no .path, so walking app.routes silently skips every router
    (/api/auth/*, /api/autopilot/*, /api/billing*, /api/coverage, /api/firecrawl/*, /api/hunter/*,
    /api/stripe/*). Not in the document, and so not in the manifest: FastAPI's own /api/openapi.json and
    /api/openapi-ui, which it excludes by design and does not gate."""
    return {(m.upper(), path): AU.required_role(m.upper(), re.sub(r"\{[^}]+\}", "1", path))
            for path, ops in app.openapi()["paths"].items() if path.startswith("/api/") for m in ops}


@app.get("/api/agent/manifest")
def api_agent_manifest():
    """Public. What an agent key can be given, what each route does and what it costs, plus the two lists an
    agent needs to plan without guessing: what needs no key at all, and what no key can ever open. Every /api
    route lands in exactly one of `routes` / `public` / `session_only`, all three generated from
    app/auth.py's AGENT_ROUTES and required_role() -- the same table and function the middleware enforces --
    so the published contract cannot drift away from what is actually gated."""
    gates = gate_map()
    scoped = {(e["method"], e["path"]) for e in AU.AGENT_ROUTES}
    return {"scopes": list(AU.SCOPES),
            "routes": [{"method": e["method"], "path": e["path"],
                        "scope": e["scope"] if isinstance(e["scope"], str) else None,
                        "scopes": e["scopes"], "side_effects": e["side_effects"], "cost": e["cost"],
                        "public": gates.get((e["method"], e["path"])) is None}
                       for e in AU.AGENT_ROUTES],
            "public": [{"method": m, "path": p} for (m, p), role in sorted(gates.items())
                       if role is None and (m, p) not in scoped],
            "session_only": [{"method": m, "path": p, "role": role}
                             for (m, p), role in sorted(gates.items()) if role and (m, p) not in scoped],
            "envelope_types": [{"type": t, "scope": s, "kind": INBOX_KIND.get(t),
                                "target": f"pflege_jobs.inbox (kind={INBOX_KIND[t]})" if t in INBOX_KIND else f"edge op {EDGE_OP[t][0]}"}
                               for t, s in AU.INGEST_SCOPE.items()],
            "auth": {"header": "X-Api-Key", "mint": "PUT /api/settings/agent-key (owner session only)",
                     "idempotency_header": "Idempotency-Key", "dry_run": "validate_only: true (body field only; "
                     "in the query string it is a 400, never a silent write)"},
            # Generated from app/data.py (same reason as `redacted` below): how the paged read routes report
            # a page, and how a sweeping agent tells "that was everything" from "that was the first page".
            "paging": D.PAGING,
            # Generated from app/data.py so the published contract cannot drift from what the redaction does.
            "redacted": {"fields": list(D.MEMBER_ONLY_JOB_FIELDS), "routes": ["GET /api/jobs", "GET /api/jobs/{posting_id}",
                                                                              "GET /api/clinics/{clinic_id}"],
                         "note": "personal data (recruiter e-mail addresses): null for anyone below a member "
                                 "session (owner or paying customer). The field stays in the row shape, so a null "
                                 "means either 'no address on the ad' or 'not yours to see' -- sign in to tell them apart.",
                         # Two more fields on otherwise public routes that answer null without a session.
                         "also": [{"route": "GET /api/stats", "field": "firecrawl",
                                   "needs": "customer (remaining/plan) or owner (full spend breakdown)",
                                   "instead": "GET /api/crawl/plan -> credits_left (read:ops)"},
                                  {"route": "GET /api/stripe/status", "field": "customers, closed_this_period",
                                   "needs": "owner", "instead": None}]},
            "note": "`session_only` needs an owner (or member) session cookie -- no scope opens it, so a key "
                    "gets 401 there, not 403. `public` needs nothing at all. A route in `routes` with "
                    "\"public\": true is open to everyone today; its scope is what a key would be checked "
                    "for if that route were ever gated, and is useful for attribution meanwhile. HTML pages "
                    "are gated too but are not listed here -- see /docs/auth.md.",
            "links": {"openapi": "/api/openapi.json", "ontology": "/api/ontology", "taxonomy": "/api/taxonomy",
                      "facets": "/api/facets", "schemas": "/api/ingest/schemas", "skill": "/skill/SKILL.md",
                      "docs": "/docs/api.md", "errors": "/docs/errors.md"}}


@app.post("/api/clinics/{clinic_id}/refetch-career")
async def api_refetch(clinic_id: str, request: Request):
    if not D.clinic(clinic_id):
        raise HTTPException(404, "unknown clinic")
    body = await D.json_body(request)
    max_credits = int(body.get("max_credits") or ST.get_firecrawl()["default_max_credits"])
    if CR._budget_left() < max_credits:
        raise HTTPException(409, f"firecrawl budget exhausted ({CR._budget_left()} credits left this week)")
    rid = R.create_run("career", clinic_id, "firecrawl", {"max_credits": max_credits}, [clinic_id], trigger="api")
    R.enqueue(rid)
    return {"run_id": rid, "queued": True}


@app.post("/api/autocrawl/tick")
def api_autocrawl_tick():
    """Fire every enabled schedule now (today's stagger slice) — what the scheduler thread would do at its cron time."""
    return S.tick(force=True)


# --- schedules ------------------------------------------------------------------------------
@app.get("/api/schedules")
def api_schedules():
    return SC.list_all()


@app.get("/api/schedules/presets")
def api_schedule_presets():
    return SC.PRESETS


@app.get("/api/schedules/{sid}/preview")
def api_schedule_preview(sid: int, day: str = None):
    """What this schedule would do on `day` (ISO date, default today): the same stagger slice fire() picks,
    without firing it. Read-only -- no run row, no credits, no last_run_at touch."""
    s = SC.get(sid)
    if not s:
        raise HTTPException(404, "unknown schedule")
    try:
        when = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) if day else None
    except ValueError:
        raise HTTPException(400, "day must be an ISO date, e.g. 2026-09-10")
    clinics = SC.clinics_today(s, day=when)
    plan = CR.plan_for(None, None, s["mode"], int(s.get("max_credits") or 0),
                       target={"scope": "clinic", "values": [c["clinic_id"] for c in clinics]})
    return {"schedule_id": sid, "target": s["target"], "mode": s["mode"], "stagger_days": s["stagger_days"],
            "next_run_at": s["next_run_at"], "day": (when or datetime.now(timezone.utc)).date().isoformat(),
            "boards": plan["boards"], "clinics": len(clinics), "slice": [c["clinic_id"] for c in clinics],
            "via_adapter": len(plan["adapter"]), "via_firecrawl": len(plan["firecrawl"]),
            "est_credits": plan["credits_needed"], "credits_left": plan["credits_left"]}


@app.post("/api/schedules")
async def api_schedule_create(request: Request):
    try:
        return SC.create(await D.json_body(request))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.put("/api/schedules/{sid}")
async def api_schedule_update(sid: int, request: Request):
    try:
        s = SC.update(sid, await D.json_body(request))
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not s:
        raise HTTPException(404, "unknown schedule")
    return s


@app.delete("/api/schedules/{sid}")
def api_schedule_delete(sid: int):
    if not SC.delete(sid):
        raise HTTPException(404, "unknown schedule")
    return {"ok": True}


@app.post("/api/schedules/{sid}/run-now")
def api_schedule_run_now(sid: int, stagger: bool = False):
    s = SC.get(sid)
    if not s:
        raise HTTPException(404, "unknown schedule")
    rid = SC.fire(s, stagger=stagger, trigger="run-now")
    if rid is None:
        raise HTTPException(404, "schedule target matched no hospital")
    return {"run_id": rid, "queued": True}


# --- mechanics ------------------------------------------------------------------------------
@app.get("/api/mechanics")
def api_mechanics():
    return [ME.describe(m) for m in ME.registry()]


@app.get("/api/mechanics/{mid}")
def api_mechanic(mid: str):
    m = ME.get(mid)
    if not m:
        raise HTTPException(404, "unknown mechanic")
    return ME.describe(m)


@app.post("/api/mechanics/{mid}/try")
async def api_mechanic_try(mid: str, request: Request):
    m = ME.get(mid)
    if not m:
        raise HTTPException(404, "unknown mechanic")
    try:
        body = await request.json()
    except Exception:
        body = {}
    inputs = body.get("inputs", body) if isinstance(body, dict) else {}
    try:
        return ME.try_it(m, inputs)
    except (TypeError, ValueError) as e:
        raise HTTPException(422, str(e))


@app.post("/api/mechanics/{mid}/test")
def api_mechanic_test(mid: str):
    m = ME.get(mid)
    if not m:
        raise HTTPException(404, "unknown mechanic")
    return ME.run_tests(m)


# --- settings -------------------------------------------------------------------------------
@app.get("/api/settings")
def api_settings():
    return ST.get_all()


@app.put("/api/settings/patterns")
async def api_put_patterns(request: Request):
    body = await D.json_body(request)
    try:
        r = ST.save_patterns(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {**r, "patterns": ST.get_patterns()}


@app.post("/api/settings/patterns/validate")
async def api_validate_patterns(request: Request):
    body = await D.json_body(request)
    return {"errors": ST.validate_patterns(body)}


@app.put("/api/settings/firecrawl")
async def api_put_firecrawl(request: Request):
    return ST.save_firecrawl(await D.json_body(request))


@app.put("/api/settings/flags")
async def api_put_flags(request: Request):
    try:
        return ST.save_feature_flags(await request.json())
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.put("/api/settings/agent-key")
def api_put_agent_key(rotate: bool = False, label: str = None, scopes: str = None):
    """Mint an agent API key. The plaintext is returned in THIS response only -- only its hash is stored, so
    it never appears again (not in GET /api/settings, not in any log of this call).

    `scopes` is a comma list from GET /api/agent/manifest; omitted means every scope, which is the reach the
    single pre-scope key had. `label` names the key so several agents can hold different ones; minting the
    same label again replaces just that key. `rotate=true` revokes every key first."""
    try:
        key = ST.set_agent_key(rotate=rotate, label=label, scopes=scopes.split(",") if scopes else None)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"key": key, **ST.public_agent_key()}


@app.delete("/api/settings/agent-key")
def api_delete_agent_key(label: str = None):
    """No label: revoke every agent key. With one: revoke only that label's."""
    ST.clear_agent_key(label=label)
    return ST.public_agent_key()


@app.get("/api/campaign")
def api_campaign():
    """State of the Firecrawl-only reingest campaign (docs/campaign.md) -- owner-only, like /api/hunter."""
    return CAM.get()


@app.post("/api/campaign")
async def api_post_campaign(request: Request):
    """The scheduled campaign routine's write path: append a reasoning snapshot and/or update safety_level
    / stopped. Not a human toggle -- the routine is the only intended caller."""
    try:
        return CAM.save(await request.json())
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/firecrawl/credits")
def api_credits():
    from pflege_jobs.sources import firecrawl_agent as FA
    fc = FA.credits()
    fc["spent_by_app"] = R.usage_total()
    fc["spent_by_app_7d"] = R.usage_total(days=7)
    return fc


@app.get("/api/firecrawl/prompts")
def api_firecrawl_prompts(clinic_id: str = None):
    """Read-only: the live prompt templates + schemas Firecrawl actually runs against, so an operator
    doesn't have to read pflege_jobs/sources/firecrawl_agent.py to know what's being asked. ?clinic_id=
    renders them for a real hospital; omitted -> generic placeholder text. No network, no credits."""
    from pflege_jobs.sources import firecrawl_agent as FA
    c = D.clinic(clinic_id) if clinic_id else None
    if clinic_id and not c:
        raise HTTPException(404, "unknown clinic")
    return FA.render_prompts(c)


# --- docs / static --------------------------------------------------------------------------
def _json_file(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


@app.get("/api/ontology")
def api_ontology():
    return _json_file(A.DOCS_DIR / "ontology.json")


@app.get("/api/docs")
def api_docs():
    return _json_file(A.DOCS_DIR / "index.json")


@app.get("/docs/krankenhausplan_2026.pdf")
def plan_pdf():
    p = A.DATA_DIR / "registry" / "krankenhausplan_2026.pdf"
    if not p.exists():
        raise HTTPException(404, "PDF not on this host (see data/registry/README.md)")
    return FileResponse(str(p), media_type="application/pdf", headers={"Cache-Control": "public, max-age=86400"})


def _servable(path, under):
    """True when `path` is a real file inside `under`. Every filesystem call sits inside the try on purpose:
    a name the OS cannot even look up is not a file this app has, and the answer is the same 404 a missing
    file already gets. Two such names were anonymous 500s, live on production until 2026-09-11 --
    `GET /skill/query.py%00.md` (ValueError, embedded NUL, out of Path.resolve) and a 300-character name
    (`OSError: [Errno 36] File name too long`, out of Path.exists, on /skill and /docs alike). Nothing is
    being swallowed: this is the lookup only, and the FileResponse each caller builds afterwards still fails
    loudly if the file goes away between here and the send."""
    try:
        p = path.resolve()
        return under.resolve() in p.parents and p.is_file()
    except (OSError, ValueError):
        return False


@app.get("/docs/{name}")
def docs_file(name: str):
    if "/" in name or ".." in name or "\0" in name or not (name.endswith(".md") or name.endswith(".json")):
        raise HTTPException(404, "not found")
    p = A.DOCS_DIR / name
    if not _servable(p, A.DOCS_DIR):
        raise HTTPException(404, "not found")
    return FileResponse(str(p), media_type="application/json" if name.endswith(".json") else "text/markdown; charset=utf-8")


def _web(rel, media=None):
    """Serve a file under web/ with Cache-Control: no-cache (forces revalidation every load, same as _ui()) --
    dock.js/dock.css gate on a live feature flag (GET /api/flags), so a browser that heuristically caches an
    older copy indefinitely (no explicit header = no guaranteed revalidation) can keep showing/hiding the dock
    long after the flag changes server-side. Without this, toggling chats_dock off does not reach an already-
    cached client until its cache happens to expire."""
    p = A.WEB_DIR / rel
    if not _servable(p, A.WEB_DIR):
        raise HTTPException(404, "not found")
    return FileResponse(str(p.resolve()), media_type=media, headers={"Cache-Control": "no-cache"})


def _ui(name):
    """Serve a built HTML page from web/. 503 (not 404) when web/build.py has not run yet."""
    p = A.WEB_DIR / name
    if not p.exists():
        return PlainTextResponse(f"web/{name} not built yet — run `python web/build.py`", status_code=503)
    return FileResponse(str(p), media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    """Default frontend: light, minimal. Built from web/index.template.html."""
    return _ui("index.html")


@app.get("/pro")
@app.get("/pro/")
def pro():
    """Full dashboard: dark; Plan, Scrape, schedules, runs, Docs, Settings. Built from web/pro.template.html."""
    return _ui("pro.html")


@app.get("/login")
def login_page():
    """Sign-in form for the shared owner passphrase (docs/auth.md). Public, and deliberately not in
    auth.GATED_PAGES -- gating the login page is how a redirect loop is built."""
    return _ui("login.html")


@app.get("/deck")
@app.get("/deck/")
def deck_page():
    """The deck. No check here on purpose: "/deck", "/deck/" are in auth.OWNER_PAGES -- owner only, not the
    member level of GATED_PAGES, because it carries unfixed security facts and deploy detail (a paying
    customer reached it until 2026-09-10). The middleware has already redirected anyone else to /login
    before this handler runs; a second check here would only be a second place to get it wrong."""
    return _ui("deck.html")


@app.get("/autopilot")
@app.get("/autopilot/")
def autopilot_page():
    """Operator console for the recruiting funnel (docs/autopilot.md) -- WIP, route disabled 2026-09-08 (no
    real WhatsApp/e-mail integration, synthetic seed data only, needs more work before it's worth exposing).
    Code and web/autopilot.template.html are untouched; flip this back to the FileResponse below to re-enable."""
    return PlainTextResponse("Autopilot console: work in progress, not available yet.", status_code=503)


@app.get("/dock.js")
def dock_js():
    return _web("dock.js", "application/javascript")


@app.get("/dock.css")
def dock_css():
    return _web("dock.css", "text/css")


SKILL_SUFFIXES = (".md", ".py")           # what web/build.py writes into web/skill/: the docs and query.py


@app.get("/skill/{name:path}")
def skill(name: str):
    """The published agent bundle. Extension allow-list, the same shape docs_file() above already uses:
    web/skill/ is a directory on disk, so `python -c "import query"` next to it left a __pycache__, and
    GET /skill/__pycache__/query.cpython-312.pyc answered 200 to anonymous callers (proven 2026-09-10).
    Compiled bytecode is a build artefact, not part of the contract. Anything else new that is meant to be
    public from here -- the tools/uieval.py `--publish` artefacts, say -- adds its suffix on purpose.

    "\0" is the third test and not decoration: GET /skill/query.py%00.md passed both checks above and then
    hit Path.resolve() in _web(), which raises ValueError on an embedded NUL -- a 500 to an anonymous caller,
    live on production until 2026-09-11. docs_file() above survived it only by accident (Path.exists()
    swallows the same ValueError), so both doors now say no to it out loud."""
    if ".." in name or "\0" in name or not name.endswith(SKILL_SUFFIXES):
        raise HTTPException(404, "not found")
    media = "text/markdown; charset=utf-8" if name.endswith(".md") else "text/plain; charset=utf-8"
    return _web("skill/" + name, media)


@app.get("/health")
def health():
    s = D.snapshot()
    return {"ok": bool(s["clinics"]), "clinics": len(s["clinics"]), "jobs": len(s["jobs"]), "error": s.get("error")}
