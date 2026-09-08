"""Stripe pay-per-closed-posting gate.

Model: a customer (clinic or recruiter) subscribes to ONE metered Stripe price (env STRIPE_PRICE_ID, unit = one closed
posting). A usage unit is written when the customer marks a posting as filled ('Stelle besetzt') in /pro:
POST /api/postings/{id}/closed. A posting merely expiring on the hospital board is NOT billed (not attributable).
Every close is recorded locally in closed_postings(posting_id, by_email, at, stripe_usage_id) -- also without a Stripe
key, so the ledger is right the day Stripe is switched on.

Feature flag: everything Stripe hangs on STRIPE_SECRET_KEY. Without it status.configured is false and
checkout / webhook / usage answer 503 {error: 'stripe not configured'}; /closed still records locally.

Endpoints (mounted at /api by app.main):
  GET  /stripe/status                {configured, price_id_set, webhook_set, customers, closed_this_period}
  POST /stripe/checkout {email}      -> {url} of a Checkout Session (mode subscription, the metered price)
  POST /stripe/webhook               signed by STRIPE_WEBHOOK_SECRET (v1 scheme: HMAC-SHA256 over 't.payload');
                                     checkout.session.completed -> customers(status active) (+ the visitor's session,
                                     when the Checkout carried one, becomes a customer session);
                                     customer.subscription.deleted -> status cancelled
  POST /postings/{id}/closed         owner or customer; idempotent per posting; local ledger row always, Stripe usage
                                     when configured and the closer maps to a Stripe customer

Stripe is spoken to with plain requests against STRIPE_API_BASE (default https://api.stripe.com), Bearer
STRIPE_SECRET_KEY; the key is never logged or echoed. Usage goes to Billing Meter events when STRIPE_METER_EVENT_NAME
is set (the current Stripe way to meter), else to the legacy usage_records of the subscription item that carries
STRIPE_PRICE_ID. docs/stripe.md has the model, the env vars and the dashboard walkthrough.
"""
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import auth as AU
from . import config as A
from . import runs as R

router = APIRouter()

DEFAULT_API_BASE = "https://api.stripe.com"
WEBHOOK_TOLERANCE_S = 300
NOT_CONFIGURED = {"error": "stripe not configured"}

# customers / sessions are owned by app/runs.py SCHEMA (auth track); repeated here with the same DDL so this module
# works whichever order the tracks land in. closed_postings is ours.
SCHEMA = """
create table if not exists customers (email text primary key, stripe_customer_id text, status text default 'active', created_at text);
create table if not exists sessions (
  sid_hash text primary key, email text, role text, created_at text, expires_at text, last_seen_at text, stripe_customer_id text);
create table if not exists closed_postings (
  posting_id integer primary key, by_email text, at text, stripe_usage_id text, stripe_customer_id text, stripe_error text);
"""

_init_lock = threading.Lock()
_inited_path = None
_session = requests.Session()          # tests swap this for a fake


# --- env -----------------------------------------------------------------------------------
def secret_key():
    return (os.environ.get("STRIPE_SECRET_KEY") or "").strip()


def price_id():
    return (os.environ.get("STRIPE_PRICE_ID") or "").strip()


