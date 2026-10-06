"""app/wa_proxy.py: the board-side proxy to the WhatsApp harness's read-only Pro API (TASK-395).

No network in this suite -- app.wa_proxy._TRANSPORT is swapped for an httpx.MockTransport standing
in for the harness, the same way tests/test_auth.py stubs Supabase/crawl/scheduler so the app's
startup event never leaves the process. Owner-gating itself is tests/test_auth.py's job (the four
proxy paths are in its DENIED parametrization); this file is about what the proxy does once past
that gate: the token it attaches, the query string it carries over, and the error mapping.
"""
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth as AU
from app import config as A
from app import data as D
from app import runs as R
from app import scheduler as S
from app import schedules as SC
from app import stripe_gate as SG
from app import wa_proxy as WP

OWNER = "owner@example.org"
CUSTOMER = "paying@example.org"
USER, PASS = AU.DEFAULT_LOGIN


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Owner auth ON (same shape as tests/test_auth.py's fixture) plus a clean WA_API_BASE/TOKEN and
    transport per test -- no test may see another test's mock handler or a real WA_API_BASE from the
    shell (tests/conftest.py already scrubs the WA_BRIDGE_*/META_WHATSAPP_* rail credentials; this
    module reads different env names, so it gets its own belt-and-braces delenv here)."""
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("OWNER_EMAILS", OWNER)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setattr(AU, "_inited_path", None)
    monkeypatch.setattr(SG, "_inited_path", None)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {"cities": []},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(R, "enqueue", lambda rid: None)
    monkeypatch.setattr(S, "start", lambda: SC.init())
    monkeypatch.setattr(A, "rest_count", lambda path, params=None, timeout=60: 0)
    monkeypatch.setattr(A, "rest_get_all", lambda path, params=None, page=1000, timeout=120: [])
    monkeypatch.setattr(A, "rest_get", lambda path, params=None, timeout=120, retries=2: [])
    monkeypatch.delenv("WA_API_BASE", raising=False)
    monkeypatch.delenv("WA_API_TOKEN", raising=False)
    monkeypatch.setattr(WP, "_TRANSPORT", None)


@pytest.fixture()
def client(env):
    from app.main import app
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        yield c


def _login(client):
    r = client.post("/api/auth/login", json={"user": USER, "pass": PASS})
    assert r.status_code == 200, r.text


def _mock(monkeypatch, handler):
    """Point the proxy's httpx.AsyncClient at a fake harness. `handler(request) -> httpx.Response`."""
    monkeypatch.setattr(WP, "_TRANSPORT", httpx.MockTransport(handler))


# --- not configured -----------------------------------------------------------------------------
def test_503_when_base_unset(client, monkeypatch):
    _login(client)
    r = client.get("/api/wa/threads")
    assert r.status_code == 503
    assert r.headers["content-type"].startswith("application/problem+json")
    assert "not configured" in r.json()["detail"]


# --- token attached, never echoed ----------------------------------------------------------------
def test_token_header_added_and_never_echoed(client, monkeypatch):
    monkeypatch.setenv("WA_API_BASE", "http://harness.internal:8502")
    monkeypatch.setenv("WA_API_TOKEN", "s3cr3t-read-token")
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"total": 0, "rows": []})

    _mock(monkeypatch, handler)
    _login(client)
    r = client.get("/api/wa/threads")
    assert r.status_code == 200
    assert seen["auth"] == "Bearer s3cr3t-read-token"
    assert "Authorization" not in r.headers and "authorization" not in r.headers
    assert "s3cr3t-read-token" not in r.text


# --- query string forwarded verbatim --------------------------------------------------------------
def test_query_string_forwarded(client, monkeypatch):
    monkeypatch.setenv("WA_API_BASE", "http://harness.internal:8502")
    monkeypatch.setenv("WA_API_TOKEN", "tok")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json={"rows": []})

    _mock(monkeypatch, handler)
    _login(client)
    r = client.get("/api/wa/threads?include_test=1&limit=5&offset=10")
    assert r.status_code == 200
    assert seen["query"] == "include_test=1&limit=5&offset=10"
    assert seen["url"] == "http://harness.internal:8502/api/wa/pro/threads?include_test=1&limit=5&offset=10"


