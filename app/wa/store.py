"""SQLite for the harness: one thread per phone number, one row per message.

Two invariants carry the whole design. ``wa_messages.wamid`` is UNIQUE, because Meta redelivers a
webhook for minutes after a non-2xx and the same message must not be answered twice. ``wa_threads.stopped``
is checked before every send, because an opt-out that can be overtaken by a queued reply is not an opt-out.
Slots are a JSON blob: they are the conversation's memory, and their vocabulary lives in app/wa/slots.py.
"""
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from . import config as C

_lock = threading.RLock()

STOPPED = "opt-out"      # wa_threads.stopped_reason for a lead who asked us to stop
# wa_reply_turn_claims.state when the brain chose silence for that inbound message. Final (TASK-101): the
# message counts as answered, catch-up never re-runs the model on it.
NO_SEND_STATE = "skipped_no_send"

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
create table if not exists wa_reply_turn_claims (
  phone text not null,
  turn_key text not null,
  state text not null default 'in_progress',
  claimed_at text not null,
  updated_at text not null,
  primary key (phone, turn_key)
);
create table if not exists wa_luna_calls (
  id integer primary key,
  phone text not null,
  at text not null
);
create index if not exists idx_wa_luna_calls_phone_at on wa_luna_calls(phone, at);
create table if not exists wa_send_failures (
  id integer primary key,
  phone text not null,
  error text not null,
  at text not null
);
create table if not exists wa_followups_sent (
  id integer primary key,
  phone text not null,
  tier integer not null,
  sent_at text not null
);
create index if not exists idx_wa_followups_sent_phone_at on wa_followups_sent(phone, sent_at);
create table if not exists wa_nudge_claims (
  phone text not null,
  fingerprint text not null,
  claimed_at text not null,
  primary key (phone, fingerprint)
);
create table if not exists wa_documents (
  id integer primary key,
  phone text not null,
  wamid text unique,
  media_id text,
  kind text not null,
  mime_type text,
  original_filename text,
  path text not null,
  sha256 text not null,
  size_bytes integer not null,
  received_at text not null,
  text text,
  text_key text,
  document_type text,
  certificate_level text,
  import_source text,
  import_ref text,
  import_meta text,
  reuse_state text,
  reuse_decided_at text
);
create index if not exists idx_wa_documents_phone on wa_documents(phone);
create table if not exists wa_imported_messages (
  id integer primary key,
  phone text not null,
  import_source text not null,
  import_ref text not null,
  direction text not null,
  kind text,
  body text,
  at text not null,
  imported_at text not null,
  unique (import_source, import_ref)
);
create index if not exists idx_wa_imported_messages_phone on wa_imported_messages(phone);
create table if not exists wa_message_statuses (
  id integer primary key,
  wamid text not null,
  phone text not null,
  status text not null,
  timestamp text not null,
  errors text,
  conversation text,
  pricing text,
  raw text not null,
  received_at text not null,
  unique (wamid, status, timestamp)
);
create index if not exists idx_wa_message_statuses_phone on wa_message_statuses(phone);
create table if not exists wa_webhook_events (
  id integer primary key,
  phone text,
  field text,
  kind text not null,
  raw text not null,
  fingerprint text not null unique,
  received_at text not null
);
create index if not exists idx_wa_webhook_events_phone on wa_webhook_events(phone);
create table if not exists wa_inbound_pending (
  wamid text primary key,
  phone text not null,
  recorded_at text not null,
  attempts integer not null default 0,
  last_error text,
  last_attempt_at text
);
create index if not exists idx_wa_inbound_pending_phone on wa_inbound_pending(phone);
"""

# A prior claim attempt that crashed mid-flight (process killed, box rebooted) must not block an
# owed reply forever -- generous vs. LUNA_TIMEOUT_SEC (120s) plus the time a real Meta send takes.
STALE_CLAIM_SECONDS = 300


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def db():
    C.SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(C.SQLITE_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("pragma journal_mode=wal")
    c.executescript(SCHEMA)
    _migrate(c)
    return c


# (table, column, type) added after the table first shipped (TASK-102): SCHEMA's create table only runs on a new
# file, so an existing wa.sqlite gets them by 'alter table add column' (same as app/runs.py).
MIGRATIONS = (("wa_documents", "import_source", "text"), ("wa_documents", "import_ref", "text"),
              ("wa_documents", "import_meta", "text"), ("wa_documents", "reuse_state", "text"),
              ("wa_documents", "reuse_decided_at", "text"))


def _migrate(c):
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in c.execute(f"pragma table_info({table})").fetchall()}
        if col in cols:
            continue
        try:
            c.execute(f"alter table {table} add column {col} {typ}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc):   # another process added it between the check and here
                raise
    # After the columns exist: one wa_documents row per imported source document (import_history.py idempotency).
    c.execute("create unique index if not exists idx_wa_documents_import on wa_documents(import_source, import_ref) "
              "where import_ref is not null")


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
    _update_thread(c, t)
    c.commit()


def _update_thread(c, t):
    c.execute("""update wa_threads set slots=?, asked=?, matches_sent_at=?, stopped=?, stopped_reason=?,
                 turns=?, last_inbound_at=?, last_outbound_at=? where phone=?""",
              (json.dumps(t["slots"], ensure_ascii=False), json.dumps(t["asked"]), t.get("matches_sent_at"),
               int(bool(t.get("stopped"))), t.get("stopped_reason"), int(t.get("turns") or 0),
               t.get("last_inbound_at"), t.get("last_outbound_at"), t["phone"]))


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


# --- reply-turn claims (TASK-77): durable, cross-process dedup beyond wamid uniqueness ----------
# The wamid UNIQUE constraint on wa_messages stops a Meta redelivery from being answered twice, but
# it says nothing about two DIFFERENT entrypoints (the webhook, and the catch-up driver, TASK-78)
# both deciding -- at the same moment, in separate processes -- to generate and send a reply for
# the SAME already-recorded inbound message. turn_key is that message's own wamid; only one caller
# may hold an active claim on a given (phone, turn_key) at a time.

def claim_reply_turn(c, phone, turn_key):
    """True if the caller may proceed to generate and send a reply for this exact inbound message;
    False if another caller already holds an active claim, or already finished one with state
    'sent' (a reply for this exact message genuinely went out already -- never reclaimable) or
    NO_SEND_STATE (the brain decided to stay silent on it, TASK-101: a retry would re-run the model on
    the same message). Any other terminal state (skipped_rate_cap / skipped_stopped / skipped_error),
    or a stale in_progress claim from a crashed prior attempt, is reclaimable: those all mean no reply
    decision was made yet, so a later retry (catch-up) must still be allowed to try."""
    now = now_iso()
    try:
        c.execute("insert into wa_reply_turn_claims (phone, turn_key, state, claimed_at, updated_at) "
                  "values (?,?,?,?,?)", (phone, turn_key, "in_progress", now, now))
        c.commit()
        return True
    except sqlite3.IntegrityError:
        pass
    row = c.execute("select state, claimed_at from wa_reply_turn_claims where phone=? and turn_key=?",
                    (phone, turn_key)).fetchone()
    if row is None:
        return True  # vanished between the failed insert and this select -- fail open rather than
                     # silently block an owed reply forever over something that should not happen
    if row["state"] in ("sent", NO_SEND_STATE):
        return False
    if row["state"] == "in_progress":
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["claimed_at"])).total_seconds()
        if age < STALE_CLAIM_SECONDS:
            return False
    c.execute("update wa_reply_turn_claims set state=?, claimed_at=?, updated_at=? where phone=? and turn_key=?",
              ("in_progress", now, now, phone, turn_key))
    c.commit()
    return True


def finish_reply_turn_claim(c, phone, turn_key, state):
    c.execute("update wa_reply_turn_claims set state=?, updated_at=? where phone=? and turn_key=?",
              (state, now_iso(), phone, turn_key))
    c.commit()


# --- per-candidate LLM call rate limit (TASK-76) ------------------------------------------------

def record_luna_call(c, phone):
    c.execute("insert into wa_luna_calls (phone, at) values (?,?)", (phone, now_iso()))
    c.commit()


def count_recent_luna_calls(c, phone, within_hours=1.0):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=within_hours)).isoformat()
    row = c.execute("select count(*) as n from wa_luna_calls where phone=? and at>=?",
                    (phone, cutoff)).fetchone()
    return row["n"]


# --- send-failure visibility (TASK-79) -----------------------------------------------------------

def record_send_failure(c, phone, error):
    c.execute("insert into wa_send_failures (phone, error, at) values (?,?,?)", (phone, error, now_iso()))
    c.commit()


def recent_send_failure(c, phone):
    row = c.execute("select error, at from wa_send_failures where phone=? order by id desc limit 1",
                    (phone,)).fetchone()
    return dict(row) if row else None


# --- proactive follow-up nudges (TASK-85) --------------------------------------------------------

def record_followup_sent(c, phone, tier):
    c.execute("insert into wa_followups_sent (phone, tier, sent_at) values (?,?,?)", (phone, tier, now_iso()))
    c.commit()


def followup_tiers_sent_since(c, phone, since_iso):
    """Tiers already nudged in the current streak -- since_iso is the candidate's own last
    message (or an epoch sentinel if they have never written), so a reply naturally resets what
    this returns without a separate counter column that could drift out of sync."""
    rows = c.execute("select tier from wa_followups_sent where phone=? and sent_at>=? order by tier",
                     (phone, since_iso)).fetchall()
    return [r["tier"] for r in rows]


def claim_nudge(c, phone, fingerprint):
    """True if this exact (phone, fingerprint) has not been claimed before; False if another
    caller already claimed it (TASK-93). A durable, cross-process dedup primitive for any
    unprompted, system-initiated send -- today's tiered follow-up nudge (app/wa/luna/followups.py)
    and any future template-driven campaign alike -- so two independent trigger paths (a second
    overlapping run of the same job, or two different campaign types) deciding to message the
    same candidate at nearly the same moment cannot both go through. ST._lock only serializes
    within one process; this table is what makes the guarantee hold across separate processes too.

    Deliberately simpler than claim_reply_turn (TASK-77): nothing here is ever reclaimable. A
    reply-turn claim protects an inbound message that is owed a reply and must eventually get one
    (so a crashed attempt has to be retryable); a nudge is never owed the way a reply is -- a claim
    that never results in an actual send is simply a nudge that did not go out this round, not a
    lost message anything needs to recover. The caller picks the fingerprint (e.g. a tier index
    plus the current streak's anchor timestamp, so a later legitimate streak is not falsely
    blocked by an earlier one that reused the same tier number)."""
    try:
        c.execute("insert into wa_nudge_claims (phone, fingerprint, claimed_at) values (?,?,?)",
                  (phone, fingerprint, now_iso()))
        c.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def candidate_phones(c):
    """Every phone with a thread, not stopped -- the pool app.wa.luna.followups/catchup-style
    drivers scan."""
    rows = c.execute("select phone from wa_threads where stopped=0").fetchall()
    return [r["phone"] for r in rows]


# --- inbound media originals (TASK-95) -----------------------------------------------------------
# One row per stored original file (app/wa/api.py:_store_original writes the file first, then this
# row). wamid is the inbound message the file came with: record_inbound's UNIQUE wa_messages.wamid
# already drops a redelivery before ingest, so a first delivery never finds its wamid taken here.

def record_document(c, phone, wamid, media_id, kind, mime_type, original_filename, path, sha256,
                    size_bytes):
    """-> the new row id. Committed at once, so a later extraction failure in the same turn cannot
    roll back the link to a file that is already on disk."""
    cur = c.execute("""insert into wa_documents (phone, wamid, media_id, kind, mime_type, original_filename,
                       path, sha256, size_bytes, received_at) values (?,?,?,?,?,?,?,?,?,?)""",
                    (phone, wamid, media_id, kind, mime_type, original_filename, path, sha256, size_bytes,
                     now_iso()))
    c.commit()
    return cur.lastrowid


def set_document_text(c, doc_id, text):
    """Extracted text, stored before classification so a failed classification still keeps it."""
    c.execute("update wa_documents set text=? where id=?", (text, doc_id))
    c.commit()


def set_document_classification(c, doc_id, document_type, certificate_level, text_key):
    """app/cv.py:classify_document() result (TASK-81) for this one file, and the card key its text
    went to -- chosen by document_type (cv_text/urkunde_text, None for any other type, TASK-96)."""
    c.execute("update wa_documents set document_type=?, certificate_level=?, text_key=? where id=?",
              (document_type, certificate_level, text_key, doc_id))
    c.commit()


def documents_for(c, phone):
    """Every stored original for this phone, oldest first, all columns (text included)."""
    rows = c.execute("select * from wa_documents where phone=? order by id", (phone,)).fetchall()
    return [dict(r) for r in rows]


def document_for_wamid(c, wamid):
    """The stored original that came with this inbound message, all columns, or None."""
    row = c.execute("select * from wa_documents where wamid=?", (wamid,)).fetchone()
    return dict(row) if row else None


# --- imported history (TASK-102, app/wa/luna/import_history.py) -----------------------------------
# An imported document is a wa_documents row with import_source/import_ref (the source system's own id for it),
# import_meta (JSON) and no wamid; reuse_state starts 'pending' and only a candidate's answer moves it to
# 'confirmed' or 'declined' (luna_brain.apply_document_reuse). Prior messages go to wa_imported_messages, never
# wa_messages: those rows would count as turns, inbound, ball and outbound_since_last_turn.

REUSE_PENDING, REUSE_CONFIRMED, REUSE_DECLINED = "pending", "confirmed", "declined"


def record_imported_document(c, phone, media_id, kind, mime_type, original_filename, path, sha256, size_bytes,
                             import_source, import_ref, import_meta):
    """-> the new wa_documents row id, reuse_state pending. Committed at once, like record_document."""
    cur = c.execute("""insert into wa_documents (phone, wamid, media_id, kind, mime_type, original_filename, path,
                       sha256, size_bytes, received_at, import_source, import_ref, import_meta, reuse_state)
                       values (?,null,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (phone, media_id, kind, mime_type, original_filename, path, sha256, size_bytes, now_iso(),
                     import_source, import_ref, json.dumps(import_meta, ensure_ascii=False), REUSE_PENDING))
    c.commit()
    return cur.lastrowid


