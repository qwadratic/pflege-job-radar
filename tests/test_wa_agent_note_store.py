"""app/wa/store.py, the TASK-303 half: the migration that gives wa_agent_notes task_id/handed_off_at,
and its five new/changed functions (set_agent_note_task, mark_agent_note_handed_off,
release_agent_note, recent_agent_notes_for_phone, handed_off_agent_notes) plus finish_agent_note's and
open_agent_notes()'s own handed_off behaviour. Offline: tmp SQLite only, nothing spawned.
"""
import sqlite3

import pytest

from app.wa import config as C
from app.wa import store as ST


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    with ST.db() as c:
        yield c


def _note(c, wamid="wamid.1", phone="+491", body="test note", kind="text"):
    return ST.record_agent_note(c, wamid, phone, body, kind)


# --- migration of an old-schema database ----------------------------------------------------------

def test_an_old_schema_database_gains_task_id_and_handed_off_at_in_place(tmp_path, monkeypatch):
    """The live db had exactly one row when this task started -- simulate that shape: a wa_agent_notes
    table exactly as it first shipped (no task_id/handed_off_at), one real-shaped row, then open it
    through ST.db() the way any caller would and confirm the row survives with the new columns
    present and null (never dropped, never re-created)."""
    db_path = tmp_path / "old.sqlite"
    old_conn = sqlite3.connect(db_path)
    old_conn.execute("""create table wa_agent_notes (
        id integer primary key, wamid text not null unique, phone text not null, kind text not null,
        body text not null, status text not null, created_at text not null, acked_at text,
        claimed_at text, attempts integer not null default 0, progress text, outcome_done text,
        outcome_not_done text, outcome_needed text, blocked_reason text, finished_at text,
        notified_at text)""")
    old_conn.execute("insert into wa_agent_notes (id, wamid, phone, kind, body, status, created_at) "
                     "values (1, 'wamid.old', '+491', 'text', 'pre-existing note', 'blocked', "
                     "'2026-09-24T18:45:45+00:00')")
    old_conn.commit()
    old_conn.close()

    monkeypatch.setattr(C, "SQLITE_PATH", db_path)
    with ST.db() as c:
        cols = {r[1] for r in c.execute("pragma table_info(wa_agent_notes)").fetchall()}
        assert {"task_id", "handed_off_at"} <= cols
        row = ST.agent_note(c, 1)
        assert row["body"] == "pre-existing note" and row["status"] == "blocked"
        assert row["task_id"] is None and row["handed_off_at"] is None


def test_migration_runs_again_without_error_on_a_database_that_already_has_the_columns(db):
    """ST.db() runs _migrate() on every call, fresh database included (MIGRATIONS' own comment) -- a
    second open must not raise 'duplicate column name'."""
    with ST.db() as c2:
        cols = {r[1] for r in c2.execute("pragma table_info(wa_agent_notes)").fetchall()}
    assert {"task_id", "handed_off_at"} <= cols


# --- A2: wa_agent_notes gains AUTOINCREMENT, ids are never reused ----------------------------------

def _create_old_shape(db_path, rows):
    """A pre-A2 database: 'id integer primary key' (plain rowid aliasing, no AUTOINCREMENT), with
    task_id/handed_off_at already present -- the live db's own shape immediately before this fix
    (round-1 review nonblocking note: those two columns were already live)."""
    conn = sqlite3.connect(db_path)
    conn.execute("""create table wa_agent_notes (
        id integer primary key, wamid text not null unique, phone text not null, kind text not null,
        body text not null, status text not null, created_at text not null, acked_at text,
        claimed_at text, attempts integer not null default 0, progress text, outcome_done text,
        outcome_not_done text, outcome_needed text, blocked_reason text, finished_at text,
        notified_at text, task_id text, handed_off_at text)""")
    for row in rows:
        conn.execute("insert into wa_agent_notes (id, wamid, phone, kind, body, status, created_at, "
                     "task_id, handed_off_at) values (?,?,?,?,?,?,?,?,?)", row)
    conn.commit()
    conn.close()


