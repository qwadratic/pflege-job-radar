"""PhoneDoctor: continuous self-heal for the handset (TASK-315 AC#9).

Ivan, 2026-09-29: "автоматически, постоянно устранять всё, что мешает основному сценарию --
автоматизации WhatsApp" -- automatically, continuously remove everything on the phone that gets in
the way of the main scenario. Two incidents drove this:

  2026-09-25/26: WhatsApp's own SmsDefaultAppWarning dialog (a send to a number not on WhatsApp)
  and Android's "USB-Nutzung" dialog both BLOCK EVERY SEND for as long as nothing dismisses them,
  and neither is closed by park()'s own BACK-then-launch-then-HOME loop: SmsDefaultAppWarning
  ignores BACK and sits inside WhatsApp's own task (park()'s monkey launch resumes straight back
  into it), and USB-Nutzung ignores BACK too (it lives in com.android.settings, outside WhatsApp
  entirely -- a monkey launch of WhatsApp never even reaches it).

  2026-09-29 10:15-10:32 UTC: the handset ran low on memory (244 MB free + 1.7 GB swap, ChatGPT/
  YouTube/Gmail left running in the background) and every send timed out waiting on the phone lock.

  2026-09-29 10:15-10:32 UTC (a second incident the same morning): WhatsApp's own
  ArchivedConversationsActivity was left on top of WhatsApp's task. park() pressed HOME, so
  focus() read clean (the launcher) -- but the task UNDERNEATH was still broken, and every later
  relaunch kept resuming the archive rather than the chat list. A focus read taken AFTER park()'s
  own HOME can never show this; only actively bringing the task forward and reading what it
  resumes INTO can (see PhoneDriver.resume_whatsapp's own docstring).

HARD RULE, and it is why every check below starts the way it does: THIS THREAD MUST NEVER DELAY OR
FAIL A REAL OPERATION. A dispatched op (bridge/dispatcher.py) always wins -- the doctor checks
``ledger.phone_ops_active()`` BEFORE it ever tries the lock (cheaper than a lock attempt, and it
catches a row that is merely queued, not yet holding the lock, the same way
``bridge/watcher.py``'s ``BroadcastRunner``/``IdentityWatcher`` already step aside on
``phone_ops_queue_counts()``), and even then only takes ``huawei01.lock`` NON-BLOCKING
(``timeout=0``, the same probe ``InboundWatcher._check_idle_dirty`` uses): busy is a skip, counted,
never a wait and never an error.

WHY THE DECIDING LIVES HERE AND NOT IN adb_driver.py. bridge/driver.py's own module docstring:
"Deliberately few verbs. Anything richer would put decisions in the driver, and the driver is the
one layer we cannot unit-test." Every driver verb this module calls is a single, low-level touch
(one tap, one key, one shell command) or a read that hands back plain data
(``dialog_scan()``'s own docstring) -- WHICH remedy runs for WHAT is decided here, in Python that
runs unchanged against ``bridge.driver.FakeDriver`` in tests/test_bridge_doctor.py.

WHY 'NEVER TAP Einladen/SMS' IS STRUCTURAL, NOT A RULE THIS FILE HAS TO REMEMBER. There is no verb
anywhere on PhoneDriver that taps a button by its text -- only ``tap_point(x, y)``, a bare
coordinate ``dialog_scan()`` computed from the dimmed scrim OUTSIDE the dialog's own frame
(adb_driver.py's own ``_outside_frame_tap``). This code could not target Einladen or SMS even if it
tried to: it never reads what either button says.

WHY A CONVERSATION IS NEVER TOUCHED HERE. ``bridge/watcher.py::InboundWatcher``'s own idle
self-check already owns a genuinely open chat -- two cycles to confirm it is not a send still
mid-flight, an operator-hold file a human can set, and a READ before it ever parks (TASK-234: the
screen clears its own notification the moment it is opened, so parking without reading loses
whatever a candidate sent in the seconds before). Every check below that could reach a Conversation
checks for it first and returns without touching anything the moment it sees one -- deferring
entirely to that watcher's own, slower, more careful schedule, rather than duplicating a cruder
version of it here.
"""
from __future__ import annotations

import threading
from contextlib import ExitStack

from . import adb_driver as AD
from . import driver as D
from . import ledger as L

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

#: BACK presses given a shot at reaching WhatsApp's own HomeActivity before force-stopping
#: (TASK-315 addendum, 2026-09-29 archive-resume incident: BACK -> HomeActivity closed it live in
#: one press from the archive; three is headroom for a deeper stack -- a contact page or a settings
#: screen reached some other way -- without looping indefinitely on a screen BACK genuinely cannot
#: leave, which is exactly what the SmsDefaultAppWarning dialog does and why that check does not
#: use this loop at all).
MAX_BACK_ATTEMPTS = 3