# --- 2xx passthrough, all six paths -----------------------------------------------------------------
@pytest.mark.parametrize("board_path,harness_path", [
    ("/api/wa/threads", "/api/wa/pro/threads"),
    ("/api/wa/threads/t_abc123", "/api/wa/pro/threads/t_abc123"),
    ("/api/wa/threads/t_abc123/messages", "/api/wa/pro/threads/t_abc123/messages"),
    ("/api/wa/health", "/api/wa/pro/health"),
    ("/api/wa/activity", "/api/wa/pro/activity"),
    ("/api/wa/ops", "/api/wa/pro/ops"),
])
def test_2xx_passthrough(client, monkeypatch, board_path, harness_path):
    monkeypatch.setenv("WA_API_BASE", "http://harness.internal:8502")
    monkeypatch.setenv("WA_API_TOKEN", "tok")
    body = {"marker": "exact-body-from-harness", "path": harness_path}
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json=body)

    _mock(monkeypatch, handler)
    _login(client)
    r = client.get(board_path)
    assert r.status_code == 200
    assert r.json() == body
    assert seen["path"] == harness_path


# --- error mapping ----------------------------------------------------------------------------
def test_harness_401_maps_to_502_not_401(client, monkeypatch):
    """A rejected WA_API_TOKEN must never surface as a bare 401 -- this board's own middleware uses
    401 to mean "you are not an owner", and the Pro view reads it exactly that way (docs/wa-dashboard.md)."""
    monkeypatch.setenv("WA_API_BASE", "http://harness.internal:8502")
    monkeypatch.setenv("WA_API_TOKEN", "wrong-token")
    _mock(monkeypatch, lambda request: httpx.Response(401, json={"detail": "bad token"}))
    _login(client)
    r = client.get("/api/wa/threads")
    assert r.status_code == 502
    assert "harness rejected the board token" in r.json()["detail"]


def test_harness_403_also_maps_to_502(client, monkeypatch):
    monkeypatch.setenv("WA_API_BASE", "http://harness.internal:8502")
    monkeypatch.setenv("WA_API_TOKEN", "read-token-used-on-write-path")
    _mock(monkeypatch, lambda request: httpx.Response(403, json={"detail": "forbidden"}))
    _login(client)
    r = client.get("/api/wa/threads")
    assert r.status_code == 502
    assert "harness rejected the board token" in r.json()["detail"]


def test_harness_404_on_unknown_thread_passes_through(client, monkeypatch):
    monkeypatch.setenv("WA_API_BASE", "http://harness.internal:8502")
    monkeypatch.setenv("WA_API_TOKEN", "tok")
    _mock(monkeypatch, lambda request: httpx.Response(404, json={"detail": "unknown thread"}))
    _login(client)
    r = client.get("/api/wa/threads/t_does_not_exist")
    assert r.status_code == 404
    assert r.json()["detail"] == "unknown thread"


def test_harness_5xx_maps_to_502_with_status_folded_in(client, monkeypatch):
    monkeypatch.setenv("WA_API_BASE", "http://harness.internal:8502")
    monkeypatch.setenv("WA_API_TOKEN", "tok")
    _mock(monkeypatch, lambda request: httpx.Response(500, text="internal error"))
    _login(client)
    r = client.get("/api/wa/threads")
    assert r.status_code == 502
    assert "500" in r.json()["detail"]


def test_harness_unreachable_maps_to_502(client, monkeypatch):
    monkeypatch.setenv("WA_API_BASE", "http://harness.internal:8502")
    monkeypatch.setenv("WA_API_TOKEN", "tok")

    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    _mock(monkeypatch, handler)
    _login(client)
    r = client.get("/api/wa/threads")
    assert r.status_code == 502
    assert "unreachable" in r.json()["detail"]


def test_harness_timeout_maps_to_504(client, monkeypatch):
    monkeypatch.setenv("WA_API_BASE", "http://harness.internal:8502")
    monkeypatch.setenv("WA_API_TOKEN", "tok")

    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    _mock(monkeypatch, handler)
    _login(client)
    r = client.get("/api/wa/threads")
    assert r.status_code == 504
    assert "did not answer in time" in r.json()["detail"]


# --- owner gate itself (the DENIED parametrization in tests/test_auth.py covers the anonymous/
# customer 401 cases already; this checks the positive case reaches the proxy at all) --------------
def test_owner_session_reaches_the_proxy(client, monkeypatch):
    monkeypatch.setenv("WA_API_BASE", "http://harness.internal:8502")
    monkeypatch.setenv("WA_API_TOKEN", "tok")
    _mock(monkeypatch, lambda request: httpx.Response(200, json={"rows": []}))
    _login(client)
    assert client.get("/api/wa/threads").status_code == 200


