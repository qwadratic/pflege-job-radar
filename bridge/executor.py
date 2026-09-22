"""Where ledger, governor and driver are sequenced. The HTTP handlers hold no logic (TASK-130).

THE FLOCK RULE, written here because this is the only file that can break it:

    ONE BUBBLE PER ACQUISITION OF huawei01.lock, RELEASED BETWEEN BUBBLES, AND NEVER HELD ACROSS A
    REMOTE CALL.

    Reason, not taste: the colleague's daemon cycles every 15 s and takes the same flock with
    timeout=5, so anything we hold for longer than a few seconds shows up in his log as
    "device error: phone lock busy" (already observed once, 20260920.log:18). Luna's brain runs on
    our VPS and a turn can take 180 s; his own code holds the lock across the brain call
    (cli.py:246 inside :331) and that is one of the defects we are not inheriting. So: the brain
    call happens on our side, one bubble arrives per HTTP request, and the lock is taken inside
    ``send`` and released before the response is written. There is no code path in this package
    that acquires the lock twice, and FakeDriver raises on a re-entrant acquire so a test notices.

    WHAT ONE ACQUISITION ACTUALLY COSTS, measured rather than hoped for (TASK-146). "Nothing slow
    happens inside it" was wrong and is corrected here: a bubble owns the lock for **90-150 s**.
    open_chat waits up to 12 s for the header, PAUSE_AFTER_OPEN adds up to 4 s, typing runs at the
    handset's own 3.2-5.5 chars/s (a 219-character reply measured at a median 56 s, min 41, max 67
    over 20 seeds), _verify allows up to BUBBLE_APPEAR_SEC=30 s and wait_for_tick up to
    TICK_WAIT_SEC=30 s, then the thread read and park. Typing and the tick read stay in ONE
    acquisition on purpose: releasing between them would let the other lane move the screen between
    the tap and the read, and a tick read off a thread someone else scrolled is not evidence.
    The consequences are owned rather than hidden -- the other lane logs "phone lock busy" for the
    duration, and our own watcher (lock timeout 5 s) counts a ``busy_cycle`` and skips, which is
    safe because the notification shade still holds the message for the next cycle.

ORDER OF OPERATIONS IN ``send``, and every step of it is load-bearing:
  1. validate      -- an unclassifiable request never reaches the phone
  2. classify      -- replay and body-mismatch answer without touching the phone AND without
                      spending quota; a replay is not a send
  3. governor      -- the fuse, before the write-ahead write, so a refusal leaves no row to
                      reconcile and the deterministic key stays usable
  4. lock          -- BEFORE the write-ahead write (TASK-146). The other lane holds the same flock
                      across its own brain call, so losing the race is the documented normal case,
                      and it is a case where nothing was typed: it has to leave no row at all. A
                      row written first would sit ``attempting``, which is not RESENDABLE, and
                      every later attempt on that deterministic key would answer 504 for good.
  5. begin         -- the write-ahead write. After this, a crash is an ``attempting`` row.
  6. open/send/tick/park -- inside the same acquisition
  7. resolve       -- ``sent`` only with a tick in hand
"""
from __future__ import annotations

import time
from contextlib import ExitStack
from datetime import datetime, timezone

from . import driver as D
from . import errors as E
from . import ledger as L

VERSION = "0.1.0"

#: The contract's key prefix (plan section 4). The executor validates, it does not mint: the key is
#: derived on our VPS from phone|turn_key|action|bubble_index (TASK-114) and an executor that could
#: invent one would break the idempotency it exists to provide.
KEY_PREFIX = "wab.o."


def _utcnow():
    return datetime.now(timezone.utc)


