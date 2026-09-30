"""The inbound watcher: the phone's side of a pull-only rail (TASK-372).

An ingress tunnel into our VPS is not available, so nothing on this machine ever connects to our
server. The shape that falls out of that is the only honest one: the watcher writes what the phone
saw into a durable local outbox with a monotonic cursor, and our VPS drains it over a connection it
opens itself (bridge/relay_pull.py). A network outage therefore delays a candidate's reply; it does
not lose it.

WHY A THREAD AND NOT A TIMER. A candidate's message must not wait minutes, and a systemd timer's
floor is a minute. The loop lives inside the executor process and reads the notification shade
(``dumpsys notification``) every cycle, a pure read that touches no UI.

NO LOCK, NOT EVEN A SHORT ONE (TASK-360 round 6). It used to take the same flock as a send, timed
out short so a long send would not stall a poll -- and a cycle that lost that race was simply
skipped: half the decoy attributions this round's own brief was written to fix traced back to
exactly that gap, a notification that arrived during a 90-150 s send and was gone by the next poll.
Reading the shade needs no UI and therefore no lock at all, so this loop can never be skipped by a
busy phone -- see ``Executor.drain_inbound``'s own docstring. The UI work this file used to justify
holding the lock for -- opening a chat to read a bubble's own attributes for identity matching --
is a different job now, done by ``IdentityWatcher`` below, on its own schedule, waiting for the lock
as long as it needs to. A busy phone delays that job; it can no longer make this one miss anything.

WHY THE COUNTERS ARE IN /v1/health. An empty outbox is what a working quiet rail looks like AND what
a dead watcher looks like. ``last_ok_at`` is the difference -- it is what a health alarm has to key
on. Until TASK-255, nothing outside a hand-run ``--probe`` ever read it: ``bridge/relay_pull.py``'s
``Relay.run()`` now asks on a slow cadence and logs loudly (``check_watcher_alarm``) when it goes
stale, but that is one log line on the VPS, not the reviewed conjunction alert with an alert channel
that TASK-361 (still To Do) is meant to be.
"""
from __future__ import annotations

import mimetypes
import re
import threading
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from . import driver as D
from . import ledger as L
from . import media as MD

#: Seconds between polls of the notification shade. Not a cap on anything -- it is how often we ask.
#: Five seconds is under the pace a human answers at and well above the cost of one dumpsys.
DEFAULT_INTERVAL_SEC = 5.0

#: Consecutive dirty cycles required before the idle self-check acts (TASK-226). One cycle is not
#: enough: a legitimate send/read still mid-flight can read as a Conversation for a moment too,
#: and its own ``finally: park()`` deserves the first chance. Two cycles is one full interval of
#: patience -- still fast next to TASK-225's incident, which sat dirty for 45+ minutes.
IDLE_DIRTY_CONFIRM_CYCLES = 2

#: Seconds between passes of the media tree (TASK-360). Its own cadence, not the notification
#: watcher's: a listing (``find``+``stat`` over the whole tree) and a pull (potentially megabytes
#: over USB tether) are both slower and more variable than one ``dumpsys``, and neither touches the
#: UI, so there is no reason to share a schedule -- let alone the flock -- with it.
DEFAULT_MEDIA_INTERVAL_SEC = 5.0


def operator_hold_until(path, now, ledger, *, event="idle_dirty_hold_unreadable"):
    """-> the moment an operator-set hold at `path` expires, or None when there is none, the file
    is missing, or its contents can't be parsed (TASK-266). Shared module-level function so any
    watcher can honour the same by-hand convention identically -- see
    ``InboundWatcher._operator_hold_until`` below (which now just delegates here) for the full
    rationale; TASK-315 review has PhoneDoctor reuse this exact function rather than growing its
    own second copy of the same file-reading logic.

    Read-only, no lock, no adb -- one local file, the human's own statement of how long they need
    the handset, written by hand before going hands-on. Its whole content is one RFC3339
    timestamp, ``ledger.utc``'s own spelling. A file that cannot be read or parsed is logged (via
    `ledger`, when one is given) and treated as no hold, never trusted silently -- a stuck or
    mistyped file must never block recovery forever, it must just fall back to behaving as if no
    hold were set.
    """
    if path is None:
        return None
    try:
        raw = path.read_text().strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        if ledger is not None:
            ledger.note(now, event, None, error=str(exc))
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        if ledger is not None:
            ledger.note(now, event, None, error=f"unparsable hold_until {raw!r}: {exc}")
        return None


