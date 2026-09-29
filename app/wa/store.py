"""SQLite for the harness: one thread per phone number, one row per message.

Two invariants carry the whole design. ``wa_messages.wamid`` is UNIQUE, because Meta redelivers a
webhook for minutes after a non-2xx and the same message must not be answered twice. ``wa_threads.stopped``
is checked before every send, because an opt-out that can be overtaken by a queued reply is not an opt-out.
``wa_suppressions`` is that second invariant's cross-thread, cross-lane twin (TASK-216): one row per human
rather than per thread, read by app/wa/suppression.py before every send on either rail.
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
# wa_reply_turn_claims.state when the brain chose silence for that inbound message. Final (TASK-204): the
# message counts as answered, catch-up never re-runs the model on it.
NO_SEND_STATE = "skipped_no_send"
# wa_reply_turn_claims.state when the message was an operator note (wa_agent_notes), not a candidate
# turn. Final for the same reason NO_SEND_STATE is: the message IS answered (the ack went out) and the
# work it asks for lives on its own row now -- a catch-up re-drive must never hand it to the brain.
AGENT_NOTE_STATE = "routed_to_agent_note"

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
  last_outbound_at text,
  is_test integer not null default 0,
  test_marked_at text
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
-- One row per suppressed phone: the cross-thread, cross-lane do-not-contact list (TASK-216/TASK-137).
-- wa_threads.stopped is per thread and every card save rewrites it; this row is per human (the key is
-- phones.canonicalize_phone) and nothing but a suppression writes it. The rules, and the choke points
-- that read it before every send, live in app/wa/suppression.py; the table is declared here because
-- db() is what creates the tables of this file.
create table if not exists wa_suppressions (
  phone text primary key,
  reason text not null,
  lane text not null,
  trigger_text text,
  at text not null
);
-- The operator inbox (Ivan, 2026-09-24). A Russian-language instruction that arrives on a TEST thread
-- is not a candidate turn: it is a note to whoever maintains this system. The gate that decides that
-- (app/wa/luna/agent_note_gate.py) only ever runs on a thread already marked is_test -- for every real
-- candidate this feature does not exist at all, which is what makes it safe: no real candidate can be
-- pulled out of the funnel by it, and no stranger can make this number emit anything.
-- Detection writes a ROW here and stops. Nothing that implements anything runs in the web process; the
-- periodic worker (app/wa/luna/agent_note_worker.py, TASK-303) reads these rows, never a live re-scan
-- of wa_messages. One row per inbound wamid -- that unique is what makes a redelivered webhook, a
-- catch-up re-drive and a crashed worker idempotent (no second row, no second ack, no second completion
-- note).
-- TASK-303's own two columns, task_id and handed_off_at, are NOT listed in this create-table block --
-- same convention as deleted_at/rail/composed and every other column this table or its neighbours
-- gained after they first shipped (see the MIGRATIONS comment below): they are added by an 'alter
-- table' that runs unconditionally on every db() call, fresh database included, so this block stays
-- exactly as first written. status gains a fifth value, handed_off: the worker found or created this
-- note's backlog card (task_id) and told the working session about it (handed_off_at stamped,
-- claimed_at cleared) -- from that moment the note belongs to that session, not to this worker, and is
-- closed only through the existing --done/--blocked CLI (agent_notes.py, finish_agent_note). A row in
-- this status must never come back from open_agent_notes(): the worker's own claim loop must not
-- re-claim a note the working session already owns.
create table if not exists wa_agent_notes (
  id integer primary key,
  wamid text not null unique,
  phone text not null,
  kind text not null,
  body text not null,
  status text not null,          -- pending | in_progress | handed_off | done | blocked
  created_at text not null,
  acked_at text,
  claimed_at text,               -- set when a worker takes it; stale after AGENT_NOTE_STALE_SEC
  attempts integer not null default 0,
  progress text,                 -- append-only '[<iso>] line' trail, so a worker that dies leaves one
  outcome_done text,
  outcome_not_done text,
  outcome_needed text,
  blocked_reason text,
  finished_at text,
  notified_at text               -- when the one completion note went out; guards a second send
);
create index if not exists idx_wa_agent_notes_status on wa_agent_notes(status, created_at);
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


# (table, column, type) added after the table first shipped (TASK-205): SCHEMA's create table only runs on a new
# file, so an existing wa.sqlite gets them by 'alter table add column' (same as app/runs.py).
MIGRATIONS = (("wa_documents", "import_source", "text"), ("wa_documents", "import_ref", "text"),
              ("wa_documents", "import_meta", "text"), ("wa_documents", "reuse_state", "text"),
              ("wa_documents", "reuse_decided_at", "text"),
              ("wa_threads", "is_test", "integer not null default 0"), ("wa_threads", "test_marked_at", "text"),
              # TASK-220: which rail this thread's messages go out on. Null until its first successful
              # outbound; see pin_rail below for why it never changes after that.
              ("wa_threads", "rail", "text"),
              # TASK-289 (Ivan, 2026-09-24): a phone/app-level chat delete let the real WhatsApp screen
              # and this DB drift apart -- twice, live, the same night. Nothing here ever hard-deletes a
              # row again: "forget" sets deleted_at instead, and every read the model reaches (messages_for,
              # message_by_wamid, documents_for, document_for_wamid) filters it out by default.
              ("wa_messages", "deleted_at", "text"), ("wa_documents", "deleted_at", "text"),
              # TASK-288: the brain's composed decision (turn()'s whole return dict, JSON), written just
              # before the risky step (send_and_record) so a pure delivery failure's retry can reuse it
              # instead of paying for a fresh claude -p call. See record_composed_reply/composed_reply.
              ("wa_reply_turn_claims", "composed", "text"),
              # TASK-303: the operator-note worker's own two columns. task_id is set once
              # (set_agent_note_task) right after this note's backlog card exists, and release_agent_note
              # deliberately never clears it -- a retry after a failed hand-off must find the card again
              # instead of creating a second one for the same note (the card's own AC#6). handed_off_at is
              # set only by mark_agent_note_handed_off, the moment the working session was actually told;
              # see the wa_agent_notes comment above for what the handed_off status itself means.
              ("wa_agent_notes", "task_id", "text"), ("wa_agent_notes", "handed_off_at", "text"))


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
    _migrate_agent_notes_autoincrement(c)


# TASK-303 (Ivan, 2026-09-25, round-1 review, finding A2 -- "correctness of the operator-note state
# machine" lens): wa_agent_notes originally shipped as `id integer primary key`, plain SQLite rowid
# aliasing, which reuses the highest-ever id the moment the table goes empty (verified empirically
# against a scratch db, 2026-09-25: delete the only row, insert a new one with no explicit id, it comes
# back as id 1 again). The nightly purge (purge_test_history.py, 03:00 Europe/Berlin,
# --older-than-hours 0 --apply) did exactly that to this table every night -- until this same review
# round's finding A1 stopped it (see purge_test_history.py's CORE_TABLES comment) -- and a reused id let
# a brand-new note's crash-recovery search adopt an OLD, already-closed card by its id-based title alone
# (agent_note_worker._ensure_card, before this fix), and would independently have let a new note's own
# ack/completion mint the SAME client_msg_id an old note's already-delivered message used, which the
# bridge's own ledger (30-day retention, bridge/ledger.py) would then have replayed instead of sending
# (see app/wa/api.py's _route_agent_note and app/wa/luna/agent_notes.py's turn-key comments). AUTOINCREMENT
# (SQLite's own guarantee: never reuse a rowid this table has EVER held, not even after it goes empty)
# closes the id-reuse half of that on its own; A3 (the turn keys built from the note's own wamid instead)
# closes the rest independently, so the two fixes do not depend on each other.
#
# WHY A REBUILD, NOT 'ALTER TABLE ... ADD COLUMN': AUTOINCREMENT is part of a column's declared type in
# SQLite, not a property that can be bolted on after the fact -- there is no 'alter table add
# autoincrement'. The only way to add it to an existing table is to build a new table with the right
# declaration and move the data across (sqlite.org's own documented recipe for exactly this situation).
#
# IDEMPOTENT VIA sqlite_master.sql, NOT A MARKER ROW: the table's own CREATE TABLE text is the one
# source of truth for whether this has already run, and it cannot disagree with reality the way a
# separate marker could (e.g. a marker written but the rebuild itself rolled back by a crash in between).
#
# ONE TRANSACTION: create the new table, copy every row by its own explicit column list (not 'select
# *' -- so a future column this repo's own MIGRATIONS list adds later fails LOUDLY here, as a mismatched
# column count, rather than silently copying nothing for it), drop the old table, rename the new one
# into place, recreate the status index -- commit or nothing, so a crash mid-rebuild leaves the ORIGINAL
# table exactly as it was, never a half-copied one. The column list is exactly what this file's own
# MIGRATIONS loop above guarantees exists by the time this function runs (task_id/handed_off_at
# included), so a database mid-upgrade (columns just added a few lines up, in the very same _migrate()
# call) and a database that already had them both copy correctly in the same run.
#
# SAFE WHILE THE LIVE WEB PROCESS HOLDS ITS OWN CONNECTION: verified empirically against a scratch db,
# 2026-09-25 -- a second sqlite3 connection opened in WAL mode (this module's own journal_mode) and left
# open and idle, mirroring pflege-wa.service's own long-lived connection, does not block this
# transaction's CREATE/INSERT/DROP/ALTER RENAME. Every connection this module opens already carries a
# 30s busy timeout (db()'s own sqlite3.connect(..., timeout=30)), which is what would absorb a brief
# collision against a connection that is itself mid-write at the exact same instant -- not a new
# allowance added for this migration, the same one every other write in this file already relies on.
#
# SEEDING sqlite_sequence TO max(id), NOT A SEPARATE STATEMENT: verified empirically, 2026-09-25 --
# copying every row with its OWN existing id (an explicit 'insert into new (id, ...) select id, ... from
# old', never a fresh sequential insert) already leaves SQLite's own sqlite_sequence bookkeeping at
# exactly max(id) of the copied data with zero extra code, and 'alter table ... rename to' carries that
# bookkeeping over to the renamed table's own name. An empty table copies to an empty table and starts
# at 1 on its first real insert -- never an invented floor. A follow-up 'insert or replace into
# sqlite_sequence' seed was deliberately NOT added on top of that: it would only be a redundant,
# separately-invented floor on top of a value SQLite already derives correctly from the data itself.
def _migrate_agent_notes_autoincrement(c):
    row = c.execute("select sql from sqlite_master where type='table' and name='wa_agent_notes'").fetchone()
    if row is None or "autoincrement" in row[0].lower():
        return   # no such table yet (a database this old cannot exist -- SCHEMA always creates it), or
                 # already migrated -- either way, nothing to do; see the sqlite_master.sql comment above
    cols = ("id", "wamid", "phone", "kind", "body", "status", "created_at", "acked_at", "claimed_at",
            "attempts", "progress", "outcome_done", "outcome_not_done", "outcome_needed", "blocked_reason",
            "finished_at", "notified_at", "task_id", "handed_off_at")
    col_list = ", ".join(cols)
    c.execute("begin immediate")
    try:
        c.execute(
            "create table wa_agent_notes__autoincrement_new ("
            "id integer primary key autoincrement, wamid text not null unique, phone text not null, "
            "kind text not null, body text not null, status text not null, created_at text not null, "
            "acked_at text, claimed_at text, attempts integer not null default 0, progress text, "
            "outcome_done text, outcome_not_done text, outcome_needed text, blocked_reason text, "
            "finished_at text, notified_at text, task_id text, handed_off_at text)")
        c.execute(f"insert into wa_agent_notes__autoincrement_new ({col_list}) "
                 f"select {col_list} from wa_agent_notes")
        c.execute("drop table wa_agent_notes")
        c.execute("alter table wa_agent_notes__autoincrement_new rename to wa_agent_notes")
        c.execute("create index if not exists idx_wa_agent_notes_status on wa_agent_notes(status, created_at)")
    except BaseException:
        c.rollback()
        raise
    c.commit()


def thread(c, phone):
    """The thread for this number, created on first contact. Never returns None: an unknown number
    is a lead, not an error."""
    row = c.execute("select * from wa_threads where phone=?", (phone,)).fetchone()
    if row is None:
        c.execute("insert into wa_threads (phone, opened_at) values (?,?)", (phone, now_iso()))
        c.commit()
        row = c.execute("select * from wa_threads where phone=?", (phone,)).fetchone()
    return _thread_row(row)


def _thread_row(row):
    t = dict(row)
    t["slots"] = json.loads(t["slots"] or "{}")
    t["asked"] = json.loads(t["asked"] or "[]")
    t["stopped"] = bool(t["stopped"])
    t["is_test"] = bool(t["is_test"])
    return t


def save_thread(c, t):
    _update_thread(c, t)
    c.commit()


def _update_thread(c, t):
    # Writes neither is_test nor rail: both are pinned by their own function (mark_test_thread,
    # pin_rail) and a card save must never be able to flip them.
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


def record_outbound(c, phone, wamid, body, kind="text", meta=None, at=None):
    """``at``, when given, is the real moment this message left (RFC3339) -- tools/wa_bridge.py's
    broadcast --status backfill (TASK-284) passes the broadcast item's own ``updated_at`` here,
    because it runs well after the actual send and stamping "now" would insert the row at POLL
    time. A row inserted late, out of chronological order relative to whatever the candidate said
    in between, is exactly the "brain has no memory of what it sent" bug TASK-284 exists to fix --
    stamping it at poll time instead of send time would just move the same defect one field over.
    Every other caller is unaffected: at=None keeps the old now_iso() behaviour bit for bit."""
    c.execute("insert into wa_messages (phone, direction, wamid, body, kind, meta, at) values (?,?,?,?,?,?,?)",
              (phone, "out", wamid, body, kind, json.dumps(meta or {}, ensure_ascii=False), at or now_iso()))
    c.commit()


def outbound_recorded(c, wamid):
    """Whether this provider id is already a stored message. ``None`` (a draft) never is."""
    if wamid is None:
        return False
    return c.execute("select 1 from wa_messages where wamid=?", (wamid,)).fetchone() is not None


def history(c, phone, limit=50):
    rows = c.execute("select direction, body, kind, at from wa_messages where phone=? order by id desc limit ?",
                     (phone, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def threads(c, limit=200):
    rows = c.execute("select * from wa_threads order by coalesce(last_inbound_at, opened_at) desc limit ?",
                     (limit,)).fetchall()
    return [_thread_row(r) for r in rows]


# --- test numbers (TASK-212) ---------------------------------------------------------------------
# A phone an operator uses to test the live harness by hand (Ivan's own number first). The flag is a
# wa_threads column, so it survives a restart and every reader sees it: the campaign sender never sends
# to it (campaign.decide -> skip_test_number), candidate reports and the consent queue leave it out, and
# app/wa/luna/purge_test_history.py wipes its history so the next manual test starts from nothing. The
# conversation itself is untouched -- a test thread answers exactly like a real lead. Only the CLI
# app/wa/luna/test_threads.py writes this flag; _update_thread does not, so no card save can flip it.

def mark_test_thread(c, phone, is_test):
    """Mark/unmark this phone as a test number, creating the thread when it has none (marking a number
    before its first message is the point: the first test conversation is then already excluded).
    -> the thread row."""
    thread(c, phone)
    c.execute("update wa_threads set is_test=?, test_marked_at=? where phone=?",
              (int(bool(is_test)), now_iso() if is_test else None, phone))
    c.commit()
    return thread(c, phone)


def is_test_thread(c, phone):
    row = c.execute("select is_test from wa_threads where phone=?", (phone,)).fetchone()
    return bool(row["is_test"]) if row else False


def test_phones(c):
    """Every phone marked as a test number, oldest thread first."""
    rows = c.execute("select phone from wa_threads where is_test=1 order by opened_at, phone").fetchall()
    return [r["phone"] for r in rows]


# --- the thread's rail (TASK-220) ----------------------------------------------------------------
# One column, written by pin_rail and by nothing else: _update_thread leaves it alone, so no card save
# can flip it, and there is no CLI to set it by hand. app/wa/transport.py reads it to decide which
# client a send is built from; GET /api/wa/threads and GET /api/wa/health report it.

RAILS = ("meta", "bridge")


def rail_of(c, phone):
    """The rail pinned on this thread, or None -- no thread yet, or nothing has gone out on it yet."""
    row = c.execute("select rail from wa_threads where phone=?", (phone,)).fetchone()
    return row["rail"] if row else None


def pin_rail(c, phone, rail):
    """Pin the rail on a thread's first successful outbound. -> the pinned rail.

    The pin is immutable because a rail is a sender number, not a configuration detail: the Meta rail
    writes from the WABA number, the bridge rail from the number registered on the handset. Letting a
    live thread change rail means the candidate's next answer arrives from a number they have never
    seen -- a stranger continuing their conversation, which earns a block or a spam report against the
    one WhatsApp asset this project has. A global env rollback (WA_TRANSPORT) would do that to every
    thread at once, which is exactly why the routing state is this row and not that variable.

    Idempotent for the rail already pinned. A different rail raises rather than overwrite: at that
    point the message has already gone out from one number while the thread claims the other, and the
    only honest thing left is to say so loudly. An unknown rail raises before anything is written.
    """
    if rail not in RAILS:
        raise ValueError(f"cannot pin {phone} to rail {rail!r}: not one of {', '.join(RAILS)}")
    # A message just went out to this number, so it has a thread: same rule as thread() ("an unknown
    # number is a lead, not an error"), written the same way record_campaign_send writes it.
    c.execute("insert or ignore into wa_threads (phone, opened_at) values (?,?)", (phone, now_iso()))
    cur = c.execute("update wa_threads set rail=? where phone=? and rail is null", (rail, phone))
    c.commit()
    if cur.rowcount == 1:
        return rail
    pinned = rail_of(c, phone)
    if pinned != rail:
        raise RuntimeError(f"{phone} is pinned to the {pinned!r} rail and a send just went out on the "
                           f"{rail!r} one -- a thread never changes rail, because the rail is the number "
                           f"the candidate sees (TASK-220)")
    return pinned


def rail_counts(c):
    """{rail or 'unpinned': number of threads} -- GET /api/wa/health, so which rail carries which
    threads is answered by one call rather than by reading the database."""
    rows = c.execute("select coalesce(rail, 'unpinned') as rail, count(*) as n from wa_threads "
                     "group by coalesce(rail, 'unpinned') order by rail").fetchall()
    return {r["rail"]: r["n"] for r in rows}


# --- reply-turn claims (TASK-181): durable, cross-process dedup beyond wamid uniqueness ----------
# The wamid UNIQUE constraint on wa_messages stops a Meta redelivery from being answered twice, but
# it says nothing about two DIFFERENT entrypoints (the webhook, and the catch-up driver, TASK-182)
# both deciding -- at the same moment, in separate processes -- to generate and send a reply for
# the SAME already-recorded inbound message. turn_key is that message's own wamid; only one caller
# may hold an active claim on a given (phone, turn_key) at a time.

def claim_reply_turn(c, phone, turn_key):
    """True if the caller may proceed to generate and send a reply for this exact inbound message;
    False if another caller already holds an active claim, or already finished one with state
    'sent' (a reply for this exact message genuinely went out already -- never reclaimable),
    NO_SEND_STATE (the brain decided to stay silent on it, TASK-204: a retry would re-run the model on
    the same message) or AGENT_NOTE_STATE (it was an operator note, answered by its ack and now owned
    by its wa_agent_notes row: a retry would hand a Russian instruction to the candidate brain, which
    is the exact failure that table exists to prevent). Any other terminal state (skipped_rate_cap /
    skipped_stopped / skipped_error),
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
    if row["state"] in ("sent", NO_SEND_STATE, AGENT_NOTE_STATE):
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


