"""tests/fixtures/wa_pro_api/threads.json must validate against app/wa/pro_models.py exactly -- so
the fixture pflege-fe develops the Pro dashboard against can never silently drift from the contract
models this harness actually serializes (design decision 9)."""
import json
import pathlib

from app.wa import pro_models as M
from app.wa import store as ST

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "wa_pro_api" / "threads.json"


def _load():
    return json.loads(FIXTURE.read_text())


def test_fixture_file_exists_with_6_to_8_rows():
    data = _load()
    assert 6 <= len(data["rows"]) <= 8


def test_fixture_validates_as_a_threads_envelope():
    data = _load()
    envelope = M.ThreadsEnvelope.model_validate(data)
    assert envelope.total == data["total"]
    assert len(envelope.rows) == len(data["rows"])


def test_fixture_every_row_validates_individually():
    data = _load()
    for row in data["rows"]:
        M.ThreadRow.model_validate(row)


def test_fixture_covers_a_test_thread():
    data = _load()
    assert any(row["is_test"] for row in data["rows"])


def test_fixture_covers_an_escalated_thread_with_escalated_at():
    data = _load()
    assert any(row["escalated_at"] and row["escalation_codes"] for row in data["rows"])


def test_fixture_covers_a_stuck_reply_thread():
    data = _load()
    assert any(row["stuck_reply"] for row in data["rows"])


def test_fixture_covers_a_suppressed_stopped_thread():
    data = _load()
    assert any(row["stopped"] and row["suppression"] for row in data["rows"])


def test_fixture_covers_a_consented_lead_with_handoffs_in_several_statuses():
    """Review item 5: handoff.clinics is the matched-clinics COUNT (an int); the per-target list
    moved to its own key, handoff.targets."""
    data = _load()
    handoff_rows = [row for row in data["rows"] if row["handoff"]]
    assert handoff_rows
    assert all(isinstance(row["handoff"]["clinics"], int) for row in handoff_rows)
    statuses_seen = {clinic["status"] for row in handoff_rows for clinic in row["handoff"]["targets"]}
    assert len(statuses_seen) >= 2
    assert any(row["handoff"]["status"] == "attention" for row in handoff_rows)


def test_fixture_covers_a_deleted_message_tombstone():
    data = _load()
    assert any(row["last_message"] and row["last_message"]["kind"] == "deleted"
              and row["last_message"]["preview"] is None for row in data["rows"])


def test_fixture_no_raw_phone_digits_longer_than_masked_tail():
    """Every phone_masked value must use the bullet character for the hidden portion -- a quick
    regression guard that the fixture itself never leaked a full number."""
    data = _load()
    for row in data["rows"]:
        masked = row["phone_masked"]
        assert masked is None or "•" in masked


# --- tests/fixtures/wa_pro_api/activity.json (TASK-283.7) -------------------------------------------
ACTIVITY_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "wa_pro_api" / "activity.json"


def _load_activity():
    return json.loads(ACTIVITY_FIXTURE.read_text())


def test_activity_fixture_validates_as_an_activity_response():
    data = _load_activity()
    M.ActivityResponse.model_validate(data)


def test_activity_fixture_covers_the_tunnel_up_normal_state():
    """activity.json is the "normal state" fixture (TASK-283.7) -- the down variant moved to its
    own file, tests/fixtures/wa_pro_api/activity_tunnel_down.json, below."""
    data = _load_activity()
    assert data["rail"]["tunnel"]["up"] is True
    assert data["rail"]["tunnel"]["last_error"] is None


def test_activity_fixture_covers_an_overdue_job():
    data = _load_activity()
    assert any(job["overdue"] is True for job in data["jobs"])


def test_activity_fixture_covers_every_heartbeat_job_and_leaves_nudges_out():
    """store.HEARTBEAT_JOBS (5) + the 3 derived jobs (relay_sync, luna_reply, broadcasts) = 8 rows.
    "nudges" is a contract job key this harness deliberately never emits (see
    docs/wa-pro-activity.md) -- the fixture must not contradict that by inventing one."""
    data = _load_activity()
    job_keys = {job["job"] for job in data["jobs"]}
    assert job_keys == set(ST.HEARTBEAT_JOBS) | {"relay_sync", "luna_reply", "broadcasts"}
    assert "nudges" not in job_keys


def test_activity_fixture_covers_a_job_with_an_error():
    data = _load_activity()
    assert any(job["last_error"] for job in data["jobs"])


# --- tests/fixtures/wa_pro_api/activity_tunnel_down.json (TASK-283.7) --------------------------------
ACTIVITY_DOWN_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "wa_pro_api" / "activity_tunnel_down.json"


