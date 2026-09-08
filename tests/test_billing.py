"""GET /api/billing: window math, free vs billable split, cost per posting, empty buckets, runs cap, Exa fallback.
SQLite in a temp dir (monkeypatched app.config.SQLITE_PATH), FA.credits() stubbed, clock frozen."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import billing as B
from app import config as A
from app import data as D
from app import runs as R
from pflege_jobs.sources import firecrawl_agent as FA

NOW = datetime(2026, 9, 8, 10, 30, tzinfo=timezone.utc)
PERIOD = {"remaining": 228, "plan": 8000, "period_start": "2026-08-19T20:01:50.000Z", "period_end": "2026-09-19T20:01:50.000Z",
          "tokens_remaining": 3420, "tokens_plan": 120000, "credits_used_hist": 222, "tokens_used_hist": 3330,
          "free_runs_per_day": 5, "agent_runs_today": 6, "free_runs_left_today": 0}


def iso(d):
    return d.isoformat(timespec="seconds")


def add_run(at, mode="firecrawl", status="done", credits=0, new=0, rows=0, clinic="36201", trigger="api", tokens=None, kind="jobs", ledger=True):
    rid = R.create_run("clinic", clinic, mode, {"max_credits": 100}, [clinic], trigger=trigger)
    R.update_run(rid, status=status, started_at=iso(at - timedelta(minutes=2)), finished_at=iso(at), credits_used=credits, n_new=new, n_rows=rows)
    if ledger and mode == "firecrawl":
        with R._lock, R.db() as c:
            c.execute("insert into firecrawl_usage(at,kind,clinic_id,credits,run_id,job_id,tokens) values(?,?,?,?,?,?,?)",
                      (iso(at), kind, clinic, credits, rid, f"job-{rid}", tokens))
    return rid


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    R.init()
    R.set_setting("firecrawl", {"eur_per_credit": 0.01})
    monkeypatch.setattr(B, "_now", lambda: NOW)
    monkeypatch.setattr(B, "_POOLS_CACHE", {})
    monkeypatch.setattr(FA, "credits", lambda **kw: dict(PERIOD))
    monkeypatch.setattr(FA, "FREE_RUNS_PER_DAY", 5)
    monkeypatch.setattr(D, "clinic", lambda cid: {"clinic_id": "36201", "name": "Krankenhaus Barmherzige Brüder"} if str(cid) == "36201" else None)
    app = FastAPI()
    app.include_router(B.router, prefix="/api")
    return TestClient(app)


def test_mounted_in_main():
    from app.main import app
    assert "/api/billing" in app.openapi()["paths"]


def test_today_window_free_vs_billable_and_cost_per_posting(client):
    add_run(NOW - timedelta(hours=3), credits=0, new=6, rows=7, tokens=30)                     # free (done, 0 credits)
    add_run(NOW - timedelta(hours=2), credits=77, new=11, rows=11, tokens=1155)                # billable
    add_run(NOW - timedelta(hours=1), status="failed", credits=0)                              # failed, not free
    add_run(NOW - timedelta(minutes=30), mode="adapter", new=3, rows=20)                       # adapter: no Firecrawl
    add_run(NOW - timedelta(days=1, hours=1), credits=50, new=1)                               # yesterday: outside 'today'
    d = client.get("/api/billing?window=today").json()
    w = d["window"]
    assert w["key"] == "today" and w["from"] == "2026-09-08T00:00:00+00:00" and w["to"] == iso(NOW) and w["granularity"] == "hour"
    t = d["totals"]
    assert t["runs"] == 4 and t["runs_free"] == 1 and t["runs_billable"] == 1 and t["runs_failed"] == 1 and t["runs_adapter"] == 1
    assert t["credits"] == 77 and t["credits_billable"] == 77 and t["credits_free"] == 0 and t["tokens"] == 1185
    assert d["price_per_credit"] == 0.01 and d["currency"] == "USD" and t["usd"] == 0.77
    assert t["new_postings"] == 20 and t["cost_per_posting_usd"] == round(0.77 / 20, 4)
    assert t["refills"] == 0
    kinds = {k["kind"]: k for k in d["by_kind"]}
    assert kinds["firecrawl_jobs"]["runs"] == 3 and kinds["firecrawl_jobs"]["credits"] == 77 and kinds["firecrawl_jobs"]["usd"] == 0.77
    assert kinds["adapter"]["runs"] == 1 and kinds["adapter"]["usd"] == 0.0
    # runs newest first, clinic names resolved, usd per run
    runs = d["runs"]
    assert [r["mode"] for r in runs] == ["adapter", "firecrawl", "firecrawl", "firecrawl"]
    assert runs[2]["credits"] == 77 and runs[2]["usd"] == 0.77 and runs[2]["free"] is False and runs[2]["clinic"] == "Krankenhaus Barmherzige Brüder"
    assert runs[3]["free"] is True and runs[3]["tokens"] == 30
    assert runs[1]["status"] == "failed" and runs[1]["free"] is False
    # pools / hist straight from FA.credits()
    assert d["pools"]["credits_remaining"] == 228 and d["pools"]["period_end"] == PERIOD["period_end"] and d["pools"]["free_runs_left_today"] == 0
    assert d["hist"] == {"credits_used_period": 222, "tokens_used_period": 3330}


def test_hour_buckets_are_continuous_with_empty_buckets(client):
    add_run(NOW - timedelta(hours=5), credits=10, new=2)
    add_run(NOW - timedelta(hours=1), credits=20, new=1)
    d = client.get("/api/billing?window=24h").json()
    s = d["series"]
    assert d["window"]["granularity"] == "hour" and len(s) == 25                       # floor(now-24h) .. floor(now) inclusive
    assert s[0]["t"] == "2026-09-07T10:00:00+00:00" and s[-1]["t"] == "2026-09-08T10:00:00+00:00"
    assert [b["t"] for b in s] == sorted(b["t"] for b in s)
    by_t = {b["t"]: b for b in s}
    assert by_t["2026-09-08T05:00:00+00:00"]["credits_billable"] == 10 and by_t["2026-09-08T09:00:00+00:00"]["credits_billable"] == 20
    assert by_t["2026-09-08T09:00:00+00:00"]["usd"] == 0.2 and by_t["2026-09-08T09:00:00+00:00"]["runs"] == 1
    empties = [b for b in s if b["runs"] == 0]
    assert len(empties) == 23 and all(b["credits_billable"] == 0 and b["credits_free"] == 0 and b["usd"] == 0 for b in empties)


def test_7d_30d_day_buckets(client):
    add_run(NOW - timedelta(days=2), credits=5, new=1)
    add_run(NOW - timedelta(days=10), credits=7, new=1)
    d7 = client.get("/api/billing?window=7d").json()
    assert d7["window"]["granularity"] == "day" and len(d7["series"]) == 8 and d7["totals"]["credits"] == 5
    assert d7["series"][0]["t"] == "2026-09-01T00:00:00+00:00" and d7["series"][-1]["t"] == "2026-09-08T00:00:00+00:00"
    d30 = client.get("/api/billing?window=30d").json()
    assert len(d30["series"]) == 31 and d30["totals"]["credits"] == 12 and d30["totals"]["runs"] == 2
    assert client.get("/api/billing?window=30d&granularity=hour").json()["window"]["granularity"] == "hour"


def test_period_window_from_firecrawl_and_fallback(client, monkeypatch):
    add_run(datetime(2026, 8, 25, 12, tzinfo=timezone.utc), credits=30, new=3)
    add_run(datetime(2026, 8, 10, 12, tzinfo=timezone.utc), credits=99, new=3)            # before the billing period
    d = client.get("/api/billing?window=period").json()
    assert d["window"]["from"] == "2026-08-19T20:01:50+00:00" and d["window"]["to"] == iso(NOW)     # period end is in the future -> clipped to now
    assert d["window"]["granularity"] == "day" and d["totals"]["credits"] == 30 and d["window"]["note"] is None
    monkeypatch.setattr(FA, "credits", lambda **kw: {"remaining": None, "error": "ConnectionError"})
    B._POOLS_CACHE.clear()
    d = client.get("/api/billing?window=period").json()
    assert d["window"]["from"] == iso(NOW - timedelta(days=30)) and "unknown" in d["window"]["note"]
    assert d["totals"]["credits"] == 129 and d["pools"]["credits_remaining"] is None and d["pools"]["error"] == "ConnectionError"


def test_custom_window(client):
    add_run(datetime(2026, 9, 1, 8, tzinfo=timezone.utc), credits=10, new=1)
    add_run(datetime(2026, 9, 3, 8, tzinfo=timezone.utc), credits=20, new=1)
    d = client.get("/api/billing?window=custom&from=2026-09-01&to=2026-09-02T00:00:00Z").json()
    assert d["window"]["from"] == "2026-09-01T00:00:00+00:00" and d["window"]["to"] == "2026-09-02T00:00:00+00:00"
    assert d["window"]["granularity"] == "hour" and len(d["series"]) == 25 and d["totals"]["credits"] == 10
    d = client.get("/api/billing?window=custom&from=2026-09-01&to=2026-09-05").json()
    assert d["window"]["granularity"] == "day" and len(d["series"]) == 5 and d["totals"]["credits"] == 30
    # a date-only 'to' is inclusive (end of that day): 09-01..09-08 counts the 09-08 08:00 run
    add_run(datetime(2026, 9, 8, 8, tzinfo=timezone.utc), credits=151, new=11)
    d = client.get("/api/billing?window=custom&from=2026-09-01&to=2026-09-08").json()
    assert d["window"]["to"] == "2026-09-08T23:59:59+00:00" and d["window"]["granularity"] == "day" and len(d["series"]) == 8
    assert d["totals"]["credits"] == 181 and d["series"][-1]["t"] == "2026-09-08T00:00:00+00:00" and d["series"][-1]["credits_billable"] == 151
    assert client.get("/api/billing?window=custom&from=2026-09-01&to=2026-09-08T00:00:00Z").json()["totals"]["credits"] == 30   # datetime 'to' stays exact
    assert client.get("/api/billing?window=custom").status_code == 400
    assert client.get("/api/billing?window=custom&from=2026-09-05&to=2026-09-01").status_code == 400
    assert client.get("/api/billing?window=nope").status_code == 400
    assert client.get("/api/billing?window=today&granularity=week").status_code == 400


def test_runs_cap_and_order(client):
    for i in range(520):
        add_run(NOW - timedelta(minutes=i + 1), mode="adapter", new=1)
    d = client.get("/api/billing?window=24h").json()
    assert d["totals"]["runs"] == 520 and d["totals"]["new_postings"] == 520
    assert len(d["runs"]) == 500 and d["runs"][0]["at"] == iso(NOW - timedelta(minutes=1))
    assert [r["at"] for r in d["runs"]] == sorted((r["at"] for r in d["runs"]), reverse=True)


def test_orphan_ledger_rows_and_allowance(client):
    """Ledger rows without a run row: free inside the day's first 5 submissions when 0 credits / 0 tokens, billable past that."""
    with R._lock, R.db() as c:
        for i in range(7):
            c.execute("insert into firecrawl_usage(at,kind,clinic_id,credits,run_id,job_id,tokens) values(?,?,?,?,?,?,?)",
                      (iso(NOW - timedelta(hours=7 - i)), "career", "36201", 0 if i < 5 else 12, None, f"j{i}", 0))
    d = client.get("/api/billing?window=today").json()
    t = d["totals"]
    assert t["runs"] == 7 and t["runs_free"] == 5 and t["runs_billable"] == 2 and t["credits"] == 24 and t["credits_billable"] == 24
    kinds = {k["kind"]: k for k in d["by_kind"]}
    assert kinds["firecrawl_career"]["runs"] == 7 and kinds["firecrawl_career"]["credits"] == 24
    assert d["runs"][0]["trigger"] == "ledger" and d["runs"][0]["free"] is False and d["runs"][-1]["free"] is True


