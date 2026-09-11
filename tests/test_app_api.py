"""Backend API tests: data layer stubbed (no network), SQLite in a temp dir."""
import re
import time

import pytest
from fastapi.testclient import TestClient

from app import config as A
from app import data as D
from app import mechanics as ME
from app import runs as R
from app import scheduler as S
from app import schedules as SC

CLINICS = [
    {"clinic_id": "36201", "name": "Krankenhaus Barmherzige Brüder", "town": "Regensburg", "operator": "BB gGmbH", "landkreis": "Kreisfreie Stadt Regensburg",
     "regierungsbezirk": "Oberpfalz", "status": "Plan-KH", "versorgungsstufe": "Maximalversorgung (III)", "traegerart": "freigemeinnuetzig", "beds": 985,
     "day_places": 27, "fachrichtungen": ["CHI", "INN"], "size": "XL", "website": "https://x", "careers_url": "https://www.barmherzige-regensburg.de/karriere/",
     "ats_type": "typo3_jobs", "jobs_open": 24, "jobs_fresh": 3, "jobs_live": 20, "routable": True, "walled": False, "board": "https://www.barmherzige-regensburg.de/karriere/",
     "vendor": "typo3_jobs", "route_reason": "adapter", "fetch": "adapter", "fetch_label": "typo3_jobs", "last_crawl_at": None, "last_crawl_status": None, "last_crawl_mode": None, "career_profile": None},
    {"clinic_id": "36202", "name": "Krankenhaus St. Josef", "town": "Regensburg", "operator": "St. Josef", "landkreis": "Kreisfreie Stadt Regensburg",
     "regierungsbezirk": "Oberpfalz", "status": "Plan-KH", "versorgungsstufe": "Schwerpunkt (II)", "traegerart": "freigemeinnuetzig", "beds": 400,
     "day_places": 0, "fachrichtungen": ["INN"], "size": "L", "website": "", "careers_url": "", "ats_type": "", "jobs_open": 0, "jobs_fresh": 0, "jobs_live": 0,
     "routable": False, "walled": False, "board": None, "vendor": None, "route_reason": "no careers_url", "fetch": "firecrawl", "fetch_label": "Firecrawl",
     "last_crawl_at": None, "last_crawl_status": None, "last_crawl_mode": None, "career_profile": None},
    {"clinic_id": "16104", "name": "kbo-Heckscher-Klinikum Ingolstadt", "town": "Ingolstadt", "operator": "kbo", "landkreis": "Kreisfreie Stadt Ingolstadt",
     "regierungsbezirk": "Oberbayern", "status": "Plan-KH", "versorgungsstufe": "Fachkrankenhaus", "traegerart": "oeffentlich", "beds": 0, "day_places": 15,
     "fachrichtungen": ["KJP"], "size": "S", "website": "", "careers_url": "https://kbo.de/jobs", "ats_type": "helios", "jobs_open": 3, "jobs_fresh": 1, "jobs_live": 3,
     "routable": False, "walled": True, "board": "https://kbo.de/jobs", "vendor": "helios", "route_reason": "walled host", "fetch": "firecrawl", "fetch_label": "Firecrawl",
     "last_crawl_at": None, "last_crawl_status": None, "last_crawl_mode": None, "career_profile": None},
]
JOBS = [{"posting_id": 1, "title": "Pflegefachkraft Intensiv", "clinic_id": "36201", "city": "Regensburg", "fresh": True, "first_published": "2026-09-05", "status": "open",
         "verify_status": "live", "role_class": "fachpflege", "employer": "BB", "clinic_town": "Regensburg", "clinic_size": "XL"}]
CSV_ROWS = [{"clinic_id": c["clinic_id"], "name": c["name"], "town": c["town"], "operator": c["operator"], "landkreis": c["landkreis"], "regierungsbezirk": c["regierungsbezirk"],
             "status": c["status"], "versorgungsstufe": c["versorgungsstufe"], "traegerart": c["traegerart"], "beds": str(c["beds"]), "day_places": str(c["day_places"]),
             "fachrichtungen": "|".join(c["fachrichtungen"]), "parse_quality": "ok", "source": "Krankenhausplan Bayern 2026 (51. Fortschreibung), StMGP",
             "website": c["website"], "careers_url": c["careers_url"], "ats_type": c["ats_type"]} for c in CLINICS]


def _classify(title):
    """demo"""
    return "pflegefachkraft" if "pflege" in title.lower() else "nicht_pflege"