def webhook_secret():
    return (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip()


def meter_event_name():
    return (os.environ.get("STRIPE_METER_EVENT_NAME") or "").strip()


def api_base():
    return (os.environ.get("STRIPE_API_BASE") or DEFAULT_API_BASE).rstrip("/")


def configured():
    from . import settings as ST                          # local import: avoids a settings<->stripe_gate import cycle
    return bool(secret_key()) and ST.get_feature_flags().get("stripe", False)


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _ensure():
    global _inited_path
    path = str(A.SQLITE_PATH)
    if _inited_path == path:
        return
    with _init_lock:
        if _inited_path != path:
            R.init()
            with R._lock, R.db() as c:
                c.executescript(SCHEMA)
            _inited_path = path


class StripeError(Exception):
    """Stripe answered with an error; message never contains the key."""

    def __init__(self, status, message):
        super().__init__(f"stripe {status}: {message}")
        self.status = status


def _stripe(method, path, data=None, idempotency_key=None, timeout=20):
    """One form-encoded call against the Stripe REST API. Returns the JSON object; raises StripeError / RequestException."""
    if not configured():
        raise StripeError(503, "not configured")
    headers = {"Authorization": "Bearer " + secret_key()}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    r = _session.request(method, api_base() + path, data=data, headers=headers, timeout=timeout)
    try:
        body = r.json() if r.content else {}
    except ValueError:
        body = {}
    if r.status_code >= 400:
        msg = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
        raise StripeError(r.status_code, (msg or "request failed")[:300])
    return body


# --- customers -----------------------------------------------------------------------------
def customer_for_email(email):
    email = (email or "").strip().lower()
    if not email:
        return None
    _ensure()
    with R._lock, R.db() as c:
        r = c.execute("select email, stripe_customer_id, status, created_at from customers where lower(email)=?", (email,)).fetchone()
    return dict(r) if r else None


def set_customer_status_by_stripe_id(stripe_customer_id, status):
    _ensure()
    with R._lock, R.db() as c:
        cur = c.execute("update customers set status=? where stripe_customer_id=?", (status, stripe_customer_id))
    return cur.rowcount


def _mark_session_customer(sid_hash, email, stripe_customer_id):
    """The Checkout carried the visitor's session (metadata.sid_hash): that session now acts as the customer.
    Owner sessions keep their role."""
    if not sid_hash:
        return 0
    _ensure()
    with R._lock, R.db() as c:
        cur = c.execute("update sessions set role=case when role='owner' then 'owner' else 'customer' end, "
                        "stripe_customer_id=?, email=coalesce(nullif(email,''), ?) where sid_hash=?",
                        (stripe_customer_id, email, sid_hash))
    return cur.rowcount


def _sid_hash_from_cookie(value):
    sid = AU._parse_cookie(value)
    return AU._sha(sid) if sid else None


# --- status --------------------------------------------------------------------------------
def period_start():
    """Start of the current billing period as we count it locally: the calendar month (UTC). Stripe's own invoice
    period starts on the subscription's anchor day; the dashboard has the authoritative count."""
    now = _now()
    return _iso(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))


@router.get("/stripe/status")
def stripe_status():
    _ensure()
    with R._lock, R.db() as c:
        customers = c.execute("select count(*) from customers where coalesce(status,'active')='active'").fetchone()[0]
        closed = c.execute("select count(*) from closed_postings where at>=?", (period_start(),)).fetchone()[0]
    return {"configured": configured(), "price_id_set": bool(price_id()), "webhook_set": bool(webhook_secret()),
            "meter": bool(meter_event_name()), "customers": customers, "closed_this_period": closed,
            "period_start": period_start()}


# --- checkout ------------------------------------------------------------------------------
class CheckoutIn(BaseModel):
    email: str


def checkout_payload(email, sid_hash=None):
    base = AU.public_base()
    d = {"mode": "subscription",
         "line_items[0][price]": price_id(),                 # metered price: no quantity
         "customer_email": email,
         "success_url": base + "/pro?stripe=ok",
         "cancel_url": base + "/pro?stripe=cancel",
         "client_reference_id": hashlib.sha256(email.encode()).hexdigest()[:32],
         "metadata[email]": email,
         "subscription_data[metadata][email]": email}
    if sid_hash:
        d["metadata[sid_hash]"] = sid_hash
    return d