def test_an_old_schema_table_gains_autoincrement_and_every_row_survives(tmp_path, monkeypatch):
    db_path = tmp_path / "old_autoincrement.sqlite"
    _create_old_shape(db_path, [
        (1, "wamid.1", "+491", "text", "first", "done", "2026-09-24T18:00:00+00:00", "TASK-1", None),
        (9, "wamid.9", "+492", "text", "ninth, with a task_id and handed_off_at", "handed_off",
         "2026-09-24T19:00:00+00:00", "TASK-9", "2026-09-24T19:05:00+00:00"),
    ])
    monkeypatch.setattr(C, "SQLITE_PATH", db_path)
    with ST.db() as c:
        sql = c.execute("select sql from sqlite_master where type='table' and name='wa_agent_notes'").fetchone()[0]
        assert "autoincrement" in sql.lower()
        r1, r9 = ST.agent_note(c, 1), ST.agent_note(c, 9)
        assert r1["wamid"] == "wamid.1" and r1["task_id"] == "TASK-1" and r1["handed_off_at"] is None
        assert r9["wamid"] == "wamid.9" and r9["status"] == "handed_off"
        assert r9["handed_off_at"] == "2026-09-24T19:05:00+00:00"
        assert r9["body"] == "ninth, with a task_id and handed_off_at"


def test_ids_are_never_reused_after_the_table_goes_empty(tmp_path, monkeypatch):
    """The exact failure the review reproduced: a plain 'integer primary key' table reuses id 1 the
    moment it is empty (e.g. after the nightly purge, before A1 excluded this table from it). Confirm
    the migrated table does not, even across a full empty-then-refill cycle."""
    db_path = tmp_path / "old_reuse.sqlite"
    _create_old_shape(db_path, [(1, "wamid.1", "+491", "text", "only row", "done",
                                 "2026-09-24T18:00:00+00:00", None, None)])
    monkeypatch.setattr(C, "SQLITE_PATH", db_path)
    with ST.db() as c:
        c.execute("delete from wa_agent_notes")
        c.commit()
        fresh = ST.record_agent_note(c, "wamid.new", "+493", "a brand new note", "text")
    assert fresh["id"] != 1, "must never come back as id 1 -- AUTOINCREMENT never reuses a used id"
    assert fresh["id"] > 1


def test_a_new_row_after_migration_gets_max_id_plus_one_no_invented_floor(tmp_path, monkeypatch):
    db_path = tmp_path / "old_seed.sqlite"
    _create_old_shape(db_path, [
        (3, "wamid.3", "+491", "text", "a", "done", "2026-09-24T18:00:00+00:00", None, None),
        (7, "wamid.7", "+491", "text", "b", "done", "2026-09-24T18:01:00+00:00", None, None),
    ])
    monkeypatch.setattr(C, "SQLITE_PATH", db_path)
    with ST.db() as c:
        fresh = ST.record_agent_note(c, "wamid.new", "+491", "next", "text")
    assert fresh["id"] == 8   # max(3,7)+1 -- not 1, not some other invented starting point


def test_an_empty_old_table_migrates_and_starts_at_one(tmp_path, monkeypatch):
    db_path = tmp_path / "old_empty.sqlite"
    _create_old_shape(db_path, [])
    monkeypatch.setattr(C, "SQLITE_PATH", db_path)
    with ST.db() as c:
        fresh = ST.record_agent_note(c, "wamid.new", "+491", "first ever", "text")
    assert fresh["id"] == 1


def test_the_autoincrement_migration_is_idempotent_across_repeated_opens(tmp_path, monkeypatch):
    db_path = tmp_path / "old_idempotent.sqlite"
    _create_old_shape(db_path, [(5, "wamid.5", "+491", "text", "x", "done",
                                 "2026-09-24T18:00:00+00:00", None, None)])
    monkeypatch.setattr(C, "SQLITE_PATH", db_path)
    with ST.db() as c:
        pass
    with ST.db() as c2:   # second open must not re-rebuild, re-raise, or reset the sequence
        row = ST.agent_note(c2, 5)
        assert row["wamid"] == "wamid.5"
        fresh = ST.record_agent_note(c2, "wamid.new2", "+491", "next", "text")
    assert fresh["id"] == 6


def test_a_database_that_already_has_autoincrement_is_left_alone(db):
    """The normal path for every NEW database created by this fix going forward (db() always runs the
    migration, SCHEMA's own create-table stays as first written per this file's convention) -- a table
    already migrated must not be rebuilt a second time on every single db() call."""
    sql_before = db.execute(
        "select sql from sqlite_master where type='table' and name='wa_agent_notes'").fetchone()[0]
    with ST.db() as c2:
        sql_after = c2.execute(
            "select sql from sqlite_master where type='table' and name='wa_agent_notes'").fetchone()[0]
    assert "autoincrement" in sql_after.lower()
    assert sql_before == sql_after


# --- open_agent_notes() must never return a handed_off row -----------------------------------------

def test_open_agent_notes_never_returns_a_handed_off_row(db):
    row = _note(db)
    ST.claim_agent_note(db, row["id"])
    ST.set_agent_note_task(db, row["id"], "TASK-1")
    assert ST.mark_agent_note_handed_off(db, row["id"]) is True
    assert [r["id"] for r in ST.open_agent_notes(db)] == []


