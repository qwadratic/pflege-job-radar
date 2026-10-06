"""TASK-435: the crawl worker is its own process (app/crawl_worker.py, deploy/pflege-crawl.service); the web process
only inserts queued rows and reads status. SQLite in a temp dir, a fake executor instead of app.crawl.dispatch, no network."""
import time
from types import SimpleNamespace

import pytest

from app import config as A
from app import data as D
from app import runs as R
from app import scheduler as S
from app import schedules as SC


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    R.init()


def new_run(clinic="36201"):
    return R.create_run("clinic", clinic, "adapter", clinic_ids=[clinic])


def finish(run_id):
    R.update_run(run_id, status="done", finished_at=R.now())


# --- the web process ------------------------------------------------------------------------------------------------
def test_web_startup_leaves_a_running_run_running(db, monkeypatch):
    """A restart of pflege-web must not touch a run the crawl service is executing: before TASK-435 the web
    startup (start_worker) marked every row left 'running' as failed 'process restarted' (run 235)."""
    from app import main
    monkeypatch.setattr(D, "refresh", lambda: None)
    monkeypatch.setattr(S, "start", lambda: None)
    rid = new_run()
    R.update_run(rid, status="running", started_at=R.now())
    main._startup()
    run = R.get_run(rid, with_log=False)
    assert run["status"] == "running" and run["error"] is None and run["finished_at"] is None


def test_enqueue_only_leaves_the_row_queued_for_the_crawl_service(db):
    """The web side's whole part: create_run inserts the row 'queued', R.enqueue starts nothing in this process."""
    rid = new_run()
    R.enqueue(rid)
    assert R.get_run(rid, with_log=False)["status"] == "queued"


# --- the crawl worker -----------------------------------------------------------------------------------------------
class Stop(Exception):
    """Raised by the patched time.sleep to leave W.main()'s endless loop after one pass."""


def run_main_one_pass(monkeypatch, executor):
    """W.main() exactly as the service runs it (init, stale handling, drain, sleep), stopped at the first sleep,
    with `executor` standing in for app.crawl.dispatch."""
    from app import crawl as CR
    from app import crawl_worker as W
    monkeypatch.setattr(CR, "dispatch", executor)

    def stop(_seconds):
        raise Stop

    monkeypatch.setattr(W, "time", SimpleNamespace(sleep=stop))      # only the worker's clock, not time.sleep everywhere
    with pytest.raises(Stop):
        W.main()


def test_worker_runs_a_queued_row_and_leaves_the_status_the_executor_set(db, monkeypatch):
    rid = new_run()
    seen = []

    def executor(run_id):
        seen.append(run_id)
        R.update_run(run_id, status="done", finished_at=R.now(), n_rows=7, credits_used=2)

    run_main_one_pass(monkeypatch, executor)
    run = R.get_run(rid)
    assert seen == [rid]
    assert (run["status"], run["n_rows"], run["credits_used"], run["error"]) == ("done", 7, 2, None)
    assert run["started_at"] and any(line.endswith("run started") for line in run["log"])


def test_worker_keeps_the_failed_status_the_executor_set(db, monkeypatch):
    rid = new_run()

    def executor(run_id):
        R.update_run(run_id, status="failed", finished_at=R.now(), error="scope matched no clinic")

    run_main_one_pass(monkeypatch, executor)
    assert (R.get_run(rid)["status"], R.get_run(rid)["error"]) == ("failed", "scope matched no clinic")


def test_worker_fails_a_run_whose_executor_raises_and_goes_on_with_the_next(db, monkeypatch):
    broken, fine = new_run("1"), new_run("2")

    def executor(run_id):
        if run_id == broken:
            raise RuntimeError("boom")
        finish(run_id)

    run_main_one_pass(monkeypatch, executor)
    r = R.get_run(broken)
    assert r["status"] == "failed" and r["error"] == "boom" and r["finished_at"]
    assert any("FAILED: RuntimeError: boom" in line for line in r["log"])
    assert R.get_run(fine)["status"] == "done"


def test_worker_skips_a_row_cancelled_while_queued(db, monkeypatch):
    cancelled, live = new_run("1"), new_run("2")
    R.update_run(cancelled, status="cancelled", finished_at=R.now(), error="cancelled by operator (never started)")
    seen = []

    def executor(run_id):
        seen.append(run_id)
        finish(run_id)

    run_main_one_pass(monkeypatch, executor)
    assert seen == [live]
    run = R.get_run(cancelled)
    assert run["status"] == "cancelled" and run["started_at"] is None


def test_worker_does_not_start_a_row_cancelled_between_pick_and_start(db):
    """POST /api/crawl/runs/{id}/cancel lands after the worker chose the row but before it flipped it to running."""
    from app import crawl_worker as W
    rid = new_run()
    assert W.next_queued() == rid
    R.update_run(rid, status="cancelled", finished_at=R.now(), error="cancelled by operator (never started)")
    W.run_one(rid, lambda run_id: pytest.fail("a cancelled run must not start"))
    assert R.get_run(rid)["status"] == "cancelled"


def test_worker_startup_fails_rows_a_previous_worker_left_running(db, monkeypatch):
    stale, waiting, finished = new_run("1"), new_run("2"), new_run("3")
    R.update_run(stale, status="running", started_at=R.now())
    finish(finished)
    seen = []

    def executor(run_id):
        seen.append(run_id)
        finish(run_id)

    run_main_one_pass(monkeypatch, executor)
    r = R.get_run(stale, with_log=False)
    assert (r["status"], r["error"]) == ("failed", "process restarted") and r["finished_at"]
    assert seen == [waiting]                                   # the queued row was taken, the stale one was not rerun
    assert R.get_run(finished, with_log=False)["status"] == "done" and R.get_run(finished, with_log=False)["error"] is None


def test_two_queued_rows_run_one_after_the_other_never_together(db, monkeypatch):
    first, second = new_run("1"), new_run("2")
    order, during = [], {}

    def executor(run_id):
        order.append(run_id)
        if run_id == first:
            time.sleep(0.2)                                    # a second worker thread, if there were one, would have started by now
        during[run_id] = {r["run_id"]: r["status"] for r in R.list_runs()}
        finish(run_id)

    run_main_one_pass(monkeypatch, executor)
    assert order == [first, second]
    assert during[first] == {first: "running", second: "queued"}
    assert during[second] == {first: "done", second: "running"}


def test_a_row_queued_while_a_run_is_running_is_taken_after_it(db, monkeypatch):
    first, late = new_run("1"), []

    def executor(run_id):
        if run_id == first:
            late.append(new_run("2"))                          # POST /api/crawl while the worker is busy
        finish(run_id)

    run_main_one_pass(monkeypatch, executor)
    assert R.get_run(late[0], with_log=False)["status"] == "done"


# --- the kill switch pauses the scheduler of ANOTHER process ----------------------------------------------------------
def test_scheduler_pause_is_shared_through_the_settings_table(db):
    """crawl.kill_switch() runs in the crawl worker, the scheduler thread in the web process: the pause has to be
    state both can see."""
    SC.init()                                                   # S.status() reads the schedules table too
    assert not S.is_paused()
    S.pause("firecrawl kill switch: 31.0%")
    assert R.get_setting("scheduler_pause") == {"reason": "firecrawl kill switch: 31.0%"}
    assert S.is_paused() and S.status()["paused_reason"] == "firecrawl kill switch: 31.0%"
    S.resume()
    assert not S.is_paused() and S.status()["paused_reason"] is None
