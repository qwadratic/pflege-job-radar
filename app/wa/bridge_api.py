"""The phone rail's inbound door: the route that replaces Meta's webhook (TASK-123).

There is no webhook on this rail. The executor on the remote machine watches the handset and pushes
what it saw here, through the ssh tunnel, as a **verbatim Meta envelope** -- so this module parses
nothing, maps nothing and decides nothing. It checks who is calling and hands the body to
``api.accept_payload`` / ``api.submit_accepted``, the exact pair ``POST /wa/webhook`` uses. Every
consequence follows from code that is already live and already tested: ``parse_message`` reads the
message, ``record_inbound_pending`` writes it, ``wa_messages.wamid`` UNIQUE drops a re-push exactly
like a Meta redelivery, the one background worker answers it. An envelope that needed a translation
layer here would mean the executor is speaking the wrong language, not that this module is missing a
feature -- that is the test: if a ``tests/test_wa_*.py`` file has to change, the adapter is wrong.

THE TRUST BOUNDARY, since there is no Meta signature to verify:

* **Loopback only.** The executor reaches us over ``ssh -R``/``-L``, so sshd opens the connection to
  this port from this host's own stack and ``request.client.host`` is ``127.0.0.1``. A POST sourced
  from anywhere else -- a VPN peer, the public interface -- is 403 with nothing read. That property
  is the reason the plan picked an ssh tunnel over a VPN: with a VPN the caller's address is the
  peer's, and this check would have to be weakened to a header anyone can forge.
* **A separate secret, compared in constant time.** ``WA_BRIDGE_INBOUND_TOKEN`` in
  ``X-Pflege-Bridge-Token``, never ``WA_BRIDGE_TOKEN`` (server -> executor) and never
  ``META_WHATSAPP_APP_SECRET``, which never leaves this host at all: a leak in one direction must not
  grant the other. Unset means the door is shut, not open -- an empty token would otherwise match an
  empty header and let an unauthenticated payload into a real conversation.

Not ``WA_INTERNAL_WEBHOOK_ENABLED`` (``router.py``, TASK-86): that flag belongs to the colleague's
production system forwarding a Meta payload it received, whose own gate is the loopback alone. This
is our executor with a secret of its own, so it carries its own switch: no token, no door.
"""
import hmac
import logging

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import api as API
from . import bridge as BR
from . import config as C
from . import meta as M

router = APIRouter()
log = logging.getLogger(__name__)

TOKEN_HEADER = "X-Pflege-Bridge-Token"
DELIVERY_HEADER = "X-Bridge-Delivery-Id"   # the executor's own id for this push, logged on a refusal
HEALTH_PATH = "/v1/health"
_LOOPBACK = ("127.0.0.1", "::1")


def _authorized(request):
    """-> True only for a loopback caller carrying the inbound token. Never says which check failed:
    the answer to a caller that got either wrong is the same 403."""
    host = request.client.host if request.client else None
    if host not in _LOOPBACK:
        return False
    if not C.BRIDGE_INBOUND_TOKEN:
        return False
    return hmac.compare_digest(str(request.headers.get(TOKEN_HEADER) or ""), C.BRIDGE_INBOUND_TOKEN)


@router.post("/wa/bridge-webhook", include_in_schema=False)
async def wa_bridge_webhook(request: Request):
    """Inbound from the phone rail. Checked, then recorded and answered exactly like Meta's webhook
    (TASK-99): only the body read runs on the event loop, the turns run in the background worker."""
    if not _authorized(request):
        log.warning("bridge-webhook refused: caller=%s delivery=%s",
                    request.client.host if request.client else None,
                    request.headers.get(DELIVERY_HEADER))
        raise HTTPException(403, "bridge callers only")
    raw = await request.body()
    return await run_in_threadpool(receive_bridge_webhook, raw)


def receive_bridge_webhook(raw):
    """The same three calls ``api.receive_webhook`` makes after its signature check, unchanged."""
    accepted = API.accept_payload(API.parse_webhook_body(raw))
    API.submit_accepted(accepted)
    return API.accepted_summary(accepted)


@router.get("/wa/bridge-health")
def wa_bridge_health():
    """Is the rail alive? One call, answered by the executor itself (``GET /v1/health``).

    Through ``bridge.Client``'s own wire rather than a second HTTP client here: same base URL, same
    bearer, same timeout, same error taxonomy -- a separate one would drift from the rail it reports
    on. An executor that cannot be reached is this endpoint's answer, not its failure: it comes back
    ``{"ok": false, "error": ...}`` with what went wrong, which is the whole point of asking.
    """
    try:
        status, body = BR.Client()._request("GET", HEALTH_PATH)
    except M.MetaError as exc:
        return {"ok": False, "status": exc.status_code, "error": str(exc)}
    return {"ok": status == 200, "status": status, "health": body}