# --- TASK-288: a composed-but-undelivered reply survives a send failure, so a retry can resend it
# without paying for another claude -p call. Written right before send_and_record, keyed on the same
# (phone, turn_key) as the claim -- deliberately not cleared on a terminal write: a "sent" or
# NO_SEND_STATE claim is never reclaimed (claim_reply_turn), so a stale blob on it is never read again.

def record_composed_reply(c, phone, turn_key, d):
    c.execute("update wa_reply_turn_claims set composed=? where phone=? and turn_key=?",
              (json.dumps(d, ensure_ascii=False), phone, turn_key))
    c.commit()


def composed_reply(c, phone, turn_key):
    """-> the brain's decision from a prior attempt on this exact turn (turn()'s return dict), or
    None when none was ever recorded -- this exact turn is being composed for the first time, or the
    prior attempt failed before the brain ever answered (nothing to reuse; the caller composes fresh,
    same as before this existed)."""
    row = c.execute("select composed from wa_reply_turn_claims where phone=? and turn_key=?",
                    (phone, turn_key)).fetchone()
    return json.loads(row["composed"]) if row and row["composed"] else None


# --- the operator inbox (Ivan, 2026-09-24) -------------------------------------------------------
# The two halves never run in the same process. The web process only ever reaches the first three
# (look up, record, mark acked) -- it decides and records, it never implements. The periodic worker
# reaches the rest. See the wa_agent_notes comment in SCHEMA for why the split is the point.

