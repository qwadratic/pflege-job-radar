"""The phone-op FIFO queue's single dispatcher thread (TASK-227).

WHY THIS EXISTS. ``bridge/server.py`` is a ``ThreadingHTTPServer``: two concurrent HTTP requests
that both touch the phone used to race a bare ``fcntl.flock`` (``bridge/adb_driver.py::PhoneLock``)
with no FIFO guarantee and a 0.5 s poll -- either could win regardless of arrival order, and a
loser past 30 s got a spurious 503 even though the lock was free between polls. Every phone-touching
HTTP route now enqueues a row in ``bridge/ledger.py``'s ``phone_ops`` table instead of calling the
executor/operations method inline, and this is the one thread that ever drains that table: claim
the oldest ``queued`` row, run it to completion, write the outcome, repeat. Ordering and mutual
exclusion among HTTP-originated phone touches fall out for free from that.

NOT THE ONLY CALLER OF THE PHONE (TASK-268, correcting this docstring's own earlier claim of
"exactly one caller left"). ``BroadcastRunner`` (``bridge/broadcast.py``) and ``IdentityWatcher``
(``bridge/watcher.py``) both take ``huawei01.lock`` directly, on their own threads, outside this
queue entirely -- routing them through here would force a 90-150 s bubble and a long identity
evidence read to interleave inside this one thread, which is the latency change TASK-131 round 6
gave ``IdentityWatcher`` its own schedule to avoid. Instead each yields: before its own long-patience
acquire, it checks ``ledger.phone_ops_queue_counts()`` and steps aside for a cycle when this queue is
non-empty, rather than racing a dispatched op for the bare flock with the identical
``LOCK_TIMEOUT_SEC``-vs-longer-patience asymmetry that made a dispatched send the losing side.

WHY A BARE 202 IS NOT A LIE HERE, though ``bridge/server.py``'s own module docstring is explicit
that this rail never answers a bare "202 queued" (that policy is about the PACING/governor problem
-- accepting work this executor cannot promise to honour -- not about ordering work that will
definitely run). ``app/wa/bridge.py::Client._request`` polls ``GET /v1/ops/<id>`` internally until
the op is terminal and only then returns to its caller, so nothing above that layer -- not
``luna_brain``, not ``campaign.py``, not ``tools/wa_bridge.py`` -- ever sees "queued" and mistakes
it for "sent". The wire says queued; every existing caller still only ever hears a confirmed result.

GENERIC DISPATCH, ON PURPOSE. ``kind`` is a bare method name, resolved first against
``operations`` (the read/list/clear/delete verbs -- ``bridge/operations.py``) and then
``executor`` (the send verbs). No per-kind branch lives here: wiring a new phone-touching route
into the queue means naming its already-existing method in a server.py route handler, not teaching
this file a new case.

PRE-FLIGHT RECOVERY RIDES ALONG FOR FREE. ``take_phone`` (``bridge/executor.py``, TASK-226) already
recovers a phone left dirty before handing the lock to its caller, and every dispatched method
still calls ``take_phone`` itself exactly as it always did -- this file changes nothing about how a
job runs, only who is allowed to start one and in what order.
"""
from __future__ import annotations

import threading
import uuid

from . import errors as E
from . import ledger as L

#: Seconds between polls of the phone_ops table while it is empty. Not a cap on anything -- how
#: promptly a freshly queued op gets picked up when the dispatcher was idle.
DEFAULT_POLL_SEC = 0.3


def mint_op_id():
    """-> a fresh op id. Not an idempotency key (bridge/bridge_ids.py mints those) -- this is a
    queue ticket, minted once per enqueue, with no requirement that the same call ever produce the
    same one twice."""
    return "op." + uuid.uuid4().hex[:24]