class InboundWatcher:
    """Polls the handset's notification shade and appends to the ledger outbox. Never takes the
    phone lock (TASK-360 round 6) -- see this module's own docstring. One per executor process."""

    def __init__(self, executor, *, interval=DEFAULT_INTERVAL_SEC, log=None, sleep=time.sleep,
                 operator_hold_path=None):
        self.executor = executor
        self.interval = float(interval)
        self.sleep = sleep
        self._log = log or (lambda msg: None)
        self.started_at = None
        self.cycles = 0
        self.errors = 0
        self.last_ok_at = None
        self.last_error = None
        self.last_error_at = None
        self._thread = None
        self._stop = threading.Event()
        # TASK-226: the idle self-check. dirty_streak counts consecutive cycles that read a
        # Conversation open; recovered counts how many times that streak crossed the confirm
        # threshold and this watcher parked the phone itself, with nothing queued.
        self._dirty_streak = 0
        self.idle_dirty_recovered = 0
        # TASK-266: the operator's own escape hatch, read-only -- see _operator_hold_until.
        self._operator_hold_path = Path(operator_hold_path) if operator_hold_path else None
        self.idle_dirty_held = 0

    # --- one cycle, so a test can run it without a clock ------------------------------------------
    def cycle(self):
        """-> the drain result. Never raises, and -- unlike before round 6 -- never skipped by a
        busy phone: ``drain_inbound`` takes no lock any more, so the only way this returns None is a
        genuine driver failure (adb gone, the handset off USB)."""
        now = self.executor.clock()
        self.cycles += 1
        try:
            result = self.executor.drain_inbound()
        except D.DriverError as exc:
            self.errors += 1
            self.last_error, self.last_error_at = str(exc), L.utc(now)
            self.executor.ledger.note(now, "watcher_error", None, error=str(exc))
            self._log(f"watcher: {exc}")
            return None
        except Exception as exc:  # a bug in our own code, named as one and never silently swallowed
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_error_at = L.utc(now)
            self.executor.ledger.note(now, "watcher_error", None, error=self.last_error)
            self._log(f"watcher: {self.last_error}")
            return None
        self.last_ok_at = L.utc(now)
        if result["stored"]:
            self._log(f"watcher: {result['stored']} new inbound "
                      f"({result['seen']} seen, {result['unresolved']} unresolved)")
        try:
            self._check_idle_dirty(now)
        except Exception as exc:
            # This call sat OUTSIDE the try above, so cycle()'s own "never raises" promise in the
            # docstring was not true for the idle half: anything _check_idle_dirty touches --
            # focus(), park(), read_cold_thread(), or comparing a hand-written operator-hold
            # timestamp that parsed but carries no UTC offset (TypeError, not ValueError, so
            # _operator_hold_until does not catch it) -- propagated through run()'s bare loop and
            # KILLED THE WATCHER THREAD. That is TASK-225's original incident exactly: inbound
            # capture stops, nothing raises anywhere, and a quiet rail is what a working one looks
            # like too. Counted as an error so /v1/health stops reading green.
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_error_at = L.utc(now)
            self.executor.ledger.note(now, "idle_check_error", None, error=self.last_error)
            self._log(f"watcher idle check: {self.last_error}")
        return result

    def _check_idle_dirty(self, now):
        """A chat left open with nothing queued and nobody at the phone is exactly TASK-225's
        incident: nothing else notices it, because an empty outbox is what a working quiet rail
        looks like too. Read-only unless it decides to act. ``focus()`` takes no lock, same as the
        notification read above; the recovery itself, when it fires, briefly takes huawei01.lock
        -- and only after a NON-BLOCKING probe (``timeout=0``) proves nothing else holds it right
        now, so a legitimate in-flight send is never interrupted, only ever raced-and-skipped
        (safe: the send's own ``finally: park()`` still runs, and the next cycle tries again).

        BEFORE PARKING, READ (TASK-234): this used to take the lock, prove the phone is free, and
        then throw away the very thing that proof was about -- whatever is on the screen it just
        confirmed nobody else is touching. That screen clears its own notification the moment it
        was opened (``pull_inbound``'s own docstring), so parking without reading is this rail's
        own recovery mechanism discarding a message none of the other doors ever saw either.
        ``current_chat_phone`` has no just-opened bubble of its own to verify against (unlike
        ``open_chat``), so it reads the header off the screen instead; unresolvable (no header, or
        a shared display name) still parks, it just has nothing sound to key a read against.

        TASK-266: a Conversation held open by a human standing at the handset reads exactly like
        one abandoned by a bug -- no flock, ``focus()`` unchanged either way -- so this could not
        tell the two apart. ``_operator_hold_until`` is the human's own way of saying which one
        this is, checked here, before the probe above, so a held phone is never touched at all.
        """
        try:
            focus = self.executor.driver.focus()
        except D.DriverError:
            return  # the notification read above already logged this cycle's real failure
        if not focus.endswith("Conversation"):
            self._dirty_streak = 0
            return
        self._dirty_streak += 1
        if self._dirty_streak < IDLE_DIRTY_CONFIRM_CYCLES:
            return
        self._dirty_streak = 0
        hold_until = self._operator_hold_until(now)
        if hold_until is not None and now < hold_until:
            self.idle_dirty_held += 1
            self.executor.ledger.note(now, "idle_dirty_held", None, focus=focus,
                                      hold_until=L.utc(hold_until))
            return
        with ExitStack() as stack:
            try:
                stack.enter_context(self.executor.driver.lock(timeout=0))
            except D.DriverError:
                return  # something else holds the phone right now -- not ours to touch
            self.idle_dirty_recovered += 1
            self.executor.ledger.note(now, "idle_dirty_recovered", None, focus=focus)
            try:
                phone = self.executor.driver.current_chat_phone()
            except D.DriverError as exc:
                phone = None
                self.executor.ledger.note(now, "idle_dirty_identity_failed", None, error=str(exc))
            if phone is not None:
                try:
                    messages, unresolved = self.executor.driver.read_cold_thread(phone)
                    seen = self.executor.record_inbound(messages, unresolved, now)
                    self.executor.ledger.note(now, "idle_dirty_read", None, **seen)
                except D.DriverError as exc:
                    self.executor.ledger.note(now, "thread_read_failed", None, error=str(exc))
            try:
                self.executor.driver.park()
            except D.DriverError as exc:
                self.executor.ledger.note(now, "park_failed", None, error=str(exc))

    def _operator_hold_until(self, now):
        """-> the moment an operator-set hold expires, or None when there is none (TASK-266).

        Delegates to the shared module-level ``operator_hold_until`` (see its docstring for the
        full rationale on why this is read-only/no-lock and why an unreadable file means "no
        hold" rather than blocking recovery) -- kept as a thin method here only so existing call
        sites (``self._operator_hold_until(now)``) do not have to change.
        """
        return operator_hold_until(self._operator_hold_path, now, self.executor.ledger)

    def run(self):
        self.started_at = L.utc(self.executor.clock())
        while not self._stop.is_set():
            self.cycle()
            self._stop.wait(self.interval)

    def start(self):
        self.executor.watcher = self
        self._thread = threading.Thread(target=self.run, name="wa-bridge-watcher", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)

    def heartbeat(self):
        return {"interval_sec": self.interval, "started_at": self.started_at,
                "cycles": self.cycles, "errors": self.errors,
                "last_ok_at": self.last_ok_at, "last_error": self.last_error,
                "last_error_at": self.last_error_at,
                "idle_dirty_recovered": self.idle_dirty_recovered,
                "idle_dirty_held": self.idle_dirty_held,
                "alive": bool(self._thread and self._thread.is_alive())}