# A worker that died mid-note must not hold it forever, but the window has to clear the worst-case
# tick, not the poll interval: one note can mean an edit, the offline test lane (~2 min) and a
# service restart. 45 min is generous against that and still bounded.
AGENT_NOTE_STALE_SEC = 2700


def agent_note_for_wamid(c, wamid):
    return c.execute("select * from wa_agent_notes where wamid=?", (wamid,)).fetchone()


def record_agent_note(c, wamid, phone, body, kind):
    """Insert-or-ignore on the unique wamid, then read back: a redelivered webhook or a catch-up
    re-drive of the same message gets the row that already exists, never a second one. -> the row."""
    c.execute("insert or ignore into wa_agent_notes (wamid, phone, kind, body, status, created_at) "
              "values (?,?,?,?,?,?)", (wamid, phone, kind, body, "pending", now_iso()))
    c.commit()
    return agent_note_for_wamid(c, wamid)


def mark_agent_note_acked(c, note_id, at=None):
    c.execute("update wa_agent_notes set acked_at=? where id=?", (at or now_iso(), note_id))
    c.commit()


def open_agent_notes(c):
    """Notes a worker may still take: pending, in_progress whose claim went stale (the worker that
    held it died), or finished-but-never-delivered -- a note whose completion send failed cleared its
    notified_at, and without it showing up here again nothing would ever look at it and the sender
    would keep an ack he was promised a report for. Oldest first, one note at a time, in arrival
    order."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(seconds=AGENT_NOTE_STALE_SEC)).replace(microsecond=0).isoformat()
    return c.execute("select * from wa_agent_notes where status='pending' "
                     "or (status='in_progress' and claimed_at<?) "
                     "or (status in ('done','blocked') and notified_at is null) "
                     "order by created_at, id", (cutoff,)).fetchall()


def claimable_agent_notes(c):
    """Notes a worker may take through the normal claim -> decode -> card -> hand-off pipeline: pending,
    or in_progress whose claim went stale (the worker that held it died). TASK-303 item B (round-1
    review, both blocking finding #3/the ops-lens equivalent): split out of what used to be
    open_agent_notes()'s own first two OR-clauses, so the third (a finished note whose completion never
    sent) is retried on its own schedule (undelivered_completion_notes, below) and can never sit at the
    head of THIS queue blocking every claimable note behind it -- the exact bug the review reproduced
    (a stuck completion retry as rows[0] starves every later note forever). Oldest first, same order
    open_agent_notes() has always used; open_agent_notes() itself is untouched (still what --list and
    a human reading the queue want: everything outstanding, retries included)."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(seconds=AGENT_NOTE_STALE_SEC)).replace(microsecond=0).isoformat()
    return c.execute("select * from wa_agent_notes where status='pending' "
                     "or (status='in_progress' and claimed_at<?) order by created_at, id",
                     (cutoff,)).fetchall()


