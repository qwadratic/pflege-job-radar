"""PhoneDoctor: continuous self-heal for the handset (TASK-315 AC#9).

Ivan, 2026-09-29: "автоматически, постоянно устранять всё, что мешает основному сценарию --
автоматизации WhatsApp" -- automatically, continuously remove everything on the phone that gets in
the way of the main scenario. Two incidents drove the original version of this file:

  2026-09-25/26: WhatsApp's own SmsDefaultAppWarning dialog (a send to a number not on WhatsApp)
  and Android's "USB-Nutzung" dialog both BLOCK EVERY SEND for as long as nothing dismisses them,
  and neither is closed by park()'s own BACK-then-launch-then-HOME loop: SmsDefaultAppWarning
  ignores BACK and sits inside WhatsApp's own task (park()'s monkey launch resumes straight back
  into it), and USB-Nutzung ignores BACK too (it lives in com.android.settings, outside WhatsApp
  entirely -- a monkey launch of WhatsApp never even reaches it).

  2026-09-29 10:15-10:32 UTC: the handset ran low on memory (244 MB free + 1.7 GB swap, ChatGPT/
  YouTube/Gmail left running in the background) and every send timed out waiting on the phone lock.

REVIEW FIX (TASK-315, Opus reject 2026-09-29, Ivan's option B): a THIRD incident-driven check --
periodically foregrounding WhatsApp's own task with ``resume_whatsapp()`` just to read what it
resumed into -- was rejected outright: foregrounding WhatsApp suppresses notifications and can
surface (or even open) a Conversation, which sends read receipts. That whole per-minute probe
(``_check_whatsapp_task``/``_settle_on_home``, the BACK loop, the "unknown foreign focus -> BACK +
HOME" remedy) is GONE. This file now:
  * never calls ``resume_whatsapp()`` except for the one remedy that needs a genuinely DEAD
    WhatsApp process relaunched -- and always follows it with ``home()`` in the same breath,
    never reading or branching on what it resumed into;
  * never presses BACK at all (the verb itself is gone from PhoneDriver -- bridge/driver.py's own
    "few verbs" discipline: a verb nothing calls does not get to exist "just in case");
  * only records the last focus seen into health, never acts on an "unknown" one;
  * rate-limits and backs off every remedy (15 min between attempts of the same kind, 3
    consecutive attempts that do not clear the condition -> give up and say so once);
  * re-checks ``phone_ops_active()`` before EACH individual check, not just once per cycle, and
    gates the whole screen-reading check on ``screen_ready()`` and a sane focus shape;
  * drains pending notifications into the ledger BEFORE any force-stop, so a force-stop can never
    silently wipe an unread message;
  * honours the same by-hand operator-hold file InboundWatcher already does.

HARD RULE, and it is why every check below starts the way it does: THIS THREAD MUST NEVER DELAY OR
FAIL A REAL OPERATION. A dispatched op (bridge/dispatcher.py) always wins -- the doctor checks
``ledger.phone_ops_active()`` before it ever tries the lock (cheaper than a lock attempt, and it
catches a row that is merely queued, not yet holding the lock), re-checks it before every single
check inside the cycle too (a fresh op can be queued mid-cycle), and even then only takes
``huawei01.lock`` NON-BLOCKING (``timeout=0``, the same probe ``InboundWatcher._check_idle_dirty``
uses): busy is a skip, counted, never a wait and never an error.

WHY THE DECIDING LIVES HERE AND NOT IN adb_driver.py. bridge/driver.py's own module docstring:
"Deliberately few verbs. Anything richer would put decisions in the driver, and the driver is the
one layer we cannot unit-test." Every driver verb this module calls is a single, low-level touch
(one tap, one key, one shell command) or a read that hands back plain data (``dialog_scan()``'s own
docstring) -- WHICH remedy runs for WHAT, and whether it is even allowed to run right now (rate
limit, give-up, screen readiness), is decided here, in Python that runs unchanged against
``bridge.driver.FakeDriver`` in tests/test_bridge_doctor.py.

WHY 'NEVER TAP Einladen/SMS' IS STRUCTURAL, NOT A RULE THIS FILE HAS TO REMEMBER. There is no verb
anywhere on PhoneDriver that taps a button by its text -- only ``tap_point(x, y)``, a bare
coordinate ``dialog_scan()`` computed from the dimmed scrim OUTSIDE the dialog's own frame
(adb_driver.py's own ``_outside_frame_tap``, rewritten this review to verify clearance rather than
assume it -- see its own docstring). This code could not target Einladen or SMS even if it tried
to: it never reads what either button says. When no safe point can be computed at all, ``tap`` is
None and this file gives up on that dialog rather than guess (point 3 of the review).

WHY A CONVERSATION IS NEVER TOUCHED HERE. ``bridge/watcher.py::InboundWatcher``'s own idle
self-check already owns a genuinely open chat -- two cycles to confirm it is not a send still
mid-flight, an operator-hold file a human can set, and a READ before it ever parks (TASK-234: the
screen clears its own notification the moment it is opened, so parking without reading loses
whatever a candidate sent in the seconds before). This file never resumes WhatsApp into anything to
find out, never dumps/taps against a Conversation, and defers entirely to that watcher's own,
slower, more careful schedule for that screen.
"""
from __future__ import annotations