class Executor:
    def __init__(self, *, ledger, governor, driver, rail_number=None, clock=_utcnow,
                 tick_wait_sec=D.TICK_WAIT_SEC, sleep=time.sleep, monotonic=time.monotonic):
        self.ledger = ledger
        self.governor = governor
        self.driver = driver
        # Injectable so a test can reach the "no tick was drawn" branch without waiting 30 s of
        # wall clock for a phone that does not exist.
        self.tick_wait_sec = tick_wait_sec
        self.sleep = sleep
        self.monotonic = monotonic
        # UNVERIFIED and blocking (TASK-136): the MSISDN of the WhatsApp account on
        # L2N4C19B14054874 is recorded nowhere on either machine. Health reports None rather than
        # the +49 number our own doc wrongly claims.
        self.rail_number = rail_number
        self.clock = clock
        # Watcher counters. They are in the health body because "the inbound path is alive" is not
        # provable from the outbox being empty -- an empty outbox is also what a dead watcher looks
        # like (TASK-143).
        self.inbound_seen = 0
        self.inbound_unresolved = 0
        self.inbound_last_at = None
        self.watcher = None
        # Set by server.main when the broadcast runner starts (TASK-147). Same reason as the
        # watcher's counters: a run that is not moving and a runner that is dead produce the same
        # queue, and only a heartbeat tells them apart.
        self.broadcast_runner = None

    # --- POST /v1/messages ---------------------------------------------------------------------
    def send(self, req):
        now = self.clock()
        key, phone, body, kind = validate_send(req)
        sha = D.body_sha256(body)

        verdict, entry = self.ledger.classify(key, sha, now)
        if verdict == "replay":
            return _replay_response(entry, self.rail_number, replayed=True, body_mismatch=False)
        if verdict == "mismatch":
            return _mismatch_response(entry, self.rail_number,
                                      self.ledger.mismatch_count(entry.client_msg_id))

        grant = self.governor.check(now=now, phone=phone, kind=kind,
                                    requested=(req.get("constraints") or {}))

        with ExitStack() as phone_held:
            # Step 4, and it is before ``begin`` on purpose -- see ORDER OF OPERATIONS above.
            self.take_phone(phone_held, phone)
            self.ledger.begin(key, phone=phone, kind=kind, body_sha256=sha, body_len=len(body),
                              now=now)
            try:
                self.driver.open_chat(phone)
            except D.DriverError as exc:
                # open_chat types nothing: their code sends the smsto: intent and reads the header.
                # So this is the one failure we can call safe, and the key stays usable.
                # PII: their message quotes the chat header (a candidate's name or number) on the
                # wrong-thread path, so the raw text stays in the mini-side ledger and the wire
                # gets the class of failure and a thread tag.
                self.ledger.mark_not_attempted(key, self.clock(), detail=str(exc))
                raise E.not_on_whatsapp("chat did not open; driver message is in the executor "
                                        "ledger", thread=L.thread_tag(phone)) from exc
            try:
                bubble = self.driver.send_bubble(body)
            except D.DriverError as exc:
                # From here on nothing is safe: their send path types first and raises later.
                self._escalate(key, phone, "send_raised", detail=str(exc))
                raise E.send_unconfirmed("driver raised after typing; driver message is in the "
                                         "executor ledger", thread=L.thread_tag(phone)) from exc

            if bubble.tick == D.UNVERIFIED:
                # Their "composer empty after send but bubble not matched -- treating as sent
                # (unverified)" path. It fired on 2 of 23 live sends and it is the single reason
                # this executor exists. Do NOT re-poll: their own loop just spent 30 s looking for
                # exactly this body and did not find it; a second pass would only hold the lock.
                self._escalate(key, phone, "driver_unverified")
                raise E.send_unconfirmed(
                    "driver reported 'unverified': the bubble was never found in the thread",
                    thread=L.thread_tag(phone))

            if bubble.tick_state is None:
                # Matched, but no tick drawn yet -- their loop stops at the body match and never
                # waits for the tick. Wait here, inside the same acquisition.
                confirmed = D.wait_for_tick(self.driver, sha, deadline_sec=self.tick_wait_sec,
                                            sleep=self.sleep, monotonic=self.monotonic)
                if confirmed is None:
                    self._escalate(key, phone, "no_tick")
                    raise E.send_unconfirmed(
                        f"bubble is in the thread but no delivery tick appeared within "
                        f"{self.tick_wait_sec:.0f}s", thread=L.thread_tag(phone))
                bubble = confirmed

            tick_state = D.require_tick(bubble)
            entry = self.ledger.mark_sent(key, self.clock(), tick=bubble.tick,
                                          tick_state=tick_state, clock=bubble.clock)
            # Opening a chat clears its notification, so anything that arrived since the last
            # watcher poll would vanish from the shade unmentioned. The thread is on screen and the
            # lock is ours: read it here, where it is free (TASK-143). Same ids as the shade would
            # have minted -- bridge/inbound.py keys on the local minute for exactly this reason.
            #
            # The result is journalled whatever it is (TASK-146). "0 messages" used to be the
            # answer both to a quiet chat and to a dump that came back unreadable, and those two
            # must not look alike on the one door that can lose a candidate's message: an empty
            # dump is now a DriverError at the Adb boundary and lands in ``thread_read_failed``,
            # and a genuinely quiet read is counted here so /v1/health can show it.
            try:
                messages, unresolved = self.driver.read_open_thread(phone)
                seen = self.record_inbound(messages, unresolved, self.clock())
                self.ledger.note(self.clock(), "thread_read", key, **seen)
            except D.DriverError as exc:
                self.ledger.note(self.clock(), "thread_read_failed", key, error=str(exc))
            # The ledger row is the durable truth and it is written before we tidy up. A park
            # failure must not turn a delivered message into an error the caller would retry.
            try:
                self.driver.park()
            except D.DriverError as exc:
                self.ledger.note(self.clock(), "park_failed", key, error=str(exc))

        return _sent_response(entry, self.rail_number, grant)

    def take_phone(self, stack, phone, **kw):
        """Take huawei01.lock, or refuse with 503 ``device_unavailable`` (TASK-146).

        The other lane takes the same flock and holds it across its own brain call, so losing the
        race is ordinary, not exceptional -- and it is the one failure class where NOTHING WAS
        TYPED. That is exactly what ``device_unavailable`` is defined as (errors.py), it is
        retryable, and it must leave the deterministic key clean: a raw ``DriverError`` escaping
        here reached the server's generic handler as a 500 instead, which is neither retryable nor
        truthful about what happened to the phone.
        """
        try:
            return stack.enter_context(self.driver.lock(**kw))
        except D.DriverError as exc:
            raise E.device_unavailable(f"the handset was not available: {exc}",
                                       thread=L.thread_tag(phone)) from exc

    def _escalate(self, key, phone, reason, detail=None):
        """One screenshot, only here. Their Device shoots before every input; ours shoots on
        escalation only (TASK-130 AC#9) -- see the NOTE in driver.py about what the wrap cannot fix.

        ``reason`` is a slug and becomes a filename: never an exception string, which on their
        wrong-thread path carries the chat header.
        """
        try:
            shot = self.driver.escalation_shot(f"{L.thread_tag(phone)}_{reason}")
        except D.DriverError as exc:
            shot = f"(screenshot failed: {exc})"
        self.ledger.mark_unconfirmed(
            key, self.clock(), detail=f"{reason} shot={shot}" + (f" driver={detail}" if detail else ""))

    # --- POST /v1/reconcile ----------------------------------------------------------------------
    def reconcile(self, client_msg_ids):
        """Three-valued, and only ``confirmed_absent`` authorises a resend.

        Honest limit, from the plan and not softened here: a tick is readable at send time but
        cannot be correlated afterwards, so the only evidence available later is a body scan of
        the chat -- and their read_thread reads the VISIBLE bubbles without scrolling. So
        confirmed_absent is returned only when the visible thread demonstrably covers the moment we
        attempted (there is an outgoing bubble at or after our attempt's clock) and our body is not
        in it. Everything else is ``indeterminate`` and goes to a human. A wrong confirmed_absent
        is a duplicate message to a real candidate; an indeterminate is a question.
        """
        results = []
        for key in client_msg_ids:
            entry = self.ledger.get(key)
            if entry is None:
                results.append({"client_msg_id": key, "verdict": "unknown_key",
                                "evidence": "no ledger row on this executor"})
                continue
            if entry.state == L.SENT:
                results.append({"client_msg_id": key, "verdict": "confirmed_sent",
                                "tick": entry.tick, "sent_at": entry.resolved_at,
                                "evidence": "ledger: delivery tick read at send time"})
                continue
            if entry.state in L.RESENDABLE:
                results.append({"client_msg_id": key, "verdict": "confirmed_absent",
                                "evidence": f"ledger state {entry.state}: nothing was typed"})
                continue
            results.append(self._scan(entry))
        return results

    def _scan(self, entry):
        now = self.clock()
        with ExitStack() as phone_held:
            self.take_phone(phone_held, entry.to_phone)
            try:
                self.driver.open_chat(entry.to_phone)
                bubbles = self.driver.read_bubbles()
            except D.DriverError as exc:
                return {"client_msg_id": entry.client_msg_id, "verdict": "indeterminate",
                        "evidence": f"thread unreadable: {exc}"}
            try:
                self.driver.park()
            except D.DriverError as exc:
                self.ledger.note(now, "park_failed", entry.client_msg_id, error=str(exc))

        outgoing = [b for b in bubbles if b.direction == "out"]
        mine = [b for b in outgoing if D.body_sha256(b.text) == entry.body_sha256]
        if mine and mine[-1].tick_state is not None:
            self.ledger.mark_sent(entry.client_msg_id, now, tick=mine[-1].tick,
                                  tick_state=mine[-1].tick_state, clock=mine[-1].clock)
            return {"client_msg_id": entry.client_msg_id, "verdict": "confirmed_sent",
                    "tick": mine[-1].tick, "sent_at": L.utc(now),
                    "evidence": "chat scan: body match carrying a delivery tick"}
        if mine:
            return {"client_msg_id": entry.client_msg_id, "verdict": "indeterminate",
                    "evidence": "chat scan: body match with no tick -- it may still deliver"}

        attempted_clock = _clock_hhmm(entry.attempted_at, self.governor.tz)
        covers = [b for b in outgoing if b.clock and b.clock >= attempted_clock]
        if not covers:
            return {"client_msg_id": entry.client_msg_id, "verdict": "indeterminate",
                    "evidence": f"chat scan: {len(outgoing)} visible outgoing bubbles, none at or "
                                f"after {attempted_clock} -- the view does not cover the attempt"}
        self.ledger.mark_absent(entry.client_msg_id, now,
                                evidence=f"chat scan: {len(outgoing)} visible outgoing bubbles, "
                                         f"{len(covers)} at or after {attempted_clock}, no body match")
        return {"client_msg_id": entry.client_msg_id, "verdict": "confirmed_absent",
                "evidence": f"chat scan: {len(covers)} outgoing bubbles at or after "
                            f"{attempted_clock}, none matching this body"}

    # --- GET /v1/outbox --------------------------------------------------------------------------
    def drain_inbound(self, *, lock_timeout=D.LOCK_TIMEOUT_SEC):
        """Phone -> ledger outbox. -> {"stored", "seen", "unresolved"}.

        The HTTP pull reads the table; it never touches the handset. Taking the flock here is what
        makes the watcher and a send queue instead of racing, and ``lock_timeout`` is short for the
        watcher so a long send does not stall a poll cycle -- a missed cycle is picked up by the
        next one, because the notification shade still holds the message.

        An inbound whose counterparty the handset cannot name is NOT put in the outbox: without a
        number there is no ``from`` for the envelope and the item would wedge the cursor forever.
        It is journalled and counted instead, and ``/v1/health`` carries the count.

        The lock failure is NOT converted to a refusal here, unlike ``send``: this is not an HTTP
        request, it is the watcher's own loop, and ``watcher.cycle`` counts a ``D.PhoneBusy`` as a
        skipped cycle rather than an incident. The shade still holds the message.
        """
        now = self.clock()
        with self.driver.lock(timeout=lock_timeout):
            messages, unresolved = self.driver.pull_inbound()
        return self.record_inbound(messages, unresolved, now)

    def record_inbound(self, messages, unresolved, now):
        stored = 0
        for message in messages:
            if self.ledger.append_inbound(message.inbound_id, message.payload(), now) is not None:
                stored += 1
        for title, reason in unresolved:
            # PII: the title is a candidate's name or number, so only its tag reaches the journal.
            self.ledger.note(now, "inbound_unresolved", None,
                             title_tag=L.thread_tag(title), reason=reason)
        self.inbound_unresolved += len(unresolved)
        self.inbound_seen += len(messages)
        self.inbound_last_at = L.utc(now)
        return {"stored": stored, "seen": len(messages), "unresolved": len(unresolved)}

    def outbox(self, *, after=0, limit=None, ack=None):
        if ack is not None:
            self.ledger.ack_inbound(ack, self.clock())
        events = self.ledger.pull_inbound(after=after, limit=limit)
        return {"ok": True, "events": events,
                "cursor": events[-1]["id"] if events else int(after),
                "backlog": self.ledger.inbound_backlog()}

    # --- GET /v1/health ----------------------------------------------------------------------------
    def health(self):
        now = self.clock()
        return {"ok": True, "version": VERSION, "at": L.utc(now),
                "rail": {"number": self.rail_number,
                         "msisdn_verified": self.rail_number is not None,
                         "note": None if self.rail_number else
                                 "MSISDN of L2N4C19B14054874 is UNVERIFIED and blocking (TASK-136)",
                         "driver": self.driver.describe()},
                "queue": self.ledger.queue_counts(),
                "quota": self.governor.quota(now),
                "inbound": {**self.ledger.inbound_backlog(),
                            "seen": self.inbound_seen,
                            "unresolved": self.inbound_unresolved,
                            "last_poll_at": self.inbound_last_at},
                "watcher": self.watcher.heartbeat() if self.watcher else None,
                "broadcast": {
                    "runs_open": len(self.ledger.open_runs()),
                    "runner": self.broadcast_runner.heartbeat() if self.broadcast_runner else None},
                "audit": {"destructions": self.ledger.audit_count()}}