#: Safe on a local filesystem: WhatsApp keeps the sender's own filename for a document, which can
#: carry spaces, umlauts or anything else a candidate's phone allowed. The rel path (folder plus
#: name) is collapsed to one component per byte, not truncated -- a name colliding after collapse
#: is still a distinct dict key upstream (the handset's own rel path), so nothing here can merge
#: two different files under one local name silently.
_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


class MediaWatcher:
    """The handset's WhatsApp media folders -> pulled, content-addressed files -> the unresolved
    queue (TASK-360). This watcher only ever PULLS: it never opens a chat and never decides who a
    file belongs to. Deciding is a separate job, on a separate schedule and a separate lock
    discipline -- ``IdentityWatcher`` below (TASK-360 round 6) -- because a listing-and-pull pass
    touches no UI and must never be made to wait behind one that does.

    Runs on its own thread and its own interval. LISTING AND PULLING NEVER TAKE huawei01.lock.
    ``adb shell`` (a ``find``+``stat`` listing) and ``adb pull`` (a filesystem copy) are both
    filesystem-level: they touch no UI, so holding the same flock the send path and
    ``InboundWatcher`` fight over would only manufacture contention that reading the SD card was
    never going to cause. Folding this into ``InboundWatcher.cycle`` (documented there as "one
    dumpsys, a second or two") would instead let one large document turn a UI-touching cycle into a
    multi-second stall of a flock the send path also wants -- so this is a separate pass, not an
    extra step of that one.
    """

    def __init__(self, driver, ledger, media_dir, *, interval=DEFAULT_MEDIA_INTERVAL_SEC,
                 log=None, sleep=time.sleep, clock=None):
        self.driver = driver
        self.ledger = ledger
        self.media_dir = Path(media_dir)
        self.interval = float(interval)
        self.sleep = sleep
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._log = log or (lambda msg: None)
        self.cycles = 0
        self.errors = 0
        self.last_ok_at = None
        self.last_error = None
        self.last_error_at = None
        self.pulled_total = 0
        self._thread = None
        self._stop = threading.Event()
        # TASK-244: adb has no way to ask WhatsApp "is this file still being written" -- a listing
        # that has not moved since the previous cycle is the only signal available that a download
        # has stopped. Whether WhatsApp writes incoming media in place or downloads to a temp name
        # and renames atomically is unknown (unreachable read-only, off the live handset); this does
        # not assume either way, only that a still-growing file will show it across two 5s-apart
        # listings.
        self._last_listing = {}

    def cycle(self):
        """-> {"pulled"}, or None on a failure. Never raises: this is the thread's own loop, same
        contract as InboundWatcher.cycle."""
        now = self.clock()
        self.cycles += 1
        try:
            result = self._cycle_once(now)
        except Exception as exc:  # a bug in our own code, named as one and never silently swallowed
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_error_at = L.utc(now)
            self.ledger.note(now, "media_watcher_error", None, error=self.last_error)
            self._log(f"media watcher: {self.last_error}")
            return None
        self.last_ok_at = L.utc(now)
        if result["pulled"]:
            self._log(f"media watcher: pulled {result['pulled']} file(s), queued for a human "
                      f"to attach")
        return result

    def _cycle_once(self, now):
        listing = self.driver.list_media()
        known = self.ledger.media_known_paths()
        # size > 0 alone used to be "fresh" (TASK-244): a candidate's PDF or voice note pulled mid-
        # write left a truncated file permanently marked known, since record_media never re-admits a
        # source_rel. Requiring this listing to agree with the previous one closes the gap a single
        # snapshot cannot: a file still growing moves its size, its mtime, or both between two 5s-
        # apart polls.
        fresh = sorted((rel, size, mtime) for rel, (size, mtime) in listing.items()
                       if rel not in known and size > 0
                       and self._last_listing.get(rel) == (size, mtime))
        pulled = 0
        for rel, size, mtime in fresh:
            local_name = _UNSAFE_PATH_CHARS.sub("_", rel)
            dest = self.media_dir / "incoming" / local_name
            try:
                self.driver.pull_media(rel, dest)
            except D.DriverError as exc:
                # Not marked "known": nothing was written, so the next cycle tries this path again
                # rather than silently forgetting a file that failed to copy once.
                self.ledger.note(now, "media_pull_failed", None, error=str(exc))
                continue
            blob = dest.read_bytes()
            if len(blob) != size:
                # The stability check above still races a pull spanning the moment writing resumes,
                # or two listings that happen to agree mid-write (TASK-244) -- caught here instead
                # of recorded: not marked "known", so the next cycle retries it exactly like a
                # failed pull above.
                dest.unlink()
                self.ledger.note(now, "media_pull_failed", None,
                                 error=f"copied {len(blob)} bytes for {rel!r}, listed size was "
                                       f"{size}")
                continue
            sha = MD.sha256_bytes(blob)
            media_id = MD.content_media_id(sha)
            kind = MD.kind_for_path(rel)
            final = self.media_dir / "store" / media_id
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                dest.unlink()          # bytes we already hold under this content id (a resend, or
                                        # a second person's byte-identical file) -- the QUEUE still
                                        # gets its own row for this pull, see record_media below.
            else:
                dest.replace(final)
            self.ledger.record_media(source_rel=rel, mtime=mtime, media_id=media_id, sha256=sha,
                                     local_path=str(final), size=size, kind=kind,
                                     mime_type=mimetypes.guess_type(rel)[0],
                                     filename=Path(rel).name, now=now)
            pulled += 1

        self.pulled_total += pulled
        self._last_listing = listing
        return {"pulled": pulled}

    def run(self):
        while not self._stop.is_set():
            self.cycle()
            self._stop.wait(self.interval)

    def start(self):
        self._thread = threading.Thread(target=self.run, name="wa-bridge-media-watcher",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)

    def heartbeat(self):
        return {"interval_sec": self.interval, "cycles": self.cycles, "errors": self.errors,
                "last_ok_at": self.last_ok_at, "last_error": self.last_error,
                "last_error_at": self.last_error_at, "pulled_total": self.pulled_total,
                # The LIVE queue (how many, how old, what kind) -- surfaced to an operator by
                # tools/wa_bridge.py's own health command, which points at `media-list` for detail.
                "unresolved_backlog": self.ledger.media_backlog(self.clock()),
                "alive": bool(self._thread and self._thread.is_alive())}


