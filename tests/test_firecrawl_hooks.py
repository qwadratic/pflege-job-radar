"""Firecrawl webhook receiver (app/firecrawl_hooks.py) + spend gate / 24h kill switch (app/crawl.py), including the
free-daily-run allowance (5 agent runs per UTC day at zero credits) the gate prefers before applying the EUR cap.

No network: app.config's rest_get/rest_post are monkeypatched, and pflege_jobs.sources.firecrawl_agent.credits()
is monkeypatched wherever the plan/remaining numbers matter to the test. The client fixture's temp SQLite starts
with an empty ledger, i.e. 0 agent runs today / 5 free runs left; _submitted() seeds accepted submissions.
"""
import itertools
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


def test_webhook_updates_the_submission_row_instead_of_adding_one(client, monkeypatch):
    """on_submit wrote a 0-credit row keyed by the job id; the webhook for that job must update it, so the job is
    one ledger row (agent_runs_today counts it once) with the webhook's credits."""
    secret = _secret(client)
    monkeypatch.setattr(CR, "_post_inbox", lambda rows, log: [])
    run_id = R.create_run("clinic", "36202", "firecrawl", {}, ["36202"], trigger="api")
    R.add_usage("jobs", "36202", 0, run_id, job_id="job-9")
    assert R.agent_runs_today() == 1
    payload = {"type": "agent.completed", "id": "job-9", "success": True, "metadata": {"clinic_id": "36202", "run_id": run_id},
               "data": [{"creditsUsed": 42, "data": {"jobs": []}}]}
    assert client.post("/api/firecrawl/webhook", json=payload, headers={"X-Pflege-Webhook-Secret": secret}).status_code == 200
    assert R.usage_total() == 42 and R.agent_runs_today() == 1
    with R.db() as c:
        assert c.execute("select count(*) from firecrawl_usage").fetchone()[0] == 1


def test_execute_stores_the_token_delta_on_the_ledger_row(client, monkeypatch):
    """The firecrawl branch of execute(): on_submit writes the row, the result updates credits AND tokens on it."""
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 100000, "plan": 8000})
    monkeypatch.setattr(CR, "_post_inbox", lambda rows, log: [])
    monkeypatch.setattr(CR, "_cli", lambda args, log, timeout=1800: 0)
    monkeypatch.setattr(CR, "_budget_left", lambda: 1000)
    monkeypatch.setattr(R, "mirror_to_supabase", lambda run: None)

    def fake_jobs_agent(clinic, max_credits, log, session, on_submit=None, **kw):
        on_submit("job-77")
        return {"rows": [], "credits_used": 27, "credits_api": 27, "credits_delta": 27, "job_id": "job-77",
                "tokens_before": 5685, "tokens_after": 5280, "tokens_delta": 405, "raw": {}, "data": {"jobs": []}}
    monkeypatch.setattr(FA, "run_jobs_agent", fake_jobs_agent)
    rid = R.create_run("clinic", "36202", "firecrawl", {"max_credits": 40, "verify": False}, ["36202"], trigger="api")
    CR.execute(rid)
    with R.db() as c:
        rows = [tuple(r) for r in c.execute("select job_id, credits, tokens from firecrawl_usage")]
    assert rows == [("job-77", 27, 405)] and R.tokens_total(days=7) == 405 and R.agent_runs_today() == 1
    assert any("tokens delta 405" in l for l in R.get_run(rid)["log"])


def test_crawl_webhook_check_reads_back_terminal_event(client):
    secret = _secret(client)
    run_id = R.create_run("clinic", "36202", "firecrawl", {}, ["36202"], trigger="api")
    payload = {"type": "agent.completed", "id": "job-5", "metadata": {"clinic_id": "36202", "run_id": run_id},
               "data": [{"creditsUsed": 3, "data": {"jobs": []}}]}
    client.post("/api/firecrawl/webhook", json=payload, headers={"X-Pflege-Webhook-Secret": secret})
    hit = CR.webhook_check("job-5")
    assert hit["status"] == "completed" and hit["creditsUsed"] == 3 and hit["data"] == {"jobs": []}
    assert CR.webhook_check("no-such-job") is None


_seq = itertools.count()


def _submitted(n, clinic_id="36202"):
    """Seed n accepted agent submissions today (what run_agent's on_submit callback writes)."""
    for _ in range(n):
        R.add_usage("jobs", clinic_id, 0, None, job_id=f"seed-job-{next(_seq)}")


# --- spend gate ----------------------------------------------------------------------------------
def test_spend_gate_free_run_is_allowed_regardless_of_the_eur_cap(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 100000, "plan": 8000})
    R.set_setting("firecrawl", {"eur_per_credit": 0.01, "max_eur_unknown_clinic": 1.0, "reserve_credits": 150})
    gate = CR.spend_gate(CLINIC_UNKNOWN, max_credits=500, log=lambda *_: None)
    assert gate["allowed"] and gate["cap"] == 500          # the 1.0 EUR cap (100 credits) is not applied to a free run
    assert gate["free_runs_left_today"] == 5 and gate["billable"] is False
    assert "unknown clinic" in gate["reason"] and "free daily run 1/5" in gate["reason"]


