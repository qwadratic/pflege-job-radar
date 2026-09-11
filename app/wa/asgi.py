"""Standalone app for the harness: `uvicorn app.wa.asgi:app --port 8502` (deploy/pflege-wa.service).

Why a second process at all: the board process rebuilds a multi-thousand-row snapshot and runs
crawls, and a lead waiting on WhatsApp should not queue behind that. The routes are the same module
the board mounts, so there is one implementation and one set of tests; run one door or the other,
whichever nginx points at -- both write data/wa.sqlite and only the one receiving webhooks writes at all.
"""
from fastapi import FastAPI

from .api import router

app = FastAPI(title="pflege-board WhatsApp harness", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router, prefix="/api", tags=["whatsapp"])


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "pflege-wa"}