@router.post("/stripe/checkout")
def api_checkout(body: CheckoutIn, request: Request):
    if not configured():
        return JSONResponse(NOT_CONFIGURED, status_code=503)
    if not price_id():
        return JSONResponse({"error": "stripe price not configured"}, status_code=503)
    email = (body.email or "").strip().lower()
    if not email or "@" not in email or len(email) > 254:
        return JSONResponse({"error": "e-mail required"}, status_code=400)
    sid_hash = _sid_hash_from_cookie(request.cookies.get(AU.COOKIE))
    try:
        sess = _stripe("POST", "/v1/checkout/sessions", checkout_payload(email, sid_hash))
    except StripeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    except requests.RequestException as e:
        return JSONResponse({"error": "stripe unreachable: " + type(e).__name__}, status_code=502)
    return {"url": sess.get("url"), "id": sess.get("id")}


# --- webhook -------------------------------------------------------------------------------
def verify_signature(payload: bytes, header: str, secret: str, tolerance=WEBHOOK_TOLERANCE_S, now=None):
    """Stripe v1 scheme: header 't=<unix>,v1=<hex>[,v1=<hex>]'; hex = HMAC-SHA256(secret, '<t>.<payload>')."""
    if not header or not secret:
        return False
    t, sigs = None, []
    for part in header.split(","):
        k, _, v = part.strip().partition("=")
        if k == "t":
            t = v
        elif k == "v1":
            sigs.append(v)
    if not t or not sigs or not t.isdigit():
        return False
    expected = hmac.new(secret.encode(), (t + ".").encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        return False
    return abs((now if now is not None else time.time()) - int(t)) <= tolerance


def sign_payload(payload: bytes, secret: str, t=None):
    """Build a Stripe-Signature header (tests, docs)."""
    t = int(t if t is not None else time.time())
    return f"t={t},v1=" + hmac.new(secret.encode(), (f"{t}.").encode() + payload, hashlib.sha256).hexdigest()


def _checkout_email(obj):
    return ((obj.get("customer_details") or {}).get("email") or obj.get("customer_email")
            or (obj.get("metadata") or {}).get("email") or "").strip().lower()


def handle_event(event):
    """Apply one verified Stripe event to the local tables; returns a small dict describing what happened."""
    typ = event.get("type") or ""
    obj = ((event.get("data") or {}).get("object") or {})
    if typ == "checkout.session.completed":
        email = _checkout_email(obj)
        cus = obj.get("customer") if isinstance(obj.get("customer"), str) else (obj.get("customer") or {}).get("id")
        if not email:
            return {"handled": False, "reason": "no e-mail on the session"}
        AU.upsert_customer(email, cus, status="active")
        marked = _mark_session_customer((obj.get("metadata") or {}).get("sid_hash"), email, cus)
        return {"handled": True, "customer": email, "session_marked": bool(marked)}
    if typ == "customer.subscription.deleted":
        cus = obj.get("customer") if isinstance(obj.get("customer"), str) else (obj.get("customer") or {}).get("id")
        n = set_customer_status_by_stripe_id(cus, "cancelled") if cus else 0
        return {"handled": True, "cancelled": n}
    return {"handled": False, "reason": "ignored event type"}


@router.post("/stripe/webhook")
async def api_webhook(request: Request):
    if not configured():
        return JSONResponse(NOT_CONFIGURED, status_code=503)
    if not webhook_secret():
        return JSONResponse({"error": "stripe webhook secret not configured"}, status_code=503)
    payload = await request.body()
    if not verify_signature(payload, request.headers.get("stripe-signature", ""), webhook_secret()):
        return JSONResponse({"error": "invalid signature"}, status_code=400)
    try:
        event = json.loads(payload.decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "invalid payload"}, status_code=400)
    if not isinstance(event, dict):
        return JSONResponse({"error": "invalid payload"}, status_code=400)
    out = handle_event(event)
    return {"received": True, "type": event.get("type"), **out}


# --- closed postings (the billable unit) ---------------------------------------------------
class ClosedIn(BaseModel):
    customer_email: str | None = None      # owner only: attribute the close to a customer address


def closed_row(posting_id):
    _ensure()
    with R._lock, R.db() as c:
        r = c.execute("select * from closed_postings where posting_id=?", (posting_id,)).fetchone()
    return dict(r) if r else None