def test_open_agent_notes_returns_pending_but_not_a_sibling_handed_off_note(db):
    pending = _note(db, wamid="w.pending")
    handed_off = _note(db, wamid="w.handedoff")
    ST.claim_agent_note(db, handed_off["id"])
    ST.mark_agent_note_handed_off(db, handed_off["id"])
    ids = {r["id"] for r in ST.open_agent_notes(db)}
    assert pending["id"] in ids
    assert handed_off["id"] not in ids


# --- finish_agent_note must work on a handed_off note -----------------------------------------------

def test_finish_agent_note_closes_a_handed_off_note(db):
    """The working session's own --done/--blocked must be able to close a note the worker already
    handed off -- finish_agent_note carries no WHERE on the current status, on purpose."""
    row = _note(db)
    ST.claim_agent_note(db, row["id"])
    ST.set_agent_note_task(db, row["id"], "TASK-2")
    ST.mark_agent_note_handed_off(db, row["id"])
    ST.finish_agent_note(db, row["id"], "done", "fixed it", "", "")
    fresh = ST.agent_note(db, row["id"])
    assert fresh["status"] == "done" and fresh["outcome_done"] == "fixed it"
    assert fresh["task_id"] == "TASK-2"        # untouched by finish_agent_note
    assert fresh["claimed_at"] is None


# --- set_agent_note_task -----------------------------------------------------------------------

def test_set_agent_note_task_records_the_card_id(db):
    row = _note(db)
    ST.set_agent_note_task(db, row["id"], "TASK-42")
    assert ST.agent_note(db, row["id"])["task_id"] == "TASK-42"


# --- mark_agent_note_handed_off ------------------------------------------------------------------

def test_mark_agent_note_handed_off_sets_status_timestamp_and_clears_claim(db):
    row = _note(db)
    ST.claim_agent_note(db, row["id"])
    ST.set_agent_note_task(db, row["id"], "TASK-3")
    assert ST.mark_agent_note_handed_off(db, row["id"]) is True
    fresh = ST.agent_note(db, row["id"])
    assert fresh["status"] == ST.AGENT_NOTE_HANDED_OFF == "handed_off"
    assert fresh["handed_off_at"] is not None
    assert fresh["claimed_at"] is None
    assert fresh["task_id"] == "TASK-3"


def test_mark_agent_note_handed_off_refuses_a_note_that_is_not_in_progress(db):
    row = _note(db)   # still pending, never claimed
    assert ST.mark_agent_note_handed_off(db, row["id"]) is False
    assert ST.agent_note(db, row["id"])["status"] == "pending"


# --- release_agent_note ---------------------------------------------------------------------------

def test_release_agent_note_returns_to_pending_keeping_task_id_and_attempts(db):
    row = _note(db)
    assert ST.claim_agent_note(db, row["id"]) is True
    ST.set_agent_note_task(db, row["id"], "TASK-4")
    assert ST.release_agent_note(db, row["id"]) is True
    fresh = ST.agent_note(db, row["id"])
    assert fresh["status"] == "pending"
    assert fresh["claimed_at"] is None
    assert fresh["task_id"] == "TASK-4"   # kept -- a retry must not create a second card (AC#6)
    assert fresh["attempts"] == 1


def test_release_agent_note_refuses_a_note_that_is_not_in_progress(db):
    row = _note(db)
    assert ST.release_agent_note(db, row["id"]) is False


def test_a_released_note_is_claimable_again_and_attempts_keeps_counting(db):
    """The shape the attempts cap depends on: release -> claim -> release -> claim must keep raising
    attempts, not reset it, or a note that always fails at the same later step would retry forever."""
    row = _note(db)
    ST.claim_agent_note(db, row["id"])
    ST.release_agent_note(db, row["id"])
    ST.claim_agent_note(db, row["id"])
    ST.release_agent_note(db, row["id"])
    assert ST.agent_note(db, row["id"])["attempts"] == 2
    assert [r["id"] for r in ST.open_agent_notes(db)] == [row["id"]]


# --- recent_agent_notes_for_phone ----------------------------------------------------------------

def test_recent_agent_notes_for_phone_excludes_self_and_other_phones_newest_first(db):
    a = ST.record_agent_note(db, "w.a", "+491", "first", "text")
    b = ST.record_agent_note(db, "w.b", "+491", "second", "text")
    current = ST.record_agent_note(db, "w.c", "+491", "current one, excluded", "text")
    ST.record_agent_note(db, "w.d", "+492", "different phone, excluded", "text")
    earlier = ST.recent_agent_notes_for_phone(db, "+491", current["id"], limit=5)
    assert [n["id"] for n in earlier] == [b["id"], a["id"]]