#: Seconds between passes of the unresolved queue (TASK-360 round 6). Slower than the shade or the
#: media pass on purpose: this is the one loop that may open a chat, and a cycle that finds nothing
#: to do (the common case -- most files are the sole candidate of their kind and are decided the
#: moment MediaWatcher pulls them into a queue this loop sees on its next pass) costs nothing, but a
#: cycle that DOES open several chats for a genuinely ambiguous file should not come back around
#: before a human could plausibly have noticed anything.
DEFAULT_IDENTITY_INTERVAL_SEC = 15.0


class IdentityWatcher:
    """THE UI work bridge/watcher.py's own module docstring promises: consumes the unresolved
    queue ``MediaWatcher`` fills and decides what ``bridge/identity.py::decide`` can decide
    (``Executor.auto_match_media``), on its own thread and its own schedule (TASK-360 round 6,
    Ivan's ruling 2026-09-22).

    THIS is the one watcher that may take ``huawei01.lock`` -- and when it does, it waits for it
    (``Executor.IDENTITY_LOCK_TIMEOUT_SEC``, sized like a send's own patience, not the notification
    watcher's 5 s that no longer even takes the lock at all). A busy phone delays this loop; it can
    never make it skip a file, because the file stays in the queue (a sqlite row) until this loop
    positively decides it, strong or weak -- there is nothing here for a busy cycle to lose.
    """

    def __init__(self, executor, *, interval=DEFAULT_IDENTITY_INTERVAL_SEC, log=None,
                 sleep=time.sleep):
        self.executor = executor
        self.interval = float(interval)
        self.sleep = sleep
        self._log = log or (lambda msg: None)
        self.started_at = None
        self.cycles = 0
        self.errors = 0
        self.last_ok_at = None
        self.last_error = None
        self.last_error_at = None
        self.attached_total = 0
        self.weak_total = 0
        self._thread = None
        self._stop = threading.Event()

    def cycle(self):
        """-> the sweep's own {"attached", "weak"}, or None on a failure. Never raises: this is the
        thread's own loop, same contract as every other watcher here."""
        now = self.executor.clock()
        self.cycles += 1
        try:
            result = self.executor.auto_match_media()
        except Exception as exc:  # a bug in our own code, named as one and never silently swallowed
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_error_at = L.utc(now)
            self.executor.ledger.note(now, "identity_watcher_error", None, error=self.last_error)
            self._log(f"identity watcher: {self.last_error}")
            return None
        self.last_ok_at = L.utc(now)
        self.attached_total += result["attached"]
        self.weak_total += result["weak"]
        if result["attached"]:
            self._log(f"identity watcher: attached {result['attached']} file(s), "
                      f"{result['weak']} weak")
        return result

    def run(self):
        self.started_at = L.utc(self.executor.clock())
        while not self._stop.is_set():
            self.cycle()
            self._stop.wait(self.interval)

    def start(self):
        self.executor.identity_watcher = self
        self._thread = threading.Thread(target=self.run, name="wa-bridge-identity-watcher",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)

    def heartbeat(self):
        return {"interval_sec": self.interval, "started_at": self.started_at,
                "cycles": self.cycles, "errors": self.errors, "last_ok_at": self.last_ok_at,
                "last_error": self.last_error, "last_error_at": self.last_error_at,
                "attached_total": self.attached_total, "weak_total": self.weak_total,
                "alive": bool(self._thread and self._thread.is_alive())}


