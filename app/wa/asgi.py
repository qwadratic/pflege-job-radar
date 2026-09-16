"""Standalone app for the harness: `uvicorn app.wa.asgi:app --port 8502` (deploy/pflege-wa.service).

Why a second process at all: the board process rebuilds a multi-thousand-row snapshot and runs
crawls, and a lead waiting on WhatsApp should not queue behind that. The routes are the same module
the board mounts, so there is one implementation and one set of tests; run one door or the other,
whichever nginx points at -- both write data/wa.sqlite and only the one receiving webhooks writes at all.

No app.auth middleware here, on purpose (Ivan, 2026-09-14): this process binds 127.0.0.1 only, nginx
forwards just the webhook path to it, and it mounts no login route -- an owner-session gate would
lock the operator out of GET /api/wa/threads on the harness host. Local reads stay open.
"""
from fastapi import FastAPI

from .api import router
from .router import router as router_router  # POST /wa/route-webhook (TASK-84): Meta's live webhook via nginx

app = FastAPI(title="pflege-board WhatsApp harness", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router, prefix="/api", tags=["whatsapp"])
app.include_router(router_router, prefix="/api", tags=["whatsapp"])


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "pflege-wa"}