def test_refills_from_hunt_meta(client):
    with R._lock, R.db() as c:
        c.execute("insert into hunt_meta(key,value) values(?,?)", ("2026-09-08/refills", "2"))
        c.execute("insert into hunt_meta(key,value) values(?,?)", ("2026-09-01/refills", "1"))
        c.execute("insert into hunt_meta(key,value) values(?,?)", ("last_balance", "228"))
    assert client.get("/api/billing?window=today").json()["totals"]["refills"] == 2
    assert client.get("/api/billing?window=30d").json()["totals"]["refills"] == 3
    with R._lock, R.db() as c:
        c.execute("drop table hunt_meta")
    assert client.get("/api/billing?window=today").json()["totals"]["refills"] == 0


def test_exa_from_cache_and_fallback(client, tmp_path):
    d = client.get("/api/billing?window=today").json()
    assert d["totals"]["exa_usd"] == 0 and d["totals"]["exa_searches"] == 0 and "no Exa cache" in d["exa_note"]
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry" / "exa_career_seeds.json").write_text(json.dumps({
        "16107": {"A": {"cost": {"search": {"neural": 0.007}, "total": 0.007}, "query": "q", "results": []}},
        "16211": {"A": {"cost": {"total": 0.007}, "query": "q", "results": []}, "B": {"cost": {"total": 0.01}, "query": "q2", "results": []}}}))
    d = client.get("/api/billing?window=today").json()
    assert d["totals"]["exa_usd"] == 0.024 and d["totals"]["exa_searches"] == 3 and "no timestamps" in d["exa_note"]
    exa = next(k for k in d["by_kind"] if k["kind"] == "exa")
    assert exa["runs"] == 3 and exa["usd"] == 0.024 and exa["credits"] is None


