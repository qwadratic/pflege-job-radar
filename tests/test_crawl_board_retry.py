"""execute()'s same-run board retry (2026-09-16, Ivan: some errors just need a restart to clear)."""
import time

import pytest

from app import config as A, data as D, runs as R
import app.crawl as CR

CLINIC = {"clinic_id": "1", "name": "Test Clinic", "town": "X", "status": "Plan-KH", "routable": True, "walled": False}


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    monkeypatch.setattr(A, "CRAWL_OUT", tmp_path / "crawl_output")
    R.init()
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [CLINIC], "by_clinic": {"1": CLINIC},
                    "facets": {}, "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "jobs", lambda: [])
    monkeypatch.setattr(D, "towns", lambda: set())
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(CR, "_post_inbox", lambda rows, log: [])
    monkeypatch.setattr(CR, "_cli", lambda args, log, timeout=1800: 0)
    monkeypatch.setattr(R, "mirror_to_supabase", lambda run: None)
    monkeypatch.setattr(CR, "plan_for", lambda *a, **k: {
        "clinics": [CLINIC], "adapter": [CLINIC], "firecrawl": [], "skipped": [], "boards": 1, "walled": 0, "credits_needed": 0, "credits_left": 0})
    board = {"kind": "vendor", "vendor": "wp_jobs", "clinics": [CLINIC]}
    monkeypatch.setattr(CR, "_boards", lambda clinics: {"https://x.example/board": board})
    monkeypatch.setattr(CR.time, "sleep", lambda *a: None)   # real code sleeps between attempts; tests don't need to
    yield


def test_board_recovers_within_3_attempts_no_issue_recorded(fresh, monkeypatch):
    calls = {"n": 0}

    def flaky(b, c, session, log):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return [{"payload": {"url": "https://x.example/1"}, "source_url": "https://x.example/1"}]

    monkeypatch.setattr(CR, "_vendor_rows", flaky)
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    assert calls["n"] == 3                     # failed, failed, succeeded -- exactly the 3-attempt ladder
    assert R.list_crawl_issues() == []         # recovered -- nothing goes in the daily report
    assert R.get_run(rid, with_log=False)["status"] == "done"


def test_board_still_failing_after_3_attempts_is_recorded(fresh, monkeypatch):
    def always_fails(b, c, session, log):
        raise RuntimeError("boom")

    monkeypatch.setattr(CR, "_vendor_rows", always_fails)
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)
    issues = R.list_crawl_issues()
    assert len(issues) == 1
    assert issues[0]["board_url"] == "https://x.example/board"
    assert issues[0]["clinic_ids"] == ["1"]
    assert "boom" in issues[0]["error"]
    assert issues[0]["run_id"] == rid


def test_verify_mode_pushes_verdicts_and_records_city_mismatch(fresh, monkeypatch):
    """mode='verify' re-checks postings instead of crawling; a page that names a different city than
    the stored one is recorded for review (this process cannot patch a posting's city itself)."""
    from app import data as D
    import pflege_jobs.verify as V

    D._snap["jobs"] = [{"posting_id": 7, "title": "Pflegefachkraft", "clinic_id": "1", "city": "Neuburg",
                        "status": "open", "external_url": "https://x.example/job/7"}]
    monkeypatch.setattr(D, "jobs", lambda: D._snap["jobs"])
    monkeypatch.setattr(V, "verify_all", lambda rows, **kw: [
        {"posting_id": 7, "verify_status": "live", "verify_http": 200, "verified_at": "2026-09-16T00:00:00+00:00",
         "verify_note": "title tokens 2/2", "method": "http", "city": "Oberhausen", "plz": "46045",
         "loc_source": "jsonld", "final_url": "https://x.example/job/7"}])
    pushed = {}
    monkeypatch.setattr("pflege_jobs.sinks.EdgeSink._post", lambda self, body: pushed.update(body) or {"verify": 1})

    rid = R.create_run("clinic", "1", "verify")
    CR.execute(rid)

    assert [r["posting_id"] for r in pushed["verify"]] == [7]
    assert set(pushed["verify"][0]) == set(V.VERIFY_FIELDS)      # extras stripped before the ingest op
    issues = R.list_crawl_issues()
    assert len(issues) == 1 and issues[0]["kind"] == "city"
    assert "Oberhausen" in issues[0]["error"] and "Neuburg" in issues[0]["error"]
    assert R.get_run(rid, with_log=False)["status"] == "done"


def test_post_inbox_drops_non_nursing_before_the_insert(monkeypatch):   # no `fresh`: it stubs _post_inbox itself
    """A board is mostly not nursing, and intake throws those rows away one step after the insert --
    1666 of 2010 rows on 2026-09-17, i.e. 83% of the inbox's daily write budget, which is what pushed
    the run into "inbox: daily limit reached for this client"."""
    from app import config as A
    posted = []
    monkeypatch.setattr(A, "rest_post", lambda path, body, **kw: posted.extend(body))
    monkeypatch.setattr(A, "rest_get", lambda *a, **kw: [])
    rows = [{"kind": "jobposting", "source_url": f"https://x/{i}", "payload": {"title": t}} for i, t in enumerate([
        "Pflegefachkraft (m/w/d) Intensiv", "Oberarzt (m/w/d) Kardiologie", "Küchenhilfe (m/w/d)",
        "Ausbildung zur Pflegefachfrau (m/w/d)", "Gesundheits- und Krankenpfleger (m/w/d)"])]

    CR._post_inbox(rows, lambda *_: None)

    assert [p["source_url"] for p in posted] == ["https://x/0", "https://x/4"]
