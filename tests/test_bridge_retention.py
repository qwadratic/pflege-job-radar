"""Offline proof for bridge/retention.py (TASK-230): review before delete. FakeDriver + a real
sqlite Ledger, no adb, no phone -- same Rig shape as tests/test_bridge_executor.py.

Every test here is a version of one question: can this artefact be deleted right now without
losing evidence of something nobody has looked at yet? "Age past 14 days" alone must never be
enough to answer yes.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bridge import driver as D
from bridge import executor as X
from bridge import governor as G
from bridge import ledger as L
from bridge import retention as RT
from bridge import server as SV

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


def test_a_missing_op_row_with_an_aged_out_artefact_is_held_with_the_aged_out_reason(rig, tmp_path):
    """TASK-265: distinguishing "op row aged out" from "op row never existed" still matters for an
    artefact whose phone_ops row is gone for any reason -- data a pre-TASK-277 ledger.sweep()
    already orphaned, or a direct caller of classify_op_artifact (its own docstring) that bypasses
    review_and_sweep entirely. Before TASK-265, op_status returning None here was reported
    identically to an op_id that never existed ("op_id not found in phone_ops"), which reads as a
    bug elsewhere; the artefact's own age is what tells the two apart.

    TASK-277 changed HOW a row this old can still go missing (see the sibling tests below --
    review_and_sweep itself no longer lets this happen to a file it is still holding), but the
    wording this test checks has to stay right regardless of the reason."""
    oid = op_id("g")
    old = rig.clock() - timedelta(days=L.LEDGER_RETENTION_DAYS + 1)
    shot = tmp_path / f"{oid}_00_pre.png"
    shot.write_bytes(b"x")
    old_epoch = old.timestamp()
    os.utime(shot, (old_epoch, old_epoch))
    # No phone_ops row at all -- classify_op_artifact never learns why.

    verdict, reason = RT.classify_op_artifact(rig.ledger, oid, shot, rig.clock())
    assert verdict == "hold"
    assert "aged out past LEDGER_RETENTION_DAYS" in reason
    assert "not found" not in reason  # the old, misdirecting reason must not still be there


def test_ledger_sweep_alone_never_deletes_a_phone_ops_row(rig):
    """TASK-277: phone_ops row deletion is retention-aware now -- only
    ledger.retire_unreferenced_ops (called from bridge/retention.py::review_and_sweep, the only
    thing that knows what is still on disk) may remove one. ledger.sweep() on its own -- the same
    call bridge/server.py::maintenance_once still makes for every other table -- must never again
    destroy a phone_ops row purely because it is old; that is exactly the bug this task is about."""
    oid = op_id("c")
    enqueue(rig, op_id=oid, kind="clear_chat", args={"phone": PHONE, "confirm": True})
    old = rig.clock() - timedelta(days=L.LEDGER_RETENTION_DAYS + 1)
    rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "device_unavailable"}}, old)

    rig.ledger.sweep(rig.clock())
    assert rig.ledger.op_status(oid) is not None


def test_review_and_sweep_keeps_an_unresolved_failed_ops_row_alive_while_its_artefact_is_held(
        rig, tmp_path):
    """TASK-277: the exact scenario TASK-265 diagnosed -- a send_gallery op fails, mints no
    client_msg_id, and nobody resolves it. Before this fix, ledger.sweep()'s age-only delete
    destroyed the phone_ops row (and with it, POST /v1/ops/<id>/resolve's own human escape hatch)
    while the screenshot review_and_sweep had already correctly held was still sitting on disk."""
    oid = op_id("0")
    enqueue(rig, op_id=oid, kind="send_gallery",
           args={"phone": PHONE, "local_paths": ["/x/a.jpg"], "caption": ""})
    old = rig.clock() - timedelta(days=L.LEDGER_RETENTION_DAYS + 1)
    rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "device_unavailable"}}, old)

    shot = tmp_path / f"{oid}_00_pre.png"
    shot.write_bytes(b"x")
    old_epoch = old.timestamp()
    os.utime(shot, (old_epoch, old_epoch))
    rig.driver.screenshot_candidates = [shot]

    result = RT.review_and_sweep(rig.executor, rig.clock(), screenshot_days=14, recording_days=14)
    assert result["screenshots"] == {"deleted": 0, "held": 1}
    assert rig.driver.deleted_paths == []
    assert rig.ledger.op_status(oid) is not None, \
        "the escape hatch must not close while the artefact is still there"

    # TASK-230's escape hatch still answers, 31 days in.
    rig.ledger.resolve_op(oid, rig.clock())
    assert rig.ledger.op_status(oid)["resolved_at"] is not None


def test_review_and_sweep_retires_a_failed_ops_row_once_nothing_on_disk_names_it(rig):
    """TASK-277's other half: a row with no live artefact -- never had one
    (WA_BRIDGE_DEBUG_CAPTURE=0), or lost its last one already -- must still be swept, or this fix
    would grow phone_ops forever for exactly the ops retention never needed to protect."""
    oid = op_id("1")
    enqueue(rig, op_id=oid, kind="clear_chat", args={"phone": PHONE, "confirm": True})
    old = rig.clock() - timedelta(days=L.LEDGER_RETENTION_DAYS + 1)
    rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "device_unavailable"}}, old)
    # No screenshot/recording candidates at all.

    RT.review_and_sweep(rig.executor, rig.clock(), screenshot_days=14, recording_days=14)
    assert rig.ledger.op_status(oid) is None


def test_sweep_never_deletes_an_unconfirmed_outbound_row(rig):
    """TASK-277: mark_unconfirmed routes through _resolve(), which sets resolved_at -- but
    UNCONFIRMED is not in SAFE_OUTBOUND_STATES (retention.py's own definition of "settled"), so
    sweep()'s outbound delete must never key on resolved_at alone: an unconfirmed send is still an
    open question only a reconcile can answer, not age."""
    rig.ledger.begin(KEY, phone=PHONE, kind="text", body_sha256="x", body_len=2, now=rig.clock())
    rig.ledger.mark_unconfirmed(KEY, rig.clock(), detail="wrong_thread shot=x")

    rig.ledger.sweep(rig.clock() + timedelta(days=L.LEDGER_RETENTION_DAYS + 1))

    entry = rig.ledger.get(KEY)
    assert entry is not None and entry.state == L.UNCONFIRMED


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


def test_a_failed_send_with_no_outbound_row_at_all_is_deletable(rig):
    """A refusal before the ledger's own begin() (a governor pacing 429, a lost flock race 503) --
    no row was ever written, which is provably "nothing was typed", the exact fact NOT_ATTEMPTED
    (a row that DOES exist) already carries and that this module already deletes on age alone
    (TASK-257). Held forever, before this fix: entry was None, and no age check ever ran for it."""
    oid = op_id("6")
    enqueue(rig, op_id=oid, kind="send",
           args={"req": {"client_msg_id": KEY, "to": PHONE, "kind": "text", "body": "hi"}})
    rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "rail_parked"}}, rig.clock())
    verdict, reason = RT.classify_op_artifact(rig.ledger, oid)
    assert verdict == "delete" and "refused before ledger.begin" in reason


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


def test_a_swept_escalation_shot_journal_row_is_held_with_the_aged_out_reason(rig, tmp_path):
    """TASK-265's other half: ledger.sweep() deletes journal rows past LEDGER_RETENTION_DAYS the
    same way it deletes phone_ops rows, so escalation_shot_index() loses the entry too. The
    artefact's own age has to tell that apart from a journal entry that was never written."""
    old = rig.clock() - timedelta(days=L.LEDGER_RETENTION_DAYS + 1)
    path = tmp_path / "old_escalation.png"
    path.write_bytes(b"x")
    old_epoch = old.timestamp()
    os.utime(path, (old_epoch, old_epoch))

    rig.ledger.begin(KEY, phone=PHONE, kind="text", body_sha256="x", body_len=2, now=old)
    rig.ledger.note(old, "escalation_shot", KEY, path=str(path))

    rig.ledger.sweep(rig.clock())
    index = rig.ledger.escalation_shot_index()
    assert str(path) not in index  # the journal row is really gone

    verdict, reason = RT.classify_escalation_shot(rig.ledger, path, index, rig.clock())
    assert verdict == "hold"
    assert "aged out past LEDGER_RETENTION_DAYS" in reason
    assert reason != "no escalation_shot journal entry for this file"


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

def test_review_and_sweep_deletes_happy_holds_the_rest_and_journals_every_hold(rig, tmp_path):
    done_id, held_id = op_id("d"), op_id("e")
    enqueue(rig, op_id=done_id, kind="read_thread", args={"phone": PHONE})
    rig.ledger.mark_op_done(done_id, {"count": 0}, rig.clock())
    enqueue(rig, op_id=held_id, kind="clear_chat", args={})
    rig.ledger.mark_op_failed(held_id, {"ok": False, "error": {"code": "x"}}, rig.clock())

    happy_shot = Path(f"/shots/{done_id}_00_pre.png")
    held_shot = Path(f"/shots/{held_id}_00_pre.png")
    # A real, freshly-written file (TASK-265: classify_escalation_shot now stats the artefact
    # itself to tell "aged out" apart from "genuinely missing" -- a fresh mtime keeps this the
    # "genuinely missing" case the test is about, not the aged-out one).
    mystery_shot = tmp_path / "20260101_001_old_style.png"   # no journal entry at all
    mystery_shot.write_bytes(b"x")
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


def test_review_and_sweep_deletes_an_artefact_from_a_send_that_never_reached_the_ledger(rig):
    """TASK-257 end to end: a rail_parked 429 (or a lost flock race 503) refuses the send before
    ledger.begin() ever runs, so no outbound row exists for its client_msg_id -- and the sweep,
    not just classify_op_artifact directly, has to actually delete the file once it is old enough."""
    oid = op_id("9")
    enqueue(rig, op_id=oid, kind="send",
           args={"req": {"client_msg_id": KEY, "to": PHONE, "kind": "text", "body": "hi"}})
    rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "rail_parked"}}, rig.clock())
    shot = Path(f"/shots/{oid}_00_pre.png")
    rig.driver.screenshot_candidates = [shot]

    result = RT.review_and_sweep(rig.executor, rig.clock(), screenshot_days=14, recording_days=14)
    assert result["screenshots"] == {"deleted": 1, "held": 0}
    assert rig.driver.deleted_paths == [shot]


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
                      "recordings": {"deleted": 0, "held": 0},
                      "oldest_held_age_sec": None}
    assert rig.driver.deleted_paths == []


