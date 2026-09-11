"""The agentic API: scoped agent keys, POST /api/ingest, validate_only, Idempotency-Key, the manifest.

Auth is ON here (like tests/test_auth.py) -- a scope test with AUTH_DISABLED=1 would pass for the wrong
reason. Supabase is stubbed with a list standing in for pflege_jobs.inbox, so "validate_only writes
nothing" and "the same event twice is a duplicate" are assertions about rows, not about mocks.
"""
import re
import time

import pytest
from fastapi.testclient import TestClient

from app import auth as AU
from app import config as A
from app import crawl as CR
from app import data as D
from app import runs as R
from app import scheduler as S
from app import schedules as SC

USER, PASS = AU.DEFAULT_LOGIN
CLINIC = {"clinic_id": "36201", "name": "Krankenhaus Barmherzige Brüder", "town": "Regensburg", "operator": "BB gGmbH",
          "landkreis": "Kreisfreie Stadt Regensburg", "regierungsbezirk": "Oberpfalz", "status": "Plan-KH",
          "versorgungsstufe": "Maximalversorgung (III)", "traegerart": "freigemeinnuetzig", "beds": 985, "day_places": 27,
          "fachrichtungen": ["CHI"], "size": "XL", "website": "https://x", "careers_url": "https://bb.de/karriere/",
          "ats_type": "typo3_jobs", "jobs_open": 24, "jobs_fresh": 3, "jobs_live": 20, "routable": True, "walled": False,
          "board": "https://bb.de/karriere/", "vendor": "typo3_jobs", "route_reason": "adapter", "fetch": "adapter",
          "fetch_label": "typo3_jobs", "last_crawl_at": None, "last_crawl_status": None, "last_crawl_mode": None,
          "career_profile": None, "parse_quality": "ok", "source": "Krankenhausplan Bayern 2026"}
JOB = {"posting_id": 1, "title": "Pflegefachkraft Intensiv", "clinic_id": "36201", "city": "Regensburg", "fresh": True,
       "first_published": "2026-09-05", "status": "open", "verify_status": "live", "role_class": "fachpflege",
       "employer": "BB", "clinic_town": "Regensburg", "clinic_size": "XL"}

def gated(app):
    """{(METHOD, path template): required role or None} for every /api route the app serves.

    Deliberately re-derived here from the app's own OpenAPI document and app/auth.py:required_role() instead
    of importing app.main.gate_map(): these tests exist to diff the served manifest against what the
    middleware enforces, and calling the manifest's own helper would assert nothing. app.routes is not
    usable for the walk -- FastAPI wraps an included router in an _IncludedRouter object with no .path, so
    walking it silently skips /api/auth/*, /api/autopilot/*, /api/billing*, /api/coverage, /api/firecrawl/*,
    /api/hunter/* and /api/stripe/*, which is exactly how 75 gated routes stayed undeclared."""
    return {(m.upper(), path): AU.required_role(m.upper(), re.sub(r"\{[^}]+\}", "1", path))
            for path, ops in app.openapi()["paths"].items() if path.startswith("/api/") for m in ops}


# Bodies for the two routes whose scope depends on the body. The expected scope is not written down here:
# _cases() asks the route's own resolver, so this cannot drift from app/auth.py.
BODIES = {("POST", "/api/crawl"): [{"mode": "adapter"}, {"mode": "auto"}, {"mode": "firecrawl"}],
          ("POST", "/api/ingest"): [{"type": t} for t in AU.INGEST_SCOPE]}


class FakeSink:
    """Stands in for pflege_jobs.sinks.EdgeSink: records {op: rows} instead of calling the edge function."""
    posted = []

    def _post(self, body):
        FakeSink.posted.append(body)
        return {k: (len(v) if isinstance(v, list) else 1) for k, v in body.items()}