import re
import threading
from contextlib import ExitStack
from pathlib import Path

from . import driver as D
from . import ledger as L
from . import watcher as W

#: How often a cycle runs. Not a cap on anything -- how often we ask, same as every other watcher's
#: own DEFAULT_*_INTERVAL_SEC (bridge/watcher.py). One minute is fast next to how long a stray
#: dialog or a low-memory phone can sit blocking every send before a human would otherwise notice
#: (the 2026-09-25 incident sat for 3 hours), and slow next to the cost of one cycle's own touches.
DEFAULT_INTERVAL_SEC = 60.0

#: Below this, ``am kill-all`` runs (TASK-315, 2026-09-29 incident: 244 MB free + 1.7 GB swap, every
#: send timing out on the phone lock; manual fix was ``am kill-all`` -> 1.46 GB). A first number, not
#: a reviewed one -- same caveat as this codebase's other first-cut thresholds (e.g.
#: bridge/relay_pull.py's own WATCHER_STALE_SEC) -- revisit if a real cycle ever kills at a level
#: that turns out to still be enough headroom, or too little.
DEFAULT_MIN_MEM_MB = 700

#: TASK-315 review point 5: no remedy kind may run more than once in this window. Applies uniformly
#: to memory/sms_default_app_warning/usb_nutzung/whatsapp_dead -- a single shared clock per kind,
#: not one constant per remedy, so there is exactly one number to tune.
RATE_LIMIT_SEC = 15 * 60

#: TASK-315 review point 5: after this many consecutive attempts of the same kind whose condition
#: is STILL present the next time it is checked, that kind gives up (health.gave_up[kind]) and
#: stops attempting -- until the condition is actually seen cleared. "Still present next time it is
#: checked" is this poller's own natural, cheap way to observe persistence: each attempt is only
#: even reached because the previous one did not clear the condition (see _note_condition_clear).
GIVE_UP_STRIKES = 3

#: TASK-315 review point 9: how long a phone_ops row may sit ``running`` before health calls it out
#: as ``blocked_by_stuck_op`` -- ``claim_next_op``'s own docstring on ``started_at`` never being
#: cleared by a crash is exactly the case this exists to surface to a human.
STUCK_OP_SEC = 10 * 60

#: A focus string shaped like Android's own ``package/Component`` (bridge/adb_driver.py::Adb.focus's
#: own regex, loosened here to a shape check only -- doctor.py does not need the exact character
#: class Adb uses to extract one, only to refuse a value that plainly is not this shape at all, e.g.
#: the raw dumpsys line Adb.focus() falls back to when its own regex did not match). TASK-315 review
#: point 7: "skip the screen checks... when focus does not parse as pkg/Activity".
_FOCUS_SHAPE_RE = re.compile(r"^[\w.]+/[\w.$]+$")


