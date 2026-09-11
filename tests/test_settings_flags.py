"""app/settings.py feature flags: default state, validated PUT, stripe_gate honouring the switch."""
import time

import pytest
from fastapi.testclient import TestClient

from app import auth as AU
from app import config as A
from app import data as D
from app import runs as R
from app import scheduler as S
from app import schedules as SC
from app import settings as ST
from app import stripe_gate as SG

OWNER = "owner@example.org"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("OWNER_EMAILS", OWNER)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(AU, "_inited_path", None)
    monkeypatch.setattr(SG, "_inited_path", None)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {"cities": []}, "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(R, "enqueue", lambda rid: None)
    monkeypatch.setattr(S, "start", lambda: SC.init())
    R.init(); SC.init()   # direct-function tests below don't go through TestClient's startup event


@pytest.fixture()
def client(env):
    from app.main import app
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        yield c


def test_stripe_flag_defaults_off(env):
    assert ST.get_feature_flags() == {"stripe": False, "chats_dock": False}
    assert SG.configured() is False          # a real key is set in `env`; the flag is what's missing


def test_public_flags_endpoint_is_open_and_allowlisted(client):
    r = client.get("/api/flags")               # no owner header at all
    assert r.status_code == 200 and r.json() == {"chats_dock": False}


def test_public_flags_reflects_toggle_but_never_leaks_stripe(client):
    h = {"Cookie": "pj_session=" + AU.create_session(OWNER, "owner")}
    client.put("/api/settings/flags", json={"stripe": True, "chats_dock": True}, headers=h)
    r = client.get("/api/flags").json()
    assert r == {"chats_dock": True} and "stripe" not in r


def test_save_feature_flags_toggles_and_validates(env):
    assert ST.save_feature_flags({"stripe": True}) == {"stripe": True, "chats_dock": False}
    assert ST.get_feature_flags() == {"stripe": True, "chats_dock": False}
    assert SG.configured() is True
    r = ST.save_feature_flags({"stripe": False, "unknown_key": True})   # unknown keys ignored
    assert r == {"stripe": False, "chats_dock": False}
    with pytest.raises(ValueError):
        ST.save_feature_flags("not a dict")


def test_get_all_surfaces_flags_and_status_notes(env):
    d = ST.get_all()
    assert d["feature_flags"] == {"stripe": False, "chats_dock": False}
    assert "stripe" in d["feature_flags_info"] and d["feature_flags_info"]["stripe"]["label"]
    keys = {n["key"] for n in d["feature_status_notes"]}
    assert {"autopilot", "tailnet_login", "hunter", "judge_runner"} <= keys


def test_put_settings_flags_owner_only(client):
    assert client.put("/api/settings/flags", json={"stripe": True}).status_code == 401
    h = {"Cookie": "pj_session=" + AU.create_session(OWNER, "owner")}
    r = client.put("/api/settings/flags", json={"stripe": True}, headers=h)
    assert r.status_code == 200 and r.json() == {"stripe": True, "chats_dock": False}
    assert client.get("/api/settings", headers=h).json()["feature_flags"] == {"stripe": True, "chats_dock": False}


def test_put_settings_flags_rejects_bad_body(client):
    h = {"Cookie": "pj_session=" + AU.create_session(OWNER, "owner")}
    r = client.put("/api/settings/flags", json="nope", headers=h)
    assert r.status_code == 422
