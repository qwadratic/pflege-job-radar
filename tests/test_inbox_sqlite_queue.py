"""TASK-95: the crawler's queue is local SQLite and nothing filters at the queue.

The Postgres inbox carries a server-side rule of 2000 rows per client_id per rolling 24h, so a
nightly crawl of the whole registry (12,251 rows on run 108) could not enqueue its own output and
intake failed on every scheduled run from 2026-09-19 on. Every row now goes to
pflege_jobs.inbox_db as it was found; filtering, matching and conversion happen when the queue is
processed, and only the finished observations reach Postgres.
"""
import argparse
import time

import pytest
import requests

from app import config as A, data as D, runs as R
from pflege_jobs import cli, inbox_db as IB
from pflege_jobs.classify import norm_text
import app.crawl as CR

CLINIC = {"clinic_id": "1", "name": "Test Clinic", "town": "München", "status": "Plan-KH", "routable": True, "walled": False}
TOWNS = {norm_text("München")}

TITLES = ["Pflegefachkraft (m/w/d) Intensiv", "Oberarzt (m/w/d) Kardiologie", "Küchenhilfe (m/w/d)",
          "Ausbildung zur Pflegefachfrau (m/w/d)", "Gesundheits- und Krankenpfleger (m/w/d)"]


def _row(i, title, host="x.example"):
    return {"kind": "jobposting", "source_host": host, "source_url": f"https://{host}/job/{i}",
            "collector": "vendor-adapters-default",
            "payload": {"url": f"https://{host}/job/{i}", "title": title, "org": "Test Clinic",
                        "loc": [{"city": "München", "plz": "80331"}]}}


@pytest.fixture()
def queue(tmp_path, monkeypatch):
    monkeypatch.setattr(IB, "PATH", str(tmp_path / "inbox.sqlite"))
    return str(tmp_path / "inbox.sqlite")


@pytest.fixture()
def crawl_run(tmp_path, monkeypatch, queue):
    """execute() with one board and no network, writing into the temp queue."""
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    monkeypatch.setattr(A, "CRAWL_OUT", tmp_path / "crawl_output")
    R.init()
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [CLINIC], "by_clinic": {"1": CLINIC},
                    "facets": {}, "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "jobs", lambda: [])
    monkeypatch.setattr(D, "towns", lambda: TOWNS)
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(CR, "_cli", lambda args, log, timeout=1800: 0)
    monkeypatch.setattr(R, "mirror_to_supabase", lambda run: None)
    monkeypatch.setattr(CR, "plan_for", lambda *a, **k: {
        "clinics": [CLINIC], "adapter": [CLINIC], "firecrawl": [], "skipped": [], "boards": 1, "walled": 0,
        "credits_needed": 0, "credits_left": 0})
    monkeypatch.setattr(CR, "_boards", lambda clinics: {"https://x.example/board": {"kind": "vendor", "vendor": "wp_jobs", "clinics": [CLINIC]}})
    monkeypatch.setattr(CR, "_vendor_rows", lambda b, c, session, log, **kw: [_row(i, t) for i, t in enumerate(TITLES)])
    monkeypatch.setattr(CR.time, "sleep", lambda *a: None)
    yield


class _FakeSink:
    posted, written = [], []

    def __init__(self, *a, batch=200, **kw):
        self.batch = batch

    def write(self, obs, **kw):
        _FakeSink.written.extend(obs)
        return {"observations": len(obs)}

    def write_clinics(self, rows, log=print):
        return len(rows)

    def _post(self, body):
        _FakeSink.posted.append(body)
        return {k: (len(v) if isinstance(v, list) else 1) for k, v in body.items()}


class _FakeMatcher:
    def match(self, *a, **kw):
        return ("77402", "exact")


def _drain_local(monkeypatch, path, no_ack=False):
    """Run the real local drain with the network stubbed out. Returns the observations written."""
    _FakeSink.posted, _FakeSink.written = [], []
    monkeypatch.setattr(cli, "EdgeSink", _FakeSink)
    monkeypatch.setattr(requests, "get", lambda u, params=None, headers=None, timeout=None:
                        type("R", (), {"json": lambda self: []})())
    a = argparse.Namespace(no_ack=no_ack, inbox_db=path)
    n = cli._drain_local_once(a, "https://db", {}, _FakeMatcher(), TOWNS)
    return n, list(_FakeSink.written)


def test_the_crawler_queues_every_row_it_found_including_the_ones_intake_will_drop(crawl_run, queue):
    """AC#1. classify_role used to run before the insert and drop everything that was not an
    experienced nursing role -- 5,976 of run 108's 12,251 rows. Those rows are the ones a rule
    change would want back, and re-crawling to recover them is expensive and lossy."""
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)

    queued = IB.pending(path=queue)
    assert [r["payload"]["title"] for r in queued] == TITLES      # the doctor and the kitchen row too
    assert {r["run_id"] for r in queued} == {rid}


