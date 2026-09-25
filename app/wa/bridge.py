"""The phone rail's outbound client (TASK-223): same duck shape as ``meta.Client``, different proof.

Architecture (decision-8, ``/home/claude/plans/2026-09-21-macmini-revision.md`` §4): our own
executor on the remote Ubuntu box imports the colleague's ``device.py`` / ``whatsapp.py`` /
``inbox.py`` as a driver library. Luna stays the brain here, ``data/wa.sqlite`` stays the source of
truth, and this module is the only thing in ``app/wa`` that knows the rail exists. Everything it
speaks is the HTTP contract in ``docs/whatsapp.md`` ("Transports"), so the executor behind it is
swappable without touching a caller.

WHAT IS DIFFERENT FROM THE META RAIL, and why it is in code rather than in prose:

* **No provider message id, ever.** The lane behind the executor has no message identifier of any
  kind, so the ``client_msg_id`` we mint (``app/wa/bridge_ids.py``, TASK-217) is the only id an
  outbound message has, and it is what ``send_text`` returns into ``wa_messages.wamid``.
* **Proof of delivery is a tick, read once, at send time.** ``VERIFIED_TICKS`` are the three
  content-desc values their UI scraper reads off the bubble (``whatsapp.py:27``:
  ``'' | 'Gesendet' | 'Zugestellt' | 'Gelesen'``). ``"unverified"`` is NOT one of them: it is their
  sentinel for "pressed send, could not find the bubble" (``whatsapp.py:140-142``), which their code
  records as sent and which fired on 2 of their 23 live sends. Here it is a 504.
  ``meta.py:560-563`` keeps its own form of this rule unchanged -- this is a per-rail
  renegotiation, not a weakening.
* **202-first is normal, and 202 is not sent.** The rail paces every message, so "accepted, not yet
  sent" is the common answer. It leaves this client as ``BridgeAccepted`` (status 504, uncertain),
  never as a returned id: the harness has exactly two outcomes for a send -- an id, which
  ``api.py:943-951`` records as sent, or a raise -- and recording a queued message as sent is the
  one lie that corrupts a thread. ``bridge_sync`` (TASK-125) is where those actually resolve;
  TASK-126 expires the ones that never do.

ERRORS. ``BridgeError`` subclasses ``M.MetaError`` so every existing ``except M.MetaError`` keeps
catching, and the two callers whose behaviour depends on ``.status_code`` stay byte-identical:
``luna/campaign.py:674-678`` classifies 4xx as ``failed`` (ownership restored) and anything else as
``uncertain``; ``luna/import_history.py:538-539`` treats a ``None`` status as "not an answer about
this media" and aborts the whole import run. That is why an unreachable bridge is 424 on a send
(nothing went out, retryable) but keeps ``status_code=None`` on a media call (no answer at all).

| situation                          | raises                        | campaign classifies |
|------------------------------------|-------------------------------|---------------------|
| 400/401/409/422/429 from the bridge | BridgeError(that status)      | failed              |
| 500/503 from the bridge             | BridgeError(that status)      | uncertain           |
| 200 with no verified tick           | BridgeError(504)              | uncertain, never auto-resent |
| 202 accepted, not yet sent          | BridgeAccepted(504)           | uncertain, resolved by TASK-125 |
| bridge unreachable, send            | BridgeError(424)              | failed              |
| bridge unreachable, media           | BridgeUnreachable(None)       | aborts the import run |

WIRED IN. ``transport.build`` returns this client for ``rail == "bridge"`` (``transport.py:51``),
``WA_TRANSPORT=bridge`` makes it the rail every unpinned thread resolves to, and the executor it
speaks to runs on the handset machine (``deploy/wa-bridge/INSTALL.md``). Setting that variable is
therefore a live change, not an inert flag: it builds a client that drives a real handset. What is
still UNVERIFIED is which number that handset sends FROM (TASK-136), which is why nothing but
Ivan's own test number is pinned to this rail.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import bridge_ids as BI
from . import config as C
from . import meta as M

# The bubble's status content-desc, German UI, as the driver reads it (``whatsapp.py:27``). Anything
# outside this tuple -- "", "unverified", a missing key -- is not proof of delivery.
VERIFIED_TICKS = ("Gesendet", "Zugestellt", "Gelesen")
# Their sentinel for "pressed send, never found the bubble". Named so the refusal can say which
# failure it is refusing.
UNVERIFIED_TICK = "unverified"
# "Nothing went out and we know it": the send is retryable and ownership goes back to whoever held
# it (campaign.py:676 -> failed). 424 Failed Dependency is the plan's choice for a dead executor.
UNREACHABLE_SEND_STATUS = 424
# "Something may have gone out and we cannot tell": never auto-resent (campaign.py:678 -> uncertain).
UNCERTAIN_STATUS = 504
# A local refusal before any POST -- a 4xx so campaign.py files it as failed, since nothing was sent.
CONTRACT_STATUS = 400

MESSAGES_PATH = "/v1/messages"
MEDIA_PATH = "/v1/media/"
HEALTH_PATH = "/v1/health"

# --- the handset operations as one-step calls (TASK-147) ------------------------------------------
# Ivan, 2026-09-21: the handset operations stop being hand-written adb one-liners and become tools a
# model or an operator calls in one step. These routes are the executor's half of that contract
# (``bridge/server.py``); they are constants because the two halves are one agreement, so a rename on
# that side is one line here. ``tools/wa_bridge.py`` is the operator's front door to them.
# (clear-chat/delete-chat used to live here too -- removed entirely, TASK-289, see
# bridge/operations.py's own module docstring for why.)
CHATS_PATH = "/v1/chats"
THREAD_PATH = "/v1/thread"
BROADCASTS_PATH = "/v1/broadcasts"
AUDIT_PATH = "/v1/audit"
# TASK-261: the read-only companion to RECONCILE_PATH -- which ids need one.
UNRESOLVED_PATH = "/v1/unresolved"
# TASK-131 round 4: the human escape hatch -- an unresolved file's own listing, and attaching one
# to a phone by hand, through the exact path an automatic link takes.
MEDIA_LIST_PATH = "/v1/media"
MEDIA_ATTACH_PATH = "/v1/media/attach"
PHOTOS_PATH = "/v1/photos"
GALLERY_PATH = "/v1/gallery"
DOCUMENT_PATH = "/v1/document"
RECONCILE_PATH = "/v1/reconcile"
# TASK-227: every phone-touching route above answers 200 {"op_id", "state": "queued"} instead of
# its result now -- a real FIFO queue on the executor's side, not a bare flock. _request() below
# polls this route until the op is terminal and unwraps it back to the exact (status, body) shape
# the route answered before the queue existed, so nothing past _request needs to know it exists.
OPS_PATH = "/v1/ops"
# TASK-243: every ``_request`` call carries the caller's OWN already-computed budget for that call
# (``send_timeout``/``_timeout_for`` above) in this header, so the executor's ``claim_next_op`` can
# refuse to start a queued op nobody is still waiting on any more -- see ``bridge/ledger.py``'s own
# comment on ``budget_sec``. Never a number invented on that side: it is exactly what this side was
# already willing to wait, restated where the queue can read it.
OP_BUDGET_HEADER = "X-Wa-Op-Budget-Sec"

# The executor's four per-item statuses (``bridge/broadcast.py``): ``sent`` is a verified tick or a
# ledger replay of one, ``queued`` is not attempted yet or deferred to ``next_attempt_at``,
# ``refused`` was refused before a key was pressed, ``failed`` touched the handset without a tick
# and is never retried automatically.
BROADCAST_SENT = "sent"
BROADCAST_QUEUED = "queued"
# Ours, and the executor never mints it: a key we posted that the run view does not mention at all.
BROADCAST_NO_ANSWER = "no_answer"
# The run's own state (``bridge/ledger.py``). Only an OPEN run has a runner working through it: the
# executor selects due items out of open runs alone, so a queued item on a stopped or done run is
# an item nothing will ever attempt, not an item still on its way.
RUN_OPEN = "open"

# --- the wire's pacing vocabulary (TASK-146) ------------------------------------------------------
# ``trace.action`` is what the executor's governor paces on, and it has exactly two legal values
# (``bridge/governor.py:KINDS``). It is NOT Luna's action slug: "ask_question", "media_ack",
# "ask_consent" and "followup" say what the message is FOR, and the fuse needs to know whether it is
# cold contact or an answer -- different gaps, different daily caps. Sending the slug there refused
# every outbound on this rail with a 400 before it reached the phone.
#
# The class is derived from WHICH TURN IS OPEN, which is structural rather than a label anyone
# chooses: a campaign attempt is by definition the first thing a stranger sees from this number, and
# a conversational turn by definition answers a message they sent us. The slug still travels, as
# ``trace.intent``, because the executor's ledger and journal are the audit trail for what was said.
PACING_FIRST_TOUCH = "first_touch"
PACING_REPLY = "reply"

# What one bubble costs the executor end to end, for the read timeout below. Read off the handset
# side rather than guessed: ``bridge/adb_driver.py`` waits up to 12 s for the chat header, pauses up
# to 4 s after opening (PAUSE_AFTER_OPEN), types at CHARS_PER_SEC = (3.2, 5.5), pauses up to 2.5 s
# before the tap (PAUSE_BEFORE_SEND), allows BUBBLE_APPEAR_SEC = 30 s for the bubble to be drawn and
# ``bridge/driver.py`` TICK_WAIT_SEC = 30 s for the tick, then reads the thread and parks.
EXECUTOR_FIXED_BUDGET_SEC = 90
EXECUTOR_SLOWEST_CHARS_PER_SEC = 3.2

#: How often _await_op polls GET /v1/ops/<id> while a queued phone operation is still
#: queued/running (TASK-227). Not a cap on anything -- the caller's own per-route timeout (the
#: budgets above) is what actually bounds the wait; this only sets how promptly it notices the
#: answer once the op is done.
OP_POLL_INTERVAL_SEC = 0.3

# --- what the OTHER routes cost the handset ---------------------------------------------------
# THE UNIT IS A CHAT-LIST PASS, and it is measured rather than guessed: GET /v1/chats (which is
# bridge/operations.py::list_chats -> driver.list_chats(include_archived=True): the main list and
# the archive, each scrolled to its own end) answered in 32.5 s and 29.8 s back to back on that
# handset on 2026-09-21 with 7 conversations on it.
HANDSET_CHAT_LIST_PASS_SEC = 33
# executor.take_phone waits the other lane out before it refuses: bridge/driver.py LOCK_TIMEOUT_SEC
# is 30 s, and a slow call can spend all of it before its first pass.
FLOCK_WAIT_SEC = 30
# GET /v1/chats: the flock, one pass, and park. GET /v1/thread: the flock, one pass to find the row
# (_locate), 12 s + 4 s to open it, a dump to read the bubbles, and park. Both land under the 90 s
# floor below, so neither changes today -- they are written down so that staying under it is a fact
# somebody can check rather than a coincidence nobody noticed.
CHATS_BUDGET_SEC = FLOCK_WAIT_SEC + HANDSET_CHAT_LIST_PASS_SEC + 5
THREAD_BUDGET_SEC = FLOCK_WAIT_SEC + HANDSET_CHAT_LIST_PASS_SEC + 16 + 5 + 5
# reconcile (TASK-230) scans one thread per id that is not already settled by its own ledger row
# (bridge/executor.py::Executor._scan) -- variable count, so this is a per-id cost the caller
# multiplies rather than a fixed budget like the ones above.
RECONCILE_PER_ID_SEC = HANDSET_CHAT_LIST_PASS_SEC + 16 + 5

# --- what the MEDIA routes cost the handset (TASK-247) ---------------------------------------------
# send_photos/send_gallery/send_document called _request with no timeout= at all, so they inherited
# the bare WA_BRIDGE_TIMEOUT_SEC floor -- the exact bug DESTROY_BUDGET_SEC above exists to fix, on
# the three routes that were added (TASK-131 round 7) after TASK-243 fixed it everywhere else. Not a
# tail case: GALLERY_BUDGET_SEC below already exceeds the 90 s floor with an EMPTY caption, so a real
# captioned gallery times out on every single call while the send is still landing on the handset.
#
# Each term is read off bridge/adb_driver.py's own named waits, never re-measured or guessed:
#   open a chat                 wait_for(header) 12 s + PAUSE_AFTER_OPEN up to 4 s (adb_driver.py:
#                                727, 736)
#   the Android share picker    wait_for(the matched row) 10 s (adb_driver.py:978, :1234), then the
#                                compose/confirm screen 10 s (adb_driver.py:989, :1246) -- send_photo
#                                and send_document only; send_gallery drives WhatsApp's OWN in-chat
#                                picker instead of Android's share sheet
#   the in-chat attach menu     the attach button 10 s + the "Galerie" option 10 s (adb_driver.py:
#                                1117, 1122) -- send_gallery only
#   the gallery picker's index  GALLERY_INDEX_SEC 45 s (adb_driver.py:186, waited at :1136)
#   the appear wait              PHOTO_APPEAR_SEC 60 s (adb_driver.py:178, used by _verify_photo_sent
#                                at :1002) -- NOT the shorter BUBBLE_APPEAR_SEC a text send waits on;
#                                all three of these routes read their result off _verify_photo_sent,
#                                never off _verify
# All three routes also re-open the chat to reverify what they just sent ("the share/send flow may
# land anywhere; come back to prove the result", adb_driver.py:998, :1190, :1283) -- a SECOND open on
# top of whichever open happened before the send. The caption, where one exists, is added the same
# way send_timeout adds a text body's typing time.
HANDSET_OPEN_CHAT_SEC = 16
HANDSET_SHARE_PICKER_SEC = 10
HANDSET_SHARE_COMPOSE_SEC = 10
HANDSET_ATTACH_SEC = 10
HANDSET_GALLERY_HOLDER_SEC = 10
HANDSET_GALLERY_INDEX_SEC = 45
HANDSET_PHOTO_APPEAR_SEC = 60

# send_gallery: the flock wait, one open_chat before the picker (bridge/executor.py's own
# send_gallery), the attach-menu waits, the index wait, the reverifying open_chat, and the appear
# wait -- 30+16+10+10+45+16+60 = 187 s before a caption is even typed, already more than double the
# bare 90 s floor on every single call.
GALLERY_BUDGET_SEC = (FLOCK_WAIT_SEC + HANDSET_OPEN_CHAT_SEC + HANDSET_ATTACH_SEC
                      + HANDSET_GALLERY_HOLDER_SEC + HANDSET_GALLERY_INDEX_SEC
                      + HANDSET_OPEN_CHAT_SEC + HANDSET_PHOTO_APPEAR_SEC)
# send_document: the flock wait, one open_chat before the share intent (bridge/executor.py's own
# send_document), the share picker, the compose/confirm screen, and -- unlike a photo -- a SECOND
# wait for a document's own intermediate recipient-confirm step before the filename readback
# (adb_driver.py:1246-1256), the reverifying open_chat, and the appear wait.
DOCUMENT_BUDGET_SEC = (FLOCK_WAIT_SEC + HANDSET_OPEN_CHAT_SEC + HANDSET_SHARE_PICKER_SEC
                       + 2 * HANDSET_SHARE_COMPOSE_SEC + HANDSET_OPEN_CHAT_SEC
                       + HANDSET_PHOTO_APPEAR_SEC)
# send_photos (plural) is not one bounded cost: AdbDriver.send_photos calls send_photo once per
# path, in order, inside ONE flock hold (its own docstring: "Stops at the first failure ... a caller
# that wants best-effort has to call send_photo itself") -- the share picker, the reverifying
# open_chat and the appear wait are each paid AGAIN for every file. HANDSET_ONE_PHOTO_SEC is that
# per-file term, multiplied by the caller the same way RECONCILE_PER_ID_SEC is; PHOTOS_FLOOR_SEC is
# what is spent once regardless of count (the flock wait, and the one open_chat bridge/executor.py's
# own send_photos does before the loop starts).
HANDSET_ONE_PHOTO_SEC = (HANDSET_SHARE_PICKER_SEC + HANDSET_SHARE_COMPOSE_SEC
                         + HANDSET_OPEN_CHAT_SEC + HANDSET_PHOTO_APPEAR_SEC)
PHOTOS_FLOOR_SEC = FLOCK_WAIT_SEC + HANDSET_OPEN_CHAT_SEC

# --- the codes this client mints when the ANSWER is lost ------------------------------------------
# Not the executor's taxonomy (bridge/errors.py): the CLI prints this as "the answer was lost",
# never as "the bridge refused" -- the same call may still be running on the handset.
CODE_ANSWER_TIMEOUT = "answer_timeout"
# The executor's OWN 504 (bridge/errors.py), whose message says the handset was touched: keys
# pressed with no tick read. Named here because "the bridge refused" must never be printed over it
# -- the executor refused nothing, it did the thing and cannot prove how it ended, which is the
# 2026-09-21 sentence in the executor's vocabulary instead of ours. (clear_chat/delete_chat had
# their own such code, destruction_unverified, and their own lost-answer recovery machinery below
# it -- removed with the capability, TASK-289.)
CODE_SEND_UNCONFIRMED = "send_unconfirmed"
HANDSET_TOUCHED_CODES = (CODE_SEND_UNCONFIRMED,)


class BridgeError(M.MetaError):
    """A bridge call failed. Subclasses ``M.MetaError`` on purpose: every ``except M.MetaError`` in
    the harness catches it unchanged, and ``.status_code`` keeps its existing meaning at both call
    sites that read it (``campaign.py:674-678``, ``import_history.py:538-539``). ``code`` is our own
    ``wab-*``-style slug -- never a forged Meta numeric, which would be provenance forgery into a
    table whose reader documents itself as Meta's delivery status."""

    def __init__(self, message, status_code=None, payload=None, code=None, client_msg_id=None):
        super().__init__(message, status_code=status_code, payload=payload)
        self.code = code
        self.client_msg_id = client_msg_id


