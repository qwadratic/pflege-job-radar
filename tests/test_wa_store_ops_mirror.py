"""Offline tests for the new store.py primitives behind the Pro activity rail (TASK-283.7, plan
~/plans/2026-10-01-pro-activity-rail-view.md): the ops mirror (wa_ops_mirror), the rail snapshot
(wa_rail_snapshot) and job heartbeats (wa_job_runs, job_run())."""
from datetime import datetime, timedelta, timezone

import pytest

from app.wa import config as C
from app.wa import store as ST

PHONE = "+491701234599"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    conn = ST.db()
    yield conn
    conn.close()


def _op(op_id, position, *, state="queued", origin="luna", phone=PHONE, **extra):
    base = {"op_id": op_id, "position": position, "kind": "send", "origin": origin, "state": state,
            "priority": 0, "created_at": "2026-10-01T10:00:00+00:00", "started_at": None,
            "finished_at": None, "resolved_at": None, "budget_sec": 60.0, "phone": phone,
            "error_code": None, "error_text": None}
    base.update(extra)
    return base


# --- upsert_mirrored_op --------------------------------------------------------------------------

def test_upsert_mirrored_op_never_stores_the_raw_phone(db):
    ST.upsert_mirrored_op(db, _op("a", 1))
    row = db.execute("select * from wa_ops_mirror where op_id='a'").fetchone()
    dumped = " ".join(str(v) for v in dict(row).values())
    assert PHONE not in dumped
    assert PHONE[1:] not in dumped          # not even the digits without the leading +
    assert row["thread_id"] == ST.thread_id_for_phone(db, PHONE)
    assert row["phone_masked"].endswith(PHONE[-4:])
    assert "•" in row["phone_masked"]


def test_upsert_mirrored_op_with_no_phone_leaves_thread_fields_null(db):
    ST.upsert_mirrored_op(db, _op("bg", 1, phone=None, kind="reconcile"))
    row = db.execute("select * from wa_ops_mirror where op_id='bg'").fetchone()
    assert row["thread_id"] is None and row["phone_masked"] is None


def test_upsert_mirrored_op_folds_an_unrecognised_origin_to_unknown(db):
    ST.upsert_mirrored_op(db, _op("a", 1, origin="something_new"))
    row = db.execute("select origin from wa_ops_mirror where op_id='a'").fetchone()
    assert row["origin"] == "unknown"


def test_upsert_mirrored_op_is_a_replace_not_an_append(db):
    ST.upsert_mirrored_op(db, _op("a", 1, state="queued"))
    ST.upsert_mirrored_op(db, _op("a", 1, state="done", finished_at="2026-10-01T10:05:00+00:00"))
    rows = db.execute("select * from wa_ops_mirror where op_id='a'").fetchall()
    assert len(rows) == 1
    assert rows[0]["state"] == "done"
    assert rows[0]["finished_at"] == "2026-10-01T10:05:00+00:00"


# --- ops_mirror_max_position / ops_mirror_open_ids -----------------------------------------------

def test_ops_mirror_max_position_is_zero_when_empty(db):
    assert ST.ops_mirror_max_position(db) == 0


def test_ops_mirror_max_position_tracks_the_highest_row(db):
    ST.upsert_mirrored_op(db, _op("a", 1))
    ST.upsert_mirrored_op(db, _op("b", 9))
    ST.upsert_mirrored_op(db, _op("c", 4))
    assert ST.ops_mirror_max_position(db) == 9


def test_ops_mirror_open_ids_excludes_terminal_states(db):
    ST.upsert_mirrored_op(db, _op("q", 1, state="queued"))
    ST.upsert_mirrored_op(db, _op("r", 2, state="running"))
    ST.upsert_mirrored_op(db, _op("d", 3, state="done"))
    ST.upsert_mirrored_op(db, _op("f", 4, state="failed"))
    assert sorted(ST.ops_mirror_open_ids(db)) == ["q", "r"]


# --- ops_mirror_counts ----------------------------------------------------------------------------

def test_ops_mirror_counts_is_complete_and_defaults_known_states_to_zero(db):
    ST.upsert_mirrored_op(db, _op("a", 1, state="queued"))
    ST.upsert_mirrored_op(db, _op("b", 2, state="queued"))
    ST.upsert_mirrored_op(db, _op("c", 3, state="done"))
    assert ST.ops_mirror_counts(db) == {"queued": 2, "running": 0, "done": 1, "failed": 0}


