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
    # Quiet hours (TASK-92) default to 21->9 Europe/Berlin -- every test in this file below is
    # about tier/streak logic, not quiet hours, and must not flake depending on the real wall-clock
    # time the suite happens to run at. Disabled here (start==end, see _in_quiet_hours' own
    # docstring for why that reads as 'disabled'); the dedicated quiet-hours tests below restore
    # real values or monkeypatch FU._in_quiet_hours directly instead.
    monkeypatch.setattr(C, "QUIET_HOURS_START", 0)
    monkeypatch.setattr(C, "QUIET_HOURS_END", 0)
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


# --- quiet hours (TASK-92) -----------------------------------------------------------------------

def _at(hour, tz="Europe/Berlin"):
    """A UTC-aware datetime whose local hour in `tz` is exactly `hour` -- Berlin has no DST
    transition at these dates, a fixed mid-year UTC offset (+2) keeps this simple and exact."""
    from zoneinfo import ZoneInfo
    return datetime(2026, 6, 15, hour, 0, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


def test_in_quiet_hours_simple_window(monkeypatch):
    monkeypatch.setattr(C, "QUIET_HOURS_START", 9)
    monkeypatch.setattr(C, "QUIET_HOURS_END", 17)
    assert FU._in_quiet_hours(_at(9)) is True, "the start hour itself is inside the window"
    assert FU._in_quiet_hours(_at(12)) is True
    assert FU._in_quiet_hours(_at(16)) is True
    assert FU._in_quiet_hours(_at(17)) is False, "the end hour itself is outside the window"
    assert FU._in_quiet_hours(_at(8)) is False


def test_in_quiet_hours_wraps_past_midnight():
    """Default-shaped window (21 -> 9): quiet in the evening, through midnight, into the morning."""
    assert FU._in_quiet_hours(_at(21)) is True
    assert FU._in_quiet_hours(_at(23)) is True
    assert FU._in_quiet_hours(_at(0)) is True
    assert FU._in_quiet_hours(_at(8)) is True
    assert FU._in_quiet_hours(_at(9)) is False, "the end hour itself is outside the window"
    assert FU._in_quiet_hours(_at(20)) is False, "the hour just before start is still daytime"
    assert FU._in_quiet_hours(_at(14)) is False, "mid-afternoon is never quiet in the default window"


def test_in_quiet_hours_zero_width_window_is_disabled(monkeypatch):
    monkeypatch.setattr(C, "QUIET_HOURS_START", 5)
    monkeypatch.setattr(C, "QUIET_HOURS_END", 5)
    assert FU._in_quiet_hours(_at(5)) is False
    assert FU._in_quiet_hours(_at(0)) is False
    assert FU._in_quiet_hours(_at(23)) is False


def test_run_sends_nothing_during_quiet_hours_even_with_an_eligible_thread(db, monkeypatch):
    _seed_them(db, "+49111", last_outbound_minutes_ago=20)
    db.close()
    monkeypatch.setattr(FU, "_in_quiet_hours", lambda now=None: True)
    assert FU.run(client=FakeMeta()) == []


# --- dedup claim (TASK-93) -----------------------------------------------------------------------

def test_two_overlapping_runs_do_not_double_nudge_the_same_candidate(db):
    """Simulates two separate processes racing on the same eligible thread (a second campaign
    trigger, or an overlapping timer tick) -- ST._lock alone would not stop this across two real
    processes, only the durable wa_nudge_claims table does."""
    _seed_them(db, "+49111", last_outbound_minutes_ago=20)
    db.close()
    meta = FakeMeta()
    with ST.db() as c1, ST.db() as c2:
        t1, t2 = ST.thread(c1, "+49111"), ST.thread(c2, "+49111")
        tier1 = FU._eligible_tier(c1, "+49111", t1.get("last_outbound_at"), t1.get("last_inbound_at"))
        tier2 = FU._eligible_tier(c2, "+49111", t2.get("last_outbound_at"), t2.get("last_inbound_at"))
        assert tier1 == tier2 == 0, "both racing callers must independently compute the same eligible tier"
        since = FU._EPOCH
        first = ST.claim_nudge(c1, "+49111", f"followup:0:{since}")
        second = ST.claim_nudge(c2, "+49111", f"followup:0:{since}")
    assert first is True and second is False, "only one of the two racing claims may win"


def test_a_nudge_due_during_quiet_hours_is_not_lost_the_next_tick_sends_it(db, monkeypatch):
    """No separate deferred-send queue: the tier is still eligible next tick since eligibility is
    computed from elapsed time, not from whether an earlier check happened to run."""
    _seed_them(db, "+49111", last_outbound_minutes_ago=20)
    db.close()
    meta = FakeMeta()
    monkeypatch.setattr(FU, "_in_quiet_hours", lambda now=None: True)
    assert FU.run(client=meta) == [], "quiet hours: the tick is skipped entirely"
    monkeypatch.setattr(FU, "_in_quiet_hours", lambda now=None: False)
    results = FU.run(client=meta)
    assert len(results) == 1 and results[0]["tier"] == 0, (
        "the same tier must still fire on the next tick once quiet hours end")


@pytest.mark.parametrize("slots", [
    {"qualification_path": "urkunde", "qualification_ok": True, "anonymous_send_consent": True},
    {"qualification_path": "reject", "qualification_ok": False},
], ids=["consented", "not_placeable"])
def test_a_finished_thread_is_never_nudged_even_with_a_tier_due(db, slots):
    """TASK-94: a consented or not-placeable thread ends with OUR message (ball=them) -- found live,
    a consented candidate got 'sind Sie noch da?' twice the next morning."""
    t = _seed_them(db, "+49111", last_outbound_minutes_ago=300, last_inbound_minutes_ago=301)
    t["slots"] = slots
    ST.save_thread(db, t)
    db.close()
    meta = FakeMeta()
    assert FU.run(client=meta) == []
    assert meta.sent == []


def test_a_thread_waiting_on_a_requested_document_is_still_nudged(db):
    """TASK-94: only terminal stages are skipped -- a qualified candidate we asked for a document
    who went quiet is exactly who a nudge is for."""
    t = _seed_them(db, "+49111", last_outbound_minutes_ago=20, last_inbound_minutes_ago=21)
    t["slots"] = {"region": "Bayern", "qualification_path": "urkunde", "qualification_ok": True,
                  "city": "Landshut", "housing_known": True}
    ST.save_thread(db, t)
    db.close()
    meta = FakeMeta()
    assert [r["tier"] for r in FU.run(client=meta)] == [0]
    assert len(meta.sent) == 1
