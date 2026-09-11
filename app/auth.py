"""Who is visiting: owner (the shared login), customer (magic link after Stripe), or anonymous.

identity(request) is computed once per request by the middleware installed with install(app) and stored in
request.state.identity. GET /api/me exposes it to the pages (role/email/via, plus login_url and
default_credentials).

Sources, in order of precedence:
  1. AUTH_DISABLED=1 (local dev / tests) -> everyone is owner via 'disabled', nothing is enforced. This branch
     must stay first: tests/conftest.py sets it for every suite except this module's own.
  2. Signed cookie 'pj_session' -> row in the sessions table (role owner / customer). Sessions are created by
     POST /api/auth/login (the shared owner passphrase) and by the magic-link flow here.
  3. Otherwise anonymous.

There is no proxy-header door and no tailnet door any more: anything that can reach the uvicorn port directly
could set X-ExeDev-Email itself, which made the header branch a forge hole rather than a login.
Schema (magic_links, sessions, customers) lives in app/runs.py; docs/auth.md explains the flows and env vars.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import config as A
from . import data as D                  # D.json_body: the one JSON-body door (see app/data.py)
from . import runs as R

router = APIRouter()

COOKIE = "pj_session"
SESSION_DAYS = 30
MAGIC_TTL_MIN = 15
MAGIC_PER_HOUR = 3
LOGIN_FAIL_PER_HOUR = 10
# A global LOGIN_PER_HOUR bucket shipped 2026-09-10 and was reverted 2026-09-11: one shared credential + one
# bucket for everyone is a lockout anyone can trigger, not a throttle (docs/auth.md, "Rate limits"). This is
# the per-IP design named there as safe -- keyed on request.client.host, not X-Forwarded-For: port 8501 is
# directly internet-reachable (confirmed against journalctl access-log source IPs 2026-09-11, not behind a
# proxy that sets a trustworthy forwarded header), so the raw TCP peer already *is* the real caller.
MAIL_GATEWAY = "http://169.254.169.254/gateway/email/send"
DEFAULT_OWNER = "ivan.d.kotelnikov@gmail.com"
DEFAULT_LOGIN = ("root", "toor")          # seeded default, guessable by design; docs/auth.md says to change it, not what it is

_init_lock = threading.Lock()
_inited_path = None       # SQLITE_PATH the tables were ensured for (tests swap the path per test)
_secret_cache = None


# --- env / helpers ---------------------------------------------------------------------------
def owner_emails():
    """Magic-link role decision only (customer_role): an address on this list gets an owner session when it
    consumes a link. It is not a login door -- the passphrase below is."""
    raw = os.environ.get("OWNER_EMAILS") or DEFAULT_OWNER
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def auth_disabled():
    return os.environ.get("AUTH_DISABLED", "").strip() in ("1", "true", "yes")


def login_url(next_path="/pro"):
    """Where an unauthenticated visitor is sent. /login is a normal page served by app/main.py."""
    return "/login?next=" + quote(next_path or "/pro", safe="/")


def unauthorised(path, need, role):
    """401 in RFC 9457 shape (docs/errors.md), same body app/main.py:_problem() builds -- the middleware and
    these routes run outside the exception handlers, so they cannot call it -- plus role/login_url, which the
    pages use to tell "sign in" apart from "signed in as the wrong role"."""
    detail = "owner only" if need == "owner" else "sign in required"
    return JSONResponse({"type": "/docs/errors.md#unauthenticated", "title": "Unauthenticated", "status": 401,
                         "detail": detail, "instance": path, "error": detail,
                         "role": role, "login_url": login_url(path if not path.startswith("/api/") else "/pro")},
                        status_code=401, media_type="application/problem+json")


def insufficient_scope(path, scope):
    """403 in RFC 9457 shape, same reason as unauthorised(): the middleware runs outside the exception
    handlers. The `scope` extension names what the caller has to be given -- an agent that gets this back
    knows exactly which scope to ask its operator for, which a bare 403 does not tell it."""
    detail = f"this agent key does not carry the scope {scope}"
    return JSONResponse({"type": "/docs/errors.md#insufficient_scope", "title": "Insufficient scope", "status": 403,
                         "detail": detail, "instance": path, "error": detail, "scope": scope},
                        status_code=403, media_type="application/problem+json")


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
    """cookie -> sid when the signature is valid, else None.

    .encode() on both sides, not compare_digest's str overload -- same shape as app/firecrawl_hooks.py:96,
    for the same reason: Starlette hands cookies over latin-1-decoded, and compare_digest refuses a str
    that carries a non-ASCII character (TypeError). This runs in the middleware for EVERY request, so the
    str overload turned one header (`Cookie: pj_session=abc.<0xff>`) into a 500 on every route including
    /, /login and /health -- anonymous, proven live 2026-09-10."""
    if not value or "." not in value:
        return None
    sid, sig = value.rsplit(".", 1)
    if not sid or not hmac.compare_digest(sig.encode(), _sign(sid).encode()):
        return None
    return sid


# --- owner login: one shared user + passphrase, only SHA-256 hashes persisted (settings key "login"),
# same shape as the agent key in app/settings.py. The plaintext is never stored, never logged, never
# echoed -- a wrong guess and a right guess differ only in the status code. ----------------------------
def login_credentials():
    """{user_hash, pass_hash, is_default}. No row yet -> the shipped root/toor pair with is_default true;
    nothing is written on read, so "no settings.login row" *is* "still on the defaults"."""
    _ensure()
    cur = R.get_setting("login")
    if not cur:
        return {"user_hash": _sha(DEFAULT_LOGIN[0]), "pass_hash": _sha(DEFAULT_LOGIN[1]), "is_default": True}
    return cur


def check_login(user, password):
    """compare_digest's str overload is safe here, unlike in _parse_cookie/stripe_gate: both sides are a
    sha256 hexdigest, and _sha() encodes the caller's text before hashing, so nothing non-ASCII can reach
    the comparison. Same reasoning covers app/settings.py:check_agent_key."""
    cur = login_credentials()
    return (hmac.compare_digest(_sha(user or ""), cur["user_hash"])
            and hmac.compare_digest(_sha(password or ""), cur["pass_hash"]))


def set_login(user, password):
    """Replace both halves at once and drop the default flag. There is no "old password" check: whoever can
    call this already holds an owner session, which is the same power."""
    _ensure()
    R.set_setting("login", {"user_hash": _sha(user), "pass_hash": _sha(password), "is_default": False})


def _client_ip(request):
    return request.client.host if request.client else ""


def _login_failures_last_hour(ip):
    _ensure()
    with R._lock, R.db() as c:
        return c.execute("select count(*) from login_failures where ip=? and at>?",
                          (ip, _iso(_now() - timedelta(hours=1)))).fetchone()[0]


def _record_login_failure(ip):
    _ensure()
    with R._lock, R.db() as c:
        c.execute("insert into login_failures(ip, at) values(?, ?)", (ip, _iso(_now())))


def warn_default_credentials():
    """Startup honesty rail (registered by install()): say out loud that the door is open."""
    if login_credentials()["is_default"]:
        print(f"WARNING: owner login still uses the shipped default credentials "
              f"({DEFAULT_LOGIN[0]}/{DEFAULT_LOGIN[1]}) -- anyone who read the repo can sign in as owner. "
              f"Change them with PUT /api/auth/password.")


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
    """{role: owner|customer|anonymous, email, via: disabled|session|none}. auth_disabled() stays first."""
    if auth_disabled():
        return {"role": "owner", "email": None, "via": "disabled"}
    sess = session_from_cookie(request.cookies.get(COOKIE))
    if sess:
        return {"role": sess["role"], "email": sess["email"], "via": "session"}
    return {"role": "anonymous", "email": None, "via": "none"}


def current(request):
    ident = getattr(request.state, "identity", None) if hasattr(request, "state") else None
    return ident or identity(request)


# --- mail ----------------------------------------------------------------------------------
def send_mail(to, subject, body):
    """VM mail gateway (plain text, rate-limited). Tests stub this."""
    r = requests.post(MAIL_GATEWAY, json={"to": to, "subject": subject, "body": body}, timeout=10)
    r.raise_for_status()
    return True


# --- shared owner login --------------------------------------------------------------------
@router.post("/auth/login")
async def api_login(request: Request):
    """{user, pass} -> 200 {ok, default_credentials} + a 30-day owner session cookie, else 401. The reply says
    nothing about which half was wrong. Throttled per source IP (LOGIN_FAIL_PER_HOUR wrong guesses/hour) --
    not the global bucket reverted 2026-09-11: a wrong guess from one address never costs another address its
    attempts, so it cannot lock the owner out from a different IP."""
    ip = _client_ip(request)
    if _login_failures_last_hour(ip) >= LOGIN_FAIL_PER_HOUR:
        return JSONResponse({"error": "too many wrong attempts from this address; try again later"}, status_code=429)
    body = await D.json_body(request)
    user, password = (body.get("user") or "").strip(), body.get("pass") or ""
    if not check_login(user, password):
        _record_login_failure(ip)
        return unauthorised(request.url.path, "owner", current(request)["role"])
    resp = JSONResponse({"ok": True, "default_credentials": login_credentials()["is_default"]})
    set_session_cookie(resp, create_session(user, "owner"))
    return resp


@router.put("/auth/password")
async def api_set_password(request: Request):
    """Owner only (OWNER_WRITE_PATHS -- deliberately not reachable with an agent key). Sets both halves and
    clears is_default; existing sessions survive, they are keyed on the cookie secret, not on the passphrase."""
    body = await D.json_body(request)
    user, password = (body.get("user") or "").strip(), body.get("pass") or ""
    if not user or not password:
        return JSONResponse({"error": "user and pass required"}, status_code=400)
    set_login(user, password)
    return {"ok": True}


# --- magic link (the customer door; an OWNER_EMAILS address gets an owner session the same way) ------
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
    """Single use, 15 min TTL. Valid -> session cookie (30 days) + redirect to /pro; else 400. The role comes off the
    token row, so this is the only way to become `customer` -- the passphrase door only ever mints owner."""
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
    ident["login_url"] = login_url()
    ident["auth_disabled"] = auth_disabled()
    ident["default_credentials"] = login_credentials()["is_default"]
    return ident


@router.post("/auth/agent")
async def api_auth_agent(request: Request):
    """The gate's password field for a non-interactive agent (no e-mail, no exe.dev account): checks the
    same key the X-Api-Key header carries, but never grants owner/customer access and says nothing about
    the key's scopes -- success just tells the caller where the agent doc lives, GET is what actually
    serves it (already public). GET /api/agent/manifest is the scope-aware entry point."""
    from . import settings as ST
    body = await D.json_body(request)
    if ST.check_agent_key((body.get("key") or "").strip()):
        return {"ok": True, "skill_url": "/skill/SKILL.md"}
    return JSONResponse({"ok": False, "error": "invalid key"}, status_code=401)


@router.get("/flags")
def api_flags():
    """Public, unauthenticated: only the render-relevant subset of feature_flags (settings.PUBLIC_FLAG_KEYS),
    so e.g. dock.js can decide whether to render at all before a visitor is known to be owner/customer/anonymous."""
    from . import settings as ST
    return ST.public_feature_flags()


# --- middleware ----------------------------------------------------------------------------
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
OWNER_WRITE_PREFIXES = ("/api/crawl", "/api/schedules", "/api/settings", "/api/inbox/drain", "/api/ingest", "/api/hunter", "/api/scheduler", "/api/campaign",
                        "/api/autopilot", "/api/autocrawl", "/api/mechanics", "/api/refresh-cache")
# "/api/refresh-cache" joined the list on 2026-09-10: app/main.py:api_refresh calls data.refresh() -> _build(),
# which re-pulls every open row of v_postings and every clinic from Supabase (app/data.py:_build, via
# A.rest_get_all -- the anon key, 1000 rows per call; there is no service key in this app, app/config.py:26-31)
# and re-runs routing over the result. It was anonymous, so any caller could make this box walk the whole
# board against Supabase in a loop, and each pass is thousands of rows. It is not scopable either (no
# AGENT_ROUTES entry): nothing an agent does needs to force a snapshot, the snapshot rebuilds itself after
# every run and on TTL. data/repair_split_merged.py:236 calls it and now needs an owner session.
OWNER_WRITE_RE = re.compile(r"^/api/clinics/[^/]+/refetch-career/?$")
# Exact paths, not a prefix: the rest of /api/auth/* is the login door itself and has to stay reachable.
OWNER_WRITE_PATHS = ("/api/auth/password",)
# "/api/crawl" (not just "/api/crawl/runs") also covers GET /api/crawl/plan -- it previews the same
# credits_left / per-clinic routing decision the gated endpoints above protect (2026-09-08 API audit).
OWNER_READ_PREFIXES = ("/api/billing", "/api/hunter", "/api/settings", "/api/coverage", "/api/inbox", "/api/firecrawl", "/api/crawl", "/api/campaign",
                       "/api/autopilot", "/api/schedules")
GATED_PAGES = ("/pro", "/pro/", "/autopilot", "/autopilot/")
# Pages a customer may not read either. /deck is the internal next-steps briefing: unfixed security facts,
# deploy detail, the open decisions -- it was in GATED_PAGES, which is the level a paying customer reaches
# through the magic link, so every customer could read it (proven 2026-09-10: GET /deck -> 200 with a
# customer cookie). /pro and /autopilot stay "member": they are what the customer pays for.
OWNER_PAGES = ("/deck", "/deck/")
# Writes that need any session (owner or customer), not owner. The page carrying the form stays public;
# only the upload itself is gated -- see docs/auth.md, "CV upload".
MEMBER_API = ("/api/cv", "/api/postings")
# Reads that need any session. GET /api/postings/{id}/closed names the address that closed a posting, so it is
# not public; /api/cv stays out of this list on purpose (only the upload is gated, not the page's GET).
MEMBER_READ = ("/api/postings",)

def _prefixed(path, prefixes):
    return any(path == p or path.startswith(p + "/") for p in prefixes)


# --- agent keys and scopes -------------------------------------------------------------------
# An agent key (X-Api-Key, minted by PUT /api/settings/agent-key) is a narrower door than an owner login,
# for a non-interactive crawler/reviewer agent with no session cookie. Each key carries a set of scopes;
# a scope opens exactly the routes AGENT_ROUTES lists for it and nothing else.
#
# Never scopable, owner session only, no exceptions -- these are simply absent from AGENT_ROUTES, and a
# path with no entry gets the plain 401 rather than a scope to go and ask for:
#   /api/settings*        can rewrite kill_switch_pct, reserve_credits and every hunter rail
#   /api/hunter*          start clears the day's stop_reason (app/hunter_api.py)
#   /api/scheduler*       resume clears a kill-switch pause without re-checking (app/scheduler.py)
#   /api/autocrawl/tick   force=True ignores the pause
#   /api/campaign         its `stopped` flag IS the global Firecrawl kill switch (app/crawl.py:90-93)
#   /api/schedules writes persistent recurring spend (reads are read:ops)
#   /api/mechanics/*/try|test  shells out to pytest
#   /api/billing*, /api/stripe*, /api/autopilot*, PUT /api/auth/password, POST /api/postings/{id}/closed
SCOPES = ("read:board", "read:ops", "write:crawl", "spend:firecrawl",
          "write:ingest:posting", "write:ingest:clinic", "write:ingest:link", "write:ingest:verify")

# envelope type -> the scope POST /api/ingest needs for it. posting/listing/probe all land in the inbox
# and share one scope; the other three hit the edge ops directly, and crawl_run.finished is run
# bookkeeping, so it rides the same authority as firing a run.
INGEST_SCOPE = {"posting.observed": "write:ingest:posting", "listing.observed": "write:ingest:posting",
                "probe.ats_discovery": "write:ingest:posting", "clinic.upserted": "write:ingest:clinic",
                "clinic_link.asserted": "write:ingest:link", "posting.verified": "write:ingest:verify",
                "crawl_run.finished": "write:crawl"}


def _crawl_scope(body):
    """mode=adapter costs nothing. auto and firecrawl both reach Firecrawl -- auto routes every
    non-routable or walled clinic there (app/crawl.py:plan_for) and mode=="firecrawl" counts as manual to
    the kill switch, which lets it through the throttle tier -- so both need the spend scope."""
    return "write:crawl" if (body or {}).get("mode", "auto") == "adapter" else "spend:firecrawl"


def ingest_scopes(body):
    """Every scope the events in this body need, first one first. The middleware lets the request through
    when the key holds any of them; POST /api/ingest then rejects the individual events it may not write,
    which is what turns a partly-scoped batch into a 207.

    Same isinstance test app/main.py:api_ingest uses, so the middleware and the handler read the same body
    the same way. It matters here: {"events": {...}} used to iterate the dict's *keys*, and `"type".get`
    is an AttributeError inside the middleware -- a 500 echoing the exception where the handler would have
    answered 400. A malformed body must reach the handler and be named there, never crash the gate.

    The type is checked for `str` and not just read, for the same reason: dict.get() with an unhashable key
    raises TypeError, so {"events": [{"type": [1]}]} crashed the gate here exactly the way it crashed
    app/main.py:_validate_event (both fixed 2026-09-11)."""
    events = (body or {}).get("events")
    events = events if isinstance(events, list) else [body or {}]
    out = []
    for e in events:
        typ = e.get("type") if isinstance(e, dict) else None
        s = INGEST_SCOPE.get(typ) if isinstance(typ, str) else None
        if s and s not in out:
            out.append(s)
    return out or ["write:ingest:posting"]


# The scopes each body-dependent route can end up needing, so GET /api/agent/manifest can publish them
# without re-deriving what the resolver above decides per request.
_crawl_scope.scopes = ("write:crawl", "spend:firecrawl")
ingest_scopes.scopes = tuple(dict.fromkeys(INGEST_SCOPE.values()))


def _rx(template):
    """'/api/clinics/{clinic_id}/refetch-career' -> a regex matching one path segment per {param}."""
    return re.compile("^" + "[^/]+".join(re.escape(p) for p in re.split(r"\{[^}]+\}", template)) + "/?$")


# (method, path, scope, side_effects, cost). `scope` is a string, or a callable(body) returning the scope
# (or the list of acceptable scopes) for that request. GET /api/agent/manifest is generated from this
# table, so the published contract and the middleware cannot drift apart.
_ROUTES = (
    ("GET", "/api/stats", "read:board", "none", "free"),
    ("GET", "/api/facets", "read:board", "none", "free"),
    ("GET", "/api/taxonomy", "read:board", "none", "free"),
    ("GET", "/api/ontology", "read:board", "none", "free"),
    ("GET", "/api/cities", "read:board", "none", "free"),
    ("GET", "/api/plan", "read:board", "none", "free"),
    ("GET", "/api/search", "read:board", "none", "free"),
    ("GET", "/api/clinics", "read:board", "none", "free"),
    ("GET", "/api/clinics/{clinic_id}", "read:board", "none", "free"),
    ("GET", "/api/jobs", "read:board", "none", "free"),
    ("GET", "/api/jobs/{posting_id}", "read:board", "none", "free"),
    ("GET", "/api/crawl/runs", "read:ops", "none", "free"),
    ("GET", "/api/crawl/runs/{run_id}", "read:ops", "none", "free"),
    ("GET", "/api/crawl/plan", "read:ops", "none", "free"),
    ("GET", "/api/crawl/estimate", "read:ops", "walks the clinic's live board", "network, no credits"),
    ("GET", "/api/schedules", "read:ops", "none", "free"),
    ("GET", "/api/schedules/presets", "read:ops", "none", "free"),
    ("GET", "/api/schedules/{sid}/preview", "read:ops", "none", "free"),
    ("GET", "/api/coverage", "read:ops", "none", "free"),
    ("GET", "/api/inbox", "read:ops", "none", "free"),
    ("POST", "/api/crawl", _crawl_scope, "queues a crawl run", "credits unless mode is adapter"),
    ("POST", "/api/crawl/runs/{run_id}/cancel", "write:crawl", "stops a run at the next board boundary", "free"),
    ("POST", "/api/inbox/drain", "write:crawl", "queues an inbox drain run", "free"),
    ("POST", "/api/clinics/{clinic_id}/refetch-career", "spend:firecrawl",
     "rewrites ats_type / careers_url in production, no rollback", "credits"),
    ("POST", "/api/ingest", ingest_scopes, "writes inbox rows or edge-op rows", "free"),
)
AGENT_ROUTES = tuple({"method": m, "path": p, "re": _rx(p), "scope": s, "side_effects": fx, "cost": cost,
                      "scopes": list(s.scopes) if callable(s) else [s]}
                     for m, p, s, fx, cost in _ROUTES)


def route_for(method, path):
    for e in AGENT_ROUTES:
        if e["method"] == method and e["re"].match(path):
            return e
    return None


def agent_allowed(method, path, body, scopes):
    """(allowed, the scope the caller is missing). No entry in AGENT_ROUTES -> (False, None): the route is
    owner-session-only and no scope opens it, so there is nothing to name in a 403."""
    e = route_for(method, path)
    if not e:
        return False, None
    need = e["scope"](body) if callable(e["scope"]) else e["scope"]
    need = [need] if isinstance(need, str) else list(need)
    if any(s in scopes for s in need):
        return True, None
    return False, need[0]


def body_scoped(method, path):
    """True when the scope this route needs depends on the request body, so the middleware has to read it."""
    e = route_for(method, path)
    return bool(e and callable(e["scope"]))


def agent_key_ok(request):
    """The X-Api-Key header's record ({label, scopes, ...}) or None. No key configured -> always None (the
    door stays owner-only until someone opts in). Only ever compares hashes -- the plaintext key is never
    stored, so a DB read (or a GET /api/settings response) can't leak it."""
    from . import settings as ST
    given = request.headers.get("x-api-key")
    if not given:
        return None
    return ST.check_agent_key(given)