class OpsDispatcher:
    """Drains ``bridge/ledger.py``'s ``phone_ops`` table strictly in ``position`` order, one row
    at a time, on its own thread. One per executor process, same as ``bridge/watcher.py``'s
    watchers."""

    def __init__(self, ledger, executor, operations, *, poll_interval=DEFAULT_POLL_SEC,
                 debug_capture=False, log=None):
        self.ledger = ledger
        self.executor = executor
        self.operations = operations
        self.poll_interval = float(poll_interval)
        # TASK-228, Ivan 2026-09-23: "первое время поставим флаг «дебаг»" -- off by default, one
        # flag (WA_BRIDGE_DEBUG_CAPTURE in bridge/server.py::main) so the postmortem instrumentation
        # below is something a night without an incident never pays for.
        self.debug_capture = bool(debug_capture)
        self._log = log or (lambda msg: None)
        # TASK-242: counted the way bridge/watcher.py's watchers count theirs, so a run of ledger
        # write failures (a full disk) shows up in heartbeat() instead of only in the thread's own
        # traceback on stderr.
        self.errors = 0
        self.last_error = None
        self.last_error_at = None
        # TASK-260: heartbeat() had no progress evidence at all -- a thread that is `alive` but
        # stuck (errors climbing, or simply falling behind) looked identical to a quiet evening.
        # Counted the way bridge/watcher.py's watchers already count theirs.
        self.cycles = 0
        self.last_ok_at = None
        self._thread = None
        self._stop = threading.Event()

    def enqueue(self, kind, args, budget_sec=None):
        """-> a fresh, already-durably-queued op_id (TASK-227). The HTTP route that calls this
        answers 200 {"op_id", "state": "queued"} immediately -- see this module's own docstring
        for why that is not the bare-202 lie ``bridge/server.py``'s module docstring warns
        against: the client polls this to a terminal state before its own caller ever sees it.

        ``budget_sec`` (TASK-243) passes straight through to ``ledger.enqueue_op`` -- see that
        method's own docstring. This file invents no value for it either."""
        op_id = mint_op_id()
        self.ledger.enqueue_op(op_id, kind, args, self.executor.clock(), budget_sec=budget_sec)
        return op_id

    def _resolve(self, kind):
        """-> the bound method ``kind`` names, on ``operations`` first and ``executor`` second
        (bridge/operations.py's six verbs and the executor's four send verbs never share a name).
        Raises AttributeError, deliberately uncaught -- an unknown ``kind`` reaching here is our
        own bug (the route handler that enqueued it named something that does not exist), not a
        caller mistake to answer politely."""
        target = getattr(self.operations, kind, None)
        return target if target is not None else getattr(self.executor, kind)

    def _capture(self, op_id, fn):
        """Run one debug-capture screenshot and never let it become the operation's own failure
        (TASK-228) -- same "loud but non-blocking" shape as ``bridge/executor.py``'s
        ``park_failed`` note. Recording start/stop (TASK-276) go through ``take_phone``'s
        ``on_phone_acquired`` hook and ``run_one``'s own ``finally`` instead: start has to fire at
        lock-acquisition time, not claim time, and stop's return value has to reach its caller,
        which this wrapper discards on purpose for every other capture step."""
        try:
            fn()
        except Exception as exc:
            self.ledger.note(self.executor.clock(), "debug_capture_failed", op_id,
                             error=f"{type(exc).__name__}: {exc}")
            self._log(f"op {op_id} debug capture failed: {type(exc).__name__}: {exc}")

    def run_one(self, row):
        """Run one claimed (``running``) row to completion and write its outcome. Never raises,
        including the outcome write itself (TASK-242): mark_op_done/mark_op_failed are sqlite
        writes and can fail (a full disk, a disk I/O error), and an exception raised inside an
        except/else clause is never caught by that same try's own except -- so the outer try below
        exists specifically to catch what the inner one structurally cannot."""
        op_id = row["op_id"]
        driver = self.executor.driver
        now = self.executor.clock
        # TASK-276: recording used to start here, at claim time -- before the dispatched method
        # ever reaches take_phone. The 180s screenrecord ceiling then burned on however long the
        # lock took (another lane's send, or IdentityWatcher's own send-sized patience) before this
        # op's own work ever began, truncating exactly the tail (tick wait, escalation, park) the
        # capture exists to prove. Hooking take_phone's on_phone_acquired instead means the budget
        # starts once the lock is genuinely this op's -- and recording_started stays False (so the
        # `finally` below skips stop_recording entirely) for an op that never touches the phone at
        # all, e.g. a refusal validated before take_phone is ever called.
        recording_started = False
        if self.debug_capture:
            self._capture(op_id, lambda: driver.debug_shot(op_id, "00_pre"))

            def _start_recording():
                nonlocal recording_started
                driver.start_recording(op_id)
                recording_started = True

            self.executor.on_phone_acquired = _start_recording
        try:
            try:
                method = self._resolve(row["kind"])
                result = method(**row["args"])
                # Two calling conventions coexist upstream of here (bridge/server.py's own comment
                # on /v1/messages vs /v1/photos explains why): Executor.send() returns
                # (200, payload) directly, everything else (send_photos/gallery/document, every
                # Operations verb) returns a bare payload dict that the OLD route handlers wrapped
                # in (200, ...) themselves. Both collapse to "the payload" here, once, so
                # op_status's ``result`` is the same shape either way.
                if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], int):
                    _status, result = result
            except E.BridgeRefusal as refusal:
                self.ledger.mark_op_failed(op_id, refusal.envelope(), now())
                self._log(f"op {op_id} ({row['kind']}) refused: {refusal.code}")
                if self.debug_capture:
                    self._capture(op_id, lambda: driver.debug_shot(op_id, "02_error"))
            except Exception as exc:  # a bug in our own code, named as one and never silently swallowed
                envelope = {"ok": False, "error": {"code": "executor_error",
                                                   "message": f"{type(exc).__name__}: {exc}",
                                                   "http_status": 500, "retryable": False}}
                self.ledger.mark_op_failed(op_id, envelope, now())
                self._log(f"op {op_id} ({row['kind']}): {type(exc).__name__}: {exc}")
                if self.debug_capture:
                    self._capture(op_id, lambda: driver.debug_shot(op_id, "02_error"))
            else:
                self.ledger.mark_op_done(op_id, result, now())
                if self.debug_capture:
                    self._capture(op_id, lambda: driver.debug_shot(op_id, "01_post"))
        except Exception as exc:
            # A dead dispatcher thread is a silent, total outage of outbound sends (TASK-242): the
            # HTTP surface and every watcher stay up, so nothing else ever notices. Counted and
            # logged instead, the same "loud but non-blocking" shape bridge/watcher.py's cycle()s
            # already use -- the row stays whatever state the failed write left it in and the next
            # poll moves on to the row behind it.
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_error_at = L.utc(now())
            self._log(f"op {op_id} ({row['kind']}): outcome write failed: {type(exc).__name__}: {exc}")
        finally:
            if self.debug_capture:
                # A hook that never fired (the op never reached/held the lock) must not outlive
                # this row and fire for someone else's acquisition later.
                self.executor.on_phone_acquired = None
                if recording_started:
                    try:
                        recording_path = driver.stop_recording(op_id)
                    except Exception as exc:
                        self.ledger.note(now(), "debug_capture_failed", op_id,
                                         error=f"{type(exc).__name__}: {exc}")
                        self._log(f"op {op_id} debug capture failed: {type(exc).__name__}: {exc}")
                    else:
                        if recording_path is None:
                            # TASK-228's own notes say a capture failure "is logged... never fails
                            # the underlying op" -- true for op state, false for the journal until
                            # now: AdbDriver.stop_recording returns None on a failed adb pull with
                            # nothing else ever noticing (retention.py only ever sees files that
                            # exist; op_status carries no recording-path field at all).
                            self.ledger.note(now(), "debug_recording_missing", op_id)
                            self._log(f"op {op_id} debug recording missing: stop_recording "
                                     f"returned no path")

    def cycle(self):
        """-> True if a job ran, False if the queue was empty. So a test can drive this without a
        thread, the same shape as ``bridge/watcher.py``'s ``cycle()`` methods."""
        self.cycles += 1
        try:
            row = self.ledger.claim_next_op()
        except Exception as exc:  # same reasoning as run_one's own outer except (TASK-242)
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_error_at = L.utc(self.executor.clock())
            self._log(f"ops dispatcher: claim failed: {type(exc).__name__}: {exc}")
            return False
        self.last_ok_at = L.utc(self.executor.clock())
        if row is None:
            return False
        self.run_one(row)
        return True

    def run(self):
        while not self._stop.is_set():
            if not self.cycle():
                self._stop.wait(self.poll_interval)

    def start(self):
        self._thread = threading.Thread(target=self.run, name="wa-bridge-ops-dispatcher",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.poll_interval + 5)

    def heartbeat(self):
        return {"poll_interval_sec": self.poll_interval,
                "alive": bool(self._thread and self._thread.is_alive()),
                "debug_capture": self.debug_capture,
                "cycles": self.cycles, "last_ok_at": self.last_ok_at,
                "errors": self.errors, "last_error": self.last_error,
                "last_error_at": self.last_error_at}
