"""Backend API tests: data layer stubbed (no network), SQLite in a temp dir."""
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