FAKE_MECHANIC = {"id": "role_class", "title": {"de": "Rollenklasse", "en": "Role class"}, "description": {"de": "Titel -> role_class", "en": "title -> role_class"},
                 "patterns_section": "role", "inputs": [{"name": "title", "label": {"de": "Titel", "en": "Title"}, "example": "Pflegefachkraft (m/w/d)"}],
                 "test_file": "tests/test_app_api.py", "functions": [_classify],
                 "try": lambda inputs: {"result": _classify(inputs.get("title", "")), "rule": "demo"}}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    D._snap.update({"at": time.time(), "jobs": JOBS, "clinics": CLINICS, "by_clinic": {c["clinic_id"]: c for c in CLINICS},
                    "facets": {"cities": []}, "taxonomy": {"ats_types": {"typo3_jobs": {"label": "TYPO3 jobs"}}}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(D, "registry_csv_rows", lambda: CSV_ROWS)
    monkeypatch.setattr(ME, "_FAKE", [FAKE_MECHANIC])
    monkeypatch.setattr(R, "enqueue", lambda rid: None)            # never run a crawl in tests
    monkeypatch.setattr(S, "start", lambda: SC.init())
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_plan_rows_and_source(client):
    r = client.get("/api/plan?q=regensburg&sort=-beds&limit=1")
    d = r.json()
    assert r.status_code == 200 and d["total"] == 2 and d["rows"][0]["clinic_id"] == "36201"
    assert d["rows"][0]["fachrichtungen"] == ["CHI", "INN"] and d["source_url"].startswith("https://www.stmgp.bayern.de")
    assert "jobs_open" in d["columns"]


def test_cities(client):
    rows = client.get("/api/cities").json()
    rb = next(r for r in rows if r["city"] == "Regensburg")
    assert rb["clinics"] == 2 and rb["jobs_open"] == 24 and rb["ats_known"] == 1 and rb["regierungsbezirk"] == "Oberpfalz"
    assert client.get("/api/cities?q=ingol").json()[0]["city"] == "Ingolstadt"


def test_clinics_q_matches_badges_and_fetch(client):
    assert [c["clinic_id"] for c in client.get("/api/clinics?q=typo3").json()["rows"]] == ["36201"]
    assert [c["clinic_id"] for c in client.get("/api/clinics?q=KJP").json()["rows"]] == ["16104"]
    fire = client.get("/api/clinics?fetch=firecrawl").json()["rows"]
    assert {c["clinic_id"] for c in fire} == {"36202", "16104"} and all(c["fetch_label"] == "Firecrawl" for c in fire)


def test_clinics_sort_by_jobs_per_100_beds(client, monkeypatch):
    """Display-only sort for a human scanning the clinic list -- not read by any automated decision."""
    ratios = {"36201": 2.4, "36202": 0.0, "16104": None}         # 24/985, 0/400, N/A (beds=0)
    for c in D._snap["clinics"]:
        c["jobs_per_100_beds"] = ratios[c["clinic_id"]]
    r = client.get("/api/clinics?sort=jobs_per_100_beds").json()["rows"]
    assert [c["clinic_id"] for c in r] == ["36202", "36201", "16104"]     # ascending, None last
    r = client.get("/api/clinics?sort=-jobs_per_100_beds").json()["rows"]
    assert [c["clinic_id"] for c in r] == ["36201", "36202", "16104"]     # descending, None still last


def test_crawl_plan_targets(client):
    d = client.get("/api/crawl/plan?scope=city&values=Regensburg&mode=auto").json()
    assert d["clinics"] == 2 and d["via_adapter"] == 1 and d["via_firecrawl"] == 1 and d["est_credits"] == 40 and d["target"]["values"] == ["Regensburg"]
    d = client.get("/api/crawl/plan?scope=ats_type&values=firecrawl&mode=adapter").json()
    assert d["clinics"] == 2 and d["via_adapter"] == 0 and len(d["skipped"]) == 2
    assert client.get("/api/crawl/plan?scope=nope").status_code == 400
    r = client.post("/api/crawl", json={"target": {"scope": "clinic", "values": ["36201"]}, "mode": "adapter"})
    assert r.status_code == 200 and r.json()["boards"] == 1


def test_cancel_queued_run_marks_cancelled_immediately(client):
    """R.enqueue is stubbed in this fixture, so a created run stays 'queued' -- exactly the case a
    cancel click can land on before the (single, serial) worker ever picks it up."""
    rid = client.post("/api/crawl", json={"target": {"scope": "clinic", "values": ["36201"]}, "mode": "adapter"}).json()["run_id"]
    r = client.post(f"/api/crawl/runs/{rid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert client.get(f"/api/crawl/runs/{rid}").json()["status"] == "cancelled"


def test_cancel_running_run_sets_flag_not_status(client):
    """A running crawl can't be hard-killed mid-request -- cancel just flags it; app/crawl.py:execute()
    polls cancel_requested between boards/Firecrawl clinics and stops there."""
    rid = R.create_run("clinic", "36201", "adapter")
    R.update_run(rid, status="running", started_at=R.now())
    r = client.post(f"/api/crawl/runs/{rid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "running" and r.json()["cancel_requested"] == 1


def test_cancel_finished_run_409s(client):
    rid = R.create_run("clinic", "36201", "adapter")
    R.update_run(rid, status="done", finished_at=R.now())
    assert client.post(f"/api/crawl/runs/{rid}/cancel").status_code == 409


def test_cancel_unknown_run_404s(client):
    assert client.post("/api/crawl/runs/999999/cancel").status_code == 404
    r = client.post("/api/crawl", json={"scope": "regierungsbezirk", "value": "Oberpfalz", "mode": "auto"})   # legacy shape
    assert r.status_code == 200 and r.json()["clinics"] == 2
    assert client.post("/api/crawl", json={"target": {"scope": "all"}, "mode": "firecrawl"}).status_code == 400
    assert client.get("/api/crawl/runs").json()[0]["scope"] == "regierungsbezirk"


def test_schedules_crud_and_presets(client):
    seeded = client.get("/api/schedules").json()
    assert len(seeded) == 1 and seeded[0]["preset"] == "weekly_staggered" and seeded[0]["stagger_days"] == 7 and seeded[0]["cron"] == "0 3 * * *"
    assert "7" in seeded[0]["human"]["de"] and seeded[0]["next_run_at"]
    r = client.post("/api/schedules", json={"name": "Oberpfalz weekdays", "preset": "weekdays", "target": {"scope": "regierungsbezirk", "values": ["Oberpfalz"]}, "mode": "adapter"})
    s = r.json()
    assert r.status_code == 200 and s["cron"] == "0 3 * * 1-5" and s["stagger_days"] == 1 and "Oberpfalz" in s["target_summary"]["en"]
    r = client.put(f"/api/schedules/{s['id']}", json={"preset": "custom", "cron": "30 6 * * 1", "enabled": False})
    assert r.status_code == 200 and r.json()["cron"] == "30 6 * * 1" and r.json()["enabled"] is False and r.json()["next_run_at"] is None
    assert client.put(f"/api/schedules/{s['id']}", json={"preset": "custom", "cron": "not a cron"}).status_code == 422
    assert client.post(f"/api/schedules/{s['id']}/run-now").json()["queued"] is True
    assert client.delete(f"/api/schedules/{s['id']}").status_code == 200
    assert len(client.get("/api/schedules").json()) == 1
    assert client.get("/api/stats").json()["next_autocrawl"]


def test_schedule_refuses_scope_all_firecrawl(client):
    """Mirrors the /api/crawl guard: a schedule must not be able to bypass it via scope=all + mode=firecrawl,
    since fire() would otherwise stagger every hospital through the paid agent over the run cycle."""
    r = client.post("/api/schedules", json={"name": "danger", "preset": "custom", "cron": "0 3 * * *",
                                             "target": {"scope": "all"}, "mode": "firecrawl"})
    assert r.status_code == 422
    r = client.put("/api/schedules/1", json={"mode": "firecrawl"})
    assert r.status_code == 422


def test_mechanics_list_try_test(client):
    ms = client.get("/api/mechanics").json()
    assert ms[0]["id"] == "role_class" and "def _classify" in ms[0]["functions"][0]["source"] and ms[0]["n_tests"] >= 5
    r = client.post("/api/mechanics/role_class/try", json={"inputs": {"title": "Pflegefachkraft"}})
    assert r.json() == {"result": "pflegefachkraft", "rule": "demo"}
    assert client.post("/api/mechanics/nope/try", json={}).status_code == 404


def test_removed_routes(client):
    assert client.get("/agents.md").status_code == 404
    assert client.get("/llms.txt").status_code == 404


def test_ui_routes(client):
    for path in ("/", "/pro", "/pro/"):        # 503 is the documented unbuilt-tree answer
        assert client.get(path).status_code in (200, 503)
    assert client.get("/simple").status_code == 404
    home = client.get("/")
    if home.status_code == 200:
        assert 'href="/pro"' in home.text and 'content="light"' in home.text
        assert 'content="dark"' in client.get("/pro").text


# --- error shape: RFC 9457 problem details (docs/errors.md) ----------------------------------


def test_problem_json_404(client):
    r = client.get("/api/clinics/99999")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")
    b = r.json()
    assert b["type"] == "/docs/errors.md#unknown-clinic" and b["title"] == "Unknown clinic"
    assert b["status"] == 404 and b["detail"] == "unknown clinic" and b["instance"] == "/api/clinics/99999"
    assert b["error"] == b["detail"]        # legacy key; web/index.template.html:237 and pro:397 read it
    assert client.get("/api/jobs/424242").json()["type"] == "/docs/errors.md#unknown-posting"


def test_problem_json_400_and_409(client):
    b = client.post("/api/crawl", json={"target": {"scope": "clinic", "values": ["36201"]}, "mode": "nope"}).json()
    assert b["status"] == 400 and b["type"] == "/docs/errors.md#invalid-body" and "mode must be one of" in b["detail"]
    rid = R.create_run("clinic", "36201", "adapter")
    R.update_run(rid, status="done", finished_at=R.now())
    r = client.post(f"/api/crawl/runs/{rid}/cancel")
    b = r.json()
    assert r.status_code == 409 and b["type"] == "/docs/errors.md#run-in-progress" and b["instance"] == f"/api/crawl/runs/{rid}/cancel"


def test_problem_json_untyped_falls_back_to_about_blank(client):
    b = client.get("/docs/nope.md").json()
    assert b["type"] == "about:blank" and b["title"] == "Not Found" and b["detail"] == "not found"


def test_problem_type_slugs():
    """The handlers see only status + detail, so every slug is decided here. Detail wording is the input:
    change an HTTPException message and the type changes with it (docs/errors.md)."""
    from app.main import _problem_type
    assert _problem_type(401, "sign in required") == "unauthenticated"
    assert _problem_type(403, "scope spend:firecrawl required") == "insufficient_scope"
    assert _problem_type(429, "too many requests for this e-mail; try again later") == "quota-exceeded"
    assert _problem_type(422, "cron does not parse") == "invalid-body"
    assert _problem_type(409, "firecrawl budget exhausted (0 credits left this week)") == "budget-exceeded"
    assert _problem_type(409, "adapter covers it") == "adapter-covers-it"
    assert _problem_type(409, "a run is already queued or running; try again once it finishes") == "run-in-progress"
    assert _problem_type(404, "unknown schedule") == "unknown-schedule"
    assert _problem_type(404, "PDF not on this host (see data/registry/README.md)") == "about:blank"


# --- bad query parameters: 400, never 500 (2026-09-10 security pass) --------------------------
# Every route that reads a numeric query parameter, and every numeric parameter on it. Nine of these pairs
# answered 500 with the Python exception echoed back to an anonymous caller (`GET /api/jobs?limit=abc` ->
# `ValueError: invalid literal for int() with base 10: 'abc'`): limit/offset/fresh_days on /api/jobs,
# limit/offset/beds_min/beds_max on /api/clinics, limit/offset on /api/plan. The rest were already 422 from
# FastAPI's own declared-int coercion and are walked here so the two halves cannot drift apart.
# (path, {other query params the route needs to get past its own validation}, [numeric params to fuzz])
NUMERIC_QUERY_PARAMS = [("/api/jobs", {}, ["limit", "offset", "fresh_days"]),
                        ("/api/clinics", {}, ["limit", "offset", "beds_min", "beds_max"]),
                        ("/api/plan", {}, ["limit", "offset"]),
                        ("/api/search", {"q": "pflege"}, ["limit"]),
                        ("/api/crawl/runs", {}, ["limit"]),
                        ("/api/crawl/plan", {"scope": "clinic", "values": "36201"}, ["max_credits"]),
                        ("/api/schedules/1/preview", {}, ["day"]),
                        # The autopilot router parsed its own query parameters with a bare int() until
                        # 2026-09-11 (app/autopilot/api.py:_page and the two id filters). It only ever
                        # answered 400 rather than 500 because conn() happens to translate ValueError --
                        # and OverflowError, which is not a ValueError, went out as a 500.
                        ("/api/autopilot/candidates", {}, ["limit", "offset"]),
                        ("/api/autopilot/conversations", {}, ["limit", "offset"]),
                        ("/api/autopilot/cohorts", {}, ["limit", "offset"]),
                        ("/api/autopilot/approvals", {}, ["limit", "offset"]),
                        ("/api/autopilot/queue", {}, ["limit", "offset"]),
                        ("/api/autopilot/interviews", {}, ["limit", "offset"]),
                        ("/api/autopilot/matches", {}, ["limit", "offset", "candidate_id", "cohort_id"]),
                        ("/api/autopilot/clinic-threads", {}, ["limit", "offset", "cohort_id"])]
# Shapes a caller (or a broken UI, or a scanner) really sends. "" is the one that must stay a 200: `?limit=`
# means "no value", which is what the old `int(x or 100)` did too. " " is not the same thing -- the callers
# test the raw string for truthiness before parsing, so whitespace has to be a 400, not "absent".
GARBAGE_VALUES = ["abc", "1e5", "-1", "9" * 20, "1,2", "null", "NaN", "0x10", " ", "[]", "1;drop"]


@pytest.fixture()
def autopilot_tmp(tmp_path, monkeypatch):
    """app/autopilot/db.py binds SQLITE_PATH at import time, so monkeypatching A.DATA_DIR does not move it --
    without this the walk below would seed the real data/autopilot.sqlite."""
    from app.autopilot import api as AP
    from app.autopilot import db as APDB
    monkeypatch.setattr(APDB, "SQLITE_PATH", tmp_path / "autopilot.sqlite")
    monkeypatch.setattr(AP, "_ready", {"ok": False})
    return tmp_path


@pytest.mark.parametrize("path,base,params", NUMERIC_QUERY_PARAMS)
def test_garbage_numeric_query_params_are_4xx_naming_the_parameter(client, autopilot_tmp, path, base, params):
    assert client.get(path, params=base).status_code == 200, (path, "base query must be valid on its own")
    for param in params:
        for bad in GARBAGE_VALUES:
            r = client.get(path, params={**base, param: bad})
            assert r.status_code < 500, (path, param, bad, r.status_code, r.text[:200])
            if r.status_code >= 400:
                assert param in r.text, (path, param, bad, r.text[:200])


@pytest.mark.parametrize("path,base,params", NUMERIC_QUERY_PARAMS[:3])
def test_an_empty_numeric_query_param_still_means_the_default(client, path, base, params):
    """`?limit=` is "no value", which is what the old `int(x or 100)` did -- the 400 is for a value that is
    there and is not a number. (The FastAPI-declared ints on the other routes have always answered 422 to an
    empty string; that is their coercion, not this one.)"""
    for param in params:
        assert client.get(path, params={**base, param: ""}).status_code == 200, (path, param)


def test_page_400_is_problem_json_and_says_what_was_wrong(client):
    r = client.get("/api/jobs?limit=abc")
    assert r.status_code == 400 and r.headers["content-type"].startswith("application/problem+json")
    b = r.json()
    assert b["type"] == "/docs/errors.md#invalid-body" and b["detail"] == "limit must be an integer, got 'abc'"
    assert b["instance"] == "/api/jobs" and b["error"] == b["detail"]
    assert client.get("/api/clinics?beds_min=x").json()["detail"].startswith("beds_min must be an integer")
    # int() succeeds, timedelta() does not -- the second way fresh_days reached a 500. 9**15 days, not
    # 9**20: above 2**63 D.int_param answers first now (next line), because such a value cannot be bound
    # into a SQLite INTEGER at all -- three autopilot (route, parameter) pairs 500ed on exactly that.
    assert client.get("/api/jobs?fresh_days=" + "9" * 15).json()["detail"].startswith("fresh_days=9")
    assert client.get("/api/jobs?fresh_days=" + "9" * 20).json()["detail"] == \
        "fresh_days must fit in a 64-bit integer, got '99999999999999999999'"
    assert client.get("/api/jobs?limit=5&offset=1").status_code == 200          # the good path still works


def test_no_query_parameter_is_parsed_with_a_bare_int(client):
    """The walk above only covers the routes listed in it. This is what stops the class coming back on a
    route nobody thought to add: reading a query parameter straight into int() is how all nine 500s
    happened, so `int(p[...])` / `int(p.get(...))` must not reappear in any of these modules. D.int_param is
    the one door, and it answers 400. autopilot/api.py joined the walk on 2026-09-11: the "one shared parser"
    claim did not hold for it, and it names its query dict `q`, which is why the pattern covers both."""
    import pathlib
    src = "".join((pathlib.Path(__file__).resolve().parent.parent / "app" / m).read_text(encoding="utf-8")
                  for m in ("main.py", "data.py", "autopilot/api.py"))
    assert not re.search(r"\bint\(\s*[pq](?:\.get\(|\[)", src), "parse query parameters with D.int_param, not int()"
    assert "def int_param" in src


def test_ingest_schemas_only_use_types_the_validator_knows(client):
    """_schema_errors() looks every published `type` up in JSON_TYPE_OK and raises KeyError on one it does
    not know -- deliberately loud rather than silently accepting. This is where that gets to be loud offline
    instead of as a 500 on a live POST /api/ingest."""
    from app.main import JSON_TYPE_OK, _schema_errors

    def walk(s):
        t = s.get("type")
        for name in ([t] if isinstance(t, str) else list(t or ())):
            assert name in JSON_TYPE_OK, name
        for sub in list((s.get("properties") or {}).values()) + ([s["items"]] if s.get("items") else []):
            walk(sub)

    doc = client.get("/api/ingest/schemas").json()
    walk(doc["envelope"])
    for t in doc["types"].values():
        walk(t["data"])
    # and it is the object the handler enforces, not a second copy of it
    from app.main import ENVELOPE_SCHEMA
    assert doc["envelope"] == ENVELOPE_SCHEMA
    full = {"id": "e1", "source": "s", "type": "posting.observed", "data": {}}
    assert _schema_errors(full, ENVELOPE_SCHEMA, "envelope") is None
    assert _schema_errors({**full, "id": 5}, ENVELOPE_SCHEMA, "envelope") == "envelope.id must be string, got int"
    assert _schema_errors({"source": "s"}, ENVELOPE_SCHEMA, "envelope") == "envelope.id is required"


# --- security response headers ---------------------------------------------------------------
def test_every_response_carries_the_security_headers(client):
    """No response carried CSP / X-Frame-Options / X-Content-Type-Options until 2026-09-10. The policy is
    deliberately not strict -- these pages are one inline <script> each -- so this asserts what it really
    is, including 'unsafe-inline', rather than a policy nobody ships."""
    for path in ("/api/stats", "/api/jobs", "/health", "/", "/api/clinics/99999"):
        h = client.get(path).headers
        assert h["x-content-type-options"] == "nosniff", path
        assert h["x-frame-options"] == "DENY", path
        assert h["referrer-policy"] == "no-referrer", path
        csp = h["content-security-policy"]
        assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "connect-src 'self'" in csp
        assert "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com" in csp
        assert "default-src 'self'" in csp
        # img-src stays open to https: on purpose -- clinicPhoto() reads each hospital's own favicon off
        # that hospital's site. Verified in Chromium: 'self' data: alone blocked every clinic mark on /.
        assert "img-src 'self' data: https:" in csp


def test_pro_page_pins_both_cdn_scripts_with_sri(client):
    """web/pro.template.html loaded marked and cytoscape from cdnjs with no integrity and no crossorigin.
    Without crossorigin the integrity attribute is inert on a cross-origin script, so both are required."""
    pro = client.get("/pro")
    if pro.status_code == 503:
        pytest.skip("web/pro.html not built")
    for tag in re.findall(r"<script[^>]*cdnjs\.cloudflare\.com[^>]*>", pro.text):
        assert "integrity=\"sha512-" in tag, tag
        assert 'crossorigin="anonymous"' in tag, tag
    assert len(re.findall(r"<script[^>]*cdnjs\.cloudflare\.com[^>]*>", pro.text)) == 2


def test_skill_route_does_not_serve_compiled_bytecode(client):
    """GET /skill/__pycache__/query.cpython-312.pyc was 200 to anonymous callers -- web/skill/ is a real
    directory and importing query.py next to it leaves a __pycache__."""
    assert client.get("/skill/__pycache__/query.cpython-312.pyc").status_code == 404
    assert client.get("/skill/query.pyc").status_code == 404
    assert client.get("/skill/../main.py").status_code == 404
    for name in ("SKILL.md", "query.py"):                       # the bundle itself still serves
        assert client.get("/skill/" + name).status_code in (200, 404)


# --- malformed JSON bodies: 400, never 500 (2026-09-11) ---------------------------------------
# `await request.json()` raises on a body that is not JSON, and the `body.get(...)` that always follows
# raises on a JSON scalar or list. 41 (route, shape) pairs answered 500 with the Python exception echoed
# back; the auth doors among them are walked in tests/test_auth.py, these are the rest.
JSON_BODY_ROUTES = [("POST", "/api/crawl"),
                    ("POST", "/api/clinics/36201/refetch-career"),
                    ("POST", "/api/schedules"),
                    ("PUT", "/api/schedules/1"),
                    ("PUT", "/api/settings/patterns"),
                    ("POST", "/api/settings/patterns/validate"),
                    ("PUT", "/api/settings/firecrawl")]
MALFORMED_BODIES = [("not JSON", b"notjson"), ("a JSON string", b'"hi"'), ("a JSON number", b"5"),
                    ("null", b"null"), ("a list", b"[1,2]"), ("a nested list", b'[{"mode":"adapter"}]')]


@pytest.mark.parametrize("method,path", JSON_BODY_ROUTES, ids=[f"{m} {p}" for m, p in JSON_BODY_ROUTES])
@pytest.mark.parametrize("name,payload", MALFORMED_BODIES, ids=[c[0] for c in MALFORMED_BODIES])
def test_a_malformed_json_body_is_400_not_500(client, method, path, name, payload):
    r = client.request(method, path, content=payload, headers={"content-type": "application/json"})
    assert r.status_code == 400, (method, path, name, r.status_code, r.text[:200])
    assert r.headers["content-type"].startswith("application/problem+json"), (path, name)
    assert r.json()["detail"].startswith("body must be"), (path, name, r.json())


def test_only_the_known_routes_read_a_json_body_without_the_shared_door(client):
    """D.json_body is the door, the way D.int_param is for query parameters. Three routes keep their own
    reader on purpose and all three already answer 4xx on every malformed shape (they were never in the 500
    set): POST /api/ingest needs to tell "not JSON" from "not an envelope" from '"events" is not a list' in
    its own words; POST /api/mechanics/{mid}/try isinstance-checks the parsed body itself; PUT
    /api/settings/flags and POST /api/campaign wrap it in `except ValueError`, which JSONDecodeError is a
    subclass of, and tests/test_settings_flags.py pins that route's 422. The number is the tripwire: a new
    route that reaches for request.json() lands here before it lands in production."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    assert src.count("await request.json()") == 4, "read JSON bodies with D.json_body, not request.json()"


def test_a_name_the_filesystem_cannot_hold_is_404_not_500(client):
    """Two anonymous 500s of one family, both live on production until 2026-09-11: a NUL byte passed the
    ".." and extension checks and raised ValueError out of Path.resolve(), and a 300-character name raised
    `OSError: [Errno 36] File name too long` out of Path.exists(). /skill and /docs share the shape --
    /docs/{name} survived the NUL only because Path.exists() happens to swallow ValueError, and did not
    survive the long one. Found by the anonymous parameter fuzz, not by the ticket."""
    long_name = "a" * 300
    for path in ("/skill/query.py%00.md", "/skill/%00.md", "/skill/SKILL.md%00.py",
                 "/docs/api.md%00.md", "/docs/%00.json", "/docs/auth.md%00",
                 f"/skill/{long_name}.md", f"/skill/{long_name}.py", f"/docs/{long_name}.md",
                 f"/docs/{long_name}.json", "/skill/" + "b" * 80 + "/" + "c" * 300 + ".md"):
        r = client.get(path)
        assert r.status_code == 404, (path[:60], r.status_code, r.text[:200])
    assert client.get("/docs/auth.md").status_code == 200                  # the real doc still serves
    assert client.get("/skill/SKILL.md").status_code in (200, 404)         # web/skill/ may not be built


# --- PUT /api/settings/patterns must not be able to destroy patterns.json (2026-09-11) --------
# What happened: an empty body is `{}` (app/data.py:json_body, on purpose), validate_patterns() only asked
# "do the regexes compile?", an object with no regexes has none that do not -- so {} was written over
# pflege_jobs/patterns.json (14,131 bytes, 9 sections -> 2 bytes) and answered 200 {"reloaded": false}.
# Every classifier then failed with ValueError: patterns: missing section 'employer'.
@pytest.fixture()
def patterns_tmp(tmp_path, monkeypatch):
    """The route's target file, pointed at a COPY. Both paths, because app/settings.py:_patterns_path()
    prefers pflege_jobs.config.PATTERNS_PATH and falls back to app.config.PATTERNS_PATH -- patch one only
    and the test writes the repository's real patterns.json, which is how it got destroyed in the first
    place. Yields the temp path; the fixture itself asserts nothing."""
    import pathlib
    import shutil

    from pflege_jobs import config as C
    real = pathlib.Path(C.__file__).resolve().parent / "patterns.json"
    copy = tmp_path / "patterns.json"
    shutil.copy(real, copy)
    monkeypatch.setattr(A, "PATTERNS_PATH", copy)
    monkeypatch.setattr(C, "PATTERNS_PATH", str(copy))
    return copy


NOT_A_PATTERN_DOCUMENT = [
    ("an empty body", b"", None),
    ("an empty object", None, {}),
    ("a partial document", None, {"version": 9}),
    ("null", b"null", None),
    ("a list", b"[1,2]", None),
]


@pytest.mark.parametrize("name,raw,doc", NOT_A_PATTERN_DOCUMENT, ids=[c[0] for c in NOT_A_PATTERN_DOCUMENT])
def test_a_body_that_is_not_a_pattern_document_writes_nothing(client, patterns_tmp, name, raw, doc):
    before = patterns_tmp.read_bytes()
    kw = {"content": raw} if raw is not None else {"json": doc}
    r = client.put("/api/settings/patterns", **kw)
    assert 400 <= r.status_code < 500, (name, r.status_code, r.text[:300])
    assert r.headers["content-type"].startswith("application/problem+json"), name
    assert patterns_tmp.read_bytes() == before, f"{name} rewrote patterns.json"


def test_a_section_of_the_wrong_shape_is_422_and_writes_nothing(client, patterns_tmp):
    """The structural check is pflege_jobs/config.py:validate(), which indexes into the document, so a
    section that is present but the wrong shape raises KeyError/TypeError rather than ValueError. Those are
    a caller mistake too and must be a 422, not a 500 with a Python traceback in the body."""
    import json
    good = json.loads(patterns_tmp.read_text(encoding="utf-8"))
    before = patterns_tmp.read_bytes()
    for name, bad in [("employer as a list", {**good, "employer": []}),
                      ("role without rules", {**good, "role": {k: v for k, v in good["role"].items() if k != "rules"}}),
                      ("a regex that does not compile", {**good, "employer": {**good["employer"], "legal_forms": "(unclosed"}})]:
        r = client.put("/api/settings/patterns", json=bad)
        assert r.status_code == 422, (name, r.status_code, r.text[:300])
        assert patterns_tmp.read_bytes() == before, f"{name} rewrote patterns.json"


def test_the_real_document_still_saves_and_reloads(client, patterns_tmp):
    """The green half: the fix rejects non-documents, it does not break the route."""
    import json
    good = json.loads(patterns_tmp.read_text(encoding="utf-8"))
    r = client.put("/api/settings/patterns", json=good)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["reloaded"] is True, r.json()
    assert json.loads(patterns_tmp.read_text(encoding="utf-8")) == good


def test_the_validate_endpoint_sees_the_same_errors_as_the_write(client, patterns_tmp):
    """POST /api/settings/patterns/validate is the dry run for the PUT. If it says a document is fine and the
    PUT then refuses it (or the other way round) the dry run is worthless."""
    r = client.post("/api/settings/patterns/validate", json={})
    assert r.status_code == 200 and r.json()["errors"], r.json()
    assert "employer" in r.json()["errors"][0]


# --- POST /api/cv: a form body is a 4xx, not RuntimeError: Stream consumed (2026-09-11) -------
EMPTY_MULTIPART = b"--xx--\r\n"          # well formed, and carries no parts at all
FORM_BODIES = [("application/x-www-form-urlencoded", b"a=b"),
               ("application/x-www-form-urlencoded", b""),
               ("multipart/form-data; boundary=xx", EMPTY_MULTIPART),
               ("MULTIPART/FORM-DATA; boundary=xx", EMPTY_MULTIPART)]


@pytest.mark.parametrize("ct,payload", FORM_BODIES, ids=[f"{c} {len(b)}B" for c, b in FORM_BODIES])
def test_a_form_body_without_a_file_part_is_a_4xx_naming_what_the_route_accepts(client, ct, payload):
    """`file: UploadFile = File(None)` makes FastAPI parse the form for any form content type, consuming the
    stream; the handler's `await request.body()` then raised RuntimeError: Stream consumed -> 500. The only
    genuine crash left after ~197,000 fuzz requests, and it hit members and the owner alike."""
    r = client.post("/api/cv", content=payload, headers={"content-type": ct})
    assert r.status_code == 400, (ct, r.status_code, r.text[:200])
    assert r.headers["content-type"].startswith("application/problem+json"), ct
    detail = r.json()["detail"]
    for shape in ("multipart/form-data", "file=<pdf|docx|txt>", "application/json"):
        assert shape in detail, (ct, detail)


def test_a_multipart_body_the_parser_cannot_read_is_also_a_4xx(client):
    """The sibling shape: a multipart header whose body is not multipart at all never reaches the handler --
    Starlette's own parser answers first. Pinned because it is the other half of "no form body is a 500"."""
    for ct in ("multipart/form-data", "multipart/form-data; boundary=xx"):
        r = client.post("/api/cv", content=b"a=b", headers={"content-type": ct})
        assert r.status_code == 400, (ct, r.status_code, r.text[:200])
        assert r.headers["content-type"].startswith("application/problem+json"), ct


CV_TEXT = "Pflegefachkraft Intensiv, 5 Jahre Erfahrung, Deutsch C1, GuK examiniert seit 2019."


def test_the_three_shapes_the_cv_route_documents_all_still_work(client):
    for name, kw in [("multipart with a file part", {"files": {"file": ("cv.txt", CV_TEXT.encode(), "text/plain")}}),
                     ("json {text}", {"json": {"text": CV_TEXT}}),
                     ("the raw document as the body", {"content": CV_TEXT.encode(), "headers": {"content-type": "text/plain"}})]:
        r = client.post("/api/cv", **kw)
        assert r.status_code == 200, (name, r.status_code, r.text[:200])
        assert r.json()["chars"] == len(CV_TEXT), name


# --- POST /api/ingest: the envelope id is bounded, and the bound is published (2026-09-11) ----
def _envelope(eid="e1", source="probe"):
    return {"id": eid, "source": source, "type": "posting.observed",
            "data": {"source_url": "https://example.org/j/1", "payload": {}}}


def test_an_over_long_envelope_id_is_rejected_not_written(client):
    """A 200,000-character id used to be accepted (202, accepted 1/1) and copied straight into the row. The
    jsonb column it lands in has no limit worth calling one, so the limit is a published one: it is in the
    schema GET /api/ingest/schemas serves and it is enforced here as an ordinary 422 -- never truncated."""
    from app.main import ID_MAX
    for field, e in [("id", _envelope(eid="x" * (ID_MAX + 1))), ("source", _envelope(source="s" * 100000))]:
        r = client.post("/api/ingest", json={"events": [e], "validate_only": True})
        item = r.json()["results"][0]
        assert item["status"] == "rejected", (field, item)
        assert item["problem"]["status"] == 422, (field, item)
        assert f"envelope.{field} must be at most {ID_MAX} characters" in item["problem"]["detail"], item
    ok = client.post("/api/ingest", json={"events": [_envelope(eid="x" * ID_MAX)], "validate_only": True})
    assert ok.json()["results"][0]["status"] == "valid", ok.json()


def test_the_published_schema_states_the_bound_it_enforces(client):
    """The bound has to be readable by a caller, or it is an invented cap with a 422 attached."""
    from app.main import ID_MAX
    props = client.get("/api/ingest/schemas").json()["envelope"]["properties"]
    for field in ("id", "source"):
        assert props[field]["maxLength"] == ID_MAX, props[field]
        assert str(ID_MAX) in props[field]["description"], props[field]


def test_a_validating_document_cannot_silently_drop_a_section(client, patterns_tmp):
    """The second half of the same incident, and the one that does not look like a bug: PUT replaces the
    whole document and pflege_jobs/config.py:validate() only requires the five sections classify.py cannot
    start without. A body carrying exactly those five used to pass, write and answer 200 {"reloaded": true}
    -- measured: `cv` gone (app/cv.py's regexes) and `excluded_role_classes` gone, which silently swapped the
    INTAKE POLICY for pflege_jobs/config.py:_apply's 3-entry default, i.e. `pflegehelfer` postings would
    start passing sinks.only_pflege(). Emptying a section on purpose still works."""
    import json
    from app import settings as ST
    good = json.loads(patterns_tmp.read_text(encoding="utf-8"))
    before = patterns_tmp.read_bytes()
    r = client.put("/api/settings/patterns", json={k: good[k] for k in ST.REQUIRED_SECTIONS})
    assert r.status_code == 422, r.text[:300]
    for section in ("cv", "excluded_role_classes"):
        assert section in r.json()["detail"], r.json()["detail"]
    assert patterns_tmp.read_bytes() == before
    assert json.loads(patterns_tmp.read_text(encoding="utf-8"))["excluded_role_classes"] == good["excluded_role_classes"]

    emptied = {**good, "cv": {}, "excluded_role_classes": []}       # explicit is still allowed
    assert client.put("/api/settings/patterns", json=emptied).status_code == 200
    assert json.loads(patterns_tmp.read_text(encoding="utf-8")) == emptied
    client.put("/api/settings/patterns", json=good)                 # leave the loaded document as it was