def undelivered_completion_notes(c):
    """Every finished note (done or blocked) whose one completion send never went out, oldest first, NO
    LIMIT (TASK-303 item B, Ivan 2026-09-25: 'no cap on completion retries', matching AC#8's own 'until
    delivered'). agent_note_worker.run_once() retries every one of these independently, every tick: one
    that can never be delivered (a suppressed test number, a standing bridge outage) must never keep any
    OTHER finished note's own completion from being retried the same tick, and must never keep a fresh
    pending note from being decoded and handed off either -- see claimable_agent_notes, above, for the
    other half of that split."""
    return c.execute("select * from wa_agent_notes where status in ('done','blocked') and notified_at is null "
                     "order by created_at, id").fetchall()


def orphaned_in_progress_notes(c):
    """An in_progress note whose claim has NOT yet gone stale, found by a worker run that itself holds
    the cron lock (tools/agent_note_cron.sh's own 'flock -n' -- in production exactly one tick ever runs
    at a time). Since nothing else could legitimately be running to have claimed it moments ago, a row
    like this can only be a CRASHED prior tick's leftover claim: TASK-303 item C, round-1 review finding
    -- 'the prefilter counts in_progress no matter how stale, so for 45 minutes every tick... writes
    health ok:true, which hides the crash', because open_agent_notes()/claimable_agent_notes() both
    deliberately exclude a live (not-yet-stale) in_progress row, so nothing before this function could
    even SEE one to report it. Oldest first; every row found is reported, not just the first -- see
    agent_note_worker.write_tick_health."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(seconds=AGENT_NOTE_STALE_SEC)).replace(microsecond=0).isoformat()
    return c.execute("select * from wa_agent_notes where status='in_progress' and claimed_at>=? "
                     "order by claimed_at, id", (cutoff,)).fetchall()


def claim_agent_note(c, note_id):
    """True if the caller now owns this note. One conditional UPDATE, so the database decides who won
    when two workers race -- same shape as claim_reply_turn's insert."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(seconds=AGENT_NOTE_STALE_SEC)).replace(microsecond=0).isoformat()
    cur = c.execute("update wa_agent_notes set status='in_progress', claimed_at=?, attempts=attempts+1 "
                    "where id=? and (status='pending' or (status='in_progress' and claimed_at<?))",
                    (now_iso(), note_id, cutoff))
    c.commit()
    return cur.rowcount == 1


