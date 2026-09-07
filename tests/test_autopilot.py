"""Backend tests for the autopilot PoC (docs/autopilot.md). SQLite in a temp dir, registry snapshot stubbed
(no Supabase/network), same fixture idiom as tests/test_app_api.py."""
import time

import pytest
from fastapi.testclient import TestClient

from app import config as A
from app import data as D
from app import mechanics as ME
from app import runs as R
from app import scheduler as S
from app import schedules as SC
from app.autopilot import db as ADB
from app.autopilot import engine as E
from app.autopilot import seed as ASEED

# A tiny registry: two clinics in Regensburg/Oberpfalz (pflegefachkraft + fachpflege openings) and one in
# München/Oberbayern (fachpflege), so matching.rank/cohort_preview/share_posting all have something real to find.
CLINICS = [
    {"clinic_id": "90001", "name": "Klinikum Regensburg Nord", "town": "Regensburg", "regierungsbezirk": "Oberpfalz",
     "landkreis": "Regensburg", "fachrichtungen": ["INN"], "beds": 300, "jobs_open": 2,
     "careers_url": "https://klinikum-nord.example/jobs", "versorgungsstufe": "Schwerpunkt (II)", "traegerart": "oeffentlich"},
    {"clinic_id": "90002", "name": "St. Anna Klinik Regensburg", "town": "Regensburg", "regierungsbezirk": "Oberpfalz",
     "landkreis": "Regensburg", "fachrichtungen": ["CHI"], "beds": 150, "jobs_open": 1,
     "careers_url": "https://st-anna.example/jobs", "versorgungsstufe": "Grundversorgung", "traegerart": "freigemeinnuetzig"},
    {"clinic_id": "90003", "name": "Klinikum Muenchen Mitte", "town": "Muenchen", "regierungsbezirk": "Oberbayern",
     "landkreis": "Muenchen", "fachrichtungen": ["PSY"], "beds": 500, "jobs_open": 3,
     "careers_url": "https://klinikum-muenchen.example/jobs", "versorgungsstufe": "Maximalversorgung (III)", "traegerart": "oeffentlich"},
]
JOBS = [
    {"posting_id": 1, "clinic_id": "90001", "title": "Pflegefachkraft Innere Medizin", "role_class": "pflegefachkraft",
     "department_hint": "Innere Medizin", "qualification_hint": "generalistisch", "city": "Regensburg",
     "external_url": "https://klinikum-nord.example/jobs/1", "first_published": "2026-08-01"},
    {"posting_id": 2, "clinic_id": "90002", "title": "Pflegefachkraft Chirurgie", "role_class": "pflegefachkraft",
     "department_hint": "Chirurgie/Orthopädie", "qualification_hint": "generalistisch", "city": "Regensburg",
     "external_url": "https://st-anna.example/jobs/2", "first_published": "2026-08-05"},
    {"posting_id": 3, "clinic_id": "90003", "title": "Fachkrankenpflege Psychiatrie", "role_class": "fachpflege",
     "department_hint": "Psychiatrie", "qualification_hint": "generalistisch", "city": "Muenchen",
     "external_url": "https://klinikum-muenchen.example/jobs/3", "first_published": "2026-08-10"},
]
CSV_ROWS = [{"clinic_id": c["clinic_id"], "name": c["name"], "town": c["town"], "operator": "", "landkreis": c["landkreis"],
             "regierungsbezirk": c["regierungsbezirk"], "status": "Plan-KH", "versorgungsstufe": c["versorgungsstufe"],
             "traegerart": c["traegerart"], "beds": str(c["beds"]), "day_places": "0", "fachrichtungen": "|".join(c["fachrichtungen"]),
             "parse_quality": "ok", "source": "test", "website": "", "careers_url": c["careers_url"], "ats_type": ""} for c in CLINICS]


def _classify(title):
    return "pflegefachkraft" if "pflege" in title.lower() else "nicht_pflege"