class PhoneDoctor:
    """One thread, one cycle at a time, default ON (TASK-315 AC#9). Started the same way every
    other watcher in bridge/watcher.py is (bridge/server.py::main), and reported into ``GET
    /v1/health``'s own "doctor" section the same way (bridge/executor.py::Executor.health)."""

    def __init__(self, executor, *, interval=DEFAULT_INTERVAL_SEC, min_mem_mb=DEFAULT_MIN_MEM_MB,
                 operator_hold_path=None, log=None):
        self.executor = executor
        self.interval = float(interval)
        self.min_mem_mb = int(min_mem_mb)
        # TASK-315 review point 8: the same by-hand escape hatch InboundWatcher already honours
        # (bridge/watcher.py::operator_hold_until, shared rather than reimplemented).
        self._operator_hold_path = Path(operator_hold_path) if operator_hold_path else None
        self._log = log or (lambda msg: None)
        self.started_at = None
        self.cycles = 0
        self.errors = 0
        self.last_run_at = None
        self.last_ok_at = None
        #: The most recent successful MemAvailable reading (TASK-315 AC#9's own health shape) --
        #: whether or not it was low enough to act on, so a cycle that found nothing wrong still
        #: leaves a fresh number for an operator to read.
        self.mem_available_mb = None
        #: The last focus() this doctor actually read (TASK-315 review point 1: the removed
        #: per-minute WhatsApp probe is replaced by nothing more than recording this -- never acted
        #: on alone). None until the first cycle that was allowed to read the screen at all.
        self.last_focus = None
        self.skipped_busy = 0
        #: Cycles skipped whole because an operator-hold file was in effect (TASK-315 review point
        #: 8) -- counted separately from skipped_busy per review point 9's own health-honesty rule.
        self.skipped_hold = 0
        #: {kind: {"since": iso, "detail": str}} -- a remedy kind that has stopped attempting
        #: itself (TASK-315 review point 5), cleared the moment its condition is next seen clear.
        self.gave_up = {}
        #: {kind: count} -- consecutive attempts of `kind` whose condition was still present the
        #: next time it was checked. Reset to nothing the moment the condition is seen clear.
        self._consecutive = {}
        #: {kind: datetime} -- when `kind` last actually ran (not merely detected), for the
        #: RATE_LIMIT_SEC gate. A plain dict of the `now` this class is always handed, never a
        #: wall-clock read of its own.
        self._last_run_at = {}
        #: None until a kill-all has actually run once; then True/False for whether THAT kill-all
        #: raised MemAvailable at all -- the memory-specific extra gate in review point 5 ("only if
        #: ... the previous kill-all actually raised it; otherwise gave_up").
        self._kill_all_helped = None
        #: {kind: count} -- one entry per ``doctor_<kind>`` journal event this instance has written.
        self.actions = {}
        #: {"kind", "at", "detail"} of the most recent remedy, or None. Same shape as
        #: bridge/watcher.py's own last_error/last_action-style fields: a quick glance at what this
        #: thread last actually DID, not only that it is alive.
        self.last_action = None
        self._thread = None
        self._stop = threading.Event()
        # Order matters only in that dialogs/recordings are pointless to chase on a phone so low on
        # memory the lock itself is the bottleneck -- memory goes first, everything else follows in
        # the same order the original incidents were discovered in.
        self._checks = (
            ("memory", self._check_memory),
            ("dialogs", self._check_dialogs),
            ("whatsapp_process", self._check_whatsapp_process),
            ("recordings", self._check_recordings),
        )

    # --- one cycle, so a test can run it without a thread (same shape as every watcher's cycle()) --
    def cycle(self):
        """-> None always; never raises (this module's own docstring: HARD RULE). A failure inside
        one check is caught by that check's own guard and never reaches here; this try/except is
        the outer net for anything ``_run`` itself does (the busy/hold checks, the lock) -- belt,
        not the only layer."""
        now = self.executor.clock()
        self.cycles += 1
        self.last_run_at = L.utc(now)
        try:
            ran = self._run(now)
        except Exception as exc:  # a bug in our own code, named as one and never silently swallowed
            self.errors += 1
            self.executor.ledger.note(now, "doctor_error", None,
                                      error=f"{type(exc).__name__}: {exc}")
            self._log(f"doctor: {type(exc).__name__}: {exc}")
            return
        # TASK-315 review point 9: last_ok_at only on a cycle that actually ran its checks -- a
        # whole-cycle skip (busy/hold) must never look like a successful check to a health reader.
        if ran:
            self.last_ok_at = L.utc(now)

    def _run(self, now):
        """-> True iff at least one check actually ran this cycle (see cycle()'s own use of this).
        """
        if self.executor.ledger.phone_ops_active():
            self.skipped_busy += 1
            return False
        hold_until = W.operator_hold_until(self._operator_hold_path, now, self.executor.ledger)
        if hold_until is not None and now < hold_until:
            self.skipped_hold += 1
            return False
        ran_any = False
        try:
            with ExitStack() as stack:
                stack.enter_context(self.executor.driver.lock(timeout=0))
                for label, fn in self._checks:
                    # TASK-315 review point 6: re-checked before EACH check, not just once per
                    # cycle -- a fresh op can be queued the instant after this cycle got the lock.
                    if self.executor.ledger.phone_ops_active():
                        break
                    self._safely(now, label, fn)
                    ran_any = True
        except D.DriverError:
            # Non-blocking probe lost the race, or the handset is genuinely gone -- either way,
            # NOTHING WAS TOUCHED (bridge/driver.py::PhoneBusy's own docstring), so this is a skip,
            # not an error.
            self.skipped_busy += 1
            return ran_any
        return ran_any

    def _safely(self, now, label, fn):
        """One check, isolated: an exception here is counted and journalled EXACTLY ONCE and never
        stops the checks behind it in the same cycle, let alone the thread (this module's own
        docstring). TASK-315 review point 9: "an adb failure is one error per cycle, not fake
        actions" -- a check that raises partway through a remedy never reaches its own
        _record_action call, so a failed remedy can never be journalled as though it succeeded."""
        try:
            fn(now)
        except Exception as exc:  # a bug in our own code, or a real DriverError -- either way named
            self.errors += 1
            self.executor.ledger.note(now, "doctor_check_error", None, check=label,
                                      error=f"{type(exc).__name__}: {exc}")
            self._log(f"doctor: {label} check failed: {type(exc).__name__}: {exc}")

    def _record_action(self, now, kind, detail):
        """A screenshot on every action (the card's own AC), journalled via
        ``ledger.note("doctor_<kind>", ...)``. The shot is best-effort: a capture failure is
        recorded IN the detail rather than raised -- the remedy itself already happened and must
        not be lost because the camera half of it did not work (same shape as
        ``bridge/executor.py::Executor._escalate``)."""
        driver = self.executor.driver
        try:
            shot = driver.escalation_shot(f"doctor_{kind}")
        except D.DriverError as exc:
            shot = f"(screenshot failed: {exc})"
        full = {"shot": shot, **detail}
        self.executor.ledger.note(now, f"doctor_{kind}", None, **full)
        self.actions[kind] = self.actions.get(kind, 0) + 1
        self.last_action = {"kind": kind, "at": L.utc(now), "detail": full}
        self._log(f"doctor: {kind} {full}")

    # --- shared rate-limit / backoff / give-up machinery (TASK-315 review point 5), one state
    # machine per remedy `kind`, reused identically by every remedy below ------------------------
    def _may_attempt(self, now, kind):
        """-> True iff `kind` is not already given up and at least RATE_LIMIT_SEC has passed since
        it last actually ran (never run at all counts as "may attempt")."""
        if kind in self.gave_up:
            return False
        last = self._last_run_at.get(kind)
        return last is None or (now - last).total_seconds() >= RATE_LIMIT_SEC

    def _note_condition_clear(self, kind):
        """The condition `kind` exists to fix was just observed NOT present. Forgets any
        consecutive-attempt streak and any give-up -- the remedy is fully live again the next time
        it is needed ("resume only after the condition was seen cleared")."""
        self._consecutive.pop(kind, None)
        self.gave_up.pop(kind, None)

    def _note_ran(self, now, kind):
        """Record that `kind` just actually ran. Bumps its consecutive-attempt streak -- reaching
        this call at all already means the condition was present (checked by the caller) and this
        is now one more attempt in a row without an intervening `_note_condition_clear`, which is
        this poller's own cheap definition of "still present afterwards". Gives up once the streak
        reaches GIVE_UP_STRIKES."""
        self._last_run_at[kind] = now
        streak = self._consecutive.get(kind, 0) + 1
        self._consecutive[kind] = streak
        if streak >= GIVE_UP_STRIKES:
            self._give_up(now, kind, f"{kind} still present after {streak} consecutive attempts")

    def _give_up(self, now, kind, detail, *, shot=False):
        """Enter (or stay in) the given-up state for `kind` -- journalled EXACTLY ONCE per episode
        (a `kind` already in ``self.gave_up`` is left alone, so a condition that keeps recurring
        every cycle does not spam the journal once a minute). `shot` takes one escalation
        screenshot into the same journal entry (TASK-315 review point 3: the no-safe-tap case)."""
        if kind in self.gave_up:
            return
        self.gave_up[kind] = {"since": L.utc(now), "detail": detail}
        self._consecutive.pop(kind, None)
        full = {"kind": kind, "detail": detail}
        if shot:
            try:
                full["shot"] = self.executor.driver.escalation_shot(f"doctor_gave_up_{kind}")
            except D.DriverError as exc:
                full["shot"] = f"(screenshot failed: {exc})"
        self.executor.ledger.note(now, "doctor_gave_up", None, **full)
        self._log(f"doctor: giving up on {kind}: {detail}")

    # --- check 1: memory (TASK-315, 2026-09-29 incident) -------------------------------------------
    def _check_memory(self, now):
        driver = self.executor.driver
        before = driver.mem_available_mb()
        self.mem_available_mb = before
        if before >= self.min_mem_mb:
            self._note_condition_clear("memory")
            return
        if "memory" in self.gave_up:
            return
        if self._kill_all_helped is False:
            # TASK-315 review point 5's own memory-specific gate: the LAST kill-all measurably did
            # not raise MemAvailable at all -- give up outright rather than keep re-running a
            # remedy already shown not to work on this phone right now.
            self._give_up(now, "memory", "previous kill-all did not raise MemAvailable")
            return
        if not self._may_attempt(now, "memory"):
            return
        driver.kill_background()
        after = driver.mem_available_mb()
        self.mem_available_mb = after
        self._kill_all_helped = after > before
        self._record_action(now, "memory", {"before_mb": before, "after_mb": after,
                                             "min_mem_mb": self.min_mem_mb})
        self._note_ran(now, "memory")

    # --- check 2: known stray dialogs, by focus + uiautomator bounds (TASK-315, 2026-09-25/26) -----
    def _check_dialogs(self, now):
        """TASK-315 review point 7: skipped whole when the screen is not awake, the keyguard is up,
        or focus does not parse as pkg/Activity -- dialog_scan() reads and interprets arbitrary
        on-screen content, which is not safe to do against a locked or asleep screen. Memory and
        pidof (_check_whatsapp_process) do not call this and are unaffected."""
        driver = self.executor.driver
        if not driver.screen_ready():
            return
        focus = driver.focus()
        self.last_focus = focus
        if not _FOCUS_SHAPE_RE.match(focus or ""):
            return
        scan = driver.dialog_scan()
        dialog = scan.get("dialog")
        if dialog is None:
            self._note_condition_clear("sms_default_app_warning")
            self._note_condition_clear("usb_nutzung")
            return
        kind = dialog.get("kind")
        tap = dialog.get("tap")
        if kind == "sms_default_app_warning":
            self._note_condition_clear("usb_nutzung")
            self._dismiss_sms_default_app_warning(now, focus, tap)
        elif kind == "usb_nutzung":
            self._note_condition_clear("sms_default_app_warning")
            self._dismiss_usb_nutzung(now, focus, tap)

    def _dismiss_sms_default_app_warning(self, now, focus, tap):
        """WhatsApp's 'number not on WhatsApp' warning (2026-09-25: blocked every send for 3 h).
        Ignores BACK and sits inside WhatsApp's own task, so a tap outside its frame closes the
        dialog but WhatsApp then simply resumes back into it on the next launch -- the fix proven
        live 2026-09-26 09:43 UTC is force-stop, then relaunch clean.

        TASK-315 review point 3: `tap` may be None (adb_driver::_outside_frame_tap's own contract,
        rewritten this review to verify clearance rather than assume it) -- a known dialog IS
        present but no safe point could be computed. Give up cleanly, with a screenshot, rather
        than guess.

        TASK-315 review point 4: the notification shade is drained into the ledger BEFORE the
        force-stop below -- force-stop must never be able to silently wipe an unread message."""
        driver = self.executor.driver
        if tap is None:
            self._give_up(now, "sms_default_app_warning",
                          "SmsDefaultAppWarning is present but no safe tap point was found",
                          shot=True)
            return
        if "sms_default_app_warning" in self.gave_up:
            return
        if not self._may_attempt(now, "sms_default_app_warning"):
            return
        self.executor.drain_inbound()
        driver.tap_point(*tap)
        driver.force_stop_whatsapp()
        driver.resume_whatsapp()
        driver.home()
        self._record_action(now, "sms_default_app_warning", {"focus": focus, "tap": list(tap)})
        self._note_ran(now, "sms_default_app_warning")

    def _dismiss_usb_nutzung(self, now, focus, tap):
        """Android's own USB-mode dialog (2026-09-26: appeared alongside SmsDefaultAppWarning).
        Lives in com.android.settings, outside WhatsApp entirely -- ABBRECHEN closes it and HOME is
        enough afterwards; there is no WhatsApp task to relaunch, so nothing here can wipe a
        notification and drain_inbound is not needed. `tap` is never None for this kind
        (adb_driver::dialog_scan only reports usb_nutzung once ABBRECHEN was actually found)."""
        driver = self.executor.driver
        if "usb_nutzung" in self.gave_up:
            return
        if not self._may_attempt(now, "usb_nutzung"):
            return
        driver.tap_point(*tap)
        driver.home()
        self._record_action(now, "usb_nutzung", {"focus": focus, "tap": list(tap)})
        self._note_ran(now, "usb_nutzung")

    # --- check 3: WhatsApp process dead (TASK-315 review point 2d) ---------------------------------
    def _check_whatsapp_process(self, now):
        """pidof only -- never gated on screen_ready() (review point 7: "memory and pidof checks
        still run"). Relaunches WITH an immediate home() in the same breath: this file never reads
        or branches on what the relaunch resumed into (this module's own docstring)."""
        driver = self.executor.driver
        if driver.whatsapp_running():
            self._note_condition_clear("whatsapp_dead")
            return
        if "whatsapp_dead" in self.gave_up:
            return
        if not self._may_attempt(now, "whatsapp_dead"):
            return
        driver.resume_whatsapp()
        driver.home()
        self._record_action(now, "whatsapp_dead", {})
        self._note_ran(now, "whatsapp_dead")

    # --- check 4: leftover recordings (TASK-228's own orphan, reused here) -------------------------
    def _check_recordings(self, now):
        """Not rate-limited/given-up like the other remedies above: sweeping is idempotent cleanup
        of files already proven safe to remove (adb_driver::sweep_orphaned_recordings' own >5 min
        age floor), not a response to a condition that can persist despite the remedy running."""
        removed = self.executor.driver.sweep_orphaned_recordings()
        if removed:
            self._record_action(now, "sweep_recordings", {"removed": removed})

    # --- thread lifecycle, same shape as every watcher in bridge/watcher.py ------------------------
    def run(self):
        self.started_at = L.utc(self.executor.clock())
        while not self._stop.is_set():
            self.cycle()
            self._stop.wait(self.interval)

    def start(self):
        self.executor.doctor = self
        self._thread = threading.Thread(target=self.run, name="wa-bridge-doctor", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)

    def heartbeat(self):
        now = self.executor.clock()
        stuck_age = self.executor.ledger.oldest_running_op_age_sec(now)
        return {"alive": bool(self._thread and self._thread.is_alive()),
                "interval_sec": self.interval,
                "started_at": self.started_at,
                "last_run_at": self.last_run_at,
                "last_ok_at": self.last_ok_at,
                "mem_available_mb": self.mem_available_mb,
                "last_focus": self.last_focus,
                "skipped_busy": self.skipped_busy,
                "skipped_hold": self.skipped_hold,
                # TASK-315 review point 9: a phone_ops row stuck ``running`` for too long is a live
                # fact, read fresh on every heartbeat() call, never cached from the doctor's own
                # (much slower) cycle cadence.
                "blocked_by_stuck_op": stuck_age is not None and stuck_age > STUCK_OP_SEC,
                "gave_up": {k: dict(v) for k, v in self.gave_up.items()},
                "actions": dict(self.actions),
                "last_action": self.last_action,
                "errors": self.errors}