def append_agent_note_progress(c, note_id, line):
    """One timestamped line onto the note's trail. A worker that dies mid-note leaves the next one a
    record of how far it got, so the retry continues instead of starting over."""
    row = c.execute("select progress from wa_agent_notes where id=?", (note_id,)).fetchone()
    trail = f"{row['progress']}\n" if row and row["progress"] else ""
    c.execute("update wa_agent_notes set progress=? where id=?",
              (f"{trail}[{now_iso()}] {line}", note_id))
    c.commit()


def finish_agent_note(c, note_id, status, done, not_done, needed, blocked_reason=None):
    """Works exactly the same on a note the worker had already handed off (status='handed_off') as on
    one it never got past claiming (TASK-303): no WHERE on the current status, because the working
    session -- the only caller of --done/--blocked once a note is handed off -- must always be able to
    close its own note, whatever the worker last left it as."""
    c.execute("update wa_agent_notes set status=?, outcome_done=?, outcome_not_done=?, outcome_needed=?, "
              "blocked_reason=?, finished_at=?, claimed_at=null where id=?",
              (status, done, not_done, needed, blocked_reason, now_iso(), note_id))
    c.commit()


# TASK-303: the worker found or created this note's backlog card and told the working session about it.
# From this moment the note belongs to that session, not to the worker -- see the wa_agent_notes SCHEMA
# comment above for the full reasoning. open_agent_notes() must never return a row in this status: none
# of its three OR-clauses (pending / stale in_progress / undelivered done|blocked) can ever match it, so
# nothing here re-derives that exclusion -- it is a property of the query, covered by its own test.
AGENT_NOTE_HANDED_OFF = "handed_off"


def set_agent_note_task(c, note_id, task_id):
    """Record the backlog card this note now has. Called exactly once per note, right after the card is
    created (or, on crash recovery, right after an existing one for it is found) -- a later retry that
    reaches the same step always finds task_id already set and skips card creation entirely, which is
    what keeps a retried hand-off from ever minting a second card for the same note."""
    c.execute("update wa_agent_notes set task_id=? where id=?", (task_id, note_id))
    c.commit()


def mark_agent_note_handed_off(c, note_id):
    """status -> handed_off, handed_off_at stamped, claimed_at cleared: the worker's own claim on this
    note is spent, and nothing about wa_agent_notes belongs to the worker's claim loop from here on.
    Conditional on the row still being in_progress -- the same shape as claim_agent_note's own
    conditional UPDATE -- so a note that raced to some other terminal state under this call (should not
    happen inside one worker's single-threaded run, but this is the row two different worker runs could
    in principle both be mid-flight on) is not silently overwritten. -> True when this call actually
    made the transition."""
    cur = c.execute("update wa_agent_notes set status=?, handed_off_at=?, claimed_at=null "
                    "where id=? and status='in_progress'", (AGENT_NOTE_HANDED_OFF, now_iso(), note_id))
    c.commit()
    return cur.rowcount == 1


def release_agent_note(c, note_id):
    """A worker gave up on this note without finishing it this run (a decode failure, a failed
    hand-off): back to pending, claimed_at cleared, so the next tick's claim_agent_note can take it
    again. task_id and attempts are deliberately left untouched -- a card already created must not be
    created a second time on the retry (see set_agent_note_task), and attempts keeps counting across
    releases so agent_note_worker.ATTEMPTS_CAP is still reached even when every attempt fails at the
    same later step, not reset back to looking fresh forever. Conditional on the row still being
    in_progress, for the same reason mark_agent_note_handed_off's own conditional is. -> True when this
    call actually released it."""
    cur = c.execute("update wa_agent_notes set status='pending', claimed_at=null "
                    "where id=? and status='in_progress'", (note_id,))
    c.commit()
    return cur.rowcount == 1


def mark_agent_note_notified(c, note_id):
    """True if this call is the one that gets to send the completion note. Conditional on notified_at
    still being null, so two workers that both reached the end of the same note still send once."""
    cur = c.execute("update wa_agent_notes set notified_at=? where id=? and notified_at is null",
                    (now_iso(), note_id))
    c.commit()
    return cur.rowcount == 1


def clear_agent_note_notified(c, note_id):
    """Undo the claim on the completion note after the send itself failed, so a later attempt may try
    again. Without this, mark-then-send would burn the one delivery on a send that never happened."""
    c.execute("update wa_agent_notes set notified_at=null where id=?", (note_id,))
    c.commit()


def agent_note(c, note_id):
    return c.execute("select * from wa_agent_notes where id=?", (note_id,)).fetchone()


def handed_off_agent_notes(c):
    """Every note currently owned by the working session (status=handed_off), oldest first -- TASK-303:
    the listing open_agent_notes() deliberately excludes, because this one is the working session's OWN
    view of what it still owes a --done/--blocked on (app/wa/luna/agent_notes.py --handed-off), not the
    worker's claim queue."""
    return c.execute("select * from wa_agent_notes where status=? order by created_at, id",
                     (AGENT_NOTE_HANDED_OFF,)).fetchall()


def recent_agent_notes_for_phone(c, phone, exclude_id, limit=5):
    """Up to ``limit`` earlier notes from this same phone, most recent first, excluding
    ``exclude_id`` itself -- the card-decoder's own prior-history context (TASK-303, agent_note_worker
    step (e)): a repeat instruction ("опять то же самое") reads very differently once the working
    session can see what the last one asked for and how it ended."""
    rows = c.execute("select * from wa_agent_notes where phone=? and id!=? order by created_at desc, id desc "
                     "limit ?", (phone, exclude_id, limit)).fetchall()
    return [dict(r) for r in rows]


# --- per-candidate LLM call rate limit (TASK-180) ------------------------------------------------

def record_luna_call(c, phone):
    c.execute("insert into wa_luna_calls (phone, at) values (?,?)", (phone, now_iso()))
    c.commit()


def count_recent_luna_calls(c, phone, within_hours=1.0):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=within_hours)).isoformat()
    row = c.execute("select count(*) as n from wa_luna_calls where phone=? and at>=?",
                    (phone, cutoff)).fetchone()
    return row["n"]


# --- send-failure visibility (TASK-183) -----------------------------------------------------------

def record_send_failure(c, phone, error):
    c.execute("insert into wa_send_failures (phone, error, at) values (?,?,?)", (phone, error, now_iso()))
    c.commit()


def recent_send_failure(c, phone):
    row = c.execute("select error, at from wa_send_failures where phone=? order by id desc limit 1",
                    (phone,)).fetchone()
    return dict(row) if row else None


