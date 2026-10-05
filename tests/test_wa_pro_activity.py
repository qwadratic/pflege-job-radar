"""Tests for GET /api/wa/pro/activity and GET /api/wa/pro/ops (TASK-283.7, builder B's half of
~/plans/2026-10-01-pro-activity-rail-view.md). Same offline pattern as tests/test_wa_pro_api.py: one
tmp sqlite per test, both pro tokens set, asgi's startup hook creates the schema."""
import re
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import data as D
from app.wa import asgi
from app.wa import config as C
from app.wa import pro_models as M
from app.wa import store as ST

READ_TOKEN = "test-read-token"
RH = {"Authorization": f"Bearer {READ_TOKEN}"}
REAL_PHONE = "+491709998877"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setenv("WA_API_TOKEN", READ_TOKEN)
    monkeypatch.delenv("WA_API_WRITE_TOKEN", raising=False)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    return tmp_path


@pytest.fixture()
def client(env):
    with TestClient(asgi.app) as c:
        yield c


def _op(op_id, position, *, state="queued", origin="luna", phone=REAL_PHONE, **extra):
    base = {"op_id": op_id, "position": position, "kind": "send", "origin": origin, "state": state,
            "priority": 0, "created_at": "2026-10-01T10:00:00+00:00", "started_at": None,
            "finished_at": None, "resolved_at": None, "budget_sec": 60.0, "phone": phone,
            "error_code": None, "error_text": None}
    base.update(extra)
    return base


# --- auth --------------------------------------------------------------------------------------

def test_activity_401_without_token(client):
    assert client.get("/api/wa/pro/activity").status_code == 401


def test_ops_401_without_token(client):
    assert client.get("/api/wa/pro/ops").status_code == 401


def test_activity_503_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.delenv("WA_API_TOKEN", raising=False)
    monkeypatch.delenv("WA_API_WRITE_TOKEN", raising=False)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    r = TestClient(asgi.app).get("/api/wa/pro/activity", headers=RH)
    assert r.status_code == 503


# --- GET /api/wa/pro/activity --------------------------------------------------------------------

def test_activity_ok_and_validates_before_anything_has_ever_run(client):
    """A fresh database: no snapshot, no job runs at all yet -- every freshness field is null, not
    invented, and the response still validates (CLAUDE.md: no silent fallbacks)."""
    r = client.get("/api/wa/pro/activity", headers=RH)
    assert r.status_code == 200
    body = r.json()
    M.ActivityResponse.model_validate(body)
    assert body["snapshot_at"] is None
    assert body["synced_at"] is None
    assert body["rail"]["tunnel"]["up"] is None
    assert body["rail"]["phone"]["state"] == "unknown"
    assert body["queue"] == {"queued": 0, "running": 0, "done": 0, "failed": 0, "other": {},
                              "as_of": None}
    assert body["human"] == {"queued": 0, "running": 0, "done": 0, "failed": 0, "other": {},
                              "as_of": None}


def test_activity_jobs_list_has_exactly_8_rows_and_excludes_nudges(client):
    body = client.get("/api/wa/pro/activity", headers=RH).json()
    keys = {j["job"] for j in body["jobs"]}
    assert keys == set(ST.HEARTBEAT_JOBS) | {"relay_sync", "luna_reply", "broadcasts"}
    assert "nudges" not in keys


def test_activity_reflects_a_recorded_job_run(client):
    with ST.db() as c:
        with ST.job_run(ST.JOB_CATCHUP) as jr:
            jr.counts = {"attempted": 3}
    body = client.get("/api/wa/pro/activity", headers=RH).json()
    catchup = next(j for j in body["jobs"] if j["job"] == "catchup")
    assert catchup["last_run_at"] is not None
    assert catchup["last_ok_at"] == catchup["last_run_at"]
    assert catchup["enabled"] is True


