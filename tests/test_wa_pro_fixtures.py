"""tests/fixtures/wa_pro_api/threads.json must validate against app/wa/pro_models.py exactly -- so
the fixture pflege-fe develops the Pro dashboard against can never silently drift from the contract
models this harness actually serializes (design decision 9)."""
import json
import pathlib

from app.wa import pro_models as M

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
    data = _load()
    handoff_rows = [row for row in data["rows"] if row["handoff"]]
    assert handoff_rows
    statuses_seen = {clinic["status"] for row in handoff_rows for clinic in row["handoff"]["clinics"]}
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