def required_role(method, path):
    """'owner' / 'member' (owner or customer) / None for a request. Single source of the matrix in docs/auth.md;
    a gated page is now really redirected by the middleware, not just reported to the SPA."""
    if method in WRITE_METHODS and (path in OWNER_WRITE_PATHS or _prefixed(path, OWNER_WRITE_PREFIXES) or OWNER_WRITE_RE.match(path)):
        return "owner"
    if method in ("GET", "HEAD") and (path in OWNER_PAGES or _prefixed(path, OWNER_READ_PREFIXES)):
        return "owner"
    if method in WRITE_METHODS and _prefixed(path, MEMBER_API):
        return "member"
    if method in ("GET", "HEAD") and (path in GATED_PAGES or _prefixed(path, MEMBER_READ)):
        return "member"
    return None


def allowed(role, need):
    if need is None:
        return True
    if need == "owner":
        return role == "owner"
    return role in ("owner", "customer")


async def _read_body(receive):
    """Drain the request body here and hand back a receive() that replays it, so the route handler can
    still read it. Only used for the routes whose scope depends on the body (POST /api/crawl's mode,
    POST /api/ingest's event types) -- everything else never touches the stream."""
    chunks, more = [], True
    while more:
        msg = await receive()
        chunks.append(msg.get("body", b""))
        more = msg.get("more_body", False)
    raw = b"".join(chunks)

    async def replay():
        return {"type": "http.request", "body": raw, "more_body": False}

    try:
        body = json.loads(raw or b"{}")
    except Exception:
        body = {}                                    # not JSON: the handler will say so; here it just has no mode/type
    return (body if isinstance(body, dict) else {}), replay