# --- proactive follow-up nudges (TASK-189) --------------------------------------------------------

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

    Deliberately simpler than claim_reply_turn (TASK-181): nothing here is ever reclaimable. A
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
    """Every phone with a thread, not stopped and not suppressed -- the pool app.wa.luna.followups/
    catchup-style drivers scan.

    TASK-216: the suppression filter is the identity-scoped twin of ``stopped=0`` right beside it. A
    suppressed number that stayed in the pool would reach ``api.send_and_record``, which refuses it by
    raising -- correct for a send somebody asked for, but it would end the whole sweep for every other
    thread. Not sending is not a decision made here: the choke point still refuses these numbers if a
    driver is pointed at one by hand (``followups --phones``)."""
    rows = c.execute("select phone from wa_threads where stopped=0 "
                     "and phone not in (select phone from wa_suppressions)").fetchall()
    return [r["phone"] for r in rows]


# --- inbound media originals (TASK-198) -----------------------------------------------------------
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
    """app/cv.py:classify_document() result (TASK-185) for this one file, and the card key its text
    went to -- chosen by document_type (cv_text/urkunde_text, None for any other type, TASK-199)."""
    c.execute("update wa_documents set document_type=?, certificate_level=?, text_key=? where id=?",
              (document_type, certificate_level, text_key, doc_id))
    c.commit()


# wa_documents.text_key of a voice note's transcript (TASK-210). Names no card key: the transcript is the turn's text.
VOICE_TRANSCRIPT_KEY = "voice_transcript"


def set_voice_transcript(c, doc_id, wamid, text, model):
    """A voice note's transcript, in one commit: wa_documents row ``doc_id`` (text, text_key VOICE_TRANSCRIPT_KEY) and
    the inbound message: body (was empty; the old system's body = COALESCE(body, transcript), so every reader of the
    message -- thread history, consent CV analysis, campaign --status, replies_to, the dry run -- sees what was said)
    and meta (transcript, transcript_model, transcribed_at: marks the body as a voice note's transcript). Raises when
    ``wamid`` is not a stored inbound message."""
    row = c.execute("select meta from wa_messages where wamid=? and direction='in'", (wamid,)).fetchone()
    if row is None:
        raise RuntimeError(f"no inbound wa_messages row for {wamid}")
    meta = {**json.loads(row["meta"] or "{}"), "transcript": text, "transcript_model": model,
            "transcribed_at": now_iso()}
    c.execute("update wa_documents set text=?, text_key=? where id=?", (text, VOICE_TRANSCRIPT_KEY, doc_id))
    c.execute("update wa_messages set body=?, meta=? where wamid=?",
              (text, json.dumps(meta, ensure_ascii=False), wamid))
    c.commit()


def documents_for(c, phone, include_deleted=False):
    """Every stored original for this phone, oldest first, all columns (text included).

    include_deleted=True is for audit/admin tooling only -- the model never reaches a forgotten
    document this way (TASK-289)."""
    sql = "select * from wa_documents where phone=?"
    if not include_deleted:
        sql += " and deleted_at is null"
    rows = c.execute(sql + " order by id", (phone,)).fetchall()
    return [dict(r) for r in rows]


def document_for_wamid(c, wamid, include_deleted=False):
    """The stored original that came with this inbound message, all columns, or None.

    include_deleted=True is for audit/admin tooling only (TASK-289)."""
    sql = "select * from wa_documents where wamid=?"
    if not include_deleted:
        sql += " and deleted_at is null"
    row = c.execute(sql, (wamid,)).fetchone()
    return dict(row) if row else None


def forget_document(c, doc_id, at=None):
    """Mark this wa_documents row as forgotten (TASK-289): same soft-delete contract as
    forget_message -- the row stays, every ordinary read stops seeing it.
    -> True when a row was actually marked (False for an unknown id or one already forgotten)."""
    cur = c.execute("update wa_documents set deleted_at=? where id=? and deleted_at is null",
                    (at or now_iso(), doc_id))
    c.commit()
    return cur.rowcount > 0


# --- imported history (TASK-205, app/wa/luna/import_history.py) -----------------------------------
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


def document_with_sha256(c, phone, sha256, include_deleted=False):
    """The oldest stored original of this phone with these bytes, all columns, or None.

    include_deleted=False (default) so a forgotten document (TASK-289) never dedupes a fresh
    upload of the same bytes -- the candidate sending it again is treated as new, not a repeat."""
    sql = "select * from wa_documents where phone=? and sha256=?"
    if not include_deleted:
        sql += " and deleted_at is null"
    row = c.execute(sql + " order by id limit 1", (phone, sha256)).fetchone()
    return dict(row) if row else None


def document_by_id(c, doc_id, include_deleted=False):
    """include_deleted=False (default): a forgotten document (TASK-289) is not reachable by id
    either -- callers that already hold a stale id for it (e.g. a reuse-confirmation candidate the
    model was shown before it was forgotten) get None, same as an unknown id."""
    sql = "select * from wa_documents where id=?"
    if not include_deleted:
        sql += " and deleted_at is null"
    row = c.execute(sql, (doc_id,)).fetchone()
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


# --- webhook statuses, raw events, pending inbound work (TASK-202) ---------------------------------
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


# --- campaign seed, message lookups for the Luna turn context (TASK-203/101) -----------------------

def _message_row(row):
    return {**dict(row), "meta": json.loads(row["meta"] or "{}")}


def message_by_wamid(c, wamid, include_deleted=False):
    """One wa_messages row (either direction) with meta parsed, or None.

    include_deleted=True is for audit/admin tooling only (e.g. tools/wa_bridge.py audit) -- the
    model never reaches a forgotten message this way (TASK-289)."""
    sql = "select * from wa_messages where wamid=?"
    if not include_deleted:
        sql += " and deleted_at is null"
    row = c.execute(sql, (wamid,)).fetchone()
    return _message_row(row) if row else None


def messages_for(c, phone, after_id=0, direction=None, include_deleted=False):
    """This phone's wa_messages rows with id > after_id, oldest first, meta parsed; one direction or both.

    include_deleted=True is for audit/admin tooling only -- the model never reaches a forgotten
    message this way (TASK-289)."""
    sql, args = "select * from wa_messages where phone=? and id>?", [phone, after_id]
    if not include_deleted:
        sql += " and deleted_at is null"
    if direction:
        sql, args = sql + " and direction=?", args + [direction]
    return [_message_row(r) for r in c.execute(sql + " order by id", args).fetchall()]