def imported_document(c, import_source, import_ref):
    """The wa_documents row imported from this source document, all columns, or None."""
    row = c.execute("select * from wa_documents where import_source=? and import_ref=?",
                    (import_source, import_ref)).fetchone()
    return dict(row) if row else None


def document_with_sha256(c, phone, sha256):
    """The oldest stored original of this phone with these bytes, all columns, or None."""
    row = c.execute("select * from wa_documents where phone=? and sha256=? order by id limit 1",
                    (phone, sha256)).fetchone()
    return dict(row) if row else None


def document_by_id(c, doc_id):
    row = c.execute("select * from wa_documents where id=?", (doc_id,)).fetchone()
    return dict(row) if row else None


def set_document_reuse(c, doc_id, state, at):
    """The candidate's reuse answer for one imported document. Raises for a row that is not imported."""
    cur = c.execute("update wa_documents set reuse_state=?, reuse_decided_at=? where id=? and import_ref is not null",
                    (state, at, doc_id))
    if cur.rowcount != 1:
        c.rollback()
        raise RuntimeError(f"wa_documents {doc_id} is not an imported document")
    c.commit()


def record_imported_message(c, phone, import_source, import_ref, direction, kind, body, at):
    """-> True when stored, False when this source message was imported before."""
    cur = c.execute("""insert or ignore into wa_imported_messages (phone, import_source, import_ref, direction, kind,
                       body, at, imported_at) values (?,?,?,?,?,?,?,?)""",
                    (phone, import_source, import_ref, direction, kind, body, at, now_iso()))
    return cur.rowcount == 1


