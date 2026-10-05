"""Offline tests for the new store.py primitives behind the Pro activity rail (TASK-283.7, plan
~/plans/2026-10-01-pro-activity-rail-view.md): the ops mirror (wa_ops_mirror), the rail snapshot
(wa_rail_snapshot) and job heartbeats (wa_job_state, job_run()) -- the last of these migrated off
an earlier append-only log table, wa_job_runs, on 2026-10-05; its own migration gets a dedicated
test module, tests/test_wa_job_state_migration.py, not this file."""
import json
import os
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
    # review finding 7: the mirror looks a thread up, it never creates one just to have an id --
    # no thread exists yet for PHONE, so thread_id stays null rather than silently minting one.
    assert row["thread_id"] is None
    assert row["phone_masked"].endswith(PHONE[-4:])
    assert "•" in row["phone_masked"]


def test_upsert_mirrored_op_finds_an_existing_thread_without_inserting(db):
    """Review finding 7, the other half: when a thread already exists (e.g. from a prior inbound
    message), the mirror does look it up -- it just never creates one on its own."""
    ST.thread(db, PHONE)   # the engine mints a thread the normal way (e.g. a prior inbound message)
    thread_id = ST.thread_id_for_phone(db, PHONE)
    ST.upsert_mirrored_op(db, _op("a", 1))
    row = db.execute("select * from wa_ops_mirror where op_id='a'").fetchone()
    assert row["thread_id"] == thread_id


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
    assert ST.ops_mirror_counts(db) == {"queued": 2, "running": 0, "done": 1, "failed": 0,
                                         "other": {}}


def test_ops_mirror_counts_scoped_to_one_origin(db):
    ST.upsert_mirrored_op(db, _op("a", 1, origin="pro_human", state="queued"))
    ST.upsert_mirrored_op(db, _op("b", 2, origin="luna", state="queued"))
    assert ST.ops_mirror_counts(db, origin="pro_human") == {"queued": 1, "running": 0, "done": 0,
                                                             "failed": 0, "other": {}}


def test_ops_mirror_counts_buckets_an_unrecognised_state_under_other(db):
    """Review finding 6: a state this mirror has not seen before is counted under "other", never
    added as a new top-level key -- QueueCounts is extra="forbid", so a new top-level key would
    500 GET /api/wa/pro/activity the moment the bridge ships one new state."""
    ST.upsert_mirrored_op(db, _op("a", 1, state="queued"))
    db.execute("update wa_ops_mirror set state='cancelled' where op_id='a'")
    db.commit()
    assert ST.ops_mirror_counts(db) == {"queued": 0, "running": 0, "done": 0, "failed": 0,
                                         "other": {"cancelled": 1}}


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


# --- job_run / job_run_summary (wa_job_state, TASK-283.7's 2026-10-05 migration off the old
# append-only wa_job_runs log -- one row per job, not one row per run; see store.py's own SCHEMA
# comment and job_run()'s docstring) ------------------------------------------------------------

def test_job_run_records_a_clean_success_by_default(db):
    with ST.job_run("catchup") as jr:
        pass
    summary = ST.job_run_summary(db, "catchup")
    assert summary["last_run_at"] is not None
    assert summary["last_ok_at"] == summary["last_run_at"]
    assert summary["last_error"] is None
    assert summary["ok_24h"] == 1 and summary["failed_24h"] == 0
    assert summary["running_since"] is None   # cleared at finish, not left set


def test_job_run_sets_running_since_while_the_block_runs_and_clears_it_at_finish(db):
    """TASK-283.7, 2026-10-05: running_since/running_pid are visible on the one wa_job_state row
    WHILE the wrapped block runs, and cleared the moment it finishes cleanly."""
    with ST.job_run("catchup"):
        row = db.execute(
            "select running_since, running_pid from wa_job_state where job='catchup'").fetchone()
        assert row["running_since"] is not None
        assert row["running_pid"] == os.getpid()
    row = db.execute("select running_since, running_pid from wa_job_state where job='catchup'").fetchone()
    assert row["running_since"] is None and row["running_pid"] is None


def test_job_run_records_an_explicit_failure_without_raising(db):
    with ST.job_run("catchup") as jr:
        jr.ok = False
        jr.error_code = "partial_failure"
        jr.counts = {"attempted": 5, "errors": 2}
    summary = ST.job_run_summary(db, "catchup")
    assert summary["last_ok_at"] is None
    assert summary["last_error"] == {"code": "partial_failure"}


def test_job_run_records_an_uncaught_exception_and_still_re_raises(db):
    with pytest.raises(RuntimeError):
        with ST.job_run("catchup"):
            raise RuntimeError("boom")
    summary = ST.job_run_summary(db, "catchup")
    assert summary["last_error"] == {"code": "RuntimeError"}