def test_anonymous_and_customer_denied(client):
    """Same 401-with-role-in-body shape as every other OWNER_READ_PREFIXES route (app/auth.py) --
    not a separate 403: a customer session is a real, valid session, just the wrong role, and the
    middleware answers that the same way it answers "no session at all" (RFC 9457, `role` extension)."""
    r = client.get("/api/wa/threads")
    assert r.status_code == 401 and r.json()["role"] == "anonymous"
    AU.upsert_customer(CUSTOMER)
    cookie = AU.create_session(CUSTOMER, "customer")
    r = client.get("/api/wa/threads", headers={"Cookie": "pj_session=" + cookie})
    assert r.status_code == 401 and r.json()["role"] == "customer"


# --- TASK-283.7: /api/wa/activity, /api/wa/ops (query string carried over too) ---------------------

def test_activity_query_string_forwarded(client, monkeypatch):
    monkeypatch.setenv("WA_API_BASE", "http://harness.internal:8502")
    monkeypatch.setenv("WA_API_TOKEN", "tok")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"rows": []})

    _mock(monkeypatch, handler)
    _login(client)
    r = client.get("/api/wa/ops?status=failed&origin=auto&limit=5")
    assert r.status_code == 200
    assert seen["url"] == ("http://harness.internal:8502/api/wa/pro/ops"
                           "?status=failed&origin=auto&limit=5")


def test_activity_and_ops_anonymous_and_customer_denied(client):
    for path in ("/api/wa/activity", "/api/wa/ops"):
        r = client.get(path)
        assert r.status_code == 401 and r.json()["role"] == "anonymous", path
    AU.upsert_customer(CUSTOMER)
    cookie = AU.create_session(CUSTOMER, "customer")
    for path in ("/api/wa/activity", "/api/wa/ops"):
        r = client.get(path, headers={"Cookie": "pj_session=" + cookie})
        assert r.status_code == 401 and r.json()["role"] == "customer", path


def test_owner_session_reaches_activity_and_ops(client, monkeypatch):
    monkeypatch.setenv("WA_API_BASE", "http://harness.internal:8502")
    monkeypatch.setenv("WA_API_TOKEN", "tok")
    _mock(monkeypatch, lambda request: httpx.Response(200, json={"rows": []}))
    _login(client)
    assert client.get("/api/wa/activity").status_code == 200
    assert client.get("/api/wa/ops").status_code == 200


# --- status documents at public token links: GET /s/{token}/[name] (TASK-436) --------------------------
TOKEN = "Ab3_dE-6hIjKlMnOpQrStU"                       # 22 characters of the url-safe alphabet, as the harness mints them
DOC_HEADERS = {"content-type": "text/html; charset=utf-8", "x-robots-tag": "noindex, nofollow",
               "referrer-policy": "no-referrer", "cache-control": "no-cache"}


def _harness_docs(monkeypatch, handler):
    monkeypatch.setenv("WA_API_BASE", "http://harness.internal:8502")
    monkeypatch.setenv("WA_API_TOKEN", "s3cr3t-read-token")
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    _mock(monkeypatch, wrapped)
    return calls


def test_an_anonymous_reader_gets_the_document_with_the_harness_s_headers_and_nothing_else(client, monkeypatch):
    calls = _harness_docs(monkeypatch, lambda request: httpx.Response(
        200, content="<h1>Weitere Kliniken</h1>".encode(), headers={**DOC_HEADERS, "x-harness-build": "abc", "set-cookie": "sid=1"}))
    r = client.get(f"/s/{TOKEN}/", headers={"Range": "bytes=0-9", "If-None-Match": '"x"', "Cookie": "pb_session=zzz"})
    assert (r.status_code, r.text) == (200, "<h1>Weitere Kliniken</h1>")
    assert {k: r.headers[k] for k in DOC_HEADERS} == DOC_HEADERS
    assert "x-harness-build" not in r.headers and "set-cookie" not in r.headers
    sent = calls[0]
    assert str(sent.url) == f"http://harness.internal:8502/api/wa/pro/status/{TOKEN}/"
    assert sent.headers["authorization"] == "Bearer s3cr3t-read-token"
    assert not {"range", "if-none-match", "cookie"} & set(sent.headers)
    assert "s3cr3t-read-token" not in r.text and "authorization" not in r.headers


@pytest.mark.parametrize("name,ctype", [("index.html", "text/html; charset=utf-8"), ("detail.html", "text/html; charset=utf-8"),
                                        ("wave-3_kliniken.v2.pdf", "application/pdf")])