class BridgeUnreachable(BridgeError):
    """No answer at all: the tunnel is down, the executor is not listening, the socket timed out.
    ``status_code`` stays None, which is what makes ``import_history.py:539`` abort an import run
    rather than record the document as unrecoverable. On a send it is translated to
    ``UNREACHABLE_SEND_STATUS`` by ``_post_message`` -- that asymmetry is the whole point."""


class BridgeAccepted(BridgeError):
    """HTTP 202: the executor owns the message and has not sent it yet (paced, queued, quiet hours).
    Not an error on the wire; an error *here*, because the only way this client can say "sent" is by
    returning an id. Carries ``client_msg_id`` and the 202 body so TASK-125's reconciliation pass can
    resolve it and TASK-126 can expire it."""


def _parse(body):
    try:
        return json.loads(body.decode("utf-8")) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"raw": body.decode("utf-8", errors="replace")}


def _default_transport(method, url, headers=None, data=None, timeout=None):
    """-> ``(http_status, parsed_body)``. Deliberately NOT ``meta._default_transport``'s shape, which
    returns the parsed body alone: on this rail 200-vs-202 is the difference between "delivered" and
    "the executor still has it", and a body cannot be trusted to carry that distinction itself."""
    req = urllib.request.Request(url=url, data=data, method=method.upper())
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return getattr(resp, "status", 200), _parse(resp.read())
    except urllib.error.HTTPError as exc:
        payload = _parse(exc.read())
        raise BridgeError(f"bridge HTTP {exc.code}", status_code=exc.code, payload=payload,
                          code=((payload.get("error") or {}).get("code")
                                if isinstance(payload.get("error"), dict) else None)) from exc
    except TimeoutError as exc:
        # The read timed out with the request already delivered: the executor may well be typing
        # this message on the handset right now. That is UNCERTAIN, not unreachable -- 424 would
        # tell campaign.py the send failed with ownership restored, and the message would then be
        # sent a second time by the next pass (TASK-146). It is not a URLError, so it used to reach
        # send_and_record as a bare TimeoutError that nothing classified at all.
        # "still working", not "still sending": this transport also carries the destructive routes,
        # and telling an operator his delete "may still be sending" is how a finished deletion got
        # read as a failure on 2026-09-21. What it was doing is the caller's business to say.
        raise BridgeError(f"bridge did not answer within {timeout}s: the executor may still be "
                          f"working", status_code=UNCERTAIN_STATUS,
                          code=CODE_ANSWER_TIMEOUT) from exc
    except urllib.error.URLError as exc:
        raise BridgeUnreachable(f"bridge unreachable: {exc.reason}") from exc


