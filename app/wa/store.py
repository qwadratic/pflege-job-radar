"""SQLite for the harness: one thread per phone number, one row per message.

Two invariants carry the whole design. ``wa_messages.wamid`` is UNIQUE, because Meta redelivers a
webhook for minutes after a non-2xx and the same message must not be answered twice. ``wa_threads.stopped``
is checked before every send, because an opt-out that can be overtaken by a queued reply is not an opt-out.
Slots are a JSON blob: they are the conversation's memory, and their vocabulary lives in app/wa/slots.py.
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone

from . import config as C

_lock = threading.RLock()

STOPPED = "opt-out"      # wa_threads.stopped_reason for a lead who asked us to stop

SCHEMA = """
create table if not exists wa_threads (
  phone text primary key,
  slots text not null default '{}',
  asked text not null default '[]',
  matches_sent_at text,
  stopped integer not null default 0,
  stopped_reason text,
  turns integer not null default 0,
  opened_at text not null,
  last_inbound_at text,
  last_outbound_at text
);
create table if not exists wa_messages (
  id integer primary key,
  phone text not null,
  direction text not null,
  wamid text unique,
  body text not null,
  kind text not null default 'text',
  meta text not null default '{}',
  at text not null
);
create index if not exists idx_wa_messages_phone_at on wa_messages(phone, at);
"""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def db():
    C.SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(C.SQLITE_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("pragma journal_mode=wal")
    c.executescript(SCHEMA)
    return c


def thread(c, phone):
    """The thread for this number, created on first contact. Never returns None: an unknown number
    is a lead, not an error."""
    row = c.execute("select * from wa_threads where phone=?", (phone,)).fetchone()
    if row is None:
        c.execute("insert into wa_threads (phone, opened_at) values (?,?)", (phone, now_iso()))
        c.commit()
        row = c.execute("select * from wa_threads where phone=?", (phone,)).fetchone()
    t = dict(row)
    t["slots"] = json.loads(t["slots"] or "{}")
    t["asked"] = json.loads(t["asked"] or "[]")
    t["stopped"] = bool(t["stopped"])
    return t


def save_thread(c, t):
    c.execute("""update wa_threads set slots=?, asked=?, matches_sent_at=?, stopped=?, stopped_reason=?,
                 turns=?, last_inbound_at=?, last_outbound_at=? where phone=?""",
              (json.dumps(t["slots"], ensure_ascii=False), json.dumps(t["asked"]), t.get("matches_sent_at"),
               int(bool(t.get("stopped"))), t.get("stopped_reason"), int(t.get("turns") or 0),
               t.get("last_inbound_at"), t.get("last_outbound_at"), t["phone"]))
    c.commit()


def record_inbound(c, phone, wamid, body, kind="text", meta=None):
    """-> True when this is the first sighting of ``wamid``, False when Meta is redelivering.

    The caller must not answer a False: the reply to that message already went out.
    """
    try:
        c.execute("insert into wa_messages (phone, direction, wamid, body, kind, meta, at) values (?,?,?,?,?,?,?)",
                  (phone, "in", wamid, body, kind, json.dumps(meta or {}, ensure_ascii=False), now_iso()))
        c.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def record_outbound(c, phone, wamid, body, kind="text", meta=None):
    c.execute("insert into wa_messages (phone, direction, wamid, body, kind, meta, at) values (?,?,?,?,?,?,?)",
              (phone, "out", wamid, body, kind, json.dumps(meta or {}, ensure_ascii=False), now_iso()))
    c.commit()


def history(c, phone, limit=50):
    rows = c.execute("select direction, body, kind, at from wa_messages where phone=? order by id desc limit ?",
                     (phone, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def threads(c, limit=200):
    rows = c.execute("select * from wa_threads order by coalesce(last_inbound_at, opened_at) desc limit ?",
                     (limit,)).fetchall()
    out = []
    for r in rows:
        t = dict(r)
        t["slots"] = json.loads(t["slots"] or "{}")
        t["asked"] = json.loads(t["asked"] or "[]")
        t["stopped"] = bool(t["stopped"])
        out.append(t)
    return out