def test_a_named_file_is_fetched_under_its_own_name_without_the_query_string(client, monkeypatch, name, ctype):
    calls = _harness_docs(monkeypatch, lambda request: httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": ctype}))
    r = client.get(f"/s/{TOKEN}/{name}?download=1&x=../../etc")
    assert (r.status_code, r.content, r.headers["content-type"]) == (200, b"%PDF-1.7", ctype)
    assert str(calls[0].url) == f"http://harness.internal:8502/api/wa/pro/status/{TOKEN}/{name}"


def test_the_bare_token_redirects_to_the_directory_so_relative_links_resolve(client, monkeypatch):
    calls = _harness_docs(monkeypatch, lambda request: httpx.Response(200, content=b"x"))
    r = client.get(f"/s/{TOKEN}")
    assert (r.status_code, r.headers["location"]) == (308, f"/s/{TOKEN}/") and calls == []


@pytest.mark.parametrize("path", [
    "/s/short/", "/s/" + "A" * 23 + "/", "/s/Ab3_dE-6hIjKlMnOpQrSt%2E/", "/s/Ab3_dE-6hIjKlMnOpQrSt./",        # not a token
    f"/s/{TOKEN}/Index.html", f"/s/{TOKEN}/notes.txt", f"/s/{TOKEN}/.pdf", f"/s/{TOKEN}/a.PDF",             # not a document name
    f"/s/{TOKEN}/..%2Fx.pdf", f"/s/{TOKEN}/%2e%2e", f"/s/{TOKEN}/a/b.pdf", f"/s/{TOKEN}/" + "a" * 82 + ".pdf",
    "/s/short"])
def test_an_address_that_is_not_a_document_s_shape_is_a_404_and_never_reaches_the_harness(client, monkeypatch, path):
    calls = _harness_docs(monkeypatch, lambda request: httpx.Response(200, content=b"x"))
    r = client.get(path)
    assert r.status_code == 404 and calls == [], (path, r.status_code, [str(c.url) for c in calls])


@pytest.mark.parametrize("upstream,expected", [(404, 404), (401, 502), (403, 502), (500, 502), (503, 502), (206, 502), (302, 502)])
def test_a_failure_tells_the_reader_nothing_about_the_harness(client, monkeypatch, upstream, expected):
    _harness_docs(monkeypatch, lambda request: httpx.Response(
        upstream, json={"detail": "sqlite3.OperationalError at /home/claude/state", "token": TOKEN}, headers={"location": "http://harness.internal:8502/x"}))
    r = client.get(f"/s/{TOKEN}/detail.html")
    assert r.status_code == expected
    assert r.json()["detail"] == ("not found" if expected == 404 else "the document could not be fetched")
    for leak in ("sqlite3", "/home/claude", "harness.internal", "s3cr3t"):      # the reader's own path (problem+json "instance") is theirs already
        assert leak not in r.text and leak not in str(dict(r.headers))


def test_an_unreachable_or_slow_harness_and_a_board_without_one_are_fixed_texts(client, monkeypatch):
    r = client.get(f"/s/{TOKEN}/")                                              # WA_API_BASE unset
    assert (r.status_code, r.json()["detail"]) == (503, "documents are not available right now")

    def refuse(request):
        raise httpx.ConnectError("connection refused to http://harness.internal:8502", request=request)

    _harness_docs(monkeypatch, refuse)
    r = client.get(f"/s/{TOKEN}/")
    assert (r.status_code, r.json()["detail"]) == (502, "the document could not be fetched") and "harness.internal" not in r.text

    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    _harness_docs(monkeypatch, slow)
    r = client.get(f"/s/{TOKEN}/")
    assert (r.status_code, r.json()["detail"]) == (504, "the document did not arrive in time")


def test_only_get_is_served_and_every_other_harness_route_stays_owner_only(client, monkeypatch):
    calls = _harness_docs(monkeypatch, lambda request: httpx.Response(200, content=b"x"))
    for method in ("head", "post", "put", "delete"):
        assert getattr(client, method)(f"/s/{TOKEN}/").status_code == 405
    assert calls == []
    for path in ("/api/wa/threads", "/api/wa/health", "/api/wa/activity", "/api/wa/ops"):        # the public door opens nothing else
        assert client.get(path).status_code == 401
    assert client.get(f"/api/wa/pro/status/{TOKEN}/").status_code == 404 and client.get(f"/api/s/{TOKEN}/").status_code == 404
    assert calls == []


def test_the_two_typefaces_come_from_this_server_and_nothing_else_does(client):
    for name in ("archivo-black.woff2", "jetbrains-mono.woff2"):
        r = client.get(f"/fonts/{name}")
        assert (r.status_code, r.headers["content-type"], r.content[:4]) == (200, "font/woff2", b"wOF2")
    for name in ("x.woff2", "..%2Fpro.html", "archivo-black.woff2.bak", "%2e%2e%2f%2e%2e%2f.env"):
        assert client.get(f"/fonts/{name}").status_code == 404
