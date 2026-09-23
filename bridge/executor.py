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

import os
import pathlib
import time
from contextlib import ExitStack
from datetime import datetime, timezone

from . import driver as D
from . import errors as E
from . import identity as ID
from . import ledger as L

#: The route ``bridge/server.py`` answers the bytes on. Named here, not imported from ``server.py``
#: (which already imports this module -- a cross-import would be circular), because
#: ``media_metadata`` has to say where the bytes live and the two files are one contract.
MEDIA_RAW_PATH_SUFFIX = "/raw"

VERSION = "0.1.0"

#: How long ``auto_match_media`` waits for huawei01.lock (TASK-131 round 6). This is UI work, the
#: same class of work a send is -- it waits for the phone as long as a send would, not the
#: notification watcher's short 5 s (which does not take the lock at all any more -- see
#: ``drain_inbound``). A busy phone delays a match; it must never make one give up and guess.
IDENTITY_LOCK_TIMEOUT_SEC = 180.0
#: How wide a net "candidates worth opening a chat for" casts, in EITHER direction from the file's
#: own stat mtime. Purely a cost control on how many threads a cycle opens -- never a filter that
#: can exclude the only correct candidate: bridge/executor.py::Executor._narrow_by_time falls back
#: to the full candidate list rather than ever returning an empty one.
IDENTITY_TIME_WINDOW_SEC = 24 * 3600.0
#: Kinds whose WhatsApp bubble draws a comparable attribute at all (audio's duration, a document's
#: filename/size) -- see bridge/adb_driver.py's own docstring: an image or video bubble draws
#: neither. Used only to decide whether a SOLE candidate's own thread is worth opening to check for
#: a contradiction (blocker B2); irrelevant to the multi-candidate tie-break, which always reads.
EVIDENCE_BEARING_KINDS = frozenset({"audio", "document"})

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
        # Set by server.main when the media watcher starts (TASK-131). Same reason as ``watcher``
        # above: an empty media backlog is what a quiet rail looks like AND what a dead media
        # watcher looks like, and only a heartbeat tells them apart.
        self.media_watcher = None
        # Set by server.main when the broadcast runner starts (TASK-147). Same reason as the
        # watcher's counters: a run that is not moving and a runner that is dead produce the same
        # queue, and only a heartbeat tells them apart.
        self.broadcast_runner = None
        # Set by server.main when the identity watcher starts (TASK-131 round 6). Same reason again:
        # an empty unresolved queue is what a fully-caught-up rail looks like AND what a dead
        # matcher looks like.
        self.identity_watcher = None

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

    # --- POST /v1/photos (TASK-131 round 7, Ivan 2026-09-22): outbound media -----------------------
    def send_photos(self, phone, local_paths):
        """Share up to D.MAX_PHOTOS_PER_SEND local image files into the thread for ``phone``. ->
        [{"clock", "tick"}, ...], one per photo, in order.

        MECHANISM PROOF, NOT PRODUCTION-READY (said plainly, not papered over): unlike send(),
        this has no ledger idempotency (no client_msg_id, no replay/mismatch handling -- a retried
        call sends the photos again, full stop), no governor pacing check, and no inbound piggyback
        read. It exists to prove the drive-a-real-send-of-a-real-photo mechanism works at all
        (Ivan, 2026-09-22: 'реализуй и протестируй... возможность прикреплять до пяти фотографий'),
        on one number, by hand. Wiring this into Luna's own automatic sends needs all three of
        those before it ever reaches a real candidate -- tracked, not silently skipped.
        """
        if not local_paths:
            raise E.invalid_request("local_paths must be a non-empty list")
        if len(local_paths) > D.MAX_PHOTOS_PER_SEND:
            raise E.invalid_request(f"{len(local_paths)} photos requested, this rail sends at "
                                    f"most {D.MAX_PHOTOS_PER_SEND} at once")
        missing = [p for p in local_paths if not os.path.exists(p)]
        if missing:
            # Checked here, before the phone is ever touched, so a bad path is a 400 (nothing
            # attempted) and not a 504 (send_unconfirmed already tells the caller "the handset WAS
            # touched" -- claiming that over a file that was never pushed is the same false
            # positive TASK-130 exists to refuse). AdbDriver.send_photo keeps its own check too, for
            # a direct driver caller that skips this method.
            raise E.invalid_request(f"{len(missing)} of {len(local_paths)} file(s) do not exist on "
                                    f"this machine -- nothing was sent: {missing!r}")
        now = self.clock()
        with ExitStack() as phone_held:
            self.take_phone(phone_held, phone)
            try:
                self.driver.open_chat(phone)
            except D.DriverError as exc:
                raise E.not_on_whatsapp(f"chat did not open; driver message: {exc}",
                                        thread=L.thread_tag(phone)) from exc
            results = []
            try:
                for path in local_paths:
                    clock, tick = self.driver.send_photo(phone, path)
                    results.append({"clock": clock, "tick": tick})
                    self.ledger.note(self.clock(), "photo_sent", None,
                                     thread=L.thread_tag(phone), file=os.path.basename(path),
                                     tick=tick)
            except D.DriverError as exc:
                self.ledger.note(self.clock(), "photo_send_failed", None,
                                 thread=L.thread_tag(phone), sent_so_far=len(results), error=str(exc))
                raise E.send_unconfirmed(
                    f"sent {len(results)}/{len(local_paths)} photos, then: {exc}",
                    thread=L.thread_tag(phone)) from exc
            finally:
                try:
                    self.driver.park()
                except D.DriverError as exc:
                    self.ledger.note(self.clock(), "park_failed", None, error=str(exc))
        return {"ok": True, "at": L.utc(now), "sent": results}

    # --- POST /v1/gallery (TASK-131 round 7 gallery redesign, Ivan 2026-09-22): one message,
    # several photos, a shared caption -------------------------------------------------------------
    def send_gallery(self, phone, local_paths, caption=""):
        """Share up to D.MAX_PHOTOS_PER_SEND local image files as ONE WhatsApp message. ->
        {"clock", "tick"} the newest outgoing bubble reads after sending.

        MECHANISM PROOF, NOT PRODUCTION-READY (same caveat as send_photos, said plainly again
        rather than assumed carried over): no ledger idempotency, no governor pacing check, no
        inbound piggyback read. Ivan, 2026-09-22, mid-test of send_photos: 'Если вы сейчас фотки
        отправляют по одной, а мы можем отправить галерейкой плюс текстовое сообщение, все это
        одно сообщение' -- one message reads as one moment to a candidate, five bubbles do not.
        """
        if not local_paths:
            raise E.invalid_request("local_paths must be a non-empty list")
        if len(local_paths) > D.MAX_PHOTOS_PER_SEND:
            raise E.invalid_request(f"{len(local_paths)} photos requested, this rail sends at "
                                    f"most {D.MAX_PHOTOS_PER_SEND} at once")
        missing = [p for p in local_paths if not os.path.exists(p)]
        if missing:
            raise E.invalid_request(f"{len(missing)} of {len(local_paths)} file(s) do not exist on "
                                    f"this machine -- nothing was sent: {missing!r}")
        now = self.clock()
        with ExitStack() as phone_held:
            self.take_phone(phone_held, phone)
            try:
                self.driver.open_chat(phone)
            except D.DriverError as exc:
                raise E.not_on_whatsapp(f"chat did not open; driver message: {exc}",
                                        thread=L.thread_tag(phone)) from exc
            try:
                clock, tick = self.driver.send_gallery(phone, local_paths, caption=caption)
                self.ledger.note(self.clock(), "gallery_sent", None, thread=L.thread_tag(phone),
                                 files=[os.path.basename(p) for p in local_paths], tick=tick)
            except D.DriverError as exc:
                self.ledger.note(self.clock(), "gallery_send_failed", None,
                                 thread=L.thread_tag(phone), error=str(exc))
                raise E.send_unconfirmed(f"gallery send did not confirm: {exc}",
                                         thread=L.thread_tag(phone)) from exc
            finally:
                try:
                    self.driver.park()
                except D.DriverError as exc:
                    self.ledger.note(self.clock(), "park_failed", None, error=str(exc))
        return {"ok": True, "at": L.utc(now), "clock": clock, "tick": tick}

    # --- POST /v1/document (TASK-131 round 7, Ivan 2026-09-23: files, not photos alone) ---------
    def send_document(self, phone, local_path, caption=""):
        """Share ONE local file, any type, as WhatsApp's own document attachment. -> {"clock",
        "tick"} the newest outgoing bubble reads after sending.

        MECHANISM PROOF, NOT PRODUCTION-READY (send_gallery's own caveat, said again rather than
        assumed carried over): no ledger idempotency, no governor pacing check. Ivan, 2026-09-23:
        'в будущем будет задача с тем, что мы будем предлагать людям их резюме обновлять...
        поэтому файлы тоже мы должны уметь прикреплять' -- a future resume-update flow needs this.
        """
        if not local_path:
            raise E.invalid_request("local_path is required")
        if not os.path.exists(local_path):
            raise E.invalid_request(f"{local_path} does not exist on this machine -- nothing "
                                    f"was sent")
        now = self.clock()
        with ExitStack() as phone_held:
            self.take_phone(phone_held, phone)
            try:
                self.driver.open_chat(phone)
            except D.DriverError as exc:
                raise E.not_on_whatsapp(f"chat did not open; driver message: {exc}",
                                        thread=L.thread_tag(phone)) from exc
            try:
                clock, tick = self.driver.send_document(phone, local_path, caption=caption)
                self.ledger.note(self.clock(), "document_sent", None, thread=L.thread_tag(phone),
                                 file=os.path.basename(local_path), tick=tick)
            except D.DriverError as exc:
                self.ledger.note(self.clock(), "document_send_failed", None,
                                 thread=L.thread_tag(phone), error=str(exc))
                raise E.send_unconfirmed(f"document send did not confirm: {exc}",
                                         thread=L.thread_tag(phone)) from exc
            finally:
                try:
                    self.driver.park()
                except D.DriverError as exc:
                    self.ledger.note(self.clock(), "park_failed", None, error=str(exc))
        return {"ok": True, "at": L.utc(now), "clock": clock, "tick": tick}

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
    def drain_inbound(self):
        """Phone -> ledger outbox. -> {"stored", "seen", "unresolved"}.

        NO LOCK (TASK-131 round 6, fixing the root of half the decoy attributions this task's own
        brief was written from). ``pull_inbound`` is ``dumpsys notification --noredact`` -- a pure
        read that touches no UI -- and holding ``huawei01.lock`` for it was never load-bearing, only
        inherited from the send path's own discipline. The old shape skipped an entire watcher cycle
        whenever a send held the lock (90-150 s, executor.py's own note on that), so a notification
        that arrived mid-send was simply missed until the next 5 s poll saw the shade still holding
        it -- usually true, but not always (a second message inside that window, overwritten before
        the next poll, was the actual loss). Now this call can never be skipped by a busy phone: it
        always runs, and the durable queue it appends to (``ledger.append_inbound``, a plain sqlite
        write, no lock either) is drained separately by whatever does the UI work
        (``Executor.auto_match_media``, ``bridge/watcher.py::IdentityWatcher``), which DOES wait for
        the lock, as long as it needs to.

        An inbound whose counterparty the handset cannot name is NOT put in the outbox: without a
        number there is no ``from`` for the envelope and the item would wedge the cursor forever.
        It is journalled and counted instead, and ``/v1/health`` carries the count.
        """
        now = self.clock()
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

    # --- GET /v1/media/<id>, GET /v1/media/<id>/raw (TASK-131) -------------------------------------
    def media_metadata(self, media_id):
        """-> {"url", "mime_type", "filename", "size"} for a pulled file, or raise 404.

        ``url`` is a PATH, not an absolute URL: this executor has no way to know which local port
        the VPS's own ssh tunnel maps it to (today 18793, per ``docs/whatsapp.md`` -- and it must
        stay free to change without a redeploy of this side). ``app/wa/bridge.Client.media_url``
        resolves a relative answer against its own ``base_url`` before handing it back, the same way
        every other route on this rail is already addressed by that client.
        """
        row = self.ledger.media_file(media_id)
        if row is None:
            raise E.media_not_found(f"no media was ever pulled for id {media_id!r}")
        return {"ok": True, "url": f"/v1/media/{media_id}{MEDIA_RAW_PATH_SUFFIX}",
                "mime_type": row["mime_type"], "filename": row["filename"], "size": row["size"]}

    def media_bytes(self, media_id):
        """-> (blob, mime_type). A recorded row whose file is missing from disk is a LOUD error
        (executor_error, 500) rather than empty bytes read as a document with nothing in it --
        the failure mode this task's own acceptance criteria name explicitly."""
        row = self.ledger.media_file(media_id)
        if row is None:
            raise E.media_not_found(f"no media was ever pulled for id {media_id!r}")
        path = pathlib.Path(row["local_path"])
        try:
            blob = path.read_bytes()
        except OSError as exc:
            raise E.executor_error(
                f"media {media_id!r} is recorded at {path} but the file could not be read: {exc}"
            ) from exc
        return blob, row["mime_type"]

    # --- GET /v1/media, POST /v1/media/attach: the queue and the human escape hatch (TASK-131
    # round 5, decision-9 2026-09-22: automatic attribution removed) ------------------------------
    def unresolved_media(self):
        """-> every pulled file nobody has attached yet, oldest first, with what a human needs to
        decide and nothing that could identify who it might be about: kind, size, age, the handset
        folder it came from, and which threads plausibly relate to it in that period (a hint, never
        a decision -- ``bridge/ledger.py::media_related_threads``). A filename (often the sender's
        own real name) or a phone is exactly the identity question ``attach_media`` below exists for
        a HUMAN to settle, not something this listing should leak while doing it."""
        now = self.clock()
        files = [self._queue_entry(row, now) for row in self.ledger.media_queue()]
        return {"ok": True, "at": L.utc(now), "count": len(files), "files": files}

    def _queue_entry(self, row, now):
        around = row["mtime"]
        if not around:
            try:
                around = datetime.fromisoformat(row["pulled_at"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                around = None
        return {"queue_id": row["queue_id"], "media_id": row["media_id"], "kind": row["kind"],
               "size": row["size"], "source_dir": row["source_dir"], "pulled_at": row["pulled_at"],
               "age_sec": L.age_sec(row["pulled_at"], now),
               "related_threads": self.ledger.media_related_threads(row["kind"], around),
               # TASK-131 round 6 ALSO FIX: >1 means this file's bytes were pulled more than once
               # (a resend, or two people sending one identical file) -- visible on the row itself,
               # not just folded into whichever pull got attached or stored first.
               "content_pull_count": row["content_pull_count"]}

    def attach_media(self, queue_id, phone):
        """THE human escape hatch: tie one queued file to ``phone``'s own thread, by hand. -> the
        executor's own report. Goes through ``bridge/ledger.py::attach_media`` -- THE SAME CALL
        (``link_media``) an automatic link used to make, before round 5 removed automatic linking
        entirely -- so the document reaches the card, gets classified, and a voice note reaches
        transcription exactly as before.

        The operator names the phone directly: on this rail that is not a shortcut around the
        identity question, it IS the answer to it -- a human, using evidence off this machine (no
        signal on this rail proves it; see ``bridge/media.py``'s own module docstring). This works
        for every queued file the same way, including one pulled before this ledger ever recorded an
        inbound row for it at all (the six files already on the live rail when this round shipped):
        there is no "pending message" this has to find first any more, only the file id and the
        phone a human is naming.
        """
        now = self.clock()
        try:
            inbound_key = self.ledger.attach_media(queue_id, phone, now=now)
        except KeyError as exc:
            raise E.media_not_found(f"no queued file with id {queue_id!r}") from exc
        except ValueError as exc:
            raise E.already_attached(str(exc), queue_id=queue_id) from exc
        row = self.ledger.media_queue_row(queue_id)
        self.ledger.note(now, "media_attached", None, thread=L.thread_tag(phone),
                         queue_id=queue_id, media_id=row["media_id"], kind=row["kind"])
        return {"ok": True, "at": L.utc(now), "queue_id": queue_id, "media_id": row["media_id"],
               "kind": row["kind"], "thread": L.thread_tag(phone), "inbound_id": inbound_key}

    # --- automatic identity matching (TASK-131 round 6, Ivan's ruling 2026-09-22) ------------------
    def auto_match_media(self, *, lock_timeout=IDENTITY_LOCK_TIMEOUT_SEC):
        """Work the unresolved queue, attributing what ``bridge/identity.py::decide`` can decide on
        what a file IS. -> {"attached", "weak"}. Never raises: a driver failure reading one
        candidate's thread is journalled and that row is left for the next cycle, the same
        never-raises contract every watcher cycle here already keeps.

        Called by ``bridge/watcher.py::IdentityWatcher`` on its own schedule, never by
        ``InboundWatcher`` or ``MediaWatcher`` -- this is the UI work bridge/watcher.py's own
        module docstring describes as a separate job from draining the shade, and the only one of
        the three that ever takes ``huawei01.lock``.
        """
        now = self.clock()
        attached = weak = 0
        for row in self.ledger.media_queue(auto_only=True):
            candidates = self.ledger.unlinked_media_candidates(row["kind"])
            if not candidates:
                continue      # nothing of this kind has arrived yet -- retried next cycle
            evidence = None
            # Evidence is read whenever it could change the decision: always among several
            # candidates (the tie-break), and ALSO for a sole candidate when this file's own kind
            # can carry a comparable bubble attribute at all (audio's duration, a document's
            # filename/size) -- round 6's own bug (Ivan's brief, blocker B2): a sole candidate was
            # unconditionally "strong" even when its own thread's bubble read a flatly different
            # duration, because evidence was never even looked at for that case. An image or video
            # bubble draws neither today (bridge/adb_driver.py's own docstring), so reading one
            # would only cost a chat open for nothing -- skipped, exactly as before, for those kinds.
            if len(candidates) > 1 or row["kind"] in EVIDENCE_BEARING_KINDS:
                narrowed = self._narrow_by_time(candidates, row["mtime"]) if len(candidates) > 1 \
                    else candidates
                try:
                    evidence = self._read_evidence_for(narrowed, lock_timeout=lock_timeout)
                except (D.DriverError, E.BridgeRefusal) as exc:
                    # D.DriverError: a thread would not open or read. E.BridgeRefusal: take_phone's
                    # own device_unavailable when even ``lock_timeout`` (round 6's long, send-sized
                    # wait) was not enough. Either way: try again next cycle rather than decide on
                    # an incomplete read.
                    self.ledger.note(now, "identity_evidence_failed", None, kind=row["kind"],
                                     error=str(exc))
                    continue
            decision = ID.decide(self._file_facts(row), candidates, evidence)
            if decision is None:
                continue
            phone, inbound_id, strength, reason = decision
            try:
                created = self.ledger.link_media_auto(row["queue_id"], inbound_id, phone,
                                                       strength=strength, reason=reason, now=now)
            except (KeyError, ValueError) as exc:
                self.ledger.note(now, "identity_attach_failed", None, kind=row["kind"],
                                 error=str(exc))
                continue
            if created:
                attached += 1
                weak += int(strength == "weak")
                self.ledger.note(now, "media_auto_attach_decided", None, kind=row["kind"],
                                 strength=strength, reason=reason)
        return {"attached": attached, "weak": weak}

    def _file_facts(self, row):
        """-> the pulled file's own facts (TASK-131 round 6): size and kind off the queue row
        (always known), filename off the store (only meaningful for a document -- the one kind
        WhatsApp keeps the sender's own name for) and, for audio, the duration computed from the
        file's own bytes (``bridge/identity.py::opus_duration_seconds_of_file`` -- no bubble read
        needed for this half of the comparison, only for the candidate's)."""
        media = self.ledger.media_file(row["media_id"]) or {}
        facts = {"kind": row["kind"], "size": row["size"], "filename": media.get("filename")}
        if row["kind"] == "audio" and media.get("local_path"):
            try:
                facts["duration_sec"] = ID.opus_duration_seconds_of_file(media["local_path"])
            except (ValueError, OSError) as exc:
                self.ledger.note(self.clock(), "identity_duration_failed", None, error=str(exc))
        return facts

    @staticmethod
    def _narrow_by_time(candidates, mtime):
        """-> candidates within ``IDENTITY_TIME_WINDOW_SEC`` of the file's own stat mtime, or every
        candidate when that narrows to nothing (no mtime, or all of them fall outside the window) --
        time is a cost control on how many threads get opened, never a filter that can exclude the
        only right answer (Ivan's ruling)."""
        if not mtime:
            return candidates
        narrowed = [c for c in candidates if c.get("time_ms") and
                   abs(c["time_ms"] / 1000.0 - mtime) <= IDENTITY_TIME_WINDOW_SEC]
        return narrowed or candidates

    def _read_evidence_for(self, candidates, *, lock_timeout):
        """-> {phone: {"duration_sec", "size_bytes", "pages", "filename"}} for each distinct
        candidate phone: open its thread, read every media bubble's own evidence
        (``driver.read_media_evidence``), and keep the NEWEST non-None value per field across all of
        them -- the candidate set here is already narrowed to one chat minute or so, so more than
        one media bubble in the window is the rare case this is written to still handle sanely
        rather than assume away. One lock acquisition per phone (the same flock discipline the send
        path documents: released between chats, not held across all of them).

        Newest, not oldest (fixed TASK-131 round 6 blocker B1): ``driver.read_media_evidence``
        returns bands oldest first, and the file this cycle is trying to place is -- by definition
        of reaching this candidate pool at all -- one nobody has attributed yet, i.e. the newest
        thing on that thread. An older bubble further up the same chat belongs to a file that was
        already resolved in an earlier cycle (or is simply old scrollback); letting it answer for
        today's file is exactly how a 7-second voice note landed on a thread whose own newest bubble
        read 1:30, because an unrelated 0:07 sat higher up the same screen."""
        by_phone = {}
        for phone in sorted({c["phone"] for c in candidates}):
            with ExitStack() as phone_held:
                self.take_phone(phone_held, phone, timeout=lock_timeout)
                try:
                    self.driver.open_chat(phone)
                    bands = self.driver.read_media_evidence()
                except D.DriverError as exc:
                    # This ONE candidate's thread would not open or read -- leaves it with no
                    # evidence (decide() treats that as "does not confirm", never as "excluded"),
                    # not a reason to give up on every other candidate in the same sweep.
                    self.ledger.note(self.clock(), "identity_thread_unreadable", None, error=str(exc))
                    continue
                finally:
                    try:
                        self.driver.park()
                    except D.DriverError as exc:
                        self.ledger.note(self.clock(), "park_failed", None, error=str(exc))
            merged = {"duration_sec": None, "size_bytes": None, "pages": None, "filename": None}
            for band in reversed(bands):    # newest first -- see this method's own docstring (B1)
                for key, value in ID.parse_bubble_evidence(band["evidence"]).items():
                    if merged[key] is None and value is not None:
                        merged[key] = value
            by_phone[phone] = merged
        return by_phone

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
                "media_watcher": self.media_watcher.heartbeat() if self.media_watcher else None,
                "identity_watcher": self.identity_watcher.heartbeat() if self.identity_watcher else None,
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