def imported_messages_for(c, phone):
    """This phone's imported prior messages, oldest first."""
    rows = c.execute("select * from wa_imported_messages where phone=? order by at, id", (phone,)).fetchall()
    return [dict(r) for r in rows]


# --- webhook statuses, raw events, pending inbound work (TASK-99) ---------------------------------
# A status webhook (sent/delivered/read/failed) is one row per (wamid, status, Meta timestamp): a Meta
# redelivery inserts nothing and records no second failure. Everything else a webhook carries for one of
# our phones that nothing here acts on (calls, contacts, reactions, unknown fields) is kept raw in
# wa_webhook_events; an identical raw object for the same phone/field/kind is stored once, so a
# redelivery adds nothing. wa_inbound_pending holds one row per accepted inbound message until its
# processing (arrival bookkeeping, media store/ingest, reply turn) finished -- the webhook's background
# worker and catch-up both work from it, so a crash or restart mid-turn leaves the row for catch-up.

def _json_or_none(value):
    return None if value is None else json.dumps(value, ensure_ascii=False)


def delivery_failure_text(wamid, errors):
    """wa_send_failures.error for a ``failed`` status: every Meta error with code, title and details."""
    parts = [f"code {e.get('code')} {e.get('title') or e.get('message') or ''}: "
             f"{(e.get('error_data') or {}).get('details') or e.get('message') or ''}".strip()
             for e in errors or []]
    return f"delivery failed for {wamid}: " + ("; ".join(parts) if parts else "Meta sent no error details")


