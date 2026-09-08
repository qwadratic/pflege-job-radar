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
