"""Proof tests for TASK-283.7's one-time wa_job_runs -> wa_job_state migration
(store._migrate_job_runs_to_job_state, 2026-10-05, Ivan: "job runs - can just extend the record
about currently running stuff + migrate (migration must reduce storage space)"). Every other
job_run()/job_run_summary() behavior test lives in tests/test_wa_store_ops_mirror.py; this file is
only the migration itself: (1) an old-schema database full of synthetic history comes out the
other side with the exact same per-job answers store.job_run_summary used to give (ok_24h/
failed_24h bounded by hour-bucket rounding, explained at the assertion below) and a markedly
smaller file, and (2) running it twice, or from several threads/processes at once, converts
exactly once and never errors.

Never touches data/wa.sqlite (CLAUDE.md "no safety nets" plus this repo's standing "tests never
reach the live rail" rule): every database here is a tmp_path file this test itself created and
populated directly against the OLD schema -- store.py has not had a wa_job_runs table to write
through since this very fix pass, so there is no live code path left that could reach a real one."""
import os
import pathlib
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import data as D
from app.wa import asgi
from app.wa import config as C
from app.wa import store as ST

ROOT = pathlib.Path(__file__).resolve().parents[1]   # this worktree -- PYTHONPATH/cwd for the
                                                       # subprocess below must resolve `app.wa` to
                                                       # THIS worktree's own edited copy, never the
                                                       # main checkout's
# The main checkout's venv (no worktree carries its own, see "Worktree base check" /
# "Mini deploy = git push" conventions) -- the interpreter only; PYTHONPATH below still points at
# ROOT so `import app.wa...` resolves to this worktree's own files, not the main checkout's.
REAL_VENV_PY = os.path.realpath("/home/claude/repo/pflege-board/.venv/bin/python")
# Same six vars the project's own standing rule requires every ad-hoc script to scrub before it
# can touch a real db() -- belt and suspenders on top of whatever the invoking shell already did,
# since the multiprocess test below launches real `python -c` child processes of its own.
_SCRUB = ("WA_TRANSPORT", "WA_BRIDGE_URL", "WA_BRIDGE_TOKEN", "WA_BRIDGE_INBOUND_TOKEN",
          "WA_BRIDGE_PHONE_NUMBER_ID", "WA_AUTOSEND", "META_WHATSAPP_ACCESS_TOKEN")

READ_TOKEN = "test-read-token"
RH = {"Authorization": f"Bearer {READ_TOKEN}"}

# Verbatim from `git show 81794b5:app/wa/store.py` (this worktree's own base commit) -- the only
# table _migrate_job_runs_to_job_state ever touches. The rest of that commit's SCHEMA (wa_threads,
# wa_messages, ...) is untouched by this migration and identical to HEAD's own copy of the same
# tables, so reproducing it here would prove nothing this test doesn't already cover by using the
# real HEAD schema (via ST.db()/pro_api.db() below) for the "after" side.
_OLD_SCHEMA = """
create table if not exists wa_job_runs (
  id integer primary key,
  job text not null,
  started_at text not null,
  finished_at text not null,
  ok integer not null,
  counts_json text not null default '{}',
  error_code text,
  error_text text
);
create index if not exists idx_wa_job_runs_job_started on wa_job_runs(job, started_at);
"""

JOBS = ST.HEARTBEAT_JOBS  # ("catchup", "followups", "tunnel_watch", "purge_test", "agent_notes")


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