def record_message_status(c, phone, status):
    """-> True for a first sighting of this (wamid, status, timestamp). A first ``failed`` status also
    writes wa_send_failures in the same commit (GET /wa/threads last_send_error)."""
    wamid, state = str(status.get("id") or ""), str(status.get("status") or "")
    if not wamid or not state or not phone:
        raise ValueError(f"status without id/status/recipient_id: {status!r}")
    now = now_iso()
    cur = c.execute("""insert or ignore into wa_message_statuses (wamid, phone, status, timestamp, errors,
                       conversation, pricing, raw, received_at) values (?,?,?,?,?,?,?,?,?)""",
                    (wamid, phone, state, str(status.get("timestamp") or ""), _json_or_none(status.get("errors")),
                     _json_or_none(status.get("conversation")), _json_or_none(status.get("pricing")),
                     json.dumps(status, ensure_ascii=False), now))
    fresh = cur.rowcount == 1
    if fresh and state == "failed":
        c.execute("insert into wa_send_failures (phone, error, at) values (?,?,?)",
                  (phone, delivery_failure_text(wamid, status.get("errors")), now))
    c.commit()
    return fresh


def _status_row(row):
    d = dict(row)
    for key in ("errors", "conversation", "pricing", "raw"):
        d[key] = json.loads(d[key]) if d[key] is not None else None
    return d