def test_processing_writes_only_clean_rows_to_postgres_and_keeps_the_raw_rows(queue, monkeypatch):
    """AC#2. The drain is where filtering/matching/conversion happen; raw rows are marked processed,
    never deleted, so the volume reaching Postgres is a fraction of what was crawled."""
    IB.enqueue([_row(i, t) for i, t in enumerate(TITLES)]
               + [_row(99, "Pflegefachkraft (m/w/d)", host="staging.x.example")], run_id=7, path=queue)

    n, written = _drain_local(monkeypatch, queue)

    assert n == 6
    assert sorted(o["title"] for o in written) == sorted([TITLES[0], TITLES[4]])   # nursing only
    rows = IB.pending(path=queue)
    assert rows == []                                            # all acked
    with IB.connect(queue) as c:
        stored = c.execute("select process_note from inbox order by inbox_id").fetchall()
    assert len(stored) == 6                                      # nothing deleted
    notes = [r["process_note"] for r in stored]
    assert notes[0].startswith("loaded") and "not an experienced nursing role" in notes[1]
    assert "non-production host" in notes[5]
    assert IB.loaded_refs(7, path=queue) == ["https://x.example/job/0", "https://x.example/job/4"]


def test_historical_raw_rows_can_be_reprocessed_after_a_rule_change(queue, monkeypatch):
    """AC#4. The payoff of keeping the raw rows: a classifier change is replayed over what is
    already on disk instead of re-crawling the boards."""
    IB.enqueue([_row(i, t) for i, t in enumerate(TITLES)], run_id=7, path=queue)
    _n, first = _drain_local(monkeypatch, queue)
    assert len(first) == 2

    # the rule changes: Ausbildung becomes something we keep
    from pflege_jobs import config as PC
    monkeypatch.setattr(PC, "EXCLUDED_ROLE_CLASSES", PC.EXCLUDED_ROLE_CLASSES - {"ausbildung"})
    assert IB.reset(run_id=7, path=queue) == 5

    _n, second = _drain_local(monkeypatch, queue)
    assert sorted(o["title"] for o in second) == sorted([TITLES[0], TITLES[3], TITLES[4]])


def test_cmd_inbox_drains_the_postgres_queue_too(queue, monkeypatch, tmp_path, capsys):
    """AC#3. The Postgres inbox stays for the producers that hold only the anon key (the browser
    collector, POST /api/ingest, the Firecrawl webhook); one command drains both."""
    clinics = tmp_path / "clinics.csv"
    clinics.write_text("clinic_id,name,town,beds\n1,Test Clinic,München,100\n", encoding="utf-8")
    monkeypatch.setenv("SUPABASE_URL", "https://db"); monkeypatch.setenv("SUPABASE_ANON_KEY", "k")
    IB.enqueue([_row(0, TITLES[0])], run_id=7, path=queue)
    _FakeSink.posted, _FakeSink.written = [], []
    monkeypatch.setattr(cli, "EdgeSink", _FakeSink)
    monkeypatch.setattr(requests, "get", lambda u, params=None, headers=None, timeout=None:
                        type("R", (), {"json": lambda self: []})())
    seen = []
    monkeypatch.setattr(cli, "_drain_once", lambda a, url, H, m, towns: seen.append("postgres") or 0)

    cli.cmd_inbox(argparse.Namespace(clinics=str(clinics), no_ack=False, max_batches=10, inbox_db=queue,
                                     reprocess_run=None, reprocess_all=False))

    assert seen == ["postgres"]
    assert IB.pending(path=queue) == []                       # the local queue was drained first
    assert "inbox drained 1 rows" in capsys.readouterr().out


def test_the_spend_gate_sees_urls_the_adapter_already_queued_locally(queue, monkeypatch):
    """Firecrawl is refused when the adapter already covers a board. Those rows live in the local
    queue now, so a lookup that only asks Postgres would call every one of them unseen and pay
    Firecrawl to re-find them."""
    monkeypatch.setattr(A, "rest_get", lambda *a, **kw: [])
    IB.enqueue([_row(0, TITLES[0])], run_id=7, path=queue)

    assert CR._unseen_source_urls(["https://x.example/job/0"]) == []
    assert CR._unseen_source_urls(["https://x.example/job/404"]) == ["https://x.example/job/404"]


def test_a_failed_intake_is_recorded_as_a_crawl_issue(crawl_run, monkeypatch):
    """TASK-92 AC#5 (first half): run 108 crawled 12,251 rows and stored none, and the only trace
    was one line in run_log. An intake failure is a crawl_issue now, like every other board failure."""
    monkeypatch.setattr(CR, "_cli", lambda args, log, timeout=1800: (_ for _ in ()).throw(RuntimeError("PostgREST 400: P0001")))

    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)

    issues = [i for i in R.list_crawl_issues() if i["kind"] == "intake"]
    assert len(issues) == 1 and "P0001" in issues[0]["error"] and issues[0]["run_id"] == rid
    assert R.get_run(rid, with_log=False)["status"] == "failed"
