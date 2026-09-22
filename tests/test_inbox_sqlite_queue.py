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


SEED_ROLE_CLASSES = ["pflegefachkraft", "nicht_pflege", "nicht_pflege", "ausbildung", "pflegefachkraft"]


def _seed_observation(i, title, role_class):
    return {"source_id": 30, "source_ref": f"https://x.example/job/{i}", "source_url": f"https://x.example/job/{i}",
            "employer_name": "Test Clinic", "city": "München", "role_class": role_class, "in_bavaria": True,
            "employer_class_rule": "r", "observed_at": "2026-09-21T00:00:00Z", "title": title}


@pytest.fixture()
def seeded_crawl_run(tmp_path, monkeypatch, queue):
    """execute() with one SEEDED board (softgarden/bite/pi_asp/umantis -- _seed_obs returns already-
    classified observations, not raw inbox_rows) and no network, writing into the temp queue."""
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
    monkeypatch.setattr(CR, "_boards", lambda clinics: {"https://x.example/board": {"kind": "seeded", "vendor": "umantis", "clinics": [CLINIC]}})
    monkeypatch.setattr(CR, "_seed_obs", lambda b, c, towns, log: (
        [_seed_observation(i, t, rc) for i, (t, rc) in enumerate(zip(TITLES, SEED_ROLE_CLASSES))],
        {"job_links_found": len(TITLES), "truncated": False}))
    monkeypatch.setattr(CR.time, "sleep", lambda *a: None)
    yield


class _FakeSink:
    posted, written, write_resolve_kwargs = [], [], []

    def __init__(self, *a, batch=200, **kw):
        self.batch = batch

    def write(self, obs, **kw):
        _FakeSink.written.extend(obs)
        _FakeSink.write_resolve_kwargs.append(kw.get("resolve"))
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
    monkeypatch.setattr(cli, "_drain_once", lambda a, url, H, m, towns, **kw: seen.append("postgres") or 0)

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


def test_seeded_adapter_observations_are_queued_too_not_sent_straight_to_edgesink(seeded_crawl_run, queue):
    """2026-09-21 review, headline defect: seeded boards (softgarden/bite/umantis/pi_asp) return
    already-classified observations, not raw inbox_rows -- _fetch_board handed those straight to
    EdgeSink, filtered in-process (role_class/in_bavaria/NON_PROD_HOST) with no persistence and no
    log line. Measured on run 96: 2,638 of 3,509 such rows (21% of the whole run) were dropped this
    way, unrecoverable without re-crawling. They must land in the same local queue as every other
    row, doctor/kitchen/Ausbildung rows included, so a rule change can recover them."""
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)

    queued = IB.pending(path=queue)
    assert len(queued) == 5
    assert {r["kind"] for r in queued} == {"observation"}
    assert {r["run_id"] for r in queued} == {rid}
    assert sorted(r["payload"]["title"] for r in queued) == sorted(TITLES)      # the doctor and the kitchen row too


def test_seeded_adapter_observations_are_filtered_by_the_drain_not_at_crawl_time(seeded_crawl_run, queue, monkeypatch):
    """The other half: once queued, the drain applies the exact same role_class/in_bavaria gate a
    seeded observation would have gotten straight-to-EdgeSink, just later and with a trace left on
    every dropped row."""
    rid = R.create_run("clinic", "1", "adapter")
    CR.execute(rid)                                        # queues 5 observation rows, none processed (cli stubbed)

    n, written = _drain_local(monkeypatch, queue)

    assert n == 5
    assert sorted(o["title"] for o in written) == sorted([TITLES[0], TITLES[4]])   # nursing only
    rows = IB.pending(path=queue)
    assert rows == []                                      # nothing deleted, all acked
    with IB.connect(queue) as c:
        notes = [r["process_note"] for r in c.execute("select process_note from inbox order by inbox_id")]
    assert notes[0].startswith("loaded") and "not an experienced nursing role" in notes[1]