def test_ops_mirror_counts_scoped_to_one_origin(db):
    ST.upsert_mirrored_op(db, _op("a", 1, origin="pro_human", state="queued"))
    ST.upsert_mirrored_op(db, _op("b", 2, origin="luna", state="queued"))
    assert ST.ops_mirror_counts(db, origin="pro_human") == {"queued": 1, "running": 0, "done": 0, "failed": 0}


# --- ops_mirror_page -------------------------------------------------------------------------------

def _seed_five(db):
    for i in range(1, 6):
        ST.upsert_mirrored_op(db, _op(f"op{i}", i))


def test_ops_mirror_page_no_cursor_is_newest_first(db):
    """Contract wording: "rows newest first" -- an activity feed, not a chat transcript (see
    ops_mirror_page's own docstring for why this differs from _messages_page's reversal)."""
    _seed_five(db)
    rows, next_before_id = ST.ops_mirror_page(db, limit=2)
    assert [r["op_id"] for r in rows] == ["op5", "op4"]
    assert next_before_id == 4


def test_ops_mirror_page_before_id_is_an_older_page_also_newest_first(db):
    _seed_five(db)
    rows, next_before_id = ST.ops_mirror_page(db, limit=2, before_id=4)
    assert [r["op_id"] for r in rows] == ["op3", "op2"]
    assert next_before_id == 2


def test_ops_mirror_page_after_id_is_everything_newer_ascending(db):
    _seed_five(db)
    rows, next_before_id = ST.ops_mirror_page(db, limit=50, after_id=3)
    assert [r["op_id"] for r in rows] == ["op4", "op5"]
    assert next_before_id is None


def test_ops_mirror_page_reaches_the_end_with_a_null_next_before_id(db):
    _seed_five(db)
    rows, next_before_id = ST.ops_mirror_page(db, limit=50)
    assert len(rows) == 5
    assert next_before_id is None


def test_ops_mirror_page_filters_by_status(db):
    ST.upsert_mirrored_op(db, _op("a", 1, state="queued"))
    ST.upsert_mirrored_op(db, _op("b", 2, state="failed"))
    rows, _ = ST.ops_mirror_page(db, limit=50, status="failed")
    assert [r["op_id"] for r in rows] == ["b"]


def test_ops_mirror_page_filters_by_a_single_origin_or_a_list(db):
    ST.upsert_mirrored_op(db, _op("a", 1, origin="pro_human"))
    ST.upsert_mirrored_op(db, _op("b", 2, origin="luna"))
    ST.upsert_mirrored_op(db, _op("c", 3, origin="catchup"))
    rows, _ = ST.ops_mirror_page(db, limit=50, origin="pro_human")
    assert [r["op_id"] for r in rows] == ["a"]
    rows, _ = ST.ops_mirror_page(db, limit=50, origin=["luna", "catchup"])
    assert sorted(r["op_id"] for r in rows) == ["b", "c"]


# --- job_run / job_run_summary ---------------------------------------------------------------------

def test_job_run_records_a_clean_success_by_default(db):
    with ST.job_run("catchup") as jr:
        pass
    summary = ST.job_run_summary(db, "catchup")
    assert summary["last_run_at"] is not None
    assert summary["last_ok_at"] == summary["last_run_at"]
    assert summary["last_error"] is None
    assert summary["ok_24h"] == 1 and summary["failed_24h"] == 0


def test_job_run_records_an_explicit_failure_without_raising(db):
    with ST.job_run("catchup") as jr:
        jr.ok = False
        jr.error_code = "partial_failure"
        jr.error_text = "2 of 5 failed"
        jr.counts = {"attempted": 5, "errors": 2}
    summary = ST.job_run_summary(db, "catchup")
    assert summary["last_ok_at"] is None
    assert summary["last_error"] == {"code": "partial_failure", "text": "2 of 5 failed"}


def test_job_run_records_an_uncaught_exception_and_still_re_raises(db):
    with pytest.raises(RuntimeError):
        with ST.job_run("catchup"):
            raise RuntimeError("boom")
    summary = ST.job_run_summary(db, "catchup")
    assert summary["last_error"] == {"code": "RuntimeError", "text": "boom"}


def test_job_run_summary_last_error_clears_after_a_later_success(db):
    with ST.job_run("catchup") as jr:
        jr.ok = False
        jr.error_code = "x"
        jr.error_text = "y"
    with ST.job_run("catchup"):
        pass
    summary = ST.job_run_summary(db, "catchup")
    assert summary["last_error"] is None


