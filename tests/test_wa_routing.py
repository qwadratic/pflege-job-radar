"""Offline tests for app/wa/routing.py (TASK-75) -- fixture-only, no real router, no production
webhook change (that part is explicitly out of scope, see the module docstring)."""
import pytest

from app.wa import config as C
from app.wa import routing as R
from app.wa import store as ST


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "REAL_SYSTEM_PHONES_FILE", "")
    conn = R.db()
    yield conn
    conn.close()


def test_a_phone_unknown_to_the_real_system_routes_to_us(db, tmp_path, monkeypatch):
    known_file = tmp_path / "known.txt"
    known_file.write_text("+49999\n", encoding="utf-8")
    monkeypatch.setattr(C, "REAL_SYSTEM_PHONES_FILE", str(known_file))
    assert R.route_decision(db, "+49111") == "us"


def test_a_phone_known_to_the_real_system_routes_to_them(db, tmp_path, monkeypatch):
    known_file = tmp_path / "known.txt"
    known_file.write_text("+49111\n+49222\n", encoding="utf-8")
    monkeypatch.setattr(C, "REAL_SYSTEM_PHONES_FILE", str(known_file))
    assert R.route_decision(db, "+49111") == "them"


def test_the_decision_is_durable_and_does_not_re_check_the_file_on_a_later_call(db, tmp_path, monkeypatch):
    known_file = tmp_path / "known.txt"
    known_file.write_text("+49111\n", encoding="utf-8")
    monkeypatch.setattr(C, "REAL_SYSTEM_PHONES_FILE", str(known_file))
    assert R.route_decision(db, "+49111") == "them"

    known_file.write_text("", encoding="utf-8")  # the phone is no longer "known" -- must not matter now
    assert R.route_decision(db, "+49111") == "them"


def test_an_unconfigured_existence_check_refuses_to_guess_for_a_new_phone(db):
    with pytest.raises(RuntimeError, match="WA_REAL_SYSTEM_PHONES_FILE"):
        R.route_decision(db, "+49111")


def test_an_unconfigured_existence_check_does_not_matter_once_already_routed(db, tmp_path, monkeypatch):
    known_file = tmp_path / "known.txt"
    known_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(C, "REAL_SYSTEM_PHONES_FILE", str(known_file))
    assert R.route_decision(db, "+49111") == "us"

    monkeypatch.setattr(C, "REAL_SYSTEM_PHONES_FILE", "")  # unset again -- must not matter, already decided
    assert R.route_decision(db, "+49111") == "us"


def test_a_missing_configured_file_raises_a_clear_error(db, tmp_path, monkeypatch):
    monkeypatch.setattr(C, "REAL_SYSTEM_PHONES_FILE", str(tmp_path / "does_not_exist.txt"))
    with pytest.raises(RuntimeError, match="does not exist"):
        R.route_decision(db, "+49111")


def test_flip_to_us_on_reopen_overrides_a_prior_them_decision(db, tmp_path, monkeypatch):
    known_file = tmp_path / "known.txt"
    known_file.write_text("+49111\n", encoding="utf-8")
    monkeypatch.setattr(C, "REAL_SYSTEM_PHONES_FILE", str(known_file))
    assert R.route_decision(db, "+49111") == "them"

    R.flip_to_us_on_reopen(db, "+49111")
    assert R.route_decision(db, "+49111") == "us"

    row = db.execute("select reason from wa_ownership where phone=?", ("+49111",)).fetchone()
    assert row["reason"] == "reopened_by_us"


def test_flip_to_us_on_reopen_works_even_with_no_prior_record(db):
    R.flip_to_us_on_reopen(db, "+49111")
    assert R.route_decision(db, "+49111") == "us"


def test_wa_ownership_table_is_separate_from_wa_threads(db):
    tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'").fetchall()}
    assert {"wa_ownership", "wa_threads", "wa_messages"} <= tables