# --- request validation and response shaping --------------------------------------------------------
def validate_send(req):
    key = req.get("client_msg_id")
    if not isinstance(key, str) or not key.startswith(KEY_PREFIX):
        raise E.invalid_request(f"client_msg_id must be a string starting {KEY_PREFIX!r}")
    phone = req.get("to")
    if not isinstance(phone, str) or not phone.startswith("+") or not phone[1:].isdigit():
        raise E.invalid_request("to must be an E.164 string (+digits); canonicalize server-side")
    if req.get("kind", "text") != "text":
        # Their package exports open_chat, read_thread, send_bubble, go_home, visible_unread and
        # nothing else: there is no media send path on this rail at all.
        raise E.invalid_request("kind must be 'text': the phone rail has no media send path")
    body = req.get("body")
    if not isinstance(body, str) or not body.strip():
        raise E.invalid_request("body must be a non-empty string")
    kind = (req.get("trace") or {}).get("action")
    return key, phone, body, kind


def _rail(rail_number):
    return {"number": rail_number, "msisdn_verified": rail_number is not None}


def _sent_response(entry, rail_number, grant):
    return 200, {
        "ok": True, "state": "sent", "client_msg_id": entry.client_msg_id,
        # There is no provider message id on this rail and there never will be: the client_msg_id
        # we mint is the only id an outbound message has (decision-8). Reported as null on purpose
        # rather than omitted, so a server-side reader cannot mistake absence for a lookup failure.
        "provider_msg_id": None,
        "sent_at": entry.resolved_at, "replayed": False,
        "verified": {"method": "delivery_tick", "tick": entry.tick, "tick_state": entry.tick_state,
                     "bubble_clock": entry.bubble_clock, "body_sha256": entry.body_sha256},
        "rail": _rail(rail_number),
        "quota": {"spent_today": grant.spent_today,
                  "spent_today_this_number": grant.spent_today_this_number,
                  "first_touches_today": grant.first_touches_today,
                  "next_slot_at": grant.next_slot_at},
    }


