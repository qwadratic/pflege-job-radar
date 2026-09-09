"""Local state (SQLite) + the crawl worker queue.

Tables: crawl_runs (one per triggered crawl), run_log (lines), career_profiles (Firecrawl career discovery per clinic),
settings (json blobs by key), hunt_state + hunt_meta (app/hunter.py: one row per clinic and UTC day, and the hunter's
per-day accumulators / flags -- schema here, all reads and writes live in app/hunter.py), magic_links + sessions + customers
(app/auth.py: single-use login tokens, cookie sessions, Stripe customers -- schema here, reads/writes in app/auth.py), firecrawl_usage (credits + Extract-token delta per call; job_id = the Firecrawl agent
job the row belongs to, so an accepted submission is counted once even when both the poller and the webhook report it).
Finished runs are mirrored, best effort, into pflege_jobs.crawl_runs so the public API shows them too.
"""
import json
import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone

from . import config as A

_lock = threading.RLock()
_queue = queue.Queue()
_worker = None
_executor = None          # set by app.crawl: callable(run_id) that performs the run

SCHEMA = """
create table if not exists crawl_runs (
  run_id integer primary key autoincrement, started_at text, finished_at text, queued_at text,
  scope text, value text, mode text, status text default 'queued', n_rows integer default 0, n_new integer default 0,
  credits_used integer default 0, clinic_ids text default '[]', params text default '{}', error text, trigger text default 'api');
create table if not exists run_log (id integer primary key autoincrement, run_id integer, at text, line text);
create index if not exists run_log_run on run_log(run_id);
create table if not exists career_profiles (clinic_id text primary key, profile text, fetched_at text, credits_used integer default 0, run_id integer);
create table if not exists settings (key text primary key, value text);
create table if not exists firecrawl_usage (id integer primary key autoincrement, at text, kind text, clinic_id text, credits integer, run_id integer, job_id text, tokens integer);
create table if not exists firecrawl_events (
  id integer primary key autoincrement, at text, event_type text, job_id text, clinic_id text, run_id integer,
  success integer, credits_used integer default 0, raw text);
create index if not exists firecrawl_events_job on firecrawl_events(job_id);
create table if not exists hunt_state (
  clinic_id text, day text, name text, host text, status text default 'pending', run_id integer, cap integer,
  credits integer default 0, tokens integer, rows integer default 0, new integer default 0, attempts integer default 0,
  last_error text, updated_at text, primary key(clinic_id, day));
create index if not exists hunt_state_day on hunt_state(day);
create table if not exists hunt_meta (key text primary key, value text);
create table if not exists magic_links (
  id integer primary key autoincrement, email text, token_hash text, role text, created_at text, expires_at text, used_at text);
create index if not exists magic_links_email on magic_links(email, created_at);
create index if not exists magic_links_hash on magic_links(token_hash);
create table if not exists sessions (
  sid_hash text primary key, email text, role text, created_at text, expires_at text, last_seen_at text, stripe_customer_id text);
create table if not exists customers (email text primary key, stripe_customer_id text, status text default 'active', created_at text);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    A.DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(A.SQLITE_PATH), check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    return c


# (table, column, type) added after the table first shipped; applied by init() with a plain
# 'alter table add column' when pragma table_info says the column is missing (SQLite has no IF NOT EXISTS for columns).
MIGRATIONS = (("firecrawl_usage", "job_id", "text"),
              ("firecrawl_usage", "tokens", "integer"),      # Extract-token delta of the run (None = not measured)
              ("crawl_runs", "cancel_requested", "integer"))  # POST /api/crawl/runs/{id}/cancel; execute() polls it between boards/clinics


def _migrate(c):
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in c.execute(f"pragma table_info({table})").fetchall()}
        if cols and col not in cols:
            c.execute(f"alter table {table} add column {col} {typ}")


def init():
    with _lock, db() as c:
        c.executescript(SCHEMA)
        _migrate(c)


# --- settings -------------------------------------------------------------------------------
def get_setting(key, default=None):
    with _lock, db() as c:
        r = c.execute("select value from settings where key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_setting(key, value):
    with _lock, db() as c:
        c.execute("insert into settings(key,value) values(?,?) on conflict(key) do update set value=excluded.value",
                  (key, json.dumps(value, ensure_ascii=False)))


# --- runs ------------------------------------------------------------------------------------
def _row(r):
    d = dict(r)
    for k in ("clinic_ids", "params"):
        try:
            d[k] = json.loads(d.get(k) or ("[]" if k == "clinic_ids" else "{}"))
        except Exception:
            pass
    return d


def create_run(scope, value, mode, params=None, clinic_ids=None, trigger="api"):
    with _lock, db() as c:
        cur = c.execute("insert into crawl_runs(queued_at,scope,value,mode,params,clinic_ids,trigger) values(?,?,?,?,?,?,?)",
                        (now(), scope, value, mode, json.dumps(params or {}), json.dumps(clinic_ids or []), trigger))
        run_id = cur.lastrowid
    return run_id


def update_run(run_id, **kw):
    if not kw:
        return
    for k in ("clinic_ids", "params"):
        if k in kw and not isinstance(kw[k], str):
            kw[k] = json.dumps(kw[k])
    with _lock, db() as c:
        c.execute("update crawl_runs set " + ", ".join(f"{k}=?" for k in kw) + " where run_id=?", (*kw.values(), run_id))


def log(run_id, line):
    line = str(line)
    with _lock, db() as c:
        c.execute("insert into run_log(run_id,at,line) values(?,?,?)", (run_id, now(), line[:2000]))


def get_run(run_id, with_log=True):
    with _lock, db() as c:
        r = c.execute("select * from crawl_runs where run_id=?", (run_id,)).fetchone()
        if not r:
            return None
        d = _row(r)
        lines = c.execute("select at, line from run_log where run_id=? order by id", (run_id,)).fetchall()
    d["log"] = [f"{x['at']} {x['line']}" for x in lines] if with_log else None
    d["log_tail"] = [f"{x['at']} {x['line']}" for x in lines[-5:]]
    return d


def list_runs(limit=50):
    with _lock, db() as c:
        rows = c.execute("select * from crawl_runs order by run_id desc limit ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = _row(r)
            lines = c.execute("select at, line from run_log where run_id=? order by id desc limit 3", (r["run_id"],)).fetchall()
            d["log_tail"] = [f"{x['at']} {x['line']}" for x in reversed(lines)]
            out.append(d)
    return out


def last_finished_run():
    with _lock, db() as c:
        r = c.execute("select * from crawl_runs where finished_at is not null order by finished_at desc limit 1").fetchone()
    return _row(r) if r else None


def last_run_per_clinic():
    """clinic_id -> {at, status, mode} from the most recent finished/running run that included it."""
    out = {}
    with _lock, db() as c:
        rows = c.execute("select run_id, started_at, finished_at, status, mode, clinic_ids from crawl_runs where status in ('done','failed','running') order by run_id desc limit 500").fetchall()
    for r in rows:
        try:
            ids = json.loads(r["clinic_ids"] or "[]")
        except Exception:
            ids = []
        for cid in ids:
            if cid not in out:
                out[cid] = {"at": r["finished_at"] or r["started_at"], "status": r["status"], "mode": r["mode"], "run_id": r["run_id"]}
    return out


def active_run_count():
    with _lock, db() as c:
        return c.execute("select count(*) from crawl_runs where status in ('queued','running')").fetchone()[0]


# --- firecrawl usage / career profiles ------------------------------------------------------
def add_usage(kind, clinic_id, credits, run_id=None, job_id=None, tokens=None):
    """Charge the local ledger. With a job_id the row is keyed by it: the first call (on_submit, credits 0)
    inserts, later calls for the same job (poll result, webhook, AgentFailed) update the credits in place --
    so a job is one ledger row however many paths report it, and agent_runs_today() counts submissions.
    tokens = the run's measured Extract-token delta (None = not measured; an update never erases a value)."""
    tok = int(tokens) if isinstance(tokens, int) else None

    def write(c):
        if job_id:
            cur = c.execute("update firecrawl_usage set credits=?, tokens=coalesce(?,tokens), clinic_id=coalesce(?,clinic_id), run_id=coalesce(?,run_id) where job_id=?",
                            (int(credits or 0), tok, clinic_id, run_id, job_id))
            if cur.rowcount:
                return
        c.execute("insert into firecrawl_usage(at,kind,clinic_id,credits,run_id,job_id,tokens) values(?,?,?,?,?,?,?)",
                  (now(), kind, clinic_id, int(credits or 0), run_id, job_id, tok))

    with _lock, db() as c:
        try:
            write(c)
        except sqlite3.OperationalError:            # job_id / tokens column not there yet (a writer that never ran init())
            c.executescript(SCHEMA)
            _migrate(c)
            write(c)


