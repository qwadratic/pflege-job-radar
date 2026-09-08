"""Who is visiting: owner (exe.dev login or tailnet), customer (magic link after Stripe), or anonymous.

identity(request) is computed once per request by the middleware installed with install(app) and stored in
request.state.identity. GET /api/me exposes it to the pages (role/email/via -- never the raw proxy headers).

Sources, in order of precedence:
  1. X-ExeDev-Email injected by the exe.dev proxy for logged-in exe.dev users (clients cannot spoof it; absent for
     anonymous visitors on a public share) -> owner when the address is in OWNER_EMAILS.
  2. Tailnet: TAILNET_TRUST=1 and request.client.host inside 100.64.0.0/10 -> owner.
  3. Signed cookie 'pj_session' -> row in the sessions table (role owner / customer). Sessions are created by the
     magic-link flow here (owner e-mails, or customer e-mails in the customers table filled by the Stripe track).
  4. Otherwise anonymous.

AUTH_DISABLED=1 (local dev / tests without headers): everyone is owner via 'disabled', nothing is enforced.
Schema (magic_links, sessions, customers) lives in app/runs.py; docs/auth.md explains the flows and env vars.
"""
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import config as A
from . import runs as R

router = APIRouter()

COOKIE = "pj_session"
SESSION_DAYS = 30
MAGIC_TTL_MIN = 15
MAGIC_PER_HOUR = 3
MAIL_GATEWAY = "http://169.254.169.254/gateway/email/send"
DEFAULT_OWNER = "ivan.d.kotelnikov@gmail.com"
TAILNET = ipaddress.ip_network("100.64.0.0/10")

_init_lock = threading.Lock()
_inited_path = None       # SQLITE_PATH the tables were ensured for (tests swap the path per test)
_secret_cache = None


# --- env / helpers ---------------------------------------------------------------------------
def owner_emails():
    raw = os.environ.get("OWNER_EMAILS") or DEFAULT_OWNER
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def auth_disabled():
    return os.environ.get("AUTH_DISABLED", "").strip() in ("1", "true", "yes")


def tailnet_trusted():
    return os.environ.get("TAILNET_TRUST", "").strip() == "1"


def public_base():
    return (os.environ.get("PUBLIC_BASE_URL") or "https://pflege-board.exe.xyz").rstrip("/")


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _ensure():
    """Tables come from app/runs.py SCHEMA; create them once per SQLite path (first boot / a fresh temp DB in tests)."""
    global _inited_path, _secret_cache
    path = str(A.SQLITE_PATH)
    if _inited_path == path:
        return
    with _init_lock:
        if _inited_path != path:
            R.init()
            _inited_path, _secret_cache = path, None


def _sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def session_secret():
    """SESSION_SECRET from env, else one generated once and persisted in the settings table."""
    global _secret_cache
    env = os.environ.get("SESSION_SECRET")
    if env:
        return env
    if _secret_cache:
        return _secret_cache
    _ensure()
    sec = R.get_setting("session_secret")
    if not sec:
        sec = secrets.token_hex(32)
        R.set_setting("session_secret", sec)
    _secret_cache = sec
    return sec


def _sign(sid):
    return hmac.new(session_secret().encode(), sid.encode(), hashlib.sha256).hexdigest()[:32]


def _cookie_value(sid):
    return f"{sid}.{_sign(sid)}"


def _parse_cookie(value):
    """cookie -> sid when the signature is valid, else None."""
    if not value or "." not in value:
        return None
    sid, sig = value.rsplit(".", 1)
    if not sid or not hmac.compare_digest(sig, _sign(sid)):
        return None
    return sid


def _is_tailnet(host):
    try:
        return ipaddress.ip_address(host) in TAILNET
    except (ValueError, TypeError):
        return False


# --- customers / sessions (also used by the Stripe track) -----------------------------------
def customer_role(email):
    """'owner' / 'customer' / None for an e-mail (owner list first, then the customers table)."""
    email = (email or "").strip().lower()
    if not email:
        return None
    if email in owner_emails():
        return "owner"
    _ensure()
    with R._lock, R.db() as c:
        r = c.execute("select status from customers where lower(email)=?", (email,)).fetchone()
    return "customer" if r and (r["status"] or "active") == "active" else None


def upsert_customer(email, stripe_customer_id=None, status="active"):
    """Stripe track: remember a paying customer so a magic link can be issued to that address."""
    _ensure()
    with R._lock, R.db() as c:
        c.execute("insert into customers(email,stripe_customer_id,status,created_at) values(?,?,?,?) "
                  "on conflict(email) do update set stripe_customer_id=coalesce(excluded.stripe_customer_id,customers.stripe_customer_id), status=excluded.status",
                  (email.strip().lower(), stripe_customer_id, status, _iso(_now())))