def test_cmd_inbox_resolves_postings_once_per_run_not_once_per_page(queue, monkeypatch, tmp_path):
    """2026-09-21 review: the queue used to be tens of rows (1-2 pages); at ~9,000 rows a drain pages
    10x, and sink.write(obs, resolve=True) on every page fired resolve_postings() -- the heaviest
    server-side call in the log -- 10x a night instead of once. cmd_inbox must resolve exactly once,
    after the last page of both queues, and only when something was actually written."""
    clinics = tmp_path / "clinics.csv"
    clinics.write_text("clinic_id,name,town,beds\n1,Test Clinic,München,100\n", encoding="utf-8")
    monkeypatch.setenv("SUPABASE_URL", "https://db"); monkeypatch.setenv("SUPABASE_ANON_KEY", "k")
    IB.enqueue([_row(i, TITLES[0]) for i in range(1500)], run_id=7, path=queue)   # forces 2 local pages
    _FakeSink.posted, _FakeSink.written, _FakeSink.write_resolve_kwargs = [], [], []
    monkeypatch.setattr(cli, "EdgeSink", _FakeSink)
    monkeypatch.setattr(requests, "get", lambda u, params=None, headers=None, timeout=None:
                        type("R", (), {"json": lambda self: []})())
    monkeypatch.setattr(cli, "_drain_once", lambda a, url, H, m, towns, **kw: 0)

    cli.cmd_inbox(argparse.Namespace(clinics=str(clinics), no_ack=False, max_batches=10, inbox_db=queue,
                                     reprocess_run=None, reprocess_all=False))

    assert len(_FakeSink.written) == 1500
    assert _FakeSink.write_resolve_kwargs == [False, False]         # 2 pages, resolve deferred both times
    resolve_calls = [b for b in _FakeSink.posted if b.get("resolve") is True]
    assert len(resolve_calls) == 1, resolve_calls                   # ... and fired exactly once, at the end


def test_cmd_inbox_links_brand_new_postings_after_the_deferred_resolve(queue, monkeypatch, tmp_path):
    """2026-09-21 review, problem #1: resolve now fires once at the end of the whole drain (previous
    test), but the posting-id lookup + clinic_links push used to still happen per page, inside
    _process_rows, right after sink.write(obs, resolve=False) -- before that end-of-run resolve had
    assigned posting_id to any brand-new posting_observations row (that assignment is what resolve
    does -- sql/002_task73_migration.sql). Every posting created this run got 0 clinic_links, every
    night. Stub models the server precisely: a posting_id lookup returns nothing until a
    {'resolve': True} POST has actually happened, exactly like the review's own repro."""
    clinics = tmp_path / "clinics.csv"
    clinics.write_text("clinic_id,name,town,beds\n1,Test Clinic,München,100\n", encoding="utf-8")
    monkeypatch.setenv("SUPABASE_URL", "https://db"); monkeypatch.setenv("SUPABASE_ANON_KEY", "k")
    IB.enqueue([_row(0, TITLES[0]), _row(4, TITLES[4])], run_id=7, path=queue)   # 2 nursing rows, both new
    _FakeSink.posted, _FakeSink.written, _FakeSink.write_resolve_kwargs = [], [], []
    monkeypatch.setattr(cli, "EdgeSink", _FakeSink)
    resolved = []
    orig_post = _FakeSink._post

    def tracking_post(self, body):
        if body.get("resolve") is True:
            resolved.append(True)
        return orig_post(self, body)
    monkeypatch.setattr(_FakeSink, "_post", tracking_post)

    def fake_get(u, params=None, headers=None, timeout=None):
        if u.endswith("/rest/v1/posting_observations"):
            rows = [] if not resolved else [
                {"posting_id": 900, "source_id": 20, "source_ref": "https://x.example/job/0"},
                {"posting_id": 904, "source_id": 20, "source_ref": "https://x.example/job/4"}]
            return type("R", (), {"json": lambda self, rows=rows: rows})()
        return type("R", (), {"json": lambda self: []})()
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(cli, "_drain_once", lambda a, url, H, m, towns, **kw: 0)

    cli.cmd_inbox(argparse.Namespace(clinics=str(clinics), no_ack=False, max_batches=10, inbox_db=queue,
                                     reprocess_run=None, reprocess_all=False))

    resolve_calls = [b for b in _FakeSink.posted if b.get("resolve") is True]
    assert len(resolve_calls) == 1, resolve_calls
    links = [l for body in _FakeSink.posted for l in body.get("clinic_links", [])]
    assert sorted(l["posting_id"] for l in links) == [900, 904], _FakeSink.posted   # both brand-new postings linked
    assert all(l["clinic_id"] == "1" for l in links)


