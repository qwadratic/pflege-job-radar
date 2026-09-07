"""FastAPI entry point. `uvicorn app.main:app --port 8501`.

Serves two frontends (web/index.html = default light page at /, web/pro.html = full dashboard at /pro), the agent skill, docs, and the JSON API documented in docs/api.md.
"""
import json
import pathlib
import threading
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

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


@app.on_event("startup")
def _startup():
    R.start_worker(CR.dispatch)
    threading.Thread(target=D.refresh, daemon=True).start()
    S.start()


@app.exception_handler(StarletteHTTPException)
async def _http_err(_, exc):
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def _any_err(_, exc):
    return JSONResponse({"error": f"{type(exc).__name__}: {str(exc)[:300]}"}, status_code=500)


def _page(rows, limit, offset):
    limit = max(1, min(int(limit or 100), 2000))
    offset = max(0, int(offset or 0))
    return {"total": len(rows), "limit": limit, "offset": offset, "rows": rows[offset:offset + limit]}


# --- read -----------------------------------------------------------------------------------
@app.get("/api/stats")
def api_stats():
    from pflege_jobs.sources import firecrawl_agent as FA
    s = D.stats()
    fc = FA.credits()
    fc["spent_by_app"] = R.usage_total()
    fc["spent_by_app_7d"] = R.usage_total(days=7)
    fc["used_period"] = (fc["plan"] - fc["remaining"]) if isinstance(fc.get("plan"), int) and isinstance(fc.get("remaining"), int) else None
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
    p = dict(request.query_params)
    rows = D.filter_clinics(p)
    return _page(rows, p.get("limit", 500), p.get("offset", 0))


@app.get("/api/cities")
def api_cities(q: str = ""):
    return D.cities(q)


@app.get("/api/plan")
def api_plan(request: Request):
    p = dict(request.query_params)
    rows = D.plan_rows(p)
    pdf = A.DATA_DIR / "registry" / "krankenhausplan_2026.pdf"
    out = _page(rows, p.get("limit", 1000), p.get("offset", 0))
    out.update({"pdf_url": "/docs/krankenhausplan_2026.pdf" if pdf.exists() else None, "source": rows[0]["source"] if rows else None,
                "source_url": D.PLAN_SOURCE_URL, "columns": list(D.PLAN_COLS) + ["size", "jobs_open"]})
    return out


@app.get("/api/clinics/{clinic_id}")
def api_clinic(clinic_id: str):
    c = D.clinic(clinic_id)
    if not c:
        raise HTTPException(404, "unknown clinic")
    out = dict(c)
    out["jobs"] = D.filter_jobs({"clinic_id": clinic_id, "sort": "-first_published"})
    out["runs"] = [r for r in R.list_runs(200) if clinic_id in (r.get("clinic_ids") or []) or (r["scope"] in ("clinic", "career") and r["value"] == clinic_id)][:20]
    return out


@app.get("/api/jobs")
def api_jobs(request: Request):
    p = dict(request.query_params)
    rows = D.filter_jobs(p)
    return _page(rows, p.get("limit", 200), p.get("offset", 0))


@app.get("/api/jobs/{posting_id}")
def api_job(posting_id: int):
    row = D.job_detail(posting_id)
    if not row:
        raise HTTPException(404, "unknown posting")
    return row


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
    if file is not None:
        blob = await file.read()
        name = file.filename
    else:
        ct = request.headers.get("content-type", "")
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


@app.post("/api/crawl")
async def api_crawl(request: Request):
    body = await request.json()
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
    if target["scope"] == "all" and mode == "firecrawl":
        raise HTTPException(400, "refusing firecrawl for every hospital at once; use auto or a narrower target")
    if mode == "firecrawl" and plan["credits_needed"] > plan["credits_left"]:
        raise HTTPException(409, f"firecrawl budget exhausted: need up to {plan['credits_needed']}, {plan['credits_left']} left this week")
    params = {"max_credits": max_credits, "deep": bool(body.get("fetch_details", body.get("deep"))), "verify": body.get("verify", True), "target": target}
    rid = R.create_run(target["scope"], T.value_string(target), mode, params, [c["clinic_id"] for c in plan["clinics"]], trigger="api")
    R.enqueue(rid)
    return {"run_id": rid, "queued": True, "clinics": len(plan["clinics"]), "boards": plan["boards"], "adapter": len(plan["adapter"]),
            "firecrawl": len(plan["firecrawl"]), "skipped": [{"clinic_id": c["clinic_id"], "reason": c.get("route_reason")} for c in plan["skipped"]][:50]}


