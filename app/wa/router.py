"""Webhook router (TASK-84): the dispatch layer TASK-75 deliberately left out. app/wa/routing.py
built the ownership decision (route_decision) and its own read endpoint, but Meta only supports
ONE webhook URL per phone-number-id -- something has to actually receive that one call and decide,
per message, whether this harness or the real production system answers it.

This module is safe, offline-testable code: an "us"-owned message goes through the existing
app.wa.api.handle_payload() unchanged; a "them"-owned message is forwarded, byte-for-byte
reconstructed and re-signed with the shared Meta app secret, to the real system's own webhook URL
(WA_REAL_SYSTEM_WEBHOOK_URL) -- their receiving code verifies X-Hub-Signature-256 exactly as if
Meta had called them directly, since both systems share one Meta app/app secret (docs/whatsapp.md).

What this module does NOT do, deliberately: register itself as Meta's actual webhook URL. That is
a one-time, external configuration change in Meta Business Manager, made by whoever owns that
Meta app -- a production-infrastructure change needing its own explicit sign-off, not something
code in this repo can or should do on its own. The route below (POST /wa/route-webhook) exists so
that step, when and if it happens, has somewhere real to point.
"""
import hashlib
import hmac
import json

from fastapi import APIRouter, HTTPException, Request

from . import api as API
from . import config as C
from . import meta as M
from . import routing as R
from . import store as ST

router = APIRouter()


def _split_payload_by_owner(payload, conn):
    """-> (us_payload, them_payload), the same nested Meta shape as the input, each holding only
    the messages whose phone's route_decision() is that owner. A change/entry left with zero
    messages after filtering is dropped, not kept empty."""
    us = {"object": payload.get("object"), "entry": []}
    them = {"object": payload.get("object"), "entry": []}
    for entry in payload.get("entry") or []:
        us_changes, them_changes = [], []
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            us_msgs, them_msgs = [], []
            for m in value.get("messages") or []:
                phone = M.sender_e164(m.get("from"))
                if not phone:
                    continue
                owner = R.route_decision(conn, phone)
                (us_msgs if owner == "us" else them_msgs).append(m)
            if us_msgs:
                us_changes.append({**change, "value": {**value, "messages": us_msgs}})
            if them_msgs:
                them_changes.append({**change, "value": {**value, "messages": them_msgs}})
        if us_changes:
            us["entry"].append({**entry, "changes": us_changes})
        if them_changes:
            them["entry"].append({**entry, "changes": them_changes})
    return us, them


def _sign(body):
    return "sha256=" + hmac.new(C.APP_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _default_forward(body, headers):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url=C.REAL_SYSTEM_WEBHOOK_URL, data=body, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=C.HTTP_TIMEOUT_SEC) as resp:
            return getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"real-system webhook returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"real-system webhook forward failed: {exc.reason}") from exc


def _forward_to_real_system(payload, forward=None):
    """Reconstructs the payload as bytes and re-signs it -- it is not the original raw body once
    split, so the original X-Hub-Signature-256 no longer applies; a fresh signature over the
    exact bytes being sent is what makes the real system's own verification pass."""
    if not C.REAL_SYSTEM_WEBHOOK_URL:
        raise RuntimeError(
            "a 'them'-owned message needs forwarding but WA_REAL_SYSTEM_WEBHOOK_URL is not "
            "configured -- refusing to silently drop it; configure the real system's webhook URL")
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)}
    fwd = forward or _default_forward
    return fwd(body, headers)


def route_webhook(raw_body, signature_header, meta_client=None, forward=None):
    """The one real Meta webhook call, split and dispatched. -> {"us": handle_payload() result or
    None, "them_forwarded": bool}. Raises PermissionError on a bad signature (same trust boundary
    as app.wa.api.wa_webhook) and propagates any forwarding failure loudly -- a silently dropped
    'them' message is a real candidate's real reply going unanswered."""
    if not M.validate_webhook_signature(raw_body, signature_header, C.APP_SECRET):
        raise PermissionError("invalid webhook signature")
    payload = json.loads(raw_body)
    with ST._lock, R.db() as conn:
        us_payload, them_payload = _split_payload_by_owner(payload, conn)
    result = {"us": None, "them_forwarded": False}
    if us_payload["entry"]:
        result["us"] = API.handle_payload(us_payload, client=meta_client)
    if them_payload["entry"]:
        _forward_to_real_system(them_payload, forward=forward)
        result["them_forwarded"] = True
    return result


@router.post("/wa/route-webhook", include_in_schema=False)
async def wa_route_webhook(request: Request):
    """Not Meta's registered webhook URL today (see module docstring) -- exists so that step has
    somewhere to point once it is deliberately taken."""
    raw = await request.body()
    try:
        return route_webhook(raw, request.headers.get("X-Hub-Signature-256"))
    except PermissionError:
        raise HTTPException(403, "invalid signature")
