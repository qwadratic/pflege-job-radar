"""GET /api/wa/* -- server-side proxy to the WhatsApp harness's read-only Pro API (TASK-395).

The board owns no WhatsApp data of its own: `app.wa.asgi:app` is a separate process on
tasker-dispatcher-01, next to its own `data/wa.sqlite` (app/main.py:53). Topology B (Ivan,
2026-09-29): the harness serves a bearer-token-gated `/api/wa/pro/*`, and this module is the one
place that holds `WA_API_TOKEN` -- it is read from the environment, attached to the outbound
request, and never echoed back to the browser in any response header or body.

This is NOT under app/wa/ (that package is the harness itself, a different process/deployment) --
it is a thin board-side adapter, each route a straight forward of the matching
`{WA_API_BASE}/api/wa/pro/...` call with the query string carried over verbatim. The contract for
every field these responses carry is docs/wa-dashboard.md (pflege-fe) for /threads*/health, and
docs/wa-pro-activity.md (TASK-283.7) for /activity and /ops; this module does not interpret either
body at all, so it cannot drift from what the harness actually sends.

Error mapping (the view depends on these being distinguishable, docs/wa-dashboard.md "Errors the
view distinguishes"):
  WA_API_BASE unset          -> 503, "not configured" (never a local DB, never 200 with empty rows)
  harness unreachable        -> 502
  harness timed out          -> 504
  harness 401/403 (bad token)-> 502 "harness rejected the board token" (NOT 401 -- the view reads a
                                 bare 401 from this board as "owners only" and would send a human to
                                 /login for a problem that is actually a misconfigured WA_API_TOKEN)
  harness 404 (unknown id)   -> 404, harness body passed through unchanged
  harness 5xx                -> 502, its status folded into the message
  anything else (2xx incl.)  -> passed through unchanged, body and status both

Every route here is owner-gated by the board's own middleware (app/auth.py: OWNER_READ_PREFIXES
carries "/api/wa/threads" and "/api/wa/health"), on top of whatever the harness itself enforces --
the harness's own /wa/health is local-only/unauthenticated on 8502, but this board-side
/api/wa/health is a different, owner-gated route that merely forwards its body.
"""
import os
import re
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

router = APIRouter()

# Connect fast -- the harness is one known host away (tasker-dispatcher-01), so a hung TCP handshake
# means it is actually down, not slow. Read generously: GET /api/wa/threads has no page cap (CLAUDE.md,
# "No safety nets"; docs/wa-dashboard.md), so a large envelope can legitimately take a while to arrive.
_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=10.0)

# Swapped for httpx.MockTransport(...) in tests/test_app_wa_proxy.py; None means "make a real
# connection" (httpx's own default transport).
_TRANSPORT = None


def _api_base():
    """WA_API_BASE, read fresh per request (not frozen at import) so a test or a config reload takes
    effect immediately, same discipline as app/wa/config.py's accessors."""
    return (os.environ.get("WA_API_BASE") or "").strip().rstrip("/")


def _api_token():
    return (os.environ.get("WA_API_TOKEN") or "").strip()


async def _forward(request: Request, harness_path: str) -> Response:
    base = _api_base()
    if not base:
        raise HTTPException(503, "the WhatsApp harness is not configured (WA_API_BASE is unset)")

    url = base + harness_path
    qs = request.url.query               # forwarded byte-for-byte: the FE's own encoding, untouched
    if qs:
        url = f"{url}?{qs}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_TRANSPORT) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {_api_token()}"})
    except httpx.TimeoutException:
        raise HTTPException(504, "the WhatsApp harness did not answer in time")
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"the WhatsApp harness is unreachable: {exc}")

    if resp.status_code in (401, 403):
        # Never pass a 401/403 straight through: this board's own middleware uses 401 to mean "you are
        # not an owner", and the view (docs/wa-dashboard.md) reads it exactly that way. A rejected
        # WA_API_TOKEN is an operator misconfiguration, not a visitor's role, so it gets its own 502.
        raise HTTPException(502, "harness rejected the board token")
    if resp.status_code >= 500:
        raise HTTPException(502, f"the WhatsApp harness answered {resp.status_code}: {resp.text[:300]}")

    # Every other status (2xx, 404 on an unknown thread, ...) passes through unchanged -- body and
    # status both -- per docs/wa-dashboard.md's "anything else: the status and the message".
    return Response(content=resp.content, status_code=resp.status_code,
                     media_type=resp.headers.get("content-type", "application/json"))