@pytest.fixture()
def inbox(monkeypatch):
    """The list that stands in for pflege_jobs.inbox. rest_get returns everything in it -- app/crawl.py's
    _post_inbox intersects with its own batch anyway -- and rest_post appends, so len(inbox) is the row
    count a validate_only run must leave untouched."""
    rows = []

    def rest_get(path, params=None, **kw):
        return [{"source_url": r["source_url"]} for r in rows] if path == "inbox" else []

    def rest_post(path, body, **kw):
        assert path == "inbox"
        rows.extend(body)

    monkeypatch.setattr(A, "rest_get", rest_get)
    monkeypatch.setattr(A, "rest_post", rest_post)
    monkeypatch.setattr("pflege_jobs.sinks.EdgeSink", FakeSink)
    FakeSink.posted = []
    return rows


@pytest.fixture()
def client(tmp_path, monkeypatch, inbox):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setattr(AU, "_inited_path", None)
    D._snap.update({"at": time.time(), "jobs": [JOB], "clinics": [CLINIC], "by_clinic": {"36201": CLINIC},
                    "facets": {"cities": []}, "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(R, "enqueue", lambda rid: None)            # never run a crawl in tests
    monkeypatch.setattr(S, "start", lambda: SC.init())
    from app.main import app
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        c.app = app
        yield c


def key_for(client, *scopes, label=None):
    """Mint an agent key with exactly these scopes, then drop the owner session again."""
    assert client.post("/api/auth/login", json={"user": USER, "pass": PASS}).status_code == 200
    params = {"label": label or ("+".join(scopes) or "none")}
    if scopes:
        params["scopes"] = ",".join(scopes)
    r = client.put("/api/settings/agent-key", params=params)
    assert r.status_code == 200, r.text
    client.post("/api/auth/logout")
    return r.json()["key"]


def envelope(eid, typ="posting.observed", source="test-collector-v1", **data):
    return {"specversion": "1.0", "id": eid, "source": source, "type": typ, "time": "2026-09-10T08:00:00Z",
            "subject": "36201", "data": data}


def posting(eid, url):
    return envelope(eid, source_url=url, payload={"title": "Pflegefachkraft (m/w/d)", "url": url})


# --- scopes ------------------------------------------------------------------------------------
def _cases():
    """(method, concrete path, body, scopes that open it) for every entry in AGENT_ROUTES. The expected
    scope comes from the route's own resolver, never from a list written out here."""
    for e in AU.AGENT_ROUTES:
        path = re.sub(r"\{[^}]+\}", "1", e["path"])
        for body in BODIES.get((e["method"], e["path"]), [None]):
            need = e["scope"](body) if callable(e["scope"]) else e["scope"]
            yield e["method"], path, body, ([need] if isinstance(need, str) else list(need))


@pytest.mark.parametrize("method,path,body,need", list(_cases()))
def test_each_scope_opens_exactly_its_own_routes(method, path, body, need):
    for scope in AU.SCOPES:
        ok, missing = AU.agent_allowed(method, path, body, {scope})
        assert ok == (scope in need), (method, path, body, scope)
        if not ok:
            assert missing in need                      # the 403 always names a scope that would work


def test_every_route_the_manifest_calls_session_only_is_closed_to_every_scope(client):
    """The other half of the manifest contract: what `session_only` publishes really is unreachable with a
    key carrying every scope -- 401 (no scope would help), not 403 (ask for this scope)."""
    session_only = client.get("/api/agent/manifest").json()["session_only"]
    assert len(session_only) > 60, "the walk lost routers again"
    key = key_for(client, *AU.SCOPES, label="full")
    for r in session_only:
        concrete = re.sub(r"\{[^}]+\}", "1", r["path"])
        assert AU.agent_allowed(r["method"], concrete, {}, set(AU.SCOPES)) == (False, None), r
        got = client.request(r["method"], concrete, json={}, headers={"X-Api-Key": key})
        assert got.status_code == 401, (r, got.text)


def test_wrong_scope_gets_403_naming_the_scope_it_needs(client):
    key = key_for(client, "read:board")
    r = client.get("/api/crawl/runs", headers={"X-Api-Key": key})
    assert r.status_code == 403 and r.json()["scope"] == "read:ops"
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("#insufficient_scope") and r.json()["error"] == r.json()["detail"]


def test_firecrawl_mode_is_refused_for_write_crawl_and_allowed_for_spend_firecrawl(client):
    """The spend split: mode=adapter costs nothing, auto and firecrawl both reach Firecrawl."""
    body = {"target": {"scope": "clinic", "values": ["36201"]}, "mode": "firecrawl", "max_credits": 10}
    cheap = key_for(client, "write:crawl")
    r = client.post("/api/crawl", json=body, headers={"X-Api-Key": cheap})
    assert r.status_code == 403 and r.json()["scope"] == "spend:firecrawl"
    r = client.post("/api/crawl", json={**body, "mode": "auto"}, headers={"X-Api-Key": cheap})
    assert r.status_code == 403 and r.json()["scope"] == "spend:firecrawl"
    r = client.post("/api/crawl", json={**body, "mode": "adapter"}, headers={"X-Api-Key": cheap})
    assert r.status_code == 200 and r.json()["queued"] is True
    spender = key_for(client, "spend:firecrawl", label="spender")
    r = client.post("/api/crawl", json=body, headers={"X-Api-Key": spender})
    assert r.status_code == 200 and r.json()["queued"] is True


def test_read_ops_opens_the_schedule_reads_that_used_to_be_public(client):
    """GET /api/schedules leaked every target, mode and max_credits to anonymous callers."""
    assert client.get("/api/schedules").status_code == 401
    assert client.get("/api/schedules/presets").status_code == 401
    key = key_for(client, "read:ops")
    assert client.get("/api/schedules", headers={"X-Api-Key": key}).status_code == 200
    assert client.get("/api/schedules/presets", headers={"X-Api-Key": key}).status_code == 200
    assert client.get("/api/schedules", headers={"X-Api-Key": key_for(client, "read:board", label="rb")}).status_code == 403


def test_schedule_preview_shows_the_stagger_slice_without_firing(client):
    key = key_for(client, "read:ops")
    sid = client.get("/api/schedules", headers={"X-Api-Key": key}).json()[0]["id"]
    before = len(R.list_runs(50))
    r = client.get(f"/api/schedules/{sid}/preview?day=2026-09-10", headers={"X-Api-Key": key})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["schedule_id"] == sid and d["day"] == "2026-09-10" and isinstance(d["slice"], list)
    assert d["clinics"] == len(d["slice"]) and "est_credits" in d and "next_run_at" in d
    assert len(R.list_runs(50)) == before                          # a preview queues nothing
    assert SC.get(sid)["last_run_at"] is None                      # and does not consume the slot either
    assert client.get(f"/api/schedules/{sid}/preview").status_code == 401
    assert client.get("/api/schedules/9999/preview", headers={"X-Api-Key": key}).status_code == 404


# --- manifest ----------------------------------------------------------------------------------
def test_manifest_is_public_and_generated_from_the_route_table(client):
    m = client.get("/api/agent/manifest").json()
    assert m["scopes"] == list(AU.SCOPES)
    assert {(r["method"], r["path"]) for r in m["routes"]} == {(e["method"], e["path"]) for e in AU.AGENT_ROUTES}
    for r in m["routes"]:
        assert r["scopes"] and all(s in AU.SCOPES for s in r["scopes"]) and r["side_effects"] and r["cost"]
    estimate = next(r for r in m["routes"] if r["path"] == "/api/crawl/estimate")
    assert "network" in estimate["cost"]                           # a live board walk is not a free read
    assert {t["type"] for t in m["envelope_types"]} == set(AU.INGEST_SCOPE)
    assert set(m["links"]) >= {"openapi", "ontology", "taxonomy", "facets", "schemas", "skill"}


def test_manifest_declares_every_api_route_exactly_once_and_agrees_with_the_middleware(client):
    """The manifest diffed against the middleware, route by route, with no hand-written list in between.
    Every /api route the app serves is in exactly one of `routes` (a scope opens it), `public` (nothing
    gates it) or `session_only` (gated, no scope opens it), and each bucket has to match what
    required_role() answers for that route. A manifest that disagrees is worse than none: an agent plans
    against it."""
    m = client.get("/api/agent/manifest").json()
    gates = gated(client.app)
    scoped = {(r["method"], r["path"]) for r in m["routes"]}
    public = {(r["method"], r["path"]) for r in m["public"]}
    session_only = {(r["method"], r["path"]): r["role"] for r in m["session_only"]}

    assert scoped <= set(gates), scoped - set(gates)                       # no route published that does not exist
    assert public == {k for k, role in gates.items() if role is None and k not in scoped}
    assert session_only == {k: role for k, role in gates.items() if role and k not in scoped}
    assert scoped | public | set(session_only) == set(gates)               # nothing undeclared
    assert len(scoped) + len(public) + len(session_only) == len(gates)     # and nothing declared twice
    for r in m["routes"]:                                                  # "needs no key today" is not guessed
        assert r["public"] == (gates[(r["method"], r["path"])] is None), r


def test_the_public_mechanics_catalogue_is_declared_as_public(client):
    """GET /api/mechanics and /api/mechanics/{mid} hand the whole de/en classification-rule catalogue, rule
    source included, to anyone. That is intended -- it is the documentation of how a row gets classified,
    and the two writes that shell out to pytest stay owner-gated -- so it has to be declared as public, not
    merely be public."""
    m = client.get("/api/agent/manifest").json()
    public = {(r["method"], r["path"]) for r in m["public"]}
    session_only = {(r["method"], r["path"]) for r in m["session_only"]}
    assert {("GET", "/api/mechanics"), ("GET", "/api/mechanics/{mid}")} <= public
    assert {("POST", "/api/mechanics/{mid}/try"), ("POST", "/api/mechanics/{mid}/test")} <= session_only
    assert client.get("/api/mechanics").status_code == 200                 # no key, no cookie, still 200


def test_ingest_schemas_are_generated_from_the_column_spec(client):
    from pflege_jobs.schema import CLINIC_SPEC
    s = client.get("/api/ingest/schemas").json()
    assert set(s["types"]) == set(AU.INGEST_SCOPE)
    clinic = s["types"]["clinic.upserted"]["data"]
    assert clinic["required"] == [c for c, _ in CLINIC_SPEC] and len(clinic["properties"]) == 17
    assert clinic["properties"]["beds"]["type"] == ["integer", "null"]
    assert s["types"]["posting.observed"]["target"] == "pflege_jobs.inbox (kind=jobposting)"
    assert s["envelope"]["required"] == ["id", "source", "type", "data"]


# --- ingest ------------------------------------------------------------------------------------
def test_ingest_writes_an_inbox_row_with_the_envelope_mapped_onto_it(client, inbox):
    key = key_for(client, "write:ingest:posting", label="post")
    r = client.post("/api/ingest", json=posting("e1", "https://bb.de/j/1"), headers={"X-Api-Key": key})
    assert r.status_code == 202, r.text
    assert r.json()["accepted"] == 1 and r.json()["results"][0]["status"] == "accepted"
    assert len(inbox) == 1
    row = inbox[0]
    assert row["kind"] == "jobposting" and row["collector"] == "test-collector-v1" and row["client_id"] == "post"
    assert row["source_url"] == "https://bb.de/j/1" and row["source_host"] == "bb.de"
    assert row["payload"]["clinic_id"] == "36201" and row["payload"]["event_id"] == "e1"


def test_ingest_dedupes_on_source_and_id_and_against_the_live_inbox(client, inbox):
    key = key_for(client, "write:ingest:posting", label="post")
    batch = {"events": [posting("e1", "https://bb.de/j/1"), posting("e1", "https://bb.de/j/1"),
                        posting("e2", "https://bb.de/j/2")]}
    r = client.post("/api/ingest", json=batch, headers={"X-Api-Key": key})
    assert r.status_code == 207                                     # mixed: two written, one duplicate
    assert [x["status"] for x in r.json()["results"]] == ["accepted", "duplicate", "accepted"]
    assert len(inbox) == 2
    again = client.post("/api/ingest", json=batch, headers={"X-Api-Key": key})
    assert again.status_code == 207 and again.json()["accepted"] == 0
    assert [x["status"] for x in again.json()["results"]] == ["duplicate"] * 3
    assert len(inbox) == 2                                          # the row already in the inbox is not re-posted


def test_ingest_validate_only_writes_nothing(client, inbox):
    key = key_for(client, "write:ingest:posting", label="post")
    before = len(inbox)
    body = {"validate_only": True, "events": [posting("e1", "https://bb.de/j/1"), posting("e2", "https://bb.de/j/2")]}
    r = client.post("/api/ingest", json=body, headers={"X-Api-Key": key})
    assert r.status_code == 202 and r.json()["validate_only"] is True and r.json()["accepted"] == 2
    assert [x["status"] for x in r.json()["results"]] == ["valid", "valid"]
    assert len(inbox) == before == 0
    bad = {"validate_only": True, "events": [envelope("e3", source_url="https://bb.de/j/3"), {"id": "e4", "source": "s"}]}
    r = client.post("/api/ingest", json=bad, headers={"X-Api-Key": key})
    assert r.status_code == 207 and [x["status"] for x in r.json()["results"]] == ["valid", "rejected"]
    assert len(inbox) == 0


def test_ingest_207_when_one_event_is_out_of_scope(client, inbox):
    """A key scoped to postings may write postings and nothing else -- the clinic event in the same batch
    is refused on its own, with the scope it would need, and the posting still lands."""
    key = key_for(client, "write:ingest:posting", label="post")
    clinic = envelope("c1", "clinic.upserted", **{"clinic_id": "36201"})
    r = client.post("/api/ingest", json={"events": [posting("e1", "https://bb.de/j/1"), clinic]},
                    headers={"X-Api-Key": key})
    assert r.status_code == 207 and r.json()["accepted"] == 1
    rejected = r.json()["results"][1]
    assert rejected["status"] == "rejected" and rejected["problem"]["scope"] == "write:ingest:clinic"
    assert rejected["problem"]["status"] == 403
    assert len(inbox) == 1


def test_ingest_rejects_a_partial_clinic_row_naming_the_missing_columns(client, inbox):
    from pflege_jobs.schema import CLINIC_SPEC
    key = key_for(client, "write:ingest:clinic", label="clin")
    r = client.post("/api/ingest", json=envelope("c1", "clinic.upserted", clinic_id="36201", name="BB"),
                    headers={"X-Api-Key": key})
    assert r.status_code == 207
    problem = r.json()["results"][0]["problem"]
    assert problem["status"] == 422 and "beds" in problem["missing"] and "clinic_id" not in problem["missing"]
    assert "full_clinic_rows" in problem["detail"]
    assert FakeSink.posted == []
    full = {c: None for c, _ in CLINIC_SPEC}
    full.update(clinic_id="36201", name="BB")
    # not envelope(**full): CLINIC_SPEC has a `source` column and the envelope has a `source` field
    r = client.post("/api/ingest", json={**envelope("c2", "clinic.upserted"), "data": full}, headers={"X-Api-Key": key})
    assert r.status_code == 202 and FakeSink.posted == [{"clinics": [full]}]


def test_ingest_rejects_an_unknown_type_and_an_empty_body(client):
    key = key_for(client, *AU.SCOPES, label="full")
    r = client.post("/api/ingest", json=envelope("x1", "posting.invented"), headers={"X-Api-Key": key})
    assert r.status_code == 207 and "unknown envelope type" in r.json()["results"][0]["problem"]["detail"]
    assert client.post("/api/ingest", json={"events": []}, headers={"X-Api-Key": key}).status_code == 400


def test_ingest_needs_a_key_or_a_session(client, inbox):
    assert client.post("/api/ingest", json=posting("e1", "https://bb.de/j/1")).status_code == 401
    assert len(inbox) == 0
    assert client.post("/api/auth/login", json={"user": USER, "pass": PASS}).status_code == 200
    r = client.post("/api/ingest", json=posting("e1", "https://bb.de/j/1"))   # owner session: every type
    assert r.status_code == 202 and inbox[0]["client_id"] == "owner-session"


# --- idempotency -------------------------------------------------------------------------------
def test_idempotent_crawl_replays_instead_of_queueing_a_second_paying_run(client):
    key = key_for(client, "spend:firecrawl")
    body = {"target": {"scope": "clinic", "values": ["36201"]}, "mode": "firecrawl", "max_credits": 10}
    h = {"X-Api-Key": key, "Idempotency-Key": "11111111-1111-1111-1111-111111111111"}
    first = client.post("/api/crawl", json=body, headers=h)
    assert first.status_code == 200
    second = client.post("/api/crawl", json=body, headers=h)
    assert second.status_code == 200 and second.json() == first.json()
    assert len(R.list_runs(50)) == 1                                # one run row, not two


def test_same_idempotency_key_with_a_different_body_is_422(client):
    key = key_for(client, "write:crawl")
    h = {"X-Api-Key": key, "Idempotency-Key": "22222222-2222-2222-2222-222222222222"}
    body = {"target": {"scope": "clinic", "values": ["36201"]}, "mode": "adapter"}
    assert client.post("/api/crawl", json=body, headers=h).status_code == 200
    r = client.post("/api/crawl", json={**body, "max_credits": 7}, headers=h)
    assert r.status_code == 422 and "different request body" in r.json()["detail"]
    assert len(R.list_runs(50)) == 1


def test_an_idempotency_key_still_in_flight_is_409(client):
    R.idem_begin(R.idem_scope("agent:write:crawl", "33333333-3333-3333-3333-333333333333"),
                 R.idem_fingerprint("POST", "/api/crawl", {"target": {"scope": "clinic", "values": ["36201"]}, "mode": "adapter"}))
    key = key_for(client, "write:crawl")            # key_for's default label is the scope list: this caller's namespace
    r = client.post("/api/crawl", json={"target": {"scope": "clinic", "values": ["36201"]}, "mode": "adapter"},
                    headers={"X-Api-Key": key, "Idempotency-Key": "33333333-3333-3333-3333-333333333333"})
    assert r.status_code == 409 and "in flight" in r.json()["detail"]
    assert len(R.list_runs(50)) == 0


def test_one_callers_idempotency_key_cannot_replay_or_block_anothers(client):
    """The key namespace is per caller. It used to be global: whoever guessed (or was told) another agent's
    Idempotency-Key got that agent's stored response back -- its run ids -- or, by claiming the key first,
    locked the other agent out with 409/422."""
    body = {"target": {"scope": "clinic", "values": ["36201"]}, "mode": "adapter"}
    h = {"Idempotency-Key": "66666666-6666-6666-6666-666666666666"}
    a, b = key_for(client, "write:crawl", label="a"), key_for(client, "write:crawl", label="b")
    first = client.post("/api/crawl", json=body, headers={**h, "X-Api-Key": a})
    second = client.post("/api/crawl", json=body, headers={**h, "X-Api-Key": b})
    assert first.status_code == second.status_code == 200, second.text
    assert second.json()["run_id"] != first.json()["run_id"]         # b did not read back a's response
    assert len(R.list_runs(50)) == 2                                 # and was not blocked by a's key either
    assert client.post("/api/crawl", json=body, headers={**h, "X-Api-Key": a}).json() == first.json()
    assert len(R.list_runs(50)) == 2                                 # a's own retry still replays a's answer
    other = client.post("/api/crawl", json={**body, "max_credits": 7}, headers={**h, "X-Api-Key": b})
    assert other.status_code == 422                                  # b's own reuse with a different body still conflicts


def test_idempotent_ingest_replays_the_same_per_item_results(client, inbox):
    key = key_for(client, "write:ingest:posting", label="post")
    h = {"X-Api-Key": key, "Idempotency-Key": "44444444-4444-4444-4444-444444444444"}
    body = {"events": [posting("e1", "https://bb.de/j/1")]}
    first = client.post("/api/ingest", json=body, headers=h)
    second = client.post("/api/ingest", json=body, headers=h)
    assert first.status_code == second.status_code == 202 and first.json() == second.json()
    assert len(inbox) == 1                                          # the replay wrote nothing


def test_validate_only_never_claims_an_idempotency_key(client, inbox):
    """A dry run wrote nothing, so it must not lock the key the real call is going to use."""
    key = key_for(client, "write:ingest:posting", label="post")
    h = {"X-Api-Key": key, "Idempotency-Key": "55555555-5555-5555-5555-555555555555"}
    body = {"events": [posting("e1", "https://bb.de/j/1")]}
    assert client.post("/api/ingest", json={**body, "validate_only": True}, headers=h).status_code == 202
    assert client.post("/api/ingest", json=body, headers=h).status_code == 202
    assert len(inbox) == 1


def test_crawl_validate_only_queues_nothing_and_returns_the_plan(client):
    key = key_for(client, "spend:firecrawl")
    body = {"target": {"scope": "clinic", "values": ["36201"]}, "mode": "firecrawl", "max_credits": 10, "validate_only": True}
    r = client.post("/api/crawl", json=body, headers={"X-Api-Key": key})
    assert r.status_code == 200 and r.json()["validate_only"] is True and r.json()["queued"] is False
    assert r.json()["via_firecrawl"] == 1 and r.json()["est_credits"] == 10 and "credits_left" in r.json()
    assert R.list_runs(50) == []
    assert client.post("/api/crawl", json={**body, "mode": "adapter", "validate_only": False},
                       headers={"X-Api-Key": key}).status_code == 403   # a dry run does not widen the scope check


# --- sparse projection / ndjson ----------------------------------------------------------------
def test_fields_projection_and_ndjson_on_the_two_list_reads(client):
    d = client.get("/api/jobs?fields=posting_id,title").json()
    assert d["rows"] == [{"posting_id": 1, "title": "Pflegefachkraft Intensiv"}] and d["fields"] == ["posting_id", "title"]
    assert client.get("/api/jobs?fields=posting_id,nope").status_code == 400
    d = client.get("/api/clinics?fields=clinic_id,beds").json()
    assert d["rows"] == [{"clinic_id": "36201", "beds": 985}]
    r = client.get("/api/clinics?fields=clinic_id", headers={"Accept": "application/x-ndjson"})
    assert r.headers["content-type"].startswith("application/x-ndjson")
    assert r.text == '{"clinic_id": "36201"}'
    r = client.get("/api/jobs", headers={"Accept": "application/x-ndjson"})
    assert '"posting_id": 1' in r.text and "total" not in r.text     # no envelope, just rows


# --- key management ----------------------------------------------------------------------------
def test_keys_are_labelled_scoped_and_never_echoed(client):
    reader = key_for(client, "read:ops", label="reader")
    writer = key_for(client, "write:crawl", label="writer")
    assert client.post("/api/auth/login", json={"user": USER, "pass": PASS}).status_code == 200
    status = client.get("/api/settings").json()["agent_key"]
    assert status["configured"] is True and {k["label"] for k in status["keys"]} == {"reader", "writer"}
    assert reader not in str(status) and writer not in str(status)
    assert AU._sha(reader) not in str(status) and AU._sha(writer) not in str(status)
    client.post("/api/auth/logout")
    assert client.get("/api/crawl/runs", headers={"X-Api-Key": reader}).status_code == 200
    assert client.get("/api/crawl/runs", headers={"X-Api-Key": writer}).status_code == 403


def test_minting_an_unknown_scope_is_422(client):
    assert client.post("/api/auth/login", json={"user": USER, "pass": PASS}).status_code == 200
    r = client.put("/api/settings/agent-key", params={"label": "x", "scopes": "read:board,write:everything"})
    assert r.status_code == 422 and "write:everything" in r.json()["detail"]


def test_deleting_one_label_leaves_the_other_keys_alone(client):
    reader = key_for(client, "read:ops", label="reader")
    writer = key_for(client, "write:crawl", label="writer")
    assert client.post("/api/auth/login", json={"user": USER, "pass": PASS}).status_code == 200
    assert client.delete("/api/settings/agent-key", params={"label": "reader"}).json()["configured"] is True
    client.post("/api/auth/logout")
    assert client.get("/api/crawl/runs", headers={"X-Api-Key": reader}).status_code == 401
    assert client.post("/api/inbox/drain", headers={"X-Api-Key": writer}).status_code == 200