def test_activity_200_on_a_legacy_job_run_row_with_no_code(client):
    """MINOR-1, 10-05 review: prod wa_job_runs holds ~186 pre-fix tunnel_watch rows with ok=0 and
    no error_code at all (from before job_run() required every ok=False exit to name one). The
    startup migration (store._backfill_legacy_job_run_error_codes, a _migrate() step) gives every
    such row the explicit code "legacy_unrecorded" the next time ANY process opens ST.db() -- in
    prod that is simply the next cron tick's own db() call; simulated here the same way: insert
    the old-shaped row directly, then open ST.db() again (the self-heal), then read it back
    through the real endpoint, as this job's newest row."""
    with ST.db() as c:
        c.execute(
            "insert into wa_job_runs (job, started_at, finished_at, ok, counts_json, error_code, "
            "error_text) values (?,?,?,?,?,?,?)",
            ("tunnel_watch", "2026-09-15T10:00:00+00:00", "2026-09-15T10:00:00+00:00", 0, "{}",
             None, None))
        c.commit()
    ST.db()  # the self-heal every later db() open performs

    resp = client.get("/api/wa/pro/activity", headers=RH)
    assert resp.status_code == 200
    tunnel_watch = next(j for j in resp.json()["jobs"] if j["job"] == "tunnel_watch")
    assert tunnel_watch["last_error"] == {"code": "legacy_unrecorded"}


def test_activity_agent_notes_has_no_cadence_even_after_a_recorded_run(client):
    """MAJOR-1, 10-05 review: tools/agent_note_cron.sh's own */5 line is the cron tick, not this
    job's real cadence -- it exits before Python starts on most ticks, and none of those gated
    exits ever write a heartbeat. A real worker run DOES write one (job_run() wraps main()), but
    next_run_at/overdue must still read null/null -- there is no real interval to compute either
    from, unlike every job that actually has a systemd timer."""
    with ST.job_run(ST.JOB_AGENT_NOTES) as jr:
        jr.counts = {"notes": 1}
    body = client.get("/api/wa/pro/activity", headers=RH).json()
    agent_notes = next(j for j in body["jobs"] if j["job"] == "agent_notes")
    assert agent_notes["last_run_at"] is not None
    assert agent_notes["next_run_at"] is None
    assert agent_notes["overdue"] is None


def test_activity_reflects_the_ops_mirror_counts_including_human(client):
    with ST.db() as c:
        ST.upsert_mirrored_op(c, _op("a", 1, state="queued", origin="luna"))
        ST.upsert_mirrored_op(c, _op("b", 2, state="done", origin="pro_human"))
    body = client.get("/api/wa/pro/activity", headers=RH).json()
    assert body["queue"] == {"queued": 1, "running": 0, "done": 1, "failed": 0, "other": {},
                              "as_of": None}
    assert body["human"] == {"queued": 0, "running": 0, "done": 1, "failed": 0, "other": {},
                              "as_of": None}


def test_activity_reflects_a_tunnel_down_snapshot(client):
    with ST.db() as c:
        ST.write_rail_snapshot(c, ok=False, error="connection refused",
                               error_code="ConnectionRefusedError", tunnel_up=False)
    body = client.get("/api/wa/pro/activity", headers=RH).json()
    assert body["rail"]["tunnel"]["up"] is False
    assert body["rail"]["tunnel"]["last_error"] == {"code": "ConnectionRefusedError"}


def test_activity_never_blocks_behind_st_lock(client):
    release = threading.Event()
    holder_ready = threading.Event()

    def _hold_lock():
        with ST._lock:
            holder_ready.set()
            release.wait(timeout=5)

    t = threading.Thread(target=_hold_lock, daemon=True)
    t.start()
    try:
        assert holder_ready.wait(timeout=5)
        started = time.monotonic()
        r = client.get("/api/wa/pro/activity", headers=RH)
        elapsed = time.monotonic() - started
        assert r.status_code == 200
        assert not release.is_set()
        assert elapsed < 2.0
    finally:
        release.set()
        t.join(timeout=5)


# --- GET /api/wa/pro/ops --------------------------------------------------------------------------