def _subscription_item(stripe_customer_id):
    """id of the subscription item carrying STRIPE_PRICE_ID on the customer's active subscription (legacy usage records)."""
    subs = _stripe("GET", "/v1/subscriptions", {"customer": stripe_customer_id, "status": "active", "limit": 10})
    want = price_id()
    for s in subs.get("data") or []:
        for it in ((s.get("items") or {}).get("data") or []):
            if not want or ((it.get("price") or {}).get("id") == want):
                return it.get("id")
    return None


def record_usage(stripe_customer_id, posting_id, at):
    """One unit for one closed posting; idempotent on the Stripe side via identifier / Idempotency-Key.
    Returns the Stripe object id (meter event identifier or usage record id)."""
    key = f"closed-posting-{posting_id}"
    ev = meter_event_name()
    if ev:
        _stripe("POST", "/v1/billing/meter_events",
                {"event_name": ev, "identifier": key, "payload[stripe_customer_id]": stripe_customer_id, "payload[value]": 1,
                 "timestamp": int(at.timestamp())}, idempotency_key=key)
        return key
    si = _subscription_item(stripe_customer_id)
    if not si:
        raise StripeError(404, "no active subscription item for the metered price")
    rec = _stripe("POST", f"/v1/subscription_items/{si}/usage_records",
                  {"quantity": 1, "action": "increment", "timestamp": int(at.timestamp())}, idempotency_key=key)
    return rec.get("id") or key


@router.post("/postings/{posting_id}/closed")
def api_posting_closed(posting_id: int, request: Request, body: ClosedIn | None = None):
    """'Stelle besetzt': the customer (or the owner) marks a posting as filled. Local ledger row always; one Stripe usage
    unit when configured and the closer maps to a Stripe customer. Idempotent: a second call answers the stored row."""
    ident = AU.current(request)
    if ident["role"] not in ("owner", "customer"):
        return JSONResponse({"error": "sign in required", "role": ident["role"], "login_url": "/__exe.dev/login?redirect=/pro"}, status_code=401)
    if posting_id <= 0:
        return JSONResponse({"error": "invalid posting id"}, status_code=400)
    email = (ident.get("email") or "").lower()
    if ident["role"] == "owner" and body and body.customer_email:
        email = body.customer_email.strip().lower()
    existing = closed_row(posting_id)
    if existing:
        return {**existing, "already": True, "billed": bool(existing.get("stripe_usage_id"))}
    at = _now()
    cust = customer_for_email(email) if ident["role"] == "customer" or (body and body.customer_email) else None
    cus_id = (cust or {}).get("stripe_customer_id")
    _ensure()
    with R._lock, R.db() as c:
        c.execute("insert or ignore into closed_postings(posting_id,by_email,at,stripe_customer_id) values(?,?,?,?)",
                  (posting_id, email or None, _iso(at), cus_id))
    usage_id, err = None, None
    if cus_id and (cust or {}).get("status", "active") == "active":
        if not configured():
            err = "stripe not configured"
        else:
            try:
                usage_id = record_usage(cus_id, posting_id, at)
            except StripeError as e:
                err = str(e)
            except requests.RequestException as e:
                err = "stripe unreachable: " + type(e).__name__
    elif email and ident["role"] == "customer":
        err = "no stripe customer for this address"
    with R._lock, R.db() as c:
        c.execute("update closed_postings set stripe_usage_id=?, stripe_error=? where posting_id=?", (usage_id, err, posting_id))
    row = closed_row(posting_id)
    return {**row, "already": False, "billed": bool(usage_id)}


@router.get("/postings/{posting_id}/closed")
def api_posting_closed_get(posting_id: int, request: Request):
    ident = AU.current(request)
    if ident["role"] not in ("owner", "customer"):
        return JSONResponse({"error": "sign in required", "role": ident["role"]}, status_code=401)
    row = closed_row(posting_id)
    return {"posting_id": posting_id, "closed": bool(row), **(row or {})}
