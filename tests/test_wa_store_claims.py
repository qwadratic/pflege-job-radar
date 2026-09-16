"""Offline tests for the new store.py primitives: reply-turn claims (TASK-77), the per-candidate
LLM call log (TASK-76), and send-failure recording (TASK-79)."""
import pytest

from app.wa import config as C
from app.wa import store as ST


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    conn = ST.db()
    yield conn
    conn.close()


# --- claim_reply_turn / finish_reply_turn_claim -------------------------------------------------

def test_first_claim_on_a_turn_key_succeeds(db):
    assert ST.claim_reply_turn(db, "+49111", "wamid.1") is True


def test_a_second_concurrent_claim_on_the_same_turn_key_fails(db):
    assert ST.claim_reply_turn(db, "+49111", "wamid.1") is True
    assert ST.claim_reply_turn(db, "+49111", "wamid.1") is False


def test_different_turn_keys_or_phones_do_not_interfere(db):
    assert ST.claim_reply_turn(db, "+49111", "wamid.1") is True
    assert ST.claim_reply_turn(db, "+49111", "wamid.2") is True
    assert ST.claim_reply_turn(db, "+49222", "wamid.1") is True


def test_a_terminal_sent_claim_is_never_reclaimable(db):
    ST.claim_reply_turn(db, "+49111", "wamid.1")
    ST.finish_reply_turn_claim(db, "+49111", "wamid.1", "sent")
    assert ST.claim_reply_turn(db, "+49111", "wamid.1") is False


def test_a_recorded_no_send_claim_is_never_reclaimable(db):
    """TASK-101: the brain chose silence for this message; catch-up must not re-run the model on it."""
    ST.claim_reply_turn(db, "+49111", "wamid.1")
    ST.finish_reply_turn_claim(db, "+49111", "wamid.1", ST.NO_SEND_STATE)
    assert ST.claim_reply_turn(db, "+49111", "wamid.1") is False


@pytest.mark.parametrize("state", ["skipped_rate_cap", "skipped_stopped", "skipped_error"])
def test_a_non_terminal_skipped_claim_is_reclaimable(db, state):
    ST.claim_reply_turn(db, "+49111", "wamid.1")
    ST.finish_reply_turn_claim(db, "+49111", "wamid.1", state)
    assert ST.claim_reply_turn(db, "+49111", "wamid.1") is True


def test_a_stale_in_progress_claim_is_reclaimable(db, monkeypatch):
    ST.claim_reply_turn(db, "+49111", "wamid.1")
    # simulate a crashed prior attempt: back-date the claim past the staleness window
    stale_at = "2020-01-01T00:00:00+00:00"
    db.execute("update wa_reply_turn_claims set claimed_at=? where phone=? and turn_key=?",
              (stale_at, "+49111", "wamid.1"))
    db.commit()
    assert ST.claim_reply_turn(db, "+49111", "wamid.1") is True


def test_a_fresh_in_progress_claim_is_not_reclaimable(db):
    ST.claim_reply_turn(db, "+49111", "wamid.1")
    assert ST.claim_reply_turn(db, "+49111", "wamid.1") is False


# --- luna call rate-limit log --------------------------------------------------------------------

def test_count_recent_luna_calls_is_zero_with_no_calls(db):
    assert ST.count_recent_luna_calls(db, "+49111") == 0


def test_count_recent_luna_calls_counts_calls_within_the_window(db):
    ST.record_luna_call(db, "+49111")
    ST.record_luna_call(db, "+49111")
    ST.record_luna_call(db, "+49222")  # a different phone must not count toward +49111's total
    assert ST.count_recent_luna_calls(db, "+49111") == 2


def test_count_recent_luna_calls_excludes_calls_outside_the_rolling_window(db):
    ST.record_luna_call(db, "+49111")
    db.execute("update wa_luna_calls set at=? where phone=?", ("2020-01-01T00:00:00+00:00", "+49111"))
    db.commit()
    assert ST.count_recent_luna_calls(db, "+49111", within_hours=1.0) == 0


# --- send-failure recording -----------------------------------------------------------------------

def test_recent_send_failure_is_none_with_no_failures(db):
    assert ST.recent_send_failure(db, "+49111") is None


def test_record_send_failure_is_readable_and_keeps_the_latest(db):
    ST.record_send_failure(db, "+49111", "first error")
    ST.record_send_failure(db, "+49111", "second error")
    failure = ST.recent_send_failure(db, "+49111")
    assert failure["error"] == "second error"


# --- nudge dedup claim (TASK-93) -------------------------------------------------------------

def test_first_claim_on_a_fingerprint_succeeds(db):
    assert ST.claim_nudge(db, "+49111", "followup:0:epoch") is True


def test_a_second_claim_on_the_same_phone_and_fingerprint_fails(db):
    assert ST.claim_nudge(db, "+49111", "followup:0:epoch") is True
    assert ST.claim_nudge(db, "+49111", "followup:0:epoch") is False


def test_different_fingerprints_or_phones_do_not_interfere(db):
    assert ST.claim_nudge(db, "+49111", "followup:0:epoch") is True
    assert ST.claim_nudge(db, "+49111", "followup:1:epoch") is True, "a different tier is a different claim"
    assert ST.claim_nudge(db, "+49111", "followup:0:2026-01-01T00:00:00+00:00") is True, (
        "a different streak anchor is a different claim -- a later legitimate streak must not be "
        "blocked by an earlier one that used the same tier index")
    assert ST.claim_nudge(db, "+49222", "followup:0:epoch") is True, "a different phone is a different claim"