#: Seconds between reconciliation passes (TASK-234). Slower than every other watcher on purpose:
#: this is a full chat listing plus one open-read-park per unread row, real handset time on the
#: order of the send path's own 90-150 s per bubble, not one cheap dumpsys. A few minutes is the
#: pace named in the finding this exists to close, not a cap on anything -- how often we ask.
DEFAULT_RECONCILE_INTERVAL_SEC = 180.0


class ReconcileWatcher:
    """The third door (TASK-234): every other capture path -- the notification shade
    (``InboundWatcher`` above) and every piggyback read (``Operations.read_thread``,
    ``Executor.send``/``send_photos``/``send_gallery``/``_read_evidence_for``, TASK-231) -- only
    ever sees a message because something else already had its own reason to touch that chat. A
    chat nobody sends to, reads from or attaches media for gets neither, and if the shade also
    missed it (a full shade, revoked notification access, a reboot before a poll) it sits
    uncaptured until a human notices the unread badge on the handset itself.

    ``Operations.reconcile_unread`` does the actual work; this is only the schedule. THIS WATCHER
    TAKES ``huawei01.lock``, same as ``IdentityWatcher`` -- once for the listing and once per
    unread chat -- and, same reasoning, WAITS for it rather than skipping: a busy phone should
    delay a slow reconciliation pass, never make it drop a badge it already saw on the listing.
    """

    def __init__(self, operations, ledger, *, interval=DEFAULT_RECONCILE_INTERVAL_SEC, log=None,
                clock=None, sleep=time.sleep):
        self.operations = operations
        self.ledger = ledger
        self.interval = float(interval)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self._log = log or (lambda msg: None)
        self.started_at = None
        self.cycles = 0
        self.errors = 0
        self.last_ok_at = None
        self.last_error = None
        self.last_error_at = None
        self.reconciled_total = 0
        self._thread = None
        self._stop = threading.Event()

    def cycle(self):
        """-> ``Operations.reconcile_unread``'s own {"chats", "reconciled", "skipped"}, or None on
        a failure. Never raises, same contract as every other watcher cycle here."""
        now = self.clock()
        self.cycles += 1
        try:
            result = self.operations.reconcile_unread()
        except Exception as exc:  # a bug in our own code, named as one and never silently swallowed
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_error_at = L.utc(now)
            self.ledger.note(now, "reconcile_watcher_error", None, error=self.last_error)
            self._log(f"reconcile watcher: {self.last_error}")
            return None
        self.last_ok_at = L.utc(now)
        self.reconciled_total += result["reconciled"]
        if result["reconciled"] or result["skipped"]:
            self._log(f"reconcile watcher: read {result['reconciled']} unread chat(s), "
                      f"{result['skipped']} skipped (unresolved identity), of "
                      f"{result['chats']} listed")
        return result

    def run(self):
        self.started_at = L.utc(self.clock())
        while not self._stop.is_set():
            self.cycle()
            self._stop.wait(self.interval)

    def start(self):
        self._thread = threading.Thread(target=self.run, name="wa-bridge-reconcile-watcher",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)

    def heartbeat(self):
        return {"interval_sec": self.interval, "started_at": self.started_at,
                "cycles": self.cycles, "errors": self.errors, "last_ok_at": self.last_ok_at,
                "last_error": self.last_error, "last_error_at": self.last_error_at,
                "reconciled_total": self.reconciled_total,
                "alive": bool(self._thread and self._thread.is_alive())}