def latest_message_status(c, wamid):
    """The latest status of one message, or None: highest Meta timestamp, then the last one received."""
    row = c.execute("select * from wa_message_statuses where wamid=? "
                    "order by cast(timestamp as integer) desc, id desc limit 1", (wamid,)).fetchone()
    return _status_row(row) if row else None


def latest_message_statuses_for(c, phone):
    """latest_message_status for every message of this phone that has a status, oldest message first."""
    rows = c.execute("""select * from (select *, row_number() over (partition by wamid
                          order by cast(timestamp as integer) desc, id desc) as rn
                        from wa_message_statuses where phone=?) where rn=1 order by id""", (phone,)).fetchall()
    return [{k: v for k, v in _status_row(r).items() if k != "rn"} for r in rows]


def record_webhook_event(c, phone, field, kind, raw):
    """Keep one webhook object nothing here handles. -> True when stored, False for an identical one."""
    raw_json = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(json.dumps([phone, field, kind, raw_json]).encode("utf-8")).hexdigest()
    cur = c.execute("insert or ignore into wa_webhook_events (phone, field, kind, raw, fingerprint, received_at) "
                    "values (?,?,?,?,?,?)", (phone, field, kind, raw_json, fingerprint, now_iso()))
    c.commit()
    return cur.rowcount == 1


def webhook_events_for(c, phone):
    rows = c.execute("select id, phone, field, kind, raw, received_at from wa_webhook_events where phone=? "
                     "order by id", (phone,)).fetchall()
    return [{**dict(r), "raw": json.loads(r["raw"])} for r in rows]


def record_inbound_pending(c, phone, wamid, body, kind="text", meta=None):
    """record_inbound plus its wa_inbound_pending row in one commit. -> False for a Meta redelivery."""
    now = now_iso()
    try:
        c.execute("insert into wa_messages (phone, direction, wamid, body, kind, meta, at) values (?,?,?,?,?,?,?)",
                  (phone, "in", wamid, body, kind, json.dumps(meta or {}, ensure_ascii=False), now))
    except sqlite3.IntegrityError:
        c.rollback()
        return False
    c.execute("insert into wa_inbound_pending (wamid, phone, recorded_at) values (?,?,?)", (wamid, phone, now))
    c.commit()
    return True


def pending_inbound(c, phone):
    """This phone's unfinished inbound messages, oldest first: the wa_messages row plus attempts/last_error.
    Raises on a pending row whose message row is gone -- that message cannot be finished."""
    rows = c.execute("""select p.wamid as pending_wamid, p.attempts, p.last_error, p.last_attempt_at, m.*
                        from wa_inbound_pending p left join wa_messages m on m.wamid = p.wamid
                        where p.phone=? order by m.id, p.recorded_at""", (phone,)).fetchall()
    orphans = [r["pending_wamid"] for r in rows if r["id"] is None]
    if orphans:
        raise RuntimeError(f"wa_inbound_pending rows without a wa_messages row for {phone}: {orphans}")
    return [dict(r) for r in rows]


def phones_with_pending_inbound(c):
    """Every phone with an unfinished inbound message, the one waiting longest first."""
    rows = c.execute("select phone, min(recorded_at) as oldest from wa_inbound_pending group by phone "
                     "order by oldest, phone").fetchall()
    return [r["phone"] for r in rows]


def pending_inbound_summary(c, phone):
    """{count, oldest_recorded_at, last_error, last_attempt_at} or None -- GET /wa/threads."""
    row = c.execute("""select count(*) as count, min(recorded_at) as oldest_recorded_at from wa_inbound_pending
                       where phone=?""", (phone,)).fetchone()
    if not row["count"]:
        return None
    last = c.execute("select last_error, last_attempt_at from wa_inbound_pending where phone=? and last_error "
                     "is not null order by last_attempt_at desc limit 1", (phone,)).fetchone()
    return {"count": row["count"], "oldest_recorded_at": row["oldest_recorded_at"],
            "last_error": last["last_error"] if last else None,
            "last_attempt_at": last["last_attempt_at"] if last else None}


def finish_pending_inbound(c, wamid):
    c.execute("delete from wa_inbound_pending where wamid=?", (wamid,))
    c.commit()


