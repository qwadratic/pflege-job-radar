"""Standalone app for the harness: `uvicorn app.wa.asgi:app --port 8502` (deploy/pflege-wa.service).

Why a second process at all: the board process rebuilds a multi-thousand-row snapshot and runs
crawls, and a lead waiting on WhatsApp should not queue behind that. This is the ONLY process that
mounts the /api/wa/* routes: the board no longer does (app/main.py), because on the board VM they
would answer from an empty wa.sqlite. The board reads the harness through the Pro API proxy (TASK-395).

No app.auth middleware here, on purpose (Ivan, 2026-09-14): this process binds 127.0.0.1 only, nginx
forwards just the webhook path to it, and it mounts no login route -- an owner-session gate would
lock the operator out of GET /api/wa/threads on the harness host. Local reads stay open.
"""
from fastapi import FastAPI

from . import pro_api as PA  # also used below for PA.db() -- the one-time schema-creation startup hook
from .api import router
from .bridge_api import router as bridge_router  # POST /wa/bridge-webhook (TASK-352): the phone rail's inbound door
from .pro_api import router as pro_router  # GET/POST /wa/pro/* (TASK-395/396): the board's token-gated proxy target
from .queue_api import router as queue_router  # GET /wa/queue* (TASK-326): consenting candidates x matched clinics
from .router import router as router_router  # POST /wa/route-webhook (TASK-419): Meta's live webhook via nginx
from .status_docs import router as status_docs_router  # GET /wa/pro/status/* (2026-10-06): public token-gated status documents

app = FastAPI(title="pflege-board WhatsApp harness", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router, prefix="/api", tags=["whatsapp"])
app.include_router(router_router, prefix="/api", tags=["whatsapp"])
app.include_router(queue_router, prefix="/api", tags=["whatsapp"])
# Only this process mounts it, and only on 127.0.0.1: the executor's push arrives through the ssh
# tunnel on this host's own stack, which is what the route's loopback check is built on.
app.include_router(bridge_router, prefix="/api", tags=["whatsapp"])
# TASK-395/396: the ONLY prefix nginx exposes off-loopback (/api/wa/pro/*) -- every route under it
# is its own bearer-token gate (pro_api._authorize), unlike every other route in this file, which
# stays open on the strength of this process binding 127.0.0.1 only (see the module docstring above).
app.include_router(pro_router, prefix="/api", tags=["whatsapp"])
# Status documents (2026-10-06, Ivan): mounted right next to pro_router, under the SAME "/api"
# prefix -- it rides the exact nginx rule and the exact pro_api._authorize bearer check above, so a
# public status-document link needs no new nginx config and no new secret. See
# app/wa/status_docs.py's own module docstring for the board-side /s/{token}/ proxy this feeds.
app.include_router(status_docs_router, prefix="/api", tags=["whatsapp"])


@app.on_event("startup")
def _create_wa_schema():
    """Eager, once, before this process serves its first request (review item 7, Ivan 2026-09-30):
    every Pro API GET route now reads over a ``mode=ro`` connection (pro_api.db_ro), which cannot run
    CREATE TABLE/ALTER TABLE -- so the schema (store.py's own, queue.py's, and pro_api's own
    wa_handoffs/wa_handoff_events) must already exist by then. ``PA.db()`` is exactly the call every
    write route already made per-request; doing it once here, at startup, is the only change."""
    PA.db()


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "pflege-wa"}
