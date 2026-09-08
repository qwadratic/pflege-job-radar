"""GET /api/coverage: per-adapter coverage from the stubbed snapshot + a temp SQLite run log (no network)."""
from app import runs as R
from tests.test_app_api import CLINICS, client  # noqa: F401  (fixture: stubbed snapshot, temp sqlite)


def _row(d, key):
    return next(r for r in d["rows"] if r["adapter"] == key)


def test_coverage_rows_and_totals(client):
    d = client.get("/api/coverage").json()
    assert d["generated_at"] and isinstance(d["rows"], list)
    keys = [r["adapter"] for r in d["rows"]]
    for k in ("rexx", "personio", "typo3_jobs", "softgarden", "bite", "pi_asp", "umantis", "wp_jobs", "firecrawl"):
        assert k in keys
    assert "helios" not in keys                                   # no adapter for it: only counted under firecrawl
    # sorted by clinics_labelled desc; the two 1-clinic rows come first, firecrawl (2 clinics) first of all
    assert keys[0] == "firecrawl" and d["rows"][0]["clinics_labelled"] == 2
    t3 = _row(d, "typo3_jobs")
    assert t3["clinics_labelled"] == 1 and t3["clinics_routable"] == 1 and t3["boards"] == 1
    assert t3["clinics_with_jobs"] == 1 and t3["open_jobs"] == 24 and t3["fresh_jobs"] == 3 and t3["coverage_pct"] == 100.0
    assert t3["last_run"] is None and "credits_7d" not in t3
    fc = _row(d, "firecrawl")                                    # 36202 (no careers_url) + 16104 (walled helios)
    assert fc["clinics_labelled"] == 2 and fc["clinics_routable"] == 0 and fc["boards"] == 1
    assert fc["clinics_with_jobs"] == 1 and fc["open_jobs"] == 3 and fc["coverage_pct"] == 50.0 and fc["credits_7d"] == 0
    assert _row(d, "rexx")["clinics_labelled"] == 0 and _row(d, "rexx")["coverage_pct"] == 0.0
    tot = d["totals"]                                             # each clinic once, not the sum of overlapping rows
    assert tot["clinics_labelled"] == 3 and tot["clinics_routable"] == 1 and tot["clinics_with_jobs"] == 2
    assert tot["open_jobs"] == 27 and tot["fresh_jobs"] == 4 and tot["boards"] == 2 and tot["coverage_pct"] == 66.7
    reasons = {u["reason"]: u["count"] for u in d["unroutable"]}
    assert reasons.get("no careers_url") == 1 and reasons.get("no adapter for helios") == 1


def test_coverage_last_run_and_credits(client):
    rid = R.create_run("ats_type", "typo3_jobs", "adapter", clinic_ids=["36201"])
    R.update_run(rid, status="done", started_at=R.now(), finished_at=R.now(), n_rows=12, n_new=4, error="2 error(s), see log")
    rid2 = R.create_run("clinic", "16104", "firecrawl", clinic_ids=["16104"])
    R.update_run(rid2, status="failed", started_at=R.now(), finished_at=R.now(), n_rows=0, n_new=0)
    R.add_usage("agent", "16104", 37, run_id=rid2, tokens=405)
    d = client.get("/api/coverage").json()
    lr = _row(d, "typo3_jobs")["last_run"]
    assert lr == {"run_id": rid, "at": lr["at"], "status": "done", "rows": 12, "new": 4, "errors": 2} and lr["at"]
    fc = _row(d, "firecrawl")
    assert fc["last_run"]["run_id"] == rid2 and fc["last_run"]["status"] == "failed" and fc["last_run"]["errors"] == 0
    assert fc["credits_7d"] == 37 and d["totals"]["credits_7d"] == 37
    assert fc["tokens_7d"] == 405 and d["totals"]["tokens_7d"] == 405 and "tokens_7d" not in _row(d, "rexx")
    assert _row(d, "rexx")["last_run"] is None