def test_recent_agent_notes_for_phone_respects_the_limit(db):
    for i in range(7):
        ST.record_agent_note(db, f"w.{i}", "+491", f"note {i}", "text")
    earlier = ST.recent_agent_notes_for_phone(db, "+491", 999999, limit=3)
    assert len(earlier) == 3


# --- claimable_agent_notes / undelivered_completion_notes / orphaned_in_progress_notes (item B/C) --

def test_claimable_agent_notes_excludes_finished_undelivered_notes(db):
    """The split item B needs: a finished-but-undelivered note must NOT be claimable through the
    normal pipeline queue (it is retried on its own, separate schedule) -- this is the fix for the
    round-1 finding that a stuck completion retry, as rows[0] of a single combined query, starved
    every later note forever."""
    pending = _note(db, wamid="w.pending")
    finished = _note(db, wamid="w.finished")
    ST.finish_agent_note(db, finished["id"], "done", "x", "", "")
    ids = {r["id"] for r in ST.claimable_agent_notes(db)}
    assert ids == {pending["id"]}


def test_claimable_agent_notes_includes_pending_and_stale_in_progress(db, monkeypatch):
    pending = _note(db, wamid="w.pending")
    stale = _note(db, wamid="w.stale")
    ST.claim_agent_note(db, stale["id"])
    db.execute("update wa_agent_notes set claimed_at='2000-01-01T00:00:00+00:00' where id=?", (stale["id"],))
    db.commit()
    ids = {r["id"] for r in ST.claimable_agent_notes(db)}
    assert ids == {pending["id"], stale["id"]}


def test_undelivered_completion_notes_lists_done_and_blocked_with_no_notified_at(db):
    done = _note(db, wamid="w.done")
    ST.finish_agent_note(db, done["id"], "done", "x", "", "")
    blocked = _note(db, wamid="w.blocked")
    ST.finish_agent_note(db, blocked["id"], "blocked", "", "y", "z", blocked_reason="y")
    delivered = _note(db, wamid="w.delivered")
    ST.finish_agent_note(db, delivered["id"], "done", "x", "", "")
    assert ST.mark_agent_note_notified(db, delivered["id"]) is True
    ids = {r["id"] for r in ST.undelivered_completion_notes(db)}
    assert ids == {done["id"], blocked["id"]}
    assert delivered["id"] not in ids


def test_undelivered_completion_notes_has_no_limit(db):
    """AC#8's own 'until delivered' / Ivan's 'no cap on completion retries' -- every undelivered note
    comes back, however many there are."""
    ids_made = []
    for i in range(12):
        row = _note(db, wamid=f"w.many.{i}")
        ST.finish_agent_note(db, row["id"], "done", "x", "", "")
        ids_made.append(row["id"])
    ids = [r["id"] for r in ST.undelivered_completion_notes(db)]
    assert ids == ids_made   # every one, oldest first, none dropped


def test_orphaned_in_progress_notes_finds_a_live_claim_but_not_a_stale_one(db):
    live = _note(db, wamid="w.live")
    ST.claim_agent_note(db, live["id"])   # claimed_at = now -- not yet stale
    stale = _note(db, wamid="w.stale")
    ST.claim_agent_note(db, stale["id"])
    db.execute("update wa_agent_notes set claimed_at='2000-01-01T00:00:00+00:00' where id=?", (stale["id"],))
    db.commit()
    pending = _note(db, wamid="w.pending")
    ids = {r["id"] for r in ST.orphaned_in_progress_notes(db)}
    assert ids == {live["id"]}
    assert stale["id"] not in ids and pending["id"] not in ids


def test_orphaned_in_progress_notes_empty_when_nothing_is_live_in_progress(db):
    row = _note(db)   # still pending -- never claimed
    assert list(ST.orphaned_in_progress_notes(db)) == []


# --- handed_off_agent_notes (the working session's own --handed-off view) ------------------------

def test_handed_off_agent_notes_lists_only_that_status_oldest_first(db):
    a = _note(db, wamid="w.a")
    b = _note(db, wamid="w.b")
    for row in (a, b):
        ST.claim_agent_note(db, row["id"])
        ST.mark_agent_note_handed_off(db, row["id"])
    still_pending = _note(db, wamid="w.c")
    ids = [r["id"] for r in ST.handed_off_agent_notes(db)]
    assert ids == [a["id"], b["id"]]
    assert still_pending["id"] not in ids
