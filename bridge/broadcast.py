"""The broadcast primitive: many recipients, one run, resumable (TASK-147).

WHY IT IS NOT A LOOP IN A SCRIPT. A loop over recipients in somebody's terminal has three defects
and all three cost real messages: it dies with the terminal, it holds its progress in a local
variable, and one refusal in the middle ends the run. Restarting it then re-sends whoever was
already messaged, because the only record of "already done" died with the process. So the run lives
in the ledger: the items are written before the first one is attempted, each item's status is
updated as it resolves, and the runner is a thread that reads the ledger rather than a list.

WHAT MAKES A RESUME SAFE, and it is two independent things, not one:
  1. an item that resolved is not queued any more, so the runner does not pick it up again;
  2. and if the process died between the send and the status update -- the one window where (1) is
     not enough -- the item is still queued, and the re-attempt goes through ``executor.send`` with
     the SAME deterministic client_msg_id. First-body-wins answers ``replayed`` and types nothing.
     The duplicate is refused one layer below this one, by the ledger rule that already existed.

PACING IS THE GOVERNOR'S AND NOTHING HERE ADDS TO IT. A run carries the caller's ``pacing`` as the
executor's ``constraints``, which the governor reconciles slower-only -- it can ask for a bigger gap
or a smaller cap, never the reverse. When the governor refuses with ``rail_parked`` it hands back a
``next_slot_at``; the item stays ``queued`` with that moment on it and the runner picks it up when
it arrives. That is the whole scheduler. There is no second pacing authority here, no retry ceiling,
and no maximum run size: the fuse is where it always was.

ONE RECIPIENT'S FAILURE IS ONE ITEM'S FAILURE. Every terminal outcome is recorded against its item
and the run continues. The statuses a caller reads afterwards:

    sent      a verified delivery tick, or a replay of one
    queued    not attempted yet, or deferred until next_attempt_at
    refused   refused before a key was pressed, for a reason pacing cannot fix
    failed    the handset was touched and the outcome is not a tick, or a bug in this package
              raised while the item was being attempted (code ``executor_error``, and whether the
              handset was touched is unknown). NEVER retried automatically, for the same reason a
              504 send is never auto-retried: it may have gone out.

THE HARD STOP is a flag in the ledger, not a variable in the runner, so it survives the restart a
caller in a hurry will try next. It is read before each item and it stops between items: a stop can
never interrupt a bubble mid-type, because an interrupted send is precisely the unconfirmable state
this rail refuses to create.
"""
from __future__ import annotations

import json
import re
import threading

from . import driver as D
from . import errors as E
from . import executor as X
from . import governor as G
from . import ledger as L

#: A run id is a name a human types and a route puts in a path. Not a cap on anything: it is the
#: shape of an identifier, checked at the boundary and rejected loudly.
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")

#: A refusal the governor can fix by waiting, and one the handset can: the item stays queued.
#: rail_parked carries a next_slot_at; a busy flock does not, and is simply due again next cycle.
DEFERRING = frozenset({"rail_parked", "device_unavailable"})
#: Refused before a key was pressed, and waiting will not help. Terminal, and the recipient is
#: untouched -- which is what lets a caller fix the input and make a new run.
REFUSING = frozenset({"invalid_request", "unauthorized", "not_on_whatsapp",
                      "idempotency_conflict", "chat_not_found", "chat_identity_mismatch"})

#: How often the runner asks the ledger whether anything is due. Not a rate limit: the governor
#: decides when a message may go, and a due item never waits longer than this to be noticed.
DEFAULT_POLL_SEC = 5.0