def _hour_floor(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def _build_old_db(path, *, n_per_job, span_days):
    """Writes n_per_job * len(JOBS) rows directly against the OLD wa_job_runs schema -- nothing in
    this process ever goes through ST for this part, since HEAD's store.py no longer knows how to
    create that table at all. Timestamps run from (real now - span_days) to real now, per job, at
    a fixed even cadence -- anchored to the real clock rather than a hardcoded calendar date (same
    fix as tests/test_wa_store_ops_mirror.py's own bucket tests) so the ok_24h/failed_24h window
    this proves against actually has data on both sides of its edge no matter what day this runs.

    Deterministic, no randomness: 1 run in 11 fails; of those, every other one (1 run in 22
    overall) has NO error_code at all -- the exact pre-fix shape LEGACY_UNRECORDED_ERROR_CODE
    exists for. tunnel_watch's own LAST row (the one that becomes last_error after migration) is
    forced into that same null-code shape, so the single biggest synthetic job here deterministically
    exercises the legacy_unrecorded substitution on the field every later read actually surfaces,
    not just on some row buried in the 24h window.

    -> the full row list actually written, as (job, started_dt, finished_dt, ok, error_code)
    tuples -- the only way to recompute the OLD job_run_summary's own answer afterwards, since
    reading wa_job_runs back is not possible after the migration drops it (and must not be
    attempted before either -- see the callers below)."""
    conn = sqlite3.connect(path)
    conn.executescript(_OLD_SCHEMA)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=span_days)
    step = timedelta(days=span_days) / n_per_job
    rows = []
    for job in JOBS:
        for i in range(n_per_job):
            started = start + step * i
            finished = started + timedelta(seconds=2)
            ok = (i % 11 != 0)
            error_code = None if ok else (None if i % 22 == 0 else "TimeoutError")
            if job == "tunnel_watch" and i == n_per_job - 1:
                ok, error_code = False, None   # the newest row: forces last_error through migration
            rows.append((job, started, finished, ok, error_code))
    conn.executemany(
        "insert into wa_job_runs (job, started_at, finished_at, ok, counts_json, error_code, "
        "error_text) values (?,?,?,?,?,?,?)",
        [(j, _iso(s), _iso(f), int(ok), "{}", code, None) for j, s, f, ok, code in rows])
    conn.commit()
    conn.close()
    return rows


def _old_job_run_summary(rows, job, cutoff):
    """Recomputes exactly what 81794b5's own job_run_summary (raw SQL against wa_job_runs, quoted
    in the module docstring of store._migrate_job_runs_to_job_state) would have answered for one
    job, from the in-memory rows _build_old_db wrote -- the only way to get this answer once that
    table is gone. last_error applies the SAME legacy_unrecorded substitution the old per-db()-open
    backfill (_backfill_legacy_job_run_error_codes, removed by this fix pass) used to apply before
    81794b5's job_run_summary ever saw a null error_code row -- the fair "before" value is what a
    process running the OLD binary actually displayed, backfill included, not the raw unbackfilled
    column."""
    job_rows = [r for r in rows if r[0] == job]
    last = max(job_rows, key=lambda r: r[1])
    last_ok_rows = [r for r in job_rows if r[3]]
    last_ok_at = max((r[1] for r in last_ok_rows), default=None)
    ok_24h = sum(1 for r in job_rows if r[3] and r[1] >= cutoff)
    failed_24h = sum(1 for r in job_rows if not r[3] and r[1] >= cutoff)
    last_error = None if last[3] else {"code": last[4] or ST.LEGACY_UNRECORDED_ERROR_CODE}
    return {"last_run_at": _iso(last[1]), "last_ok_at": _iso(last_ok_at) if last_ok_at else None,
            "last_error": last_error, "ok_24h": ok_24h, "failed_24h": failed_24h}


# --- the actual proof: same answers, smaller file ----------------------------------------------