@router.get("/wa/threads")
async def wa_threads(request: Request):
    return await _forward(request, "/api/wa/pro/threads")


@router.get("/wa/threads/{thread_id}")
async def wa_thread(request: Request, thread_id: str):
    return await _forward(request, f"/api/wa/pro/threads/{quote(thread_id, safe='')}")


@router.get("/wa/threads/{thread_id}/messages")
async def wa_thread_messages(request: Request, thread_id: str):
    return await _forward(request, f"/api/wa/pro/threads/{quote(thread_id, safe='')}/messages")


@router.get("/wa/health")
async def wa_health(request: Request):
    return await _forward(request, "/api/wa/pro/health")


@router.get("/wa/activity")
async def wa_activity(request: Request):
    """TASK-283.7 (board side): the Pro activity rail (rail/queue/job health) --
    docs/wa-pro-activity.md. Owner-gated the same way as every route above
    (app/auth.py:OWNER_READ_PREFIXES carries "/api/wa/activity")."""
    return await _forward(request, "/api/wa/pro/activity")


@router.get("/wa/ops")
async def wa_ops(request: Request):
    """TASK-283.7 (board side): the ops mirror list, query string carried over verbatim (status/
    origin/before_id/after_id/limit -- docs/wa-pro-activity.md). Owner-gated
    (app/auth.py:OWNER_READ_PREFIXES carries "/api/wa/ops")."""
    return await _forward(request, "/api/wa/pro/ops")


# --- status documents at public token links (TASK-436) ---------------------------------------------------
# GET /s/{token}/ and /s/{token}/{name}: the one route of this module that is public. The harness keeps a
# candidate's status documents (index.html, detail.html, PDFs) outside the repository and serves them at
# /api/wa/pro/status/{token}/[name] behind the board bearer; the unguessable token in the URL is the whole
# access check, so whoever holds the link reads the document. Contract: docs/wa-dashboard.md, "Status
# documents (public token links)". Mounted without the /api prefix (app/main.py), and in none of
# app/auth.py's gated lists on purpose.
#
# The reader is anonymous, so nothing of the board or the harness is told to them: an address that is not
# a document's shape is a 404 from here without a call, and every failure behind it is a fixed text with
# no upstream body, URL or token in it.
status_router = APIRouter()

_STATUS_TOKEN = re.compile(r"[A-Za-z0-9_-]{22}")                           # secrets.token_urlsafe(16), as the harness mints it
_STATUS_NAME = re.compile(r"index\.html|detail\.html|[a-z0-9][a-z0-9._-]{0,80}\.pdf")
_STATUS_HEADERS = ("content-type", "x-robots-tag", "referrer-policy", "cache-control")


async def _status(token: str, name: str) -> Response:
    if not _STATUS_TOKEN.fullmatch(token) or (name and not _STATUS_NAME.fullmatch(name)):
        raise HTTPException(404, "not found")
    base = _api_base()
    if not base:
        raise HTTPException(503, "documents are not available right now")
    try:
        # No Range, no If-*, no query string: the harness supports none of them on this route.
        async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_TRANSPORT) as client:
            resp = await client.get(f"{base}/api/wa/pro/status/{token}/{name}", headers={"Authorization": f"Bearer {_api_token()}"})
    except httpx.TimeoutException:
        raise HTTPException(504, "the document did not arrive in time")
    except httpx.HTTPError:
        raise HTTPException(502, "the document could not be fetched")
    if resp.status_code == 404:
        raise HTTPException(404, "not found")
    if resp.status_code != 200:                 # 401/403 = this board's WA_API_TOKEN is wrong; 5xx; anything the contract does not name
        raise HTTPException(502, "the document could not be fetched")
    # nginx in front of the harness sets X-Robots-Tag as well, so the header arrives twice: each distinct value once.
    return Response(content=resp.content, status_code=200,
                    headers={k: ", ".join(dict.fromkeys(resp.headers.get_list(k))) for k in _STATUS_HEADERS if k in resp.headers})


@status_router.get("/s/{token}")
async def status_doc_bare(token: str):
    """A relative link inside index.html (detail.html, a PDF) resolves against the directory, so the page lives at /s/{token}/."""
    if not _STATUS_TOKEN.fullmatch(token):
        raise HTTPException(404, "not found")
    return RedirectResponse(f"/s/{token}/", status_code=308)


@status_router.get("/s/{token}/")
async def status_doc_index(token: str):
    return await _status(token, "")


@status_router.get("/s/{token}/{name}")
async def status_doc_file(token: str, name: str):
    return await _status(token, name)
