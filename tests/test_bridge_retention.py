"""Offline proof for bridge/retention.py (TASK-230): review before delete. FakeDriver + a real
sqlite Ledger, no adb, no phone -- same Rig shape as tests/test_bridge_executor.py.

Every test here is a version of one question: can this artefact be deleted right now without
losing evidence of something nobody has looked at yet? "Age past 14 days" alone must never be
enough to answer yes.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bridge import driver as D
from bridge import executor as X
from bridge import governor as G
from bridge import ledger as L
from bridge import retention as RT

PHONE = "+491700000001"
KEY = "wab.o.0000000000000000000000000000aaaa"


class Clock:
    def __init__(self, moment):
        self.now = moment

    def __call__(self):
        return self.now


def berlin_now():
    return datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


class Rig:
    def __init__(self, tmp_path):
        self.clock = Clock(berlin_now())
        self.ledger = L.Ledger(tmp_path / "ledger.sqlite")
        self.driver = D.FakeDriver()
        self.governor = G.Governor(self.ledger, per_number_daily_cap=3,
                                   rng=__import__("random").Random(7))
        self.executor = X.Executor(ledger=self.ledger, governor=self.governor, driver=self.driver,
                                   rail_number=None, clock=self.clock, tick_wait_sec=0.0,
                                   sleep=lambda _s: None, monotonic=lambda: 0.0)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def op_id(tag="a"):
    return "op." + (tag * 24)[:24]


# --- the migration this whole feature depends on ---------------------------------------------------
def test_opening_a_pre_task230_database_adds_resolved_at_without_losing_rows(tmp_path):
    """The mini's live ledger.sqlite already has a phone_ops table from TASK-227, written before
    this column existed -- `create table if not exists` cannot add a column to a table that
    already exists (same limit _migrate_media_seen exists for), so this is the one path that has
    to work against a database this test file does not control the history of."""
    import sqlite3

    path = tmp_path / "pre_existing.sqlite"
    raw = sqlite3.connect(str(path))
    raw.executescript("""
        create table phone_ops (
          op_id       text primary key,
          position    integer not null,
          kind        text not null,
          args        text not null,
          state       text not null,
          result      text,
          error       text,
          created_at  text not null,
          started_at  text,
          finished_at text
        );
        create index idx_phone_ops_state on phone_ops(state, position);
    """)
    raw.execute("insert into phone_ops(op_id, position, kind, args, state, created_at) "
               "values(?,?,?,?,?,?)", (op_id("1"), 1, "clear_chat", "{}", "failed", "2026-09-01T00:00:00Z"))
    raw.commit()
    raw.close()

    ledger = L.Ledger(path)
    row = ledger.op_status(op_id("1"))
    assert row is not None and row["resolved_at"] is None, "the pre-existing row survives the ALTER"
    ledger.resolve_op(op_id("1"), berlin_now())
    assert ledger.op_status(op_id("1"))["resolved_at"] is not None
    ledger.close()


def enqueue(rig, *, op_id, kind="clear_chat", args=None):
    rig.ledger.enqueue_op(op_id, kind, args or {}, rig.clock())


def _journal_rows(rig, event):
    rows = rig.ledger._db.execute(
        "select * from journal where event = ? order by id", (event,)).fetchall()
    return [dict(r) for r in rows]


# --- classify_op_artifact --------------------------------------------------------------------------

def test_a_done_op_is_happy(rig):
    oid = op_id("1")
    enqueue(rig, op_id=oid, kind="read_thread", args={"phone": PHONE})
    rig.ledger.mark_op_done(oid, {"count": 0}, rig.clock())
    assert RT.classify_op_artifact(rig.ledger, oid) == ("delete", "op done")


def test_a_still_queued_op_is_held(rig):
    oid = op_id("2")
    enqueue(rig, op_id=oid)
    verdict, reason = RT.classify_op_artifact(rig.ledger, oid)
    assert verdict == "hold" and "queued" in reason


def test_an_unknown_op_id_is_held(rig):
    verdict, reason = RT.classify_op_artifact(rig.ledger, op_id("9"))
    assert verdict == "hold" and "not found" in reason


def test_a_failed_send_whose_outbound_row_later_reached_sent_is_resolved(rig):
    oid = op_id("3")
    enqueue(rig, op_id=oid, kind="send",
           args={"req": {"client_msg_id": KEY, "to": PHONE, "kind": "text", "body": "hi"}})
    rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "device_unavailable"}}, rig.clock())
    rig.ledger.begin(KEY, phone=PHONE, kind="text", body_sha256="x", body_len=2, now=rig.clock())
    rig.ledger.mark_sent(KEY, rig.clock(), tick="Gelesen", tick_state="read", clock="09:15")
    assert RT.classify_op_artifact(rig.ledger, oid) == ("delete", "outbound resolved: sent")


def test_a_failed_send_whose_outbound_row_reconciled_to_absent_is_resolved(rig):
    oid = op_id("4")
    enqueue(rig, op_id=oid, kind="send",
           args={"req": {"client_msg_id": KEY, "to": PHONE, "kind": "text", "body": "hi"}})
    rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "device_unavailable"}}, rig.clock())
    rig.ledger.begin(KEY, phone=PHONE, kind="text", body_sha256="x", body_len=2, now=rig.clock())
    rig.ledger.mark_absent(KEY, rig.clock(), evidence="chat scan: nothing")
    assert RT.classify_op_artifact(rig.ledger, oid) == ("delete", "outbound resolved: absent")


def test_a_failed_send_still_unconfirmed_is_held(rig):
    oid = op_id("5")
    enqueue(rig, op_id=oid, kind="send",
           args={"req": {"client_msg_id": KEY, "to": PHONE, "kind": "text", "body": "hi"}})
    rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "device_unavailable"}}, rig.clock())
    rig.ledger.begin(KEY, phone=PHONE, kind="text", body_sha256="x", body_len=2, now=rig.clock())
    rig.ledger.mark_unconfirmed(KEY, rig.clock(), detail="wrong_thread shot=x")
    verdict, reason = RT.classify_op_artifact(rig.ledger, oid)
    assert verdict == "hold" and "outbound still unconfirmed" in reason


def test_a_failed_send_with_no_outbound_row_at_all_is_held(rig):
    """A refusal before the ledger's own begin() (a governor pacing 429, say) -- nothing to read a
    verdict off, so this is exactly the case a human should look at, not the case to assume fine."""
    oid = op_id("6")
    enqueue(rig, op_id=oid, kind="send",
           args={"req": {"client_msg_id": KEY, "to": PHONE, "kind": "text", "body": "hi"}})
    rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "rail_parked"}}, rig.clock())
    verdict, reason = RT.classify_op_artifact(rig.ledger, oid)
    assert verdict == "hold" and "outbound still missing" in reason


def test_a_failed_non_send_op_is_held_until_manually_resolved(rig):
    """clear_chat/delete_chat/read_thread/send_photos mint no client_msg_id -- reconcile has
    nothing to read. The only way out is a human calling resolve_op."""
    oid = op_id("7")
    enqueue(rig, op_id=oid, kind="clear_chat", args={"phone": PHONE, "confirm": True})
    rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "destroy_unconfirmed"}}, rig.clock())
    verdict, reason = RT.classify_op_artifact(rig.ledger, oid)
    assert verdict == "hold" and "not yet resolved" in reason

    rig.ledger.resolve_op(oid, rig.clock())
    assert RT.classify_op_artifact(rig.ledger, oid) == ("delete", "manually resolved")


def test_resolving_an_op_twice_is_harmless(rig):
    oid = op_id("8")
    enqueue(rig, op_id=oid, kind="clear_chat", args={})
    rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "x"}}, rig.clock())
    rig.ledger.resolve_op(oid, rig.clock())
    rig.ledger.resolve_op(oid, rig.clock())
    assert RT.classify_op_artifact(rig.ledger, oid) == ("delete", "manually resolved")


def test_resolving_a_send_op_does_not_override_a_still_open_outbound_row(rig):
    """Adversarial review (2026-09-23), finding #4: resolve_op is documented as the escape hatch
    for ops with NO client_msg_id -- a send's own outbound state is the only thing allowed to
    settle it. Calling resolve_op on a send anyway (operator error, stale runbook) must not open a
    second, weaker path to the same "delete" verdict while the send is still an open question."""
    oid = op_id("1")
    enqueue(rig, op_id=oid, kind="send",
           args={"req": {"client_msg_id": KEY, "to": PHONE, "kind": "text", "body": "hi"}})
    rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "device_unavailable"}}, rig.clock())
    rig.ledger.begin(KEY, phone=PHONE, kind="text", body_sha256="x", body_len=2, now=rig.clock())
    rig.ledger.mark_unconfirmed(KEY, rig.clock(), detail="wrong_thread shot=x")

    rig.ledger.resolve_op(oid, rig.clock())
    verdict, reason = RT.classify_op_artifact(rig.ledger, oid)
    assert verdict == "hold" and "outbound still unconfirmed" in reason


# --- classify_op_artifact: OP_DONE is not always the end of the question (adversarial review,
# 2026-09-23) -- reconcile and the media sends both need the result itself inspected. -------------

def test_a_done_reconcile_op_with_an_indeterminate_verdict_is_held(rig):
    oid = op_id("1")
    enqueue(rig, op_id=oid, kind="reconcile", args={"client_msg_ids": [KEY]})
    rig.ledger.mark_op_done(
        oid, [{"client_msg_id": KEY, "verdict": "indeterminate", "evidence": "thread unreadable"}],
        rig.clock())
    verdict, reason = RT.classify_op_artifact(rig.ledger, oid)
    assert verdict == "hold" and "indeterminate" in reason


def test_a_done_reconcile_op_that_settled_everything_is_happy(rig):
    oid = op_id("1")
    enqueue(rig, op_id=oid, kind="reconcile", args={"client_msg_ids": [KEY]})
    rig.ledger.mark_op_done(
        oid, [{"client_msg_id": KEY, "verdict": "confirmed_absent", "evidence": "chat scan: nothing"}],
        rig.clock())
    assert RT.classify_op_artifact(rig.ledger, oid) == ("delete", "op done")


def test_a_done_send_photos_op_with_an_unconfirmed_tick_is_held(rig):
    oid = op_id("1")
    enqueue(rig, op_id=oid, kind="send_photos", args={"phone": PHONE, "local_paths": ["/x/a.jpg"]})
    rig.ledger.mark_op_done(
        oid, {"ok": True, "at": "2026-09-23T12:00:00Z", "sent": [{"clock": "09:15", "tick": ""}]},
        rig.clock())
    verdict, reason = RT.classify_op_artifact(rig.ledger, oid)
    assert verdict == "hold" and "no delivery tick" in reason


def test_a_done_send_photos_op_with_every_tick_confirmed_is_happy(rig):
    oid = op_id("1")
    enqueue(rig, op_id=oid, kind="send_photos", args={"phone": PHONE, "local_paths": ["/x/a.jpg"]})
    rig.ledger.mark_op_done(
        oid,
        {"ok": True, "at": "2026-09-23T12:00:00Z", "sent": [{"clock": "09:15", "tick": "Gesendet"}]},
        rig.clock())
    assert RT.classify_op_artifact(rig.ledger, oid) == ("delete", "op done")


def test_a_done_send_gallery_op_with_no_tick_is_held(rig):
    oid = op_id("1")
    enqueue(rig, op_id=oid, kind="send_gallery",
           args={"phone": PHONE, "local_paths": ["/x/a.jpg", "/x/b.jpg"], "caption": ""})
    rig.ledger.mark_op_done(
        oid, {"ok": True, "at": "2026-09-23T12:00:00Z", "clock": "09:15", "tick": ""}, rig.clock())
    verdict, reason = RT.classify_op_artifact(rig.ledger, oid)
    assert verdict == "hold" and "no delivery tick" in reason


# --- classify_escalation_shot (old-style, no op_id in the filename) -------------------------------

def test_an_escalation_shot_with_no_journal_entry_is_held(rig):
    verdict, reason = RT.classify_escalation_shot(rig.ledger, Path("/shots/mystery.png"), {})
    assert verdict == "hold" and "no escalation_shot" in reason


def test_an_escalation_shot_is_resolved_once_its_send_reaches_sent(rig):
    path = Path("/shots/20260923_001_reply_thread.png")
    rig.ledger.begin(KEY, phone=PHONE, kind="text", body_sha256="x", body_len=2, now=rig.clock())
    rig.ledger.note(rig.clock(), "escalation_shot", KEY, path=str(path))
    rig.ledger.mark_unconfirmed(KEY, rig.clock(), detail=f"wrong_thread shot={path}")
    index = rig.ledger.escalation_shot_index()
    verdict, reason = RT.classify_escalation_shot(rig.ledger, path, index)
    assert verdict == "hold" and "unconfirmed" in reason

    rig.ledger.mark_sent(KEY, rig.clock(), tick="Gesendet", tick_state="sent", clock="09:15")
    index = rig.ledger.escalation_shot_index()
    verdict, reason = RT.classify_escalation_shot(rig.ledger, path, index)
    assert verdict == "delete" and reason == "outbound resolved: sent"


# --- review_and_sweep: the end-to-end pass maintenance_once calls ---------------------------------

def test_review_and_sweep_deletes_happy_holds_the_rest_and_journals_every_hold(rig):
    done_id, held_id = op_id("d"), op_id("e")
    enqueue(rig, op_id=done_id, kind="read_thread", args={"phone": PHONE})
    rig.ledger.mark_op_done(done_id, {"count": 0}, rig.clock())
    enqueue(rig, op_id=held_id, kind="clear_chat", args={})
    rig.ledger.mark_op_failed(held_id, {"ok": False, "error": {"code": "x"}}, rig.clock())

    happy_shot = Path(f"/shots/{done_id}_00_pre.png")
    held_shot = Path(f"/shots/{held_id}_00_pre.png")
    mystery_shot = Path("/shots/20260101_001_old_style.png")   # no journal entry at all
    rig.driver.screenshot_candidates = [happy_shot, held_shot, mystery_shot]

    result = RT.review_and_sweep(rig.executor, rig.clock(), screenshot_days=14, recording_days=14)
    assert result["screenshots"] == {"deleted": 1, "held": 2}
    assert rig.driver.deleted_paths == [happy_shot]

    held_rows = _journal_rows(rig, "retention_held")
    held_paths = {r["client_msg_id"] for r in held_rows}  # note() stores None here; check detail
    import json as _json
    held_paths = {_json.loads(r["detail"])["path"] for r in held_rows}
    assert held_paths == {str(held_shot), str(mystery_shot)}


def test_review_and_sweep_holds_a_recording_it_cannot_recognise(rig):
    weird = Path("/recordings/not_an_op_id.mp4")
    rig.driver.recording_candidates = [weird]
    result = RT.review_and_sweep(rig.executor, rig.clock(), screenshot_days=14, recording_days=14)
    assert result["recordings"] == {"deleted": 0, "held": 1}
    assert rig.driver.deleted_paths == []


def test_review_and_sweep_deletes_a_happy_recording(rig):
    oid = op_id("f")
    enqueue(rig, op_id=oid, kind="send",
           args={"req": {"client_msg_id": KEY, "to": PHONE, "kind": "text", "body": "hi"}})
    rig.ledger.mark_op_done(oid, {"state": "sent"}, rig.clock())
    rec = Path(f"/recordings/{oid}.mp4")
    rig.driver.recording_candidates = [rec]
    result = RT.review_and_sweep(rig.executor, rig.clock(), screenshot_days=14, recording_days=14)
    assert result["recordings"] == {"deleted": 1, "held": 0}
    assert rig.driver.deleted_paths == [rec]


def test_a_resend_landing_mid_sweep_cannot_reach_a_file_already_deleted_but_still_holds_the_next_one(rig):
    """Adversarial review (2026-09-23), finding #1: the old classify-everything-then-batch-delete
    shape left every already-"delete"-classified file exposed to a resend for as long as the whole
    directory's worth of DB reads took. Deleting the instant a file is classified narrows that
    window to nothing -- a resend landing right after file A is physically gone cannot un-delete
    it (nothing wrong happened: A really was safe at the moment it was removed), but it must still
    be seen by file B's own classification, which has not run yet."""
    key_a, key_b = KEY, "wab.o.0000000000000000000000000000bbbb"
    op_a, op_b = op_id("a"), op_id("b")
    for key, oid in ((key_a, op_a), (key_b, op_b)):
        enqueue(rig, op_id=oid, kind="send",
               args={"req": {"client_msg_id": key, "to": PHONE, "kind": "text", "body": "hi"}})
        rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "device_unavailable"}}, rig.clock())
        rig.ledger.begin(key, phone=PHONE, kind="text", body_sha256="x", body_len=2, now=rig.clock())
        rig.ledger.mark_absent(key, rig.clock(), evidence="chat scan: nothing")

    shot_a, shot_b = Path(f"/shots/{op_a}_00_pre.png"), Path(f"/shots/{op_b}_00_pre.png")
    rig.driver.screenshot_candidates = [shot_a, shot_b]

    real_delete = rig.driver.delete_paths

    def delete_then_resend(paths):
        n = real_delete(paths)
        # A legitimate resend of key_b (catchup.py, an operator retry -- both authorised on an
        # ABSENT row) lands right after A's file is gone and before B is ever classified.
        rig.ledger.begin(key_b, phone=PHONE, kind="text", body_sha256="x", body_len=2, now=rig.clock())
        return n

    rig.driver.delete_paths = delete_then_resend

    result = RT.review_and_sweep(rig.executor, rig.clock(), screenshot_days=14, recording_days=14)
    assert rig.driver.deleted_paths == [shot_a]
    assert result["screenshots"] == {"deleted": 1, "held": 1}
    assert rig.ledger.get(key_b).state == L.ATTEMPTING


def test_review_and_sweep_touches_nothing_when_there_are_no_candidates(rig):
    result = RT.review_and_sweep(rig.executor, rig.clock(), screenshot_days=14, recording_days=14)
    assert result == {"screenshots": {"deleted": 0, "held": 0},
                      "recordings": {"deleted": 0, "held": 0}}
    assert rig.driver.deleted_paths == []