def _seed_ops(client):
    with ST.db() as c:
        ST.upsert_mirrored_op(c, _op("op1", 1, state="done", origin="luna"))
        ST.upsert_mirrored_op(c, _op("op2", 2, state="queued", origin="pro_human"))
        ST.upsert_mirrored_op(c, _op("op3", 3, state="failed", origin="catchup",
                                     error_code="op_expired", error_text="no ack"))
        ST.upsert_mirrored_op(c, _op("op4", 4, state="running", origin="bridge", phone=None))
        ST.upsert_mirrored_op(c, _op("op5", 5, state="queued", origin="followups"))


def test_ops_ok_and_validates(client):
    _seed_ops(client)
    r = client.get("/api/wa/pro/ops", headers=RH)
    assert r.status_code == 200
    M.OpsEnvelope.model_validate(r.json())


def test_ops_default_order_is_newest_first(client):
    _seed_ops(client)
    rows = client.get("/api/wa/pro/ops", headers=RH).json()["rows"]
    assert [r["id"] for r in rows] == ["op5", "op4", "op3", "op2", "op1"]


def test_ops_before_id_and_after_id_page_correctly(client):
    _seed_ops(client)
    full = client.get("/api/wa/pro/ops?limit=10", headers=RH).json()["rows"]
    mid = full[2]  # op3, position 3
    older = client.get(f"/api/wa/pro/ops?before_id=3&limit=10", headers=RH).json()["rows"]
    assert [r["id"] for r in older] == ["op2", "op1"]
    newer = client.get("/api/wa/pro/ops?after_id=3", headers=RH).json()["rows"]
    assert [r["id"] for r in newer] == ["op4", "op5"]
    assert mid["id"] == "op3"


def test_ops_before_id_and_after_id_together_is_400(client):
    r = client.get("/api/wa/pro/ops?before_id=1&after_id=1", headers=RH)
    assert r.status_code == 400


def test_ops_non_positive_limit_is_400(client):
    assert client.get("/api/wa/pro/ops?limit=0", headers=RH).status_code == 400
    assert client.get("/api/wa/pro/ops?limit=-1", headers=RH).status_code == 400


def test_ops_filters_by_status(client):
    _seed_ops(client)
    rows = client.get("/api/wa/pro/ops?status=failed", headers=RH).json()["rows"]
    assert [r["id"] for r in rows] == ["op3"]


def test_ops_origin_auto_excludes_pro_human(client):
    _seed_ops(client)
    rows = client.get("/api/wa/pro/ops?origin=auto", headers=RH).json()["rows"]
    assert "op2" not in [r["id"] for r in rows]
    assert {r["id"] for r in rows} == {"op1", "op3", "op4", "op5"}


def test_ops_origin_pro_is_exactly_pro_human(client):
    _seed_ops(client)
    rows = client.get("/api/wa/pro/ops?origin=pro", headers=RH).json()["rows"]
    assert [r["id"] for r in rows] == ["op2"]


def test_ops_origin_exact_value(client):
    _seed_ops(client)
    rows = client.get("/api/wa/pro/ops?origin=catchup", headers=RH).json()["rows"]
    assert [r["id"] for r in rows] == ["op3"]


def test_ops_origin_invalid_value_is_400(client):
    r = client.get("/api/wa/pro/ops?origin=not_a_real_origin", headers=RH)
    assert r.status_code == 400


def test_ops_failed_row_carries_its_error(client):
    _seed_ops(client)
    rows = client.get("/api/wa/pro/ops?status=failed", headers=RH).json()["rows"]
    assert rows[0]["error"] == {"code": "op_expired"}


def test_ops_attempts_always_null(client):
    _seed_ops(client)
    rows = client.get("/api/wa/pro/ops", headers=RH).json()["rows"]
    assert all(r["attempts"] is None for r in rows)


def test_ops_background_op_with_no_phone_has_null_thread_and_phone(client):
    _seed_ops(client)
    rows = client.get("/api/wa/pro/ops", headers=RH).json()["rows"]
    bg = next(r for r in rows if r["id"] == "op4")
    assert bg["thread_id"] is None and bg["phone_masked"] is None


