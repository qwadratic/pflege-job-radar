"""app/auth.py: identity (exe.dev header, tailnet flag, session cookie), magic link, middleware matrix, AUTH_DISABLED."""
import re
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import auth as AU
from app import config as A
from app import data as D
from app import runs as R
from app import scheduler as S
from app import schedules as SC

OWNER = "owner@example.org"
CUSTOMER = "paying@example.org"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh SQLite, auth ON, owner list set, mail gateway stubbed. Yields the list of sent mails."""
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("OWNER_EMAILS", f"{OWNER}, Second.Owner@Example.org")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.delenv("TAILNET_TRUST", raising=False)
    monkeypatch.setattr(AU, "_inited_path", None)
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


# --- identity -------------------------------------------------------------------------------
def test_me_anonymous_without_headers(client):
    d = client.get("/api/me").json()
    assert d["role"] == "anonymous" and d["via"] == "none" and d["email"] is None and d["auth_disabled"] is False
    assert d["login_url"].startswith("/__exe.dev/login")
    assert "x-exedev-email" not in {k.lower() for k in d}


def test_me_owner_via_exe_header_case_insensitive(client):
    d = client.get("/api/me", headers={"X-ExeDev-Email": "SECOND.owner@example.org", "X-ExeDev-UserID": "u1"}).json()
    assert d == {**d, "role": "owner", "via": "exe", "email": "second.owner@example.org"}
    assert "X-ExeDev-UserID" not in str(d) and "u1" not in str(d)


def test_me_exe_logged_in_but_not_owner_is_anonymous(client):
    d = client.get("/api/me", headers={"X-ExeDev-Email": "someone@else.org"}).json()
    assert d["role"] == "anonymous" and d["via"] == "exe" and d["email"] == "someone@else.org"


def test_tailnet_only_when_trusted(env, monkeypatch):
    class Req:
        headers = {}
        cookies = {}

        class client:
            host = "100.64.12.7"

        class state:
            pass
    assert AU.identity(Req())["role"] == "anonymous"                 # TAILNET_TRUST unset
    monkeypatch.setenv("TAILNET_TRUST", "1")
    assert AU.identity(Req()) == {"role": "owner", "email": None, "via": "tailnet"}
    Req.client.host = "10.0.0.5"                                       # outside 100.64.0.0/10
    assert AU.identity(Req())["role"] == "anonymous"
    Req.client.host = "testclient"                                     # not an IP at all
    assert AU.identity(Req())["role"] == "anonymous"


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
    AU.upsert_customer(CUSTOMER, "cus_123")
    client.post("/api/auth/magic", json={"email": CUSTOMER})
    assert len(env) == 1
    r = client.get(f"/api/auth/magic/{_magic_token(env)}")
    assert r.status_code == 303
    d = client.get("/api/me").json()
    assert d["role"] == "customer" and d["email"] == CUSTOMER
    assert client.get("/api/billing").status_code == 401             # customer is not owner
    AU.upsert_customer(CUSTOMER, status="cancelled")                   # cancelled customers get no new links
    client.post("/api/auth/magic", json={"email": CUSTOMER})
    assert len(env) == 1


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
    client.post("/api/auth/magic", json={"email": OWNER})
    client.get(f"/api/auth/magic/{_magic_token(env)}")
    assert client.get("/api/me").json()["role"] == "owner"
    value = client.cookies.get("pj_session")
    sid, sig = value.rsplit(".", 1)
    assert client.get("/api/me", headers={"Cookie": "pj_session=" + sid + ".deadbeef"}).json()["role"] == "anonymous"
    later = datetime.now(timezone.utc) + timedelta(days=AU.SESSION_DAYS + 1)
    monkeypatch.setattr(AU, "_now", lambda: later)
    assert client.get("/api/me").json()["role"] == "anonymous"


# --- middleware matrix ----------------------------------------------------------------------
OWNER_H = {"X-ExeDev-Email": OWNER}
DENIED = [("POST", "/api/crawl"), ("POST", "/api/schedules"), ("PUT", "/api/schedules/1"), ("DELETE", "/api/schedules/1"),
          ("PUT", "/api/settings/firecrawl"), ("PUT", "/api/settings/hunter"), ("POST", "/api/inbox/drain"), ("POST", "/api/hunter/start"),
          ("POST", "/api/hunter/stop"), ("POST", "/api/scheduler/pause"), ("POST", "/api/clinics/36201/refetch-career"),
          ("GET", "/api/billing"), ("GET", "/api/billing?window=7d"), ("GET", "/api/hunter/status"), ("GET", "/api/hunter/targets"),
          ("GET", "/api/settings"), ("GET", "/api/coverage"), ("GET", "/api/inbox")]
OPEN = [("GET", "/api/me"), ("GET", "/api/stats"), ("GET", "/api/clinics"), ("GET", "/api/jobs"), ("GET", "/api/search?q=x"),
        ("GET", "/api/schedules"), ("GET", "/api/facets"), ("GET", "/health"), ("GET", "/"),
        ("GET", "/pro"), ("GET", "/autopilot"), ("POST", "/api/auth/magic")]


@pytest.mark.parametrize("method,path", DENIED)
def test_owner_only_denied_for_anonymous_and_customer(client, method, path):
    r = client.request(method, path)
    assert r.status_code == 401 and r.json()["error"] and r.json()["role"] == "anonymous"
    AU.upsert_customer(CUSTOMER)
    cookie = AU.create_session(CUSTOMER, "customer")
    r = client.request(method, path, headers={"Cookie": "pj_session=" + cookie})
    assert r.status_code == 401 and r.json()["role"] == "customer"
    assert "location" not in r.headers                                 # no automatic bounce to exe.dev login


@pytest.mark.parametrize("method,path", DENIED)
def test_owner_only_passes_for_owner(client, method, path):
    r = client.request(method, path, headers=OWNER_H, json={})
    assert r.status_code != 401, (path, r.text)


@pytest.mark.parametrize("method,path", OPEN)
def test_open_routes_stay_open(client, method, path):
    r = client.request(method, path, json={"email": "x@y.z"} if method == "POST" else None)
    assert r.status_code != 401, (path, r.text)


def test_pages_served_to_anonymous_not_redirected(client):
    for p in ("/pro", "/pro/", "/autopilot"):
        r = client.get(p)
        assert r.status_code in (200, 503) and "location" not in r.headers


def test_required_role_matrix():
    rr = AU.required_role
    assert rr("POST", "/api/crawl") == "owner" and rr("GET", "/api/crawl/runs") == "owner" and rr("GET", "/api/firecrawl/credits") == "owner" and rr("GET", "/api/crawl/plan") == "owner"
    assert rr("POST", "/api/clinics/36201/refetch-career") == "owner" and rr("GET", "/api/clinics/36201") is None
    assert rr("GET", "/api/billing") == "owner" and rr("GET", "/api/hunter/status") == "owner" and rr("GET", "/api/inbox") == "owner"
    assert rr("GET", "/pro") == "member" and rr("GET", "/autopilot/") == "member" and rr("GET", "/") is None
    assert rr("POST", "/api/firecrawl/webhook") is None and rr("POST", "/api/stripe/webhook") is None and rr("POST", "/api/cv") is None
    assert AU.allowed("customer", "member") and not AU.allowed("customer", "owner") and not AU.allowed("anonymous", "member")


# --- agent API key ---------------------------------------------------------------------------
def test_agent_key_generation_is_owner_only_and_shown_once(client):
    assert client.put("/api/settings/agent-key").status_code == 401           # anonymous can't mint one
    r = client.put("/api/settings/agent-key", headers=OWNER_H)
    assert r.status_code == 200
    d = r.json()
    key = d["key"]
    assert len(key) > 20 and d["configured"] is True and d["created_at"]
    settings = client.get("/api/settings", headers=OWNER_H).json()
    assert "agent_key" in settings and settings["agent_key"] == {**settings["agent_key"], "configured": True}
    assert key not in str(settings) and AU.hashlib.sha256(key.encode()).hexdigest() not in str(settings)


@pytest.mark.parametrize("method,path", [("POST", "/api/crawl"), ("POST", "/api/inbox/drain"), ("POST", "/api/clinics/36201/refetch-career")])
def test_agent_key_unlocks_only_the_crawl_subset(client, method, path):
    key = client.put("/api/settings/agent-key", headers=OWNER_H).json()["key"]
    assert client.request(method, path, json={}).status_code == 401                       # no key: still gated
    assert client.request(method, path, json={}, headers={"X-Api-Key": "wrong"}).status_code == 401
    r = client.request(method, path, json={}, headers={"X-Api-Key": key})
    assert r.status_code != 401, (path, r.text)                                            # anonymous + right key: in


@pytest.mark.parametrize("method,path", [("PUT", "/api/settings/firecrawl"), ("POST", "/api/hunter/start"), ("POST", "/api/schedules"), ("POST", "/api/campaign")])
def test_agent_key_does_not_unlock_settings_hunter_scheduler_campaign(client, method, path):
    key = client.put("/api/settings/agent-key", headers=OWNER_H).json()["key"]
    assert client.request(method, path, json={}, headers={"X-Api-Key": key}).status_code == 401


def test_agent_key_rotate_invalidates_previous_key(client):
    old = client.put("/api/settings/agent-key", headers=OWNER_H).json()["key"]
    new = client.put("/api/settings/agent-key?rotate=true", headers=OWNER_H).json()["key"]
    assert old != new
    assert client.post("/api/inbox/drain", headers={"X-Api-Key": old}).status_code == 401
    assert client.post("/api/inbox/drain", headers={"X-Api-Key": new}).status_code != 401


def test_agent_key_delete_locks_the_door_again(client):
    key = client.put("/api/settings/agent-key", headers=OWNER_H).json()["key"]
    assert client.delete("/api/settings/agent-key", headers=OWNER_H).json()["configured"] is False
    assert client.post("/api/inbox/drain", headers={"X-Api-Key": key}).status_code == 401


def test_no_agent_key_configured_means_subset_stays_owner_only(client):
    assert client.post("/api/inbox/drain", headers={"X-Api-Key": "anything"}).status_code == 401


def test_gate_password_door_opens_skill_doc_not_the_dashboard(client):
    """POST /api/auth/agent -- the gate's password field for a headless agent. Right key -> points at
    the public skill doc, never a session/dashboard access; wrong/missing key -> 401, no cookie set."""
    key = client.put("/api/settings/agent-key", headers=OWNER_H).json()["key"]
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
    monkeypatch.setenv("AUTH_DISABLED", "0")
    assert client.get("/api/coverage").status_code == 401
