"""The inbound watcher: the phone's side of a pull-only rail (TASK-143).

An ingress tunnel into our VPS is not available, so nothing on this machine ever connects to our
server. The shape that falls out of that is the only honest one: the watcher writes what the phone
saw into a durable local outbox with a monotonic cursor, and our VPS drains it over a connection it
opens itself (bridge/relay_pull.py). A network outage therefore delays a candidate's reply; it does
not lose it.

WHY A THREAD AND NOT A TIMER. A candidate's message must not wait minutes, and a systemd timer's
floor is a minute. The loop lives inside the executor process and reads the notification shade
(``dumpsys notification``) every cycle, a pure read that touches no UI.

NO LOCK, NOT EVEN A SHORT ONE (TASK-131 round 6). It used to take the same flock as a send, timed
out short so a long send would not stall a poll -- and a cycle that lost that race was simply
skipped: half the decoy attributions this round's own brief was written to fix traced back to
exactly that gap, a notification that arrived during a 90-150 s send and was gone by the next poll.
Reading the shade needs no UI and therefore no lock at all, so this loop can never be skipped by a
busy phone -- see ``Executor.drain_inbound``'s own docstring. The UI work this file used to justify
holding the lock for -- opening a chat to read a bubble's own attributes for identity matching --
is a different job now, done by ``IdentityWatcher`` below, on its own schedule, waiting for the lock
as long as it needs to. A busy phone delays that job; it can no longer make this one miss anything.

WHY THE COUNTERS ARE IN /v1/health. An empty outbox is what a working quiet rail looks like AND what
a dead watcher looks like. ``last_ok_at`` is the difference, and it is what the health alarm keys on.
"""
from __future__ import annotations

import mimetypes
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import driver as D
from . import ledger as L
from . import media as MD

#: Seconds between polls of the notification shade. Not a cap on anything -- it is how often we ask.
#: Five seconds is under the pace a human answers at and well above the cost of one dumpsys.
DEFAULT_INTERVAL_SEC = 5.0

#: Seconds between passes of the media tree (TASK-131). Its own cadence, not the notification
#: watcher's: a listing (``find``+``stat`` over the whole tree) and a pull (potentially megabytes
#: over USB tether) are both slower and more variable than one ``dumpsys``, and neither touches the
#: UI, so there is no reason to share a schedule -- let alone the flock -- with it.
DEFAULT_MEDIA_INTERVAL_SEC = 5.0


class InboundWatcher:
    """Polls the handset's notification shade and appends to the ledger outbox. Never takes the
    phone lock (TASK-131 round 6) -- see this module's own docstring. One per executor process."""

    def __init__(self, executor, *, interval=DEFAULT_INTERVAL_SEC, log=None, sleep=time.sleep):
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
        return result

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
                "alive": bool(self._thread and self._thread.is_alive())}


#: Safe on a local filesystem: WhatsApp keeps the sender's own filename for a document, which can
#: carry spaces, umlauts or anything else a candidate's phone allowed. The rel path (folder plus
#: name) is collapsed to one component per byte, not truncated -- a name colliding after collapse
#: is still a distinct dict key upstream (the handset's own rel path), so nothing here can merge
#: two different files under one local name silently.
_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


class MediaWatcher:
    """The handset's WhatsApp media folders -> pulled, content-addressed files -> the unresolved
    queue (TASK-131). This watcher only ever PULLS: it never opens a chat and never decides who a
    file belongs to. Deciding is a separate job, on a separate schedule and a separate lock
    discipline -- ``IdentityWatcher`` below (TASK-131 round 6) -- because a listing-and-pull pass
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
        fresh = sorted((rel, size, mtime) for rel, (size, mtime) in listing.items()
                       if rel not in known and size > 0)
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


#: Seconds between passes of the unresolved queue (TASK-131 round 6). Slower than the shade or the
#: media pass on purpose: this is the one loop that may open a chat, and a cycle that finds nothing
#: to do (the common case -- most files are the sole candidate of their kind and are decided the
#: moment MediaWatcher pulls them into a queue this loop sees on its next pass) costs nothing, but a
#: cycle that DOES open several chats for a genuinely ambiguous file should not come back around
#: before a human could plausibly have noticed anything.
DEFAULT_IDENTITY_INTERVAL_SEC = 15.0


class IdentityWatcher:
    """THE UI work bridge/watcher.py's own module docstring promises: consumes the unresolved
    queue ``MediaWatcher`` fills and decides what ``bridge/identity.py::decide`` can decide
    (``Executor.auto_match_media``), on its own thread and its own schedule (TASK-131 round 6,
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
