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

#: The inbound event minted for a human's attach (TASK-131 round 5 -- decision-9, 2026-09-22:
#: automatic attribution removed). There is never a real notification behind it: the file may have
#: arrived long after its own placeholder message was drained and swept off this ledger, or (the
#: files already on the live rail before this round) before this ledger even recorded one. A fresh
#: event is minted every time rather than trying to find and patch an old one -- one code path,
#: whether or not a placeholder ever existed. Deterministic on the queue id alone, so a retried
#: attach after a crash between ``append_inbound`` and ``link_media`` below replays (the same
#: unique key) instead of minting a second event for the same file.
ATTACH_INBOUND_PREFIX = "wab.i.attach."

#: WhatsApp's own generic notification glyph per kind (bridge/inbound.py::MEDIA_HINTS), used as the
#: placeholder text of a hand-minted attach event -- honest about being a placeholder, never a
#: sender's real filename.
ATTACH_PLACEHOLDER_TEXT = {"image": "\U0001f4f7 Foto", "video": "\U0001f3a5 Video",
                          "document": "\U0001f4c4 Dokument", "audio": "\U0001f3a4 Sprachnachricht"}

#: How wide a net "threads that plausibly relate to this file" (the queue listing, TASK-131 round
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


#: One queue entry per PULL INSTANCE, not per unique content (TASK-131 round 5, requirement 2): two
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


class Ledger:
    def __init__(self, path):
        # check_same_thread=False: ThreadingHTTPServer answers health and outbox while a send is
        # in flight. Every write goes through _lock, so there is one writer at a time.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("pragma journal_mode=wal")
        self._db.executescript(SCHEMA)
        self._migrate_media_seen()
        self._db.commit()
        self._lock = threading.RLock()

    def _migrate_media_seen(self):
        """TASK-131 round 5 (decision-9, 2026-09-22). ``media_seen`` already holds rows on the mini
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
        # A real, named migrations table (TASK-131 round 7, second attempt -- the first attempt
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
        # TASK-131 round 6 fix (Ivan, 2026-09-22, acceptance-run blocker): these six rows have no
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
        """-> events with id > after, oldest first. No default page size: the caller decides, and a
        truncation invented here would silently drop a candidate's message.

        A row a file has since been attached to (a human's ``Executor.attach_media``, or an
        automatic match -- ``Executor.auto_match_media``, TASK-131 round 6, both through
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

    # --- inbound media: the unresolved queue (TASK-131 round 5) ----------------------------------
    def media_known_paths(self):
        """-> the handset rel-paths already pulled at least once. A file at one of these paths is
        never fetched a second time (colleague's own ``_known_media`` set, made durable here)."""
        return {r[0] for r in self._db.execute("select source_rel from media_seen").fetchall()}

    def record_media(self, *, source_rel, mtime, media_id, sha256, local_path, size, kind,
                     mime_type, filename, now):
        """A file just pulled off the handset -> one queue row, always. -> True when these bytes
        are new to the store.

        Content-addressed for the BYTES only (TASK-131): two handset paths carrying the same bytes
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
        many pulls (this row included) share this file's bytes (TASK-131 round 6 ALSO FIX): 1 for an
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
        automatic match (TASK-131 round 6) without opening ``media-list``."""
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
        (TASK-131 round 6). Never the decision itself, only the pool ``bridge/identity.py::decide``
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
        """THE human escape hatch (TASK-131 round 5, decision-9 2026-09-22): the one way a file
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
        """THE automatic path (TASK-131 round 6, Ivan's ruling 2026-09-22, supersedes decision-9):
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
        """
        row = self._db.execute("select * from media_seen where queue_id=?", (queue_id,)).fetchone()
        if row is None:
            raise KeyError(queue_id)
        if row["attached_at"] is not None:
            raise ValueError(f"{queue_id} is already attached")
        media = self.media_file(row["media_id"])
        created = self.link_media(inbound_id, row["media_id"],
                                  filename=media["filename"] if media else None, now=now)
        if not created:
            return False
        stamp = utc(now)
        with self._lock:
            self._db.execute(
                "update media_seen set attached_at=?, attached_inbound_id=?, attached_phone=?, "
                "link_strength=?, link_reason=? where queue_id=?",
                (stamp, inbound_id, phone, strength, reason, queue_id))
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