def agent_runs_today():
    """Accepted Firecrawl agent submissions today (UTC midnight to now): firecrawl_usage rows of kind
    'jobs' or 'career' that carry a job id. Rows without a job id (legacy, webhook-only 'webhook' kind,
    manual charges) are not submissions and do not count. Firecrawl's 5 free daily agent runs are what
    this is measured against (pflege_jobs.sources.firecrawl_agent.FREE_RUNS_PER_DAY)."""
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    q = "select count(*) from firecrawl_usage where kind in ('jobs','career') and job_id is not null and job_id != '' and at >= ?"
    with _lock, db() as c:
        try:
            r = c.execute(q, (midnight,)).fetchone()
        except sqlite3.OperationalError:            # table or job_id column not there yet (process older than the migration)
            c.executescript(SCHEMA)
            _migrate(c)
            r = c.execute(q, (midnight,)).fetchone()
    return int(r[0])


def _usage_sum(column, days=None, hours=None):
    """Sum of one firecrawl_usage column. hours takes precedence over days when both are given."""
    secs = hours * 3600 if hours else (days * 86400 if days else None)
    with _lock, db() as c:
        if secs:
            since_iso = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - secs, timezone.utc).isoformat(timespec="seconds")
            r = c.execute(f"select coalesce(sum({column}),0) from firecrawl_usage where at>=?", (since_iso,)).fetchone()
        else:
            r = c.execute(f"select coalesce(sum({column}),0) from firecrawl_usage").fetchone()
    return int(r[0])