def create_session(email, role, stripe_customer_id=None):
    """Insert a session row; returns the signed cookie value to set with set_session_cookie()."""
    _ensure()
    sid = secrets.token_urlsafe(32)
    now = _now()
    with R._lock, R.db() as c:
        c.execute("insert into sessions(sid_hash,email,role,created_at,expires_at,last_seen_at,stripe_customer_id) values(?,?,?,?,?,?,?)",
                  (_sha(sid), (email or "").lower(), role, _iso(now), _iso(now + timedelta(days=SESSION_DAYS)), _iso(now), stripe_customer_id))
    return _cookie_value(sid)


def set_session_cookie(response, value):
    response.set_cookie(COOKIE, value, max_age=SESSION_DAYS * 86400, httponly=True, secure=True, samesite="lax", path="/")


def clear_session_cookie(response):
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=True, samesite="lax")


def session_from_cookie(value):
    """Valid, unexpired session row for a cookie value, else None."""
    sid = _parse_cookie(value)
    if not sid:
        return None
    _ensure()
    with R._lock, R.db() as c:
        r = c.execute("select email, role, expires_at, stripe_customer_id from sessions where sid_hash=?", (_sha(sid),)).fetchone()
    if not r or (r["expires_at"] or "") <= _iso(_now()) or r["role"] not in ("owner", "customer"):
        return None
    if r["role"] == "customer":                      # a cancelled Stripe subscription revokes access at once
        with R._lock, R.db() as c:
            st = c.execute("select status from customers where lower(email)=?", ((r["email"] or "").lower(),)).fetchone()
        if not st or st["status"] != "active":
            return None
    return dict(r)


def delete_session(value):
    sid = _parse_cookie(value)
    if not sid:
        return
    _ensure()
    with R._lock, R.db() as c:
        c.execute("delete from sessions where sid_hash=?", (_sha(sid),))


# --- identity ------------------------------------------------------------------------------
def identity(request):
    """{role: owner|customer|anonymous, email, via: exe|tailnet|session|disabled|none}."""
    if auth_disabled():
        return {"role": "owner", "email": None, "via": "disabled"}
    exe_email = (request.headers.get("x-exedev-email") or "").strip().lower()
    if exe_email and exe_email in owner_emails():
        return {"role": "owner", "email": exe_email, "via": "exe"}
    client = request.client
    if tailnet_trusted() and client and _is_tailnet(client.host):
        return {"role": "owner", "email": None, "via": "tailnet"}
    sess = session_from_cookie(request.cookies.get(COOKIE))
    if sess:
        return {"role": sess["role"], "email": sess["email"], "via": "session"}
    # exe.dev-logged-in but not an owner: still anonymous for the app; the e-mail helps the gate card explain why.
    return {"role": "anonymous", "email": exe_email or None, "via": "exe" if exe_email else "none"}


def current(request):
    ident = getattr(request.state, "identity", None) if hasattr(request, "state") else None
    return ident or identity(request)


# --- mail ----------------------------------------------------------------------------------
def send_mail(to, subject, body):
    """VM mail gateway (plain text, rate-limited). Tests stub this."""
    r = requests.post(MAIL_GATEWAY, json={"to": to, "subject": subject, "body": body}, timeout=10)
    r.raise_for_status()
    return True


# --- magic link ----------------------------------------------------------------------------
class MagicIn(BaseModel):
    email: str


@router.post("/auth/magic")
def api_magic(body: MagicIn):
    """Always 200 {sent: true} (or 429 when the address asked more than 3 times this hour) -- never reveals whether the
    e-mail is known. A token row is written for every address; only owner / customer rows are mailed and resolvable."""
    email = (body.email or "").strip().lower()
    if not email or "@" not in email or len(email) > 254:
        return JSONResponse({"error": "e-mail required"}, status_code=400)
    _ensure()
    now = _now()
    role = customer_role(email) or "none"
    token = secrets.token_urlsafe(32)
    with R._lock, R.db() as c:
        n = c.execute("select count(*) from magic_links where email=? and created_at>?", (email, _iso(now - timedelta(hours=1)))).fetchone()[0]
        if n >= MAGIC_PER_HOUR:
            return JSONResponse({"error": "too many requests for this e-mail; try again later"}, status_code=429)
        c.execute("insert into magic_links(email,token_hash,role,created_at,expires_at) values(?,?,?,?,?)",
                  (email, _sha(token), role, _iso(now), _iso(now + timedelta(minutes=MAGIC_TTL_MIN))))
    if role in ("owner", "customer"):
        link = f"{public_base()}/api/auth/magic/{token}"
        try:
            send_mail(email, "pflege-board: your sign-in link",
                      f"Open this link within {MAGIC_TTL_MIN} minutes to sign in to pflege-board:\n\n{link}\n\n"
                      "The link works once. If you did not request it, ignore this mail.")
        except Exception as e:                                       # gateway down / rate-limited: do not leak that via the status
            print("magic-link mail failed:", type(e).__name__, str(e)[:200])
    return {"sent": True}