def _default_media_transport(method, url, headers=None, timeout=None):
    """Raw bytes, never parsed -- same reason ``meta._default_binary_transport`` exists: a
    JSON-parsing transport corrupts binary media content."""
    req = urllib.request.Request(url=url, method=method.upper())
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise BridgeError(f"bridge HTTP {exc.code}", status_code=exc.code,
                          payload={"raw": exc.read().decode("utf-8", errors="replace")[:500]}) from exc
    except urllib.error.URLError as exc:
        raise BridgeUnreachable(f"bridge unreachable: {exc.reason}") from exc


class Client:
    """Outbound half of the phone rail. One instance per turn, not per process: the only state it
    holds is which turn it is sending, because that is what makes the minted ids deterministic.

    ``requires_freeform_window = False`` is this rail's entire v1 value: a consumer chat has no 24 h
    Cloud-API window, so a thread Meta would refuse is answerable here. The gate that reads it is
    ``api.py:936`` (``_freeform_window_open``, ``api.py:907``), via ``getattr(cl, "requires_freeform_window", True)``
    once TASK-221 lands -- every existing FakeMeta without the attribute keeps Meta semantics.

    ``supports_buttons = False`` and ``wants_idempotency_key = True`` are the other two capability
    flags: there is no way to compose a TAPPABLE reply button on a phone (a platform limit, not a
    gap -- ``send_buttons`` renders the titles as numbered text instead, and the flag stays False
    because the candidate's answer comes back as typed text and not as a tap), and this transport
    cannot mint a key for itself out of thin air -- see ``begin_turn``.
    """

    requires_freeform_window = False
    supports_buttons = False
    wants_idempotency_key = True

    def __init__(self, transport=None, media_transport=None, base_url=None, token=None, timeout=None,
                 sleep=time.sleep, now=None):
        self.transport = transport or _default_transport
        self.media_transport = media_transport or _default_media_transport
        self.base_url = (C.BRIDGE_URL if base_url is None else base_url).rstrip("/")
        self.token = C.BRIDGE_TOKEN if token is None else token
        self.timeout = C.BRIDGE_TIMEOUT_SEC if timeout is None else timeout
        # How the inter-bubble gap is waited out; injectable so a test does not sit through it.
        self.sleep = sleep
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._next_slot_at = None
        self._turn = None
        self._campaign = None
        # The last terminal 200 body, for the caller that wants the tick and the replay flags
        # (TASK-125's ledger reconciliation, TASK-130's audit). Not consulted by this module.
        self.last_send = None

    # --- which turn is being sent (TASK-217) ------------------------------------------------------

    def begin_turn(self, phone, turn_key, action):
        """Open a conversational turn: one ``turn_key``, many bubbles, one HTTP call per bubble.

        The executor needs the turn boundary to key its ledger and to take the handset's flock once
        per bubble rather than once per turn (a remote brain call must never hold the phone). Here it
        is what makes ``send_text`` deterministic: bubble N of this turn always gets the same
        ``client_msg_id``, in any process, after any restart, so the catch-up timer re-driving a
        failed turn (``api.py:885`` -> ``store.py:311-318`` -> ``pflege-wa-catchup.timer``) replays
        the bubbles that already went out instead of sending them again.

        ``turn_key`` is required and explicit: on this rail there is no inbound provider id to fall
        back on (TASK-131 mints ours), and a default would key two different turns the same.
        """
        self._turn = {"phone": BI.require_e164(phone),
                      "turn_key": BI.require_text(turn_key, "turn_key"),
                      "action": BI.require_text(action, "action"),
                      "next_index": 0}
        self._campaign = None

    def begin_campaign_attempt(self, campaign_id, phone, attempt):
        """Open one campaign attempt: exactly one message, so its key is constant for the attempt.

        ``attempt`` is the number ``store.claim_campaign_send`` (``store.py:929``) already wrote
        before the POST, so a retry inside the same attempt re-posts the same key and the executor's
        ledger replays it -- idempotent across process death, which the Cloud API does not give us.
        """
        self._campaign = {"campaign_id": BI.require_text(campaign_id, "campaign_id"),
                          "phone": BI.require_e164(phone),
                          "attempt": BI.require_index(attempt, "attempt", 1)}
        self._turn = None

    def _next_send(self, to_e164):
        """-> ``(client_msg_id, trace)`` for the message about to go out, or raise.

        A campaign attempt is one message, so its key is constant: a retry inside the same attempt
        re-posts the same key and the executor's ledger replays it, sending nothing. A conversational
        turn is many bubbles, so the index advances once per bubble handed to a send method -- which
        is exactly what ``api.py:943-951`` loops over."""
        if self._campaign is not None:
            if to_e164 != self._campaign["phone"]:
                raise BridgeError(f"campaign attempt was opened for {self._campaign['phone']!r} but the send "
                                  f"is to {to_e164!r}", status_code=CONTRACT_STATUS)
            return (BI.campaign_key(campaign_id=self._campaign["campaign_id"], phone=self._campaign["phone"],
                                    attempt=self._campaign["attempt"]),
                    {"action": PACING_FIRST_TOUCH, "campaign_id": self._campaign["campaign_id"],
                     "attempt": self._campaign["attempt"]})
        if self._turn is not None:
            if to_e164 != self._turn["phone"]:
                raise BridgeError(f"turn was opened for {self._turn['phone']!r} but the send is to {to_e164!r}",
                                  status_code=CONTRACT_STATUS)
            index = self._turn["next_index"]
            self._turn["next_index"] = index + 1
            return (BI.reply_key(phone=self._turn["phone"], turn_key=self._turn["turn_key"],
                                 action=self._turn["action"], bubble_index=index),
                    {"action": PACING_REPLY, "intent": self._turn["action"],
                     "turn_key": self._turn["turn_key"], "bubble_index": index})
        raise BridgeError(
            "no turn is open: call begin_turn(phone, turn_key, action) or begin_campaign_attempt(...) "
            "before a send. On this rail the client_msg_id is the message's only id, and a key that is "
            "not derived from the turn makes the catch-up timer's re-drive a second delivery to a real "
            "candidate (TASK-217, app/wa/bridge_ids.py).", status_code=CONTRACT_STATUS)

    # --- the wire ---------------------------------------------------------------------------------

    def send_timeout(self, body):
        """-> how long to wait for the answer to ONE bubble, from what that bubble costs the phone.

        A fixed number was wrong here (TASK-146): a 219-character reply is 41-67 s of typing alone
        at the handset's own pace, and the fixed 90 s default expired while the executor was still
        working -- the turn was recorded ``skipped_error`` while the message went on to be
        delivered. ``WA_BRIDGE_TIMEOUT_SEC`` stays the floor (and the whole budget for the calls
        that do not type anything); a long body raises it by what that body takes to type.

        TASK-243: also carries ``FLOCK_WAIT_SEC``, same as every other per-operation budget in
        this file (``CHATS_BUDGET_SEC``, ``THREAD_BUDGET_SEC``, ...) -- this was the one that did
        not, with no comment ever claiming that was on purpose, while
        ``executor.take_phone`` waits the same ``LOCK_TIMEOUT_SEC`` for the flock before a send
        even opens the chat.
        """
        return max(float(self.timeout),
                   FLOCK_WAIT_SEC + EXECUTOR_FIXED_BUDGET_SEC
                   + len(body or "") / EXECUTOR_SLOWEST_CHARS_PER_SEC)

    def _timeout_for(self, budget_sec):
        """-> how long to wait for a route whose worst case is ``budget_sec`` on the handset.

        ``WA_BRIDGE_TIMEOUT_SEC`` stays the FLOOR, exactly as in ``send_timeout``: it is the budget
        for everything that does not touch the phone, and a route that does touch it raises the
        wait to what that route costs. Lowering it here would re-create the bug this exists for --
        a caller giving up while the executor is mid-operation and then reporting a guess.
        """
        return max(float(self.timeout), float(budget_sec))

    def _request(self, method, path, payload=None, timeout=None):
        if not self.base_url or not self.token:
            raise BridgeError("WA_BRIDGE_URL / WA_BRIDGE_TOKEN are not set")
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        budget = self.timeout if timeout is None else timeout
        headers = {"Authorization": "Bearer " + self.token, OP_BUDGET_HEADER: str(budget)}
        if data is not None:
            headers["Content-Type"] = "application/json"
        answer = self.transport(method=method, url=self.base_url + path, headers=headers, data=data,
                                timeout=budget)
        if not (isinstance(answer, tuple) and len(answer) == 2):
            raise BridgeError(f"a bridge transport returns (http_status, body), got {answer!r} -- the HTTP "
                              f"status is load-bearing here (200 sent vs 202 accepted), unlike meta.py's")
        status, body = answer
        # TASK-227: a phone-touching route answers 200 {"op_id", "state": "queued"} now, never the
        # result itself -- see OPS_PATH's own comment. Every caller of _request from here up
        # (_post_message, send_photos, send_gallery, send_document, read_thread, ...) still expects
        # the ORIGINAL synchronous (status, body) shape, so that is
        # what this unwraps back to before returning -- nothing above this method may ever see
        # "queued" on the wire.
        if status == 200 and isinstance(body, dict) and body.get("state") == "queued" and body.get("op_id"):
            return self._await_op(body["op_id"], timeout=budget)
        return status, body

    def _await_op(self, op_id, *, timeout):
        """Poll ``GET /v1/ops/<op_id>`` until it leaves ``queued``/``running`` (TASK-227). -> the
        SAME ``(http_status, body)`` shape the route would have answered before the queue existed:
        ``done`` unwraps to ``(200, result)``; ``failed`` unwraps to the stored refusal envelope at
        its own ``http_status``, exactly as if that refusal had come back on the original call.
        Uses ``self.now``/``self.sleep`` (both already injectable for a test) rather than a bare
        ``time.sleep``, so this stays on the same clock the rest of the class already waits on."""
        deadline = self.now() + timedelta(seconds=timeout)
        while True:
            status, body = self.transport(
                method="GET", url=f"{self.base_url}{OPS_PATH}/{op_id}",
                headers={"Authorization": "Bearer " + self.token}, data=None, timeout=self.timeout)
            if status == 200 and isinstance(body, dict):
                state = body.get("state")
                if state == "done":
                    # TASK-230: NOT `body.get("result") or {}` -- a dispatched kind whose own
                    # result is a legitimately falsy value (reconcile([]) answers [], an empty
                    # list) would have that silently rewritten to {} here, a type change a caller
                    # matching on `isinstance(result, list)` would never expect. Only an ACTUALLY
                    # missing key (None) falls back to {}.
                    result = body.get("result")
                    return 200, ({} if result is None else result)
                if state == "failed":
                    error = body.get("error") or {}
                    return (error.get("error") or {}).get("http_status") or 500, error
            if self.now() >= deadline:
                # TASK-243: giving up here used to have zero effect on op_id -- the dispatcher had
                # no cancel route at all, so a ticket still queued behind lock contention or a
                # deep queue went on to be claimed and typed into a chat nobody was reading the
                # answer to any more. Best-effort and fire-and-forget: op_id may already be
                # RUNNING by now, in which case the executor's own cancel_op refuses to touch it
                # (never interrupts a live adb call), and this call cannot tell which case it is
                # in from here -- either way, the answer_timeout below is what this caller reports.
                self._cancel_op(op_id)
                raise BridgeError(f"op {op_id} did not reach a terminal state within {timeout:.0f}s "
                                  f"of queueing", status_code=UNCERTAIN_STATUS, code=CODE_ANSWER_TIMEOUT)
            self.sleep(OP_POLL_INTERVAL_SEC)

    def _cancel_op(self, op_id):
        """Tell the executor this caller is no longer waiting on ``op_id`` (TASK-243). Never
        raises: a cancel that cannot be delivered (the bridge is unreachable, the op is already
        gone) changes nothing about the answer_timeout ``_await_op`` is about to raise regardless
        -- that is the real, already-decided outcome, and this is a courtesy on top of it."""
        try:
            self._request("POST", f"{OPS_PATH}/{op_id}/cancel")
        except BridgeError:
            pass

    def _await_slot(self):
        """Wait out the rail's own inter-bubble gap before the next bubble of the same turn.

        Not a retry and not a cap we invented (TASK-146): the executor's fuse publishes
        ``quota.next_slot_at`` in the 200 body precisely because "nothing here sleeps" on that side
        -- it refuses and tells the caller when to come back, and this is the caller coming back.
        A Luna reply is routinely two or three bubbles and the handset's own floor is 4 s between
        bubbles to one person, so without this the second bubble of every multi-bubble turn was a
        429, the whole turn raised, and catch-up redelivered it three minutes later with the
        remaining bubbles regenerated by a second brain call.

        Only inside a conversational turn: a campaign attempt is one message, and its next slot is
        minutes away and belongs to the campaign's own scheduler, not to a blocked worker thread.
        """
        if self._turn is None or not self._next_slot_at:
            return
        try:
            due = datetime.fromisoformat(str(self._next_slot_at).replace("Z", "+00:00"))
        except ValueError:
            return
        wait = (due - self.now()).total_seconds()
        if wait > 0:
            self.sleep(wait)

    def _post_message(self, client_msg_id, to_e164, kind, body, trace):
        """One message, one HTTP call. -> ``client_msg_id`` only when the answer is terminal-sent."""
        if not self.base_url or not self.token:
            # A 4xx, unlike the media path's None below: nothing was posted, so this is `failed` with
            # ownership restored, not an open question about a message that may be on its way.
            raise BridgeError("WA_BRIDGE_URL / WA_BRIDGE_TOKEN are not set -- nothing was sent",
                              status_code=CONTRACT_STATUS, client_msg_id=client_msg_id)
        payload = {"client_msg_id": client_msg_id, "to": to_e164, "kind": kind, "body": body, "trace": trace}
        self._await_slot()
        try:
            status, out = self._request("POST", MESSAGES_PATH, payload,
                                        timeout=self.send_timeout(body))
        except BridgeUnreachable as exc:
            # No answer at all -> a 4xx, so campaign.py records `failed` with ownership restored and
            # the lead stays retryable. What makes that safe even if the request did reach the
            # executor is the deterministic key: the retry posts the same client_msg_id and the
            # ledger replays it (TASK-217/TASK-130). A read timeout mid-answer is a different
            # animal and does not arrive here -- it is not a URLError, so it stays uncertain.
            raise BridgeError(f"bridge unreachable on send: {exc}", status_code=UNREACHABLE_SEND_STATUS,
                              code="bridge_unreachable", client_msg_id=client_msg_id) from exc
        if not isinstance(out, dict):
            raise BridgeError(f"bridge answered HTTP {status} with {type(out).__name__}, not an object",
                              status_code=UNCERTAIN_STATUS, payload=out, client_msg_id=client_msg_id)
        if status == 202:
            raise BridgeAccepted(f"bridge accepted {client_msg_id} and has not sent it yet "
                                 f"(state={out.get('state')!r}, reason={out.get('reason')!r})",
                                 status_code=UNCERTAIN_STATUS, payload=out, code="accepted_not_sent",
                                 client_msg_id=client_msg_id)
        if status != 200:
            raise BridgeError(f"bridge answered HTTP {status} to a send, which is neither 200 (sent) nor "
                              f"202 (accepted)", status_code=status, payload=out, client_msg_id=client_msg_id)
        echoed = out.get("client_msg_id")
        if echoed != client_msg_id:
            raise BridgeError(f"bridge answered for client_msg_id {echoed!r}, we sent {client_msg_id!r} -- "
                              f"we do not know what went out", status_code=UNCERTAIN_STATUS, payload=out,
                              client_msg_id=client_msg_id)
        if out.get("ok") is not True or out.get("state") != "sent":
            raise BridgeError(f"bridge answered 200 with ok={out.get('ok')!r} state={out.get('state')!r}, "
                              f"which is not a completed send", status_code=UNCERTAIN_STATUS, payload=out,
                              client_msg_id=client_msg_id)
        tick = (out.get("verified") or {}).get("tick")
        if tick not in VERIFIED_TICKS:
            detail = ("the driver's 'pressed send, never found the bubble' sentinel"
                      if tick == UNVERIFIED_TICK else f"not one of {', '.join(VERIFIED_TICKS)}")
            raise BridgeError(f"bridge reported {client_msg_id} sent with tick {tick!r}: {detail}. No `sent` "
                              f"without a verified delivery tick (decision-8) -- this is uncertain, and it is "
                              f"never auto-resent", status_code=UNCERTAIN_STATUS, payload=out,
                              code="send_unconfirmed", client_msg_id=client_msg_id)
        self.last_send = out
        self._next_slot_at = (out.get("quota") or {}).get("next_slot_at")
        return client_msg_id

    # --- the methods production reaches ------------------------------------------------------------

    def send_text(self, to_e164, body):
        """One bubble. -> the ``client_msg_id``, which is this rail's ``wa_messages.wamid``."""
        client_msg_id, trace = self._next_send(to_e164)
        return self._post_message(client_msg_id, to_e164, "text", body, trace)

    def send_buttons(self, to_e164, body, buttons):
        """Reply buttons do not exist on a phone rail, so the titles are rendered as numbered text.

        There is no UI to compose a tappable button in the consumer app, so nothing can automate
        one -- a platform limit, not a gap. What a candidate CAN do is read three options and type
        one, which is what this sends (TASK-224's rendering half).

        WHAT IS NOT DONE HERE, and is not papered over either: the typed answer is not turned back
        into a button id. ``api._send`` stores the offered set on the outbound row
        (``kind='buttons'``), which is the data TASK-224's recovery needs, and until that matcher
        exists a typed "1" reaches the brain as ordinary text and costs one turn. That is the
        deliberate half to leave out -- an ordinal/title/prefix matcher shipped half-written would
        resolve "1" against the wrong offer, and on the consent pair that is the one unacceptable
        error in this design. ``luna_brain`` still sets consent from a genuine tap and from nothing
        else, so this rendering cannot manufacture a consent nobody gave (TASK-122 is where that is
        answered, with its own switch and a confirmation turn).

        Raising instead was worse than either: ``NotImplementedError`` is not a ``MetaError``, so
        nothing classified it, the turn failed and catch-up re-drove it forever (TASK-146).
        """
        titles = [str((b or {}).get("title") or "").strip() for b in (buttons or [])]
        titles = [t for t in titles if t]
        if not titles:
            raise BridgeError(f"send_buttons to {to_e164} carries no button titles to render",
                              status_code=CONTRACT_STATUS)
        numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(titles, start=1))
        return self.send_text(to_e164, f"{body}\n\n{numbered}")

    def send_photos(self, to_e164, local_paths):
        """Attach up to ``bridge.driver.MAX_PHOTOS_PER_SEND`` local image files to the thread for
        ``to_e164`` (TASK-131 round 7, outbound media; Ivan, 2026-09-22).

        ``local_paths`` names files already sitting on the MINI's own filesystem, not this
        machine's -- there is no upload step in this route, the same way ``media_url``/
        ``download_media`` is a two-step read rather than one that carries bytes over this call.
        An operator (or a caller with filesystem access to the mini) puts the files there first.

        MECHANISM PROOF, NOT PRODUCTION-READY (said plainly here too, matching the executor's own
        docstring): no idempotency key, so calling this twice sends the photos twice; no governor
        pacing check either. Built and tested by hand, on one number, before wiring photos into
        Luna's own automatic sends -- that wiring needs both of those first.

        TASK-247: the timeout is PHOTOS_FLOOR_SEC plus HANDSET_ONE_PHOTO_SEC per file, not the bare
        WA_BRIDGE_TIMEOUT_SEC floor -- that floor is smaller than what even one file costs.

        -> {"ok", "at", "sent": [{"clock", "tick"}, ...]}, one entry per photo, in order.
        """
        phone = BI.require_e164(to_e164)
        if not local_paths:
            raise BridgeError(f"send_photos to {phone} carries no files", status_code=CONTRACT_STATUS)
        budget = PHOTOS_FLOOR_SEC + len(local_paths) * HANDSET_ONE_PHOTO_SEC
        status, body = self._request("POST", PHOTOS_PATH,
                                     {"phone": phone, "local_paths": list(local_paths)},
                                     timeout=self._timeout_for(budget))
        if status != 200:
            raise BridgeError(f"send_photos to {phone}: bridge HTTP {status}", status_code=status,
                              payload=body)
        return body

    def send_gallery(self, to_e164, local_paths, caption=""):
        """Attach up to ``bridge.driver.MAX_PHOTOS_PER_SEND`` local image files to the thread for
        ``to_e164`` as ONE WhatsApp message -- a photo album with a single shared caption -- via
        the mini's gallery-picker automation (TASK-131 round 7 gallery redesign; Ivan, 2026-09-22:
        'галерейкой плюс текстовое сообщение, все это одно сообщение').

        Same two-step-read shape as send_photos: ``local_paths`` names files already on the
        mini's own filesystem, nothing is uploaded over this call.

        MECHANISM PROOF, NOT PRODUCTION-READY (send_photos' own caveat, unchanged here): no
        idempotency key, no governor pacing check. Built and tested by hand, on one number, before
        wiring a gallery send into Luna's own automatic sends.

        TASK-247: the timeout is GALLERY_BUDGET_SEC plus what the caption costs to type, not the
        bare WA_BRIDGE_TIMEOUT_SEC floor -- that floor is smaller than GALLERY_BUDGET_SEC alone,
        with an empty caption, before this ever reaches the picker.

        -> {"ok", "at", "clock", "tick"}.
        """
        phone = BI.require_e164(to_e164)
        if not local_paths:
            raise BridgeError(f"send_gallery to {phone} carries no files", status_code=CONTRACT_STATUS)
        payload = {"phone": phone, "local_paths": list(local_paths)}
        if caption:
            payload["caption"] = caption
        budget = GALLERY_BUDGET_SEC + len(caption or "") / EXECUTOR_SLOWEST_CHARS_PER_SEC
        status, body = self._request("POST", GALLERY_PATH, payload, timeout=self._timeout_for(budget))
        if status != 200:
            raise BridgeError(f"send_gallery to {phone}: bridge HTTP {status}", status_code=status,
                              payload=body)
        return body

    def send_document(self, to_e164, local_path, caption=""):
        """Attach ONE local file, any type, to the thread for ``to_e164`` as WhatsApp's own
        document attachment (TASK-131 round 7; Ivan, 2026-09-23: a future resume-update flow needs
        files, not photos alone).

        Same two-step-read shape as send_photos/send_gallery: ``local_path`` names a file already
        on the mini's own filesystem, nothing is uploaded over this call.

        MECHANISM PROOF, NOT PRODUCTION-READY (send_gallery's own caveat, unchanged here): no
        idempotency key, no governor pacing check.

        TASK-247: the timeout is DOCUMENT_BUDGET_SEC plus what the caption costs to type, not the
        bare WA_BRIDGE_TIMEOUT_SEC floor -- that floor is smaller than DOCUMENT_BUDGET_SEC alone,
        with an empty caption, before this ever reaches the share picker.

        -> {"ok", "at", "clock", "tick"}.
        """
        phone = BI.require_e164(to_e164)
        if not local_path:
            raise BridgeError(f"send_document to {phone} carries no file", status_code=CONTRACT_STATUS)
        payload = {"phone": phone, "local_path": local_path}
        if caption:
            payload["caption"] = caption
        budget = DOCUMENT_BUDGET_SEC + len(caption or "") / EXECUTOR_SLOWEST_CHARS_PER_SEC
        status, body = self._request("POST", DOCUMENT_PATH, payload, timeout=self._timeout_for(budget))
        if status != 200:
            raise BridgeError(f"send_document to {phone}: bridge HTTP {status}", status_code=status,
                              payload=body)
        return body

    def send_template(self, to_e164, template_name=None, language=None, params=None, *, definition=None):
        """A campaign message on this rail is plain text: a consumer number has no Meta template
        registry, so there is nothing to invoke by name and nothing Meta could approve.

        ``definition`` is therefore required, and it is still validated and rendered by
        ``meta.render_template`` -- the validate-then-render discipline ``campaign.py`` depends on
        (a missing or extra variable raises ``TemplateParamsError`` before any POST, exactly as on
        the Meta rail). What the recipient sees is the definition's flat text.

        A definition whose rendering is not pure text is refused rather than flattened: buttons are
        TASK-224, and the lane behind the executor has no media send path at all (its ``whatsapp.py``
        exports ``open_chat, read_thread, send_bubble, go_home, visible_unread`` and nothing else).
        The local first-touch message set that replaces Graph templates here is TASK-124.
        """
        if definition is None:
            raise BridgeError(f"send_template on the phone rail needs definition= (template_name={template_name!r}): "
                              f"a consumer number has no Meta template registry to resolve a name against "
                              f"(TASK-124)", status_code=CONTRACT_STATUS)
        for label, given, defined in (("template_name", template_name, definition.get("name")),
                                      ("language", language, definition.get("language"))):
            if given is not None and given != defined:
                raise BridgeError(f"send_template {label}={given!r} does not match the definition's {defined!r}",
                                  status_code=CONTRACT_STATUS)
        rendered = M.render_template(definition, params)
        header = rendered.get("header") or {}
        unsupported = [name for name, present in
                       (("buttons", rendered.get("buttons")), ("carousel", rendered.get("carousel")),
                        ("media header", header.get("format") not in (None, "TEXT")))
                       if present]
        if unsupported:
            raise BridgeError(f"template {definition.get('name')!r} carries {', '.join(unsupported)}: the phone "
                              f"rail sends text only (buttons are TASK-224, the driver has no media send path)",
                              status_code=CONTRACT_STATUS, payload=definition)
        client_msg_id, trace = self._next_send(to_e164)
        return self._post_message(client_msg_id, to_e164, "text", rendered["text"], trace)

    def get_template(self, template_id, require_approved=False, fields=M.TEMPLATE_FIELDS):
        """There is no Graph lookup on this rail and no local store yet: TASK-124 is the local
        first-touch message set that takes this over. Raising keeps the caller honest -- a stub
        returning a fabricated APPROVED definition would put unreviewed text in front of a
        candidate."""
        raise BridgeError(f"the phone rail cannot look up template {template_id!r}: a consumer number has no "
                          f"Meta template registry, and the local set is TASK-124", status_code=CONTRACT_STATUS)

    def media_url(self, media_id):
        """Step 1 of the same two-step download ``meta.Client`` does: the executor answers with the
        mime type and a URL for its own ``/v1/media/<id>/raw`` route (TASK-131). Unreachable keeps
        ``status_code=None`` so ``import_history.py:538-539`` aborts the run instead of recording the
        document as permanently unrecoverable; a definite refusal (media never pulled) is a 404, so
        ``import_history`` records that one document as not recoverable and keeps going.

        The executor answers with a PATH, not an absolute URL -- it has no way to know which local
        port our own ssh tunnel maps it to, and that mapping must stay free to change without a
        redeploy on its side. An already-absolute answer (a test's fake transport, or a future
        executor that does know its own address) is passed through untouched.
        """
        status, out = self._request("GET", MEDIA_PATH + urllib.parse.quote(str(media_id)))
        if status != 200 or not isinstance(out, dict) or not out.get("url"):
            raise BridgeError(f"bridge media lookup for {media_id!r} returned HTTP {status} with no url",
                              status_code=status if status != 200 else None, payload=out)
        url = out["url"]
        if not urllib.parse.urlsplit(url).scheme:
            url = self.base_url + url
        return {**out, "url": url}

    def download_media(self, url):
        """Step 2: the bytes, through the binary transport, with the same bearer token."""
        if not self.token:
            raise BridgeError("WA_BRIDGE_TOKEN is not set")
        return self.media_transport(method="GET", url=url,
                                    headers={"Authorization": "Bearer " + self.token}, timeout=self.timeout)

    # --- the handset operations (TASK-147) --------------------------------------------------------
    # One method per operation Ivan named, plus what they need around them. Same transport seam, same
    # error taxonomy, same refusal to call anything "done" that the executor did not verify.

    def _require_ok(self, status, out, path):
        """-> the 200 body, or raise. A non-200 keeps the executor's own status (the refusal
        taxonomy in ``bridge/errors.py`` is what tells a caller whether anything happened); a 200
        that is not an ``ok`` object is UNCERTAIN, because we cannot tell what the phone did."""
        if status != 200:
            raise BridgeError(f"bridge answered HTTP {status} to {path}", status_code=status, payload=out)
        if not isinstance(out, dict) or out.get("ok") is not True:
            raise BridgeError(f"bridge answered 200 to {path} with {out!r}, which is not an ok object",
                              status_code=UNCERTAIN_STATUS, payload=out)
        return out

    def _read_target(self, phone, chat, archived):
        """-> ``{"phone": ...}`` or ``{"chat": <title>, "archived": ...}``. Exactly one identity,
        never both, which is the executor's own rule (``bridge/operations.py::_one_address``): two
        identities cannot be checked against one another before the chat is open. ``chat`` is the
        title as the handset draws it -- a display name for a saved contact, the number itself for
        an unsaved one -- and ``phone`` is the address that lets the executor compare WhatsApp's own
        header against the address book."""
        if (phone is None) == (chat is None):
            raise BridgeError("name the chat by exactly one of phone= or chat= (the title)",
                              status_code=CONTRACT_STATUS)
        if phone is not None:
            return {"phone": BI.require_e164(phone)}
        return {"chat": BI.require_text(chat, "chat"), "archived": bool(archived)}

    def health(self):
        """-> the executor's own health body (``GET /v1/health``): rail number, queue, quota, driver,
        watcher heartbeat. The one call that is safe to make at any hour."""
        return self._require_ok(*self._request("GET", HEALTH_PATH), HEALTH_PATH)

    def list_chats(self):
        """-> the chat list as the handset draws it, top row first, each row as the executor read it
        (its title, the unread badge, the date column, whether it is archived, and the E.164 it maps
        to when it maps to one). Read-only: it opens no chat and types nothing. The rows are passed
        through as they come, because the row -- not a field of ours -- is the identity every other
        operation names a chat by."""
        body = self._require_ok(*self._request("GET", CHATS_PATH,
                                               timeout=self._timeout_for(CHATS_BUDGET_SEC)),
                                CHATS_PATH)
        chats = body.get("chats")
        if not isinstance(chats, list):
            raise BridgeError(f"bridge answered {CHATS_PATH} without a chat list (chats={chats!r})",
                              status_code=UNCERTAIN_STATUS, payload=body)
        return chats

    def read_thread(self, *, phone=None, chat=None, archived=False, include_text=True):
        """-> ``{"chat": {...}, "messages": [...], "count": n, ...}`` for one thread: the bubbles
        WhatsApp draws without scrolling, each with direction, clock, delivery tick and body digest.
        Read-only, and the preview a destructive call is supposed to show before it destroys
        anything. ``include_text=False`` asks for everything except the bodies, which is what a
        destructive preview needs (the count is the check; the text is not)."""
        target = {**self._read_target(phone, chat, archived), "include_text": include_text}
        # "1"/"0" rather than Python's True/False: a query string is text, and "False" is a
        # non-empty string on the other side of every naive parser ever written.
        query = urllib.parse.urlencode({k: ("1" if v else "0") if isinstance(v, bool) else v
                                        for k, v in target.items()})
        body = self._require_ok(*self._request("GET", f"{THREAD_PATH}?{query}",
                                               timeout=self._timeout_for(THREAD_BUDGET_SEC)),
                                THREAD_PATH)
        messages = body.get("messages")
        if not isinstance(messages, list):
            raise BridgeError(f"bridge answered {THREAD_PATH} without messages (messages={messages!r})",
                              status_code=UNCERTAIN_STATUS, payload=body)
        return body

    def reconcile(self, client_msg_ids):
        """Ask the executor what actually happened to each of these sends. -> a list of
        {"client_msg_id", "verdict", ...} dicts, in the order asked -- verdict is one of
        confirmed_sent/confirmed_absent/indeterminate/unknown_key (bridge/executor.py::
        Executor.reconcile's own docstring has the exact rules; only confirmed_absent authorises a
        resend). A key already SENT or RESENDABLE in the executor's ledger answers from that row
        alone; anything else costs a live chat scan (TASK-230), so the budget scales with how many
        ids are asked. Not run through ``_require_ok`` -- the dispatched result is the bare verdict
        list itself, never wrapped in an ``{"ok": ...}`` envelope."""
        client_msg_ids = list(client_msg_ids)
        budget = FLOCK_WAIT_SEC + max(1, len(client_msg_ids)) * RECONCILE_PER_ID_SEC
        status, body = self._request("POST", RECONCILE_PATH, {"client_msg_ids": client_msg_ids},
                                     timeout=self._timeout_for(budget))
        if status != 200:
            raise BridgeError(f"bridge answered HTTP {status} to {RECONCILE_PATH}",
                              status_code=status, payload=body)
        if not isinstance(body, list):
            raise BridgeError(f"bridge answered {RECONCILE_PATH} with {body!r}, not a list of verdicts",
                              status_code=UNCERTAIN_STATUS, payload=body)
        return body

    def resolve_op(self, op_id):
        """Mark one failed op resolved (TASK-230) -- the human escape hatch for a failed op that
        minted no client_msg_id to auto-resolve against (read_thread, send_photos/gallery/document
        -- none of these have an outbound ledger row reconcile can read a verdict off). -> {"ok":
        True, "op_id", "resolved": True}. A plain ledger write, never queued -- it does not touch
        the phone."""
        path = f"{OPS_PATH}/{op_id}/resolve"
        return self._require_ok(*self._request("POST", path), path)

    def broadcast_keys(self, run_id, recipients, *, attempt=1):
        """-> ``{phone: client_msg_id}`` for a run, without asking anything of the executor.

        The keys are ``BI.campaign_key(campaign_id=run_id, phone=..., attempt=...)`` -- the same
        minting a campaign attempt uses, because a broadcast is the same thing: a first touch, one
        message per recipient, keyed on values the caller already holds. That is what makes a re-run
        after a crash a replay instead of a second message: the executor's ledger recognises the key
        (first-body-wins, TASK-130) and types nothing. Public because a dry run has to be able to
        print exactly the keys the real run would use.
        """
        BI.require_text(run_id, "run_id")
        BI.require_index(attempt, "attempt", 1)
        keys, seen = {}, {}
        for index, phone in enumerate(recipients):
            to = BI.require_e164(phone)
            if to in seen:
                raise BridgeError(f"broadcast {run_id!r} names {to} twice (items {seen[to]} and {index}) -- "
                                  f"one recipient, one message", status_code=CONTRACT_STATUS)
            seen[to] = index
            keys[to] = BI.campaign_key(campaign_id=run_id, phone=to, attempt=attempt)
        return keys

    def broadcast(self, run_id, items, *, attempt=1, pacing=None, note=None):
        """Queue a broadcast run. -> the run view, with one status per item.

        NOTHING IS SENT BY THIS CALL and the answer says so: the executor writes the run to its
        ledger and its own runner thread works through it, paced by the governor, one item at a
        time, surviving a restart (``bridge/broadcast.py``). So the items come back ``queued`` and
        the run is followed with ``broadcast_status`` -- an HTTP call that blocked until the last
        recipient had been typed would be a socket held open for hours and a run that dies with it.

        One recipient failing does not abort the run, so per-item outcomes are data here, not
        exceptions. Transport-level failure still raises: nothing is reported as queued when the
        executor never answered.
        """
        if not items:
            raise BridgeError("a broadcast with no recipients", status_code=CONTRACT_STATUS)
        keys = self.broadcast_keys(run_id, [(i or {}).get("to") for i in items], attempt=attempt)
        wire = [{"client_msg_id": keys[BI.require_e164(item["to"])], "to": item["to"], "kind": "text",
                 "body": BI.require_text(item.get("body"), f"item {index} body"),
                 "action": PACING_FIRST_TOUCH}
                for index, item in enumerate(items)]
        payload = {"run_id": run_id, "items": wire, "pacing": dict(pacing or {})}
        if note is not None:
            payload["note"] = note
        body = self._require_ok(*self._request("POST", BROADCASTS_PATH, payload), BROADCASTS_PATH)
        return self._run_view(body, set(keys.values()))

    def broadcast_status(self, run_id):
        """-> the run as the executor's ledger holds it now: per-item status, code and detail. This
        is how a run is followed, and how one that outlived the terminal that started it is picked
        back up instead of started again."""
        path = f"{BROADCASTS_PATH}/{urllib.parse.quote(BI.require_text(run_id, 'run_id'), safe='')}"
        return self._run_view(self._require_ok(*self._request("GET", path), path), None)

    def broadcast_stop(self, run_id):
        """The hard stop. -> the run view. It takes effect between items on the executor's side: a
        stop never interrupts a bubble mid-type, because a half-typed message is the one state this
        rail refuses to create."""
        path = (f"{BROADCASTS_PATH}/{urllib.parse.quote(BI.require_text(run_id, 'run_id'), safe='')}/stop")
        return self._run_view(self._require_ok(*self._request("POST", path, {}), path), None)

    def broadcast_runs(self):
        """-> every run the executor holds, with its per-status counts."""
        body = self._require_ok(*self._request("GET", BROADCASTS_PATH), BROADCASTS_PATH)
        runs = body.get("runs")
        if not isinstance(runs, list):
            raise BridgeError(f"bridge answered {BROADCASTS_PATH} without runs (runs={runs!r})",
                              status_code=UNCERTAIN_STATUS, payload=body)
        return runs

    def _run_view(self, body, posted_keys):
        """-> the run view, passed through, with one addition: a key we posted that the view does
        not mention comes back as an item in state ``no_answer``. Not "failed" and not "sent" -- we
        asked for a message to be queued and the run does not know about it, which is a state to
        look at rather than to average away."""
        items = body.get("items")
        if not isinstance(items, list):
            raise BridgeError(f"bridge answered a broadcast view without items (items={items!r})",
                              status_code=UNCERTAIN_STATUS, payload=body)
        for item in items:
            if not isinstance(item, dict) or not item.get("client_msg_id"):
                raise BridgeError(f"a broadcast item has no client_msg_id: {item!r}",
                                  status_code=UNCERTAIN_STATUS, payload=body)
        missing = (posted_keys or set()) - {i["client_msg_id"] for i in items}
        return {**body, "items": items + [{"client_msg_id": key, "status": BROADCAST_NO_ANSWER,
                                           "code": None, "detail": "the run view does not mention this key"}
                                          for key in sorted(missing)]}

    # --- the destruction record (what a lost answer is asked about) -------------------------------

    def audit(self, *, limit=None):
        """-> every destruction this executor has recorded, newest first (``GET /v1/audit``).

        The rows are the executor's write-ahead record: one is appended BEFORE the destructive verb
        and finished after the verification, so a row is evidence that the taps were about to
        happen and its ``verified`` flag is evidence of how they ended. Read-only, touches no phone,
        and it is the only way to answer "did that delete go through" after the answer was lost.
        """
        path = AUDIT_PATH + (f"?limit={int(limit)}" if limit is not None else "")
        body = self._require_ok(*self._request("GET", path), AUDIT_PATH)
        rows = body.get("rows")
        if not isinstance(rows, list):
            raise BridgeError(f"bridge answered {AUDIT_PATH} without rows (rows={rows!r})",
                              status_code=UNCERTAIN_STATUS, payload=body)
        return rows

    # --- the sends a reconcile still has to answer for (TASK-261) ---------------------------------
    def unresolved_sends(self):
        """-> every send still ATTEMPTING/UNCONFIRMED, oldest first (``GET /v1/unresolved``): the
        client_msg_id, thread_tag, state and age a reconcile needs -- the same ids ``reconcile()``
        above takes. Read-only, touches no phone: TASK-235's ``UnresolvedSendWatcher`` already
        reconciles these on its own schedule, this only lists them."""
        body = self._require_ok(*self._request("GET", UNRESOLVED_PATH), UNRESOLVED_PATH)
        rows = body.get("rows")
        if not isinstance(rows, list):
            raise BridgeError(f"bridge answered {UNRESOLVED_PATH} without rows (rows={rows!r})",
                              status_code=UNCERTAIN_STATUS, payload=body)
        return rows

    # --- the queue and the human escape hatch (TASK-131 round 6, Ivan's ruling 2026-09-22): most
    # pulled files never reach this queue at all any more -- bridge/identity.py::decide attributes
    # them automatically, strong or weak (bridge/executor.py::Executor.auto_match_media). What is
    # LEFT here is whatever has zero same-kind candidates yet, the genuine fallback. -------------------
    def unresolved_media(self):
        """-> every pulled file automatic matching has not been able to place yet, oldest first
        (``GET /v1/media``): kind, size, age, the handset folder it came from, and which threads
        plausibly relate to it in that period -- no filename, no phone. The listing exists so an
        operator can decide, from facts alone, which id in ``attach_media`` below is the one they
        mean."""
        body = self._require_ok(*self._request("GET", MEDIA_LIST_PATH), MEDIA_LIST_PATH)
        files = body.get("files")
        if not isinstance(files, list):
            raise BridgeError(f"bridge answered {MEDIA_LIST_PATH} without files (files={files!r})",
                              status_code=UNCERTAIN_STATUS, payload=body)
        return files

    def attach_media(self, queue_id, phone):
        """Attach one queued file to one phone's own thread, by hand (``POST /v1/media/attach``).
        -> the executor's own report. The fallback for whatever automatic matching could not place
        at all (no same-kind candidate yet): the operator already knows whose file this is from
        something off this machine, and naming the phone is how that knowledge reaches the ledger,
        through ``bridge/ledger.py::link_media`` -- the same call an automatic match makes."""
        payload = {"queue_id": BI.require_text(queue_id, "queue_id"),
                  "phone": BI.require_e164(phone)}
        return self._require_ok(*self._request("POST", MEDIA_ATTACH_PATH, payload), MEDIA_ATTACH_PATH)

