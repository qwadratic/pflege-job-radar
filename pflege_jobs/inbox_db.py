"""The raw crawler queue, local (SQLite). Same row shape as pflege_jobs.inbox in Postgres.

Why it is not Postgres any more (Ivan, 2026-09-21): pflege_jobs.inbox carries a server-side write
rule of 2000 rows per client_id per rolling 24h, so a nightly crawl of the whole registry (12,251
rows on run 108) could not enqueue its own output at all -- intake failed on every scheduled run
from 09-19 on. The queue's only job is to hold a row between the crawl and the processing step, so
it belongs on the machine doing both. Filtering, matching and conversion move into the processing
step (pflege_jobs.cli cmd_inbox) and only the finished observations go to Postgres, a volume small
enough to be inside any limit by construction.

Why its own file and not data/app.sqlite: app/ imports pflege_jobs, never the other way round, and
the queue is written by the CLI as well as the app; and it is bulk, fast-growing data (~35 MB a
night at run 108's size) with its own retention story, next to app.sqlite's small operational state
(settings, sessions, run log) that a web process holds a lock on.

Rows are never deleted. Processing marks them processed_at/process_note; reset() clears that mark so
the same raw rows can be run through a changed classifier or matcher without re-crawling.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PATH = os.environ.get("PFLEGE_INBOX_DB") or str(Path(__file__).resolve().parent.parent / "data" / "inbox.sqlite")

SCHEMA = """
create table if not exists inbox (
  inbox_id integer primary key autoincrement,
  kind text not null, collector text, client_id text, source_host text, source_url text,
  payload text, received_at text not null, run_id integer,
  processed_at text, process_note text);
create index if not exists inbox_unprocessed on inbox(inbox_id) where processed_at is null;
create index if not exists inbox_run on inbox(run_id);
create index if not exists inbox_source_url on inbox(source_url);
"""


def connect(path=None):
    p = Path(path or PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=60)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    # A crawl writing thousands of rows must not block the drain reading them.
    c.execute("pragma journal_mode=wal")
    return c


def _row(r):
    d = dict(r)
    d["payload"] = json.loads(d["payload"]) if d.get("payload") else {}
    d["queue"] = "sqlite"
    return d


def enqueue(rows, run_id=None, path=None):
    """Store crawler rows as-is. No filtering and no dedupe: this is the raw record, and a row the
    classifier drops today may be wanted after a rule change (TASK-11, TASK-95)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(path) as c:
        c.executemany(
            "insert into inbox(kind,collector,client_id,source_host,source_url,payload,received_at,run_id)"
            " values(?,?,?,?,?,?,?,?)",
            [(r.get("kind"), r.get("collector"), r.get("client_id"), r.get("source_host"), r.get("source_url"),
              json.dumps(r.get("payload") or {}, ensure_ascii=False), now, run_id) for r in rows])
    return len(rows)


def pending(limit=1000, path=None):
    with connect(path) as c:
        return [_row(r) for r in c.execute(
            "select * from inbox where processed_at is null order by inbox_id limit ?", (limit,))]


def ack(acks, path=None):
    """[{'inbox_id':.., 'note':..}] -> rows marked processed. Mirrors the pflege-ingest inbox_ack op."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(path) as c:
        c.executemany("update inbox set processed_at=?, process_note=? where inbox_id=?",
                      [(now, a.get("note"), a["inbox_id"]) for a in acks])
    return len(acks)


def reset(run_id=None, inbox_ids=None, path=None):
    """Unmark processed rows so the next drain reprocesses them (classifier/matcher changed).
    run_id=None and inbox_ids=None resets the whole table -- say so explicitly, it is not a default."""
    with connect(path) as c:
        if inbox_ids is not None:
            q = "update inbox set processed_at=null, process_note=null where inbox_id in (%s)" % ",".join("?" * len(inbox_ids))
            return c.execute(q, list(inbox_ids)).rowcount
        if run_id is not None:
            return c.execute("update inbox set processed_at=null, process_note=null where run_id=?", (run_id,)).rowcount
        return c.execute("update inbox set processed_at=null, process_note=null").rowcount


def loaded_refs(run_id, path=None):
    """source_urls of this run's rows that the drain turned into an observation -- what reached
    Postgres, which is what the run's link-cross/verify steps work on."""
    with connect(path) as c:
        return [r["source_url"] for r in c.execute(
            "select source_url from inbox where run_id=? and process_note like 'loaded%'", (run_id,))]


def known_urls(urls, path=None):
    """Which of these source_urls the local queue has already seen (any state)."""
    urls = list(urls)
    out = set()
    with connect(path) as c:
        for i in range(0, len(urls), 500):
            batch = urls[i:i + 500]
            q = "select distinct source_url from inbox where source_url in (%s)" % ",".join("?" * len(batch))
            out.update(r["source_url"] for r in c.execute(q, batch))
    return out


def counts(path=None):
    with connect(path) as c:
        r = c.execute("select count(*) total, sum(processed_at is null) unprocessed from inbox").fetchone()
        oldest = c.execute("select received_at from inbox where processed_at is null order by inbox_id limit 1").fetchone()
    return {"total": r["total"], "unprocessed": r["unprocessed"] or 0, "oldest_unprocessed_at": oldest["received_at"] if oldest else None}
