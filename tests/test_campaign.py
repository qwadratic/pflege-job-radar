"""app/campaign.py + /api/campaign: default state, validated merge, history append, owner-only."""
import time

import pytest
from fastapi.testclient import TestClient

from app import auth as AU
from app import campaign as CAM
from app import config as A
from app import data as D
from app import runs as R
from app import scheduler as S
from app import schedules as SC

OWNER = "owner@example.org"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("OWNER_EMAILS", OWNER)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setattr(AU, "_inited_path", None)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {"cities": []}, "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(R, "enqueue", lambda rid: None)
    monkeypatch.setattr(S, "start", lambda: SC.init())
    R.init(); SC.init()


@pytest.fixture()
def client(env):
    from app.main import app
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        yield c


def test_default_state(env):
    assert CAM.get() == {"safety_level": "safe", "stopped": False, "stop_reason": None, "history": []}


def test_save_validates_safety_level(env):
    with pytest.raises(ValueError):
        CAM.save({"safety_level": "yolo"})
    with pytest.raises(ValueError):
        CAM.save("not a dict")


def test_save_merges_and_appends_history(env):
    r = CAM.save({"safety_level": "greedy", "snapshot": {"open_jobs": 1749, "note": "tick 1"}})
    assert r["safety_level"] == "greedy"
    assert len(r["history"]) == 1 and r["history"][0]["open_jobs"] == 1749 and "ts" in r["history"][0]
    r = CAM.save({"snapshot": {"open_jobs": 1760, "note": "tick 2"}})
    assert len(r["history"]) == 2
    assert r["safety_level"] == "greedy"          # untouched by a snapshot-only save


def test_save_stop_reason_and_stopped(env):
    r = CAM.save({"stopped": True, "stop_reason": "plateau: no growth in 5 ticks"})
    assert r["stopped"] is True and r["stop_reason"] == "plateau: no growth in 5 ticks"
    r = CAM.save({"stop_reason": None})
    assert r["stop_reason"] is None


def test_api_campaign_owner_only(client):
    assert client.get("/api/campaign").status_code == 401
    assert client.post("/api/campaign", json={"safety_level": "greedy"}).status_code == 401
    h = {"X-ExeDev-Email": OWNER}
    r = client.post("/api/campaign", json={"snapshot": {"open_jobs": 1749}}, headers=h)
    assert r.status_code == 200 and len(r.json()["history"]) == 1
    assert client.get("/api/campaign", headers=h).json()["history"][0]["open_jobs"] == 1749


def test_api_campaign_rejects_bad_body(client):
    h = {"X-ExeDev-Email": OWNER}
    r = client.post("/api/campaign", json={"safety_level": "yolo"}, headers=h)
    assert r.status_code == 422
