"""The write-ahead journal and the first-body-wins idempotency store (TASK-130, TASK-114).

Lives on the remote machine, next to the phone, because that is where the uncertainty is: the
window between "keys were pressed" and "a tick was read" is 20-60 s on this rail and it is the
dominant failure class, not an edge case. A ledger on our VPS would be a record of what we asked
for; this one is a record of what the handset was told to do.

FOUR RULES, and they are the task:
  1. The row is written BEFORE the send is attempted. If the process dies between the write and
     the confirm, the row stays ``attempting`` -- which is the truth, and the only honest state.
  2. Same key, same body -> replay the original result, send nothing.
  3. Same key, DIFFERENT body -> record a body_mismatch, return the first body's outcome, send
     nothing. First body wins, always. A regenerated reply must never overwrite a delivered one.
  4. ``attempting`` is resolvable only by a reconcile, and only ``confirmed_absent`` authorises a
     resend. An automatic retry out of ``attempting`` is a duplicate message to a real person.

WHAT IS NOT STORED: message bodies and nothing derived from them but a sha256. The phone number is
stored because reconcile has to be able to re-open the chat; it is never written to a log line
(``thread_tag`` is what the logs get).

THE ONE EXCEPTION, AND IT IS DELIBERATE (TASK-147): ``broadcast_item.body`` holds the text of an
outbound message that has not been sent yet. A broadcast that survives a restart has to be able to
say what it was still going to type, and a queue that forgot its own text would resume by sending
nothing. These are our own outreach lines, not a candidate's words; they are swept with the rest of
the ledger once the run is finished, and an item's row is the only place they live on this machine.
Inbound bodies are still never stored: they go to the outbox and leave.

Counting for the governor lives here too, because the quota question is "what did this handset
attempt", and only the ledger knows. Anything that reached ``attempting`` counts against the cap
even if it was never confirmed -- a message that may have gone out is a message that went out, as
far as a cap is concerned.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import timedelta, timezone

# --- states -----------------------------------------------------------------------------------
ATTEMPTING = "attempting"        # written before the first keystroke; only reconcile resolves it
SENT = "sent"                    # a verified delivery tick was read off the bubble. Terminal.
UNCONFIRMED = "unconfirmed"      # keys were pressed, no tick. 504. Never auto-resent.
ABSENT = "absent"                # reconcile proved nothing went out. Authorises one resend.
NOT_ATTEMPTED = "not_attempted"  # refused before any keystroke (lock busy, chat would not open)

#: states in which the same key may be sent again
RESENDABLE = frozenset({ABSENT, NOT_ATTEMPTED})

# --- broadcast states (TASK-147) ---------------------------------------------------------------
RUN_OPEN = "open"                # the runner may pick items out of it
RUN_STOPPED = "stopped"          # a caller pulled the handle; queued items stay queued
RUN_DONE = "done"                # nothing queued is left
ITEM_QUEUED = "queued"           # not attempted yet, or deferred by the governor until next_attempt_at
ITEM_SENT = "sent"               # a verified delivery tick. Terminal, and never attempted again
ITEM_REFUSED = "refused"         # refused before a key was pressed, and not by pacing. Terminal
ITEM_FAILED = "failed"           # the phone was touched and the outcome is not a tick. Terminal
#: An item in one of these is finished with, whatever the run does next.
ITEM_TERMINAL = frozenset({ITEM_SENT, ITEM_REFUSED, ITEM_FAILED})
#: states that consumed a slot on the handset, whether or not they were confirmed
SPENT = frozenset({ATTEMPTING, SENT, UNCONFIRMED})

#: TASK-130 AC#9. Ledger rows and journal lines are kept 30 days; the mini has 457 G free but a
#: candidate's thread metadata is not something to keep forever in a shared home directory.
LEDGER_RETENTION_DAYS = 30
#: Inbound events already acked by our VPS are handed over; 7 days is the batch-result retention.
INBOUND_RETENTION_DAYS = 7

SCHEMA = """
create table if not exists outbound (
  client_msg_id text primary key,
  to_phone      text not null,
  thread_tag    text not null,
  kind          text not null,
  body_sha256   text not null,
  body_len      integer not null,
  state         text not null,
  attempts      integer not null default 0,
  tick          text,
  tick_state    text,
  bubble_clock  text,
  detail        text,
  created_at    text not null,
  attempted_at  text,
  resolved_at   text
);
create index if not exists idx_outbound_attempt on outbound(attempted_at);
create index if not exists idx_outbound_phone on outbound(to_phone, attempted_at);
create table if not exists body_mismatch (
  id            integer primary key,
  client_msg_id text not null,
  seen_sha256   text not null,
  seen_at       text not null
);
create table if not exists journal (
  id            integer primary key,
  at            text not null,
  client_msg_id text,
  event         text not null,
  detail        text
);
create table if not exists inbound (
  id            integer primary key autoincrement,
  inbound_key   text not null unique,
  received_at   text not null,
  acked_at      text,
  payload       text not null
);
create table if not exists broadcast_run (
  run_id         text primary key,
  created_at     text not null,
  note           text,
  state          text not null,
  stop_requested integer not null default 0,
  pacing         text not null,
  finished_at    text
);
create table if not exists broadcast_item (
  run_id          text not null,
  client_msg_id   text not null,
  position        integer not null,
  to_phone        text not null,
  thread_tag      text not null,
  action          text not null,
  body            text not null,
  body_sha256     text not null,
  status          text not null,
  attempts        integer not null default 0,
  code            text,
  detail          text,
  next_attempt_at text,
  updated_at      text not null,
  primary key (run_id, client_msg_id)
);
create index if not exists idx_item_status on broadcast_item(run_id, status, position);
create table if not exists audit (
  id            integer primary key,
  at            text not null,
  operation     text not null,
  chat_title    text not null,
  chat_tag      text not null,
  to_phone      text,
  verified      integer not null,
  detail        text not null
);
"""


def utc(now):
    """RFC3339 UTC, milliseconds, absolute. Never a duration: a skewed clock must not move a cap."""
    if now.tzinfo is None:
        raise ValueError("naive datetime reached the ledger")
    return now.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _audit_row(row):
    """One audit row as JSON-ready data: the flag as a bool, the detail as the object it was."""
    return {**dict(row), "verified": bool(row["verified"]), "detail": json.loads(row["detail"])}


def thread_tag(phone):
    """A phone number that is safe in a log line. PII: the number itself never appears in one."""
    return hashlib.sha256(str(phone).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Entry:
    client_msg_id: str
    to_phone: str
    thread_tag: str
    kind: str
    body_sha256: str
    body_len: int
    state: str
    attempts: int
    tick: str | None
    tick_state: str | None
    bubble_clock: str | None
    detail: str | None
    created_at: str
    attempted_at: str | None
    resolved_at: str | None


class Ledger:
    def __init__(self, path):
        # check_same_thread=False: ThreadingHTTPServer answers health and outbox while a send is
        # in flight. Every write goes through _lock, so there is one writer at a time.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("pragma journal_mode=wal")
        self._db.executescript(SCHEMA)
        self._db.commit()
        self._lock = threading.RLock()

    def close(self):
        self._db.close()

    # --- journal ------------------------------------------------------------------------------
    def note(self, now, event, client_msg_id=None, **detail):
        with self._lock:
            self._db.execute(
                "insert into journal(at, client_msg_id, event, detail) values(?,?,?,?)",
                (utc(now), client_msg_id, event, json.dumps(detail, sort_keys=True)))
            self._db.commit()

    # --- idempotency --------------------------------------------------------------------------
    def get(self, client_msg_id):
        row = self._db.execute(
            "select * from outbound where client_msg_id=?", (client_msg_id,)).fetchone()
        return Entry(**dict(row)) if row else None

    def classify(self, client_msg_id, body_sha256, now):
        """-> ('proceed'|'replay'|'mismatch', Entry|None). The only place rules 2 and 3 live.

        'proceed' means: nothing under this key has been attempted, or a reconcile proved the last
        attempt never left the phone. Anything else sends nothing.
        """
        entry = self.get(client_msg_id)
        if entry is None:
            return "proceed", None
        if entry.body_sha256 != body_sha256:
            with self._lock:
                self._db.execute(
                    "insert into body_mismatch(client_msg_id, seen_sha256, seen_at) values(?,?,?)",
                    (client_msg_id, body_sha256, utc(now)))
                self._db.commit()
            self.note(now, "body_mismatch", client_msg_id, state=entry.state)
            return "mismatch", entry
        if entry.state in RESENDABLE:
            return "proceed", entry
        return "replay", entry

    def mismatch_count(self, client_msg_id):
        row = self._db.execute(
            "select count(*) c from body_mismatch where client_msg_id=?", (client_msg_id,)).fetchone()
        return row["c"]

    # --- the write-ahead write ------------------------------------------------------------------
    def begin(self, client_msg_id, *, phone, kind, body_sha256, body_len, now):
        """Rule 1. After this returns, a crash is indistinguishable from a send in flight -- which
        is correct, because from the outside it is."""
        stamp = utc(now)
        with self._lock:
            self._db.execute(
                """insert into outbound(client_msg_id, to_phone, thread_tag, kind, body_sha256,
                                        body_len, state, attempts, created_at, attempted_at)
                   values(?,?,?,?,?,?,?,1,?,?)
                   on conflict(client_msg_id) do update set
                     state=excluded.state, attempts=outbound.attempts+1,
                     attempted_at=excluded.attempted_at, resolved_at=null, detail=null,
                     tick=null, tick_state=null, bubble_clock=null""",
                (client_msg_id, phone, thread_tag(phone), kind, body_sha256, body_len,
                 ATTEMPTING, stamp, stamp))
            self._db.commit()
        self.note(now, "begin", client_msg_id, kind=kind, thread=thread_tag(phone))
        return self.get(client_msg_id)

    def _resolve(self, client_msg_id, state, now, *, tick=None, tick_state=None, clock=None,
                 detail=None):
        with self._lock:
            self._db.execute(
                """update outbound set state=?, tick=?, tick_state=?, bubble_clock=?, detail=?,
                          resolved_at=? where client_msg_id=?""",
                (state, tick, tick_state, clock, detail, utc(now), client_msg_id))
            self._db.commit()
        self.note(now, state, client_msg_id, tick=tick, detail=detail)
        return self.get(client_msg_id)

    def mark_sent(self, client_msg_id, now, *, tick, tick_state, clock):
        return self._resolve(client_msg_id, SENT, now, tick=tick, tick_state=tick_state, clock=clock)

    def mark_unconfirmed(self, client_msg_id, now, *, detail):
        return self._resolve(client_msg_id, UNCONFIRMED, now, detail=detail)

    def mark_not_attempted(self, client_msg_id, now, *, detail):
        return self._resolve(client_msg_id, NOT_ATTEMPTED, now, detail=detail)

    def mark_absent(self, client_msg_id, now, *, evidence):
        """Only a reconcile calls this, and only this state authorises a resend."""
        return self._resolve(client_msg_id, ABSENT, now, detail=evidence)

    def unresolved(self):
        """-> entries a reconcile has to answer for, oldest first."""
        rows = self._db.execute(
            "select * from outbound where state in (?,?) order by attempted_at",
            (ATTEMPTING, UNCONFIRMED)).fetchall()
        return [Entry(**dict(r)) for r in rows]

    # --- counting, for the governor --------------------------------------------------------------
    def count_spent(self, start, end, *, phone=None, kind=None):
        """How many sends this handset spent in [start, end). Counts attempting and unconfirmed."""
        sql = ["select count(*) c from outbound where attempted_at >= ? and attempted_at < ?",
               "and state in (%s)" % ",".join("?" * len(SPENT))]
        args = [utc(start), utc(end), *sorted(SPENT)]
        if phone is not None:
            sql.append("and to_phone = ?")
            args.append(phone)
        if kind is not None:
            sql.append("and kind = ?")
            args.append(kind)
        return self._db.execute(" ".join(sql), args).fetchone()["c"]

    def last_spent_at(self, *, phone=None, kind=None):
        """-> RFC3339 string of the most recent attempt, or None."""
        sql = ["select max(attempted_at) m from outbound where state in (%s)" % ",".join("?" * len(SPENT))]
        args = [*sorted(SPENT)]
        if phone is not None:
            sql.append("and to_phone = ?")
            args.append(phone)
        if kind is not None:
            sql.append("and kind = ?")
            args.append(kind)
        return self._db.execute(" ".join(sql), args).fetchone()["m"]

    def queue_counts(self):
        rows = self._db.execute("select state, count(*) c from outbound group by state").fetchall()
        return {r["state"]: r["c"] for r in rows}

    # --- inbound handover (GET /v1/outbox) --------------------------------------------------------
    def append_inbound(self, inbound_key, payload, now):
        """-> the row id, or None when this event was already recorded.

        The pull is the contract, the 200 body is an optimisation (plan section 4, graft from B):
        an event sits here until our VPS has pulled it, so a tunnel outage delays inbound instead
        of losing it.
        """
        with self._lock:
            try:
                cur = self._db.execute(
                    "insert into inbound(inbound_key, received_at, payload) values(?,?,?)",
                    (inbound_key, utc(now), json.dumps(payload, sort_keys=True)))
            except sqlite3.IntegrityError:
                return None
            self._db.commit()
            return cur.lastrowid

    def pull_inbound(self, after=0, limit=None):
        """-> events with id > after, oldest first. No default page size: the caller decides, and
        a truncation we invented here would silently drop a candidate's message."""
        sql = "select id, received_at, payload from inbound where id > ? order by id"
        args = [int(after)]
        if limit is not None:
            sql += " limit ?"
            args.append(int(limit))
        return [{"id": r["id"], "received_at": r["received_at"], "payload": json.loads(r["payload"])}
                for r in self._db.execute(sql, args).fetchall()]

    def ack_inbound(self, through, now):
        """Everything up to `through` reached our VPS. Acked rows are swept, unacked rows are not."""
        with self._lock:
            cur = self._db.execute(
                "update inbound set acked_at=? where id <= ? and acked_at is null",
                (utc(now), int(through)))
            self._db.commit()
            return cur.rowcount

    def inbound_backlog(self):
        row = self._db.execute(
            "select count(*) c, min(received_at) oldest from inbound where acked_at is null").fetchone()
        return {"unacked": row["c"], "oldest_unacked_at": row["oldest"]}

    # --- broadcast runs (TASK-147) -----------------------------------------------------------------
    def create_run(self, run_id, *, note, pacing, items, now):
        """Write the whole run before a single item is attempted, for the same reason ``begin``
        writes before a keystroke: a run that exists only in a thread's memory is a run that a
        restart turns into a half-sent campaign nobody can enumerate.

        Raises on a run_id that already exists -- resuming an existing run is ``open_runs``, not a
        second create with the same name.
        """
        stamp = utc(now)
        with self._lock:
            if self._db.execute("select 1 from broadcast_run where run_id=?", (run_id,)).fetchone():
                raise ValueError(f"broadcast run {run_id} already exists")
            self._db.execute(
                """insert into broadcast_run(run_id, created_at, note, state, stop_requested,
                                             pacing) values(?,?,?,?,0,?)""",
                (run_id, stamp, note, RUN_OPEN, json.dumps(pacing or {}, sort_keys=True)))
            for position, item in enumerate(items):
                self._db.execute(
                    """insert into broadcast_item(run_id, client_msg_id, position, to_phone,
                           thread_tag, action, body, body_sha256, status, updated_at)
                       values(?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, item["client_msg_id"], position, item["to_phone"],
                     thread_tag(item["to_phone"]), item["action"], item["body"],
                     item["body_sha256"], ITEM_QUEUED, stamp))
            self._db.commit()
        self.note(now, "broadcast_created", None, run_id=run_id, items=len(items))
        return self.get_run(run_id)

    def get_run(self, run_id):
        row = self._db.execute("select * from broadcast_run where run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self):
        return [dict(r) for r in self._db.execute(
            "select * from broadcast_run order by created_at desc").fetchall()]

    def run_items(self, run_id):
        """Every item of a run, in the order the caller gave them. Bodies stay here: the caller
        gets status, not text."""
        return [dict(r) for r in self._db.execute(
            "select * from broadcast_item where run_id=? order by position", (run_id,)).fetchall()]

    def run_counts(self, run_id):
        rows = self._db.execute(
            "select status, count(*) c from broadcast_item where run_id=? group by status",
            (run_id,)).fetchall()
        return {r["status"]: r["c"] for r in rows}

    def open_runs(self):
        return [dict(r) for r in self._db.execute(
            "select * from broadcast_run where state=? order by created_at", (RUN_OPEN,)).fetchall()]

    def next_due_item(self, now):
        """-> (run, item) the runner should attempt next, or (None, None).

        Oldest run first, then the caller's own order inside it. A deferred item (the governor said
        'not yet') carries ``next_attempt_at`` and is invisible until that moment, so one parked
        recipient does not block the ones behind it forever -- and does not spin either.
        """
        row = self._db.execute(
            """select i.* from broadcast_item i join broadcast_run r using(run_id)
               where r.state=? and r.stop_requested=0 and i.status=?
                 and (i.next_attempt_at is null or i.next_attempt_at <= ?)
               order by r.created_at, i.position limit 1""",
            (RUN_OPEN, ITEM_QUEUED, utc(now))).fetchone()
        if row is None:
            return None, None
        return self.get_run(row["run_id"]), dict(row)

    def mark_item(self, run_id, client_msg_id, status, now, *, code=None, detail=None,
                  next_attempt_at=None, attempted=False):
        with self._lock:
            self._db.execute(
                """update broadcast_item set status=?, code=?, detail=?, next_attempt_at=?,
                          attempts=attempts+?, updated_at=?
                   where run_id=? and client_msg_id=?""",
                (status, code, detail, next_attempt_at, 1 if attempted else 0, utc(now),
                 run_id, client_msg_id))
            self._db.commit()
        self.note(now, f"broadcast_{status}", client_msg_id, run_id=run_id, code=code)

    def set_run_state(self, run_id, state, now):
        with self._lock:
            self._db.execute(
                "update broadcast_run set state=?, finished_at=? where run_id=?",
                (state, utc(now) if state in (RUN_DONE, RUN_STOPPED) else None, run_id))
            self._db.commit()
        self.note(now, "broadcast_state", None, run_id=run_id, state=state)
        return self.get_run(run_id)

    def request_stop(self, run_id, now):
        """The hard stop. A flag in the ledger and not in a thread's memory, so it survives the
        restart that a caller in a hurry is quite likely to try next."""
        with self._lock:
            changed = self._db.execute(
                "update broadcast_run set stop_requested=1 where run_id=?", (run_id,)).rowcount
            self._db.commit()
        if not changed:
            return None
        self.note(now, "broadcast_stop_requested", None, run_id=run_id)
        return self.get_run(run_id)

    def queued_count(self, run_id):
        return self._db.execute(
            "select count(*) c from broadcast_item where run_id=? and status=?",
            (run_id, ITEM_QUEUED)).fetchone()["c"]

    # --- the destruction audit (TASK-147) ----------------------------------------------------------
    def append_audit(self, now, *, operation, chat_title, phone, verified, detail):
        """A row per clear/delete, written BEFORE the destructive verb and finished afterwards.

        NOT SWEPT by ``sweep`` below, and that is the point: the record of a destruction has to
        outlive the thing it destroyed. It is also the only table here that stores a display name,
        because "which chat did we delete" is unanswerable without one.

        Write-ahead, like ``begin`` on the send path: operations.py appends this row with
        ``verified=False`` and a detail saying ``attempted`` before it taps anything, then calls
        ``finish_audit``. A row still reading ``attempted`` is a destruction whose verification
        never ran -- which is a state to look at, not one to lose.
        """
        with self._lock:
            cur = self._db.execute(
                """insert into audit(at, operation, chat_title, chat_tag, to_phone, verified, detail)
                   values(?,?,?,?,?,?,?)""",
                (utc(now), operation, chat_title, thread_tag(chat_title), phone,
                 1 if verified else 0, json.dumps(detail, sort_keys=True)))
            self._db.commit()
        self.note(now, f"audit_{operation}", None, chat=thread_tag(chat_title), verified=verified)
        return cur.lastrowid

    def finish_audit(self, audit_id, now, *, verified, detail):
        """Write the verification's outcome onto the row appended before the taps. -> the row id.

        ``at`` is left as the moment the destruction was attempted: that is the time the record is
        about the operation, not the time we finished looking at it.
        """
        with self._lock:
            changed = self._db.execute(
                "update audit set verified=?, detail=? where id=?",
                (1 if verified else 0, json.dumps(detail, sort_keys=True), audit_id)).rowcount
            self._db.commit()
        if not changed:
            raise ValueError(f"no audit row {audit_id!r} to finish")
        self.note(now, "audit_verified", None, audit_id=audit_id, verified=verified)
        return audit_id

    def audit_count(self):
        return self._db.execute("select count(*) c from audit").fetchone()["c"]

    def audit_rows(self, limit=None):
        sql = "select * from audit order by id desc"
        args = []
        if limit is not None:
            sql += " limit ?"
            args.append(int(limit))
        return [_audit_row(r) for r in self._db.execute(sql, args).fetchall()]

    def audit_for_chat(self, chat_title, *, operation=None):
        """-> the audit rows about ONE conversation, newest first.

        What this answers is "did we destroy this chat ourselves", which is the difference between
        a title that was never on the handset and one that is not on it any more BECAUSE OF US.
        Matched on the title as the handset drew it, which is the identity a caller names a chat by
        and the one ``append_audit`` stores.
        """
        sql = "select * from audit where chat_title=?"
        args = [chat_title]
        if operation is not None:
            sql += " and operation=?"
            args.append(operation)
        return [_audit_row(r) for r in self._db.execute(sql + " order by id desc", args).fetchall()]

    # --- retention (TASK-130 AC#9) -----------------------------------------------------------------
    def sweep(self, now, *, ledger_days=LEDGER_RETENTION_DAYS, inbound_days=INBOUND_RETENTION_DAYS):
        ledger_cut = utc(now - timedelta(days=ledger_days))
        inbound_cut = utc(now - timedelta(days=inbound_days))
        with self._lock:
            done = self._db.execute(
                "delete from outbound where resolved_at is not null and resolved_at < ?",
                (ledger_cut,)).rowcount
            lines = self._db.execute("delete from journal where at < ?", (ledger_cut,)).rowcount
            mism = self._db.execute(
                "delete from body_mismatch where seen_at < ?", (ledger_cut,)).rowcount
            acked = self._db.execute(
                "delete from inbound where acked_at is not null and acked_at < ?",
                (inbound_cut,)).rowcount
            # Finished runs take their bodies with them. An unfinished run is never swept: it is
            # still owed to somebody. The audit table is not here on purpose -- see append_audit.
            items = self._db.execute(
                """delete from broadcast_item where run_id in
                   (select run_id from broadcast_run where finished_at is not null
                                                       and finished_at < ?)""",
                (ledger_cut,)).rowcount
            runs = self._db.execute(
                "delete from broadcast_run where finished_at is not null and finished_at < ?",
                (ledger_cut,)).rowcount
            self._db.commit()
        return {"outbound": done, "journal": lines, "body_mismatch": mism, "inbound": acked,
                "broadcast_runs": runs, "broadcast_items": items}