def record_pending_attempt(c, wamid, error):
    """A failed processing attempt. -> the previous last_error, so a caller can record a repeat once."""
    row = c.execute("select last_error from wa_inbound_pending where wamid=?", (wamid,)).fetchone()
    c.execute("update wa_inbound_pending set attempts=attempts+1, last_error=?, last_attempt_at=? where wamid=?",
              (error, now_iso(), wamid))
    c.commit()
    return row["last_error"] if row else None


def reply_turn_claim_state(c, phone, turn_key):
    row = c.execute("select state from wa_reply_turn_claims where phone=? and turn_key=?",
                    (phone, turn_key)).fetchone()
    return row["state"] if row else None


def claim_in_flight(c, phone):
    """True while any claim of this phone is in_progress and younger than STALE_CLAIM_SECONDS -- some
    process is storing, reading or answering one of its messages right now."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STALE_CLAIM_SECONDS)).replace(microsecond=0).isoformat()
    row = c.execute("select 1 from wa_reply_turn_claims where phone=? and state='in_progress' and claimed_at>? "
                    "limit 1", (phone, cutoff)).fetchone()
    return row is not None


def inbound_position(c, wamid):
    """-> (recorded at, number of inbound messages of its phone up to and including it) for one inbound row."""
    row = c.execute("select id, phone, at from wa_messages where wamid=? and direction='in'", (wamid,)).fetchone()
    if row is None:
        raise RuntimeError(f"no inbound wa_messages row for {wamid}")
    n = c.execute("select count(*) as n from wa_messages where phone=? and direction='in' and id<=?",
                  (row["phone"], row["id"])).fetchone()["n"]
    return row["at"], n


def inbound_is_pending(c, wamid):
    return c.execute("select 1 from wa_inbound_pending where wamid=?", (wamid,)).fetchone() is not None


# --- campaign seed, message lookups for the Luna turn context (TASK-100/101) -----------------------

def _message_row(row):
    return {**dict(row), "meta": json.loads(row["meta"] or "{}")}


def message_by_wamid(c, wamid):
    """One wa_messages row (either direction) with meta parsed, or None."""
    row = c.execute("select * from wa_messages where wamid=?", (wamid,)).fetchone()
    return _message_row(row) if row else None


def messages_for(c, phone, after_id=0, direction=None):
    """This phone's wa_messages rows with id > after_id, oldest first, meta parsed; one direction or both."""
    sql, args = "select * from wa_messages where phone=? and id>?", [phone, after_id]
    if direction:
        sql, args = sql + " and direction=?", args + [direction]
    return [_message_row(r) for r in c.execute(sql + " order by id", args).fetchall()]


def last_message_id(c, phone):
    """Highest wa_messages id of this phone, 0 without messages."""
    return c.execute("select coalesce(max(id), 0) as n from wa_messages where phone=?", (phone,)).fetchone()["n"]


def has_inbound(c, phone):
    """True once the candidate ever wrote (any inbound row)."""
    return c.execute("select 1 from wa_messages where phone=? and direction='in' limit 1",
                     (phone,)).fetchone() is not None


def last_inbound(c, phone):
    """This phone's latest inbound wa_messages row (meta parsed), or None."""
    row = c.execute("select * from wa_messages where phone=? and direction='in' order by id desc limit 1",
                    (phone,)).fetchone()
    return _message_row(row) if row else None


def record_campaign_send(c, phone, wamid, rendered, campaign_id, sent_at=None, kind="template", template_id=None,
                         variables=None, commit=True):
    """The one place a campaign template send lands (TASK-100; the sender, TASK-103, calls it after Meta
    returned ``wamid``). ``rendered`` is app/wa/meta.py:render_template's result for the exact definition
    and parameters sent. In one commit: the thread (created when missing), the outbound row (body = rendered
    text, meta = {action: 'campaign', campaign_id, template_id, template, language, variables, buttons}),
    ``last_outbound_at`` (never last_inbound_at), and the card contract Luna reads, replacing an earlier
    campaign on the card (every send stays a row):

        card.campaign = {campaign_id, template_name, language, rendered_text, buttons, sent_at, wamid}

    ``buttons`` are render_template's buttons ({type, text, payload, ...}); a quick-reply tap arrives as
    button_id 'tpl:<payload>' (api.parse_message). ``variables`` = the params sent. ``commit=False`` leaves the
    commit to the caller (the sender commits it with its claim). -> the campaign dict."""
    at = sent_at or now_iso()
    campaign = {"campaign_id": campaign_id, "template_name": rendered["name"], "language": rendered["language"],
                "rendered_text": rendered["text"], "buttons": rendered["buttons"], "sent_at": at, "wamid": wamid}
    c.execute("insert or ignore into wa_threads (phone, opened_at) values (?,?)", (phone, at))
    t = thread(c, phone)
    c.execute("insert into wa_messages (phone, direction, wamid, body, kind, meta, at) values (?,?,?,?,?,?,?)",
              (phone, "out", wamid, rendered["text"], kind,
               json.dumps({"action": "campaign", "campaign_id": campaign_id, "template_id": template_id,
                           "template": rendered["name"], "language": rendered["language"], "variables": variables,
                           "buttons": rendered["buttons"]}, ensure_ascii=False),
               at))
    t["slots"]["campaign"] = campaign
    t["last_outbound_at"] = at
    _update_thread(c, t)
    if commit:
        c.commit()
    return campaign