#: WhatsApp's own package (bridge/adb_driver.py's own constant, imported rather than duplicated --
#: this module's own docstring on why "no fixed coordinates, no guessed button text" also applies
#: to not re-typing a string this codebase already names once).
_WHATSAPP = AD.WHATSAPP


def _is_launcher(focus):
    """-> True when ``focus`` names the system launcher, not WhatsApp and not some other foreign
    app. No single package name is reliable across a real device fleet, but every launcher this
    codebase is ever going to meet -- and bridge.driver.FakeDriver's own default focus_value --
    names itself 'launcher' somewhere in its package, so that is the one thing checked."""
    pkg = focus.split("/", 1)[0] if focus else ""
    return "launcher" in pkg.lower()


def _is_whatsapp(focus):
    return focus.startswith(_WHATSAPP)


def _is_whatsapp_conversation(focus):
    return focus.startswith(_WHATSAPP) and focus.endswith("Conversation")


def _is_whatsapp_home(focus):
    return focus.startswith(_WHATSAPP) and focus.endswith("HomeActivity")


class PhoneDoctor:
    """One thread, one cycle at a time, default ON (TASK-315 AC#9). Started the same way every
    other watcher in bridge/watcher.py is (bridge/server.py::main), and reported into ``GET
    /v1/health``'s own "doctor" section the same way (bridge/executor.py::Executor.health)."""

    def __init__(self, executor, *, interval=DEFAULT_INTERVAL_SEC, min_mem_mb=DEFAULT_MIN_MEM_MB,
                 log=None):
        self.executor = executor
        self.interval = float(interval)
        self.min_mem_mb = int(min_mem_mb)
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
        self.skipped_busy = 0
        #: {kind: count} -- one entry per ``doctor_<kind>`` journal event this instance has written.
        self.actions = {}
        #: {"kind", "at", "detail"} of the most recent remedy, or None. Same shape as
        #: bridge/watcher.py's own last_error/last_action-style fields: a quick glance at what this
        #: thread last actually DID, not only that it is alive.
        self.last_action = None
        self._thread = None
        self._stop = threading.Event()

    # --- one cycle, so a test can run it without a thread (same shape as every watcher's cycle()) --
    def cycle(self):
        """-> None always; never raises (this module's own docstring: HARD RULE). A failure inside
        one check is caught by that check's own guard and never reaches here; this try/except is
        the outer net for anything ``_run`` itself does (the busy checks, the lock) -- belt, not
        the only layer."""
        now = self.executor.clock()
        self.cycles += 1
        self.last_run_at = L.utc(now)
        try:
            self._run(now)
        except Exception as exc:  # a bug in our own code, named as one and never silently swallowed
            self.errors += 1
            self.executor.ledger.note(now, "doctor_error", None,
                                      error=f"{type(exc).__name__}: {exc}")
            self._log(f"doctor: {type(exc).__name__}: {exc}")
            return
        self.last_ok_at = L.utc(now)

    def _run(self, now):
        if self.executor.ledger.phone_ops_active():
            self.skipped_busy += 1
            return
        try:
            with ExitStack() as stack:
                stack.enter_context(self.executor.driver.lock(timeout=0))
                self._safely(now, "memory", self._check_memory)
                self._safely(now, "dialogs", self._check_dialogs)
                self._safely(now, "whatsapp_task", self._check_whatsapp_task)
                self._safely(now, "recordings", self._check_recordings)
        except D.DriverError:
            # Non-blocking probe lost the race, or the handset is genuinely gone -- either way,
            # NOTHING WAS TOUCHED (bridge/driver.py::PhoneBusy's own docstring), so this is a skip,
            # not an error.
            self.skipped_busy += 1

    def _safely(self, now, label, fn):
        """One check, isolated: an exception here is counted and journalled and never stops the
        checks behind it in the same cycle, let alone the thread (this module's own docstring)."""
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

    # --- check 1: memory (TASK-315, 2026-09-29 incident) -------------------------------------------
    def _check_memory(self, now):
        driver = self.executor.driver
        before = driver.mem_available_mb()
        self.mem_available_mb = before
        if before >= self.min_mem_mb:
            return
        driver.kill_background()
        after = driver.mem_available_mb()
        self.mem_available_mb = after
        self._record_action(now, "memory", {"before_mb": before, "after_mb": after,
                                             "min_mem_mb": self.min_mem_mb})

    # --- check 2: known stray dialogs, by focus + uiautomator bounds (TASK-315, 2026-09-25/26) -----
    def _check_dialogs(self, now):
        driver = self.executor.driver
        scan = driver.dialog_scan()
        focus = scan.get("focus", "") or ""
        dialog = scan.get("dialog")
        if dialog is not None and dialog.get("kind") == "sms_default_app_warning":
            self._dismiss_sms_default_app_warning(now, focus, dialog["tap"])
            return
        if dialog is not None and dialog.get("kind") == "usb_nutzung":
            self._dismiss_usb_nutzung(now, focus, dialog["tap"])
            return
        if _is_launcher(focus) or _is_whatsapp(focus):
            # Nothing to do: already clean, or any WhatsApp state at all -- Home/Conversation need
            # nothing, and anything else WhatsApp-shaped is _check_whatsapp_task's own job below
            # (this module's own docstring on why a Conversation specifically is never touched here).
            return
        # A genuinely foreign, non-WhatsApp, non-launcher app in front. One BACK, then HOME --
        # never a tap on something this code has no reason to believe it understands.
        driver.back()
        driver.home()
        self._record_action(now, "foreign_focus", {"focus": focus})

    def _dismiss_sms_default_app_warning(self, now, focus, tap):
        """WhatsApp's 'number not on WhatsApp' warning (2026-09-25: blocked every send for 3 h).
        Ignores BACK and sits inside WhatsApp's own task, so a tap outside its frame closes the
        dialog but WhatsApp then simply resumes back into it on the next launch -- the fix proven
        live 2026-09-26 09:43 UTC is force-stop, then relaunch clean."""
        driver = self.executor.driver
        driver.tap_point(*tap)
        driver.force_stop_whatsapp()
        driver.resume_whatsapp()
        driver.home()
        self._record_action(now, "sms_default_app_warning", {"focus": focus, "tap": list(tap)})

    def _dismiss_usb_nutzung(self, now, focus, tap):
        """Android's own USB-mode dialog (2026-09-26: appeared alongside SmsDefaultAppWarning).
        Lives in com.android.settings, outside WhatsApp entirely -- ABBRECHEN closes it and HOME is
        enough afterwards; there is no WhatsApp task to relaunch."""
        driver = self.executor.driver
        driver.tap_point(*tap)
        driver.home()
        self._record_action(now, "usb_nutzung", {"focus": focus, "tap": list(tap)})

    # --- check 3: WhatsApp's own task -- dead, or resumed into the wrong place (TASK-315 addendum,
    # 2026-09-29 archive-resume incident) -----------------------------------------------------------
    def _check_whatsapp_task(self, now):
        driver = self.executor.driver
        was_alive = driver.whatsapp_running()
        resumed = driver.resume_whatsapp()
        if _is_whatsapp_conversation(resumed):
            return  # an open chat is InboundWatcher's own job (this module's own docstring)
        if was_alive and _is_whatsapp_home(resumed):
            # Already alive and resumed clean -- our own probe is the only thing that foregrounded
            # it, so put it back exactly where it was (this is tidying up after ourselves, not a
            # remedy: nothing here is journalled).
            driver.home()
            return
        final = self._settle_on_home(driver)
        if _is_whatsapp_conversation(final):
            return  # the BACK loop surfaced an open chat -- leave it, same reasoning as above
        driver.home()
        kind = "whatsapp_relaunch" if not was_alive else "whatsapp_task_reset"
        self._record_action(now, kind, {"was_alive": was_alive, "resumed_focus": resumed,
                                        "final_focus": final})

    def _settle_on_home(self, driver):
        """BACK up to MAX_BACK_ATTEMPTS times, reading focus() after each, stopping the instant
        WhatsApp's own HomeActivity is reached OR a Conversation is surfaced (handed off, never
        touched further -- same reasoning as _check_whatsapp_task's own docstring). Force-stops and
        relaunches once clean if BACK alone did not get there -- the SmsDefaultAppWarning-adjacent
        fallback this incident's own postmortem names as the proven fix. -> the final focus."""
        focus = driver.focus()
        for _ in range(MAX_BACK_ATTEMPTS):
            if _is_whatsapp_home(focus) or _is_whatsapp_conversation(focus):
                return focus
            driver.back()
            focus = driver.focus()
        if _is_whatsapp_home(focus) or _is_whatsapp_conversation(focus):
            return focus
        driver.force_stop_whatsapp()
        return driver.resume_whatsapp()

    # --- check 4: leftover recordings (TASK-228's own orphan, reused here) -------------------------
    def _check_recordings(self, now):
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
        return {"alive": bool(self._thread and self._thread.is_alive()),
                "interval_sec": self.interval,
                "started_at": self.started_at,
                "last_run_at": self.last_run_at,
                "last_ok_at": self.last_ok_at,
                "mem_available_mb": self.mem_available_mb,
                "skipped_busy": self.skipped_busy,
                "actions": dict(self.actions),
                "last_action": self.last_action,
                "errors": self.errors}
