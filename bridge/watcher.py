"""The inbound watcher: the phone's side of a pull-only rail (TASK-143).

An ingress tunnel into our VPS is not available, so nothing on this machine ever connects to our
server. The shape that falls out of that is the only honest one: the watcher writes what the phone
saw into a durable local outbox with a monotonic cursor, and our VPS drains it over a connection it
opens itself (bridge/relay_pull.py). A network outage therefore delays a candidate's reply; it does
not lose it.

WHY A THREAD AND NOT A TIMER. A candidate's message must not wait minutes, and a systemd timer's
floor is a minute. The loop lives inside the executor process, takes the same flock as a send, and
holds it for one dumpsys -- a second or two. A cycle it cannot get the lock for is skipped and
noted, not retried in place: the shade still holds the message, so the next cycle sees it.

WHY THE COUNTERS ARE IN /v1/health. An empty outbox is what a working quiet rail looks like AND what
a dead watcher looks like. ``last_ok_at`` is the difference, and it is what the health alarm keys on.
"""
from __future__ import annotations

import threading
import time

from . import driver as D
from . import ledger as L

#: Seconds between polls of the notification shade. Not a cap on anything -- it is how often we ask.
#: Five seconds is under the pace a human answers at and well above the cost of one dumpsys.
DEFAULT_INTERVAL_SEC = 5.0
#: A watcher cycle waits only briefly for the flock: a send in flight owns the phone for up to a
#: minute, and queueing behind it would just make the watcher's own interval meaningless.
DEFAULT_LOCK_TIMEOUT_SEC = 5.0


class InboundWatcher:
    """Polls the handset and appends to the ledger outbox. One per executor process."""

    def __init__(self, executor, *, interval=DEFAULT_INTERVAL_SEC,
                 lock_timeout=DEFAULT_LOCK_TIMEOUT_SEC, log=None, sleep=time.sleep):
        self.executor = executor
        self.interval = float(interval)
        self.lock_timeout = float(lock_timeout)
        self.sleep = sleep
        self._log = log or (lambda msg: None)
        self.started_at = None
        self.cycles = 0
        self.busy_cycles = 0
        self.errors = 0
        self.last_ok_at = None
        self.last_error = None
        self.last_error_at = None
        self._thread = None
        self._stop = threading.Event()

    # --- one cycle, so a test can run it without a clock ------------------------------------------
    def cycle(self):
        """-> the drain result, or None when the phone was busy. Never raises."""
        now = self.executor.clock()
        self.cycles += 1
        try:
            result = self.executor.drain_inbound(lock_timeout=self.lock_timeout)
        except D.PhoneBusy:
            # The flock being busy is the expected case, not an incident: a send owns the phone for
            # 90-150 s (executor.py's flock note) and so does the other lane's daemon.
            self.busy_cycles += 1
            return None
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
                "cycles": self.cycles, "busy_cycles": self.busy_cycles, "errors": self.errors,
                "last_ok_at": self.last_ok_at, "last_error": self.last_error,
                "last_error_at": self.last_error_at,
                "alive": bool(self._thread and self._thread.is_alive())}
