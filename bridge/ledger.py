"""The write-ahead journal and the first-body-wins idempotency store (TASK-359, TASK-217).

Lives on the remote machine, next to the phone, because that is where the uncertainty is: the
window between "keys were pressed" and "a tick was read" is 20-60 s on this rail and it is the
dominant failure class, not an edge case. A ledger on our VPS would be a record of what we asked
for; this one is a record of what the handset was told to do.

FOUR RULES, and they are the task:
  1. The row is written BEFORE the send is attempted. If the process dies between the write and
     the confirm, the row stays ``attempting`` -- which is the truth, and the only honest state.
  2. Same key, same body -> replay the original result, send nothing.
  3. Same key, DIFFERENT body, and the first body is SENT or may still be in flight (``attempting``,
     ``unconfirmed``) -> record a body_mismatch, return the first body's outcome, send nothing. A
     regenerated reply must never overwrite a delivered one. But if the first body is RESENDABLE
     (``not_attempted``, ``absent``) it demonstrably never reached the phone, so there is nothing to
     protect: record the body_mismatch for the audit trail, then let the new body proceed (TASK-236
     -- the alternative is a body-sensitive key paired with a non-deterministic generator, which has
     no exit).
  4. ``attempting`` is resolvable only by a reconcile, and only ``confirmed_absent`` authorises a
     resend. An automatic retry out of ``attempting`` is a duplicate message to a real person.

WHAT IS NOT STORED: message bodies and nothing derived from them but a sha256. The phone number is
stored because reconcile has to be able to re-open the chat; it is never written to a log line
(``thread_tag`` is what the logs get).

THE ONE EXCEPTION, AND IT IS DELIBERATE (TASK-376): ``broadcast_item.body`` holds the text of an
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
from datetime import datetime, timedelta, timezone

from . import media as MD

# --- states -----------------------------------------------------------------------------------
ATTEMPTING = "attempting"        # written before the first keystroke; only reconcile resolves it
SENT = "sent"                    # a verified delivery tick was read off the bubble. Terminal.
UNCONFIRMED = "unconfirmed"      # keys were pressed, no tick. 504. Never auto-resent.
ABSENT = "absent"                # reconcile proved nothing went out. Authorises one resend.
NOT_ATTEMPTED = "not_attempted"  # refused before any keystroke (lock busy, chat would not open)

#: states in which the same key may be sent again
RESENDABLE = frozenset({ABSENT, NOT_ATTEMPTED})

#: states in which a send's fate is known and it is not still open (TASK-277) -- the same set
#: bridge/retention.py::classify_op_artifact trusts to auto-resolve a failed op's own artefact.
#: Owned here, not retention.py, so sweep() below can key its own outbound delete on the exact set
#: retention calls "settled" instead of drifting from it. UNCONFIRMED is deliberately absent: "keys
#: were pressed, no tick" is still an open question a reconcile has to answer, never one age alone
#: settles.
SAFE_OUTBOUND_STATES = frozenset({SENT, ABSENT, NOT_ATTEMPTED})

# --- broadcast states (TASK-376) ---------------------------------------------------------------
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

# --- phone-op queue states (TASK-227) -----------------------------------------------------------
OP_QUEUED = "queued"    # waiting its turn; nothing has touched the phone for it yet
OP_RUNNING = "running"  # the dispatcher claimed it and is inside the executor call right now
OP_DONE = "done"        # the executor call returned; ``result`` carries what it returned
OP_FAILED = "failed"    # the executor call raised; ``error`` carries the refusal/error envelope
OP_TERMINAL = frozenset({OP_DONE, OP_FAILED})

#: TASK-296-adjacent, Ivan 2026-09-24: three tiers, lower claims first, FIFO preserved WITHIN a
#: tier (``claim_next_op`` orders by ``priority, position``). A live, candidate-facing exchange
#: (a reply, or any photo/gallery/document send -- there is no cold-outreach media path on this
#: rail) outranks a first-touch/campaign send, which outranks background reading (chat-list/thread
#: reads, reconcile) that nobody is waiting on. Classified by the caller at enqueue time
#: (bridge/server.py's route handlers, bridge/watcher.py's UnresolvedSendWatcher) -- never here or
#: in bridge/dispatcher.py, which is generic dispatch on purpose (its own module docstring).
PRIORITY_URGENT = 0
PRIORITY_NORMAL = 1
PRIORITY_LOW = 2

#: TASK-359 AC#9. Ledger rows and journal lines are kept 30 days; the mini has 457 G free but a
#: candidate's thread metadata is not something to keep forever in a shared home directory.
LEDGER_RETENTION_DAYS = 30
#: Inbound events already acked by our VPS are handed over; 7 days is the batch-result retention.
INBOUND_RETENTION_DAYS = 7

#: The inbound event minted for a human's attach (TASK-360 round 5 -- decision-9, 2026-09-22:
#: automatic attribution removed). There is never a real notification behind it: the file may have
#: arrived long after its own placeholder message was drained and swept off this ledger, or (the
#: files already on the live rail before this round) before this ledger even recorded one. A fresh
#: event is minted every time rather than trying to find and patch an old one -- one code path,
#: whether or not a placeholder ever existed. Deterministic on the queue id alone, so a retried
#: attach after a crash between ``append_inbound`` and ``link_media`` below replays (the same
#: unique key) instead of minting a second event for the same file.
ATTACH_INBOUND_PREFIX = "wab.i.attach."

#: The inbound event ``link_media_auto`` mints when the row it matched has already been acked
#: (TASK-233): ``pull_inbound`` only merges a link into rows with ``id`` past the relay's own
#: cursor, and an acked row's id is behind it for good -- linking that row directly is invisible
#: forever, not delayed. Same idea as ``ATTACH_INBOUND_PREFIX`` above, own prefix so the two mints
#: (a human naming a phone; the matcher rescuing an already-drained row) never collide on one key.
AUTO_LINK_INBOUND_PREFIX = "wab.i.autolink."

#: WhatsApp's own generic notification glyph per kind (bridge/inbound.py::MEDIA_HINTS), used as the
#: placeholder text of a hand-minted attach event -- honest about being a placeholder, never a
#: sender's real filename.
ATTACH_PLACEHOLDER_TEXT = {"image": "\U0001f4f7 Foto", "video": "\U0001f3a5 Video",
                          "document": "\U0001f4c4 Dokument", "audio": "\U0001f3a4 Sprachnachricht"}

#: How wide a net "threads that plausibly relate to this file" (the queue listing, TASK-360 round
#: 5 requirement 3) casts. Informational only, never a decision: nothing built on this ever
#: attaches anything, so a wide net costs a human a longer glance, not a wrong attach -- unlike the
#: deleted link_files' 180s pre-filter, which was a proof obligation for an automatic decision.
RELATED_THREAD_WINDOW_SEC = 3600.0

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
  resolved_at   text,
  reconcile_attempts integer not null default 0,
  escalated_at  text
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
create table if not exists phone_ops (
  op_id       text primary key,
  position    integer not null,
  kind        text not null,
  args        text not null,
  state       text not null,
  result      text,
  error       text,
  created_at  text not null,
  started_at  text,
  finished_at text,
  resolved_at text,
  budget_sec  real,
  priority    integer not null default 1  -- PRIORITY_NORMAL, kept in sync by hand (plain SQL text)
);
create index if not exists idx_phone_ops_state on phone_ops(state, position);
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
create table if not exists media_seen (
  source_rel    text primary key,
  media_id      text not null,
  size          integer not null,
  mtime         integer not null,
  seen_at       text not null,
  queue_id      text,
  kind          text,
  source_dir    text,
  attached_at   text,
  attached_inbound_id text,
  attached_phone text,
  link_strength text,
  link_reason   text,
  legacy        integer not null default 0
);
create table if not exists media_file (
  media_id      text primary key,
  sha256        text not null,
  local_path    text not null,
  size          integer not null,
  mime_type     text,
  filename      text,
  pulled_at     text not null
);
create table if not exists media_link (
  inbound_id    text primary key,
  media_id      text not null,
  filename      text,
  linked_at     text not null
);
create index if not exists idx_media_link_media on media_link(media_id);
"""