def test_coverage_firecrawl_row_shows_both_pools_and_free_runs(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 379, "plan": 8000, "tokens_remaining": 5685, "tokens_plan": 120000,
                                                        "free_runs_per_day": 5, "agent_runs_today": 2, "free_runs_left_today": 3})
    d = client.get("/api/coverage").json()
    fc = _row(d, "firecrawl")
    assert fc["credits_remaining"] == 379 and fc["credits_plan"] == 8000
    assert fc["tokens_remaining"] == 5685 and fc["tokens_plan"] == 120000
    assert fc["free_runs_left_today"] == 3 and fc["free_runs_per_day"] == 5 and fc["agent_runs_today"] == 2
    assert d["totals"]["free_runs_left_today"] == 3
    assert "tokens_remaining" not in _row(d, "typo3_jobs")
    monkeypatch.setattr(FA, "credits", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    fc = _row(client.get("/api/coverage").json(), "firecrawl")
    assert fc["tokens_remaining"] is None and fc["free_runs_left_today"] is None and fc["credits_7d"] == 0 and fc["tokens_7d"] == 0


# --- GET /api/billing/clinics (app/coverage.py): spend per clinic in a window -----------------------------------------
def _ledger(at, clinic_id, credits, run_id=None, tokens=None, kind="jobs"):
    with R._lock, R.db() as c:
        c.execute("insert into firecrawl_usage(at,kind,clinic_id,credits,run_id,job_id,tokens) values(?,?,?,?,?,?,?)",
                  (at, kind, clinic_id, credits, run_id, f"job-{clinic_id}-{run_id}", tokens))


def _finish(rid, **kw):
    R.update_run(rid, status=kw.pop("status", "done"), started_at=R.now(), finished_at=R.now(), **kw)


def test_billing_clinics_per_clinic_split_and_totals(client):
    R.set_setting("firecrawl", {"eur_per_credit": 0.01})
    r1 = R.create_run("clinic", "16104", "firecrawl", clinic_ids=["16104"])                       # billable, 40 credits, 4 new
    _finish(r1, credits_used=40, n_new=4, n_rows=9)
    _ledger(R.now(), "16104", 40, run_id=r1, tokens=500)
    r2 = R.create_run("clinic", "16104", "firecrawl", clinic_ids=["16104"])                       # free run (done, 0 credits)
    _finish(r2, credits_used=0, n_new=1, n_rows=2)
    _ledger(R.now(), "16104", 0, run_id=r2, tokens=30)
    r3 = R.create_run("clinic", "36201", "adapter", clinic_ids=["36201"])                         # adapter: no credits, 3 new
    _finish(r3, credits_used=0, n_new=3, n_rows=12)
    r4 = R.create_run("all", "", "firecrawl", clinic_ids=["36201", "36202"])                       # multi-clinic: split by ledger
    _finish(r4, credits_used=25, n_new=7)
    _ledger(R.now(), "36201", 10, run_id=r4, tokens=100)
    _ledger(R.now(), "36202", 15, run_id=r4, tokens=200)
    _ledger(R.now(), "36202", 5, run_id=None, tokens=None)                                        # orphan ledger row (no run)
    d = client.get("/api/billing/clinics?window=today&limit=30").json()
    assert d["window"]["key"] == "today" and d["price_per_credit"] == 0.01 and d["currency"] == "USD"
    rows = {r["clinic_id"]: r for r in d["rows"]}
    assert [r["clinic_id"] for r in d["rows"]] == ["16104", "36202", "36201"]                   # usd desc
    k = rows["16104"]
    assert k["clinic"] == "kbo-Heckscher-Klinikum Ingolstadt" and k["town"] == "Ingolstadt" and k["open_jobs"] == 3
    assert k["runs"] == 2 and k["runs_free"] == 1 and k["credits"] == 40 and k["tokens"] == 530 and k["usd"] == 0.4
    assert k["new_postings"] == 5 and k["cost_per_posting_usd"] == 0.08 and k["last_run_at"]
    j = rows["36202"]                                                                             # 15 (split) + 5 (orphan)
    assert j["runs"] == 2 and j["credits"] == 20 and j["tokens"] == 200 and j["usd"] == 0.2 and j["new_postings"] == 0 and j["cost_per_posting_usd"] is None
    b = rows["36201"]                                                                             # adapter run + its ledger share of r4
    assert b["runs"] == 2 and b["credits"] == 10 and b["usd"] == 0.1 and b["new_postings"] == 3 and b["open_jobs"] == 24
    t = d["totals"]
    assert t["clinics"] == 3 and t["runs"] == 6 and t["runs_free"] == 1 and t["credits"] == 70 and t["usd"] == 0.7
    assert t["new_postings"] == 8 and t["cost_per_posting_usd"] == round(0.7 / 8, 4) and t["open_jobs"] == 27 and t["tokens"] == 830


def test_billing_clinics_limit_windows_and_errors(client):
    for cid in ("16104", "36201", "36202"):
        rid = R.create_run("clinic", cid, "firecrawl", clinic_ids=[cid])
        _finish(rid, credits_used={"16104": 30, "36201": 20, "36202": 10}[cid])
    d = client.get("/api/billing/clinics?window=7d&limit=2").json()
    assert d["limit"] == 2 and [r["clinic_id"] for r in d["rows"]] == ["16104", "36201"] and d["totals"]["clinics"] == 3
    assert client.get("/api/billing/clinics?window=custom&from=2020-01-01&to=2020-01-02").json()["rows"] == []
    assert client.get("/api/billing/clinics?window=custom").status_code == 400
    assert client.get("/api/billing/clinics?window=custom&from=2026-02-02&to=2026-01-01").status_code == 400
    assert client.get("/api/billing/clinics?window=nope").status_code == 400
    empty = client.get("/api/billing/clinics?window=today").json()                              # fresh window with data: rows are present
    assert empty["totals"]["credits"] == 60 and len(empty["rows"]) == 3


def test_billing_clinics_period_falls_back_when_api_down(client, monkeypatch):
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    d = client.get("/api/billing/clinics?window=period").json()
    assert "last 30 days" in d["window"]["note"] and d["rows"] == [] and d["totals"]["usd"] == 0.0


# --- POST /api/scheduler/pause|resume (app/hunter_api.py) -----------------------------------------------------------
def test_scheduler_pause_resume_routes(client):
    from app import scheduler as S
    try:
        d = client.post("/api/scheduler/pause", json={"reason": "operator"}).json()
        assert d == {"paused": True, "reason": "operator"} and S.is_paused()
        assert client.get("/api/settings").json()["scheduler"]["paused"] is True
        d = client.post("/api/scheduler/pause").json()                                            # no body: default reason
        assert d["paused"] is True and "POST /api/scheduler/pause" in d["reason"]
        d = client.post("/api/scheduler/resume").json()
        assert d == {"paused": False, "reason": None} and not S.is_paused()
        assert client.get("/api/settings").json()["scheduler"]["paused"] is False
    finally:
        S.resume()
