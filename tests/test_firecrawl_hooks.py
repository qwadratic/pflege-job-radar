"""Firecrawl webhook receiver (app/firecrawl_hooks.py) + spend gate / 24h kill switch (app/crawl.py).

No network: app.config's rest_get/rest_post are monkeypatched, and pflege_jobs.sources.firecrawl_agent.credits()
is monkeypatched wherever the plan/remaining numbers matter to the test.
"""
import time

import pytest
from fastapi.testclient import TestClient

from app import config as A
from app import crawl as CR
from app import data as D
from app import firecrawl_hooks as FH
from app import runs as R
from app import scheduler as S
from app import schedules as SC

CLINIC_UNKNOWN = {"clinic_id": "36202", "name": "Krankenhaus St. Josef", "town": "Regensburg", "operator": "St. Josef",
                   "website": "", "careers_url": "", "ats_type": "", "routable": False, "walled": False}
CLINIC_KNOWN = {"clinic_id": "36201", "name": "Krankenhaus Barmherzige Brueder", "town": "Regensburg", "operator": "BB",
                "website": "https://x", "careers_url": "https://x/karriere", "ats_type": "typo3_jobs", "routable": True, "walled": False,
                "board": "https://x/karriere", "vendor": "typo3_jobs"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [CLINIC_UNKNOWN, CLINIC_KNOWN],
                    "by_clinic": {CLINIC_UNKNOWN["clinic_id"]: CLINIC_UNKNOWN, CLINIC_KNOWN["clinic_id"]: CLINIC_KNOWN},
                    "facets": {}, "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(R, "enqueue", lambda rid: None)
    monkeypatch.setattr(S, "start", lambda: SC.init())
    from app.main import app
    with TestClient(app) as c:
        yield c


def _secret(client):
    return FH.get_webhook_secret()


# --- webhook: auth + event storage --------------------------------------------------------------
def test_webhook_rejects_missing_or_wrong_secret(client):
    r = client.post("/api/firecrawl/webhook", json={"type": "agent.started", "id": "job-1"})
    assert r.status_code == 401
    r = client.post("/api/firecrawl/webhook", json={"type": "agent.started", "id": "job-1"},
                     headers={"X-Pflege-Webhook-Secret": "wrong"})
    assert r.status_code == 401


def test_webhook_secret_is_generated_once_and_persisted(client):
    s1 = FH.get_webhook_secret()
    s2 = FH.get_webhook_secret()
    assert s1 == s2 and len(s1) > 10
    assert R.get_setting("firecrawl")["webhook_secret"] == s1


def test_webhook_stores_every_event(client):
    secret = _secret(client)
    run_id = R.create_run("clinic", "36202", "firecrawl", {}, ["36202"], trigger="api")
    payload = {"type": "agent.action", "id": "job-2", "metadata": {"clinic_id": "36202", "run_id": run_id}, "action": "clicking pagination"}
    r = client.post("/api/firecrawl/webhook", json=payload, headers={"X-Pflege-Webhook-Secret": secret})
    assert r.status_code == 200 and r.json() == {"ok": True}
    events = R.firecrawl_events_for_job("job-2")
    assert len(events) == 1 and events[0]["event_type"] == "agent.action" and events[0]["raw"]["action"] == "clicking pagination"
    log = R.get_run(run_id)["log"]
    assert any("agent.action" in l for l in log)


# --- webhook: agent.completed -> inbox rows, credits recorded -----------------------------------
def test_webhook_completed_posts_inbox_rows_and_records_usage(client, monkeypatch):
    secret = _secret(client)
    posted = {}

    def fake_post_inbox(rows, log):
        posted["rows"] = rows
        return [r["source_url"] for r in rows]
    monkeypatch.setattr(CR, "_post_inbox", fake_post_inbox)
    run_id = R.create_run("clinic", "36202", "firecrawl", {}, ["36202"], trigger="api")
    payload = {"type": "agent.completed", "id": "job-3", "success": True,
               "metadata": {"clinic_id": "36202", "run_id": run_id},
               "data": [{"creditsUsed": 42, "data": {"jobs": [{"title": "Pflegefachkraft (m/w/d)", "url": "https://x/jobs/1"}]}}]}
    r = client.post("/api/firecrawl/webhook", json=payload, headers={"X-Pflege-Webhook-Secret": secret})
    assert r.status_code == 200
    assert len(posted["rows"]) == 1 and posted["rows"][0]["source_url"] == "https://x/jobs/1"
    assert R.usage_total() == 42
    log = R.get_run(run_id)["log"]
    assert any("agent.completed" in l and "42 credits" in l for l in log)


def test_webhook_failed_records_credits_and_error(client):
    secret = _secret(client)
    run_id = R.create_run("clinic", "36202", "firecrawl", {}, ["36202"], trigger="api")
    payload = {"type": "agent.failed", "id": "job-4", "success": False, "error": "cancelled by user",
               "metadata": {"clinic_id": "36202", "run_id": run_id}, "data": [{"creditsUsed": 5}]}
    r = client.post("/api/firecrawl/webhook", json=payload, headers={"X-Pflege-Webhook-Secret": secret})
    assert r.status_code == 200
    assert R.usage_total() == 5
    log = R.get_run(run_id)["log"]
    assert any("agent.failed" in l and "cancelled by user" in l for l in log)


def test_crawl_webhook_check_reads_back_terminal_event(client):
    secret = _secret(client)
    run_id = R.create_run("clinic", "36202", "firecrawl", {}, ["36202"], trigger="api")
    payload = {"type": "agent.completed", "id": "job-5", "metadata": {"clinic_id": "36202", "run_id": run_id},
               "data": [{"creditsUsed": 3, "data": {"jobs": []}}]}
    client.post("/api/firecrawl/webhook", json=payload, headers={"X-Pflege-Webhook-Secret": secret})
    hit = CR.webhook_check("job-5")
    assert hit["status"] == "completed" and hit["creditsUsed"] == 3 and hit["data"] == {"jobs": []}
    assert CR.webhook_check("no-such-job") is None


# --- spend gate ----------------------------------------------------------------------------------
def test_spend_gate_unknown_clinic_caps_by_euro_budget(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 100000, "plan": 8000})
    R.set_setting("firecrawl", {"eur_per_credit": 0.01, "max_eur_unknown_clinic": 1.0, "reserve_credits": 150})
    gate = CR.spend_gate(CLINIC_UNKNOWN, max_credits=500)
    assert gate["allowed"] and gate["cap"] == 100          # 1.0 EUR / 0.01 EUR-per-credit
    assert "unknown clinic" in gate["reason"]