def test_review_and_sweep_reports_the_oldest_held_artefacts_age_in_seconds(rig, tmp_path):
    """TASK-277: health must carry the age of the oldest held artefact, not just a count -- a
    "held: 1" an operator cannot tell a fresh failure from one three months stale."""
    oid = op_id("2")
    enqueue(rig, op_id=oid, kind="clear_chat", args={"phone": PHONE, "confirm": True})
    rig.ledger.mark_op_failed(oid, {"ok": False, "error": {"code": "x"}}, rig.clock())

    shot = tmp_path / f"{oid}_00_pre.png"
    shot.write_bytes(b"x")
    shot_age_sec = 20 * 86400
    old_epoch = rig.clock().timestamp() - shot_age_sec
    os.utime(shot, (old_epoch, old_epoch))
    rig.driver.screenshot_candidates = [shot]

    result = RT.review_and_sweep(rig.executor, rig.clock(), screenshot_days=14, recording_days=14)
    assert result["screenshots"] == {"deleted": 0, "held": 1}
    assert result["oldest_held_age_sec"] == pytest.approx(shot_age_sec, abs=2)


# --- ledger.sweep()'s media sweep (TASK-278): pulled inbound media -- rows and bytes -- was the
# one durable store with no sweep at all. An attached file's inbound row is swept
# INBOUND_RETENTION_DAYS after ack (ledger.py's own cut); once that row is gone the file has done
# its job (the VPS already holds its own permanent copy, app/wa/api.py::_write_original) and the
# media_seen/media_link rows naming it, plus the media_file row and the bytes under media/store,
# go with it. A pulled file NEVER attached to anyone is explicitly left alone -- how long an
# unclaimed pull should wait is a decision nobody has made yet, not one this sweep invents.