def test_migration_preserves_job_summaries_within_the_documented_bound_and_shrinks_the_file(
        tmp_path, monkeypatch):
    path = tmp_path / "wa.sqlite"
    rows = _build_old_db(path, n_per_job=1000, span_days=3)   # 5 jobs * 1000 = 5000 rows, 3 days
    size_before = path.stat().st_size

    cutoff_before = datetime.now(timezone.utc) - timedelta(hours=24)
    before = {job: _old_job_run_summary(rows, job, cutoff_before) for job in JOBS}

    monkeypatch.setattr(C, "SQLITE_PATH", path)
    # The migration itself (store._migrate_job_runs_to_job_state, called from store._migrate, called
    # from every store.db()) is what CLAUDE.md "no safety nets" and Ivan's own instruction both hold
    # to "must reduce storage space" -- measured right here, store-scoped, before anything else
    # (queue.py's/pro_api's own extra tables, created by the TestClient startup hook below) adds any
    # schema this migration had nothing to do with.
    c = ST.db()
    c.close()
    size_after = path.stat().st_size
    wal = path.with_name(path.name + "-wal")
    shm = path.with_name(path.name + "-shm")
    size_after += (wal.stat().st_size if wal.exists() else 0) + (shm.stat().st_size if shm.exists() else 0)

    shrink = 1 - (size_after / size_before)
    assert shrink > 0.5, (f"expected the migration to shrink the file by more than half, got "
                          f"{shrink:.1%} ({size_before} -> {size_after} bytes)")

    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as ro:
        assert ro.execute(
            "select 1 from sqlite_master where type='table' and name='wa_job_runs'").fetchone() is None
        assert ro.execute("select count(*) from wa_job_state").fetchone()[0] == len(JOBS)

    # Field equivalence, read through the REAL endpoint (TestClient's startup hook calls pro_api.db()
    # -> store.db() again -- idempotent, see the dedicated tests below -- and also creates queue.py's/
    # pro_api's own unrelated tables, which is why the size assertion above happened before this).
    # Same offline env setup every other /api/wa/pro/activity test uses (tests/test_wa_pro_activity.py):
    # a fixed board snapshot and a stubbed D.refresh so this never attempts a real board crawl.
    monkeypatch.setenv("WA_API_TOKEN", READ_TOKEN)
    monkeypatch.delenv("WA_API_WRITE_TOKEN", raising=False)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    with TestClient(asgi.app) as client:
        resp = client.get("/api/wa/pro/activity", headers=RH)
        assert resp.status_code == 200
        cutoff_after = datetime.now(timezone.utc) - timedelta(hours=24)
        after_by_job = {j["job"]: j for j in resp.json()["jobs"]}

    for job in JOBS:
        b, a = before[job], after_by_job[job]
        assert a["last_run_at"] == b["last_run_at"], job
        assert a["last_ok_at"] == b["last_ok_at"], job
        assert a["last_error"] == b["last_error"], job
        # finished_at is NOT NULL on every pre-283.7 row -- no run could ever look "still running"
        # after this migration (store._migrate_job_runs_to_job_state's own docstring).
        assert a["running_since"] is None, job

        # ok_24h/failed_24h: the OLD query compared each row's exact started_at against a cutoff;
        # the NEW one sums whole-HOUR buckets (keyed off finished_at) against a cutoff taken at
        # read time. A bucket is all-in or all-out, so the only rows that can disagree between
        # "before" (cutoff_before, captured first) and "after" (whatever instant job_run_summary's
        # own datetime.now() landed on, somewhere between cutoff_before and cutoff_after, captured
        # last) are the ones whose finished_at falls in the hour-bucket starting at either cutoff's
        # own floor-hour -- outside that single (almost always shared) hour every row's membership
        # is decided the same way by both formulas, proven in the task's own design notes. NEW can
        # only ever under-count relative to this "before" (the earliest, most permissive, of the
        # three cutoffs in play), never over-count.
        boundary_hours = {_hour_floor(cutoff_before), _hour_floor(cutoff_after)}
        boundary = [r for r in rows if r[0] == job and _hour_floor(r[2]) in boundary_hours]
        bound_ok = sum(1 for r in boundary if r[3])
        bound_failed = sum(1 for r in boundary if not r[3])
        assert 0 <= b["ok_24h"] - a["ok_24h"] <= bound_ok, job
        assert 0 <= b["failed_24h"] - a["failed_24h"] <= bound_failed, job


