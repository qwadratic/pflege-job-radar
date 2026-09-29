"""Offline proof for PhoneDoctor (TASK-315 AC#9). FakeDriver only -- no adb, no phone, no ssh.

Every test here asserts something the card's own incidents make concrete: a queued op the doctor
must never race for the lock, a dialog dismissed without ever addressing Einladen/SMS by name (the
"never tap" guarantee is structural -- see bridge/doctor.py's own module docstring -- so what is
actually asserted here is that the ONLY tap recorded is the exact point the driver handed back), and
the two live incidents behind this card: low memory (2026-09-29 10:15 UTC) and WhatsApp resuming
into the archive after park() already made focus() read clean (2026-09-29, the same morning).
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from bridge import doctor as PD
from bridge import driver as D
from bridge import executor as X
from bridge import governor as G
from bridge import ledger as L

PHONE = "+491700000001"


class Clock:
    def __init__(self, moment):
        self.now = moment

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        return self.now


class Rig:
    """ledger + governor + fake phone + executor, on one tmp sqlite file (same shape as
    tests/test_bridge_executor.py::Rig, trimmed to what PhoneDoctor touches)."""

    def __init__(self, tmp_path, *, moment=None):
        self.clock = Clock(moment or datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc))
        self.ledger = L.Ledger(tmp_path / "ledger.sqlite")
        self.driver = D.FakeDriver()
        self.governor = G.Governor(self.ledger, per_number_daily_cap=3)
        self.executor = X.Executor(ledger=self.ledger, governor=self.governor, driver=self.driver,
                                   rail_number=None, clock=self.clock, sleep=lambda _s: None)


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.ledger.close()


def journal(rig, event):
    return [dict(r) for r in rig.ledger._db.execute(
        "select * from journal where event=?", (event,)).fetchall()]


def doc(rig, **kw):
    return PD.PhoneDoctor(rig.executor, log=lambda _m: None, **kw)


# --- busy: a real operation always wins ----------------------------------------------------------
def test_a_queued_phone_op_skips_the_cycle_without_touching_the_phone(rig):
    rig.ledger.enqueue_op("op.x", "list_chats", {}, rig.clock())
    d = doc(rig)
    d.cycle()
    assert d.skipped_busy == 1
    assert d.errors == 0
    assert d.last_ok_at == L.utc(rig.clock())   # the cycle itself did not fail, it declined to run
    # NOTHING was touched: not a single driver verb this module owns was ever called.
    assert rig.driver.kill_all_calls == 0
    assert rig.driver.doctor_taps == []
    assert rig.driver.whatsapp_resumed == 0
    assert rig.driver.back_presses == 0
    assert rig.driver.home_presses == 0
    assert rig.driver.lock_events == []


def test_a_running_phone_op_also_skips_the_cycle(rig):
    rig.ledger.enqueue_op("op.x", "list_chats", {}, rig.clock())
    rig.ledger.claim_next_op()   # queued -> running
    d = doc(rig)
    d.cycle()
    assert d.skipped_busy == 1
    assert rig.driver.doctor_taps == []


def test_a_lock_something_else_holds_directly_also_skips(rig):
    """IdentityWatcher/ReconcileWatcher/BroadcastRunner take huawei01.lock outside the phone_ops
    queue entirely (bridge/dispatcher.py's own docstring) -- the ledger check alone is not enough."""
    rig.driver.busy = True
    d = doc(rig)
    d.cycle()
    assert d.skipped_busy == 1
    assert d.errors == 0
    assert rig.driver.doctor_taps == []


# --- check 1: memory (2026-09-29 10:15 UTC incident) ----------------------------------------------
def test_low_memory_runs_kill_all_and_journals_before_and_after(rig):
    rig.driver.mem_available_mb_value = 244     # the incident's own reading
    rig.driver.mem_available_after_kill = 1460  # the incident's own manual-fix reading
    d = doc(rig, min_mem_mb=700)
    d.cycle()
    assert rig.driver.kill_all_calls == 1
    assert d.mem_available_mb == 1460
    assert d.actions.get("memory") == 1
    rows = journal(rig, "doctor_memory")
    assert len(rows) == 1
    detail = json.loads(rows[0]["detail"])
    assert detail["before_mb"] == 244
    assert detail["after_mb"] == 1460
    assert detail["min_mem_mb"] == 700
    assert detail["shot"] == "/fake/shots/doctor_memory.png"
    assert d.last_action["kind"] == "memory"


def test_memory_above_the_floor_is_read_but_not_acted_on(rig):
    rig.driver.mem_available_mb_value = 2000
    d = doc(rig, min_mem_mb=700)
    d.cycle()
    assert rig.driver.kill_all_calls == 0
    assert d.mem_available_mb == 2000
    assert "memory" not in d.actions
    assert journal(rig, "doctor_memory") == []


# --- check 2: known stray dialogs ------------------------------------------------------------------
def test_sms_default_app_warning_is_dismissed_by_tapping_outside_and_resetting_the_task(rig):
    """2026-09-25: this dialog blocked every send for 3h; 2026-09-26 09:43 UTC found the fix --
    tap outside, force-stop, relaunch. The tap recorded is the ONLY tap this whole cycle makes,
    which is what proves Einladen/SMS (this module has no verb that could even name them) was
    never addressed."""
    rig.driver.dialog_scan_value = {
        "focus": "com.whatsapp/.registration.SmsDefaultAppWarning",
        "dialog": {"kind": "sms_default_app_warning", "tap": (120, 340)}}
    d = doc(rig)
    d.cycle()
    assert rig.driver.doctor_taps == [(120, 340)]
    assert rig.driver.force_stop_calls == 1
    assert rig.driver.whatsapp_resumed >= 1
    assert d.actions.get("sms_default_app_warning") == 1
    rows = journal(rig, "doctor_sms_default_app_warning")
    detail = json.loads(rows[0]["detail"])
    assert detail["tap"] == [120, 340]
    assert detail["focus"] == "com.whatsapp/.registration.SmsDefaultAppWarning"


def test_usb_nutzung_dialog_is_dismissed_by_tapping_abbrechen_only(rig):
    """2026-09-26: appeared alongside SmsDefaultAppWarning, in com.android.settings, outside
    WhatsApp entirely -- ABBRECHEN closes it and there is nothing to force-stop."""
    rig.driver.dialog_scan_value = {
        "focus": "com.android.settings/.usb.UsbAccessoryUriActivity",
        "dialog": {"kind": "usb_nutzung", "tap": (900, 1500)}}
    d = doc(rig)
    d.cycle()
    assert rig.driver.doctor_taps == [(900, 1500)]
    assert rig.driver.force_stop_calls == 0
    assert d.actions.get("usb_nutzung") == 1
    rows = journal(rig, "doctor_usb_nutzung")
    detail = json.loads(rows[0]["detail"])
    assert detail["tap"] == [900, 1500]


def test_unknown_foreign_focus_gets_one_back_then_home_and_never_a_tap(rig):
    rig.driver.dialog_scan_value = {
        "focus": "com.google.android.gm/.ConversationListActivityGmail", "dialog": None}
    d = doc(rig)
    d.cycle()
    assert rig.driver.doctor_taps == []
    assert rig.driver.back_presses == 1
    assert rig.driver.home_presses >= 1
    assert d.actions.get("foreign_focus") == 1
    rows = journal(rig, "doctor_foreign_focus")
    assert json.loads(rows[0]["detail"])["focus"] == \
        "com.google.android.gm/.ConversationListActivityGmail"


def test_the_launcher_is_already_clean_and_nothing_is_touched_by_the_dialog_check(rig):
    rig.driver.dialog_scan_value = {"focus": "com.android.launcher/.Home", "dialog": None}
    d = doc(rig)
    d.cycle()
    assert rig.driver.doctor_taps == []
    assert rig.driver.back_presses == 0
    assert "foreign_focus" not in d.actions


def test_a_whatsapp_focus_that_is_not_home_or_conversation_is_left_to_the_task_check(rig):
    """The dialog check must not duplicate the task-reset remedy: seeing e.g. the archive on top,
    it does nothing at all -- _check_whatsapp_task is what owns this case (TASK-315 addendum)."""
    rig.driver.dialog_scan_value = {
        "focus": "com.whatsapp/.conversation.conversationslist.ArchivedConversationsActivity",
        "dialog": None}
    d = doc(rig)
    d.cycle()
    assert rig.driver.back_presses == 0   # not the foreign-focus remedy's single BACK
    assert "foreign_focus" not in d.actions


# --- check 3: WhatsApp's own task -- dead, or resumed somewhere wrong -----------------------------
def test_whatsapp_not_running_is_relaunched_and_journalled(rig):
    rig.driver.whatsapp_alive = False
    rig.driver.resume_focus_value = "com.whatsapp/.HomeActivity"
    d = doc(rig)
    d.cycle()
    assert rig.driver.whatsapp_resumed == 1
    assert rig.driver.whatsapp_alive is True
    assert d.actions.get("whatsapp_relaunch") == 1
    rows = journal(rig, "doctor_whatsapp_relaunch")
    detail = json.loads(rows[0]["detail"])
    assert detail == {"shot": "/fake/shots/doctor_whatsapp_relaunch.png", "was_alive": False,
                      "resumed_focus": "com.whatsapp/.HomeActivity",
                      "final_focus": "com.whatsapp/.HomeActivity"}


def test_an_already_alive_and_clean_whatsapp_is_tidied_but_not_journalled(rig):
    """resume_whatsapp() is our own probe foregrounding an already-parked, healthy WhatsApp -- HOME
    puts it back, but this is not a remedy and must not spam the journal every single cycle."""
    rig.driver.whatsapp_alive = True
    rig.driver.resume_focus_value = "com.whatsapp/.HomeActivity"
    d = doc(rig)
    d.cycle()
    assert rig.driver.home_presses >= 1
    assert d.actions == {}
    assert d.last_action is None


def test_whatsapp_resumed_into_the_archive_is_backed_out_to_home_activity(rig):
    """The addendum's own live incident, 2026-09-29: park() pressed HOME so focus() read clean, but
    WhatsApp's task itself kept resuming the archive. One BACK closed it live; scripted the same
    way here."""
    rig.driver.resume_focus_value = \
        "com.whatsapp/.conversation.conversationslist.ArchivedConversationsActivity"
    rig.driver.back_sequence = ["com.whatsapp/.HomeActivity"]
    d = doc(rig)
    d.cycle()
    assert rig.driver.back_presses == 1
    assert rig.driver.force_stop_calls == 0
    assert d.actions == {"whatsapp_task_reset": 1}
    rows = journal(rig, "doctor_whatsapp_task_reset")
    detail = json.loads(rows[0]["detail"])
    assert detail["was_alive"] is True
    assert detail["resumed_focus"].endswith("ArchivedConversationsActivity")
    assert detail["final_focus"] == "com.whatsapp/.HomeActivity"


def test_whatsapp_stuck_off_home_after_three_backs_is_force_stopped_and_relaunched(rig):
    rig.driver.resume_focus_sequence = [
        "com.whatsapp/.ContactInfoActivity",   # what the task first resumed into
        "com.whatsapp/.HomeActivity",          # a clean relaunch, once force-stopped
    ]
    rig.driver.back_sequence = []   # BACK never moves it off ContactInfoActivity
    d = doc(rig)
    d.cycle()
    assert rig.driver.back_presses == PD.MAX_BACK_ATTEMPTS
    assert rig.driver.force_stop_calls == 1
    assert rig.driver.whatsapp_resumed == 2
    assert d.actions == {"whatsapp_task_reset": 1}
    rows = journal(rig, "doctor_whatsapp_task_reset")
    detail = json.loads(rows[0]["detail"])
    assert detail["resumed_focus"] == "com.whatsapp/.ContactInfoActivity"
    assert detail["final_focus"] == "com.whatsapp/.HomeActivity"


def test_whatsapp_resumed_into_an_open_conversation_is_left_entirely_alone(rig):
    """InboundWatcher's own idle self-check owns a genuinely open chat -- reading it before parking
    is its job, on its own 2-cycle-confirmed schedule; this check must never race ahead of it."""
    rig.driver.resume_focus_value = "com.whatsapp/.Conversation"
    d = doc(rig)
    d.cycle()
    assert rig.driver.back_presses == 0
    assert rig.driver.force_stop_calls == 0
    assert rig.driver.home_presses == 0
    assert d.actions == {}


def test_back_that_surfaces_a_conversation_is_also_left_alone(rig):
    rig.driver.resume_focus_value = "com.whatsapp/.ContactInfoActivity"
    rig.driver.back_sequence = ["com.whatsapp/.Conversation"]
    d = doc(rig)
    d.cycle()
    assert rig.driver.back_presses == 1
    assert rig.driver.force_stop_calls == 0
    assert rig.driver.home_presses == 0
    assert d.actions == {}


# --- check 4: leftover recordings (TASK-228's own orphan sweep, reused here) ----------------------
def test_leftover_recordings_are_swept_and_journalled_when_there_were_any(rig):
    rig.driver.orphaned_recordings = 2
    d = doc(rig)
    d.cycle()
    assert d.actions.get("sweep_recordings") == 1
    rows = journal(rig, "doctor_sweep_recordings")
    assert json.loads(rows[0]["detail"])["removed"] == 2


def test_nothing_to_sweep_is_not_journalled(rig):
    d = doc(rig)
    d.cycle()
    assert "sweep_recordings" not in d.actions
    assert journal(rig, "doctor_sweep_recordings") == []


# --- one check's exception never stops the others, or the thread ----------------------------------
def test_a_failing_check_is_counted_and_journalled_and_the_rest_of_the_cycle_still_runs(rig):
    rig.driver.fail_mem_available = True   # the memory check blows up first
    rig.driver.dialog_scan_value = {
        "focus": "com.google.android.gm/.ConversationListActivityGmail", "dialog": None}
    d = doc(rig)
    d.cycle()
    assert d.errors == 1
    rows = journal(rig, "doctor_check_error")
    assert len(rows) == 1 and json.loads(rows[0]["detail"])["check"] == "memory"
    # the dialog check behind it still ran and still acted:
    assert d.actions.get("foreign_focus") == 1
    assert d.last_ok_at == L.utc(rig.clock())   # cycle() itself never raised


def test_a_failing_cycle_never_kills_the_thread(rig):
    class Flaky:
        """Everything delegates to the real ledger (its own ``note`` included -- the error path
        below has to be ABLE to journal what went wrong) except the one call scripted to blow up."""

        def __getattr__(self, name):
            return getattr(rig.ledger, name)

        def phone_ops_active(self):
            raise RuntimeError("sqlite is gone")

    d = doc(rig)
    d.executor.ledger = Flaky()
    d.cycle()
    assert d.errors == 1
    assert d.cycles == 1
    assert d.last_ok_at is None   # this cycle did not succeed
    d.executor.ledger = rig.ledger   # restore -- the next cycle must still be able to run cleanly
    d.cycle()
    assert d.cycles == 2
    assert d.last_ok_at == L.utc(rig.clock())


# --- /v1/health's own "doctor" section --------------------------------------------------------------
def test_the_heartbeat_carries_the_shape_health_needs(rig):
    d = doc(rig, interval=45, min_mem_mb=800)
    d.cycle()
    hb = d.heartbeat()
    assert hb["interval_sec"] == 45
    assert hb["mem_available_mb"] == rig.driver.mem_available_mb_value
    assert hb["skipped_busy"] == 0
    assert hb["errors"] == 0
    assert hb["last_ok_at"] == L.utc(rig.clock())
    for key in ("alive", "started_at", "last_run_at", "actions", "last_action"):
        assert key in hb


def test_health_reports_the_doctor_when_wired(rig):
    started = doc(rig).start()
    try:
        heartbeat = rig.executor.health()["doctor"]
        assert heartbeat["alive"] is True
    finally:
        started.stop()


def test_health_reports_no_doctor_when_none_is_wired(rig):
    assert rig.executor.health()["doctor"] is None