#: Seconds between passes of the ledger's own unresolved rows (TASK-235). Shorter than
#: pflege-wa-catchup.timer's 3-minute cadence, on purpose: that timer is the clock this exists to
#: race -- a row still ATTEMPTING/UNCONFIRMED when catch-up re-drives the turn is exactly the
#: wedge this closes, so the reconcile has to have a real chance to land a verdict first. Not a cap
#: on anything -- how often we ask.
DEFAULT_UNRESOLVED_SEND_INTERVAL_SEC = 60.0


class UnresolvedSendWatcher:
    """The other "nothing ever runs reconcile" (TASK-235). ``ReconcileWatcher`` above answers the
    third door for INBOUND (an unread chat notification nobody's send/read happened to surface).
    This answers it for OUTBOUND: ``bridge/executor.py::reconcile`` (POST /v1/reconcile) is the
    only thing that can move a send out of ``attempting``/``unconfirmed``, and before this class
    the only callers were a human running ``tools/wa_bridge.py reconcile`` and tests. A send that
    504s (no tick inside the wait) sits UNCONFIRMED forever: ``ledger.classify`` refuses to
    auto-resend it (Rule 4, ``bridge/ledger.py``'s own docstring), so ``pflege-wa-catchup.timer``
    re-drives the owed turn every 3 minutes, spends a full Luna call, and dies at the same
    ``send_unconfirmed`` -- burning the phone's entire hourly Luna budget on a reply that can never
    land, and starving a genuinely new message from the same candidate onto ``rate_limited``.

    ``ledger.unresolved()`` already returns exactly the rows a reconcile has to answer for, oldest
    first, and ``Executor.reconcile``'s three-valued verdict (confirmed_sent / confirmed_absent /
    indeterminate) is already safe to call on a row that is still mid-flight: it goes through
    ``ops_dispatcher`` (TASK-227/230) like every other phone-touching call, so it queues behind
    whatever is running and only scans once that op is done and the row may already be resolved.
    This class is only the schedule: cycle() asks the ledger the question and, if it is not empty,
    enqueues one ``reconcile`` op with every unresolved key -- the same op kind and the same
    dispatcher POST /v1/reconcile already uses (``bridge/server.py``), so nothing about how a
    reconcile runs changes, only who else calls it.
    """

    def __init__(self, ledger, ops_dispatcher, *, interval=DEFAULT_UNRESOLVED_SEND_INTERVAL_SEC,
                log=None, clock=None):
        self.ledger = ledger
        self.ops_dispatcher = ops_dispatcher
        self.interval = float(interval)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._log = log or (lambda msg: None)
        self.started_at = None
        self.cycles = 0
        self.errors = 0
        self.last_ok_at = None
        self.last_error = None
        self.last_error_at = None
        self.queued_total = 0
        self._thread = None
        self._stop = threading.Event()

    def cycle(self):
        """-> {"unresolved", "op_id"}, or None on a failure. Never raises, same contract as every
        other watcher cycle here: a bug in this thread must not stop the ops dispatcher it feeds."""
        now = self.clock()
        self.cycles += 1
        try:
            rows = self.ledger.unresolved()
            op_id = None
            if rows:
                # LOW (TASK-296-adjacent, 2026-09-24): background sync nobody is waiting on. This
                # is the exact op kind that starved real sends behind it for ~14 hours on two
                # forever-indeterminate keys on 2026-09-23/24 -- see ledger.unresolved()'s own
                # docstring for the other half of that fix (escalation stops the re-enqueue).
                op_id = self.ops_dispatcher.enqueue(
                    "reconcile", {"client_msg_ids": [row.client_msg_id for row in rows]},
                    priority=L.PRIORITY_LOW)
        except Exception as exc:  # a bug in our own code, named as one and never silently swallowed
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_error_at = L.utc(now)
            self.ledger.note(now, "unresolved_send_watcher_error", None, error=self.last_error)
            self._log(f"unresolved send watcher: {self.last_error}")
            return None
        self.last_ok_at = L.utc(now)
        if op_id is not None:
            self.queued_total += 1
            self._log(f"unresolved send watcher: queued reconcile op {op_id} for {len(rows)} "
                      f"row(s), oldest attempted_at {rows[0].attempted_at}")
        return {"unresolved": len(rows), "op_id": op_id}

    def run(self):
        self.started_at = L.utc(self.clock())
        while not self._stop.is_set():
            self.cycle()
            self._stop.wait(self.interval)

    def start(self):
        self._thread = threading.Thread(target=self.run, name="wa-bridge-unresolved-send-watcher",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)

    def heartbeat(self):
        return {"interval_sec": self.interval, "started_at": self.started_at,
                "cycles": self.cycles, "errors": self.errors, "last_ok_at": self.last_ok_at,
                "last_error": self.last_error, "last_error_at": self.last_error_at,
                "queued_total": self.queued_total,
                "alive": bool(self._thread and self._thread.is_alive())}