def test_job_run_summary_is_all_null_before_any_run(db):
    assert ST.job_run_summary(db, "catchup") == {
        "last_run_at": None, "last_ok_at": None, "last_error": None, "ok_24h": 0, "failed_24h": 0}


def test_job_run_summary_ok_24h_excludes_runs_older_than_a_day(db):
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    ST.record_job_run(db, "catchup", old, old, True, {})
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    ST.record_job_run(db, "catchup", recent, recent, True, {})
    summary = ST.job_run_summary(db, "catchup")
    assert summary["ok_24h"] == 1


def test_job_run_scoped_to_its_own_job_key(db):
    with ST.job_run("catchup"):
        pass
    assert ST.job_run_summary(db, "followups")["last_run_at"] is None


# --- write_rail_snapshot / rail_snapshot -----------------------------------------------------------

def test_rail_snapshot_is_none_before_anything_is_written(db):
    assert ST.rail_snapshot(db) is None


def test_write_rail_snapshot_ok_stores_health_and_phone_state(db):
    ST.write_rail_snapshot(db, ok=True, health={"watcher": {"alive": True}}, tunnel_up=True,
                           phone_state="ready")
    snap = ST.rail_snapshot(db)
    assert snap["health"] == {"watcher": {"alive": True}}
    assert snap["tunnel_up"] == 1
    assert snap["phone_state"] == "ready"
    assert snap["last_error_code"] is None


def test_write_rail_snapshot_since_only_moves_on_a_transition(db, monkeypatch):
    times = iter(["2026-10-01T10:00:00+00:00", "2026-10-01T10:00:03+00:00",
                  "2026-10-01T10:00:06+00:00"])
    monkeypatch.setattr(ST, "now_iso", lambda: next(times))
    ST.write_rail_snapshot(db, ok=True, health={}, tunnel_up=True, phone_state="ready")
    since_1 = ST.rail_snapshot(db)["phone_state_since"]
    ST.write_rail_snapshot(db, ok=True, health={}, tunnel_up=True, phone_state="ready")
    since_2 = ST.rail_snapshot(db)["phone_state_since"]
    assert since_1 == since_2   # unchanged -- same state as before, even though the clock moved
    ST.write_rail_snapshot(db, ok=True, health={}, tunnel_up=True, phone_state="blocked")
    since_3 = ST.rail_snapshot(db)["phone_state_since"]
    assert since_3 != since_1   # a real transition moved it
    assert since_3 == "2026-10-01T10:00:06+00:00"


def test_write_rail_snapshot_failure_keeps_the_last_health_and_phone_state(db):
    ST.write_rail_snapshot(db, ok=True, health={"watcher": {"alive": True}}, tunnel_up=True,
                           phone_state="ready")
    ST.write_rail_snapshot(db, ok=False, error="connection refused", error_code="ConnectionRefusedError",
                           tunnel_up=False)
    snap = ST.rail_snapshot(db)
    assert snap["health"] == {"watcher": {"alive": True}}   # never blanked
    assert snap["phone_state"] == "ready"                   # never blanked
    assert snap["tunnel_up"] == 0                            # this DID move
    assert snap["last_error_code"] == "ConnectionRefusedError"
    assert snap["last_error_text"] == "connection refused"


# --- luna_reply_job_summary (derived, no heartbeat row) --------------------------------------------

def test_luna_reply_job_summary_with_no_data_is_all_null(db):
    assert ST.luna_reply_job_summary(db) == {
        "last_run_at": None, "last_ok_at": None, "last_error": None, "ok_24h": 0, "failed_24h": 0}


def test_luna_reply_job_summary_counts_calls_and_send_failures(db):
    ST.record_luna_call(db, PHONE)
    ST.record_send_failure(db, PHONE, "template not approved")
    summary = ST.luna_reply_job_summary(db)
    assert summary["ok_24h"] == 1
    assert summary["failed_24h"] == 1
    assert summary["last_error"] == {"code": "send_failed", "text": "template not approved"}


# --- the shared enums themselves --------------------------------------------------------------------

def test_origin_values_has_exactly_the_12_contract_values():
    assert ST.ORIGIN_VALUES == ("luna", "luna_tool", "followups", "nudges", "catchup", "campaign",
                                "broadcast", "operator", "agent_notes", "bridge", "pro_human", "unknown")


def test_heartbeat_jobs_excludes_nudges_and_the_3_derived_jobs():
    assert set(ST.HEARTBEAT_JOBS) == {"catchup", "followups", "tunnel_watch", "purge_test", "agent_notes"}
