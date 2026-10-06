"""The crawl worker as its own process (TASK-435): `python -m app.crawl_worker`, run by deploy/pflege-crawl.service.

The web process (pflege-web) only inserts a `crawl_runs` row with status 'queued' (runs.create_run, called by
schedules.fire, POST /api/crawl, ...) and reads status; this process takes queued rows in run_id order and runs
them one at a time (host politeness) through crawl.dispatch. A restart of pflege-web leaves a running run alone;
a restart of this service is the one thing that fails it: rows left 'running' by the previous worker process
are marked failed ('process restarted') when the next one starts. Cancel is the same flag as before
(POST /api/crawl/runs/{id}/cancel): a queued row becomes 'cancelled' and is never taken, a running one is polled
by crawl.execute() through `cancel_requested`.
"""
import os
import time

from . import crawl as CR
from . import runs as R

POLL_SECONDS = 5


def fail_stale():
    """Rows still 'running' when this process starts belong to a worker that died with its run."""
    with R._lock, R.db() as c:
        c.execute("update crawl_runs set status='failed', error='process restarted', finished_at=? where status='running'", (R.now(),))


def next_queued():
    with R._lock, R.db() as c:
        r = c.execute("select run_id from crawl_runs where status='queued' order by run_id limit 1").fetchone()
    return r[0] if r else None


def run_one(run_id, executor):
    with R._lock, R.db() as c:
        claimed = c.execute("update crawl_runs set status='running', started_at=? where run_id=? and status='queued'",
                            (R.now(), run_id)).rowcount
    if not claimed:
        return                          # cancelled while still queued (POST /api/crawl/runs/{id}/cancel)
    try:
        R.log(run_id, "run started")
        executor(run_id)
    except Exception as e:              # the executor sets its own status; this is the last resort
        R.log(run_id, f"FAILED: {type(e).__name__}: {str(e)[:400]}")
        R.update_run(run_id, status="failed", finished_at=R.now(), error=str(e)[:400])


def drain(executor):
    while (run_id := next_queued()) is not None:
        run_one(run_id, executor)


def main():
    R.init()
    fail_stale()
    print(f"crawl worker pid {os.getpid()}", flush=True)
    while True:
        drain(CR.dispatch)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