# --- idempotent and race-safe, same discipline as ensure_campaign_schema/ -----------------------
# _migrate_agent_notes_autoincrement elsewhere in store.py (see _migrate_job_runs_to_job_state's own
# docstring for why this two-phase-check design is race-safe in the first place).

def test_migration_is_idempotent_across_repeated_sequential_opens(tmp_path, monkeypatch):
    path = tmp_path / "wa.sqlite"
    _build_old_db(path, n_per_job=20, span_days=3)
    monkeypatch.setattr(C, "SQLITE_PATH", path)

    def _snapshot():
        ro = sqlite3.connect(path)
        ro.row_factory = sqlite3.Row
        try:
            rows = sorted((dict(r) for r in ro.execute("select * from wa_job_state").fetchall()),
                          key=lambda d: d["job"])
            runs_gone = ro.execute(
                "select 1 from sqlite_master where type='table' and name='wa_job_runs'"
            ).fetchone() is None
            return rows, runs_gone
        finally:
            ro.close()

    ST.db().close()   # the one real conversion
    snapshot_1, runs_gone_1 = _snapshot()
    assert runs_gone_1

    for _ in range(2):   # "run init twice" -- a later db() call must find nothing left to convert
        ST.db().close()
        snapshot_n, runs_gone_n = _snapshot()
        assert runs_gone_n
        assert snapshot_n == snapshot_1


def test_migration_is_race_safe_across_concurrent_threads(tmp_path, monkeypatch):
    path = tmp_path / "wa.sqlite"
    rows = _build_old_db(path, n_per_job=20, span_days=3)
    monkeypatch.setattr(C, "SQLITE_PATH", path)

    errors = []

    def _open():
        try:
            ST.db().close()
        except BaseException as exc:   # pragma: no cover -- only ever populated on a real failure
            errors.append(exc)

    threads = [threading.Thread(target=_open) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors

    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    assert ro.execute(
        "select 1 from sqlite_master where type='table' and name='wa_job_runs'").fetchone() is None
    state_rows = {r["job"]: dict(r) for r in ro.execute("select * from wa_job_state").fetchall()}
    ro.close()
    assert set(state_rows) == set(JOBS)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    for job in JOBS:
        expected = _old_job_run_summary(rows, job, cutoff)
        assert state_rows[job]["last_started_at"] == expected["last_run_at"]


def _worker_env():
    env = {k: v for k, v in os.environ.items() if k not in _SCRUB}
    env["PYTHONPATH"] = str(ROOT)
    return env


_WORKER_CODE = (
    "import pathlib, sys\n"
    "from app.wa import config as C, store as ST\n"
    "C.SQLITE_PATH = pathlib.Path(sys.argv[1])\n"
    "ST.db().close()\n"
)


def test_migration_is_race_safe_across_concurrent_processes(tmp_path):
    path = tmp_path / "wa.sqlite"
    rows = _build_old_db(path, n_per_job=20, span_days=3)

    procs = [subprocess.Popen([REAL_VENV_PY, "-c", _WORKER_CODE, str(path)], cwd=ROOT,
                              env=_worker_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True)
             for _ in range(3)]
    results = [(p.wait(timeout=30), p.stdout.read(), p.stderr.read()) for p in procs]
    for returncode, _out, err in results:
        assert returncode == 0, err

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    assert conn.execute(
        "select 1 from sqlite_master where type='table' and name='wa_job_runs'").fetchone() is None
    state_rows = {r["job"]: dict(r) for r in conn.execute("select * from wa_job_state").fetchall()}
    conn.close()
    assert set(state_rows) == set(JOBS)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    for job in JOBS:
        expected = _old_job_run_summary(rows, job, cutoff)
        assert state_rows[job]["last_started_at"] == expected["last_run_at"]