def messages_before(c, phone, before_id=None, limit=20, include_deleted=False):
    """Up to ``limit`` of this phone's wa_messages rows older than ``before_id`` (the most recent
    ``limit`` overall when ``before_id`` is None), returned OLDEST FIRST within the page -- paging
    further back than turn_context's own recent tail reaches (TASK-290), on the same deleted_at
    filter as messages_for.
    -> (rows, has_more): has_more is True when at least one older row exists beyond this page.

    The operator inbox's own ack/completion messages are filtered out for the same reason
    luna_brain.turn_context drops them: on a test thread they sit between the operator's German test
    turns, and the model paging back through its own history must not read the harness talking to a
    colleague about a work item as something the candidate persona said."""
    sql, args = "select * from wa_messages where phone=?", [phone]
    if not include_deleted:
        sql += " and deleted_at is null"
    sql += " and coalesce(json_extract(meta, '$.action'), '') not in ('agent_note_ack','agent_note_done')"
    if before_id is not None:
        sql += " and id<?"
        args.append(before_id)
    rows = c.execute(sql + " order by id desc limit ?", args + [limit + 1]).fetchall()
    has_more = len(rows) > limit
    page = list(rows[:limit])
    page.reverse()
    return [_message_row(r) for r in page], has_more


def forget_message(c, wamid, at=None):
    """Mark this wa_messages row as forgotten (TASK-289): the model and every ordinary read stop seeing
    it (message_by_wamid/messages_for filter it out by default), but the row itself is never deleted --
    an operator asking to "forget" something means hide it from the conversation, not destroy the record.
    ``at``, when given, is the real moment this was forgotten (RFC3339); at=None uses now.
    -> True when a row was actually marked (False for an unknown wamid or one already forgotten)."""
    cur = c.execute("update wa_messages set deleted_at=? where wamid=? and deleted_at is null",
                    (at or now_iso(), wamid))
    c.commit()
    return cur.rowcount > 0


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
    """The one place a campaign template send lands (TASK-203; the sender, TASK-206, calls it after Meta
    returned ``wamid``). ``rendered`` is app/wa/meta.py:render_template's result for the exact definition
    and parameters sent. In one commit: the thread (created when missing), the outbound row (body = rendered
    text, meta = {action: 'campaign', campaign_id, template_id, template, language, variables, buttons}),
    ``last_outbound_at`` (never last_inbound_at), and the card contract Luna reads, replacing an earlier
    campaign or an earlier attempt of the same campaign on the card (TASK-209; every send stays a row):

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


# --- campaign sends (TASK-206, app/wa/luna/campaign.py; attempts TASK-209) ------------------------------------------
# One row per attempt (campaign, phone, attempt 1..n): the durable send claim, never overwritten by a later attempt.
# state: in_progress = claimed, the POST result not recorded (after a crash: uncertain); sent = Meta returned the wamid,
# recorded in the same commit as the outbound row and card.campaign; failed = Meta answered with an HTTP 4xx error,
# nothing went out, ownership restored; uncertain = network error, timeout, HTTP 5xx or a 2xx without wamid: it may
# have gone out. The phone's claim in a campaign is its latest attempt. Delivery is not copied here:
# wa_message_statuses by wamid is the one record, per attempt. Not in SCHEMA: only the sender's connection creates the
# table (ensure_campaign_schema). While an attempt is in_progress the phone also holds the reply-turn claim
# 'campaign:<id>', so the webhook worker and catch-up leave the card alone until the send is recorded
# (ST.claim_in_flight).

_CAMPAIGN_TABLE = """create table {name} (
  campaign_id text not null,
  phone text not null,
  attempt integer not null,
  state text not null check (state in ('in_progress', 'sent', 'failed', 'uncertain')),
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
  primary key (campaign_id, phone, attempt)
)"""
CAMPAIGN_SCHEMA = (_CAMPAIGN_TABLE.format(name="if not exists wa_campaign_sends") + ";\n"
                   "create index if not exists idx_wa_campaign_sends_phone on wa_campaign_sends(phone);\n")
CAMPAIGN_CLAIM_PREFIX = "campaign:"   # wa_reply_turn_claims.turn_key 'campaign:<id>', wa_nudge_claims 'campaign:<id>:<n>'
# Columns of the TASK-206 layout (one row per campaign and phone, ``attempts`` overwritten by every re-claim).
_TASK103_COLUMNS = ("campaign_id", "phone", "state", "template_id", "template_name", "template_language", "variables",
                    "rendered_text", "prior_owner", "prior_reason", "prior_since", "ownership_restore", "wamid", "error",
                    "error_code", "error_payload", "claimed_at", "sent_at", "finished_at")


def _campaign_table_is_task103(c):
    return "attempts" in {r[1] for r in c.execute("pragma table_info(wa_campaign_sends)").fetchall()}


def ensure_campaign_schema(c):
    """Create wa_campaign_sends, or rebuild a TASK-206 table (pk campaign_id+phone, column ``attempts``) into one row
    per attempt: each row becomes attempt = its ``attempts`` value (the earlier attempts of such a row were
    overwritten then and are not recoverable). One BEGIN IMMEDIATE transaction, the layout checked again under the
    write lock (another sender may have rebuilt it meanwhile); a crash rolls it back. Idempotent."""
    if _campaign_table_is_task103(c):
        c.commit()
        c.execute("begin immediate")
        try:
            if _campaign_table_is_task103(c):
                cols = ", ".join(_TASK103_COLUMNS)
                c.execute(_CAMPAIGN_TABLE.format(name="wa_campaign_sends_task106"))
                c.execute(f"insert into wa_campaign_sends_task106 (attempt, {cols}) "
                          f"select attempts, {cols} from wa_campaign_sends")
                c.execute("drop table wa_campaign_sends")
                c.execute("alter table wa_campaign_sends_task106 rename to wa_campaign_sends")
            c.commit()
        except BaseException:
            c.rollback()
            raise
    c.executescript(CAMPAIGN_SCHEMA)


def _campaign_row(row):
    d = dict(row)
    d["variables"] = json.loads(d["variables"])
    d["error_payload"] = json.loads(d["error_payload"]) if d["error_payload"] is not None else None
    return d


def campaign_send(c, campaign_id, phone):
    """The phone's claim in one campaign: its latest attempt, or None."""
    row = c.execute("select * from wa_campaign_sends where campaign_id=? and phone=? order by attempt desc limit 1",
                    (campaign_id, phone)).fetchone()
    return _campaign_row(row) if row else None


def campaign_attempts(c, campaign_id, phone=None):
    """Every attempt of one campaign in claim order; of one phone when given, by attempt number."""
    if phone is None:
        rows = c.execute("select * from wa_campaign_sends where campaign_id=? order by claimed_at, attempt",
                         (campaign_id,)).fetchall()
    else:
        rows = c.execute("select * from wa_campaign_sends where campaign_id=? and phone=? order by attempt",
                         (campaign_id, phone)).fetchall()
    return [_campaign_row(r) for r in rows]


def _latest_per(rows, key):
    latest = {}
    for r in rows:   # claim order: a key keeps its first position, the value becomes its highest attempt
        if key(r) not in latest or r["attempt"] > latest[key(r)]["attempt"]:
            latest[key(r)] = r
    return list(latest.values())