def test_maintenance_once_sweeps_an_attached_files_bytes_once_its_inbound_row_ages_out(rig, tmp_path):
    blob = tmp_path / "store" / "wab.m.aaaa"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"x")
    rig.ledger.record_media(source_rel="WhatsApp Documents/x.pdf", mtime=0, media_id="wab.m.aaaa",
                            sha256="a" * 64, local_path=str(blob), size=1, kind="document",
                            mime_type="application/pdf", filename="Lebenslauf.pdf", now=rig.clock())
    [queued] = rig.ledger.media_queue()
    inbound_key = rig.ledger.attach_media(queued["queue_id"], PHONE, now=rig.clock())
    inbound_id = rig.ledger._db.execute(
        "select id from inbound where inbound_key=?", (inbound_key,)).fetchone()["id"]
    rig.ledger.ack_inbound(inbound_id, rig.clock())

    later = rig.clock() + timedelta(days=L.INBOUND_RETENTION_DAYS + 1)
    result = SV.maintenance_once(rig.executor, later)

    assert result["ledger"]["media_seen"] == 1
    assert result["ledger"]["media_link"] == 1
    assert result["ledger"]["media_file"] == 1
    assert not blob.exists(), "the bytes must not outlive the inbound row they were linked to"
    assert rig.ledger.media_file("wab.m.aaaa") is None
    assert rig.ledger.media_queue_row(queued["queue_id"]) is None
    assert rig.ledger._db.execute(
        "select 1 from media_link where inbound_id=?", (inbound_key,)).fetchone() is None