class Broadcast:
    """The run store and the single-item step. The loop is BroadcastRunner's."""

    def __init__(self, executor):
        self.executor = executor
        self.ledger = executor.ledger
        self.clock = executor.clock

    # --- the caller's surface -----------------------------------------------------------------
    def create(self, req):
        """Write a run. -> the run view. Nothing is sent here: the runner picks it up.

        The whole request is validated before any of it is stored. A partially accepted broadcast
        would be the worst of both worlds -- some recipients queued, some silently dropped, and no
        single answer to "what did I just start".
        """
        now = self.clock()
        run_id = req.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
            raise E.invalid_request("run_id must be 1-64 chars of [A-Za-z0-9._-]")
        raw = req.get("items")
        if not isinstance(raw, list) or not raw:
            raise E.invalid_request("items must be a non-empty list")
        # Validated here, where run_id and every item are validated, and not left to the governor:
        # it reads these values with int()/float() one item at a time, so a null or a typo'd key
        # would be accepted at 200 and only surface as a wedged run hours later.
        pacing = G.validate_constraints(req.get("pacing") or {}, what="pacing")
        items, keys = [], set()
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise E.invalid_request(f"items[{index}] must be an object")
            # The same validator the single-send route uses. One definition of a valid message.
            key, phone, body, action = X.validate_send({
                "client_msg_id": item.get("client_msg_id"), "to": item.get("to"),
                "kind": item.get("kind", "text"), "body": item.get("body"),
                "trace": {"action": item.get("action")}})
            if action not in G.KINDS:
                raise E.invalid_request(
                    f"items[{index}].action must be one of {G.KINDS}: an unclassified send cannot "
                    "be paced", position=index)
            if key in keys:
                raise E.invalid_request(
                    f"items[{index}] repeats a client_msg_id already in this run: the key is the "
                    "identity of one message and two of them are not one message", position=index)
            keys.add(key)
            items.append({"client_msg_id": key, "to_phone": phone, "action": action, "body": body,
                          "body_sha256": D.body_sha256(body)})
        try:
            self.ledger.create_run(run_id, note=req.get("note"), pacing=pacing, items=items,
                                   now=now)
        except ValueError as exc:
            raise E.invalid_request(str(exc), run_id=run_id) from exc
        return self.view(run_id)

    def view(self, run_id):
        """-> the run and every item's status. Bodies stay on the handset machine: a caller gets
        the sha256 it already knows, not the text back."""
        run = self.ledger.get_run(run_id)
        if run is None:
            raise E.invalid_request(f"no broadcast run {run_id!r} on this executor", run_id=run_id)
        items = [{"client_msg_id": i["client_msg_id"], "position": i["position"],
                  "thread": i["thread_tag"], "action": i["action"],
                  "body_sha256": i["body_sha256"], "status": i["status"],
                  "attempts": i["attempts"], "code": i["code"], "detail": i["detail"],
                  "next_attempt_at": i["next_attempt_at"], "updated_at": i["updated_at"]}
                 for i in self.ledger.run_items(run_id)]
        return {"ok": True, "run": _run_view(run), "counts": self.ledger.run_counts(run_id),
                "items": items}

    def list_runs(self):
        return {"ok": True, "runs": [{**_run_view(r), "counts": self.ledger.run_counts(r["run_id"])}
                                     for r in self.ledger.list_runs()]}

    def stop(self, run_id):
        """The hard stop. -> the run view. Takes effect before the next item, never mid-bubble.

        A run that is already finished with is LEFT ALONE and its real state is returned. Stamping
        ``stopped`` on a completed campaign would rewrite the record of a run whose every message
        went out -- and a stop arriving after the last item resolved is ordinary, not exotic: the
        operator who did not see the completion, or a retried request.
        """
        now = self.clock()
        run = self.ledger.get_run(run_id)
        if run is None:
            raise E.invalid_request(f"no broadcast run {run_id!r} on this executor", run_id=run_id)
        if run["state"] != L.RUN_OPEN:
            self.ledger.note(now, "broadcast_stop_noop", None, run_id=run_id, state=run["state"])
            return self.view(run_id)
        self.ledger.request_stop(run_id, now)
        self.ledger.set_run_state(run_id, L.RUN_STOPPED, now)
        return self.view(run_id)

    # --- one item, which is all the runner ever does at a time --------------------------------
    def step(self):
        """Attempt the next due item. -> a result dict, or None when nothing is due.

        Every outcome lands in the ledger before this returns, including the ones that raised.
        """
        now = self.clock()
        self._settle(now)
        run, item = self.ledger.next_due_item(now)
        if run is None:
            return None
        run_id, key = run["run_id"], item["client_msg_id"]
        request = {"client_msg_id": key, "to": item["to_phone"], "kind": "text",
                   "body": item["body"], "trace": {"action": item["action"]},
                   "constraints": json.loads(run["pacing"])}
        try:
            _status, payload = self.executor.send(request)
        except E.BridgeRefusal as refusal:
            return self._refusal(run_id, key, refusal, self.clock())
        except D.DriverError as exc:
            # A driver error the executor did not classify. The handset was touched; terminal.
            return self._resolve(run_id, key, L.ITEM_FAILED, self.clock(),
                                 code="driver_error", detail=str(exc))
        except Exception as exc:
            # A bug in our own code. Whether the handset was touched is precisely what an
            # unclassified exception does not say, so the item is terminal for the same reason
            # send_unconfirmed is -- and it is RESOLVED rather than left queued, because
            # next_due_item orders by (run, position) and hands the same item back every poll:
            # one bug would otherwise spin forever on it and no other run would ever start.
            # The exception still leaves this method: the runner counts it, journals it and puts
            # it in /v1/health, now with the run and the item that were being attempted.
            exc.wa_broadcast = {"run_id": run_id, "client_msg_id": key}
            self.ledger.mark_item(run_id, key, L.ITEM_FAILED, self.clock(), code="executor_error",
                                  detail=f"{type(exc).__name__}: {exc}", attempted=True)
            raise
        detail = "replayed" if payload.get("replayed") else (payload.get("verified") or {}).get("tick")
        return self._resolve(run_id, key, L.ITEM_SENT, self.clock(), code=None, detail=detail)

    def _settle(self, now):
        """Close the runs that are finished with, and honour a stop before anything is picked up."""
        for run in self.ledger.open_runs():
            if run["stop_requested"]:
                self.ledger.set_run_state(run["run_id"], L.RUN_STOPPED, now)
            elif self.ledger.queued_count(run["run_id"]) == 0:
                self.ledger.set_run_state(run["run_id"], L.RUN_DONE, now)

    def _refusal(self, run_id, key, refusal, now):
        if refusal.code in DEFERRING:
            # Nothing was typed and the reason is a clock. next_slot_at is the governor's own
            # advice; a busy handset has no such moment and is simply due again next cycle.
            when = (refusal.detail or {}).get("next_slot_at")
            self.ledger.mark_item(run_id, key, L.ITEM_QUEUED, now, code=refusal.code,
                                  detail=refusal.message, next_attempt_at=when)
            return {"run_id": run_id, "client_msg_id": key, "status": L.ITEM_QUEUED,
                    "code": refusal.code, "next_attempt_at": when}
        status = L.ITEM_REFUSED if refusal.code in REFUSING else L.ITEM_FAILED
        return self._resolve(run_id, key, status, now, code=refusal.code, detail=refusal.message)

    def _resolve(self, run_id, key, status, now, *, code, detail):
        self.ledger.mark_item(run_id, key, status, now, code=code, detail=detail, attempted=True)
        self._settle(now)
        return {"run_id": run_id, "client_msg_id": key, "status": status, "code": code}