def test_job_run_leaves_running_since_set_when_ok_is_false_with_no_code(db):
    """Review finding 1 (BLOCKER, still applies): a clean exit with rec.ok False and no
    rec.error_code is a caller bug -- job_run() raises instead of writing a row ErrorInfo would
    reject. job_run()'s own docstring is explicit that this means the finish write (and so the
    running_since/running_pid clear) never happens either -- the row is left showing a run that
    started but never cleanly finished, which this asserts directly."""
    with pytest.raises(RuntimeError, match="no rec.error_code"):
        with ST.job_run("catchup") as jr:
            jr.ok = False
    row = db.execute("select running_since, running_pid from wa_job_state where job='catchup'").fetchone()
    assert row["running_since"] is not None and row["running_pid"] == os.getpid()


def test_job_run_error_code_is_only_the_exception_class_name_never_str_exc(db):
    """NIT-1, 10-05 review: an uncaught exception's own str() can carry a raw phone (the same class
    of leak review finding 2 fixed for error_code) -- last_error_code must never store it. The
    class name only; the full traceback stays in the job's own stderr/journal."""
    with pytest.raises(RuntimeError):
        with ST.job_run("catchup"):
            raise RuntimeError(f"could not reach the chat for {PHONE}")
    row = db.execute("select last_error_code from wa_job_state where job='catchup'").fetchone()
    assert row["last_error_code"] == "RuntimeError"
    assert PHONE not in row["last_error_code"]


def test_job_run_summary_last_error_clears_after_a_later_success(db):
    with ST.job_run("catchup") as jr:
        jr.ok = False
        jr.error_code = "x"
    with ST.job_run("catchup"):
        pass
    summary = ST.job_run_summary(db, "catchup")
    assert summary["last_error"] is None


def test_job_run_summary_is_all_null_before_any_run(db):
    assert ST.job_run_summary(db, "catchup") == {
        "last_run_at": None, "last_ok_at": None, "last_error": None, "ok_24h": 0, "failed_24h": 0,
        "running_since": None}


def test_job_run_summary_ok_24h_excludes_runs_older_than_a_day(db):
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    ST.record_job_run(db, "catchup", old, old, True)
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    ST.record_job_run(db, "catchup", recent, recent, True)
    summary = ST.job_run_summary(db, "catchup")
    assert summary["ok_24h"] == 1


def test_job_run_scoped_to_its_own_job_key(db):
    with ST.job_run("catchup"):
        pass
    assert ST.job_run_summary(db, "followups")["last_run_at"] is None


def test_job_run_finish_does_not_erase_an_overlapping_runs_running_state(db):
    """record_job_finished's own docstring, the review requirement this guards: an overlapping run
    of the SAME job (its own start having overwritten running_pid) must not have ITS still-live
    running state erased by a DIFFERENT run's finish. Simulated directly against the two low-level
    writers job_run() itself uses -- no real second process needed: "process" A starts, "process" B
    starts (overwriting running_pid on the one row), then A finishes -- B's own running_since/
    running_pid must survive A's finish untouched."""
    ST.record_job_started(db, "catchup", started_at="2026-10-05T10:00:00+00:00", pid=111)
    ST.record_job_started(db, "catchup", started_at="2026-10-05T10:00:05+00:00", pid=222)
    ST.record_job_finished(db, "catchup", 111, True, "2026-10-05T10:00:00+00:00",
                           "2026-10-05T10:00:10+00:00")
    row = db.execute("select running_since, running_pid from wa_job_state where job='catchup'").fetchone()
    assert row["running_pid"] == 222
    assert row["running_since"] == "2026-10-05T10:00:05+00:00"


def test_job_run_buckets_prune_hours_older_than_24h_as_of_the_run_that_just_finished(db):
    """buckets_json holds only the trailing 24h as of each WRITE's own finished_at (not the real
    wall clock) -- see record_job_finished's own docstring for why that is what lets a sequence of
    historical record_job_run calls (tools/wa_pro_fixtures.py) replay correctly."""
    old, recent = "2026-10-01T00:00:00+00:00", "2026-10-05T00:00:00+00:00"
    ST.record_job_run(db, "catchup", old, old, True)
    ST.record_job_run(db, "catchup", recent, recent, True)
    buckets = json.loads(
        db.execute("select buckets_json from wa_job_state where job='catchup'").fetchone()["buckets_json"])
    assert "2026-10-01T00:00:00+00:00" not in buckets
    assert "2026-10-05T00:00:00+00:00" in buckets


