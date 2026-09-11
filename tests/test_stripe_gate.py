"""app/stripe_gate.py: feature flag (503 paths), checkout payload, webhook signature, idempotent closed-posting ledger,
usage records (meter event / legacy usage record) through a fake requests session."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import auth as AU
from app import config as A
from app import data as D
from app import runs as R
from app import scheduler as S
from app import schedules as SC
from app import stripe_gate as SG


def SG_ST():
    from app import settings as ST
    return ST

OWNER = "owner@example.org"
CUSTOMER = "clinic@example.org"
KEY = "sk_test_NEVERLOGTHIS123"
WH = "whsec_testsecret"


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.content = json.dumps(body).encode()

    def json(self):
        return self._body


class FakeSession:
    """Records every call; answers from a queue of (status, body) or a default per path."""

    def __init__(self):
        self.calls = []
        self.queue = []

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "data": dict(data or {}), "headers": dict(headers or {})})
        if self.queue:
            status, body = self.queue.pop(0)
            return FakeResp(status, body)
        if url.endswith("/v1/checkout/sessions"):
            return FakeResp(200, {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"})
        if "/v1/subscriptions" in url:
            return FakeResp(200, {"data": [{"id": "sub_1", "items": {"data": [{"id": "si_1", "price": {"id": "price_closed"}}]}}]})
        if url.endswith("/usage_records"):
            return FakeResp(200, {"id": "mbur_1", "quantity": 1})
        if url.endswith("/v1/billing/meter_events"):
            return FakeResp(200, {"object": "billing.meter_event", "identifier": data.get("identifier")})
        return FakeResp(404, {"error": {"message": "unknown fake path"}})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("OWNER_EMAILS", OWNER)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    for k in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET", "STRIPE_METER_EVENT_NAME", "STRIPE_API_BASE", "TAILNET_TRUST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(AU, "_inited_path", None)
    monkeypatch.setattr(SG, "_inited_path", None)
    fake = FakeSession()
    monkeypatch.setattr(SG, "_session", fake)
    monkeypatch.setattr(AU, "send_mail", lambda to, subject, body: True)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {"cities": []}, "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(R, "enqueue", lambda rid: None)
    monkeypatch.setattr(S, "start", lambda: SC.init())
    return fake


@pytest.fixture()
def client(env):
    from app.main import app
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        yield c


@pytest.fixture()
def configured(monkeypatch, env):
    """Stripe fully wired: key + price + webhook secret, AND the feature_flags.stripe switch on (off by
    default -- see test_settings_flags.py for the flag itself)."""
    from app import settings as ST
    monkeypatch.setenv("STRIPE_SECRET_KEY", KEY)
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_closed")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WH)
    ST.save_feature_flags({"stripe": True})


def _owner_cookie():
    return {"Cookie": "pj_session=" + AU.create_session(OWNER, "owner")}


def _customer_cookie(email=CUSTOMER, cus="cus_1"):
    AU.upsert_customer(email, cus)
    return {"Cookie": "pj_session=" + AU.create_session(email, "customer", cus)}


def _event(typ, obj):
    return json.dumps({"id": "evt_1", "type": typ, "data": {"object": obj}}).encode()


def _post_event(client, payload, secret=WH, t=None):
    return client.post("/api/stripe/webhook", content=payload,
                       headers={"Stripe-Signature": SG.sign_payload(payload, secret, t), "Content-Type": "application/json"})


# --- feature flag -----------------------------------------------------------------------------
def test_status_unconfigured(client):
    """Anonymous sees the setup booleans (POST /stripe/checkout is public, so "can I buy?" has to be), and
    null for the two revenue counts. The owner sees the counts."""
    d = client.get("/api/stripe/status").json()
    assert d == {**d, "configured": False, "price_id_set": False, "webhook_set": False,
                 "customers": None, "closed_this_period": None}
    o = client.get("/api/stripe/status", headers=_owner_cookie()).json()
    assert o == {**o, "configured": False, "customers": 0, "closed_this_period": 0}


def test_503_paths_without_key(client, env):
    # problem+json, the shape app/main.py:_problem() builds for every other error (docs/errors.md)
    r = client.post("/api/stripe/checkout", json={"email": CUSTOMER})
    assert r.status_code == 503 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json() == {**r.json(), "status": 503, "detail": "stripe not configured", "error": "stripe not configured"}
    r = client.post("/api/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "t=1,v1=00"})
    assert r.status_code == 503 and r.json()["error"] == "stripe not configured"
    assert env.calls == []


def test_checkout_needs_price_id(client, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", KEY)
    SG_ST().save_feature_flags({"stripe": True})
    r = client.post("/api/stripe/checkout", json={"email": CUSTOMER})
    assert r.status_code == 503 and "price" in r.json()["error"]
    assert client.get("/api/stripe/status").json() == {**client.get("/api/stripe/status").json(), "configured": True, "price_id_set": False}


def test_closed_records_locally_without_stripe(client, env):
    r = client.post("/api/postings/4711/closed", headers=_owner_cookie())
    assert r.status_code == 200
    d = r.json()
    assert d["posting_id"] == 4711 and d["by_email"] == OWNER and d["already"] is False and d["billed"] is False and d["stripe_usage_id"] is None
    r2 = client.post("/api/postings/4711/closed", headers=_owner_cookie()).json()           # idempotent
    assert r2["already"] is True and r2["at"] == d["at"]
    with R._lock, R.db() as c:
        assert c.execute("select count(*) from closed_postings").fetchone()[0] == 1
    assert client.get("/api/stripe/status", headers=_owner_cookie()).json()["closed_this_period"] == 1
    assert client.get("/api/postings/4711/closed", headers=_owner_cookie()).json()["closed"] is True
    assert client.get("/api/postings/4712/closed", headers=_owner_cookie()).json()["closed"] is False
    assert env.calls == []


def test_closed_by_customer_without_key_keeps_ledger(client, env):
    h = _customer_cookie()
    d = client.post("/api/postings/99/closed", headers=h).json()
    assert d["by_email"] == CUSTOMER and d["stripe_customer_id"] == "cus_1" and d["billed"] is False
    assert d["stripe_error"] == "stripe not configured"
    assert env.calls == []


def test_closed_requires_member(client):
    r = client.post("/api/postings/1/closed")
    assert r.status_code == 401 and r.json()["error"] == "sign in required" and r.json()["role"] == "anonymous"
    assert client.get("/api/postings/1/closed").status_code == 401
    assert client.post("/api/postings/0/closed", headers=_owner_cookie()).status_code == 400
    assert client.post("/api/postings/abc/closed", headers=_owner_cookie()).status_code == 422


# --- checkout ---------------------------------------------------------------------------------
def test_checkout_payload_shape(client, env, configured):
    r = client.post("/api/stripe/checkout", json={"email": " Clinic@Example.org "})
    assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.com/")
    assert len(env.calls) == 1
    call = env.calls[0]
    assert call["method"] == "POST" and call["url"] == "https://api.stripe.com/v1/checkout/sessions"
    assert call["headers"]["Authorization"] == "Bearer " + KEY
    d = call["data"]
    assert d["mode"] == "subscription" and d["line_items[0][price]"] == "price_closed" and "line_items[0][quantity]" not in d
    assert d["customer_email"] == CUSTOMER
    assert d["success_url"] == "https://pflege-board.exe.xyz/pro?stripe=ok" and d["cancel_url"] == "https://pflege-board.exe.xyz/pro?stripe=cancel"
    assert d["metadata[email]"] == CUSTOMER and "metadata[sid_hash]" not in d
    assert client.post("/api/stripe/checkout", json={"email": "nope"}).status_code == 400


def test_checkout_carries_session_hash_and_api_base(client, env, configured, monkeypatch):
    monkeypatch.setenv("STRIPE_API_BASE", "https://stripe.example.test/")
    cookie = AU.create_session(OWNER, "owner")
    r = client.post("/api/stripe/checkout", json={"email": CUSTOMER}, headers={"Cookie": "pj_session=" + cookie})
    assert r.status_code == 200
    call = env.calls[0]
    assert call["url"] == "https://stripe.example.test/v1/checkout/sessions"
    sid = cookie.rsplit(".", 1)[0]
    assert call["data"]["metadata[sid_hash]"] == AU._sha(sid) and sid not in json.dumps(call["data"])


def test_checkout_stripe_error_never_leaks_key(client, env, configured):
    env.queue.append((402, {"error": {"message": "Your card was declined"}}))
    r = client.post("/api/stripe/checkout", json={"email": CUSTOMER})
    assert r.status_code == 502 and "declined" in r.json()["error"] and KEY not in r.text


# --- webhook ----------------------------------------------------------------------------------
def test_verify_signature_v1_scheme():
    payload = b'{"id":"evt_1"}'
    t = 1_700_000_000
    h = SG.sign_payload(payload, WH, t)
    assert h.startswith(f"t={t},v1=")
    assert SG.verify_signature(payload, h, WH, now=t + 10)
    assert SG.verify_signature(payload, h + ",v1=deadbeef", WH, now=t)          # extra v1 entries are fine
    assert not SG.verify_signature(payload, h, "whsec_other", now=t)
    assert not SG.verify_signature(payload + b" ", h, WH, now=t)
    assert not SG.verify_signature(payload, h, WH, now=t + SG.WEBHOOK_TOLERANCE_S + 1)  # stale
    assert not SG.verify_signature(payload, "", WH) and not SG.verify_signature(payload, "v1=abc", WH) and not SG.verify_signature(payload, "t=x,v1=abc", WH)
    # non-ASCII: the header arrives latin-1-decoded and hmac.compare_digest refuses such a str (TypeError,
    # i.e. a 500 echoing the exception on a public route). The check compares encoded bytes, so this is
    # just a wrong signature. Same shape as app/firecrawl_hooks.py:96 and app/auth.py:_parse_cookie.
    assert not SG.verify_signature(payload, f"t={t},v1=\xff", WH, now=t)


def test_webhook_non_ascii_signature_is_a_400_not_a_500(client, configured):
    payload = b'{"id":"evt_1","type":"invoice.paid","data":{"object":{}}}'
    r = client.post("/api/stripe/webhook", content=payload,
                    headers={"Stripe-Signature": b"t=1700000000,v1=\xff", "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["detail"] == "invalid signature"


def test_webhook_checkout_completed_upserts_customer(client, env, configured):
    obj = {"id": "cs_1", "customer": "cus_42", "customer_details": {"email": "New@Clinic.org"}, "metadata": {"email": "new@clinic.org"}}
    r = _post_event(client, _event("checkout.session.completed", obj))
    assert r.status_code == 200 and r.json() == {**r.json(), "received": True, "handled": True, "customer": "new@clinic.org", "session_marked": False}
    assert SG.customer_for_email("new@clinic.org") == {**SG.customer_for_email("new@clinic.org"), "stripe_customer_id": "cus_42", "status": "active"}
    assert AU.customer_role("new@clinic.org") == "customer"
    assert client.get("/api/stripe/status", headers=_owner_cookie()).json()["customers"] == 1
    # cancellation
    r = _post_event(client, _event("customer.subscription.deleted", {"id": "sub_1", "customer": "cus_42"}))
    assert r.status_code == 200 and r.json()["cancelled"] == 1
    assert AU.customer_role("new@clinic.org") is None and client.get("/api/stripe/status", headers=_owner_cookie()).json()["customers"] == 0
    # unknown events are acknowledged, not applied
    r = _post_event(client, _event("invoice.paid", {"id": "in_1"}))
    assert r.status_code == 200 and r.json()["handled"] is False
    assert env.calls == []                                                          # webhook never calls Stripe back


def test_webhook_marks_session_customer(client, env, configured):
    cookie = AU.create_session("", "customer")                                       # a pre-checkout session row
    sid_hash = AU._sha(cookie.rsplit(".", 1)[0])
    obj = {"customer": "cus_7", "customer_email": CUSTOMER, "metadata": {"sid_hash": sid_hash}}
    assert _post_event(client, _event("checkout.session.completed", obj)).json()["session_marked"] is True
    sess = AU.session_from_cookie(cookie)
    assert sess["role"] == "customer" and sess["stripe_customer_id"] == "cus_7" and sess["email"] == CUSTOMER
    owner_cookie = AU.create_session(OWNER, "owner")                                # owner sessions keep their role
    obj["metadata"]["sid_hash"] = AU._sha(owner_cookie.rsplit(".", 1)[0])
    _post_event(client, _event("checkout.session.completed", obj))
    assert AU.session_from_cookie(owner_cookie)["role"] == "owner"


def test_webhook_rejects_bad_signature(client, env, configured):
    payload = _event("checkout.session.completed", {"customer": "cus_1", "customer_email": CUSTOMER})
    assert client.post("/api/stripe/webhook", content=payload).status_code == 400
    assert _post_event(client, payload, secret="whsec_wrong").status_code == 400
    assert _post_event(client, payload, t=int(time.time()) - 3600).status_code == 400
    assert SG.customer_for_email(CUSTOMER) is None
    r = _post_event(client, b"not json")
    assert r.status_code == 400 and r.json()["error"] == "invalid payload"


def test_webhook_needs_secret(client, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", KEY)
    SG_ST().save_feature_flags({"stripe": True})
    r = client.post("/api/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "t=1,v1=00"})
    assert r.status_code == 503 and "webhook" in r.json()["error"]


# --- usage ------------------------------------------------------------------------------------
def test_closed_by_customer_writes_usage_record(client, env, configured):
    h = _customer_cookie()
    d = client.post("/api/postings/555/closed", headers=h).json()
    assert d["billed"] is True and d["stripe_usage_id"] == "mbur_1" and d["stripe_error"] is None and d["by_email"] == CUSTOMER
    assert [c["method"] + " " + c["url"] for c in env.calls] == [
        "GET https://api.stripe.com/v1/subscriptions", "POST https://api.stripe.com/v1/subscription_items/si_1/usage_records"]
    assert env.calls[0]["data"]["customer"] == "cus_1"
    post = env.calls[1]
    assert post["data"] == {**post["data"], "quantity": 1, "action": "increment"} and post["headers"]["Idempotency-Key"] == "closed-posting-555"
    assert post["headers"]["Authorization"] == "Bearer " + KEY
    n = len(env.calls)
    d2 = client.post("/api/postings/555/closed", headers=h).json()                 # idempotent: no second usage record
    assert d2["already"] is True and d2["stripe_usage_id"] == "mbur_1" and len(env.calls) == n
    assert client.get("/api/stripe/status", headers=_owner_cookie()).json()["closed_this_period"] == 1


def test_closed_meter_event_path(client, env, configured, monkeypatch):
    monkeypatch.setenv("STRIPE_METER_EVENT_NAME", "closed_posting")
    d = client.post("/api/postings/556/closed", headers=_customer_cookie()).json()
    assert d["billed"] is True and d["stripe_usage_id"] == "closed-posting-556"
    assert len(env.calls) == 1 and env.calls[0]["url"].endswith("/v1/billing/meter_events")
    data = env.calls[0]["data"]
    assert data == {**data, "event_name": "closed_posting", "identifier": "closed-posting-556", "payload[stripe_customer_id]": "cus_1", "payload[value]": 1}


def test_closed_keeps_ledger_when_stripe_fails(client, env, configured):
    env.queue.append((500, {"error": {"message": "boom"}}))
    d = client.post("/api/postings/557/closed", headers=_customer_cookie()).json()
    assert d["billed"] is False and d["stripe_usage_id"] is None and "500" in d["stripe_error"] and KEY not in json.dumps(d)
    assert SG.closed_row(557)["by_email"] == CUSTOMER
    env.queue.append((200, {"data": []}))                                            # no active subscription
    d = client.post("/api/postings/558/closed", headers=_customer_cookie("other@clinic.org", "cus_2")).json()
    assert d["billed"] is False and "no active subscription" in d["stripe_error"]


def test_owner_closes_on_behalf_of_customer(client, env, configured):
    AU.upsert_customer(CUSTOMER, "cus_1")
    d = client.post("/api/postings/600/closed", headers=_owner_cookie(), json={"customer_email": CUSTOMER}).json()
    assert d["by_email"] == CUSTOMER and d["billed"] is True
    d = client.post("/api/postings/601/closed", headers=_owner_cookie()).json()              # owner alone: ledger only
    assert d["by_email"] == OWNER and d["billed"] is False and d["stripe_error"] is None
    # a customer cannot re-attribute
    d = client.post("/api/postings/602/closed", headers=_customer_cookie("me@clinic.org", "cus_9"), json={"customer_email": CUSTOMER}).json()
    assert d["by_email"] == "me@clinic.org"


def test_cancelled_customer_not_billed(client, env, configured):
    h = _customer_cookie()
    AU.upsert_customer(CUSTOMER, status="cancelled")
    r = client.post("/api/postings/700/closed", headers=h)              # a cancelled subscription revokes the session at once
    assert r.status_code == 401 and env.calls == [] and not SG.closed_row(700)


def test_schema_is_self_sufficient(tmp_path, monkeypatch):
    """closed_postings (and customers/sessions, whichever track lands first) exist after _ensure() on a fresh DB."""
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "x.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    monkeypatch.setattr(SG, "_inited_path", None)
    SG._ensure()
    with R._lock, R.db() as c:
        names = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    assert {"closed_postings", "customers", "sessions"} <= names


# --- a closed_postings row belongs to the customer that closed it (2026-09-11) -----------------
# GET /api/postings/{id}/closed took no Request and no identity: it returned closed_row() verbatim, so ANY
# signed-in member (auth.MEMBER_READ covers /api/postings) could walk posting ids and read every other
# paying clinic's e-mail address, Stripe customer id, usage id and Stripe's own error string. The POST's
# "already closed" branch handed back the same row.
OTHER = "other-clinic@example.org"
PRIVATE = ("by_email", "stripe_customer_id", "stripe_usage_id", "stripe_error")


def _close_as(client, posting_id, email, cus):
    """`email` closes `posting_id` as a paying customer. Returns that customer's cookie header."""
    h = _customer_cookie(email, cus)
    r = client.post(f"/api/postings/{posting_id}/closed", headers=h)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["by_email"] == email, r.json()
    return h