def test_cmd_inbox_does_not_resolve_when_nothing_was_written(queue, monkeypatch, tmp_path):
    """The other half: an empty run (or a run where every row was filtered out) must not call
    resolve_postings() at all -- it is the heaviest server-side call in the log, not a heartbeat."""
    clinics = tmp_path / "clinics.csv"
    clinics.write_text("clinic_id,name,town,beds\n1,Test Clinic,München,100\n", encoding="utf-8")
    monkeypatch.setenv("SUPABASE_URL", "https://db"); monkeypatch.setenv("SUPABASE_ANON_KEY", "k")
    _FakeSink.posted, _FakeSink.written = [], []
    monkeypatch.setattr(cli, "EdgeSink", _FakeSink)
    monkeypatch.setattr(cli, "_drain_once", lambda a, url, H, m, towns, **kw: 0)

    cli.cmd_inbox(argparse.Namespace(clinics=str(clinics), no_ack=False, max_batches=10, inbox_db=queue,
                                     reprocess_run=None, reprocess_all=False))

    assert not any(b.get("resolve") is True for b in _FakeSink.posted)


def test_purge_older_than_deletes_by_enqueue_time_not_processed_time(queue):
    """Retention decision (Ivan, 2026-09-21): a 30-day age-based rotation on data/inbox.sqlite, run
    as a separate maintenance step -- never baked into the crawl/drain write path. Age is
    received_at (enqueue time): an unprocessed row must still age out, or it would live forever."""
    IB.enqueue([_row(0, TITLES[0])], run_id=1, path=queue)          # will be "old"
    IB.enqueue([_row(1, TITLES[1])], run_id=2, path=queue)          # will be "recent"
    with IB.connect(queue) as c:
        c.execute("update inbox set received_at=? where inbox_id=1", ("2026-08-01T00:00:00+00:00",))
        # never processed, but old -- must still be purged (age is received_at, not processed_at)
    IB.ack([{"inbox_id": 2, "note": "loaded"}], path=queue)

    n = IB.purge_older_than(30, path=queue, now_iso="2026-09-21T00:00:00+00:00")

    assert n == 1
    remaining = [r["inbox_id"] for r in IB.pending(path=queue) or []]           # pending() only shows unprocessed...
    with IB.connect(queue) as c:
        all_ids = [r["inbox_id"] for r in c.execute("select inbox_id from inbox")]
    assert all_ids == [2]                                                       # ...so check the table directly


def test_purge_older_than_rejects_a_non_positive_window():
    """No invented default when the caller gets this wrong -- 0 or negative days would purge
    everything (or nothing meaningfully bounded), silently. Fail loudly instead."""
    with pytest.raises(ValueError):
        IB.purge_older_than(0)
    with pytest.raises(ValueError):
        IB.purge_older_than(-5)


def test_cmd_purge_inbox_reports_what_it_deleted(queue, monkeypatch, capsys):
    IB.enqueue([_row(0, TITLES[0])], run_id=1, path=queue)
    with IB.connect(queue) as c:
        c.execute("update inbox set received_at=?", ("2020-01-01T00:00:00+00:00",))

    cli.cmd_purge_inbox(argparse.Namespace(days=30, inbox_db=queue))

    assert "purged 1 row" in capsys.readouterr().out
    with IB.connect(queue) as c:
        assert c.execute("select count(*) n from inbox").fetchone()["n"] == 0