def _run_view(run):
    return {"run_id": run["run_id"], "created_at": run["created_at"], "note": run["note"],
            "state": run["state"], "stop_requested": bool(run["stop_requested"]),
            "pacing": json.loads(run["pacing"]), "finished_at": run["finished_at"]}


class BroadcastRunner:
    """The thread that turns queued items into bubbles. One per executor process.

    Modelled on InboundWatcher, including the part that matters: it never raises out of a cycle,
    it counts what it did, and its heartbeat is in /v1/health -- because an idle runner and a dead
    runner produce exactly the same empty queue.

    ONE ITEM PER CYCLE, on purpose. Draining until nothing is due would spin against a handset
    whose flock is held by the other lane (that refusal has no next_slot_at to wait for), and the
    governor's own floors are minutes long, so a five-second poll is never what paces a campaign.
    """

    def __init__(self, broadcast, *, interval=DEFAULT_POLL_SEC, log=None):
        self.broadcast = broadcast
        self.interval = float(interval)
        self._log = log or (lambda msg: None)
        self.started_at = None
        self.cycles = 0
        self.attempted = 0
        self.errors = 0
        self.last_item_at = None
        self.last_error = None
        self.last_error_at = None
        self._thread = None
        self._stop = threading.Event()

    def cycle(self):
        """-> the step result, or None. Never raises: a bug here must not stop the runner."""
        now = self.broadcast.clock()
        self.cycles += 1
        try:
            result = self.broadcast.step()
        except Exception as exc:  # a bug in our own code, named as one and recorded
            # ``wa_broadcast`` is step()'s note of which item it was attempting. Without it a
            # health reader sees the exception and not the run, and finding the poisoned run means
            # guessing which one to stop.
            where = getattr(exc, "wa_broadcast", None) or {}
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}" + (
                f" (run {where['run_id']}, item {where['client_msg_id']})" if where else "")
            self.last_error_at = L.utc(now)
            self.broadcast.ledger.note(now, "broadcast_runner_error", where.get("client_msg_id"),
                                       error=self.last_error, run_id=where.get("run_id"))
            self._log(f"broadcast: {self.last_error}")
            return None
        if result is not None:
            self.attempted += 1
            self.last_item_at = L.utc(now)
            self._log(f"broadcast {result['run_id']}: {result['status']}"
                      + (f" ({result['code']})" if result.get("code") else ""))
        return result

    def run(self):
        self.started_at = L.utc(self.broadcast.clock())
        while not self._stop.is_set():
            self.cycle()
            self._stop.wait(self.interval)

    def start(self):
        self._thread = threading.Thread(target=self.run, name="wa-bridge-broadcast", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)

    def heartbeat(self):
        return {"interval_sec": self.interval, "started_at": self.started_at,
                "cycles": self.cycles, "attempted": self.attempted, "errors": self.errors,
                "last_item_at": self.last_item_at, "last_error": self.last_error,
                "last_error_at": self.last_error_at,
                "alive": bool(self._thread and self._thread.is_alive())}