def test_pools_cached_for_60s(client, monkeypatch):
    """FA.credits() is called once per 60 s per process (badge + page fetch the endpoint back to back)."""
    calls = []
    monkeypatch.setattr(FA, "credits", lambda **kw: calls.append(1) or dict(PERIOD))
    client.get("/api/billing?window=today"); client.get("/api/billing?window=7d")
    assert len(calls) == 1
    monkeypatch.setattr(B, "_now", lambda: NOW + timedelta(seconds=59))
    client.get("/api/billing?window=today")
    assert len(calls) == 1
    monkeypatch.setattr(B, "_now", lambda: NOW + timedelta(seconds=61))
    assert client.get("/api/billing?window=today").json()["pools"]["credits_remaining"] == 228 and len(calls) == 2


def test_multi_clinic_run_reports_first_clinic_and_count(client):
    ids = ["36201"] + [str(40000 + i) for i in range(64)]
    rid = R.create_run("clinic", ",".join(ids), "adapter", {}, ids, trigger="schedule")
    R.update_run(rid, status="done", started_at=iso(NOW - timedelta(hours=2)), finished_at=iso(NOW - timedelta(hours=1)), n_rows=721, n_new=41)
    r = client.get("/api/billing?window=today").json()["runs"][0]
    assert r["clinic_id"] == "36201" and r["clinic"] == "Krankenhaus Barmherzige Brüder" and r["clinics"] == 65 and r["rows"] == 721 and r["new"] == 41