def utc(now):
    """RFC3339 UTC, milliseconds, absolute. Never a duration: a skewed clock must not move a cap."""
    if now.tzinfo is None:
        raise ValueError("naive datetime reached the ledger")
    return now.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def age_sec(stamp, now):
    """Seconds between the ledger's own RFC3339 spelling of a moment and ``now``."""
    return (now - datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))).total_seconds()


def _audit_row(row):
    """One audit row as JSON-ready data: the flag as a bool, the detail as the object it was."""
    return {**dict(row), "verified": bool(row["verified"]), "detail": json.loads(row["detail"])}


def thread_tag(phone):
    """A phone number that is safe in a log line. PII: the number itself never appears in one."""
    return hashlib.sha256(str(phone).encode("utf-8")).hexdigest()[:12]


#: One queue entry per PULL INSTANCE, not per unique content (TASK-360 round 5, requirement 2): two
#: people sending byte-identical files are two ``media_seen`` rows sharing one ``media_file`` row
#: for the bytes, so this has to be keyed on the handset path, never on the content id -- a content
#: id is exactly what the two rows have in common and would collapse them back into one entry, the
#: silent-loss bug this round fixes (the second sender's file used to vanish from the queue and
#: health alike the moment the first one's was linked).
_QUEUE_ID_PREFIX = "wab.q."
_QUEUE_ID_CHARS = 20


def _queue_id(source_rel):
    return _QUEUE_ID_PREFIX + hashlib.sha256(str(source_rel).encode("utf-8")).hexdigest()[:_QUEUE_ID_CHARS]


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
    reconcile_attempts: int
    escalated_at: str | None