FAKE_MECHANIC = {"id": "role_class", "title": {"de": "x", "en": "x"}, "description": {"de": "x", "en": "x"}, "patterns_section": "role",
                 "inputs": [{"name": "title", "label": {"de": "x", "en": "x"}, "example": "x"}], "test_file": "tests/test_autopilot.py",
                 "functions": [_classify], "try": lambda inputs: {"result": _classify(inputs.get("title", "")), "rule": "demo"}}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ADB, "SQLITE_PATH", tmp_path / "autopilot.sqlite")
    D._snap.update({"at": time.time(), "jobs": JOBS, "clinics": CLINICS, "by_clinic": {c["clinic_id"]: c for c in CLINICS},
                    "facets": {"cities": []}, "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(D, "registry_csv_rows", lambda: CSV_ROWS)
    monkeypatch.setattr(ME, "_FAKE", [FAKE_MECHANIC])
    monkeypatch.setattr(R, "enqueue", lambda rid: None)
    monkeypatch.setattr(S, "start", lambda: SC.init())
    from app.main import app
    with TestClient(app) as c:
        yield c


def _db():
    return ADB.db()


# --- seed determinism -----------------------------------------------------------------------------------------
def test_seed_determinism(client):
    counts1 = ASEED.seed(reset=True, n_candidates=25)
    with ADB._lock, ADB.db() as c:
        names1 = [r["name"] for r in c.execute("select name from candidates order by id")]
        scores1 = [r["score"] for r in c.execute("select score from matches order by id")]
    counts2 = ASEED.seed(reset=True, n_candidates=25)
    with ADB._lock, ADB.db() as c:
        names2 = [r["name"] for r in c.execute("select name from candidates order by id")]
        scores2 = [r["score"] for r in c.execute("select score from matches order by id")]
    assert counts1 == counts2
    assert names1 == names2 and len(names1) == 25
    assert scores1 == scores2


# --- overview / conversations views -----------------------------------------------------------------------------
def test_overview_counts_match_conversation_views(client):
    ov = client.get("/api/autopilot/overview").json()
    for view, key in [("needs_reply", "needs_reply"), ("waiting_candidate", "waiting_candidate"), ("waiting_clinic", "waiting_clinic"),
                       ("manager", "manager"), ("paused", "paused"), ("overdue", "overdue")]:
        total = client.get(f"/api/autopilot/conversations?view={view}&limit=1").json()["total"]
        assert total == ov["counts"][key], f"{view}: {total} != {ov['counts'][key]}"
    assert ov["counts"]["luna_active"] == client.get("/api/autopilot/conversations?view=luna&limit=1").json()["total"]
    assert set(s["stage"] for s in ov["funnel"]) >= set(ASEED.STAGES)


# --- mode transition: event logged, stopped blocks send --------------------------------------------------------
def test_mode_transition_logs_event_and_stopped_blocks_send(client):
    rows = client.get("/api/autopilot/conversations?kind=candidate&mode=luna&limit=1").json()["rows"]
    assert rows, "need at least one candidate conversation in mode=luna"
    cid = rows[0]["id"]
    r = client.post(f"/api/autopilot/conversations/{cid}/mode", json={"mode": "stopped", "reason": "opt-out test"})
    assert r.status_code == 200 and r.json()["mode"] == "stopped"
    detail = client.get(f"/api/autopilot/conversations/{cid}").json()
    assert any(e["kind"] == "mode_changed" for e in detail["timeline"])
    r = client.post(f"/api/autopilot/conversations/{cid}/send", json={"text": "hallo?", "as": "operator"})
    assert r.status_code == 400


# --- auto mode + medium risk -> pending approval; approving applies the effect --------------------------------
def test_medium_risk_send_creates_approval_and_approving_applies_it(client):
    pol = client.put("/api/autopilot/policy", json={"mode": "auto"}).json()
    assert pol["mode"] == "auto"
    # a 'proposed' match on a luna-mode candidate conversation
    m = None
    for row in client.get("/api/autopilot/matches?status=proposed&limit=200").json()["rows"]:
        conv = client.get(f"/api/autopilot/candidates/{row['candidate_id']}").json()
        cv_id = conv.get("conversation_id")
        if cv_id and client.get(f"/api/autopilot/conversations/{cv_id}").json()["conversation"]["mode"] not in ("stopped",):
            m = row
            break
    assert m is not None, "need a proposed match to send"
    r = client.post(f"/api/autopilot/matches/{m['id']}/status", json={"status": "sent"})
    assert r.status_code == 200
    d = r.json()
    assert d["send_status"] == "pending_approval" and d["approval_id"]
    assert d["status"] == "approved"          # parked, not yet sent
    pending = client.get(f"/api/autopilot/approvals?status=pending&risk=medium").json()["rows"]
    assert any(a["id"] == d["approval_id"] for a in pending)
    r2 = client.post(f"/api/autopilot/approvals/{d['approval_id']}", json={"decision": "approve"})
    assert r2.status_code == 200
    eff = r2.json()["effect"]
    assert eff["decision"] == "approve"
    m2 = next(x for x in client.get(f"/api/autopilot/matches?candidate_id={m['candidate_id']}").json()["rows"] if x["id"] == m["id"])
    assert m2["status"] == "sent"


# --- STOP inbound sets mode=stopped -------------------------------------------------------------------------------
def test_stop_inbound_sets_mode_stopped(client):
    rows = client.get("/api/autopilot/conversations?kind=candidate&mode=luna&limit=1&offset=1").json()["rows"]
    assert rows
    cid = rows[0]["id"]
    with ADB._lock, ADB.db() as c:
        conv = ADB.get(c, "conversations", cid)
        result = E.apply_inbound(conv, "STOP", c=c)
        c.commit()
    assert result["stopped"] is True
    detail = client.get(f"/api/autopilot/conversations/{cid}").json()
    assert detail["conversation"]["mode"] == "stopped"


# --- tick advances the clock and returns a non-trivial summary --------------------------------------------------
def test_tick_advances_clock_and_returns_summary(client):
    before = client.get("/api/autopilot/policy").json()["sim_now"]
    r = client.post("/api/autopilot/tick", json={"minutes": 180})
    assert r.status_code == 200
    d = r.json()
    assert d["sim_now"] != before
    s = d["summary"]
    assert set(s) >= {"messages_out", "messages_in", "approvals_created", "queue_run", "leads_new"}
    assert sum(s.values()) > 0
    assert isinstance(d["events"], list) and len(d["events"]) > 0


# --- cohort preview respects the throttles in policy -----------------------------------------------------------
def test_cohort_preview_respects_throttles(client):
    r = client.post("/api/autopilot/cohorts/preview", json={"criteria": {"role_class": "pflegefachkraft", "region": "Oberpfalz"}})
    assert r.status_code == 200
    d = r.json()
    assert "throttled" in d and {"candidates", "clinics", "max_concurrent_profiles_per_candidate", "max_profiles_per_clinic_per_week"} <= set(d["throttled"])
    pol = client.get("/api/autopilot/policy").json()
    assert d["throttled"]["max_concurrent_profiles_per_candidate"] == pol["max_concurrent_profiles_per_candidate"]
    assert d["throttled"]["max_profiles_per_clinic_per_week"] == pol["max_profiles_per_clinic_per_week"]
    for cl in d["clinics"]:
        assert cl["profiles_this_week"] < d["throttled"]["max_profiles_per_clinic_per_week"]


# --- funnel: conversions are sane probabilities ------------------------------------------------------------------
def test_funnel_conversions_are_probabilities(client):
    for by in ("source", "language", "region", "owner"):
        d = client.get(f"/api/autopilot/funnel?by={by}").json()
        for s in d["stages"]:
            if s["conversion_from_prev"] is not None:
                assert 0.0 <= s["conversion_from_prev"] <= 1.0
        for g in d["groups"]:
            for s in g["stages"]:
                if s["conversion_from_prev"] is not None:
                    assert 0.0 <= s["conversion_from_prev"] <= 1.0


# --- share_posting composes a message with the real clinic/job name --------------------------------------------
def test_share_posting_uses_real_clinic_and_job_name(client):
    rows = client.get("/api/autopilot/conversations?kind=candidate&mode=luna&limit=50").json()["rows"]
    conv_id = None
    for row in rows:
        detail = client.get(f"/api/autopilot/conversations/{row['id']}").json()
        if detail["conversation"]["mode"] == "luna":
            conv_id = row["id"]
            break
    assert conv_id is not None
    r = client.post(f"/api/autopilot/conversations/{conv_id}/action", json={"action": "share_posting", "params": {"clinic_id": "90001"}})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["posting"]["clinic_name"] == "Klinikum Regensburg Nord"
    assert result["posting"]["title"] == "Pflegefachkraft Innere Medizin"
    text = result.get("text") or ""
    assert "Klinikum Regensburg Nord" in text and "Pflegefachkraft Innere Medizin" in text
