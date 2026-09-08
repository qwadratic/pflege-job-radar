"""app/hunter.py + /api/hunter/*: target selection, cap ladder, refill detection, every stop rule, resume, API --
all with stubbed FA.credits() / CR.execute (no network, no credits)."""
import threading

import pytest

from app import config as A
from app import hunter as H
from app import runs as R
from app import settings as ST
from tests.test_app_api import CLINICS, client  # noqa: F401  (fixture: stubbed snapshot, temp sqlite)


def clinic(cid, name, beds, url, status="Plan-KH", fetch="firecrawl"):
    return {"clinic_id": cid, "name": name, "beds": beds, "careers_url": url, "website": "", "status": status, "fetch": fetch}


def res(cid, run_id=1, rows=0, new=0, credits=0, error=None, max_credits_hit=False, tokens_delta=None, disagree=False):
    return {"clinic_id": cid, "name": cid, "run_id": run_id, "status": "failed" if error else "done", "rows": rows, "new": new, "credits": credits,
            "tokens_delta": tokens_delta, "error": error, "disagree": disagree, "gate": "", "notes": "", "secs": 1, "max_credits_hit": max_credits_hit,
            "fail_text": f"FAILED {error}" if error else ""}


@pytest.fixture()
def hdb(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    R.init()
    return tmp_path


class Runner:
    """run_fn stub: script[clinic_id] = list of results per attempt; records (clinic_id, cap) calls."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, c, cap):
        self.calls.append((c["clinic_id"], cap))
        return self.script[c["clinic_id"]].pop(0)


def mk(clinics, run_fn=None, cfg=None, bal=None, precheck=None, ks=None, **kw):
    bal = bal if bal is not None else {"remaining": 228, "plan": 8000, "period_end": "2026-10-01", "tokens_remaining": 3400, "free_runs_left_today": 0, "agent_runs_today": 10}
    return H.Hunter(cfg={"concurrency": 1, **(cfg or {})}, log=lambda *p: None, clinics=clinics, run_fn=run_fn or (lambda c, cap: res(c["clinic_id"])),
                    precheck_fn=precheck or (lambda c: ("run", "ok")), credits_fn=lambda: dict(bal), kill_switch_fn=ks or (lambda: (True, None)),
                    sleep=lambda s: None, **kw)


def rows_by_id(day):
    return {r["clinic_id"]: r for r in H.state_rows(day)}


# --- target selection ---------------------------------------------------------------------------
def test_targets_done_today_sibling_host_and_precheck_skip(hdb):
    day = H.today()
    cl = [clinic("A", "A", 900, "https://www.a.de/jobs"), clinic("B", "B", 500, "https://b.de/karriere"), clinic("C", "C", 300, "https://www.b.de/x"),
          clinic("D", "D", 200, "https://d.de/jobs"), clinic("E", "E", 100, "https://e.de", status="nicht_mehr_im_plan"), clinic("F", "F", 50, "https://f.de", fetch="adapter")]
    H.state_set("A", day, status="done", run_id=7, rows=3, new=1, credits=0, name="A", host="a.de")
    run = Runner({"B": [res("B", 8, rows=5, new=5, credits=47)]})
    h = mk(cl, run, precheck=lambda c: ("skip", "careers page says there are no openings right now") if c["clinic_id"] == "D" else ("run", "ok"))
    assert [c["clinic_id"] for c in h.candidates()] == ["A", "B", "C", "D"]          # beds desc; E (retired) and F (adapter) out
    reason = h.run_once(day)
    assert reason.startswith("all_updated")
    assert run.calls == [("B", 120)]                                                  # A done today: never re-run
    st = rows_by_id(day)
    assert st["A"]["run_id"] == 7 and st["A"]["status"] == "done"
    assert st["B"]["status"] == "done" and st["B"]["rows"] == 5 and st["B"]["credits"] == 47 and st["B"]["host"] == "b.de"
    assert st["C"]["status"] == "skipped" and "sibling board" in st["C"]["last_error"]
    assert st["D"]["status"] == "skipped" and "no openings" in st["D"]["last_error"]
    assert "E" not in st and "F" not in st
    assert H.day_get(day, "credits_spent") == 47 and H.day_get(day, "new_postings") == 6 and H.day_get(day, "runs") == 1


def test_sibling_host_only_after_rows(hdb):
    day = H.today()
    cl = [clinic("B", "B", 500, "https://b.de/karriere"), clinic("C", "C", 300, "https://b.de/x")]
    run = Runner({"B": [res("B", 1, rows=0, credits=27)], "C": [res("C", 2, rows=2, new=2, credits=47)]})
    h = mk(cl, run)
    h.run_once(day)
    assert [c for c, _ in run.calls] == ["B", "C"]                                    # B produced nothing -> C still runs


def test_dry_run_submits_nothing(hdb):
    day = H.today()
    cl = [clinic("A", "A", 900, "https://a.de/jobs"), clinic("B", "B", 500, "https://b.de/karriere")]
    H.state_set("A", day, status="needs_manual", last_error="x", host="a.de")
    run = Runner({})
    d = mk(cl, run, precheck=lambda c: ("skip", "no openings")).dry_run(day)
    assert d["dry_run"] and d["submitted"] == 0 and run.calls == [] and H.state_rows(day)[0]["status"] == "needs_manual"
    assert [(t["clinic_id"], t["verdict"]) for t in d["targets"]] == [("A", "needs_manual"), ("B", "skip")] and d["n_run"] == 0


# --- cap ladder ---------------------------------------------------------------------------------
def test_cap_escalation_unbilled_max_credits_then_done(hdb):
    day = H.today()
    cl = [clinic("A", "A", 900, "https://a.de/jobs")]
    run = Runner({"A": [res("A", 1, error="1 error(s), see log", max_credits_hit=True, credits=0), res("A", 2, rows=11, new=11, credits=77, tokens_delta=1155)]})
    h = mk(cl, run)
    reason = h.run_once(day)
    assert run.calls == [("A", 120), ("A", 200)]
    r = rows_by_id(day)["A"]
    assert r["status"] == "done" and r["attempts"] == 2 and r["cap"] == 200 and r["run_id"] == 2 and r["credits"] == 77 and r["tokens"] == 1155
    assert reason.startswith("all_updated") and h.consecutive_failures == 0


def test_second_max_credits_failure_is_needs_manual(hdb):
    day = H.today()
    cl = [clinic("A", "A", 900, "https://a.de/jobs"), clinic("B", "B", 100, "https://b.de/jobs")]
    run = Runner({"A": [res("A", 1, error="1 error(s), see log", max_credits_hit=True), res("A", 2, error="1 error(s), see log", max_credits_hit=True)]})
    h = mk(cl, run)
    reason = h.run_once(day)
    assert run.calls == [("A", 120), ("A", 200)]
    r = rows_by_id(day)["A"]
    assert r["status"] == "needs_manual" and r["attempts"] == 2
    assert reason.startswith("suspicious: run 2 error")                               # an error past the ladder stops the pass
    assert "B" not in rows_by_id(day)


def test_billed_failure_never_retried(hdb):
    day = H.today()
    cl = [clinic("A", "A", 900, "https://a.de/jobs")]
    run = Runner({"A": [res("A", 1, error="1 error(s), see log", max_credits_hit=True, credits=30)]})
    mk(cl, run).run_once(day)
    r = rows_by_id(day)["A"]
    assert run.calls == [("A", 120)] and r["status"] == "needs_manual" and r["attempts"] == 1 and r["credits"] == 30


def test_other_failure_needs_manual_no_retry(hdb):
    day = H.today()
    cl = [clinic("A", "A", 900, "https://a.de/jobs")]
    run = Runner({"A": [res("A", 1, error="1 error(s), see log")]})
    mk(cl, run).run_once(day)
    assert run.calls == [("A", 120)] and rows_by_id(day)["A"]["status"] == "needs_manual"


# --- refill detection ---------------------------------------------------------------------------
def test_refill_detected_within_period_not_on_period_reset(hdb):
    day = H.today()
    bal = {"remaining": 100, "period_end": "2026-10-01", "tokens_remaining": 3400}
    h = mk([], bal=bal)
    h.read_balance(day)
    assert H.day_get(day, "refills", 0) == 0 and H.meta_get("last_balance") == 100
    bal["remaining"] = 40                                                             # spend: no refill
    h.read_balance(day)
    assert H.day_get(day, "refills", 0) == 0
    bal["remaining"] = 1040                                                           # auto-reload inside the period
    h.read_balance(day)
    assert H.day_get(day, "refills", 0) == 1 and H.meta_get("pack_size_observed") == 1000
    bal.update({"remaining": 8000, "period_end": "2026-11-01"})                       # new billing period: not a refill
    h.read_balance(day)
    assert H.day_get(day, "refills", 0) == 1 and H.meta_get("last_period_end") == "2026-11-01"
    assert h.last_pools["credits"] == 8000


# --- stop rules ---------------------------------------------------------------------------------
def _pending():
    return [clinic("A", "A", 900, "https://a.de/jobs")]


def test_all_updated_and_empty_registry(hdb):
    day = H.today()
    H.state_set("A", day, status="done")
    h = mk(_pending())
    assert h.check_stop(day).startswith("all_updated: 1 target(s)")
    h2 = mk([])
    assert h2.check_stop(day).startswith("suspicious: the registry snapshot has 0")


def test_combined_bar_is_an_and(hdb):
    day = H.today()
    ST.save_firecrawl({"eur_per_credit": 0.0053})
    # refills 2 AND $5.30/posting -> stop
    H.state_set("A", day, status="done", credits=1000, new=1, attempts=1)
    H.day_set(day, "refills", 2)
    cl = _pending() + [clinic("B", "B", 100, "https://b.de/jobs")]
    slow = {"max_credits_per_hour": 10 ** 6}                                         # keep the burn-rate rule out of this test
    r = mk(cl, cfg=slow).check_stop(day)
    assert r.startswith("combined_bar: refills 2 >= 2 AND cost per posting $5.30 > $0.50")
    # refills 1 AND expensive -> no stop
    H.day_set(day, "refills", 1)
    assert mk(cl, cfg=slow).check_stop(day) is None
    # refills 2 AND cheap ($0.05) -> no stop
    H.day_set(day, "refills", 2)
    H.state_set("A", day, status="done", credits=100, new=10, attempts=1)
    assert mk(cl, cfg=slow).check_stop(day) is None
    # refills 2 AND credits spent for zero postings -> infinite cost -> stop
    H.state_set("A", day, status="done", credits=100, new=0, attempts=1)
    assert mk(cl, cfg=slow).check_stop(day).startswith("combined_bar")
    assert H.day_get(day, "stop_reason").startswith("combined_bar")


def test_suspicious_rules(hdb):
    day = H.today()
    cl = _pending() + [clinic("B", "B", 100, "https://b.de/jobs")]
    assert mk(cl).check_stop(day, res=res("A", 1, rows=1, credits=160)).startswith("suspicious: run 1 charged 160 credits (> 150)")
    assert mk(cl).check_stop(day, res=res("A", 1, rows=1, credits=50, tokens_delta=3000)).startswith("suspicious: run 1 token delta 3000")
    assert mk(cl).check_stop(day, res=res("A", 1, rows=70, credits=50)).startswith("suspicious: run 1 returned 70 rows")
    assert mk(cl).check_stop(day, res=res("A", 1, rows=1, credits=50, disagree=True)).startswith("suspicious: run 1: API creditsUsed and balance delta disagree")
    h = mk(cl); h.zero_streak = 3
    assert h.check_stop(day, res=res("A", 1, rows=0, credits=27)) == "suspicious: three billable runs in a row returned nothing"
    h = mk(cl); h.consecutive_failures = 3
    assert h.check_stop(day) == "suspicious: 3 failures in a row"
    H.state_set("B", day, status="done", credits=700, attempts=1)                     # 700 credits within the last hour
    assert mk(cl).check_stop(day).startswith("suspicious: burn rate 700.0 credits/hour > 600")
    H.state_set("B", day, status="done", credits=0)
    h = mk(cl); h.read_balance(day); h.last_pools["tokens"] = 100
    assert h.check_stop(day) == "suspicious: tokens_remaining 100 < 300"


def test_suspicious_api_errors_after_backoff(hdb):
    day = H.today()
    slept = []

    def boom():
        raise RuntimeError("HTTP 503")
    h = H.Hunter(cfg={"concurrency": 1}, log=lambda *p: None, clinics=_pending(), run_fn=lambda c, cap: res("A"), precheck_fn=lambda c: ("run", ""),
                 credits_fn=boom, kill_switch_fn=lambda: (True, None), sleep=slept.append)
    h.read_balance(day)
    assert h.api_error_streak == 5 and slept == [30, 60, 120, 300]
    assert h.check_stop(day) == "suspicious: Firecrawl API 429/5xx 5 times despite backoff"
    assert h.run_once(day).startswith("suspicious: Firecrawl API")                    # the pass submits nothing


def test_kill_switch_rules(hdb):
    day = H.today()
    cl = _pending()
    assert mk(cl, ks=lambda: (False, "kill switch DISABLE: 31.0%")).check_stop(day) == "kill_switch: kill switch DISABLE: 31.0%"
    ST.save_firecrawl({"enabled": False})
    assert mk(cl).check_stop(day) == "kill_switch: firecrawl.enabled is false"
    ST.save_firecrawl({"enabled": True})
    assert mk(cl).check_stop(day) is None
    H.stop_file().write_text("")
    assert mk(cl).check_stop(day).startswith("kill_switch: ") and "HUNTER_STOP exists" in mk(cl).check_stop(day)
    H.stop_file().unlink()
    # first match wins: combined bar outranks the kill switch, all_updated outranks everything
    H.state_set("B", day, status="done", credits=1000, new=1, attempts=1); H.day_set(day, "refills", 2)
    assert mk(cl, ks=lambda: (False, "x")).check_stop(day).startswith("combined_bar")
    H.state_set("A", day, status="done")
    assert mk(cl, ks=lambda: (False, "x")).check_stop(day).startswith("all_updated")


def test_stop_lets_in_flight_finish_and_records_reason(hdb):
    """concurrency 2: A's result trips the max-charge rule while B is in flight; B still lands in hunt_state."""
    day = H.today()
    cl = [clinic("A", "A", 900, "https://a.de/jobs"), clinic("B", "B", 500, "https://b.de/jobs"), clinic("C", "C", 100, "https://c.de/jobs")]
    gate = threading.Event()

    def run_fn(c, cap):
        if c["clinic_id"] == "A":
            return res("A", 1, rows=1, new=1, credits=160)
        gate.wait(5)
        return res(c["clinic_id"], 2, rows=2, new=2, credits=40)
    h = mk(cl, run_fn, cfg={"concurrency": 2})
    t = threading.Thread(target=lambda: h.run_once(day)); t.start()
    for _ in range(200):
        if h.stop_reason:
            break
        threading.Event().wait(0.02)
    assert h.stop_reason.startswith("suspicious: run 1 charged 160")
    gate.set(); t.join(5)
    st = rows_by_id(day)
    assert st["A"]["status"] == "done" and st["B"]["status"] == "done" and "C" not in st
    assert H.day_get(day, "stop_reason") == h.stop_reason and H.meta_get("running") is None


# --- resume ------------------------------------------------------------------------------------
def test_resume_from_state(hdb):
    day = H.today()
    cl = [clinic("A", "A", 900, "https://a.de/jobs"), clinic("B", "B", 500, "https://b.de/jobs"), clinic("C", "C", 100, "https://c.de/jobs")]
    H.state_set("A", day, status="running", cap=120, attempts=0)                      # a dead process left it running
    H.state_set("B", day, status="done", run_id=3, rows=2, new=2)
    H.state_set("C", day, status="pending", cap=200, attempts=1, last_error="max credits")   # retry scheduled before the crash
    run = Runner({"C": [res("C", 9, rows=1, new=1, credits=60)]})
    reason = mk(cl, run).run_once(day)
    st = rows_by_id(day)
    assert st["A"]["status"] == "failed" and "process restarted" in st["A"]["last_error"]
    assert st["B"]["run_id"] == 3 and run.calls == [("C", 200)] and st["C"]["status"] == "done" and st["C"]["attempts"] == 2
    assert reason.startswith("all_updated")


def test_resume_imports_todays_hunt_runs(hdb):
    """A tools/fc_hunt.py run (trigger='hunt') made earlier today counts: done -> done, failed -> needs_manual."""
    day = H.today()
    cl = [clinic("A", "A", 900, "https://a.de/jobs"), clinic("B", "B", 500, "https://b.de/jobs"), clinic("C", "C", 100, "https://c.de/jobs")]
    ra = R.create_run("clinic", "A", "firecrawl", {"max_credits": 120}, ["A"], trigger="hunt")
    R.update_run(ra, status="done", finished_at=R.now(), n_rows=11, n_new=11, credits_used=77)
    rb = R.create_run("clinic", "B", "firecrawl", {"max_credits": 60}, ["B"], trigger="hunt")
    R.update_run(rb, status="failed", finished_at=R.now(), error="1 error(s), see log")
    ra0 = R.create_run("clinic", "A", "firecrawl", {"max_credits": 60}, ["A"], trigger="hunt")     # A's earlier failed try: credits add up, done wins
    R.update_run(ra0, status="failed", finished_at=R.now(), credits_used=5, error="1 error(s), see log")
    rx = R.create_run("clinic", "C", "firecrawl", {"max_credits": 60}, ["C"], trigger="api")      # an API/experiment run counts too
    R.update_run(rx, status="done", finished_at=R.now(), n_rows=1)
    run = Runner({"C": [res("C", 9, rows=1, new=1, credits=47)]})
    reason = mk(cl, run).run_once(day)
    st = rows_by_id(day)
    assert st["A"]["status"] == "done" and st["A"]["run_id"] == ra and st["A"]["credits"] == 82 and st["A"]["new"] == 11 and st["A"]["cap"] == 120 and st["A"]["attempts"] == 2
    assert st["B"]["status"] == "needs_manual" and st["B"]["run_id"] == rb
    assert run.calls == [] and st["C"]["status"] == "done" and st["C"]["run_id"] == rx and reason.startswith("all_updated")
    assert H.day_get(day, "credits_spent") == 82 and H.day_get(day, "new_postings") == 11 and H.day_get(day, "runs") == 4   # A x2, B, C (api) imported; C not re-run


def test_helpers(hdb):
    assert H.host_of("https://www.Karriere.b.de/x?y") == "karriere.b.de" and H.host_of("") is None and H.host_of(None) is None
    assert H.cost_per_posting(0, 0, 0.0053) == 0.0 and H.cost_per_posting(77, 11, 0.0053) == 0.0371 and H.cost_per_posting(27, 0, 0.0053) == float("inf")
    ST.save_hunter({"cap": 130, "escalate_cap": 210, "max_usd_per_posting": "0.75", "concurrency": 2})
    assert ST.get_hunter()["cap"] == 130 and ST.get_hunter()["max_usd_per_posting"] == 0.75
    for bad in ({"cap": 5}, {"escalate_cap": 100}, {"concurrency": "x"}, {"max_usd_per_posting": -1}, {"concurrency": True}):
        with pytest.raises(ValueError):
            ST.save_hunter(bad)
    H.set_enabled(True); assert H.is_enabled() and ST.get_hunter()["enabled"]
    H.set_enabled(False); assert not H.is_enabled()


# --- API ---------------------------------------------------------------------------------------
@pytest.fixture()
def hclient(client, monkeypatch):
    from app import crawl as CR
    from app import hunter_api as HA
    from pflege_jobs.sources import firecrawl_agent as FA
    monkeypatch.setattr(FA, "credits", lambda *a, **k: {"remaining": 228, "plan": 8000, "period_end": "2026-10-01", "tokens_remaining": 3420, "tokens_plan": 120000,
                                                        "agent_runs_today": 10, "free_runs_left_today": 0})

    def fake_execute(rid):
        R.log(rid, "  firecrawl x y: 3 rows, 0 credits charged (API 0, credits delta 0, tokens delta 12, cap 120)")
        R.update_run(rid, status="done", finished_at=R.now(), n_rows=3, n_new=2, credits_used=0)
    monkeypatch.setattr(CR, "execute", fake_execute)
    monkeypatch.setattr(H, "precheck", lambda c: ("run", "stub"))
    HA._bg["thread"] = None
    return client


def test_api_status_start_stop_targets_settings(hclient):
    d = hclient.get("/api/hunter/status").json()
    assert d["enabled"] is False and d["running"] is False and d["stop_reason"] is None and "stub" not in d
    assert d["targets"] == {"total": 2, "pending": 2, "done": 0, "skipped": 0, "failed": 0, "needs_manual": 0, "running": 0}   # 36202 + 16104 are fetch=firecrawl
    assert d["today"]["credits"] == 0 and d["today"]["refills"] == 0 and d["today"]["cost_per_posting_usd"] == 0.0
    assert d["pools"]["credits"] == 228 and d["pools"]["tokens"] == 3420 and d["pools"]["free_runs_left_today"] == 0
    assert d["rules"]["max_refills"] == 2 and d["rules"]["max_usd_per_posting"] == 0.5 and d["rules"]["cap"] == 120
    assert hclient.post("/api/hunter/start").json()["enabled"] is True
    assert hclient.get("/api/hunter/status").json()["enabled"] is True
    s = hclient.post("/api/hunter/stop").json()
    assert s["enabled"] is False and s["stop_reason"].startswith("kill_switch")
    d = hclient.get("/api/hunter/status").json()
    assert d["enabled"] is False and d["stop_reason"].startswith("kill_switch: stopped via")
    assert hclient.get("/api/hunter/targets").json() == {"day": H.today(), "rows": []}
    r = hclient.put("/api/settings/hunter", json={"max_refills": 3, "max_usd_per_posting": 0.2})
    assert r.status_code == 200 and r.json()["max_refills"] == 3
    assert hclient.put("/api/settings/hunter", json={"cap": 1}).status_code == 422
    assert hclient.get("/api/settings").json()["hunter"]["max_refills"] == 3
    assert hclient.get("/api/hunter/status").json()["rules"]["max_refills"] == 3


def test_api_run_once(hclient):
    from app import hunter_api as HA
    r = hclient.post("/api/hunter/run-once")
    assert r.status_code == 200 and r.json()["started"]
    HA._bg["thread"].join(15)
    d = hclient.get("/api/hunter/status").json()
    assert d["stop_reason"].startswith("all_updated") and d["targets"]["done"] == 2 and d["targets"]["pending"] == 0 and d["running"] is False
    assert d["today"]["runs"] == 2 and d["today"]["new_postings"] == 4 and d["today"]["credits"] == 0 and d["last_run"]["rows"] == 3
    rows = hclient.get("/api/hunter/targets").json()["rows"]
    assert {r["clinic_id"] for r in rows} == {"36202", "16104"} and all(r["status"] == "done" and r["cap"] == 120 for r in rows)
    runs = hclient.get("/api/crawl/runs?limit=10").json()
    assert sum(1 for x in runs if x.get("trigger") == "hunter") == 2
    # a second pass finds nothing pending and stops with all_updated again; the STOP file blocks it outright
    H.stop_file().write_text("")
    assert hclient.post("/api/hunter/run-once").status_code == 409
    H.stop_file().unlink()


def test_daemon_polls_enabled_and_stop_reason(hdb, monkeypatch):
    """The daemon runs a pass only while enabled and today's stop_reason is clear; otherwise it just polls.
    Deterministic: the clock is injected (no midnight roll-over between the test's `day` and the daemon's today()),
    and a hunter pass still running in a thread from an earlier test is waited for (bounded) so its stop_reason /
    enabled writes cannot land in this test's DB while the daemon polls."""
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.name.startswith("hunter"):
            t.join(15)
    day = "2026-09-08"
    monkeypatch.setattr(H, "today", lambda: day)
    calls = []
    h = mk(_pending(), run_fn=lambda c, cap: res("A", 1, rows=1, new=1))
    monkeypatch.setattr(h, "run_once", lambda d=None: calls.append(d) or "all_updated: x")
    polls = []

    def sleep(s):
        polls.append(s)
        if len(polls) == 1:
            H.set_enabled(True)                                                       # POST /api/hunter/start
        if len(polls) == 3:
            H.day_set(day, "stop_reason", None); H.set_enabled(True)                  # human re-enables
        if len(polls) == 4:
            raise KeyboardInterrupt
    h.sleep = sleep
    H.set_enabled(False)
    with pytest.raises(KeyboardInterrupt):
        h.daemon(poll=60)
    assert polls == [60, 60, 60, 60]
    assert calls == [day, day]                       # poll 1: disabled; poll 2: enabled -> pass; poll 3: stop_reason set -> no pass; poll 4: cleared -> pass


def test_single_instance_lock(hdb):
    assert H.lock_held() is False
    f = H.acquire_lock()
    assert f is not None and H.lock_held() is True and H.acquire_lock() is None
    f.close()
    assert H.lock_held() is False