def _replay_response(entry, rail_number, *, replayed, body_mismatch):
    common = {"ok": True, "client_msg_id": entry.client_msg_id, "replayed": replayed,
              "body_mismatch": body_mismatch, "rail": _rail(rail_number)}
    if entry.state == L.SENT:
        return 200, {**common, "state": "sent", "provider_msg_id": None,
                     "sent_at": entry.resolved_at,
                     "verified": {"method": "delivery_tick", "tick": entry.tick,
                                  "tick_state": entry.tick_state,
                                  "bubble_clock": entry.bubble_clock,
                                  "body_sha256": entry.body_sha256}}
    # attempting / unconfirmed: uncertain, and it stays uncertain until a reconcile says otherwise.
    raise E.send_unconfirmed(
        f"key is {entry.state}: reconcile before any resend",
        state=entry.state, client_msg_id=entry.client_msg_id, replayed=replayed,
        body_mismatch=body_mismatch, attempts=entry.attempts)


def _mismatch_response(entry, rail_number, mismatches):
    """Rule 3. A different body under a live key sends nothing, whatever the key's state."""
    if entry.state in L.RESENDABLE:
        # There is no first-body result to replay and the first body may still be resent, so a
        # second body cannot be quietly accepted or quietly dropped: it is a conflict.
        raise E.idempotency_conflict(
            f"key already carries a different body (state {entry.state}); first body wins",
            client_msg_id=entry.client_msg_id, body_mismatch=True, mismatches=mismatches)
    return _replay_response(entry, rail_number, replayed=True, body_mismatch=True)


def _clock_hhmm(stamp, tz):
    """Their bubbles carry 'HH:MM' and no date -- that is all WhatsApp draws."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(tz).strftime("%H:%M")