def message_statuses_for(c, phone, status=None):
    """Every stored status row of this phone (one status value or all), oldest received first, JSON parsed."""
    sql, args = "select * from wa_message_statuses where phone=?", [phone]
    if status:
        sql, args = sql + " and status=?", args + [status]
    return [_status_row(r) for r in c.execute(sql + " order by id", args).fetchall()]


# --- campaign sends (TASK-103, app/wa/luna/campaign.py) -------------------------------------------------------
# One row per (campaign, phone): the durable send claim. state: in_progress = claimed, the POST result not recorded
# (after a crash: uncertain); sent = Meta returned the wamid, recorded in the same commit as the outbound row and
# card.campaign; failed = Meta answered with an HTTP 4xx error, nothing went out, ownership restored; uncertain =
# network error, timeout, HTTP 5xx or a 2xx without wamid: it may have gone out. Delivery is not copied here:
# wa_message_statuses by wamid is the one record. Not in SCHEMA: only the sender's connection creates the table.
# While a claim is in_progress the phone also holds the reply-turn claim 'campaign:<id>', so the webhook worker and
# catch-up leave the card alone until the send is recorded (ST.claim_in_flight).

CAMPAIGN_SCHEMA = """
create table if not exists wa_campaign_sends (
  campaign_id text not null,
  phone text not null,
  state text not null check (state in ('in_progress', 'sent', 'failed', 'uncertain')),
  attempts integer not null,
  template_id text not null,
  template_name text not null,
  template_language text not null,
  variables text not null,
  rendered_text text not null,
  prior_owner text,
  prior_reason text,
  prior_since text,
  ownership_restore text,
  wamid text unique,
  error text,
  error_code text,
  error_payload text,
  claimed_at text not null,
  sent_at text,
  finished_at text,
  primary key (campaign_id, phone)
);
create index if not exists idx_wa_campaign_sends_phone on wa_campaign_sends(phone);
"""
CAMPAIGN_CLAIM_PREFIX = "campaign:"   # wa_reply_turn_claims.turn_key 'campaign:<id>', wa_nudge_claims 'campaign:<id>:<n>'


def _campaign_row(row):
    d = dict(row)
    d["variables"] = json.loads(d["variables"])
    d["error_payload"] = json.loads(d["error_payload"]) if d["error_payload"] is not None else None
    return d


def campaign_send(c, campaign_id, phone):
    row = c.execute("select * from wa_campaign_sends where campaign_id=? and phone=?", (campaign_id, phone)).fetchone()
    return _campaign_row(row) if row else None


def campaign_sends(c, campaign_id):
    """Every claim of one campaign, in claim order."""
    rows = c.execute("select * from wa_campaign_sends where campaign_id=? order by claimed_at, rowid",
                     (campaign_id,)).fetchall()
    return [_campaign_row(r) for r in rows]


def campaign_sends_for_phone(c, phone):
    rows = c.execute("select * from wa_campaign_sends where phone=? order by claimed_at, rowid", (phone,)).fetchall()
    return [_campaign_row(r) for r in rows]


def campaign_claims_after(c, campaign_id, after_iso):
    """claimed_at of this campaign's claims later than ``after_iso`` (same UTC ISO format), oldest first."""
    rows = c.execute("select claimed_at from wa_campaign_sends where campaign_id=? and claimed_at>? order by claimed_at",
                     (campaign_id, after_iso)).fetchall()
    return [r["claimed_at"] for r in rows]