def usage_total(days=None, hours=None):
    """Sum of credits recorded via add_usage()."""
    return _usage_sum("credits", days, hours)


def tokens_total(days=None, hours=None):
    """Sum of the Extract-token deltas recorded via add_usage(tokens=...); rows without a measurement count 0."""
    return _usage_sum("tokens", days, hours)


# --- firecrawl webhook events -----------------------------------------------------------------
def add_firecrawl_event(event_type, job_id, clinic_id=None, run_id=None, success=None, credits_used=None, raw=None):
    with _lock, db() as c:
        c.execute("insert into firecrawl_events(at,event_type,job_id,clinic_id,run_id,success,credits_used,raw) values(?,?,?,?,?,?,?,?)",
                  (now(), event_type, job_id, clinic_id, run_id, None if success is None else int(bool(success)),
                   int(credits_used or 0), json.dumps(raw, ensure_ascii=False) if raw is not None else None))


def _event_row(r):
    d = dict(r)
    try:
        d["raw"] = json.loads(d["raw"]) if d.get("raw") else None
    except Exception:
        pass
    d["success"] = None if d.get("success") is None else bool(d["success"])
    return d


def firecrawl_events_for_job(job_id):
    with _lock, db() as c:
        rows = c.execute("select * from firecrawl_events where job_id=? order by id", (job_id,)).fetchall()
    return [_event_row(r) for r in rows]


