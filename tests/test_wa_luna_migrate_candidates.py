"""Offline tests for app/wa/luna/migrate_candidates.py -- fixture rows only, no real data."""
import pytest

from app.wa import config as C
from app.wa import store as ST
from app.wa.luna import migrate_candidates as MIG


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    conn = ST.db()
    yield conn
    conn.close()


def test_migrates_a_well_formed_row(db):
    rows = [{"phone": "0170 1234567", "qualification_path": "urkunde", "city": "München"}]
    out = MIG.migrate(rows, conn=db)
    assert out["unmappable"] == []
    assert out["migrated"] == ["+491701234567"]
    t = ST.thread(db, "+491701234567")
    assert t["slots"]["qualification_path"] == "urkunde" and t["slots"]["city"] == "München"


def test_missing_phone_is_reported_not_dropped_silently(db):
    out = MIG.migrate([{"city": "München"}], conn=db)
    assert out["migrated"] == []
    assert out["unmappable"] == [{"row": {"city": "München"}, "reason": "missing phone"}]


def test_uncanonicalizable_phone_is_reported(db):
    """canonicalize_phone("not-a-phone-number") -> "+49" (every character stripped as non-digit,
    leaving only the default country code) -- truthy but not a real number; must not pass as one."""
    out = MIG.migrate([{"phone": "not-a-phone-number"}], conn=db)
    assert out["migrated"] == []
    assert len(out["unmappable"]) == 1
    assert "did not canonicalize" in out["unmappable"][0]["reason"]


def test_empty_phone_string_is_reported(db):
    out = MIG.migrate([{"phone": ""}], conn=db)
    assert out["unmappable"] == [{"row": {"phone": ""}, "reason": "missing phone"}]


def test_re_running_merges_rather_than_replaces_the_card(db):
    MIG.migrate([{"phone": "+491701234567", "qualification_path": "urkunde"}], conn=db)
    t = ST.thread(db, "+491701234567")
    t["slots"]["city"] = "Regensburg"  # the harness learned this on its own since the export
    ST.save_thread(db, t)

    MIG.migrate([{"phone": "+491701234567", "department_pref": "Intensiv/IMC"}], conn=db)
    t = ST.thread(db, "+491701234567")
    assert t["slots"]["qualification_path"] == "urkunde", "first migration's field must survive"
    assert t["slots"]["city"] == "Regensburg", "harness-learned field must not be wiped by a re-run"
    assert t["slots"]["department_pref"] == "Intensiv/IMC", "second migration's field must land"


def test_dry_run_does_not_write_anything(db):
    out = MIG.migrate([{"phone": "+491701234567", "qualification_path": "urkunde"}], conn=db, write=False)
    assert out["migrated"] == ["+491701234567"]
    assert db.execute("select count(*) as n from wa_threads").fetchone()["n"] == 0


def test_none_fields_are_not_written_into_the_card(db):
    MIG.migrate([{"phone": "+491701234567", "qualification_path": "urkunde", "city": None}], conn=db)
    t = ST.thread(db, "+491701234567")
    assert "city" not in t["slots"]