def test_spend_gate_sixth_run_of_the_day_gets_the_euro_cap(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 100000, "plan": 8000})
    R.set_setting("firecrawl", {"eur_per_credit": 0.01, "max_eur_unknown_clinic": 1.0, "reserve_credits": 150})
    _submitted(4)
    gate = CR.spend_gate(CLINIC_UNKNOWN, max_credits=500, log=lambda *_: None)
    assert gate["allowed"] and gate["cap"] == 500 and gate["free_runs_left_today"] == 1 and "free daily run 5/5" in gate["reason"]
    _submitted(1, clinic_id="16104")                        # 5th accepted submission today, any clinic
    logged = []
    gate = CR.spend_gate(CLINIC_UNKNOWN, max_credits=500, log=logged.append)
    assert gate["allowed"] and gate["cap"] == 100          # 1.0 EUR / 0.01 EUR-per-credit
    assert gate["free_runs_left_today"] == 0 and gate["billable"] is True and "billable run (#6 today" in gate["reason"]
    assert any("billable" in l and "EUR cap applies" in l for l in logged)


def test_spend_gate_free_run_still_refused_by_the_reserve_floor(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 100, "plan": 8000})   # below the 150 reserve floor
    R.set_setting("firecrawl", {"eur_per_credit": 0.01, "max_eur_unknown_clinic": 5.0, "reserve_credits": 150})
    gate = CR.spend_gate(CLINIC_UNKNOWN, max_credits=500, log=lambda *_: None)
    assert not gate["allowed"] and gate["cap"] == 0 and gate["free_runs_left_today"] == 5 and "reserve_credits" in gate["reason"]


def test_spend_gate_unreadable_ledger_is_treated_as_billable(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 100000, "plan": 8000})
    monkeypatch.setattr(FA, "agent_runs_today", lambda: None)
    R.set_setting("firecrawl", {"eur_per_credit": 0.01, "max_eur_unknown_clinic": 1.0, "reserve_credits": 150})
    gate = CR.spend_gate(CLINIC_UNKNOWN, max_credits=500, log=lambda *_: None)
    assert gate["allowed"] and gate["cap"] == 100 and gate["free_runs_left_today"] is None and gate["billable"] is True
    assert "ledger unavailable" in gate["reason"]


def test_spend_gate_free_run_then_kill_switch_still_refuses(client, monkeypatch):
    """A free run is still subject to the kill switch: the gate says free, the switch says no -- execute() asks the switch first."""
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 5600, "plan": 8000, "tokens_remaining": 5685, "tokens_plan": 120000})
    gate = CR.spend_gate(CLINIC_UNKNOWN, max_credits=40, log=lambda *_: None)
    assert gate["allowed"] and gate["billable"] is False
    monkeypatch.setattr(R, "usage_total", lambda days=None, hours=None: 2400)  # 30% of plan in 24h
    allowed, reason = CR.kill_switch(run_mode="firecrawl", trigger="api", log=lambda *_: None)
    assert not allowed and "DISABLE" in reason
    S.resume()


def test_spend_gate_unknown_clinic_blocked_by_reserve(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 100, "plan": 8000})   # below the 150 reserve floor
    R.set_setting("firecrawl", {"eur_per_credit": 0.01, "max_eur_unknown_clinic": 5.0, "reserve_credits": 150})
    gate = CR.spend_gate(CLINIC_UNKNOWN, max_credits=500)
    assert not gate["allowed"] and gate["cap"] == 0


def test_spend_gate_refuses_a_cap_too_thin_to_ever_succeed(client, monkeypatch):
    """2026-09-08 run 69: remaining 26 - reserve 20 = cap 6; every one of 4 attempts failed with the API's own
    'Agent reached max credits' (a 60 cap already fails on a real board per HUNTER_DEFAULT's own comment).
    Below MIN_VIABLE_CAP the gate must refuse outright instead of submitting a doomed job."""
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 26, "plan": 8000})
    R.set_setting("firecrawl", {"eur_per_credit": 0.01, "max_eur_unknown_clinic": 5.0, "reserve_credits": 20})
    gate = CR.spend_gate(CLINIC_UNKNOWN, max_credits=40, log=lambda *_: None)
    assert not gate["allowed"] and gate["cap"] == 0 and "budget too thin" in gate["reason"] and "6 credits available" in gate["reason"]
    monkeypatch.setattr(A, "rest_get", lambda path, params=None, **k: [])
    gate = CR.spend_gate(CLINIC_KNOWN, max_credits=40, probe_adapter=lambda c: ["https://x/jobs/1"], log=lambda *_: None)
    assert not gate["allowed"] and "budget too thin" in gate["reason"] and gate["unseen"] == 1


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
    assert gate["allowed"] and gate["cap"] == 40 and gate["unseen"] == 2 and gate["free_runs_left_today"] == 5
    _submitted(5)
    gate = CR.spend_gate(CLINIC_KNOWN, max_credits=40, probe_adapter=lambda c: ["https://x/jobs/1", "https://x/jobs/2"], log=lambda *_: None)
    assert gate["allowed"] and gate["cap"] == 40 and gate["billable"] is True     # known clinics never had the EUR cap; reserve only


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


def test_kill_switch_status_includes_both_pools_and_free_runs(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 379, "plan": 8000, "tokens_remaining": 5685, "tokens_plan": 120000,
                                                        "free_runs_left_today": 4, "agent_runs_today": 1})
    monkeypatch.setattr(R, "usage_total", lambda days=None, hours=None: 80)
    st = CR.kill_switch_status()
    assert st["pct"] == 1.0 and st["thresholds"] == [10, 20, 30] and st["plan"] == 8000 and st["remaining"] == 379 and st["used_24h"] == 80
    assert st["tokens_remaining"] == 5685 and st["tokens_plan"] == 120000 and st["free_runs_left_today"] == 4 and st["agent_runs_today"] == 1
    assert CR.kill_switch_pct() == (1.0, [10, 20, 30])


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
