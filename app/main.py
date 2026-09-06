"""FastAPI entry point. `uvicorn app.main:app --port 8501`.

Serves the SPA (web/index.html), the agent skill, docs, and the JSON API documented in docs/api.md.
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
from . import runs as R
from . import scheduler as S
from . import search as SE
from . import settings as ST

app = FastAPI(title="pflege-board", version="1.0", docs_url="/api/openapi-ui", redoc_url=None, openapi_url="/api/openapi.json")


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
SCOPES = ("clinic", "city", "regierungsbezirk", "landkreis", "job", "board", "all")
MODES = ("auto", "adapter", "firecrawl")


@app.post("/api/crawl")
async def api_crawl(request: Request):
    body = await request.json()
    scope, mode = body.get("scope", "clinic"), body.get("mode", "auto")
    value = str(body.get("value") or "").strip()
    if scope not in SCOPES:
        raise HTTPException(400, f"scope must be one of {SCOPES}")
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    if scope != "all" and not value:
        raise HTTPException(400, "value required")
    max_credits = int(body.get("max_credits") or ST.get_all()["firecrawl"]["default_max_credits"])
    if max_credits <= 0 or max_credits > 500:
        raise HTTPException(400, "max_credits must be 1..500")
    try:
        plan = CR.plan_for(scope, value, mode, max_credits)
    except Exception as e:
        raise HTTPException(400, str(e))
    if not plan["clinics"]:
        raise HTTPException(404, "scope matched no clinic")
    if mode == "firecrawl" and plan["credits_needed"] > plan["credits_left"]:
        raise HTTPException(409, f"firecrawl budget exhausted: need up to {plan['credits_needed']}, {plan['credits_left']} left this week")
    if scope == "all" and mode == "firecrawl":
        raise HTTPException(400, "refusing firecrawl for every clinic at once; use auto or a narrower scope")
    params = {"max_credits": max_credits, "deep": bool(body.get("deep")), "verify": body.get("verify", True)}
    rid = R.create_run(scope, value or "all", mode, params, [c["clinic_id"] for c in plan["clinics"]], trigger="api")
    R.enqueue(rid)
    return {"run_id": rid, "queued": True, "clinics": len(plan["clinics"]), "adapter": len(plan["adapter"]), "firecrawl": len(plan["firecrawl"]),
            "skipped": [{"clinic_id": c["clinic_id"], "reason": c.get("route_reason")} for c in plan["skipped"]][:50]}


@app.get("/api/crawl/plan")
def api_crawl_plan(scope: str = "clinic", value: str = "", mode: str = "auto", max_credits: int = 40):
    p = CR.plan_for(scope, value, mode, max_credits)
    return {"clinics": len(p["clinics"]), "adapter": [c["clinic_id"] for c in p["adapter"]], "firecrawl": [c["clinic_id"] for c in p["firecrawl"]],
            "skipped": [{"clinic_id": c["clinic_id"], "reason": c.get("route_reason")} for c in p["skipped"]], "credits_needed": p["credits_needed"], "credits_left": p["credits_left"]}


@app.get("/api/crawl/runs")
def api_runs(limit: int = 50):
    return R.list_runs(max(1, min(limit, 500)))


@app.get("/api/crawl/runs/{run_id}")
def api_run(run_id: int):
    r = R.get_run(run_id)
    if not r:
        raise HTTPException(404, "unknown run")
    return r


@app.post("/api/clinics/{clinic_id}/refetch-career")
async def api_refetch(clinic_id: str, request: Request):
    if not D.clinic(clinic_id):
        raise HTTPException(404, "unknown clinic")
    try:
        body = await request.json()
    except Exception:
        body = {}
    max_credits = int((body or {}).get("max_credits") or ST.get_all()["firecrawl"]["default_max_credits"])
    if CR._budget_left() < max_credits:
        raise HTTPException(409, f"firecrawl budget exhausted ({CR._budget_left()} credits left this week)")
    rid = R.create_run("career", clinic_id, "firecrawl", {"max_credits": max_credits}, [clinic_id], trigger="api")
    R.enqueue(rid)
    return {"run_id": rid, "queued": True}


@app.post("/api/autocrawl/tick")
def api_autocrawl_tick():
    """Run today's autocrawl batch now (what the scheduler would do at `hour`)."""
    return S.tick(force=True) or {"runs": []}


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


@app.put("/api/settings/schedule")
async def api_put_schedule(request: Request):
    return ST.save_schedule(await request.json())


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


@app.get("/agents.md")
def agents_md():
    p = A.DOCS_DIR / "agents.md"
    if not p.exists():
        raise HTTPException(404, "docs/agents.md missing")
    return FileResponse(str(p), media_type="text/markdown; charset=utf-8")


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


@app.get("/")
def index():
    p = A.WEB_DIR / "index.html"
    if not p.exists():
        return PlainTextResponse("web/index.html not built yet — run `python web/build.py`", status_code=503)
    return FileResponse(str(p), media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.get("/llms.txt")
def llms():
    return _web("llms.txt", "text/plain; charset=utf-8")


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