@router.get("/auth/magic/{token}")
def api_magic_consume(token: str):
    """Single use, 15 min TTL. Valid -> session cookie (30 days) + redirect to /pro; else 400."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", token or ""):
        return JSONResponse({"error": "invalid or expired link"}, status_code=400)
    _ensure()
    now = _iso(_now())
    with R._lock, R.db() as c:
        r = c.execute("select id, email, role, expires_at, used_at from magic_links where token_hash=?", (_sha(token),)).fetchone()
        if not r or r["used_at"] or (r["expires_at"] or "") <= now or r["role"] not in ("owner", "customer"):
            return JSONResponse({"error": "invalid or expired link"}, status_code=400)
        c.execute("update magic_links set used_at=? where id=? and used_at is null", (now, r["id"]))
    value = create_session(r["email"], r["role"])
    resp = RedirectResponse("/pro", status_code=303)
    set_session_cookie(resp, value)
    return resp


@router.post("/auth/logout")
def api_logout(request: Request):
    delete_session(request.cookies.get(COOKIE))
    resp = JSONResponse({"ok": True})
    clear_session_cookie(resp)
    return resp


@router.get("/me")
def api_me(request: Request):
    ident = dict(current(request))
    ident["login_url"] = "/__exe.dev/login?redirect=/pro"
    ident["auth_disabled"] = auth_disabled()
    return ident


@router.get("/flags")
def api_flags():
    """Public, unauthenticated: only the render-relevant subset of feature_flags (settings.PUBLIC_FLAG_KEYS),
    so e.g. dock.js can decide whether to render at all before a visitor is known to be owner/customer/anonymous."""
    from . import settings as ST
    return ST.public_feature_flags()


# --- middleware ----------------------------------------------------------------------------
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
OWNER_WRITE_PREFIXES = ("/api/crawl", "/api/schedules", "/api/settings", "/api/inbox/drain", "/api/hunter", "/api/scheduler")
OWNER_WRITE_RE = re.compile(r"^/api/clinics/[^/]+/refetch-career/?$")
OWNER_READ_PREFIXES = ("/api/billing", "/api/hunter", "/api/settings", "/api/coverage", "/api/inbox", "/api/firecrawl/credits", "/api/crawl/runs")
GATED_PAGES = ("/pro", "/pro/", "/autopilot", "/autopilot/")


def _prefixed(path, prefixes):
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def required_role(method, path):
    """'owner' / 'member' (owner or customer) / None for a request. Pages are gated only for the SPA's benefit
    (the middleware serves them regardless; /api/me drives the gate card)."""
    if method in WRITE_METHODS and (_prefixed(path, OWNER_WRITE_PREFIXES) or OWNER_WRITE_RE.match(path)):
        return "owner"
    if method in ("GET", "HEAD") and _prefixed(path, OWNER_READ_PREFIXES):
        return "owner"
    if method in ("GET", "HEAD") and path in GATED_PAGES:
        return "member"
    return None


def allowed(role, need):
    if need is None:
        return True
    if need == "owner":
        return role == "owner"
    return role in ("owner", "customer")


class AuthMiddleware:
    """Pure ASGI: computes identity once (scope['state']['identity'] == request.state.identity), then enforces the
    matrix above for API paths. Unauthorised API -> 401 {error}; unauthorised page -> served anyway (no 302 to the
    exe.dev login; the SPA's gate card offers it)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        ident = identity(request)
        scope.setdefault("state", {})["identity"] = ident
        if auth_disabled():
            return await self.app(scope, receive, send)
        need = required_role(scope.get("method", "GET"), scope.get("path", ""))
        if not allowed(ident["role"], need) and scope.get("path", "").startswith("/api/"):
            resp = JSONResponse({"error": "owner only" if need == "owner" else "sign in required", "role": ident["role"],
                                 "login_url": "/__exe.dev/login?redirect=/pro"}, status_code=401)
            return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


def install(app):
    """Register the identity middleware on the FastAPI app (called once at import by app.main)."""
    app.add_middleware(AuthMiddleware)
    return app