def claim_campaign_send(c, campaign_id, phone, template, variables, rendered_text, prior, claimed_at,
                        retry_uncertain=False):
    """Claim one campaign send. No commit: the caller commits it with the ownership flip (routing.
    flip_to_us_for_campaign) in one transaction. A first claim inserts; a ``failed`` one is claimed again; an
    ``in_progress``/``uncertain`` one only with ``retry_uncertain``; a ``sent`` one raises. Also writes the
    nudge claim 'campaign:<id>:<attempt>' and the reply-turn claim 'campaign:<id>' (in_progress). -> attempt number."""
    existing = campaign_send(c, campaign_id, phone)
    if existing is not None and not (existing["state"] == "failed" or
                                     (retry_uncertain and existing["state"] in ("in_progress", "uncertain"))):
        raise RuntimeError(f"campaign {campaign_id} {phone} is {existing['state']}, not claimable")
    attempt = 1 if existing is None else existing["attempts"] + 1
    prior = prior or {}
    values = (attempt, str(template["id"]), template["name"], template["language"],
              json.dumps(variables, ensure_ascii=False), rendered_text, prior.get("owner"), prior.get("reason"),
              prior.get("since"), claimed_at)
    if existing is None:
        c.execute("""insert into wa_campaign_sends (state, attempts, template_id, template_name, template_language,
                     variables, rendered_text, prior_owner, prior_reason, prior_since, claimed_at, campaign_id, phone)
                     values ('in_progress',?,?,?,?,?,?,?,?,?,?,?,?)""", values + (campaign_id, phone))
    else:
        c.execute("""update wa_campaign_sends set state='in_progress', attempts=?, template_id=?, template_name=?,
                     template_language=?, variables=?, rendered_text=?, prior_owner=?, prior_reason=?, prior_since=?,
                     claimed_at=?, ownership_restore=null, wamid=null, error=null, error_code=null,
                     error_payload=null, sent_at=null, finished_at=null
                     where campaign_id=? and phone=?""", values + (campaign_id, phone))
    c.execute("insert into wa_nudge_claims (phone, fingerprint, claimed_at) values (?,?,?)",
              (phone, f"{CAMPAIGN_CLAIM_PREFIX}{campaign_id}:{attempt}", claimed_at))
    now = now_iso()
    c.execute("""insert into wa_reply_turn_claims (phone, turn_key, state, claimed_at, updated_at)
                 values (?,?,'in_progress',?,?) on conflict(phone, turn_key) do update set state='in_progress',
                 claimed_at=excluded.claimed_at, updated_at=excluded.updated_at""",
              (phone, CAMPAIGN_CLAIM_PREFIX + campaign_id, now, now))
    return attempt


def finish_campaign_send(c, campaign_id, phone, state, at, wamid=None, error=None, error_code=None,
                         error_payload=None, ownership_restore=None):
    """in_progress -> sent (with ``wamid``) / failed / uncertain, and the reply-turn claim 'campaign:<id>' ->
    'campaign_<state>'. No commit. Raises unless the claim is in_progress."""
    if state not in ("sent", "failed", "uncertain"):
        raise ValueError(f"not a final campaign send state: {state!r}")
    cur = c.execute("""update wa_campaign_sends set state=?, wamid=?, error=?, error_code=?, error_payload=?,
                       ownership_restore=?, sent_at=?, finished_at=? where campaign_id=? and phone=? and
                       state='in_progress'""",
                    (state, wamid, error, None if error_code is None else str(error_code),
                     _json_or_none(error_payload), ownership_restore, at if state == "sent" else None, at,
                     campaign_id, phone))
    if cur.rowcount != 1:
        raise RuntimeError(f"campaign {campaign_id} {phone}: no in_progress claim to finish as {state}")
    c.execute("update wa_reply_turn_claims set state=?, updated_at=? where phone=? and turn_key=?",
              ("campaign_" + state, now_iso(), phone, CAMPAIGN_CLAIM_PREFIX + campaign_id))


def reconcile_campaign_send(c, campaign_id, phone, wamid, sent_at):
    """An in_progress/uncertain claim whose template did go out (operator evidence: a status webhook for ``wamid``,
    app/wa/luna/campaign.py --mark-sent) -> sent with that wamid and sent_at; the earlier error stays as history.
    The reply-turn claim 'campaign:<id>' -> 'campaign_sent'. No commit. Raises unless the claim is in_progress or
    uncertain."""
    now = now_iso()
    cur = c.execute("""update wa_campaign_sends set state='sent', wamid=?, sent_at=?, finished_at=? where campaign_id=?
                       and phone=? and state in ('in_progress', 'uncertain')""", (wamid, sent_at, now, campaign_id, phone))
    if cur.rowcount != 1:
        raise RuntimeError(f"campaign {campaign_id} {phone}: no in_progress/uncertain claim to mark sent")
    c.execute("update wa_reply_turn_claims set state='campaign_sent', updated_at=? where phone=? and turn_key=?",
              (now, phone, CAMPAIGN_CLAIM_PREFIX + campaign_id))