def test_spend_gate_unknown_clinic_blocked_by_reserve(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 100, "plan": 8000})   # below the 150 reserve floor
    R.set_setting("firecrawl", {"eur_per_credit": 0.01, "max_eur_unknown_clinic": 5.0, "reserve_credits": 150})
    gate = CR.spend_gate(CLINIC_UNKNOWN, max_credits=500)
    assert not gate["allowed"] and gate["cap"] == 0


def test_spend_gate_known_clinic_refuses_when_adapter_covers_it(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 100000, "plan": 8000})
    monkeypatch.setattr(A, "rest_get", lambda path, params=None, **k: [{"source_url": "https://x/jobs/1"}] if path == "inbox" else [])
    gate = CR.spend_gate(CLINIC_KNOWN, max_credits=100, probe_adapter=lambda c: ["https://x/jobs/1"])
    assert not gate["allowed"] and gate["reason"] == "adapter covers it" and gate["unseen"] == 0


def test_spend_gate_known_clinic_allows_unseen_rows(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 100000, "plan": 8000})
    monkeypatch.setattr(A, "rest_get", lambda path, params=None, **k: [])
    gate = CR.spend_gate(CLINIC_KNOWN, max_credits=40, probe_adapter=lambda c: ["https://x/jobs/1", "https://x/jobs/2"])
    assert gate["allowed"] and gate["cap"] == 40 and gate["unseen"] == 2


def test_spend_gate_adapter_probe_failure_refuses(client):
    def boom(c):
        raise RuntimeError("board down")
    gate = CR.spend_gate(CLINIC_KNOWN, max_credits=40, probe_adapter=boom)
    assert not gate["allowed"] and "board down" in gate["reason"]


# --- 24h kill switch -------------------------------------------------------------------------
def test_kill_switch_ok_below_warn(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 7900, "plan": 8000})
    monkeypatch.setattr(R, "usage_total", lambda days=None, hours=None: 50)   # 50/8000 = 0.6%
    allowed, reason = CR.kill_switch(run_mode="auto", log=lambda *_: None)
    assert allowed and reason is None


def test_kill_switch_warns_at_10pct_but_still_allows(client, monkeypatch, capsys):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 7200, "plan": 8000})
    monkeypatch.setattr(R, "usage_total", lambda days=None, hours=None: 800)  # 10%
    logged = []
    allowed, reason = CR.kill_switch(run_mode="auto", log=logged.append)
    assert allowed and reason is None and any("WARNING" in l for l in logged)


def test_kill_switch_throttles_scheduled_auto_at_20pct(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 6400, "plan": 8000})
    monkeypatch.setattr(R, "usage_total", lambda days=None, hours=None: 1600)  # 20%
    allowed, reason = CR.kill_switch(run_mode="auto", trigger="schedule", log=lambda *_: None)
    assert not allowed and "THROTTLE" in reason


def test_kill_switch_throttle_still_allows_explicit_manual_mode(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 6400, "plan": 8000})
    monkeypatch.setattr(R, "usage_total", lambda days=None, hours=None: 1600)  # 20%
    logged = []
    allowed, reason = CR.kill_switch(run_mode="firecrawl", trigger="api", log=logged.append)
    assert allowed and reason is None and any("explicit" in l for l in logged)


def test_kill_switch_disables_everything_and_pauses_scheduler_at_30pct(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 5600, "plan": 8000})
    monkeypatch.setattr(R, "usage_total", lambda days=None, hours=None: 2400)  # 30%
    assert not S.is_paused()
    allowed, reason = CR.kill_switch(run_mode="firecrawl", trigger="api", log=lambda *_: None)
    assert not allowed and "DISABLE" in reason
    assert S.is_paused()
    S.resume()                                              # don't leak paused state into other tests


def test_kill_switch_unknown_plan_never_blocks(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": None, "plan": None, "error": "network"})
    allowed, reason = CR.kill_switch(run_mode="auto", log=lambda *_: None)
    assert allowed and reason is None


# --- scheduler paused flag surfaces in settings/scheduler status --------------------------------
def test_scheduler_status_reports_paused(client):
    S.pause("test")
    try:
        assert S.status()["paused"] is True and S.status()["paused_reason"] == "test"
        r = client.get("/api/settings").json()
        assert r["scheduler"]["paused"] is True
    finally:
        S.resume()
    assert S.status()["paused"] is False


def test_paused_scheduler_tick_fires_nothing_unless_forced(client):
    S.pause("test")
    try:
        assert CR is not None                                # keep import used
        out = S.tick(force=False)
        assert out["fired"] == [] and out.get("paused") is True
    finally:
        S.resume()