class AuthMiddleware:
    """Pure ASGI: computes identity once (scope['state']['identity'] == request.state.identity), then enforces the
    matrix above. Unauthorised API -> 401 problem+json; unauthorised page -> 303 to /login?next=<path>, so a gated
    page's HTML never reaches someone who may not read it (a gate card in the SPA ships the whole page first).
    /login itself is not in GATED_PAGES, so the redirect cannot loop.

    An owner-only route can also be opened by an agent key that carries the right scope (AGENT_ROUTES); the
    key's record lands in scope['state']['agent'] so the handler knows which client wrote a row."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        ident = identity(request)
        state = scope.setdefault("state", {})
        state["identity"], state["agent"] = ident, None
        if auth_disabled():
            return await self.app(scope, receive, send)
        method, path = scope.get("method", "GET"), scope.get("path", "")
        need = required_role(method, path)
        if not allowed(ident["role"], need):
            record = agent_key_ok(request)
            if record is not None:
                body = None
                if body_scoped(method, path):
                    body, receive = await _read_body(receive)
                ok, missing = agent_allowed(method, path, body, set(record.get("scopes") or ()))
                if ok:
                    state["agent"] = record
                    return await self.app(scope, receive, send)
                if missing:
                    return await insufficient_scope(path, missing)(scope, receive, send)
            resp = (unauthorised(path, need, ident["role"]) if path.startswith("/api/")
                    else RedirectResponse(login_url(path), status_code=303))
            return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


def install(app):
    """Register the identity middleware on the FastAPI app (called once at import by app.main), plus the
    startup warning that fires while the shipped default credentials are still in place."""
    app.add_middleware(AuthMiddleware)
    app.on_event("startup")(warn_default_credentials)          # same hook style as app/main.py:_startup
    return app
