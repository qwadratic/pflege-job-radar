"""Webhook router (TASK-84): Meta supports ONE webhook URL per phone-number-id, so this route receives
that one call and decides, per object, whether this harness or the real production system gets it.
nginx on the harness host forwards Meta's registered webhook path to POST /api/wa/route-webhook.

Split (``_split_payload_by_owner``, TASK-99): every object lands in exactly one half, except call objects. First
every message sender gets its route_decision() (app/wa/routing.py, which records a new phone's owner). Then each
object goes to the recorded owner of its phone: messages (``from``), statuses (``recipient_id``; a status
of one of our own outbound wamids is always ours), contacts and user preferences (``wa_id``), message echoes
(``to``). Call objects (``api.is_call_object``: ``calls``, statuses with type=call, call-permission replies) always
go to the real system, whoever owns the chat: this harness places and answers no calls, and the real system's
manager CRM places calls and reads the SDP answer and ACCEPTED from these webhooks; a phone we own also keeps a
raw copy (wa_webhook_events). Only a message decides a new phone's owner: a status,
call or contact for a phone with no ownership record goes to the real system and records nothing -- the
known-phones export lags, and a status of the real system's outbound message to a brand-new number must
not hand that conversation to us. Anything naming no phone -- other value keys (errors, history, ...),
other change fields (template status, account updates, ...), a change for another phone_number_id, an
entry of an unknown shape -- goes to the real system unchanged; only the envelope is copied.

Order (``route_webhook``, run in the threadpool, never on the event loop): verify the signature -> split
-> record the "us" half (app.wa.api.accept_payload: messages with their pending rows, statuses, raw
events) and queue it for the background worker -> forward the "them" half, re-signed with the shared
Meta app secret, to WA_REAL_SYSTEM_WEBHOOK_URL -> 200. A forward failure answers 502 so Meta redelivers
the whole payload; by then the "us" messages are duplicates and statuses/events are stored once, so the
redelivery only retries the forward.
"""
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import api as API
from . import config as C
from . import meta as M
from . import routing as R

router = APIRouter()
log = logging.getLogger(__name__)


class ForwardError(RuntimeError):
    """The "them" half did not reach the real system; the route answers 502 so Meta redelivers."""


class InvalidPayload(ValueError):
    """A correctly signed body that is not a JSON object."""


def _split_payload_by_owner(payload, conn):
    """-> (us_payload or None, them_payload or None), each in Meta's nested shape; see the module docstring
    for which object goes where. A change or entry left with nothing is dropped."""
    entries = payload.get("entry") or []
    if not isinstance(entries, list):
        return None, payload
    for change in _changes_of_our_number(entries):
        for m in change["value"].get("messages") or []:
            phone = API.item_phone("messages", m)
            if phone:
                R.route_decision(conn, phone)

    def owner(key, item, phone):
        if key == "statuses" and conn.execute("select 1 from wa_messages where wamid=? and direction='out'",
                                              (str(item.get("id") or ""),)).fetchone():
            return "us"
        row = conn.execute("select owner from wa_ownership where phone=?", (phone,)).fetchone()
        return row["owner"] if row else "them"

    us = {"object": payload.get("object"), "entry": []}
    them = {**{k: v for k, v in payload.items() if k != "entry"}, "entry": []}
    for entry in entries:
        changes = entry.get("changes") if isinstance(entry, dict) else None
        if not isinstance(changes, list):
            them["entry"].append(entry)
            continue
        us_changes, them_changes = [], []
        for change in changes:
            us_change, them_change = _split_change(change, owner)
            if us_change:
                us_changes.append(us_change)
            if them_change:
                them_changes.append(them_change)
        envelope = {k: v for k, v in entry.items() if k != "changes"}
        if us_changes:
            us["entry"].append({**envelope, "changes": us_changes})
        if them_changes:
            them["entry"].append({**envelope, "changes": them_changes})
    has_them = bool(them["entry"]) or any(k not in ("object", "entry") for k in payload)
    return (us if us["entry"] else None), (them if has_them else None)


def _changes_of_our_number(entries):
    for entry in entries:
        changes = entry.get("changes") if isinstance(entry, dict) else None
        for change in changes if isinstance(changes, list) else []:
            value = change.get("value") if isinstance(change, dict) else None
            if isinstance(value, dict) and API._number_matches(value):
                yield change


