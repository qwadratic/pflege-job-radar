"""SQLite for the harness: one thread per phone number, one row per message.

Two invariants carry the whole design. ``wa_messages.wamid`` is UNIQUE, because Meta redelivers a
webhook for minutes after a non-2xx and the same message must not be answered twice. ``wa_threads.stopped``
is checked before every send, because an opt-out that can be overtaken by a queued reply is not an opt-out.
Slots are a JSON blob: they are the conversation's memory, and their vocabulary lives in app/wa/slots.py.
"""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

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


# --- reply-turn claims (TASK-77): durable, cross-process dedup beyond wamid uniqueness ----------
# The wamid UNIQUE constraint on wa_messages stops a Meta redelivery from being answered twice, but
# it says nothing about two DIFFERENT entrypoints (the webhook, and the catch-up driver, TASK-78)
# both deciding -- at the same moment, in separate processes -- to generate and send a reply for
# the SAME already-recorded inbound message. turn_key is that message's own wamid; only one caller
# may hold an active claim on a given (phone, turn_key) at a time.

def claim_reply_turn(c, phone, turn_key):
    """True if the caller may proceed to generate and send a reply for this exact inbound message;
    False if another caller already holds an active claim, or already finished one with state
    'sent' (a reply for this exact message genuinely went out already -- never reclaimable). Any
    other terminal state (skipped_rate_cap / skipped_no_send / skipped_stopped / skipped_error), or
    a stale in_progress claim from a crashed prior attempt, is reclaimable: those all mean no reply
    actually left this system yet, so a later retry (catch-up) must still be allowed to try."""
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
    if row["state"] == "sent":
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