def campaign_sends(c, campaign_id):
    """The latest attempt of every phone of one campaign, in first-claim order."""
    return _latest_per(campaign_attempts(c, campaign_id), key=lambda r: r["phone"])


def campaign_sends_for_phone(c, phone):
    """The latest attempt of this phone in every campaign, in first-claim order."""
    rows = c.execute("select * from wa_campaign_sends where phone=? order by claimed_at, attempt", (phone,)).fetchall()
    return _latest_per([_campaign_row(r) for r in rows], key=lambda r: r["campaign_id"])


def campaign_claims_after(c, campaign_id, after_iso):
    """claimed_at of this campaign's attempts later than ``after_iso`` (same UTC ISO format), oldest first. Every
    attempt counts: each is a claim and a POST."""
    rows = c.execute("select claimed_at from wa_campaign_sends where campaign_id=? and claimed_at>? order by claimed_at",
                     (campaign_id, after_iso)).fetchall()
    return [r["claimed_at"] for r in rows]


def claim_campaign_send(c, campaign_id, phone, template, variables, rendered_text, prior, claimed_at,
                        retry_uncertain=False, retry_delivery_failed=False):
    """Claim the phone's next attempt (a new row; earlier attempts keep their wamid, error and statuses). No commit:
    the caller commits it with the ownership flip (routing.flip_to_us_for_campaign) in one transaction. Attempt 1
    when the phone has none; after a ``failed`` latest attempt always; after ``in_progress``/``uncertain`` only with
    ``retry_uncertain`` (an ``in_progress`` one, whose POST result was never recorded, is finished as uncertain);
    after ``sent`` only with ``retry_delivery_failed`` while the latest Meta status of its wamid is ``failed``
    (TASK-209). Anything else raises. Also writes the nudge claim 'campaign:<id>:<attempt>' and the reply-turn claim
    'campaign:<id>' (in_progress). -> attempt number."""
    existing = campaign_send(c, campaign_id, phone)
    if existing is not None:
        state = existing["state"]
        delivery = latest_message_status(c, existing["wamid"]) if state == "sent" else None
        undelivered = delivery is not None and delivery["status"] == "failed"
        if not (state == "failed" or (retry_uncertain and state in ("in_progress", "uncertain"))
                or (retry_delivery_failed and undelivered)):
            raise RuntimeError(f"campaign {campaign_id} {phone} attempt {existing['attempt']} is {state}"
                               f"{' (delivery failed)' if undelivered else ''}, not claimable")
    attempt = 1 if existing is None else existing["attempt"] + 1
    if existing is not None and existing["state"] == "in_progress":
        c.execute("update wa_campaign_sends set state='uncertain', error=?, finished_at=? where campaign_id=? and "
                  "phone=? and attempt=? and state='in_progress'",
                  (f"no POST result recorded (the sending run stopped); superseded by attempt {attempt}", claimed_at,
                   campaign_id, phone, existing["attempt"]))
    prior = prior or {}
    c.execute("""insert into wa_campaign_sends (campaign_id, phone, attempt, state, template_id, template_name,
                 template_language, variables, rendered_text, prior_owner, prior_reason, prior_since, claimed_at)
                 values (?,?,?,'in_progress',?,?,?,?,?,?,?,?,?)""",
              (campaign_id, phone, attempt, str(template["id"]), template["name"], template["language"],
               json.dumps(variables, ensure_ascii=False), rendered_text, prior.get("owner"), prior.get("reason"),
               prior.get("since"), claimed_at))
    c.execute("insert into wa_nudge_claims (phone, fingerprint, claimed_at) values (?,?,?)",
              (phone, f"{CAMPAIGN_CLAIM_PREFIX}{campaign_id}:{attempt}", claimed_at))
    now = now_iso()
    c.execute("""insert into wa_reply_turn_claims (phone, turn_key, state, claimed_at, updated_at)
                 values (?,?,'in_progress',?,?) on conflict(phone, turn_key) do update set state='in_progress',
                 claimed_at=excluded.claimed_at, updated_at=excluded.updated_at""",
              (phone, CAMPAIGN_CLAIM_PREFIX + campaign_id, now, now))
    return attempt


def finish_campaign_send(c, campaign_id, phone, attempt, state, at, wamid=None, error=None, error_code=None,
                         error_payload=None, ownership_restore=None):
    """Attempt ``attempt`` in_progress -> sent (with ``wamid``) / failed / uncertain, and the reply-turn claim
    'campaign:<id>' -> 'campaign_<state>'. No commit. Raises unless that attempt is in_progress."""
    if state not in ("sent", "failed", "uncertain"):
        raise ValueError(f"not a final campaign send state: {state!r}")
    cur = c.execute("""update wa_campaign_sends set state=?, wamid=?, error=?, error_code=?, error_payload=?,
                       ownership_restore=?, sent_at=?, finished_at=? where campaign_id=? and phone=? and attempt=?
                       and state='in_progress'""",
                    (state, wamid, error, None if error_code is None else str(error_code),
                     _json_or_none(error_payload), ownership_restore, at if state == "sent" else None, at,
                     campaign_id, phone, attempt))
    if cur.rowcount != 1:
        raise RuntimeError(f"campaign {campaign_id} {phone} attempt {attempt}: no in_progress claim to finish as {state}")
    c.execute("update wa_reply_turn_claims set state=?, updated_at=? where phone=? and turn_key=?",
              ("campaign_" + state, now_iso(), phone, CAMPAIGN_CLAIM_PREFIX + campaign_id))


def reconcile_campaign_send(c, campaign_id, phone, attempt, wamid, sent_at):
    """An in_progress/uncertain attempt whose template did go out (operator evidence: a status webhook for ``wamid``,
    app/wa/luna/campaign.py --mark-sent) -> sent with that wamid and sent_at; the earlier error stays as history.
    The reply-turn claim 'campaign:<id>' -> 'campaign_sent'. No commit. Raises unless that attempt is in_progress
    or uncertain."""
    now = now_iso()
    cur = c.execute("""update wa_campaign_sends set state='sent', wamid=?, sent_at=?, finished_at=? where campaign_id=?
                       and phone=? and attempt=? and state in ('in_progress', 'uncertain')""",
                    (wamid, sent_at, now, campaign_id, phone, attempt))
    if cur.rowcount != 1:
        raise RuntimeError(f"campaign {campaign_id} {phone} attempt {attempt}: no in_progress/uncertain claim to mark "
                           f"sent")
    c.execute("update wa_reply_turn_claims set state='campaign_sent', updated_at=? where phone=? and turn_key=?",
              (now, phone, CAMPAIGN_CLAIM_PREFIX + campaign_id))