def _split_change(change, owner):
    """-> (us_change or None, them_change or None) for one entry[].changes[] object."""
    value = change.get("value") if isinstance(change, dict) else None
    if not isinstance(value, dict) or not API._number_matches(value):
        return None, change
    envelope = {k: v for k, v in value.items() if k in API._ENVELOPE_KEYS}
    us_value, them_value = dict(envelope), dict(envelope)
    for key, items in value.items():
        if key in API._ENVELOPE_KEYS:
            continue
        if key not in API.PHONE_OF_ITEM or not isinstance(items, list):
            them_value[key] = items
            continue
        for item in items:
            phone = API.item_phone(key, item)
            ours = bool(phone) and owner(key, item, phone) == "us"
            if API.is_call_object(key, item):
                # The real system is the only call bridge (its manager CRM places calls and reads the SDP answer
                # and ACCEPTED from these webhooks); we keep a raw copy for a phone we own.
                them_value.setdefault(key, []).append(item)
                if ours:
                    us_value.setdefault(key, []).append(item)
                continue
            (us_value if ours else them_value).setdefault(key, []).append(item)
    rest = {k: v for k, v in change.items() if k != "value"}
    return ({**rest, "value": us_value} if len(us_value) > len(envelope) else None,
            {**rest, "value": them_value} if len(them_value) > len(envelope) else None)


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
        raise ForwardError(f"real-system webhook returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ForwardError(f"real-system webhook forward failed: {exc.reason}") from exc


def _forward_to_real_system(payload, forward=None):
    """Reconstructs the payload as bytes and re-signs it -- it is not the original raw body once
    split, so the original X-Hub-Signature-256 no longer applies; a fresh signature over the
    exact bytes being sent is what makes the real system's own verification pass. Any failure of the
    transport is a ForwardError."""
    if not C.REAL_SYSTEM_WEBHOOK_URL:
        raise ForwardError(
            "a 'them'-owned object needs forwarding but WA_REAL_SYSTEM_WEBHOOK_URL is not "
            "configured -- refusing to silently drop it; configure the real system's webhook URL")
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)}
    fwd = forward or _default_forward
    try:
        return fwd(body, headers)
    except ForwardError:
        raise
    except Exception as exc:
        raise ForwardError(f"real-system webhook forward failed: {type(exc).__name__}: {exc}") from exc


def route_webhook(raw_body, signature_header, meta_client=None, forward=None):
    """The one real Meta webhook call, split and dispatched (module docstring). -> {"us": the accepted
    summary or None, "them_forwarded": bool}; the "us" turns run later in the background worker
    (app.wa.api.wait_for_background). Raises PermissionError on a bad signature (same trust boundary as
    app.wa.api.wa_webhook), InvalidPayload on a body that is not a JSON object, ForwardError when the
    "them" half was not delivered -- a silently dropped 'them' message is a real candidate's real reply
    going unanswered."""
    if not M.validate_webhook_signature(raw_body, signature_header, C.APP_SECRET):
        raise PermissionError("invalid webhook signature")
    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise InvalidPayload(f"invalid json: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidPayload("webhook payload is not a JSON object")
    with R.db() as conn:
        us_payload, them_payload = _split_payload_by_owner(payload, conn)
    result = {"us": None, "them_forwarded": False}
    if us_payload:
        accepted = API.accept_payload(us_payload)
        API.submit_accepted(accepted, client=meta_client)
        result["us"] = API.accepted_summary(accepted)
    if them_payload:
        _forward_to_real_system(them_payload, forward=forward)
        result["them_forwarded"] = True
    return result


@router.post("/wa/route-webhook", include_in_schema=False)
async def wa_route_webhook(request: Request):
    """Meta's webhook, via nginx. Only the body read runs on the event loop."""
    raw = await request.body()
    try:
        return await run_in_threadpool(route_webhook, raw, request.headers.get("X-Hub-Signature-256"))
    except PermissionError:
        raise HTTPException(403, "invalid signature")
    except InvalidPayload:
        raise HTTPException(400, "invalid payload")
    except ForwardError as exc:
        log.error("real-system forward failed, answering 502 so Meta redelivers: %s", exc)
        raise HTTPException(502, "real-system forward failed")


# --- local-only internal receiver (TASK-86): the real system stays Meta's primary webhook -------
# An alternative to the split-and-forward design above: instead of THIS harness receiving Meta's
# call directly and deciding ownership, the real system's OWN webhook handler gains a small check
# ("is this phone already one of our candidates?") and forwards only a brand-new lead's payload to
# this endpoint, over a same-host loopback call. This repo does not modify the real system's code
# -- see docs/whatsapp.md for exactly what that side would need to add; this endpoint is only the
# receiving half, built and tested here.
#
# No Meta signature to check here: the caller is the real system, not Meta, and the call never
# crosses the public internet. The trust boundary is network origin (loopback only) plus an
# explicit opt-in flag (WA_INTERNAL_WEBHOOK_ENABLED, default off) instead of a cryptographic check
# -- dropping signature verification is a real access-control change, not a shortcut, so it needs
# its own explicit gate rather than silently relying on "nobody will guess this path".

_LOCAL_HOSTS = ("127.0.0.1", "::1")


def _is_local_caller(request):
    """True only for a direct loopback connection. Does not handle a reverse-proxied deployment
    (X-Forwarded-For) -- this endpoint is meant to be called directly by a same-host process, not
    through nginx; if that ever changes, this check needs revisiting rather than trusting a
    spoofable header by default."""
    host = request.client.host if request.client else None
    return host in _LOCAL_HOSTS


@router.post("/wa/internal-webhook", include_in_schema=False)
async def wa_internal_webhook(request: Request):
    """Receives a payload already vetted and forwarded by the real production system's own
    webhook (see module docstring) -- not Meta directly, so there is nothing to verify here beyond
    the access checks below. Recorded and answered like POST /wa/webhook (TASK-99): off the event loop,
    turns in the background worker."""
    if not C.INTERNAL_WEBHOOK_ENABLED or not _is_local_caller(request):
        raise HTTPException(403, "local calls only")
    raw = await request.body()
    return await run_in_threadpool(_receive_internal, raw)


def _receive_internal(raw):
    accepted = API.accept_payload(API.parse_webhook_body(raw))
    API.submit_accepted(accepted)
    return API.accepted_summary(accepted)