def _load_activity_down():
    return json.loads(ACTIVITY_DOWN_FIXTURE.read_text())


def test_activity_down_fixture_validates_as_an_activity_response():
    data = _load_activity_down()
    M.ActivityResponse.model_validate(data)


def test_activity_down_fixture_covers_a_tunnel_down_variant():
    data = _load_activity_down()
    assert data["rail"]["tunnel"]["up"] is False
    assert data["rail"]["tunnel"]["last_error"] is not None
    assert data["rail"]["tunnel"]["since"] is not None


def test_activity_down_fixture_snapshot_at_is_stale_not_missing():
    """write_rail_snapshot's own ok=False rule (its docstring): health/snapshot_at are left exactly
    as the last successful read, never blanked -- a dead bridge shows as an OLD snapshot_at, not a
    null one. Same snapshot_at as the normal-state fixture, generated_at strictly later."""
    down, normal = _load_activity_down(), _load_activity()
    assert down["snapshot_at"] == normal["snapshot_at"]
    assert down["generated_at"] > down["snapshot_at"]


# --- tests/fixtures/wa_pro_api/ops.json (TASK-283.7) -------------------------------------------------
OPS_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "wa_pro_api" / "ops.json"


def _load_ops():
    return json.loads(OPS_FIXTURE.read_text())


def test_ops_fixture_validates_as_an_ops_envelope():
    data = _load_ops()
    envelope = M.OpsEnvelope.model_validate(data)
    assert len(envelope.rows) == len(data["rows"])


def test_ops_fixture_every_row_validates_individually():
    data = _load_ops()
    for row in data["rows"]:
        M.OpRow.model_validate(row)


def test_ops_fixture_covers_every_status():
    data = _load_ops()
    assert {row["status"] for row in data["rows"]} == {"queued", "running", "done", "failed"}


def test_ops_fixture_covers_every_origin_a_code_path_really_sends_plus_pro_human():
    """Review finding 6's own repro ("ops.json:39 and :58 show broadcast and nudges, which no code
    path sends"): neither of those two ORIGIN_VALUES has a real phone_ops-emitting code path --
    bridge/ledger.py's own origin comment says ``followups`` already covers the tiered nudge sweep,
    and a broadcast run never touches phone_ops at all (its own ``/v1/broadcasts`` table) -- so the
    fixture no longer claims to cover them. ``pro_human`` stays (TASK-283.3's own write half is not
    built yet either, but the review did not flag this one, and it is this feature's own "human"
    queue bucket -- dropping it would leave that branch unexercised)."""
    data = _load_ops()
    origins = {row["origin"] for row in data["rows"]}
    assert origins == set(ST.ORIGIN_VALUES) - {"nudges", "broadcast"}
    assert "pro_human" in origins


def test_ops_fixture_covers_a_failed_op_with_an_error():
    data = _load_ops()
    failed = [row for row in data["rows"] if row["status"] == "failed"]
    assert failed
    assert all(row["error"] and row["error"]["code"] for row in failed)


def test_ops_fixture_attempts_always_null():
    """Contract: "attempts is null when the bridge does not track it" -- the bridge tracks no
    retry count at all, so this is never anything else."""
    data = _load_ops()
    assert all(row["attempts"] is None for row in data["rows"])


def test_ops_fixture_no_raw_phone_digits_longer_than_masked_tail():
    data = _load_ops()
    for row in data["rows"]:
        masked = row["phone_masked"]
        assert masked is None or "•" in masked


# --- tests/fixtures/wa_pro_api/ops_page2.json (TASK-283.7) -------------------------------------------
OPS_PAGE2_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "wa_pro_api" / "ops_page2.json"


def _load_ops_page2():
    return json.loads(OPS_PAGE2_FIXTURE.read_text())


def test_ops_page2_fixture_validates_as_an_ops_envelope():
    data = _load_ops_page2()
    envelope = M.OpsEnvelope.model_validate(data)
    assert len(envelope.rows) == len(data["rows"])


def test_ops_page2_fixture_is_the_page_after_ops_json():
    """Shows the cursor shape (contract wording): ops.json's own next_before_id is exactly this
    page's own before_id query, and this page is the last one -- nothing left to page to."""
    page1, page2 = _load_ops(), _load_ops_page2()
    assert page1["next_before_id"] is not None
    assert page2["next_before_id"] is None
    assert page2["rows"]
    assert all(row["id"] not in {r["id"] for r in page1["rows"]} for row in page2["rows"])
