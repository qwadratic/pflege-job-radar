"""Offline proof for PhoneDoctor (TASK-315 AC#9). FakeDriver only -- no adb, no phone, no ssh.

TASK-315 review (Opus reject 2026-09-29, Ivan's option B): every test here proves one of the
TRIMMED doctor's own rules -- never a fake echo of behaviour the review deleted. In particular:
this file no longer has anything resembling the old per-minute WhatsApp task probe
(resume_whatsapp() used to read what it resumed into and BACK its way home; that whole check, and
the ``back()`` verb it used, are gone from the driver entirely). What is proven instead: rate
limiting and give-up per remedy kind, screen_ready()/focus-shape gating around the dialog check,
the operator-hold skip, drain-before-force-stop, the mid-cycle phone_ops_active() abort, and the
honest last_ok_at/skipped_busy/skipped_hold/blocked_by_stuck_op health shape.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from bridge import doctor as PD
from bridge import driver as D
from bridge import executor as X
from bridge import governor as G
from bridge import ledger as L


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


# --- busy: a real operation always wins ------------------------------------------------------------
def test_a_queued_phone_op_skips_the_cycle_without_touching_the_phone(rig):
    rig.ledger.enqueue_op("op.x", "list_chats", {}, rig.clock())
    d = doc(rig)
    d.cycle()
    assert d.skipped_busy == 1
    assert d.errors == 0
    assert d.last_ok_at is None   # a whole-cycle skip is not a run (review point 9)
    # NOTHING was touched: not a single driver verb this module owns was ever called.
    assert rig.driver.kill_all_calls == 0
    assert rig.driver.doctor_taps == []
    assert rig.driver.whatsapp_resumed == 0
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


def test_an_op_that_arrives_mid_cycle_aborts_the_rest_of_the_checks(rig):
    """TASK-315 review point 6: phone_ops_active() is re-checked before EACH check, not just once
    per cycle. A ledger proxy scripts exactly when "an op showed up" without a real race: the
    first call (before the lock) says no, the second (before the memory check) says no, the third
    (before the dialogs check) says yes -- memory must have run, nothing after it may have."""
    class ScriptedActive:
        def __init__(self, ledger, sequence):
            self._ledger = ledger
            self._sequence = list(sequence)

        def __getattr__(self, name):
            return getattr(self._ledger, name)

        def phone_ops_active(self):
            return self._sequence.pop(0) if self._sequence else False

    rig.driver.mem_available_mb_value = 244
    rig.driver.mem_available_after_kill = 1460
    rig.driver.dialog_scan_value = {
        "focus": "com.android.settings/.usb.UsbAccessoryUriActivity",
        "dialog": {"kind": "usb_nutzung", "tap": (900, 1500)}}
    d = doc(rig)
    d.executor.ledger = ScriptedActive(rig.ledger, [False, False, True])
    d.cycle()
    assert rig.driver.kill_all_calls == 1        # memory ran
    assert rig.driver.doctor_taps == []           # dialogs never got to run
    assert d.actions.get("memory") == 1
    assert "usb_nutzung" not in d.actions
    assert d.last_ok_at == L.utc(rig.clock())     # some checks ran -> this still counts (point 9)


# --- operator hold (TASK-315 review point 8) --------------------------------------------------------
def test_operator_hold_file_skips_the_whole_cycle(rig, tmp_path):
    hold_path = tmp_path / "operator_hold"
    hold_path.write_text(L.utc(rig.clock() + timedelta(minutes=10)))
    d = doc(rig, operator_hold_path=str(hold_path))
    d.cycle()
    assert d.skipped_hold == 1
    assert d.skipped_busy == 0
    assert d.last_ok_at is None
    assert rig.driver.lock_events == []


def test_an_expired_hold_file_does_not_skip(rig, tmp_path):
    hold_path = tmp_path / "operator_hold"
    hold_path.write_text(L.utc(rig.clock() - timedelta(minutes=1)))
    d = doc(rig, operator_hold_path=str(hold_path))
    d.cycle()
    assert d.skipped_hold == 0
    assert d.last_ok_at == L.utc(rig.clock())


def test_no_hold_path_configured_never_skips_for_it(rig):
    d = doc(rig)
    d.cycle()
    assert d.skipped_hold == 0


# --- check 1: memory (2026-09-29 10:15 UTC incident) -------------------------------------------------
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
    assert "memory" not in d.gave_up   # it worked -- nothing to give up on


def test_memory_above_the_floor_is_read_but_not_acted_on(rig):
    rig.driver.mem_available_mb_value = 2000
    d = doc(rig, min_mem_mb=700)
    d.cycle()
    assert rig.driver.kill_all_calls == 0
    assert d.mem_available_mb == 2000
    assert "memory" not in d.actions
    assert journal(rig, "doctor_memory") == []


def test_memory_remedy_is_rate_limited_to_once_per_15_minutes(rig):
    rig.driver.mem_available_mb_value = 244
    rig.driver.mem_available_after_kill = 300   # improves, but stays below the floor
    d = doc(rig, min_mem_mb=700)
    d.cycle()
    assert rig.driver.kill_all_calls == 1
    d.cycle()   # same clock moment -- must not re-run within 15 min
    assert rig.driver.kill_all_calls == 1
    rig.clock.advance(PD.RATE_LIMIT_SEC + 1)
    d.cycle()
    assert rig.driver.kill_all_calls == 2


def test_memory_gives_up_immediately_when_a_kill_all_did_not_help(rig):
    """TASK-315 review point 5's own memory-specific gate: a kill-all that achieved NOTHING is a
    stronger, faster signal than the generic 3-strikes rule -- give up on the very next occasion,
    not after two more wasted attempts."""
    rig.driver.mem_available_mb_value = 244
    rig.driver.mem_available_after_kill = 244   # the remedy changes nothing at all
    d = doc(rig, min_mem_mb=700)
    d.cycle()
    assert rig.driver.kill_all_calls == 1
    assert "memory" not in d.gave_up   # the first attempt always gets a chance
    rig.clock.advance(PD.RATE_LIMIT_SEC + 1)
    d.cycle()
    assert rig.driver.kill_all_calls == 1   # refused outright -- gave up instead of trying again
    assert d.gave_up["memory"]["detail"] == "previous kill-all did not raise MemAvailable"
    rows = journal(rig, "doctor_gave_up")
    assert len(rows) == 1 and json.loads(rows[0]["detail"])["kind"] == "memory"


def test_memory_gives_up_after_three_consecutive_kills_that_never_clear_it(rig):
    rig.driver.mem_available_mb_value = 500
    calls = {"n": 0}
    orig_kill = rig.driver.kill_background

    def scripted_kill():
        calls["n"] += 1
        orig_kill()
        rig.driver.mem_available_mb_value = 500 + calls["n"]  # improves a little, never clears

    rig.driver.kill_background = scripted_kill
    d = doc(rig, min_mem_mb=700)
    for _ in range(3):
        d.cycle()
        rig.clock.advance(PD.RATE_LIMIT_SEC + 1)
    assert calls["n"] == 3
    assert "memory" in d.gave_up
    assert d.gave_up["memory"]["detail"] == "memory still present after 3 consecutive attempts"
    # a 4th cycle must not attempt a 4th kill-all
    d.cycle()
    assert calls["n"] == 3


def test_memory_gave_up_state_clears_once_memory_recovers_on_its_own(rig):
    rig.driver.mem_available_mb_value = 244
    rig.driver.mem_available_after_kill = 244
    d = doc(rig, min_mem_mb=700)
    d.cycle()
    rig.clock.advance(PD.RATE_LIMIT_SEC + 1)
    d.cycle()
    assert "memory" in d.gave_up
    rig.driver.mem_available_mb_value = 5000   # something else (not the doctor) freed memory
    d.cycle()
    assert "memory" not in d.gave_up
    assert d.mem_available_mb == 5000


# --- check 2: known stray dialogs --------------------------------------------------------------------
def test_sms_default_app_warning_is_dismissed_by_tapping_outside_and_resetting_the_task(rig):
    """2026-09-25: this dialog blocked every send for 3h; 2026-09-26 09:43 UTC found the fix --
    tap outside, force-stop, relaunch. The tap recorded is the ONLY tap this whole cycle makes,
    which is what proves Einladen/SMS (this module has no verb that could even name them) was
    never addressed."""
    rig.driver.focus_value = "com.whatsapp/.registration.SmsDefaultAppWarning"
    rig.driver.dialog_scan_value = {
        "focus": "com.whatsapp/.registration.SmsDefaultAppWarning",
        "dialog": {"kind": "sms_default_app_warning", "tap": (120, 340)}}
    d = doc(rig)
    d.cycle()
    assert rig.driver.doctor_taps == [(120, 340)]
    assert rig.driver.force_stop_calls == 1
    assert rig.driver.whatsapp_resumed >= 1
    assert rig.driver.home_presses >= 1
    assert d.actions.get("sms_default_app_warning") == 1
    rows = journal(rig, "doctor_sms_default_app_warning")
    detail = json.loads(rows[0]["detail"])
    assert detail["tap"] == [120, 340]
    assert detail["focus"] == "com.whatsapp/.registration.SmsDefaultAppWarning"


def test_drain_inbound_runs_before_force_stop_so_an_unread_notification_is_never_wiped(rig):
    """TASK-315 review point 4: force-stop must never be able to silently wipe an unread
    notification -- the drain has to happen first, every time this remedy runs."""
    order = []
    real_drain = rig.executor.drain_inbound
    real_force_stop = rig.driver.force_stop_whatsapp
    rig.executor.drain_inbound = lambda: (order.append("drain"), real_drain())[-1]
    rig.driver.force_stop_whatsapp = lambda: (order.append("force_stop"), real_force_stop())[-1]
    rig.driver.dialog_scan_value = {
        "focus": "com.whatsapp/.registration.SmsDefaultAppWarning",
        "dialog": {"kind": "sms_default_app_warning", "tap": (120, 340)}}
    d = doc(rig)
    d.cycle()
    assert order == ["drain", "force_stop"]


def test_sms_default_app_warning_with_no_safe_tap_gives_up_with_a_screenshot(rig):
    """TASK-315 review point 3: dialog_scan() may report the dialog present with `tap: None` (no
    safe point could be computed) -- the doctor must do nothing at all and give up cleanly, once,
    with a screenshot, rather than guess or loop silently forever."""
    rig.driver.dialog_scan_value = {
        "focus": "com.whatsapp/.registration.SmsDefaultAppWarning",
        "dialog": {"kind": "sms_default_app_warning", "tap": None}}
    d = doc(rig)
    d.cycle()
    assert rig.driver.doctor_taps == []
    assert rig.driver.force_stop_calls == 0
    assert "sms_default_app_warning" not in d.actions
    assert d.gave_up["sms_default_app_warning"]["detail"] == \
        "SmsDefaultAppWarning is present but no safe tap point was found"
    rows = journal(rig, "doctor_gave_up")
    assert len(rows) == 1
    detail = json.loads(rows[0]["detail"])
    assert detail["kind"] == "sms_default_app_warning"
    assert detail["shot"] == "/fake/shots/doctor_gave_up_sms_default_app_warning.png"
    # a second cycle with the same no-safe-tap dialog must not journal a second doctor_gave_up
    d.cycle()
    assert len(journal(rig, "doctor_gave_up")) == 1


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


def test_dialog_remedies_are_rate_limited_to_once_per_15_minutes(rig):
    rig.driver.dialog_scan_value = {
        "focus": "com.android.settings/.usb.UsbAccessoryUriActivity",
        "dialog": {"kind": "usb_nutzung", "tap": (900, 1500)}}
    d = doc(rig)
    d.cycle()
    assert rig.driver.doctor_taps == [(900, 1500)]
    d.cycle()
    assert rig.driver.doctor_taps == [(900, 1500)]   # not tapped again within 15 min
    rig.clock.advance(PD.RATE_LIMIT_SEC + 1)
    d.cycle()
    assert rig.driver.doctor_taps == [(900, 1500), (900, 1500)]


def test_usb_nutzung_gives_up_after_three_consecutive_attempts(rig):
    rig.driver.dialog_scan_value = {
        "focus": "com.android.settings/.usb.UsbAccessoryUriActivity",
        "dialog": {"kind": "usb_nutzung", "tap": (900, 1500)}}
    d = doc(rig)
    for _ in range(3):
        d.cycle()
        rig.clock.advance(PD.RATE_LIMIT_SEC + 1)
    assert len(rig.driver.doctor_taps) == 3
    assert "usb_nutzung" in d.gave_up
    d.cycle()
    assert len(rig.driver.doctor_taps) == 3   # a 4th attempt never happens


def test_no_dialog_present_clears_any_prior_give_up_or_streak(rig):
    rig.driver.dialog_scan_value = {
        "focus": "com.android.settings/.usb.UsbAccessoryUriActivity",
        "dialog": {"kind": "usb_nutzung", "tap": (900, 1500)}}
    d = doc(rig)
    for _ in range(3):
        d.cycle()
        rig.clock.advance(PD.RATE_LIMIT_SEC + 1)
    assert "usb_nutzung" in d.gave_up
    rig.driver.dialog_scan_value = {"focus": "com.android.launcher/.Home", "dialog": None}
    d.cycle()
    assert "usb_nutzung" not in d.gave_up


def test_screen_not_ready_skips_the_dialog_check_entirely(rig):
    """TASK-315 review point 7: asleep or locked, the dump is never even taken."""
    rig.driver.screen_ready_value = False
    rig.driver.dialog_scan_value = {
        "focus": "com.whatsapp/.registration.SmsDefaultAppWarning",
        "dialog": {"kind": "sms_default_app_warning", "tap": (120, 340)}}
    d = doc(rig)
    d.cycle()
    assert rig.driver.doctor_taps == []
    assert rig.driver.force_stop_calls == 0
    assert d.last_focus is None   # focus was never even read this cycle
    assert d.actions == {}


def test_focus_that_does_not_parse_as_pkg_activity_skips_the_dialog_check(rig):
    """TASK-315 review point 7: a malformed focus (Adb.focus()'s own raw-dumpsys-line fallback) is
    never handed to dialog_scan()'s dump/tap logic."""
    rig.driver.focus_value = "ERROR: could not get focus"
    rig.driver.dialog_scan_value = {
        "focus": "com.whatsapp/.registration.SmsDefaultAppWarning",
        "dialog": {"kind": "sms_default_app_warning", "tap": (120, 340)}}
    d = doc(rig)
    d.cycle()
    assert rig.driver.doctor_taps == []
    assert d.last_focus == "ERROR: could not get focus"   # still recorded (review point 1)


def test_a_clean_launcher_focus_touches_nothing(rig):
    rig.driver.focus_value = "com.android.launcher/.Home"
    rig.driver.dialog_scan_value = {"focus": "com.android.launcher/.Home", "dialog": None}
    d = doc(rig)
    d.cycle()
    assert rig.driver.doctor_taps == []
    assert d.last_focus == "com.android.launcher/.Home"
    assert d.actions == {}


def test_an_arbitrary_whatsapp_or_foreign_focus_is_never_acted_on_by_the_dialog_check(rig):
    """TASK-315 review point 1: the old "unknown foreign focus -> BACK + HOME" remedy is gone.
    ANY focus that is not a known dialog is left alone by this check -- only recorded."""
    rig.driver.focus_value = \
        "com.whatsapp/.conversation.conversationslist.ArchivedConversationsActivity"
    rig.driver.dialog_scan_value = {
        "focus": "com.whatsapp/.conversation.conversationslist.ArchivedConversationsActivity",
        "dialog": None}
    d = doc(rig)
    d.cycle()
    assert rig.driver.doctor_taps == []
    assert rig.driver.home_presses == 0
    assert d.last_focus == \
        "com.whatsapp/.conversation.conversationslist.ArchivedConversationsActivity"
    assert d.actions == {}


def test_a_conversation_focus_is_never_touched_by_this_check(rig):
    """InboundWatcher's own idle self-check owns a genuinely open chat -- reading it before parking
    is its job, on its own 2-cycle-confirmed schedule; this doctor must never race ahead of it, and
    never even calls resume_whatsapp() to find out (this module's own docstring)."""
    rig.driver.focus_value = "com.whatsapp/.Conversation"
    rig.driver.dialog_scan_value = {"focus": "com.whatsapp/.Conversation", "dialog": None}
    d = doc(rig)
    d.cycle()
    assert rig.driver.doctor_taps == []
    assert rig.driver.home_presses == 0
    assert rig.driver.whatsapp_resumed == 0
    assert d.actions == {}


# --- check 3: WhatsApp process dead (TASK-315 review point 2d) --------------------------------------
def test_whatsapp_process_dead_is_relaunched_and_sent_home(rig):
    rig.driver.whatsapp_alive = False
    d = doc(rig)
    d.cycle()
    assert rig.driver.whatsapp_resumed == 1
    assert rig.driver.whatsapp_alive is True
    assert rig.driver.home_presses >= 1
    assert d.actions.get("whatsapp_dead") == 1
    rows = journal(rig, "doctor_whatsapp_dead")
    assert len(rows) == 1


def test_whatsapp_process_alive_is_left_alone(rig):
    rig.driver.whatsapp_alive = True
    d = doc(rig)
    d.cycle()
    assert rig.driver.whatsapp_resumed == 0
    assert "whatsapp_dead" not in d.actions


def test_whatsapp_dead_remedy_is_rate_limited_and_gives_up_after_three(rig):
    rig.driver.whatsapp_alive = False

    def relaunch_but_stay_dead():
        rig.driver.whatsapp_resumed += 1
        rig.driver.whatsapp_alive = False   # override FakeDriver's own default "comes back alive"

    rig.driver.resume_whatsapp = relaunch_but_stay_dead
    d = doc(rig)
    d.cycle()
    assert rig.driver.whatsapp_resumed == 1
    d.cycle()   # same clock moment -- rate limited
    assert rig.driver.whatsapp_resumed == 1
    for _ in range(2):
        rig.clock.advance(PD.RATE_LIMIT_SEC + 1)
        d.cycle()
    assert rig.driver.whatsapp_resumed == 3
    assert "whatsapp_dead" in d.gave_up
    rig.clock.advance(PD.RATE_LIMIT_SEC + 1)
    d.cycle()
    assert rig.driver.whatsapp_resumed == 3   # a 4th relaunch never happens


# --- check 4: leftover recordings (TASK-228's own orphan sweep, reused here) -----------------------
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


# --- one check's exception never stops the others, or the thread ------------------------------------
def test_a_failing_check_is_counted_and_journalled_once_and_the_rest_of_the_cycle_still_runs(rig):
    rig.driver.fail_mem_available = True   # the memory check blows up first
    rig.driver.dialog_scan_value = {
        "focus": "com.android.settings/.usb.UsbAccessoryUriActivity",
        "dialog": {"kind": "usb_nutzung", "tap": (900, 1500)}}
    d = doc(rig)
    d.cycle()
    assert d.errors == 1
    rows = journal(rig, "doctor_check_error")
    assert len(rows) == 1 and json.loads(rows[0]["detail"])["check"] == "memory"
    # the dialog check behind it still ran and still acted:
    assert d.actions.get("usb_nutzung") == 1
    assert d.last_ok_at == L.utc(rig.clock())   # cycle() itself never raised


def test_a_failed_remedy_never_journals_a_fake_action(rig):
    """TASK-315 review point 9: 'an adb failure is one error per cycle, not fake actions.' A remedy
    that blows up PART way through must never reach _record_action for that kind."""
    rig.driver.dialog_scan_value = {
        "focus": "com.android.settings/.usb.UsbAccessoryUriActivity",
        "dialog": {"kind": "usb_nutzung", "tap": (900, 1500)}}

    def boom():
        raise D.DriverError("adb timed out")

    rig.driver.home = boom   # the LAST step of _dismiss_usb_nutzung
    d = doc(rig)
    d.cycle()
    assert d.errors == 1
    assert "usb_nutzung" not in d.actions
    assert journal(rig, "doctor_usb_nutzung") == []


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


# --- /v1/health's own "doctor" section ----------------------------------------------------------------
def test_the_heartbeat_carries_the_shape_health_needs(rig):
    d = doc(rig, interval=45, min_mem_mb=800)
    d.cycle()
    hb = d.heartbeat()
    assert hb["interval_sec"] == 45
    assert hb["mem_available_mb"] == rig.driver.mem_available_mb_value
    assert hb["skipped_busy"] == 0
    assert hb["skipped_hold"] == 0
    assert hb["errors"] == 0
    assert hb["last_ok_at"] == L.utc(rig.clock())
    assert hb["blocked_by_stuck_op"] is False
    assert hb["gave_up"] == {}
    for key in ("alive", "started_at", "last_run_at", "last_focus", "actions", "last_action"):
        assert key in hb


def test_blocked_by_stuck_op_appears_after_ten_minutes_running(rig):
    rig.ledger.enqueue_op("op.stuck", "list_chats", {}, rig.clock())
    started = L.utc(rig.clock() - timedelta(minutes=15))
    rig.ledger._db.execute("update phone_ops set state=?, started_at=? where op_id=?",
                           (L.OP_RUNNING, started, "op.stuck"))
    rig.ledger._db.commit()
    d = doc(rig)
    assert d.heartbeat()["blocked_by_stuck_op"] is True


def test_not_yet_blocked_by_stuck_op_under_ten_minutes(rig):
    rig.ledger.enqueue_op("op.stuck", "list_chats", {}, rig.clock())
    started = L.utc(rig.clock() - timedelta(minutes=2))
    rig.ledger._db.execute("update phone_ops set state=?, started_at=? where op_id=?",
                           (L.OP_RUNNING, started, "op.stuck"))
    rig.ledger._db.commit()
    d = doc(rig)
    assert d.heartbeat()["blocked_by_stuck_op"] is False


def test_gave_up_kinds_appear_in_the_heartbeat(rig):
    rig.driver.mem_available_mb_value = 244
    rig.driver.mem_available_after_kill = 244
    d = doc(rig, min_mem_mb=700)
    d.cycle()
    rig.clock.advance(PD.RATE_LIMIT_SEC + 1)
    d.cycle()
    hb = d.heartbeat()
    assert "memory" in hb["gave_up"]
    assert hb["gave_up"]["memory"]["detail"] == "previous kill-all did not raise MemAvailable"
    assert "since" in hb["gave_up"]["memory"]


def test_health_reports_the_doctor_when_wired(rig):
    started = doc(rig).start()
    try:
        heartbeat = rig.executor.health()["doctor"]
        assert heartbeat["alive"] is True
    finally:
        started.stop()


def test_health_reports_no_doctor_when_none_is_wired(rig):
    assert rig.executor.health()["doctor"] is None