@app.get("/api/crawl/plan")
def api_crawl_plan(request: Request, mode: str = "auto", max_credits: int = 40):
    try:
        target = _target_from_query(request)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    p = CR.plan_for(None, None, mode, max_credits, target=target)
    return {"target": target, "clinics": len(p["clinics"]), "boards": p["boards"], "via_adapter": len(p["adapter"]), "via_firecrawl": len(p["firecrawl"]),
            "walled": p["walled"], "est_credits": p["credits_needed"], "credits_left": p["credits_left"],
            "skipped": [{"clinic_id": c["clinic_id"], "reason": c.get("route_reason")} for c in p["skipped"]][:50],
            "sample": [c["name"] for c in p["clinics"][:8]],
            "adapter": [c["clinic_id"] for c in p["adapter"]], "firecrawl": [c["clinic_id"] for c in p["firecrawl"]], "credits_needed": p["credits_needed"]}


@app.get("/api/crawl/runs")
def api_runs(limit: int = 50):
    return R.list_runs(max(1, min(limit, 500)))


@app.get("/api/crawl/runs/{run_id}")
def api_run(run_id: int):
    r = R.get_run(run_id)
    if not r:
        raise HTTPException(404, "unknown run")
    return r


@app.get("/api/inbox")
def api_inbox(recent: int = 25):
    return D.inbox_summary(recent=max(0, min(int(recent), 200)))


@app.post("/api/inbox/drain")
def api_inbox_drain():
    """Queue a run that drains pflege_jobs.inbox via `cli inbox`. Guarded against active runs: a crawl's
    own `execute()` already calls `_cli(["inbox"])` at the end, and two `cli inbox` processes racing the
    same ack/offset-free pagination would double-process rows."""
    if R.active_run_count():
        raise HTTPException(409, "a run is already queued or running; try again once it finishes")
    rid = R.create_run("inbox", "", "adapter", {}, [], trigger="api")
    R.enqueue(rid)
    return {"run_id": rid, "queued": True}


@app.post("/api/clinics/{clinic_id}/refetch-career")
async def api_refetch(clinic_id: str, request: Request):
    if not D.clinic(clinic_id):
        raise HTTPException(404, "unknown clinic")
    try:
        body = await request.json()
    except Exception:
        body = {}
    max_credits = int((body or {}).get("max_credits") or ST.get_firecrawl()["default_max_credits"])
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


@app.post("/api/schedules")
async def api_schedule_create(request: Request):
    try:
        return SC.create(await request.json())
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.put("/api/schedules/{sid}")
async def api_schedule_update(sid: int, request: Request):
    try:
        s = SC.update(sid, await request.json())
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
    body = await request.json()
    try:
        r = ST.save_patterns(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {**r, "patterns": ST.get_patterns()}


@app.post("/api/settings/patterns/validate")
async def api_validate_patterns(request: Request):
    body = await request.json()
    return {"errors": ST.validate_patterns(body)}


@app.put("/api/settings/firecrawl")
async def api_put_firecrawl(request: Request):
    return ST.save_firecrawl(await request.json())


@app.get("/api/firecrawl/credits")
def api_credits():
    from pflege_jobs.sources import firecrawl_agent as FA
    fc = FA.credits()
    fc["spent_by_app"] = R.usage_total()
    fc["spent_by_app_7d"] = R.usage_total(days=7)
    return fc


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


@app.get("/docs/{name}")
def docs_file(name: str):
    if "/" in name or ".." in name or not (name.endswith(".md") or name.endswith(".json")):
        raise HTTPException(404, "not found")
    p = A.DOCS_DIR / name
    if not p.exists():
        raise HTTPException(404, "not found")
    return FileResponse(str(p), media_type="application/json" if name.endswith(".json") else "text/markdown; charset=utf-8")


def _web(rel, media=None):
    p = (A.WEB_DIR / rel).resolve()
    if A.WEB_DIR.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(str(p), media_type=media)


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


@app.get("/autopilot")
@app.get("/autopilot/")
def autopilot_page():
    """Operator console for the recruiting funnel (docs/autopilot.md). Built from web/autopilot.template.html by web/build.py."""
    p = A.WEB_DIR / "autopilot.html"
    if not p.exists():
        return PlainTextResponse("web/autopilot.html not built yet — run `python web/build.py`", status_code=503)
    return FileResponse(str(p), media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.get("/dock.js")
def dock_js():
    return _web("dock.js", "application/javascript")


@app.get("/dock.css")
def dock_css():
    return _web("dock.css", "text/css")


@app.get("/collect.html")
def collect():
    return _web("collect.html", "text/html; charset=utf-8")


@app.get("/collector.js")
def collector():
    return _web("collector.js", "application/javascript")


@app.get("/skill/{name:path}")
def skill(name: str):
    if ".." in name:
        raise HTTPException(404, "not found")
    media = "text/markdown; charset=utf-8" if name.endswith(".md") else None
    return _web("skill/" + name, media)


@app.get("/health")
def health():
    s = D.snapshot()
    return {"ok": bool(s["clinics"]), "clinics": len(s["clinics"]), "jobs": len(s["jobs"]), "error": s.get("error")}
