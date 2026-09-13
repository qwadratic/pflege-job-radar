"""Offline tests for app/wa/luna/followups.py (TASK-85) -- fixture threads only, no real CLI, no
network. Fakes app.wa.meta.Client, same pattern as tests/test_wa_luna_catchup.py."""
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import data as D
from app.wa import config as C
from app.wa import store as ST
from app.wa.luna import followups as FU


class FakeMeta:
    def __init__(self):
        self.sent = []
        self.n = 0

    def send_text(self, to_e164, body):
        self.n += 1
        self.sent.append({"to": to_e164, "body": body})
        return f"wamid.out.{self.n}"

    def send_buttons(self, to_e164, body, buttons):
        return self.send_text(to_e164, body)

    def send_template(self, to_e164, template_name, language="de", params=None):
        self.n += 1
        self.sent.append({"to": to_e164, "template": template_name})
        return f"wamid.out.{self.n}"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "FOLLOWUP_TIER_MINUTES", [15, 60, 240])
    monkeypatch.setattr(C, "MAX_FOLLOWUPS_PER_STREAK", 4)
    conn = ST.db()
    yield conn
    conn.close()


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


def _seed_them(conn, phone, last_outbound_minutes_ago, last_inbound_minutes_ago=None):
    """A thread where WE answered last (ball='them') -- the candidate has gone quiet."""
    t = ST.thread(conn, phone)
    if last_inbound_minutes_ago is not None:
        ST.record_inbound(conn, phone, f"wamid.in.{phone}", "Hallo")
    ST.record_outbound(conn, phone, f"wamid.out.{phone}", "Willkommen!")
    t["last_outbound_at"] = _ago(last_outbound_minutes_ago)
    if last_inbound_minutes_ago is not None:
        t["last_inbound_at"] = _ago(last_inbound_minutes_ago)
    ST.save_thread(conn, t)
    return t


def test_no_nudge_before_the_first_tier(db):
    _seed_them(db, "+49111", last_outbound_minutes_ago=5)
    db.close()
    assert FU.run(client=FakeMeta()) == []


def test_first_tier_nudge_fires_once_the_threshold_passes(db):
    _seed_them(db, "+49111", last_outbound_minutes_ago=20)
    db.close()
    results = FU.run(client=FakeMeta())
    assert len(results) == 1
    assert results[0]["phone"] == "+49111"
    assert results[0]["tier"] == 0
    assert results[0]["status"] == "sent"


def test_a_second_run_soon_after_does_not_double_nudge_the_same_tier(db):
    _seed_them(db, "+49111", last_outbound_minutes_ago=20)
    db.close()
    meta = FakeMeta()
    FU.run(client=meta)
    results = FU.run(client=meta)
    assert results == [], "tier 0 was already nudged this streak -- must not fire again immediately"


def test_tier_progresses_only_after_the_next_threshold(db):
    _seed_them(db, "+49111", last_outbound_minutes_ago=20)
    db.close()
    meta = FakeMeta()
    FU.run(client=meta)  # tier 0 fires
    with ST.db() as c:
        t = ST.thread(c, "+49111")
        t["last_outbound_at"] = _ago(70)  # now past tier 1's 60-minute mark too
        ST.save_thread(c, t)
    results = FU.run(client=meta)
    assert len(results) == 1 and results[0]["tier"] == 1


def test_a_reply_resets_the_streak(db):
    _seed_them(db, "+49111", last_outbound_minutes_ago=20)
    db.close()
    meta = FakeMeta()
    FU.run(client=meta)  # tier 0 fires, recorded at "now"
    with ST.db() as c:
        # Backdate the recorded followup well into the past so it is unambiguously part of a
        # prior streak, then lay out a chronologically consistent reset: old followup (100m ago)
        # -> candidate replies (50m ago) -> we reply back (20m ago) -> now, 20m have passed since
        # OUR reply, so tier 0 is eligible again.
        c.execute("update wa_followups_sent set sent_at=? where phone=?", (_ago(100), "+49111"))
        c.commit()
        ST.record_inbound(c, "+49111", "wamid.reply.1", "Sorry, war beschäftigt!")
        ST.record_outbound(c, "+49111", "wamid.manual.reply.1", "Kein Problem!")
        t = ST.thread(c, "+49111")
        t["last_inbound_at"] = _ago(50)
        t["last_outbound_at"] = _ago(20)
        ST.save_thread(c, t)
    results = FU.run(client=meta)
    assert len(results) == 1 and results[0]["tier"] == 0, (
        "the reply must reset the streak -- tier 0 is eligible again, not blocked as 'already sent'")


def test_the_streak_cap_stops_nudging_after_max_followups(db, monkeypatch):
    monkeypatch.setattr(C, "MAX_FOLLOWUPS_PER_STREAK", 1)
    _seed_them(db, "+49111", last_outbound_minutes_ago=20)
    db.close()
    meta = FakeMeta()
    FU.run(client=meta)  # tier 0 fires, hits the cap of 1
    with ST.db() as c:
        t = ST.thread(c, "+49111")
        t["last_outbound_at"] = _ago(300)  # long past every tier now
        ST.save_thread(c, t)
    results = FU.run(client=meta)
    assert results == [], "the per-streak cap must stop further nudges even though tiers remain"


def test_a_stopped_thread_is_never_nudged(db):
    t = _seed_them(db, "+49111", last_outbound_minutes_ago=300)
    t["stopped"], t["stopped_reason"] = True, ST.STOPPED
    ST.save_thread(db, t)
    db.close()
    assert FU.run(client=FakeMeta()) == []


def test_a_thread_where_the_candidate_owes_us_nothing_is_not_nudged(db):
    """ball_for()=='us' (candidate wrote last, WE owe a reply) is catchup.py's job, not a nudge."""
    t = ST.thread(db, "+49111")
    ST.record_inbound(db, "+49111", "wamid.in.1", "Hallo")
    t["last_inbound_at"] = ST.now_iso()
    ST.save_thread(db, t)
    db.close()
    assert FU.run(client=FakeMeta()) == []


def test_a_thread_with_no_messages_at_all_is_not_nudged(db):
    ST.thread(db, "+49111")
    db.close()
    assert FU.run(client=FakeMeta()) == []


def test_the_24h_window_gate_applies_to_a_nudge_too(db, monkeypatch):
    monkeypatch.setattr(C, "WA_REOPEN_TEMPLATE_NAME", "candidate_reopen_v1")
    _seed_them(db, "+49111", last_outbound_minutes_ago=20, last_inbound_minutes_ago=60 * 30)  # 30h ago
    db.close()
    results = FU.run(client=FakeMeta())
    assert len(results) == 1
    assert results[0]["status"] == "sent_template", (
        "a closed 24h window must route the nudge through the reopen template, same as any other send")


def test_run_can_be_scoped_to_specific_phones(db):
    _seed_them(db, "+49111", last_outbound_minutes_ago=20)
    _seed_them(db, "+49222", last_outbound_minutes_ago=20)
    db.close()
    results = FU.run(client=FakeMeta(), phones=["+49222"])
    assert [r["phone"] for r in results] == ["+49222"]
