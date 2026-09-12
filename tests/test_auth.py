"""app/auth.py: shared owner login, magic link, session cookie identity, middleware matrix, AUTH_DISABLED.

The exe.dev header and tailnet doors are gone (2026-09-10). What is left: POST /api/auth/login (the shared
owner passphrase), the magic link (the only door that can mint a `customer` session), and AUTH_DISABLED=1.
"""
import json
import pathlib
import re
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import auth as AU
from app import config as A
from app import crawl as CR
from app import data as D
from app import runs as R
from app import scheduler as S
from app import schedules as SC
from app import stripe_gate as SG

OWNER = "owner@example.org"
CUSTOMER = "paying@example.org"
USER, PASS = AU.DEFAULT_LOGIN


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh SQLite, auth ON, owner list set, mail gateway stubbed. Yields the list of sent mails."""
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("OWNER_EMAILS", f"{OWNER}, Second.Owner@Example.org")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setattr(AU, "_inited_path", None)
    monkeypatch.setattr(SG, "_inited_path", None)      # closed_postings lives in stripe_gate's SCHEMA, not runs.py's
    sent = []
    monkeypatch.setattr(AU, "send_mail", lambda to, subject, body: sent.append({"to": to, "subject": subject, "body": body}) or True)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {"cities": []}, "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(R, "enqueue", lambda rid: None)
    monkeypatch.setattr(S, "start", lambda: SC.init())
    return sent


@pytest.fixture()
def client(env):
    from app.main import app
    # https base: the session cookie is Secure, and the cookie jar only replays it over https
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        yield c


def _magic_token(sent):
    m = re.search(r"/api/auth/magic/([A-Za-z0-9_-]+)", sent[-1]["body"])
    assert m, sent[-1]["body"]
    return m.group(1)


def _login(client, user=USER, password=PASS):
    r = client.post("/api/auth/login", json={"user": user, "pass": password})
    assert r.status_code == 200, r.text
    return r


# --- identity -------------------------------------------------------------------------------
def test_me_anonymous(client):
    d = client.get("/api/me").json()
    assert d["role"] == "anonymous" and d["via"] == "none" and d["email"] is None and d["auth_disabled"] is False
    assert d["login_url"] == "/login?next=/pro" and d["default_credentials"] is True


def test_exe_dev_header_and_tailnet_doors_are_gone(client, monkeypatch):
    """Both used to hand out owner. Anything that can reach the port can set the header, so it is not a login."""
    d = client.get("/api/me", headers={"X-ExeDev-Email": OWNER, "X-ExeDev-UserID": "u1"}).json()
    assert d["role"] == "anonymous" and d["via"] == "none" and d["email"] is None
    assert client.get("/api/billing", headers={"X-ExeDev-Email": OWNER}).status_code == 401
    monkeypatch.setenv("TAILNET_TRUST", "1")                     # env kept around by nothing; no code reads it
    assert not hasattr(AU, "tailnet_trusted")

    class Req:                                                    # a request straight off the tailnet range
        headers, cookies = {}, {}

        class client:
            host = "100.64.12.7"
    assert AU.identity(Req())["role"] == "anonymous"


# --- shared owner login ---------------------------------------------------------------------
def test_login_with_defaults_sets_an_owner_session(client):
    r = _login(client)
    assert r.json() == {"ok": True, "default_credentials": True}
    ck = r.headers["set-cookie"]
    assert "pj_session=" in ck and "HttpOnly" in ck and "Secure" in ck and "SameSite=lax" in ck.replace("Lax", "lax")
    d = client.get("/api/me").json()
    assert d["role"] == "owner" and d["via"] == "session" and d["email"] == USER
    assert client.get("/api/billing?window=today").status_code != 401
    assert client.post("/api/auth/logout").json() == {"ok": True}
    assert client.get("/api/me").json()["role"] == "anonymous"


@pytest.mark.parametrize("body", [{"user": USER, "pass": "wrong"}, {"user": "wrong", "pass": PASS}, {}, {"user": "", "pass": ""}])
def test_login_rejected_in_problem_json_without_a_cookie(client, body):
    r = client.post("/api/auth/login", json=body)
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("#unauthenticated") and r.json()["status"] == 401
    assert "set-cookie" not in {k.lower() for k in r.headers}
    assert client.get("/api/me").json()["role"] == "anonymous"


def test_only_hashes_are_stored_never_the_plaintext(client):
    _login(client)
    client.put("/api/auth/password", json={"user": "ivan", "pass": "s3cret-passphrase"})
    with R._lock, R.db() as c:
        stored = str([dict(r) for r in c.execute("select key, value from settings").fetchall()])
    assert "s3cret-passphrase" not in stored and "ivan" not in stored
    assert AU._sha("s3cret-passphrase") in stored


def test_password_change_clears_default_credentials_and_is_owner_only(client):
    assert client.put("/api/auth/password", json={"user": "ivan", "pass": "new-pass"}).status_code == 401
    _login(client)
    assert client.put("/api/auth/password", json={"user": "ivan", "pass": "new-pass"}).json() == {"ok": True}
    assert client.get("/api/me").json()["default_credentials"] is False
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"user": USER, "pass": PASS}).status_code == 401     # the seeded default is dead
    assert _login(client, "ivan", "new-pass").json() == {"ok": True, "default_credentials": False}
    assert client.get("/api/me").json()["default_credentials"] is False


def test_password_change_needs_both_halves(client):
    _login(client)
    assert client.put("/api/auth/password", json={"user": "ivan", "pass": ""}).status_code == 400
    assert client.put("/api/auth/password", json={"pass": "new-pass"}).status_code == 400
    assert AU.login_credentials()["is_default"] is True


def test_agent_key_cannot_change_the_password(client):
    """The agent key is a crawl-firing door, not an owner door -- it must not be able to lock the owner out."""
    _login(client)
    key = client.put("/api/settings/agent-key").json()["key"]
    client.post("/api/auth/logout")
    assert client.put("/api/auth/password", json={"user": "x", "pass": "y"}, headers={"X-Api-Key": key}).status_code == 401


def test_default_credentials_warning_at_startup(client, capsys):
    AU.warn_default_credentials()
    assert "WARNING" in capsys.readouterr().out
    _login(client)
    client.put("/api/auth/password", json={"user": "ivan", "pass": "new-pass"})
    AU.warn_default_credentials()
    assert capsys.readouterr().out == ""


# --- magic link -----------------------------------------------------------------------------
def test_magic_link_happy_path_single_use(client, env):
    r = client.post("/api/auth/magic", json={"email": OWNER.upper()})
    assert r.status_code == 200 and r.json() == {"sent": True}
    assert len(env) == 1 and env[0]["to"] == OWNER and "https://pflege-board.exe.xyz/api/auth/magic/" in env[0]["body"]
    token = _magic_token(env)
    with R._lock, R.db() as c:                                         # only the hash is stored
        row = c.execute("select token_hash, role from magic_links").fetchone()
    assert row["token_hash"] != token and row["role"] == "owner"

    r = client.get(f"/api/auth/magic/{token}")
    assert r.status_code == 303 and r.headers["location"] == "/pro"
    ck = r.headers["set-cookie"]
    assert "pj_session=" in ck and "HttpOnly" in ck and "Secure" in ck and "SameSite=lax" in ck.replace("Lax", "lax")
    d = client.get("/api/me").json()
    assert d == {**d, "role": "owner", "via": "session", "email": OWNER}
    assert client.get("/api/billing?window=today").status_code != 401  # session owner passes the gate

    assert client.get(f"/api/auth/magic/{token}").status_code == 400  # single use

    r = client.post("/api/auth/logout")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert client.get("/api/me").json()["role"] == "anonymous"


def test_magic_link_unknown_email_still_200_but_no_mail(client, env):
    r = client.post("/api/auth/magic", json={"email": "nobody@example.org"})
    assert r.status_code == 200 and r.json() == {"sent": True} and env == []
    assert client.post("/api/auth/magic", json={"email": "not-an-email"}).status_code == 400


def test_magic_link_customer_from_customers_table(client, env):
    """The customer role end to end: link -> session -> the one write a customer may do."""
    AU.upsert_customer(CUSTOMER, "cus_123")
    client.post("/api/auth/magic", json={"email": CUSTOMER})
    assert len(env) == 1
    r = client.get(f"/api/auth/magic/{_magic_token(env)}")
    assert r.status_code == 303
    d = client.get("/api/me").json()
    assert d["role"] == "customer" and d["email"] == CUSTOMER
    assert client.get("/api/billing").status_code == 401             # customer is not owner
    assert client.get("/pro").status_code != 303                     # but the gated page is theirs
    closed = client.post("/api/postings/4711/closed")                # MEMBER_API: owner or customer
    assert closed.status_code == 200, closed.text
    assert closed.json()["by_email"] == CUSTOMER and closed.json()["already"] is False
    assert client.get("/api/postings/4711/closed").json()["closed"] is True
    AU.upsert_customer(CUSTOMER, status="cancelled")                   # cancelled customers get no new links
    client.post("/api/auth/magic", json={"email": CUSTOMER})
    assert len(env) == 1
    assert client.post("/api/postings/4712/closed").status_code == 401  # and the live session dies with the subscription


def test_magic_link_expiry(client, env, monkeypatch):
    client.post("/api/auth/magic", json={"email": OWNER})
    token = _magic_token(env)
    later = datetime.now(timezone.utc) + timedelta(minutes=AU.MAGIC_TTL_MIN + 1)
    monkeypatch.setattr(AU, "_now", lambda: later)
    assert client.get(f"/api/auth/magic/{token}").status_code == 400
    assert client.get("/api/auth/magic/garbage").status_code == 400
    assert client.get("/api/auth/magic/" + "A" * 40).status_code == 400


def test_magic_link_rate_limit_per_email_per_hour(client, env, monkeypatch):
    for _ in range(3):
        assert client.post("/api/auth/magic", json={"email": OWNER}).status_code == 200
    assert client.post("/api/auth/magic", json={"email": OWNER}).status_code == 429
    assert len(env) == 3
    assert client.post("/api/auth/magic", json={"email": "other@example.org"}).status_code == 200  # per address
    later = datetime.now(timezone.utc) + timedelta(hours=1, minutes=1)
    monkeypatch.setattr(AU, "_now", lambda: later)
    assert client.post("/api/auth/magic", json={"email": OWNER}).status_code == 200


def test_session_expiry_and_tampered_cookie(client, env, monkeypatch):
    _login(client)
    assert client.get("/api/me").json()["role"] == "owner"
    value = client.cookies.get("pj_session")
    sid, sig = value.rsplit(".", 1)
    assert client.get("/api/me", headers={"Cookie": "pj_session=" + sid + ".deadbeef"}).json()["role"] == "anonymous"
    later = datetime.now(timezone.utc) + timedelta(days=AU.SESSION_DAYS + 1)
    monkeypatch.setattr(AU, "_now", lambda: later)
    assert client.get("/api/me").json()["role"] == "anonymous"


# --- middleware matrix ----------------------------------------------------------------------
DENIED = [("POST", "/api/crawl"), ("POST", "/api/schedules"), ("PUT", "/api/schedules/1"), ("DELETE", "/api/schedules/1"),
          ("PUT", "/api/settings/firecrawl"), ("PUT", "/api/settings/hunter"), ("POST", "/api/inbox/drain"), ("POST", "/api/hunter/start"),
          ("POST", "/api/hunter/stop"), ("POST", "/api/scheduler/pause"), ("POST", "/api/clinics/36201/refetch-career"),
          ("PUT", "/api/auth/password"), ("GET", "/api/billing"), ("GET", "/api/billing?window=7d"), ("GET", "/api/hunter/status"),
          ("GET", "/api/hunter/targets"), ("GET", "/api/settings"), ("GET", "/api/coverage"), ("GET", "/api/inbox"),
          # the schedule reads left the OPEN list on 2026-09-10: they published every target, mode and
          # max_credits to anonymous callers. Owner session, or an agent key carrying read:ops
          # (tests/test_agent_api.py).
          ("GET", "/api/schedules"), ("GET", "/api/schedules/presets"), ("POST", "/api/ingest"),
          # left the OPEN list on 2026-09-10: app/main.py:221 calls data.refresh() -> _build(), a full
          # Supabase re-pull with the service key, and it was anonymous.
          ("POST", "/api/refresh-cache")]
OPEN = [("GET", "/api/me"), ("GET", "/api/stats"), ("GET", "/api/clinics"), ("GET", "/api/jobs"), ("GET", "/api/search?q=x"),
        ("GET", "/api/facets"), ("GET", "/health"), ("GET", "/"), ("GET", "/login"),
        ("GET", "/api/ingest/schemas"), ("GET", "/api/agent/manifest"), ("POST", "/api/auth/magic")]


@pytest.mark.parametrize("method,path", DENIED)
def test_owner_only_denied_for_anonymous_and_customer(client, method, path):
    r = client.request(method, path, json={})
    assert r.status_code == 401 and r.json()["role"] == "anonymous"
    assert r.headers["content-type"].startswith("application/problem+json") and r.json()["detail"]
    AU.upsert_customer(CUSTOMER)
    cookie = AU.create_session(CUSTOMER, "customer")
    r = client.request(method, path, json={}, headers={"Cookie": "pj_session=" + cookie})
    assert r.status_code == 401 and r.json()["role"] == "customer"
    assert "location" not in r.headers                                 # API stays an API: 401, never a redirect


@pytest.mark.parametrize("method,path", DENIED)
def test_owner_only_passes_after_login(client, method, path):
    _login(client)
    r = client.request(method, path, json={"user": "ivan", "pass": "new-pass"})
    assert r.status_code != 401, (path, r.text)


@pytest.mark.parametrize("method,path", OPEN)
def test_open_routes_stay_open(client, method, path):
    r = client.request(method, path, json={"email": "x@y.z"} if method == "POST" else None)
    assert r.status_code != 401 and "location" not in r.headers, (path, r.text)


@pytest.mark.parametrize("path", ["/pro", "/pro/", "/autopilot", "/deck", "/deck/"])
def test_gated_pages_redirect_anonymous_to_login(client, path):
    r = client.get(path)
    assert r.status_code == 303 and r.headers["location"] == f"/login?next={path}"
    assert "<html" not in r.text.lower()                               # the HTML itself never leaves the server
    _login(client)
    r = client.get(path)
    assert r.status_code != 303 and "location" not in r.headers, r.text


@pytest.mark.parametrize("path", ["/deck", "/deck/"])
def test_deck_is_owner_only_in_all_three_directions(client, path):
    """The deck is the internal next-steps briefing. It used to sit in GATED_PAGES, which is the level a
    paying customer reaches through the magic link, so every customer could read it (proven 2026-09-10:
    GET /deck -> 200 with a customer cookie). A customer gets the same 303 as an anonymous visitor rather
    than a 403: /login is the one page-shaped answer for "not you", and it is where a session of the wrong
    role goes to become another one."""
    assert AU.required_role("GET", path) == "owner"
    r = client.get(path)                                               # anonymous
    assert r.status_code == 303 and r.headers["location"] == f"/login?next={path}"
    assert "<html" not in r.text.lower()

    AU.upsert_customer(CUSTOMER)
    cookie = AU.create_session(CUSTOMER, "customer")
    r = client.get(path, headers={"Cookie": "pj_session=" + cookie})   # customer
    assert r.status_code == 303 and r.headers["location"] == f"/login?next={path}"
    assert "<html" not in r.text.lower()
    assert client.get("/pro", headers={"Cookie": "pj_session=" + cookie}).status_code == 200   # still a member page

    _login(client)                                                     # owner
    r = client.get(path)
    assert r.status_code == 200 and "<html" in r.text.lower()


def test_cv_upload_needs_a_session_but_the_page_does_not(client):
    assert client.get("/").status_code == 200                          # the page carrying the form stays public
    r = client.post("/api/cv", json={"text": "Krankenschwester"})
    assert r.status_code == 401 and r.json()["detail"] == "sign in required"
    assert r.json()["login_url"] == "/login?next=/pro"
    AU.upsert_customer(CUSTOMER)
    cookie = AU.create_session(CUSTOMER, "customer")                   # a customer is enough: member, not owner
    assert client.post("/api/cv", json={"text": "Krankenschwester"}, headers={"Cookie": "pj_session=" + cookie}).status_code != 401


def test_required_role_matrix():
    rr = AU.required_role
    assert rr("POST", "/api/crawl") == "owner" and rr("GET", "/api/crawl/runs") == "owner" and rr("GET", "/api/firecrawl/credits") == "owner" and rr("GET", "/api/crawl/plan") == "owner"
    assert rr("POST", "/api/clinics/36201/refetch-career") == "owner" and rr("GET", "/api/clinics/36201") is None
    assert rr("GET", "/api/billing") == "owner" and rr("GET", "/api/hunter/status") == "owner" and rr("GET", "/api/inbox") == "owner"
    assert rr("GET", "/api/schedules") == "owner" and rr("GET", "/api/schedules/1/preview") == "owner"
    assert rr("POST", "/api/ingest") == "owner" and rr("GET", "/api/ingest/schemas") is None and rr("GET", "/api/agent/manifest") is None
    assert rr("PUT", "/api/auth/password") == "owner" and rr("POST", "/api/auth/login") is None and rr("POST", "/api/auth/logout") is None
    assert rr("GET", "/pro") == "member" and rr("GET", "/autopilot/") == "member"
    assert rr("GET", "/deck") == "owner" and rr("GET", "/deck/") == "owner"        # OWNER_PAGES, not GATED_PAGES
    assert rr("POST", "/api/refresh-cache") == "owner" and rr("GET", "/api/refresh-cache") is None
    assert rr("GET", "/") is None and rr("GET", "/login") is None      # /login is never gated: no redirect loop
    assert rr("POST", "/api/cv") == "member" and rr("GET", "/api/cv") is None
    assert rr("POST", "/api/postings/1/closed") == "member" and rr("GET", "/api/postings/1/closed") == "member"
    assert rr("POST", "/api/auth/magic") is None and rr("GET", "/api/auth/magic/abc") is None
    assert rr("POST", "/api/firecrawl/webhook") is None and rr("POST", "/api/stripe/webhook") is None
    assert AU.allowed("customer", "member") and not AU.allowed("customer", "owner") and not AU.allowed("anonymous", "member")


# Every route required_role() answers None for -- i.e. the "everyone" row of the matrix in docs/auth.md.
# Not written by hand: printed from the walk in test_everyone_row_matches_required_role below and pasted
# here, so a new public route (or one that quietly stops being gated) fails the suite instead of sitting
# in a doc row nobody regenerates. The row was last wrong on 2026-09-10, when it omitted GET /api/docs,
# GET /api/mechanics, GET /api/mechanics/{mid}, GET /api/flags, GET /api/stripe/status,
# POST /api/stripe/checkout and POST /api/refresh-cache (that last one is owner-only now).
PUBLIC_ROUTES = [
    ("GET", "/"), ("GET", "/login"), ("GET", "/health"), ("GET", "/dock.css"), ("GET", "/dock.js"),
    ("GET", "/docs/{name}"), ("GET", "/docs/krankenhausplan_2026.pdf"), ("GET", "/skill/{name}"),
    ("GET", "/api/docs"), ("GET", "/api/stats"), ("GET", "/api/facets"), ("GET", "/api/taxonomy"),
    ("GET", "/api/ontology"), ("GET", "/api/cities"), ("GET", "/api/plan"), ("GET", "/api/search"),
    ("GET", "/api/clinics"), ("GET", "/api/clinics/{clinic_id}"), ("GET", "/api/jobs"), ("GET", "/api/jobs/{posting_id}"),
    ("GET", "/api/mechanics"), ("GET", "/api/mechanics/{mid}"), ("GET", "/api/flags"), ("GET", "/api/me"),
    ("GET", "/api/ingest/schemas"), ("GET", "/api/agent/manifest"),
    ("POST", "/api/auth/login"), ("POST", "/api/auth/logout"), ("POST", "/api/auth/magic"),
    ("GET", "/api/auth/magic/{token}"), ("POST", "/api/auth/agent"),
    ("POST", "/api/firecrawl/webhook"),                                   # authenticates with its own shared secret
    ("GET", "/api/wa/health"),                                            # readiness booleans, no secret
    ("GET", "/api/stripe/status"), ("POST", "/api/stripe/checkout"),      # a visitor who is not a customer yet pays here
    ("POST", "/api/stripe/webhook"),                                      # authenticates with Stripe's signature
]


def test_everyone_row_matches_required_role():
    """docs/auth.md says its "everyone" row is generated from required_role(). This is the generator.

    The inventory is app.openapi(), not app.routes: FastAPI 0.141 keeps an included router as a single
    _IncludedRouter object in app.routes, so a walk over app.routes sees none of /api/me, /api/hunter/*,
    /api/stripe/* ... -- it reports a short, clean, wrong list. That blindness is why the previous guard
    test was deleted. This one cannot go blind quietly: /api/me and the stripe routes are in the expected
    list below, so an inventory that stops seeing included routers fails here."""
    from app.main import app
    got = sorted((m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops
                 if m.upper() not in ("HEAD", "OPTIONS") and AU.required_role(m.upper(), p) is None)
    assert got == sorted(PUBLIC_ROUTES), "public route set moved -- regenerate the 'everyone' row in docs/auth.md"

    doc = (pathlib.Path(__file__).resolve().parent.parent / "docs" / "auth.md").read_text(encoding="utf-8")
    row = [ln for ln in doc.splitlines() if ln.startswith("| everyone |")]
    assert len(row) == 1, "docs/auth.md has no single 'everyone' row"
    assert [f"`{m} {p}`" for m, p in PUBLIC_ROUTES if f"`{m} {p}`" not in row[0]] == []
    # and nothing gated is advertised as public
    assert [f"`{m} {p}`" for m, p in DENIED if f"`{m} {p}`" in row[0]] == []


# --- agent API key ---------------------------------------------------------------------------
def test_agent_key_generation_is_owner_only_and_shown_once(client):
    assert client.put("/api/settings/agent-key").status_code == 401           # anonymous can't mint one
    _login(client)
    r = client.put("/api/settings/agent-key")
    assert r.status_code == 200
    d = r.json()
    key = d["key"]
    assert len(key) > 20 and d["configured"] is True and d["created_at"]
    settings = client.get("/api/settings").json()
    assert "agent_key" in settings and settings["agent_key"] == {**settings["agent_key"], "configured": True}
    assert key not in str(settings) and AU.hashlib.sha256(key.encode()).hexdigest() not in str(settings)


@pytest.mark.parametrize("method,path", [("POST", "/api/crawl"), ("POST", "/api/inbox/drain"), ("POST", "/api/clinics/36201/refetch-career")])
def test_agent_key_unlocks_only_the_crawl_subset(client, method, path):
    _login(client)
    key = client.put("/api/settings/agent-key").json()["key"]
    client.post("/api/auth/logout")
    assert client.request(method, path, json={}).status_code == 401                       # no key: still gated
    assert client.request(method, path, json={}, headers={"X-Api-Key": "wrong"}).status_code == 401
    r = client.request(method, path, json={}, headers={"X-Api-Key": key})
    assert r.status_code != 401, (path, r.text)                                            # anonymous + right key: in


@pytest.mark.parametrize("method,path", [("PUT", "/api/settings/firecrawl"), ("POST", "/api/hunter/start"), ("POST", "/api/schedules"), ("POST", "/api/campaign")])
def test_agent_key_does_not_unlock_settings_hunter_scheduler_campaign(client, method, path):
    _login(client)
    key = client.put("/api/settings/agent-key").json()["key"]
    client.post("/api/auth/logout")
    assert client.request(method, path, json={}, headers={"X-Api-Key": key}).status_code == 401


def test_agent_key_rotate_invalidates_previous_key(client):
    _login(client)
    old = client.put("/api/settings/agent-key").json()["key"]
    new = client.put("/api/settings/agent-key?rotate=true").json()["key"]
    client.post("/api/auth/logout")
    assert old != new
    assert client.post("/api/inbox/drain", headers={"X-Api-Key": old}).status_code == 401
    assert client.post("/api/inbox/drain", headers={"X-Api-Key": new}).status_code != 401


def test_agent_key_delete_locks_the_door_again(client):
    _login(client)
    key = client.put("/api/settings/agent-key").json()["key"]
    assert client.delete("/api/settings/agent-key").json()["configured"] is False
    client.post("/api/auth/logout")
    assert client.post("/api/inbox/drain", headers={"X-Api-Key": key}).status_code == 401


def test_no_agent_key_configured_means_subset_stays_owner_only(client):
    assert client.post("/api/inbox/drain", headers={"X-Api-Key": "anything"}).status_code == 401


def test_gate_password_door_opens_skill_doc_not_the_dashboard(client):
    """POST /api/auth/agent -- the gate's key field for a headless agent. Right key -> points at the public
    skill doc, never a session; wrong/missing key -> 401, no cookie set."""
    _login(client)
    key = client.put("/api/settings/agent-key").json()["key"]
    client.post("/api/auth/logout")
    r = client.post("/api/auth/agent", json={"key": key})
    assert r.status_code == 200 and r.json() == {"ok": True, "skill_url": "/skill/SKILL.md"}
    assert "set-cookie" not in {k.lower() for k in r.headers}
    assert client.get("/api/me").json()["role"] == "anonymous"          # did not log the caller in
    assert client.post("/api/auth/agent", json={"key": "wrong"}).status_code == 401
    assert client.post("/api/auth/agent", json={}).status_code == 401


# --- AUTH_DISABLED --------------------------------------------------------------------------
def test_auth_disabled_makes_everyone_owner(client, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "1")
    d = client.get("/api/me").json()
    assert d["role"] == "owner" and d["via"] == "disabled" and d["auth_disabled"] is True
    assert client.get("/api/coverage").status_code != 401
    assert client.post("/api/hunter/stop").status_code != 401
    assert client.get("/deck").status_code != 303                       # pages are not redirected either
    assert client.post("/api/cv", json={"text": "x"}).status_code != 401
    monkeypatch.setenv("AUTH_DISABLED", "0")
    assert client.get("/api/coverage").status_code == 401


# --- security pass 2026-09-10 ----------------------------------------------------------------
def _session_cookie(email, role):
    """A ready-made session cookie, no login round-trip. A customer needs a customers row: session_from_cookie
    re-checks it on every request (a cancelled subscription revokes access at once)."""
    if role == "customer":
        AU.upsert_customer(email)
    return {"Cookie": AU.COOKIE + "=" + AU.create_session(email, role)}


def test_non_ascii_cookie_is_rejected_not_a_500(client):
    """One header used to 500 every route in the app, anonymously: Starlette hands the cookie over
    latin-1-decoded and hmac.compare_digest's str overload raises TypeError on a non-ASCII character, inside
    the middleware, before any route ran. Same bug that was fixed once in app/firecrawl_hooks.py:96."""
    bad = {"Cookie": b"pj_session=abc.\xff"}          # bytes: httpx refuses to encode a non-ASCII header str
    for path in ("/", "/login", "/health", "/api/me", "/api/jobs", "/api/stats"):
        r = client.get(path, headers=bad)
        assert r.status_code < 500, (path, r.status_code, r.text[:200])
    assert client.get("/api/me", headers=bad).json()["role"] == "anonymous"      # bad signature -> no session
    assert AU._parse_cookie("abc.\xff") is None
    good = AU._cookie_value("sid-1")
    assert AU._parse_cookie(good) == "sid-1"                                     # a real cookie still verifies


JOB_WITH_EMAIL = {"posting_id": 7, "title": "Pflegefachkraft", "clinic_id": "36201", "city": "Regensburg", "fresh": True,
                  "first_published": "2026-09-05", "status": "open", "enr_contact_emails": ["stefan.wurzer@kno.ag"],
                  "enr_housing": False, "clinic_town": "Regensburg", "clinic_size": "XL"}
CLINIC_ROW = {"clinic_id": "36201", "name": "Barmherzige Brüder", "town": "Regensburg", "jobs_open": 1, "jobs_fresh": 1,
              "jobs_live": 1, "routable": True, "walled": False, "beds": 985, "size": "XL", "fachrichtungen": [],
              "board": None, "vendor": None, "route_reason": "adapter", "fetch": "adapter", "fetch_label": "typo3_jobs",
              "ats_type": "typo3_jobs", "last_crawl_at": None, "last_crawl_status": None, "last_crawl_mode": None,
              "career_profile": None}


@pytest.fixture()
def board(client, monkeypatch):
    """One open posting carrying a recruiter e-mail, plus the clinic it belongs to."""
    D._snap.update({"jobs": [dict(JOB_WITH_EMAIL)], "clinics": [dict(CLINIC_ROW)], "by_clinic": {"36201": dict(CLINIC_ROW)}})
    monkeypatch.setattr(D, "job_detail", lambda pid: dict(JOB_WITH_EMAIL, description="…", observations=[]))
    return client


def test_recruiter_emails_are_member_only(board):
    """enr_contact_emails is personal data and GET /api/jobs is public: until 2026-09-10 one anonymous
    limit=2000 call returned 431 addresses off the live board. Null below member, the real value for a
    paying customer and for the owner -- on the list, on the detail row and on the clinic page."""
    def emails(headers=None):
        return (board.get("/api/jobs", headers=headers or {}).json()["rows"][0]["enr_contact_emails"],
                board.get("/api/jobs/7", headers=headers or {}).json()["enr_contact_emails"],
                board.get("/api/clinics/36201", headers=headers or {}).json()["jobs"][0]["enr_contact_emails"])

    assert emails() == (None, None, None)                                        # anonymous
    assert emails(_session_cookie(CUSTOMER, "customer")) == (["stefan.wurzer@kno.ag"],) * 3
    assert emails(_session_cookie(OWNER, "owner")) == (["stefan.wurzer@kno.ag"],) * 3
    # asking for the field by name does not route around the redaction, and it is still a known field
    r = board.get("/api/jobs?fields=posting_id,enr_contact_emails")
    assert r.status_code == 200 and r.json()["rows"] == [{"posting_id": 7, "enr_contact_emails": None}]
    # the snapshot itself is untouched -- redaction copies, it must not blank the cache for the owner
    assert D._snap["jobs"][0]["enr_contact_emails"] == ["stefan.wurzer@kno.ag"]
    # nothing else in the row is redacted
    assert board.get("/api/jobs").json()["rows"][0]["title"] == "Pflegefachkraft"


def test_firecrawl_budget_is_member_only(board, monkeypatch):
    """GET /api/stats is public. The Firecrawl balance is the operator's paid budget, no public page reads
    it (index.template.html uses clinics/open_jobs/fresh_jobs), and /pro's credits pill is a member page."""
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda **kw: {"remaining": 381, "plan": 8000, "tokens_remaining": 1})
    assert board.get("/api/stats").json()["firecrawl"] is None
    assert board.get("/api/stats").json()["open_jobs"] == 1                       # the board itself stays public
    assert board.get("/api/stats", headers=_session_cookie(CUSTOMER, "customer")).json()["firecrawl"] == {"remaining": 381, "plan": 8000}
    owner = board.get("/api/stats", headers=_session_cookie(OWNER, "owner")).json()["firecrawl"]
    assert owner["remaining"] == 381 and owner["used_period"] == 7619 and "spent_by_app" in owner


def _agent_key(client):
    _login(client)
    key = client.put("/api/settings/agent-key").json()["key"]
    client.post("/api/auth/logout")
    return {"X-Api-Key": key}


def test_validate_only_in_the_query_string_is_a_400_not_a_write(client, monkeypatch):
    """validate_only is a body field. Passing it as a query parameter used to be silently ignored: the write
    happened and the answer said validate_only false -- that is how probe row inbox_id 14043 reached the
    production inbox on 2026-09-09."""
    posted = []
    monkeypatch.setattr(CR, "_post_inbox", lambda rows, log=None: posted.extend(rows) or [r["source_url"] for r in rows])
    h = _agent_key(client)
    ev = {"id": "e1", "source": "probe", "type": "probe.ats_discovery", "data": {"source_url": "https://x.example/jobs"}}
    r = client.post("/api/ingest?validate_only=true", json=ev, headers=h)
    assert r.status_code == 400 and "validate_only is a body field" in r.json()["detail"]
    assert posted == []                                                          # and nothing was written
    r = client.post("/api/crawl?validate_only=1", json={"scope": "clinic", "values": "36201"}, headers=h)
    assert r.status_code == 400 and "validate_only is a body field" in r.json()["detail"]
    # the body field itself still works
    r = client.post("/api/ingest", json={**ev, "validate_only": True}, headers=h)
    assert r.status_code == 202 and r.json()["validate_only"] is True and posted == []


def test_malformed_ingest_body_is_a_4xx_not_a_middleware_500(client):
    """{"events": {...}} used to make auth.ingest_scopes iterate the dict's keys and call .get on a str --
    an AttributeError inside the middleware, i.e. a 500 echoing the exception to a caller who merely sent
    the wrong shape."""
    h = _agent_key(client)
    r = client.post("/api/ingest", json={"events": {"type": "posting.observed"}}, headers=h)
    assert r.status_code == 400, r.text
    assert '"events" must be a list' in r.json()["detail"] and "dict" in r.json()["detail"]
    assert AU.ingest_scopes({"events": {"type": "posting.observed"}}) == ["write:ingest:posting"]
    assert AU.ingest_scopes({"events": ["nonsense", None, {"type": "clinic.upserted"}]}) == ["write:ingest:clinic"]
    assert AU.ingest_scopes({"events": 5}) == ["write:ingest:posting"]            # not iterable at all
    assert client.post("/api/ingest", json={"events": 5}, headers=h).status_code == 400
    r = client.post("/api/ingest", json={"events": ["nonsense"]}, headers=h)
    assert r.status_code == 207 and r.json()["results"][0]["status"] == "rejected"


def test_recruiter_emails_are_redacted_from_the_ad_body_too(board, monkeypatch):
    """Nulling enr_contact_emails redacted nothing on its own: GET /api/jobs/{id} returns the `postings`
    row, ad body included, and 569 of the 572 postings that carry an address in enr_contact_emails carry the
    same address in the description text. Below member every free-text value in the row is scrubbed."""
    body = ("Ihre Bewerbung richten Sie an stefan.wurzer@kno.ag oder an bewerbung@kno.ag.\n"
            "Rückfragen: Frau Wurzer, 0941 369-0.")
    monkeypatch.setattr(D, "job_detail", lambda pid: dict(
        JOB_WITH_EMAIL, description=body, enr_requirements="Kontakt: p.mueller@klinik-x.de",
        observations=[{"source_code": "employer_ats", "source_url": "https://kno.ag/j/7?ref=stefan.wurzer@kno.ag"}],
        provenance={"title": "employer_ats", "note": "geliefert von stefan.wurzer@kno.ag"}))

    anon = board.get("/api/jobs/7").json()
    assert "stefan.wurzer@kno.ag" not in json.dumps(anon), anon["description"]
    assert "bewerbung@kno.ag" not in anon["description"] and "p.mueller@klinik-x.de" not in anon["enr_requirements"]
    assert D.EMAIL_MASK in anon["description"] and anon["description"].count(D.EMAIL_MASK) == 2
    assert D.EMAIL_MASK in anon["observations"][0]["source_url"]          # nested list of dicts
    assert D.EMAIL_MASK in anon["provenance"]["note"]                     # nested dict
    assert "Frau Wurzer" in anon["description"] and "0941 369-0" in anon["description"]   # named ceiling: not caught
    assert "Ihre Bewerbung richten Sie an" in anon["description"]         # the rest of the ad is untouched

    for who in (CUSTOMER, "owner"):
        role = "customer" if who == CUSTOMER else "owner"
        full = board.get("/api/jobs/7", headers=_session_cookie(OWNER if role == "owner" else CUSTOMER, role)).json()
        assert full["description"] == body, role                          # a member pays for the real thing
        assert full["enr_contact_emails"] == ["stefan.wurzer@kno.ag"], role
    # the shared snapshot is untouched -- the scrub copies, like the field redaction does
    assert D._snap["jobs"][0]["enr_contact_emails"] == ["stefan.wurzer@kno.ag"]


def test_email_scrub_leaves_ordinary_text_alone():
    """Pure check on the substitution, no HTTP: it must not eat @-shaped text that is not an address."""
    keep = "Schichtzulage 15 @ Nacht, siehe @Team-Handbuch und user@ (unvollständig)"
    assert D._scrub_emails(keep) == keep
    assert D._scrub_emails("a@b.de") == D.EMAIL_MASK
    assert D._scrub_emails(["x", "a.b+c@sub.klinik-x.co.uk"]) == ["x", D.EMAIL_MASK]
    assert D._scrub_emails({"k": None, "n": 5, "b": True}) == {"k": None, "n": 5, "b": True}
    # named ceiling in app/data.py: obfuscated spellings are NOT caught, and the comment says so
    assert D._scrub_emails("name (at) klinik.de") == "name (at) klinik.de"


# --- POST /api/auth/login: throttled per source IP, not the reverted global bucket ------------
def test_login_throttle_is_per_ip_not_the_reverted_global_bucket(env):
    """A LOGIN_PER_HOUR=10 global failure bucket shipped on 2026-09-10 and was reverted on 2026-09-11:
    one shared credential behind one bucket for everyone meant ten wrong guesses from any anonymous caller
    shut the owner out of the only deployed sign-in door for an hour -- a free denial of service, strictly
    worse than the guessing it was meant to slow (docs/auth.md, "Rate limits").

    Shipped 2026-09-11: the same LOGIN_FAIL_PER_HOUR limit, keyed on request.client.host instead of one
    global counter. Port 8501 is directly internet-reachable (confirmed against live access-log source IPs,
    not fronted by a proxy), so the raw TCP peer is already the real caller and needs no forwarded header.
    An attacker at one address can no longer cost the owner, at a different address, a single attempt."""
    from app.main import app
    with TestClient(app, base_url="https://testserver", follow_redirects=False, client=("203.0.113.5", 51000)) as attacker:
        for i in range(AU.LOGIN_FAIL_PER_HOUR):
            assert attacker.post("/api/auth/login", json={"user": USER, "pass": "wrong"}).status_code == 401, i
        assert attacker.post("/api/auth/login", json={"user": USER, "pass": "wrong"}).status_code == 429
        r = attacker.post("/api/auth/login", json={"user": USER, "pass": PASS})   # even the right pair, same IP
        assert r.status_code == 429 and r.json() == {"error": "too many wrong attempts from this address; try again later"}
    with TestClient(app, base_url="https://testserver", follow_redirects=False, client=("198.51.100.9", 51000)) as owner:
        assert owner.post("/api/auth/login", json={"user": USER, "pass": PASS}).json() == {"ok": True, "default_credentials": True}
        assert owner.get("/api/me").json()["role"] == "owner"
    assert R.get_setting("login_failures") is None          # the bucket lives in the login_failures table, not settings


# --- malformed bodies at the unauthenticated front door ---------------------------------------
# `await request.json()` raises on a body that is not JSON, and the `body.get(...)` that always follows
# raises on a JSON scalar or list. Every one of these was a 500 with the Python exception echoed back on
# 2026-09-11; the first two doors answer to anonymous callers.
MALFORMED_BODIES = [("not JSON", b"notjson"), ("a JSON string", b'"hi"'), ("a JSON number", b"5"),
                    ("null", b"null"), ("a list", b"[1,2]"), ("a nested list", b'[{"user":"x"}]')]
BODY_DOORS = [("POST", "/api/auth/login", "anonymous"), ("POST", "/api/auth/agent", "anonymous"),
              ("PUT", "/api/auth/password", "owner")]


@pytest.mark.parametrize("method,path,who", BODY_DOORS, ids=[f"{m} {p}" for m, p, _ in BODY_DOORS])
@pytest.mark.parametrize("name,payload", MALFORMED_BODIES, ids=[c[0] for c in MALFORMED_BODIES])
def test_malformed_body_on_an_auth_door_is_400_not_500(client, method, path, who, name, payload):
    if who == "owner":
        _login(client)
    r = client.request(method, path, content=payload, headers={"content-type": "application/json"})
    assert r.status_code == 400, (path, name, r.status_code, r.text[:200])
    assert r.headers["content-type"].startswith("application/problem+json"), (path, name)
    assert r.json()["detail"].startswith("body must be"), (path, name, r.json())


def test_an_empty_body_is_an_empty_object_not_a_400(client):
    """`{}` is what several routes already documented ("Body may carry {reason}"), and every field they read
    is optional -- so an empty body reaches the handler and gets that handler's own answer, not a parse 400."""
    assert client.post("/api/auth/login", content=b"").status_code == 401          # no user/pass -> wrong pair
    assert client.post("/api/auth/agent", content=b"").status_code == 401          # no key -> invalid key


# --- GET /api/clinics/{clinic_id}: crawl-run bookkeeping is owner-only -------------------------
def test_clinic_detail_publishes_runs_to_the_owner_only(client, env):
    """GET /api/crawl/runs is owner-only (auth.OWNER_READ_PREFIXES), but this route built out["runs"] from
    the same R.list_runs(200) and never redacted it -- so per-run Firecrawl credits_used, the internal error
    string and the last three log lines reached anonymous callers. All three roles asserted here."""
    D._snap.update({"clinics": [{"clinic_id": "36201", "name": "BB", "town": "Regensburg", "last_crawl_at": None}],
                    "by_clinic": {"36201": {"clinic_id": "36201", "name": "BB", "town": "Regensburg", "last_crawl_at": None}}})
    rid = R.create_run("clinic", "36201", "firecrawl", {}, ["36201"])
    R.update_run(rid, status="failed", credits_used=137, error="SupabaseError: service key rejected for table inbox")
    R.log(rid, "firecrawl job 9f2 failed: 402 payment required")

    assert AU.required_role("GET", "/api/crawl/runs") == "owner"                   # the gate being mirrored
    for role, headers in (("anonymous", {}), ("customer", _session_cookie(CUSTOMER, "customer")),
                          ("owner", _session_cookie(OWNER, "owner"))):
        body = client.get("/api/clinics/36201", headers=headers)
        assert body.status_code == 200, (role, body.text[:200])
        d = body.json()
        assert "runs" in d, role                                                   # the key stays in the shape
        if role == "owner":
            assert [r["run_id"] for r in d["runs"]] == [rid]
            assert d["runs"][0]["credits_used"] == 137 and d["runs"][0]["error"].startswith("SupabaseError")
            assert d["runs"][0]["log_tail"]
        else:
            assert d["runs"] == [], role
            assert "137" not in body.text and "SupabaseError" not in body.text and "402 payment" not in body.text, role
            assert "last_crawl_at" in d, role                                      # what a public page needs


# --- POST /api/ingest: malformed nested types (the middleware fix only covered the outer shape) ---
INGEST_EVENT = {"type": "posting.observed", "id": "a", "source": "s"}
MALFORMED_INGEST = [
    ("body is a list", [1, 2, 3], 400),
    ("body is a string", "hello", 400),
    ("body is null", None, 400),
    ('"events" is a string', {"events": "x"}, 400),
    ('"events" is null', {"events": None}, 400),
    ("an event is a string", {"events": ["x"]}, 207),
    ("an event is null", {"events": [None]}, 207),
    ("data is a string", {"events": [{**INGEST_EVENT, "data": "str"}]}, 207),
    ("data is null", {"events": [{**INGEST_EVENT, "data": None}]}, 207),
    ("data.payload is a string", {"events": [{**INGEST_EVENT, "data": {"source_url": "http://x/y", "payload": "str"}}]}, 207),
    ("data.payload is a list", {"events": [{**INGEST_EVENT, "data": {"source_url": "http://x/y", "payload": [1]}}]}, 207),
    ("data.source_url is a number", {"events": [{**INGEST_EVENT, "data": {"source_url": 5}}]}, 207),
    ("data.source_url is a list", {"events": [{**INGEST_EVENT, "data": {"source_url": ["http://x/y"]}}]}, 207),
    ("data.source_host is an object", {"events": [{**INGEST_EVENT, "data": {"source_url": "http://x/y", "source_host": {}}}]}, 207),
    ("data is missing", {"events": [dict(INGEST_EVENT)]}, 207),
    ("id is an object", {"events": [{**INGEST_EVENT, "id": {"a": 1}, "data": {"source_url": "http://x/y"}}]}, 207),
    # `typ not in INBOX_KIND` is a dict lookup: an unhashable envelope type raised TypeError out of
    # _validate_event and took the WHOLE batch down with a 500 instead of rejecting the one item.
    ("type is a list", {"events": [{**INGEST_EVENT, "type": [1, 2]}]}, 207),
    ("type is an object", {"events": [{**INGEST_EVENT, "type": {"a": 1}}]}, 207),
    ("type is a number", {"events": [{**INGEST_EVENT, "type": 5}]}, 207),
    ("clinic.upserted data is a string", {"events": [{**INGEST_EVENT, "type": "clinic.upserted", "data": "x"}]}, 207),
    ("crawl_run.finished data is a list", {"events": [{**INGEST_EVENT, "type": "crawl_run.finished", "data": [1]}]}, 207),
]


@pytest.mark.parametrize("name,body,status", MALFORMED_INGEST, ids=[c[0] for c in MALFORMED_INGEST])
def test_malformed_ingest_shapes_are_4xx_naming_the_field_not_500(client, monkeypatch, name, body, status):
    """`{"events":[{"type":"posting.observed","id":"a","source":"s","data":{"source_url":"http://x/y",
    "payload":"str"}}]}` with a valid agent key still answered 500 after the middleware fix: the middleware
    only ever looked at the outer shape, and dict("str") inside the handler is a ValueError. Every shape is
    now checked against the schema GET /api/ingest/schemas publishes, at the handler."""
    posted = []
    monkeypatch.setattr(CR, "_post_inbox", lambda rows, log=None: posted.extend(rows) or [r["source_url"] for r in rows])
    h = _agent_key(client)
    r = client.post("/api/ingest", content=json.dumps(body), headers={**h, "content-type": "application/json"})
    assert r.status_code == status, (name, r.status_code, r.text[:300])
    assert posted == [], name                                    # nothing malformed reached the inbox
    if status == 207:
        problem = r.json()["results"][0]["problem"]
        assert problem["status"] in (400, 422), (name, problem)
        assert problem["detail"], name
    else:
        assert r.headers["content-type"].startswith("application/problem+json"), name


def test_one_malformed_event_does_not_kill_the_batch(client, monkeypatch):
    """The contract promises a per-item answer in the 207. An unhashable `type` raised TypeError out of
    _validate_event instead, so a single bad envelope turned the whole batch into one 500 and the good
    events beside it were never written."""
    posted = []
    monkeypatch.setattr(CR, "_post_inbox", lambda rows, log=None: posted.extend(rows) or [r["source_url"] for r in rows])
    h = _agent_key(client)
    r = client.post("/api/ingest", json={"events": [
        {**INGEST_EVENT, "type": [1]},
        {**INGEST_EVENT, "id": "good", "data": {"source_url": "http://x/y"}},
        {**INGEST_EVENT, "id": "c", "type": {"k": 1}}]}, headers=h)
    assert r.status_code == 207, r.text[:300]
    d = r.json()
    assert [x["status"] for x in d["results"]] == ["rejected", "accepted", "rejected"], d
    assert d["accepted"] == 1 and d["total"] == 3
    assert d["results"][0]["problem"]["detail"].startswith("envelope.type must be string, got list")
    assert d["results"][2]["problem"]["detail"].startswith("envelope.type must be string, got dict")
    assert [x["source_url"] for x in posted] == ["http://x/y"]      # the good event still landed


def test_ingest_schema_rejection_names_the_offending_field(client, monkeypatch):
    monkeypatch.setattr(CR, "_post_inbox", lambda rows, log=None: [r["source_url"] for r in rows])
    h = _agent_key(client)

    def detail(data, **env):
        body = {**INGEST_EVENT, "data": data, **env}
        return client.post("/api/ingest", json=body, headers=h).json()["results"][0]["problem"]["detail"]

    assert detail({"source_url": "http://x/y", "payload": "str"}).startswith("data.payload must be object")
    assert detail({"source_url": 5}).startswith("data.source_url must be string, got int")
    assert detail({"source_url": "http://x/y"}, id={"a": 1}).startswith("envelope.id must be string, got dict")
    assert "GET /api/ingest/schemas" in detail({"source_url": "http://x/y", "payload": [1]})
    # the well-formed event still lands
    ok = client.post("/api/ingest", json={**INGEST_EVENT, "data": {"source_url": "http://x/y", "payload": {"title": "t"}}}, headers=h)
    assert ok.status_code == 202, ok.text


def test_ingest_type_checks_edge_op_columns_against_the_published_spec(client):
    """clinic.upserted publishes every column with a JSON type; a wrong one is a 422 naming the column,
    not a row of the wrong type pushed at the edge function."""
    from pflege_jobs.schema import CLINIC_SPEC
    h = _agent_key(client)
    full = {c: None for c, _ in CLINIC_SPEC}
    full.update(clinic_id="36201", name="BB")
    body = {"id": "c1", "source": "s", "type": "clinic.upserted", "data": {**full, "beds": "985"}}
    r = client.post("/api/ingest", json=body, headers=h)
    assert r.status_code == 207
    assert r.json()["results"][0]["problem"]["detail"].startswith("data.beds must be integer or null, got str")