def test_job_run_buckets_collapse_two_runs_in_the_same_utc_hour(db):
    """Two runs in the same UTC hour collapse into one bucket key (_job_state_hour_key) -- picked
    relative to the real clock (never a fixed calendar date) so this keeps passing regardless of
    what day it runs on, and so job_run_summary's OWN read-side 24h window (real
    datetime.now(timezone.utc), see its docstring) always includes both."""
    hour_start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    t1, t2 = (hour_start + timedelta(minutes=1)).isoformat(), (hour_start + timedelta(minutes=2)).isoformat()
    ST.record_job_run(db, "catchup", t1, t1, True)
    ST.record_job_run(db, "catchup", t2, t2, False, error_code="messages_errored")
    buckets = json.loads(
        db.execute("select buckets_json from wa_job_state where job='catchup'").fetchone()["buckets_json"])
    assert buckets == {hour_start.isoformat(): [1, 1]}
    summary = ST.job_run_summary(db, "catchup")
    assert summary["ok_24h"] == 1 and summary["failed_24h"] == 1


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
    assert summary["last_error"] == {"code": "send_failed"}


# --- review finding 2 (BLOCKER): new code reading a row the OLD (pre-fix) 283.7 code wrote ---------
# wa_ops_mirror's own schema never changed (every column below already existed; this fix pass added
# no ALTER) -- so the one real compatibility question is a *data* one: data/wa.sqlite on the box
# already has wa_ops_mirror rows the OLD code wrote, with error_text genuinely populated (word for
# word, per review finding 2(a)/(b)) before this fix pass ever ran. This test writes such a row with
# a raw INSERT -- deliberately bypassing upsert_mirrored_op, which now always writes error_text as
# NULL -- to prove the NEW read side (ops_mirror_page, exercised directly here; the full HTTP path is
# covered separately in tests/test_wa_pro_activity.py) works unchanged against that
# already-unmigrated-in-place data: no crash, and the leaked text never resurfaces through a
# code-only read. CLAUDE.md: never touch real data/wa.sqlite -- this is its own disposable tmp_path
# db, built with the CURRENT schema (the only one there is) and then seeded exactly how an OLD binary
# would have left it. (wa_job_runs itself -- a table, not a column -- did change, on 2026-10-05, into
# wa_job_state; that migration, including an old ok=0/no-code row exactly like the one this comment
# used to describe, gets its own dedicated test module, tests/test_wa_job_state_migration.py.)

def test_ops_mirror_page_reads_an_old_pre_fix_row_without_crashing_or_leaking(db):
    db.execute(
        "insert into wa_ops_mirror (op_id, position, kind, origin, state, priority, created_at, "
        "started_at, finished_at, resolved_at, budget_sec, thread_id, phone_masked, error_code, "
        "error_text, mirrored_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("old1", 1, "send", "luna", "failed", 0, "2026-09-15T10:00:00+00:00", None,
         "2026-09-15T10:00:05+00:00", None, 60.0, None, "•••• 8877", "op_failed",
         f"no row in WhatsApp's own share picker matches {PHONE}",   # the OLD code's own verbatim write
         "2026-09-15T10:00:05+00:00"))
    db.commit()

    rows, _ = ST.ops_mirror_page(db, limit=10)
    assert len(rows) == 1
    assert rows[0]["op_id"] == "old1"
    assert rows[0]["error_code"] == "op_failed"
    # the raw column read back still carries the old text (this is a row store.py only READS here,
    # never rewrites) -- the discipline is that nothing in the Pro API path ever surfaces it, which
    # pro_api.py's own _error_info(code)-only (single-arg) signature enforces independent of what
    # this column happens to hold, old row or new.
    assert rows[0]["error_text"] == f"no row in WhatsApp's own share picker matches {PHONE}"


def test_luna_reply_job_summary_reads_an_old_pre_fix_send_failure_without_leaking(db):
    """wa_send_failures.error is populated by app/wa/api.py's own str(exc) and was never touched by
    this fix pass (it is the one durable log of a send failure, by design) -- the fix is that
    luna_reply_job_summary never reads that column's text back out, old row or new."""
    freeform_text = f"the WhatsApp free-form window closed for {PHONE} (last inbound message is over 72h old)"
    ST.record_luna_call(db, PHONE)
    db.execute("insert into wa_send_failures (phone, error, at) values (?,?,?)",
              (PHONE, freeform_text, ST.now_iso()))
    db.commit()

    summary = ST.luna_reply_job_summary(db)
    assert summary["last_error"] == {"code": "send_failed"}
    assert PHONE not in str(summary)


# --- the shared enums themselves --------------------------------------------------------------------

def test_origin_values_has_exactly_the_12_contract_values():
    assert ST.ORIGIN_VALUES == ("luna", "luna_tool", "followups", "nudges", "catchup", "campaign",
                                "broadcast", "operator", "agent_notes", "bridge", "pro_human", "unknown")


def test_heartbeat_jobs_excludes_nudges_and_the_3_derived_jobs():
    assert set(ST.HEARTBEAT_JOBS) == {"catchup", "followups", "tunnel_watch", "purge_test", "agent_notes"}