class Ledger:
    def __init__(self, path):
        # check_same_thread=False: ThreadingHTTPServer answers health and outbox while a send is
        # in flight. Every write goes through _lock, so there is one writer at a time.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("pragma journal_mode=wal")
        self._db.executescript(SCHEMA)
        self._migrate_media_seen()
        self._migrate_phone_ops()
        self._migrate_outbound_reconcile()
        self._db.commit()
        self._lock = threading.RLock()
        self._recover_stuck_ops(datetime.now(timezone.utc))

    def _migrate_media_seen(self):
        """TASK-360 round 5 (decision-9, 2026-09-22). ``media_seen`` already holds rows on the mini
        that predate the queue columns entirely (six from August) -- ``create table if not exists``
        cannot add a column to a table that already exists, so they need their own explicit step,
        guarded by checking what is already there rather than assuming a bare install. This is also
        where those six rows become reachable: their ``media_file`` rows carry NULL kind and NULL
        mtime (an earlier round's ALTER added those columns after the rows existed, and never
        backfilled them), which is exactly why the attach path used to refuse them with
        ``no_pending_media`` -- there was no kind to match against. ``media_seen``, unlike
        ``media_file``, has held the real handset mtime since the very first pull (it was never an
        ALTER-added column here), and a file's kind is fully recoverable from its own handset path
        (``bridge/media.py::kind_for_path`` -- exact, not a guess) -- so both are backfilled from
        facts already on the row, not invented.
        """
        # A real, named migrations table (TASK-360 round 7, second attempt -- the first attempt
        # tried to infer "has this database ever seen the legacy column before" from the column's
        # own presence, which broke the moment this code itself was deployed twice in one day: the
        # first deploy's ALTER already added the column with its own DEFAULT applied to every
        # existing row, erasing the very distinction the second deploy needed to read. A migration
        # that must run exactly once needs its own durable marker, not an inference from unrelated
        # schema state that other code changes can shift out from under it.
        self._db.execute(
            "create table if not exists schema_migrations (name text primary key, applied_at text not null)")
        cols = {r["name"] for r in self._db.execute("pragma table_info(media_seen)").fetchall()}
        for name, decl in (("queue_id", "text"), ("kind", "text"), ("source_dir", "text"),
                          ("attached_at", "text"), ("attached_inbound_id", "text"),
                          ("attached_phone", "text"), ("link_strength", "text"),
                          ("link_reason", "text"),
                          ("legacy", "integer not null default 0")):
            if name not in cols:
                self._db.execute(f"alter table media_seen add column {name} {decl}")
        # After the columns are guaranteed to exist, never before -- an index on a column a
        # pre-round-5 table does not have yet is the same "alter after the rows existed" trap the
        # six live files are already in (this docstring's own opening paragraph).
        self._db.execute("create index if not exists idx_media_seen_queue on media_seen(queue_id)")
        # TASK-360 round 6 fix (Ivan, 2026-09-22, acceptance-run blocker): these six rows have no
        # notification, no candidate, no time context left -- whatever inbound message they once
        # belonged to is long past. Round 6's own brief asked for them to be reachable "by a human";
        # leaving them in auto_match_media()'s pool instead let them win as a false "sole candidate"
        # against a live person's fresh file the moment one of the same kind arrived, permanently
        # stranding the real file (queue-full-of-one). ``legacy=1`` keeps them in
        # ``unresolved_media()`` (attach_media still reaches them) and OUT of ``media_queue(auto_only=True)``
        # -- marked here, once, for exactly the rows this backfill branch already identifies as
        # pre-round-5 (queue_id was null), never re-computed and never applied to a row pulled by a
        # live MediaWatcher cycle.
        rows = self._db.execute(
            "select source_rel, media_id, mtime from media_seen where queue_id is null").fetchall()
        for row in rows:
            self._db.execute(
                "update media_seen set queue_id=?, kind=?, source_dir=?, legacy=1 where source_rel=?",
                (_queue_id(row["source_rel"]), MD.kind_for_path(row["source_rel"]),
                 MD.source_dir_for_path(row["source_rel"]), row["source_rel"]))
        # One-time reconciliation for a database that already lived through the OTHER migration
        # (round 6, same day, shipped before the legacy column existed): on such a database every
        # row above this line already has queue_id set, so the branch just above finds none of them
        # -- the six files are still exactly as unattributable as ever, just no longer reachable by
        # the WHERE clause that used to find them. Guarded by ``schema_migrations`` (above), not by
        # column presence: the moment this specific reconciliation has EVER run on THIS database is
        # the one thing safe to check, because nothing pulled by a live MediaWatcher cycle had a
        # chance to exist yet the first time it runs (round 6 and round 7 shipped hours apart with no
        # live traffic recorded in between -- this mini's own health counters confirmed it:
        # pulled_total was 0 going into this deploy) -- so every row still sitting unattached at that
        # moment IS one of the pre-round-5 six. Runs exactly once per database, ever, regardless of
        # how many more times this file gets redeployed today: any row inserted after this migration
        # already carries its own explicit legacy=0.
        migration_name = "task131_round7_legacy_backfill"
        if not self._db.execute("select 1 from schema_migrations where name=?",
                                (migration_name,)).fetchone():
            self._db.execute("update media_seen set legacy=1 where attached_at is null and legacy=0")
            self._db.execute(
                "insert into schema_migrations(name, applied_at) values(?,?)",
                (migration_name, datetime.now(timezone.utc).isoformat(timespec="milliseconds")))
        # A pre-round-5 automatic link (rare in the acceptance run, but not assumed impossible):
        # carry it over as an attachment rather than stranding it back in the queue. Only when
        # exactly one still-unattached media_seen row shares that media_id -- an ambiguous case (two
        # pulls of identical bytes, one old link) is left alone rather than guessed, the same
        # discipline the deleted matcher itself used to follow.
        for link in self._db.execute(
                "select l.inbound_id, l.media_id, l.linked_at, i.payload from media_link l "
                "left join inbound i on i.inbound_key = l.inbound_id").fetchall():
            candidates = self._db.execute(
                "select source_rel from media_seen where media_id=? and attached_at is null",
                (link["media_id"],)).fetchall()
            if len(candidates) != 1:
                continue
            phone = None
            if link["payload"]:
                phone = json.loads(link["payload"]).get("from")
            self._db.execute(
                "update media_seen set attached_at=?, attached_inbound_id=?, attached_phone=? "
                "where source_rel=?",
                (link["linked_at"], link["inbound_id"], phone, candidates[0]["source_rel"]))

    def _migrate_phone_ops(self):
        """TASK-230, Ivan 2026-09-23: ``resolved_at`` on ``phone_ops`` did not exist when TASK-227
        shipped, so the mini's live ``phone_ops`` table predates it -- same ``create table if not
        exists`` limit ``_migrate_media_seen`` explains, same fix (an ALTER guarded by what the
        table already has, not an inference). TASK-243 adds ``budget_sec`` the same way: a row
        enqueued before this shipped has none, and ``claim_next_op`` treats that as unbounded --
        never a backfilled guess at what the caller would have said."""
        self._db.execute(
            "create table if not exists schema_migrations (name text primary key, applied_at text not null)")
        cols = {r["name"] for r in self._db.execute("pragma table_info(phone_ops)").fetchall()}
        if "resolved_at" not in cols:
            self._db.execute("alter table phone_ops add column resolved_at text")
        if "budget_sec" not in cols:
            self._db.execute("alter table phone_ops add column budget_sec real")
        if "priority" not in cols:
            # Ivan, 2026-09-24: a flat FIFO let a background chat-list read/reconcile queue ahead
            # of a reply to someone who had just written in, with no way to tell them apart. A row
            # enqueued before this shipped gets PRIORITY_NORMAL (the same tier a caller with no
            # opinion gets going forward) -- never inferred as urgent or low from nothing.
            self._db.execute(f"alter table phone_ops add column priority integer not null default {PRIORITY_NORMAL}")
        self._db.execute(
            "create index if not exists idx_phone_ops_state_priority_position "
            "on phone_ops(state, priority, position)")

    def _migrate_outbound_reconcile(self):
        """Ivan, 2026-09-24, same postmortem as the priority migration above: two client_msg_ids
        sat ATTEMPTING/UNCONFIRMED for ~14 hours because ``reconcile``'s ``indeterminate`` verdict
        never marks anything resolved, so ``UnresolvedSendWatcher`` re-enqueued a real phone scan
        for them every 60s forever -- ~66% of an hour's lock time on two rows nothing could ever
        settle. ``reconcile_attempts`` counts scans (not ``outbound.attempts``, which already means
        something else: how many times this key was POSTed). ``escalated_at`` is set once the
        count reaches the limit; ``unresolved()`` excludes it from then on, so the watcher stops
        spending phone time on a row nothing is going to resolve on its own."""
        cols = {r["name"] for r in self._db.execute("pragma table_info(outbound)").fetchall()}
        if "reconcile_attempts" not in cols:
            self._db.execute("alter table outbound add column reconcile_attempts integer not null default 0")
        if "escalated_at" not in cols:
            self._db.execute("alter table outbound add column escalated_at text")

    def _recover_stuck_ops(self, now):
        """TASK-232: ``claim_next_op`` is the only writer of ``OP_RUNNING`` and nothing else in
        this package ever reads it back -- a systemd restart (``Restart=always``, TASK-227's own
        service unit) or a mini reboot landing inside ``dispatcher.run_one()`` left that row
        ``running`` forever: never re-claimed (``claim_next_op`` only selects ``OP_QUEUED``),
        never swept (``sweep`` only touches rows with ``finished_at`` set, and only
        ``mark_op_done``/``mark_op_failed`` set it), and every debug artefact named after it held
        by ``retention.classify_op_artifact`` as "op still running", forever. There is exactly one
        dispatcher thread per process, so a row still ``running`` when a fresh ``Ledger`` opens
        belongs, by construction, to a process that no longer exists. Fail it with a reason no
        real refusal ever raises, so it becomes terminal, reviewable, and swept like any other
        failed op once past retention -- this never touches the outbound row a ``send`` may have
        left ``attempting``; only a reconcile resolves that (Rule 4 above)."""
        stuck = [r["op_id"] for r in self._db.execute(
            "select op_id from phone_ops where state = ?", (OP_RUNNING,)).fetchall()]
        for op_id in stuck:
            self.mark_op_failed(op_id, {"ok": False, "error": {
                "code": "restarted_while_running",
                "message": "the executor restarted while this op was in flight",
                "http_status": 504, "retryable": False}}, now)

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

        TASK-236: Rule 3's own justification is "a regenerated reply must never overwrite a
        delivered one" -- that is a claim about SENT/ATTEMPTING/UNCONFIRMED, where a first body may
        already be on the phone or headed there. A RESENDABLE entry (NOT_ATTEMPTED, ABSENT) is the
        opposite case by construction: the old body demonstrably never reached the phone, so there
        is nothing to protect. Refusing there anyway turned a body-sensitive key plus a
        non-deterministic generator (LB.turn is a live Sonnet call) into a permanent wedge: the
        catch-up loop regenerates, mismatches, and never delivers, forever. So a RESENDABLE
        mismatch still gets the audit row -- the disagreement is real and worth keeping -- but is
        let through to attempt the new body instead of refused.
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
            if entry.state not in RESENDABLE:
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
        is correct, because from the outside it is.

        TASK-236: the ON CONFLICT clause also refreshes body_sha256/body_len. A RESENDABLE key can
        now proceed with a new body (see classify()); without this, the row would keep the stale
        first body's hash while the new body is what actually gets typed, so the NEXT replay of
        that new body would classify as a mismatch against a hash nobody ever sent.
        """
        stamp = utc(now)
        with self._lock:
            self._db.execute(
                """insert into outbound(client_msg_id, to_phone, thread_tag, kind, body_sha256,
                                        body_len, state, attempts, created_at, attempted_at)
                   values(?,?,?,?,?,?,?,1,?,?)
                   on conflict(client_msg_id) do update set
                     state=excluded.state, attempts=outbound.attempts+1,
                     attempted_at=excluded.attempted_at, resolved_at=null, detail=null,
                     tick=null, tick_state=null, bubble_clock=null,
                     body_sha256=excluded.body_sha256, body_len=excluded.body_len""",
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

    def unresolved(self, *, include_escalated=False):
        """-> entries a reconcile has to answer for, oldest first.

        Excludes ``escalated_at is not null`` by default (TASK-296-adjacent, Ivan 2026-09-24): a row
        this many times ``indeterminate`` is not going to resolve itself, and re-scanning it forever
        is what starved everything else behind it on 2026-09-23/24. Once escalated it is a human's
        question, not this watcher's -- see ``bump_reconcile_attempts``/``escalate`` below.
        ``UnresolvedSendWatcher`` relies on that default to stop re-enqueueing an escalated row.

        ``include_escalated=True`` is for an operator asking "what's stuck, and why" (``Executor.
        unresolved_sends``, behind ``tools/wa_bridge.py unresolved-list``) -- escalated rows should
        stay visible there, just marked, since Ivan asked for the *possibility* of finding out, not
        for them to vanish once given up on.
        """
        query = "select * from outbound where state in (?,?)"
        if not include_escalated:
            query += " and escalated_at is null"
        rows = self._db.execute(query + " order by attempted_at",
                                (ATTEMPTING, UNCONFIRMED)).fetchall()
        return [Entry(**dict(r)) for r in rows]

    def bump_reconcile_attempts(self, client_msg_id, now):
        """One more scan spent on this key with no resolution. -> the new count."""
        with self._lock:
            self._db.execute(
                "update outbound set reconcile_attempts = reconcile_attempts + 1 where client_msg_id=?",
                (client_msg_id,))
            self._db.commit()
            return self._db.execute(
                "select reconcile_attempts from outbound where client_msg_id=?",
                (client_msg_id,)).fetchone()["reconcile_attempts"]

    def escalate(self, client_msg_id, now):
        """Stop retrying this key: it has hit the reconcile attempt limit. -> nothing; the caller
        already knows the count it escalated at. Journalled so a human reading the ledger later can
        see when and why this row stopped being scanned, not just that it did."""
        with self._lock:
            self._db.execute("update outbound set escalated_at=? where client_msg_id=?",
                             (utc(now), client_msg_id))
            self._db.commit()
        self.note(now, "reconcile_escalated", client_msg_id)

    def escalated_count(self):
        """-> how many outbound rows have given up resolving on their own -- the ``/v1/health``
        visibility Ivan asked for: a growing number here is exactly what should have been visible
        during the ~14 hours two keys spent stuck on 2026-09-23/24."""
        return self._db.execute(
            "select count(*) c from outbound where escalated_at is not null").fetchone()["c"]

    # --- counting, for the governor --------------------------------------------------------------
    def count_spent(self, start, end, *, phone=None, kind=None, exclude_phones=()):
        """How many sends this handset spent in [start, end). Counts attempting and unconfirmed.

        ``exclude_phones`` leaves the operators' own test numbers out of the budget a real candidate
        is paced against (Ivan, 2026-09-24). Without it a night of testing eats the day's first-touch
        allowance and the next morning's real campaign is refused -- observed live that evening,
        first_touches_today 10/10 with every one of them sent to the two test handsets."""
        sql = ["select count(*) c from outbound where attempted_at >= ? and attempted_at < ?",
               "and state in (%s)" % ",".join("?" * len(SPENT))]
        args = [utc(start), utc(end), *sorted(SPENT)]
        if phone is not None:
            sql.append("and to_phone = ?")
            args.append(phone)
        if kind is not None:
            sql.append("and kind = ?")
            args.append(kind)
        excluded = [p for p in exclude_phones if p]
        if excluded:
            sql.append("and to_phone not in (%s)" % ",".join("?" * len(excluded)))
            args.extend(excluded)
        return self._db.execute(" ".join(sql), args).fetchone()["c"]

    def event_count(self, event, start, end):
        """How many journal rows named ``event`` landed in [start, end) -- same bounded-window
        shape as count_spent above, but over the append-only journal instead of outbound (TASK-264).
        An instance counter fed by the same event (Executor.dirty_recovered,
        InboundWatcher.idle_dirty_recovered/errors) reads 0 after a crash-restart; the journal row
        each of those events also writes does not."""
        row = self._db.execute(
            "select count(*) c from journal where event = ? and at >= ? and at < ?",
            (event, utc(start), utc(end))).fetchone()
        return row["c"]

    def last_spent_at(self, *, phone=None, kind=None, exclude_phones=()):
        """-> RFC3339 string of the most recent attempt, or None. ``exclude_phones`` as in
        count_spent: the global first-touch GAP is about strangers seeing one number wake up, and a
        send to an operator's own handset is not that -- without this, one test message parks the
        next real first touch for four minutes."""
        sql = ["select max(attempted_at) m from outbound where state in (%s)" % ",".join("?" * len(SPENT))]
        args = [*sorted(SPENT)]
        if phone is not None:
            sql.append("and to_phone = ?")
            args.append(phone)
        if kind is not None:
            sql.append("and kind = ?")
            args.append(kind)
        excluded = [p for p in exclude_phones if p]
        if excluded:
            sql.append("and to_phone not in (%s)" % ",".join("?" * len(excluded)))
            args.extend(excluded)
        return self._db.execute(" ".join(sql), args).fetchone()["m"]

    def queue_counts(self):
        rows = self._db.execute("select state, count(*) c from outbound group by state").fetchall()
        return {r["state"]: r["c"] for r in rows}

    # --- the phone-op queue (TASK-227): every HTTP route that touches the phone enqueues here and
    # a single dispatcher thread drains it strictly in ``position`` order. This is what makes two
    # concurrent HTTP requests execute one-after-another instead of racing a bare flock. -----------
    def enqueue_op(self, op_id, kind, args, now, budget_sec=None, priority=None):
        """One row, ``queued``, at the back of ITS TIER. -> nothing; ``op_id`` is already minted by
        the caller (``bridge/server.py``), not here -- the caller needs it before this call returns
        to answer the HTTP request with it.

        ``budget_sec`` (TASK-243) is the CALLER's own already-computed patience for this op --
        ``app/wa/bridge.py::Client`` sends it as the ``X-Wa-Op-Budget-Sec`` header, the exact
        number it is about to poll ``GET /v1/ops/<id>`` against. Never invented here: ``None``
        (no header, an older client, a direct caller) means ``claim_next_op`` treats the row as
        unbounded, exactly its behaviour before this column existed.

        ``priority`` (TASK-296-adjacent): ``None`` maps to ``PRIORITY_NORMAL`` -- a caller with no
        opinion gets the tier every op got before this column existed, not a guess at urgency."""
        with self._lock:
            position = self._db.execute(
                "select coalesce(max(position), 0) + 1 as n from phone_ops").fetchone()["n"]
            self._db.execute(
                "insert into phone_ops(op_id, position, kind, args, state, created_at, budget_sec, priority) "
                "values (?,?,?,?,?,?,?,?)",
                (op_id, position, kind, json.dumps(args, sort_keys=True), OP_QUEUED, utc(now),
                 None if budget_sec is None else float(budget_sec),
                 PRIORITY_NORMAL if priority is None else int(priority)))
            self._db.commit()

    def claim_next_op(self):
        """-> the highest-priority, oldest-within-that-tier still-``queued`` row that is not past
        its own budget, marked ``running``, or None once nothing claimable is left. Single-writer
        via ``self._lock`` same as everything else here -- there is only ever one dispatcher thread
        calling this, but the guard costs nothing and keeps that an invariant this method enforces
        rather than one the caller has to remember.

        TASK-296-adjacent: ``order by priority, position`` -- a lower-numbered tier (URGENT) always
        claims before a higher-numbered one (NORMAL, LOW) regardless of arrival order, and FIFO is
        preserved WITHIN a tier exactly as it always was. A LOW background read queued a minute ago
        does not get to sit in front of a URGENT reply queued this instant.

        TASK-243: a row whose ``budget_sec`` has elapsed is not claimed -- the caller that queued
        it already stopped listening (``app/wa/bridge.py::Client._await_op`` raised
        ``answer_timeout`` and, on a live client, also called ``cancel_op`` below, but a lost
        cancel or an older caller with no cancel route must not leave this stuck open). It is
        expired instead, the same ``mark_op_failed``-shaped write ``_recover_stuck_ops`` already
        uses for a different terminal reason, and the scan moves on to the row behind it -- one
        stale ticket must never block a fresh one sitting right behind it in the queue. A row with
        no ``budget_sec`` (no header, an older client) is never expired: unbounded stays unbounded.
        """
        with self._lock:
            now_dt = datetime.now(timezone.utc)
            while True:
                row = self._db.execute(
                    "select * from phone_ops where state = ? order by priority, position limit 1",
                    (OP_QUEUED,)).fetchone()
                if row is None:
                    return None
                budget = row["budget_sec"]
                if budget is not None and age_sec(row["created_at"], now_dt) > budget:
                    self._expire_op(row, now_dt, budget)
                    continue
                now = utc(now_dt)
                self._db.execute(
                    "update phone_ops set state = ?, started_at = ? where op_id = ? and state = ?",
                    (OP_RUNNING, now, row["op_id"], OP_QUEUED))
                self._db.commit()
                return dict(row, state=OP_RUNNING, started_at=now, args=json.loads(row["args"]))

    def _expire_op(self, row, now_dt, budget):
        """Caller already holds ``self._lock`` (an ``RLock``, so this nested acquire is free)."""
        age = age_sec(row["created_at"], now_dt)
        self._db.execute(
            "update phone_ops set state = ?, error = ?, finished_at = ? where op_id = ? and state = ?",
            (OP_FAILED, json.dumps({"ok": False, "error": {
                "code": "op_expired",
                "message": f"queued {age:.0f}s, past the {budget:.0f}s budget the caller queued it "
                          f"under -- nobody is still waiting on it",
                "http_status": 504, "retryable": False}}, sort_keys=True),
             utc(now_dt), row["op_id"], OP_QUEUED))
        self._db.commit()
        self.note(now_dt, "op_expired", None, op_id=row["op_id"], age_sec=age, budget_sec=budget)

    def cancel_op(self, op_id, now):
        """The caller gave up (TASK-243: ``app/wa/bridge.py::Client._await_op``'s own timeout). ->
        True when this flipped a still-``queued`` row to a terminal cancellation, False when the
        row was already ``running`` or terminal (or does not exist) -- a no-op, never an
        interruption of a live adb call: only a row still in ``OP_QUEUED`` can lose the race here,
        by the same ``and state = ?`` guard ``claim_next_op`` itself relies on."""
        with self._lock:
            changed = self._db.execute(
                "update phone_ops set state = ?, error = ?, finished_at = ? "
                "where op_id = ? and state = ?",
                (OP_FAILED, json.dumps({"ok": False, "error": {
                    "code": "op_cancelled",
                    "message": "the caller gave up waiting for this op and cancelled it before it "
                              "was claimed",
                    "http_status": 504, "retryable": False}}, sort_keys=True),
                 utc(now), op_id, OP_QUEUED)).rowcount
            self._db.commit()
        if changed:
            self.note(now, "op_cancelled", None, op_id=op_id)
        return bool(changed)

    def mark_op_done(self, op_id, result, now):
        with self._lock:
            self._db.execute(
                "update phone_ops set state = ?, result = ?, finished_at = ? where op_id = ?",
                (OP_DONE, json.dumps(result, sort_keys=True), utc(now), op_id))
            self._db.commit()

    def mark_op_failed(self, op_id, error, now):
        with self._lock:
            self._db.execute(
                "update phone_ops set state = ?, error = ?, finished_at = ? where op_id = ?",
                (OP_FAILED, json.dumps(error, sort_keys=True), utc(now), op_id))
            self._db.commit()

    def op_status(self, op_id):
        """-> the row as a plain dict with ``args``/``result``/``error`` decoded back from JSON, or
        None when this ``op_id`` was never enqueued on this ledger."""
        row = self._db.execute("select * from phone_ops where op_id = ?", (op_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["args"] = json.loads(out["args"])
        out["result"] = json.loads(out["result"]) if out["result"] is not None else None
        out["error"] = json.loads(out["error"]) if out["error"] is not None else None
        return out

    def phone_ops_queue_counts(self):
        """-> {"queued": count, "oldest_queued_at": timestamp or None, "by_priority": {...}} over
        ``phone_ops`` -- ``queue_counts()`` above only ever grouped ``outbound``, so a dispatcher
        silently falling behind (TASK-260) moved nothing in that number for anyone to see. Same
        shape as ``inbound_backlog()`` below: a raw timestamp, not an age -- the caller has a
        clock, this does not.

        ``by_priority`` (TASK-296-adjacent, 2026-09-24) breaks the same total down by tier -- the
        exact visibility tonight's postmortem had to reconstruct by hand from the journal, built in
        this time so it never has to be again. Existing callers (``bridge/broadcast.py``'s
        TASK-268 defer check, ``bridge/dispatcher.py``'s own docstring) only ever read ``queued``,
        unchanged."""
        row = self._db.execute(
            "select count(*) c, min(created_at) oldest from phone_ops where state = ?",
            (OP_QUEUED,)).fetchone()
        by_priority = {"urgent": 0, "normal": 0, "low": 0}
        tier_name = {PRIORITY_URGENT: "urgent", PRIORITY_NORMAL: "normal", PRIORITY_LOW: "low"}
        for r in self._db.execute(
                "select priority, count(*) c from phone_ops where state = ? group by priority",
                (OP_QUEUED,)).fetchall():
            by_priority[tier_name.get(r["priority"], str(r["priority"]))] = r["c"]
        return {"queued": row["c"], "oldest_queued_at": row["oldest"], "by_priority": by_priority}

    def phone_ops_active(self):
        """-> True when any phone_ops row is still ``queued`` or ``running`` (TASK-315 AC#9): the
        guard ``bridge/doctor.py::PhoneDoctor`` checks BEFORE it ever tries ``huawei01.lock`` --
        cheaper than a lock attempt, and it catches a row that is merely queued (the dispatcher has
        not claimed it yet, so the lock itself is still free right now) the same way
        ``bridge/watcher.py``'s ``BroadcastRunner``/``IdentityWatcher`` already step aside on
        ``phone_ops_queue_counts()`` before their own long-patience acquire -- the doctor must never
        race a dispatched op for the lock the instant it frees up mid-sequence, only ever act when
        the queue is genuinely empty AND nothing is running."""
        row = self._db.execute(
            "select count(*) c from phone_ops where state in (?, ?)",
            (OP_QUEUED, OP_RUNNING)).fetchone()
        return row["c"] > 0

    def oldest_running_op_age_sec(self, now):
        """-> seconds since the oldest still-``running`` phone_ops row's ``started_at``, or None
        when nothing is running (TASK-315 review point 9: PhoneDoctor's health must show
        ``blocked_by_stuck_op`` once a row has been running for more than 10 minutes -- see
        ``claim_next_op``'s own docstring on ``started_at`` never being cleared by a crash, which
        is exactly the case this age is meant to surface to a human)."""
        row = self._db.execute(
            "select min(started_at) started from phone_ops where state = ?", (OP_RUNNING,)).fetchone()
        if row is None or row["started"] is None:
            return None
        return age_sec(row["started"], now)

    # --- retention review (TASK-230, Ivan 2026-09-23): a failed op with no outbound entry to read a
    # verdict off (read_thread, send_photos/gallery/document -- none of these mint a client_msg_id)
    # has no automatic way to become safe to delete. This is the manual
    # escape hatch: a human looks at the op's own debug-capture artifacts (op_id names them) and
    # marks it resolved once satisfied nothing is owed. Idempotent -- resolving twice, or resolving
    # an op that turned out fine on its own, is harmless.
    def resolve_op(self, op_id, now):
        with self._lock:
            self._db.execute("update phone_ops set resolved_at = ? where op_id = ?",
                             (utc(now), op_id))
            self._db.commit()
        self.note(now, "op_resolved", None, op_id=op_id)

    def escalation_shot_index(self):
        """-> {shot_path: client_msg_id} for every escalation shot this ledger has journalled
        (bridge/executor.py::_escalate). The retention sweep's only way to tell which send an
        old-style escalation shot (filename carries no op_id) belongs to, so it can read that
        send's own outbound.state instead of guessing from the shot alone."""
        rows = self._db.execute(
            "select client_msg_id, detail from journal where event = 'escalation_shot'").fetchall()
        out = {}
        for row in rows:
            detail = json.loads(row["detail"]) if row["detail"] else {}
            path = detail.get("path")
            if path:
                out[path] = row["client_msg_id"]
        return out

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
        """-> events with id > after, oldest first. No default page size: the caller decides, and a
        truncation invented here would silently drop a candidate's message.

        A row a file has since been attached to (a human's ``Executor.attach_media``, or an
        automatic match -- ``Executor.auto_match_media``, TASK-360 round 6, both through
        ``link_media`` below) has ``media_id``/``media_mime_type``/``media_filename`` merged into
        the payload the caller already stored fresh from whatever ``media_link``/``media_file`` say
        right now -- the stored payload itself is never rewritten, so a re-poll of the same window
        still shows the original text too. An automatic match ALSO carries
        ``media_link_strength`` ('strong'|'weak'): the one field ``app/wa/api.py`` reads to keep a
        weakly-attributed document's text from ever reaching the model (bridge/envelope.py mints
        the same field into the webhook payload downstream). A human attach or a legacy link
        (migrated before this column existed) carries no strength at all -- omitted, not "strong",
        so a reader cannot mistake silence for a claim of confidence. Every other row is released
        exactly as stored, immediately: there is nothing left to wait for.
        """
        sql = ("select i.id, i.received_at, i.payload, m.media_id, m.filename as link_filename, "
               "f.mime_type, s.link_strength from inbound i "
               "left join media_link m on m.inbound_id = i.inbound_key "
               "left join media_file f on f.media_id = m.media_id "
               "left join media_seen s on s.attached_inbound_id = i.inbound_key "
               "where i.id > ? order by i.id")
        out = []
        for r in self._db.execute(sql, (int(after),)).fetchall():
            payload = json.loads(r["payload"])
            if r["media_id"]:
                payload = {**payload, "media_id": r["media_id"], "media_mime_type": r["mime_type"],
                          "media_filename": r["link_filename"]}
                if r["link_strength"]:
                    payload["media_link_strength"] = r["link_strength"]
            out.append({"id": r["id"], "received_at": r["received_at"], "payload": payload})
            if limit is not None and len(out) >= int(limit):
                break
        return out

    # --- inbound media: the unresolved queue (TASK-360 round 5) ----------------------------------
    def media_known_paths(self):
        """-> the handset rel-paths already pulled at least once. A file at one of these paths is
        never fetched a second time (colleague's own ``_known_media`` set, made durable here)."""
        return {r[0] for r in self._db.execute("select source_rel from media_seen").fetchall()}

    def record_media(self, *, source_rel, mtime, media_id, sha256, local_path, size, kind,
                     mime_type, filename, now):
        """A file just pulled off the handset -> one queue row, always. -> True when these bytes
        are new to the store.

        Content-addressed for the BYTES only (TASK-360): two handset paths carrying the same bytes
        (a resend, or two different people) share one ``media_file`` row and the second pull does
        not re-store a duplicate copy. The QUEUE is keyed on the handset path instead
        (``media_seen``, one row per ``source_rel``) -- on purpose, and it is the fix for the round
        4 silent-loss bug: two people sending byte-identical files must produce two queue entries,
        and a content id is exactly the thing the two pulls have in common.
        """
        stamp = utc(now)
        queue_id = _queue_id(source_rel)
        with self._lock:
            self._db.execute(
                "insert or ignore into media_seen(source_rel, media_id, size, mtime, seen_at, "
                "queue_id, kind, source_dir) values(?,?,?,?,?,?,?,?)",
                (source_rel, media_id, int(size), int(mtime), stamp, queue_id, kind,
                 MD.source_dir_for_path(source_rel)))
            new = self._db.execute(
                "insert or ignore into media_file(media_id, sha256, local_path, size, mime_type, "
                "filename, pulled_at) values(?,?,?,?,?,?,?)",
                (media_id, sha256, str(local_path), int(size), mime_type, filename, stamp)
            ).rowcount > 0
            self._db.commit()
        self.note(now, "media_pulled", None, media_id=media_id, queue_id=queue_id, new_bytes=new,
                 size=size)
        return new

    def media_file(self, media_id):
        row = self._db.execute("select * from media_file where media_id=?", (media_id,)).fetchone()
        return dict(row) if row else None

    def media_queue(self, *, auto_only=False):
        """-> every pulled file nobody has attached yet, oldest first -- what a human still sees for
        whatever it cannot place (``auto_only=False``, the default: everything, legacy rows
        included -- ``attach_media`` must still reach them by hand). ``content_pull_count`` is how
        many pulls (this row included) share this file's bytes (TASK-360 round 6 ALSO FIX): 1 for an
        ordinary file, >1 when the same content was pulled more than once -- a resend, or two people
        sending one identical template -- made visible here rather than silently folded into
        whichever pull got stored first.

        ``auto_only=True`` is ``bridge/executor.py::Executor.auto_match_media``'s own pool: legacy
        rows (``legacy=1``, see ``_migrate_media_seen``) are excluded, never candidates for automatic
        attribution -- they predate this matcher and carry no notification or candidate context to
        decide against, and letting them compete for a fresh live candidate the moment one of the
        same kind arrives is exactly the acceptance-run bug this excludes."""
        where = "s.attached_at is null" + (" and s.legacy = 0" if auto_only else "")
        rows = self._db.execute(
            "select s.queue_id, s.media_id, s.kind, s.source_dir, s.size, s.mtime, "
            "s.seen_at as pulled_at, s.attached_at, s.legacy, "
            "(select count(*) from media_seen d where d.media_id = s.media_id) as content_pull_count "
            f"from media_seen s where {where} order by s.seen_at").fetchall()
        return [dict(r) for r in rows]

    def media_queue_row(self, queue_id):
        row = self._db.execute("select * from media_seen where queue_id=?", (queue_id,)).fetchone()
        return dict(row) if row else None

    def media_backlog(self, now):
        """-> {"unresolved", "oldest_unresolved_sec", "by_kind", "duplicate_content", "weak_links"}
        for /v1/health -- enough for a human to see the queue is growing, or not, and to audit an
        automatic match (TASK-360 round 6) without opening ``media-list``."""
        rows = self._db.execute(
            "select kind, seen_at from media_seen where attached_at is null").fetchall()
        by_kind, oldest = {}, None
        for r in rows:
            kind = r["kind"] or "unknown"
            by_kind[kind] = by_kind.get(kind, 0) + 1
            age = age_sec(r["seen_at"], now)
            if oldest is None or age > oldest:
                oldest = age
        dup = self._db.execute(
            "select count(*) c from (select media_id from media_seen group by media_id "
            "having count(*) > 1)").fetchone()["c"]
        weak = self._db.execute(
            "select count(*) c from media_seen where link_strength = 'weak'").fetchone()["c"]
        return {"unresolved": len(rows), "oldest_unresolved_sec": oldest, "by_kind": by_kind,
               "duplicate_content": dup, "weak_links": weak}

    def media_related_threads(self, kind, around_epoch, *, window_sec=RELATED_THREAD_WINDOW_SEC):
        """-> sorted thread tags (never a phone number) of inbound messages carrying this file's
        own kind within ``window_sec`` of ``around_epoch`` -- decision SUPPORT for the human running
        ``media-attach``, never a decision: nothing here selects, ranks or attaches anything, and
        every tag it can name is one ``media-attach`` would happily let through even if it were not
        on this list. An operator who already knows a candidate's own number can compute that
        number's own tag (``thread_tag``) and check it is here; nobody else learns anything from it.
        Read and filtered in Python, not SQL: inbound volume on this rail is candidates, not rows at
        any scale that would matter."""
        if not around_epoch:
            return []
        tags = set()
        for r in self._db.execute("select payload from inbound").fetchall():
            payload = json.loads(r["payload"])
            if payload.get("media_kind") != kind:
                continue
            t = (payload.get("time_ms") or 0) / 1000.0
            phone = payload.get("from")
            if t and phone and abs(t - around_epoch) <= window_sec:
                tags.add(thread_tag(phone))
        return sorted(tags)

    def unlinked_media_candidates(self, kind):
        """-> [{"phone", "inbound_id", "time_ms"}] for inbound rows of this media kind that no file
        has been linked to yet -- ``bridge/executor.py::Executor.auto_match_media``'s candidate pool
        (TASK-360 round 6). Never the decision itself, only the pool ``bridge/identity.py::decide``
        picks from. Read and filtered in Python, like ``media_related_threads`` above: inbound
        volume on this rail is candidates, not rows at any scale that would matter."""
        rows = self._db.execute(
            "select i.inbound_key, i.payload from inbound i "
            "left join media_link m on m.inbound_id = i.inbound_key "
            "where m.inbound_id is null").fetchall()
        out = []
        for r in rows:
            payload = json.loads(r["payload"])
            phone = payload.get("from")
            if payload.get("media_kind") == kind and phone:
                out.append({"phone": phone, "inbound_id": r["inbound_key"],
                           "time_ms": payload.get("time_ms") or 0})
        return out

    def link_media(self, inbound_id, media_id, *, filename, now):
        """Tie one pulled file to one inbound message. -> True when this created the link, False
        when that message already had one (idempotent; never a silent overwrite of an existing link
        with a different file). The one place a media_id reaches an inbound row -- both the human
        escape hatch (``attach_media`` below) and, before round 5 removed it, the automatic linker
        went through this exact call, so ``pull_inbound``'s merge and everything past it is the same
        either way."""
        with self._lock:
            try:
                self._db.execute(
                    "insert into media_link(inbound_id, media_id, filename, linked_at) "
                    "values(?,?,?,?)", (inbound_id, media_id, filename, utc(now)))
            except sqlite3.IntegrityError:
                return False
            self._db.commit()
        self.note(now, "media_linked", None, media_id=media_id)
        return True

    def attach_media(self, queue_id, phone, *, now):
        """THE human escape hatch (TASK-360 round 5, decision-9 2026-09-22): the one way a file
        leaves the queue. -> the inbound_key minted for it. Raises ``KeyError`` for an unknown
        ``queue_id``, ``ValueError`` when it is already attached -- ``Executor.attach_media`` turns
        both into the wire's own refusal codes.

        There is never a real notification behind the event this mints: the file may have arrived
        long after its own placeholder message was drained and swept off this ledger, or (the files
        already on the live rail before this round) before this ledger ever recorded one for it --
        so a fresh event is minted every time rather than trying to find and patch an old one. One
        code path, whether or not a placeholder still exists. It goes through ``link_media`` above
        -- THE SAME CALL an automatic link used to make -- so ``pull_inbound``'s merge, and
        everything past it (the card, classification, transcription), is unchanged; the only thing
        round 5 removed is WHO decides the phone. A human does, always, by naming it.
        """
        row = self._db.execute("select * from media_seen where queue_id=?", (queue_id,)).fetchone()
        if row is None:
            raise KeyError(queue_id)
        if row["attached_at"] is not None:
            raise ValueError(f"{queue_id} is already attached")
        media = self.media_file(row["media_id"])
        stamp = utc(now)
        inbound_key = ATTACH_INBOUND_PREFIX + hashlib.sha256(
            queue_id.encode("utf-8")).hexdigest()[:32]
        payload = {"envelope": "wa_bridge.inbound.v1", "inbound_id": inbound_key, "from": phone,
                  "title": phone, "text": ATTACH_PLACEHOLDER_TEXT.get(row["kind"], ""),
                  "local_date": stamp[:10], "clock": stamp[11:16],
                  "time_ms": int(now.timestamp() * 1000), "media_kind": row["kind"],
                  "source": "attached", "occurrence": 0}
        self.append_inbound(inbound_key, payload, now)
        self.link_media(inbound_key, row["media_id"],
                        filename=media["filename"] if media else None, now=now)
        with self._lock:
            self._db.execute(
                "update media_seen set attached_at=?, attached_inbound_id=?, attached_phone=?, "
                "link_strength=?, link_reason=? where queue_id=?",
                (stamp, inbound_key, phone, "human", "human_attach", queue_id))
            self._db.commit()
        return inbound_key

    def link_media_auto(self, queue_id, inbound_id, phone, *, strength, reason, now):
        """THE automatic path (TASK-360 round 6, Ivan's ruling 2026-09-22, supersedes decision-9):
        tie a queued file to an EXISTING inbound row -- the real notification-shade message
        ``bridge/identity.py::decide`` picked a candidate for -- unlike ``attach_media`` above,
        which mints a placeholder because a human names only a phone, never a specific message.

        -> True when this created the link, False when the inbound row already had one (idempotent:
        a re-run of the matcher over the same still-unattached queue row never double-attaches).
        Raises ``KeyError`` for an unknown ``queue_id``, ``ValueError`` when it is already attached
        -- same two refusals as the human path, same reason: the caller (``Executor.auto_match_media``)
        already filtered to unattached rows, so either means a race with another attach.

        ``strength``/``reason`` land on the row (audit trail per Ivan's ruling: a weak pick must be
        visible) and, through ``pull_inbound``'s merge, in the payload itself -- the one field
        ``app/wa/api.py`` reads to keep a weakly-attributed document's text from the model.

        TASK-233: ``inbound_id`` is always linked (below), so it drops out of
        ``unlinked_media_candidates`` either way -- a stale placeholder must never sit around to be
        wrongly matched against a later, unrelated file of the same kind. But that link alone only
        reaches ``pull_inbound``'s merge while the relay has not yet acked the row; once acked, its
        id is behind the relay's cursor for good and the link is invisible forever, not delayed --
        the loss this task is about. So when ``inbound_id`` is already acked, a SECOND, fresh row is
        minted (``attach_media``'s own pattern: a new key past whatever the cursor now is) and that
        one carries the real link and the payload's ``attached_inbound_id``; the already-acked
        original keeps its link purely to vacate the candidate pool, not to deliver anything -- a
        stale cursor re-reading it redelivers old text under its own already-consumed wamid, which
        the webhook dedupes for free, exactly as any redelivery on this rail does.
        """
        row = self._db.execute("select * from media_seen where queue_id=?", (queue_id,)).fetchone()
        if row is None:
            raise KeyError(queue_id)
        if row["attached_at"] is not None:
            raise ValueError(f"{queue_id} is already attached")
        media = self.media_file(row["media_id"])
        filename = media["filename"] if media else None
        created = self.link_media(inbound_id, row["media_id"], filename=filename, now=now)
        if not created:
            return False
        stamp = utc(now)
        delivered_id = inbound_id
        acked = self._db.execute(
            "select acked_at from inbound where inbound_key=?", (inbound_id,)).fetchone()
        if acked and acked["acked_at"] is not None:
            delivered_id = AUTO_LINK_INBOUND_PREFIX + hashlib.sha256(
                queue_id.encode("utf-8")).hexdigest()[:32]
            payload = {"envelope": "wa_bridge.inbound.v1", "inbound_id": delivered_id, "from": phone,
                      "title": phone, "text": ATTACH_PLACEHOLDER_TEXT.get(row["kind"], ""),
                      "local_date": stamp[:10], "clock": stamp[11:16],
                      "time_ms": int(now.timestamp() * 1000), "media_kind": row["kind"],
                      "source": "auto_attached", "occurrence": 0}
            self.append_inbound(delivered_id, payload, now)
            self.link_media(delivered_id, row["media_id"], filename=filename, now=now)
        with self._lock:
            self._db.execute(
                "update media_seen set attached_at=?, attached_inbound_id=?, attached_phone=?, "
                "link_strength=?, link_reason=? where queue_id=?",
                (stamp, delivered_id, phone, strength, reason, queue_id))
            self._db.commit()
        self.note(now, "media_auto_attached", None, media_id=row["media_id"], strength=strength,
                 reason=reason)
        return True

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

    # --- broadcast runs (TASK-376) -----------------------------------------------------------------
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

    # --- the destruction audit (TASK-376; write side removed TASK-289 -- see
    # bridge/operations.py's own module docstring) -------------------------------------------------
    def audit_count(self):
        return self._db.execute("select count(*) c from audit").fetchone()["c"]

    def audit_rows(self, limit=None):
        """-> the destruction record, newest first (history: the capability that wrote it is gone,
        TASK-289, but the six rows it already wrote stay readable, GET /v1/audit)."""
        sql = "select * from audit order by id desc"
        args = []
        if limit is not None:
            sql += " limit ?"
            args.append(int(limit))
        return [_audit_row(r) for r in self._db.execute(sql, args).fetchall()]

    # --- retention (TASK-359 AC#9) -----------------------------------------------------------------
    def sweep(self, now, *, ledger_days=LEDGER_RETENTION_DAYS, inbound_days=INBOUND_RETENTION_DAYS):
        ledger_cut = utc(now - timedelta(days=ledger_days))
        inbound_cut = utc(now - timedelta(days=inbound_days))
        with self._lock:
            # TASK-277: age alone used to be "resolved_at is not null" -- but mark_unconfirmed also
            # goes through _resolve() and sets resolved_at, so a still-open UNCONFIRMED row (a
            # reconcile has not yet answered for) was swept at ledger_days like any settled one,
            # taking bridge/retention.py::classify_escalation_shot's only way to read its outcome
            # with it. Gated to the same SAFE_OUTBOUND_STATES retention.py itself already calls
            # "settled" -- nothing new invented, just the set this delete should have used from the
            # start.
            done = self._db.execute(
                "delete from outbound where resolved_at is not null and resolved_at < ? "
                "and state in (%s)" % ",".join("?" * len(SAFE_OUTBOUND_STATES)),
                (ledger_cut, *sorted(SAFE_OUTBOUND_STATES))).rowcount
            lines = self._db.execute("delete from journal where at < ?", (ledger_cut,)).rowcount
            mism = self._db.execute(
                "delete from body_mismatch where seen_at < ?", (ledger_cut,)).rowcount
            acked = self._db.execute(
                "delete from inbound where acked_at is not null and acked_at < ?",
                (inbound_cut,)).rowcount
            # TASK-278: an attached file's inbound row just reached the cut above -- its job is
            # done, the VPS already holds its own permanent copy (app/wa/api.py::_write_original)
            # -- so the media_seen row that names it, and any media_link row pointing at an
            # inbound_key that no longer exists (the acked-branch of link_media_auto above can
            # mint a second, throwaway inbound row purely to vacate the queue; that row ages out
            # on the same clock and its link goes with it), are swept here too. A media_seen row
            # that was NEVER attached (attached_inbound_id is null, still sitting in the queue) is
            # left alone -- how long an unclaimed pull should wait is a decision nobody has made
            # yet, not one this sweep invents.
            orphan_media_seen = self._db.execute(
                "select source_rel, media_id from media_seen where attached_inbound_id is not null "
                "and attached_inbound_id not in (select inbound_key from inbound)").fetchall()
            media_seen = 0
            touched_media_ids = set()
            for row in orphan_media_seen:
                self._db.execute(
                    "delete from media_seen where source_rel=?", (row["source_rel"],))
                media_seen += 1
                touched_media_ids.add(row["media_id"])
            media_link = self._db.execute(
                "delete from media_link where inbound_id not in "
                "(select inbound_key from inbound)").rowcount
            # media_file is content-addressed (bridge/media.py) and shared across resends or two
            # people sending byte-identical bytes -- the bytes are only safe to drop once no
            # media_seen row (attached-and-live, or still unattached) names this media_id any more.
            media_file_paths = []
            for media_id in touched_media_ids:
                if self._db.execute(
                        "select 1 from media_seen where media_id=? limit 1", (media_id,)).fetchone():
                    continue
                row = self._db.execute(
                    "select local_path from media_file where media_id=?", (media_id,)).fetchone()
                if row is None:
                    continue
                self._db.execute("delete from media_file where media_id=?", (media_id,))
                media_file_paths.append(row["local_path"])
            # Finished runs take their bodies with them. An unfinished run is never swept: it is
            # still owed to somebody. The audit table is not here on purpose: a destruction record
            # outlives the thing it destroyed (TASK-376; the capability that wrote it is gone,
            # TASK-289, but its six existing rows are not swept retroactively).
            items = self._db.execute(
                """delete from broadcast_item where run_id in
                   (select run_id from broadcast_run where finished_at is not null
                                                       and finished_at < ?)""",
                (ledger_cut,)).rowcount
            runs = self._db.execute(
                "delete from broadcast_run where finished_at is not null and finished_at < ?",
                (ledger_cut,)).rowcount
            self._db.commit()
        # TASK-227/TASK-277: a terminal phone_ops row past its retention window used to be deleted
        # right here, on age alone -- exactly what bridge/retention.py's own artefact review cannot
        # promise, since a debug-capture screenshot or recording is reviewed independently, on its
        # OWN 14-day clock, and routinely still named a row this bare age check had already deleted
        # (closing resolve_op's escape hatch out from under a file nobody had looked at yet).
        # retire_unreferenced_ops below is the replacement: bridge/retention.py::review_and_sweep
        # calls it, once per pass, with the op_ids that SAME pass just classified `hold` -- the
        # only place that knows, right now, whether anything on disk still needs the row.
        return {"outbound": done, "journal": lines, "body_mismatch": mism, "inbound": acked,
                "broadcast_runs": runs, "broadcast_items": items, "media_seen": media_seen,
                "media_link": media_link, "media_file_paths": media_file_paths}

    def retire_unreferenced_ops(self, now, held_op_ids, *, ledger_days=LEDGER_RETENTION_DAYS):
        """TASK-277: delete a terminal (done or failed) phone_ops row past ``ledger_days`` UNLESS
        ``held_op_ids`` -- bridge/retention.py::review_and_sweep's own op_ids classified `hold`
        THIS pass -- says a screenshot or recording still names it.

        Must be called AFTER that pass's own classify-and-delete loop, never before: a row a human
        just resolved, or a send whose outbound row just reached SENT/ABSENT/NOT_ATTEMPTED, has
        its last artefact deleted by that loop and so is already absent from ``held_op_ids`` by
        the time this runs -- resolving it here a second, independent way (an age check keyed on
        ``resolved_at`` instead) would race that loop and could delete the row before its own
        artefact was, leaving the file behind with no row left to explain why it is safe. -> how
        many rows this removed.
        """
        cut = utc(now - timedelta(days=ledger_days))
        with self._lock:
            rows = self._db.execute(
                "select op_id from phone_ops where finished_at is not null and finished_at < ?",
                (cut,)).fetchall()
            stale = [r["op_id"] for r in rows if r["op_id"] not in held_op_ids]
            if stale:
                self._db.executemany(
                    "delete from phone_ops where op_id = ?", [(op_id,) for op_id in stale])
                self._db.commit()
        return len(stale)