def test_media_shared_by_a_still_unattached_duplicate_pull_keeps_its_bytes(rig, tmp_path):
    """media_file is content-addressed (bridge/media.py) -- two pulls of byte-identical content
    share one row. The attached pull's inbound row aging out must not take bytes a second,
    still-queued media_seen row is still allowed to point at."""
    blob = tmp_path / "store" / "wab.m.cccc"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"y")
    for source_rel in ("WhatsApp Documents/first.pdf", "WhatsApp Documents/second.pdf"):
        rig.ledger.record_media(source_rel=source_rel, mtime=0, media_id="wab.m.cccc",
                                sha256="c" * 64, local_path=str(blob), size=1, kind="document",
                                mime_type="application/pdf", filename="Lebenslauf.pdf",
                                now=rig.clock())
    first_queue_id = rig.ledger._db.execute(
        "select queue_id from media_seen where source_rel=?",
        ("WhatsApp Documents/first.pdf",)).fetchone()["queue_id"]
    inbound_key = rig.ledger.attach_media(first_queue_id, PHONE, now=rig.clock())
    inbound_id = rig.ledger._db.execute(
        "select id from inbound where inbound_key=?", (inbound_key,)).fetchone()["id"]
    rig.ledger.ack_inbound(inbound_id, rig.clock())

    later = rig.clock() + timedelta(days=L.INBOUND_RETENTION_DAYS + 1)
    SV.maintenance_once(rig.executor, later)

    assert blob.exists(), "the second pull is still unattached and still names this media_id"
    assert rig.ledger.media_file("wab.m.cccc") is not None


def test_an_unattached_pulled_files_bytes_are_never_swept(rig, tmp_path):
    """TASK-278's own open half: how long an unclaimed pull should wait is a decision nobody has
    made yet, so a media_seen row that was never attached survives no matter how old it is."""
    blob = tmp_path / "store" / "wab.m.bbbb"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"x")
    rig.ledger.record_media(source_rel="WhatsApp Documents/y.pdf", mtime=0, media_id="wab.m.bbbb",
                            sha256="b" * 64, local_path=str(blob), size=1, kind="document",
                            mime_type="application/pdf", filename="Anschreiben.pdf", now=rig.clock())

    later = rig.clock() + timedelta(days=L.INBOUND_RETENTION_DAYS + 1)
    result = SV.maintenance_once(rig.executor, later)

    assert result["ledger"]["media_seen"] == 0
    assert result["ledger"]["media_file"] == 0
    assert blob.exists()
    assert rig.ledger.media_file("wab.m.bbbb") is not None