def firecrawl_event_exists(job_id, event_type=None):
    with _lock, db() as c:
        if event_type:
            r = c.execute("select 1 from firecrawl_events where job_id=? and event_type=? limit 1", (job_id, event_type)).fetchone()
        else:
            r = c.execute("select 1 from firecrawl_events where job_id=? limit 1", (job_id,)).fetchone()
    return bool(r)


def save_career_profile(clinic_id, profile, credits, run_id=None):
    with _lock, db() as c:
        c.execute("insert into career_profiles(clinic_id,profile,fetched_at,credits_used,run_id) values(?,?,?,?,?) "
                  "on conflict(clinic_id) do update set profile=excluded.profile, fetched_at=excluded.fetched_at, credits_used=excluded.credits_used, run_id=excluded.run_id",
                  (clinic_id, json.dumps(profile, ensure_ascii=False), now(), int(credits or 0), run_id))


def career_profiles():
    with _lock, db() as c:
        rows = c.execute("select * from career_profiles").fetchall()
    out = {}
    for r in rows:
        try:
            p = json.loads(r["profile"])
        except Exception:
            p = {}
        p = dict(p)
        p["fetched_at"] = r["fetched_at"]
        p["credits_used"] = r["credits_used"]
        out[r["clinic_id"]] = p
    return out


# --- worker ---------------------------------------------------------------------------------
def enqueue(run_id):
    _queue.put(run_id)


def _loop():
    while True:
        run_id = _queue.get()
        try:
            if (get_run(run_id, with_log=False) or {}).get("status") == "cancelled":
                continue    # cancelled while still queued (POST /api/crawl/runs/{id}/cancel); finally still runs task_done()
            update_run(run_id, status="running", started_at=now())
            log(run_id, "run started")
            _executor(run_id)
        except Exception as e:                                   # executor sets its own status; this is the last resort
            log(run_id, f"FAILED: {type(e).__name__}: {str(e)[:400]}")
            update_run(run_id, status="failed", finished_at=now(), error=str(e)[:400])
        finally:
            _queue.task_done()


def start_worker(executor):
    """Start the single background worker (one crawl at a time keeps host politeness simple)."""
    global _worker, _executor
    _executor = executor
    init()
    # runs left 'running' by a previous process are dead
    with _lock, db() as c:
        c.execute("update crawl_runs set status='failed', error='process restarted', finished_at=? where status='running'", (now(),))
        stale = [r[0] for r in c.execute("select run_id from crawl_runs where status='queued' order by run_id").fetchall()]
    for rid in stale:
        _queue.put(rid)
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_loop, name="crawl-worker", daemon=True)
        _worker.start()


def mirror_to_supabase(run):
    """Best effort copy of a finished run into pflege_jobs.crawl_runs (via the ingest function's crawl_run op;
    the REST key has no insert grant on that table)."""
    try:
        from pflege_jobs import config as C
        from pflege_jobs.sinks import EdgeSink
        src = C.SOURCES.get("firecrawl_agent", {}).get("source_id", 25) if run.get("mode") == "firecrawl" else C.SOURCES["employer_ats"]["source_id"]
        notes = (f"app run {run['run_id']}: {run.get('scope')}={run.get('value')} mode={run.get('mode')} status={run.get('status')} "
                 f"rows={run.get('n_rows') or 0} new={run.get('n_new') or 0} credits={run.get('credits_used') or 0}"
                 + (f" error={run.get('error')}" if run.get("error") else ""))
        EdgeSink()._post({"crawl_run": {"source_id": src, "notes": notes[:2000],
                                        "slice_counts": {"rows": run.get("n_rows") or 0, "new": run.get("n_new") or 0, "credits_used": run.get("credits_used") or 0,
                                                         "clinics": len(run.get("clinic_ids") or []), "app_run_id": run["run_id"]}}})
    except Exception as e:                                       # never fail a run over monitoring
        log(run["run_id"], f"mirror to supabase skipped: {str(e)[:120]}")