def test_ops_never_leaks_a_raw_phone_or_wamid(client):
    """Seed a real-looking phone through the mirror and assert no 5+ digit run of it, nor any
    wamid-shaped string, survives into the response body (review items 1/2/6's own discipline,
    applied to this new endpoint)."""
    with ST.db() as c:
        ST.upsert_mirrored_op(c, _op("opx", 1, phone=REAL_PHONE,
                                     error_text=f"failed for {REAL_PHONE} wamid.ABC123=="))
    raw = client.get("/api/wa/pro/ops", headers=RH).text
    assert REAL_PHONE not in raw
    assert REAL_PHONE[1:] not in raw
    assert "wamid." not in raw or "wamid.…" in raw


def test_pii_from_the_review_never_reaches_either_endpoint_or_the_mirror_row(client):
    """Review finding 2 (BLOCKER), fed its own VERBATIM leaking strings end to end:
    - (a) op errors: executor.py's DriverError text, "no row in WhatsApp's own share picker
      matches {phone}" (adb_driver.py), mirrored word for word into wa_ops_mirror.error_text and
      then into GET /api/wa/pro/ops' error.text.
    - (b) the luna_reply job: wa_send_failures.error carrying str(exc) from api.py, which itself
      carries the raw phone ("free-form window closed for {phone}") and message text
      ("record {body!r}").
    The fix is code-only, both in the API response AND in the wa_ops_mirror row itself (never just
    at the serialization layer) -- this asserts both, plus the no-raw-digit-run/no-body-text
    discipline over the full rendered text of both endpoints."""
    share_picker_text = f"no row in WhatsApp's own share picker matches {REAL_PHONE}"
    freeform_text = f"the WhatsApp free-form window closed for {REAL_PHONE} (last inbound message is over 72h old)"
    body_text = "record {'to': '" + REAL_PHONE + "', 'body': 'Ich bin schwanger und suche eine Stelle'}"

    with ST.db() as c:
        ST.upsert_mirrored_op(c, _op("op_pii", 1, state="failed", error_code="op_failed",
                                     error_text=share_picker_text))
        row = c.execute("select error_text from wa_ops_mirror where op_id='op_pii'").fetchone()
        assert row["error_text"] is None   # review's fix reaches the mirror TABLE, not just the API

        ST.record_luna_call(c, REAL_PHONE)
        ST.record_send_failure(c, REAL_PHONE, freeform_text)
        ST.record_send_failure(c, REAL_PHONE, body_text)

    ops_raw = client.get("/api/wa/pro/ops", headers=RH).text
    activity_raw = client.get("/api/wa/pro/activity", headers=RH).text

    for raw in (ops_raw, activity_raw):
        assert REAL_PHONE not in raw
        assert REAL_PHONE[1:] not in raw                 # not even the digits without the leading +
        assert "share picker" not in raw
        assert "free-form window closed" not in raw
        assert "schwanger" not in raw                    # the sensitive message body itself
        assert not re.search(r"\d{6,}", raw)              # no long digit run of any kind

    ops_body = client.get("/api/wa/pro/ops", headers=RH).json()
    assert ops_body["rows"][0]["error"] == {"code": "op_failed"}
    activity_body = client.get("/api/wa/pro/activity", headers=RH).json()
    luna_reply = next(j for j in activity_body["jobs"] if j["job"] == "luna_reply")
    assert luna_reply["last_error"] == {"code": "send_failed"}


def test_ops_never_blocks_behind_st_lock(client):
    _seed_ops(client)
    release = threading.Event()
    holder_ready = threading.Event()

    def _hold_lock():
        with ST._lock:
            holder_ready.set()
            release.wait(timeout=5)

    t = threading.Thread(target=_hold_lock, daemon=True)
    t.start()
    try:
        assert holder_ready.wait(timeout=5)
        started = time.monotonic()
        r = client.get("/api/wa/pro/ops", headers=RH)
        elapsed = time.monotonic() - started
        assert r.status_code == 200
        assert not release.is_set()
        assert elapsed < 2.0
    finally:
        release.set()
        t.join(timeout=5)
