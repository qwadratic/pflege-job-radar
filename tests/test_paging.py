"""The read contract for paged routes: `limit` is what the caller asked for, `next_offset` says whether
that was everything, and neither answer is ever a silent truncation.

Regression for the cap proven live on 2026-09-11: GET /api/jobs?limit=999999 served 2000 of 2725 open
postings and reported {"total": 2725, "limit": 2000} -- the truncation echoed back as if it were the
request, and nothing at all on the NDJSON path.
"""
import time

import pytest
from fastapi.testclient import TestClient

from app import config as A
from app import data as D
from app import runs as R
from app import scheduler as S
from app import schedules as SC

N = 2552                                      # > the old 2000 ceiling, and not a multiple of any page size used here
JOBS = [{"posting_id": i, "title": f"Pflegefachkraft {i}", "clinic_id": "36201", "city": "Regensburg", "fresh": True,
         "first_published": "2026-09-05", "status": "open", "verify_status": "live", "role_class": "fachpflege",
         "employer": "BB", "clinic_town": "Regensburg", "clinic_size": "XL", "enr_contact_emails": None}
        for i in range(1, N + 1)]
CLINICS = [{"clinic_id": "36201", "name": "Krankenhaus Barmherzige Brüder", "town": "Regensburg", "operator": "BB",
            "landkreis": "Stadt Regensburg", "regierungsbezirk": "Oberpfalz", "status": "Plan-KH",
            "versorgungsstufe": "Maximalversorgung (III)", "traegerart": "freigemeinnuetzig", "beds": 985,
            "day_places": 27, "fachrichtungen": ["CHI"], "size": "XL", "website": "", "careers_url": "",
            "ats_type": "", "jobs_open": N, "jobs_fresh": N, "jobs_live": N, "routable": False, "walled": False,
            "board": None, "vendor": None, "route_reason": "x", "fetch": "firecrawl", "fetch_label": "Firecrawl",
            "last_crawl_at": None, "last_crawl_status": None, "last_crawl_mode": None, "career_profile": None}]
CSV_ROWS = [{"clinic_id": "36201", "name": "Krankenhaus Barmherzige Brüder", "town": "Regensburg", "operator": "BB",
             "landkreis": "Stadt Regensburg", "regierungsbezirk": "Oberpfalz", "status": "Plan-KH",
             "versorgungsstufe": "Maximalversorgung (III)", "traegerart": "freigemeinnuetzig", "beds": "985",
             "day_places": "27", "fachrichtungen": "CHI", "parse_quality": "ok", "source": "Krankenhausplan",
             "website": "", "careers_url": "", "ats_type": ""}]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    D._snap.update({"at": time.time(), "jobs": JOBS, "clinics": CLINICS,
                    "by_clinic": {c["clinic_id"]: c for c in CLINICS}, "facets": {}, "taxonomy": {},
                    "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(D, "registry_csv_rows", lambda: CSV_ROWS)
    monkeypatch.setattr(R, "enqueue", lambda rid: None)            # never run a crawl in tests
    monkeypatch.setattr(S, "start", lambda: SC.init())
    from app.main import app
    with TestClient(app) as c:
        yield c


# --- the cap ------------------------------------------------------------------------------------
def test_asking_for_everything_gets_everything(client):
    """The reported bug, with the row count that reproduced it."""
    d = client.get("/api/jobs?limit=999999").json()
    assert d["total"] == N
    assert len(d["rows"]) == N                 # was 2000
    assert d["limit"] == 999999                # was 2000: the truncation reported as the request
    assert d["next_offset"] is None            # "that was everything"


def test_page_that_is_not_everything_says_so(client):
    d = client.get("/api/jobs?limit=2000").json()
    assert (len(d["rows"]), d["limit"], d["next_offset"]) == (2000, 2000, 2000)
    rest = client.get(f"/api/jobs?limit=2000&offset={d['next_offset']}").json()
    assert (len(rest["rows"]), rest["next_offset"]) == (N - 2000, None)
    ids = [r["posting_id"] for r in d["rows"]] + [r["posting_id"] for r in rest["rows"]]
    assert ids == list(range(1, N + 1))        # the two pages are the whole list, no gap, no repeat


def test_full_last_page_is_still_the_end(client):
    """len(rows) == limit is NOT the end test: 2552 = 8 * 319, so the last page is full."""
    d = client.get("/api/jobs?limit=319&offset=2233").json()
    assert len(d["rows"]) == d["limit"] == 319 and d["next_offset"] is None


def test_offset_past_the_end_is_empty_and_final(client):
    d = client.get(f"/api/jobs?offset={N + 10}").json()
    assert d["rows"] == [] and d["next_offset"] is None and d["total"] == N


def test_clinics_and_plan_share_the_contract(client):
    for url in ("/api/clinics?limit=999999", "/api/plan?limit=999999"):
        d = client.get(url).json()
        assert d["limit"] == 999999 and d["next_offset"] is None and len(d["rows"]) == d["total"], url


def test_manifest_publishes_the_paging_contract(client):
    m = client.get("/api/agent/manifest").json()["paging"]
    assert m["max_page_size"] is None and "next_offset" in m["envelope"]
    assert "GET /api/jobs" in m["routes"] and m["stability"]


# --- the same answer without the envelope -------------------------------------------------------
def test_ndjson_reports_the_page_in_content_range(client):
    r = client.get("/api/jobs?limit=2000", headers={"Accept": "application/x-ndjson"})
    assert len(r.text.splitlines()) == 2000
    assert r.headers["content-range"] == f"rows 0-1999/{N}"      # the header was absent entirely

    whole = client.get("/api/jobs?limit=999999", headers={"Accept": "application/x-ndjson"})
    assert len(whole.text.splitlines()) == N and whole.headers["content-range"] == f"rows 0-{N - 1}/{N}"

    empty = client.get(f"/api/jobs?offset={N + 1}", headers={"Accept": "application/x-ndjson"})
    assert empty.text == "" and empty.headers["content-range"] == f"rows */{N}"


# --- nonsense values fail loudly rather than being clamped --------------------------------------
@pytest.mark.parametrize("qs", ["limit=-1", "offset=-5"])
def test_negative_paging_is_a_400_naming_the_parameter(client, qs):
    r = client.get("/api/jobs?" + qs)
    assert r.status_code == 400 and qs.split("=")[0] in r.json()["detail"]


def test_limit_zero_returns_zero_rows(client):
    """`?limit=0` used to answer one row and call it "limit": 1."""
    d = client.get("/api/jobs?limit=0").json()
    assert d["rows"] == [] and d["limit"] == 0 and d["total"] == N and d["next_offset"] == 0


def test_non_integer_limit_is_still_a_400(client):
    r = client.get("/api/jobs?limit=abc")
    assert r.status_code == 400 and "limit" in r.json()["detail"]


# --- the autopilot lists go through the same helper ---------------------------------------------
def test_autopilot_page_shares_the_helper():
    from app.autopilot import api as AP
    rows = list(range(5000))
    assert AP._page(rows, {"limit": "999999"}) == {"total": 5000, "limit": 999999, "offset": 0,
                                                  "next_offset": None, "rows": rows}
    assert AP._page(rows, {})["next_offset"] == 100          # default page size unchanged


def test_page_helper_is_the_one_place_paging_is_decided():
    """Whoever adds a fourth paged route gets the contract for free -- and a second copy of the clamp
    would have to be written on purpose, which is how the autopilot lists got theirs."""
    import inspect

    from app import main as M
    assert "D.page(" in inspect.getsource(M._page)
    from app.autopilot import api as AP
    assert "D.page(" in inspect.getsource(AP._page)