def test_a_customer_cannot_read_another_customers_closed_row(client, configured):
    """Two different paying customers, one closed posting. The stranger learns the posting is taken and
    when; everything that identifies or bills the closer stays with the closer and the owner."""
    mine = _close_as(client, 4242, CUSTOMER, "cus_a")
    stranger = _customer_cookie(OTHER, "cus_b")

    seen = client.get("/api/postings/4242/closed", headers=stranger)
    assert seen.status_code == 200
    body = seen.json()
    assert body["closed"] is True and body["posting_id"] == 4242 and body["at"]
    assert set(body) == {"posting_id", "closed", "at"}, body
    for leak in PRIVATE:
        assert leak not in body, (leak, body)
    for secret in (CUSTOMER, "cus_a"):
        assert secret not in seen.text, (secret, seen.text)

    own = client.get("/api/postings/4242/closed", headers=mine).json()      # the closer still sees its own row
    assert own["by_email"] == CUSTOMER and own["stripe_customer_id"] == "cus_a"
    owner = client.get("/api/postings/4242/closed", headers=_owner_cookie()).json()
    assert owner["by_email"] == CUSTOMER and all(k in owner for k in PRIVATE), owner


def test_the_already_closed_answer_does_not_leak_the_other_customers_row(client, configured):
    """The POST's idempotent branch returned the stored row too -- the same leak through the write door."""
    _close_as(client, 4243, CUSTOMER, "cus_a")
    again = client.post("/api/postings/4243/closed", headers=_customer_cookie(OTHER, "cus_b"))
    assert again.status_code == 200
    body = again.json()
    assert body["already"] is True and body["posting_id"] == 4243
    assert set(body) == {"posting_id", "at", "already"}, body
    assert "billed" not in body, body                      # billing state belongs to whoever is billed
    for secret in (CUSTOMER, "cus_a"):
        assert secret not in again.text, (secret, again.text)
    mine = client.post("/api/postings/4243/closed", headers=_customer_cookie(CUSTOMER, "cus_a")).json()
    assert mine["already"] is True and mine["by_email"] == CUSTOMER and "billed" in mine


def test_a_stripe_error_string_is_not_readable_by_a_stranger(client, env):
    """stripe_error is Stripe's own message about someone else's account ("no stripe customer for this
    address", a card decline, an API error). Without a key it is the unconfigured string; the point is that
    the column never crosses to another member whatever it holds."""
    _close_as(client, 4244, CUSTOMER, "cus_a")
    assert client.get("/api/postings/4244/closed", headers=_customer_cookie(CUSTOMER, "cus_a")).json()["stripe_error"] == "stripe not configured"
    assert "stripe_error" not in client.get("/api/postings/4244/closed", headers=_customer_cookie(OTHER, "cus_b")).json()


def test_a_posting_nobody_closed_still_answers_the_same_shape(client, env):
    for h in (_owner_cookie(), _customer_cookie(OTHER, "cus_b")):
        assert client.get("/api/postings/4245/closed", headers=h).json() == {"posting_id": 4245, "closed": False}
