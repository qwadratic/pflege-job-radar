"""The phone rail's outbound client (TASK-120): same duck shape as ``meta.Client``, different proof.

Architecture (decision-8, ``/home/claude/plans/2026-09-21-macmini-revision.md`` §4): our own
executor on the remote Ubuntu box imports the colleague's ``device.py`` / ``whatsapp.py`` /
``inbox.py`` as a driver library. Luna stays the brain here, ``data/wa.sqlite`` stays the source of
truth, and this module is the only thing in ``app/wa`` that knows the rail exists. Everything it
speaks is the HTTP contract in ``docs/whatsapp.md`` ("Transports"), so the executor behind it is
swappable without touching a caller.

WHAT IS DIFFERENT FROM THE META RAIL, and why it is in code rather than in prose:

* **No provider message id, ever.** The lane behind the executor has no message identifier of any
  kind, so the ``client_msg_id`` we mint (``app/wa/bridge_ids.py``, TASK-114) is the only id an
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
from datetime import datetime, timezone

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
# model or an operator calls in one step. These five routes are the executor's half of that contract
# (``bridge/server.py``); they are constants because the two halves are one agreement, so a rename on
# that side is one line here. ``tools/wa_bridge.py`` is the operator's front door to them.
CHATS_PATH = "/v1/chats"
THREAD_PATH = "/v1/thread"
BROADCASTS_PATH = "/v1/broadcasts"
CHAT_CLEAR_PATH = "/v1/chats/clear"
CHAT_DELETE_PATH = "/v1/chats/delete"
AUDIT_PATH = "/v1/audit"
# TASK-131 round 4: the human escape hatch -- an unresolved file's own listing, and attaching one
# to a phone by hand, through the exact path an automatic link takes.
MEDIA_LIST_PATH = "/v1/media"
MEDIA_ATTACH_PATH = "/v1/media/attach"
PHOTOS_PATH = "/v1/photos"
GALLERY_PATH = "/v1/gallery"
DOCUMENT_PATH = "/v1/document"

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

# --- what the OTHER routes cost the handset (the 2026-09-21 delete) -------------------------------
# A destructive call is not a send and must not inherit a send's budget. On 2026-09-21 Ivan deleted
# three chats from the handset; the executor's own log times the three POSTs at 110 s, 98 s and
# 91 s, and the third expired against WA_BRIDGE_TIMEOUT_SEC=90 one second before the executor
# answered 200. The operator read the timeout as "nothing happened", pressed again, got "no chat
# with that title", and concluded the tool had matched the wrong chat. It had matched the right one
# and destroyed it. So these budgets are derived per operation, term by term.
#
# THE UNIT IS A CHAT-LIST PASS, and it is measured rather than guessed: GET /v1/chats (which is
# bridge/operations.py::list_chats -> driver.list_chats(include_archived=True): the main list and
# the archive, each scrolled to its own end) answered in 32.5 s and 29.8 s back to back on that
# handset on 2026-09-21 with 7 conversations on it.
HANDSET_CHAT_LIST_PASS_SEC = 33
# bridge/operations.py::_destroy walks the list FOUR times inside ONE flock acquisition:
#   _match_row         which row is this, and is it the only one carrying that title
#   _preview -> _open  open_chat_row -> _locate scrolls the list to find the row to tap
#   the verb           delete_chat_row / clear_chat_history -> _select_row -> _locate, again
#   the verification   _verify_deleted / _verify_cleared rescan the list off the handset
DESTROY_LIST_PASSES = 4
# The waits between those passes, each one a ceiling the driver names itself (bridge/adb_driver.py):
#   open the chat        12 s wait_for(header) + PAUSE_AFTER_OPEN up to 4 s
#   long press           LONG_PRESS_HOLD 1.2 s + 0.8 s settle + 8 s wait_for(the selection bar)
#   menu and dialog      12 s wait_for(the sheet / the alert) + UI_SETTLE 1.5 s, twice for the
#                        clear_chat scope sheet
#   leave the selection  up to 3 dumps
#   park                 up to 4 rounds of focus/back/relaunch at 1.2 s
DESTROY_TAPS_SEC = 60
# executor.take_phone waits the other lane out before it refuses: bridge/driver.py LOCK_TIMEOUT_SEC
# is 30 s, and a destructive call can spend all of it before its first pass.
FLOCK_WAIT_SEC = 30
# 30 + 4*33 + 60 = 222 s: twice the slowest POST that handset has actually served, with every term
# named. It is a BUDGET, not a cap on the phone -- the executor stops itself; this is only how long
# this side is willing to keep listening for the answer.
DESTROY_BUDGET_SEC = FLOCK_WAIT_SEC + DESTROY_LIST_PASSES * HANDSET_CHAT_LIST_PASS_SEC + DESTROY_TAPS_SEC
# GET /v1/chats: the flock, one pass, and park. GET /v1/thread: the flock, one pass to find the row
# (_locate), 12 s + 4 s to open it, a dump to read the bubbles, and park. Both land under the 90 s
# floor below, so neither changes today -- they are written down so that staying under it is a fact
# somebody can check rather than a coincidence nobody noticed.
CHATS_BUDGET_SEC = FLOCK_WAIT_SEC + HANDSET_CHAT_LIST_PASS_SEC + 5
THREAD_BUDGET_SEC = FLOCK_WAIT_SEC + HANDSET_CHAT_LIST_PASS_SEC + 16 + 5 + 5

# --- the codes this client mints when the ANSWER is lost ------------------------------------------
# Not the executor's taxonomy (bridge/errors.py): these say what we could establish about a
# destruction whose answer never arrived, by reading the audit row the executor writes BEFORE the
# destructive verb. The CLI prints them as "the answer was lost", never as "the bridge refused".
CODE_ANSWER_TIMEOUT = "answer_timeout"
# The executor's own slug for "no chat by that identity is on the handset's list"
# (bridge/errors.py). Named here because this client branches on it.
CHAT_NOT_FOUND = "chat_not_found"
CODE_DESTROY_NOT_STARTED = "destroy_not_started"
CODE_DESTROY_UNVERIFIED = "destroy_unverified"
CODE_DESTROY_OUTCOME_UNKNOWN = "destroy_outcome_unknown"
CODE_CHAT_ALREADY_DELETED = "chat_already_deleted"
LOST_ANSWER_CODES = (CODE_DESTROY_NOT_STARTED, CODE_DESTROY_UNVERIFIED,
                     CODE_DESTROY_OUTCOME_UNKNOWN, CODE_CHAT_ALREADY_DELETED)
# The executor's OWN two 504s (bridge/errors.py), the ones whose message says the handset was
# touched: a destruction that was tapped and could not be proved, and keys pressed with no tick
# read. They are named here because "the bridge refused" must never be printed over either -- the
# executor refused nothing, it did the thing and cannot prove how it ended, which is the 2026-09-21
# sentence in the executor's vocabulary instead of ours.
CODE_DESTRUCTION_UNVERIFIED = "destruction_unverified"
CODE_SEND_UNCONFIRMED = "send_unconfirmed"
HANDSET_TOUCHED_CODES = (CODE_DESTRUCTION_UNVERIFIED, CODE_SEND_UNCONFIRMED)
# The executor's verdict on whether the record's row is about the number the caller named
# (bridge/operations.py::_not_found). It travels as a word because neither number may leave that
# machine, and the comparison is the difference between "we already deleted your chat" and "we
# deleted somebody else's chat that wore the same name".
NUMBER_SAME = "same"
NUMBER_DIFFERENT = "different"
NUMBER_UNRECORDED = "unrecorded"
# Where a report came from when it did not come from the answer to the call that made it.
FROM_AUDIT_AFTER_LOSS = "audit_after_lost_answer"
FROM_AUDIT_ALREADY_GONE = "audit_already_destroyed"


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


def _moment(value):
    """-> an aware datetime, from the ledger's RFC3339 spelling (``...Z``) or from one we hold.

    Raises on anything else rather than treating an unreadable timestamp as "long ago": these
    comparisons decide whether an audit row belongs to the call we are asking about.
    """
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise BridgeError(f"{value!r} is where a timestamp belongs", status_code=UNCERTAIN_STATUS)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stamp(moment):
    """The ledger's own spelling of a moment (``bridge/ledger.py::utc``), so a sentence we print can
    be compared with an audit row character for character."""
    return _moment(moment).astimezone(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


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
    once TASK-118 lands -- every existing FakeMeta without the attribute keeps Meta semantics.

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

    # --- which turn is being sent (TASK-114) ------------------------------------------------------

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
            "candidate (TASK-114, app/wa/bridge_ids.py).", status_code=CONTRACT_STATUS)

    # --- the wire ---------------------------------------------------------------------------------

    def send_timeout(self, body):
        """-> how long to wait for the answer to ONE bubble, from what that bubble costs the phone.

        A fixed number was wrong here (TASK-146): a 219-character reply is 41-67 s of typing alone
        at the handset's own pace, and the fixed 90 s default expired while the executor was still
        working -- the turn was recorded ``skipped_error`` while the message went on to be
        delivered. ``WA_BRIDGE_TIMEOUT_SEC`` stays the floor (and the whole budget for the calls
        that do not type anything); a long body raises it by what that body takes to type.
        """
        return max(float(self.timeout),
                   EXECUTOR_FIXED_BUDGET_SEC + len(body or "") / EXECUTOR_SLOWEST_CHARS_PER_SEC)

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
        headers = {"Authorization": "Bearer " + self.token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        answer = self.transport(method=method, url=self.base_url + path, headers=headers, data=data,
                                timeout=self.timeout if timeout is None else timeout)
        if not (isinstance(answer, tuple) and len(answer) == 2):
            raise BridgeError(f"a bridge transport returns (http_status, body), got {answer!r} -- the HTTP "
                              f"status is load-bearing here (200 sent vs 202 accepted), unlike meta.py's")
        return answer

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
            # ledger replays it (TASK-114/TASK-130). A read timeout mid-answer is a different
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
        one, which is what this sends (TASK-121's rendering half).

        WHAT IS NOT DONE HERE, and is not papered over either: the typed answer is not turned back
        into a button id. ``api._send`` stores the offered set on the outbound row
        (``kind='buttons'``), which is the data TASK-121's recovery needs, and until that matcher
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

        -> {"ok", "at", "sent": [{"clock", "tick"}, ...]}, one entry per photo, in order.
        """
        phone = BI.require_e164(to_e164)
        if not local_paths:
            raise BridgeError(f"send_photos to {phone} carries no files", status_code=CONTRACT_STATUS)
        status, body = self._request("POST", PHOTOS_PATH,
                                     {"phone": phone, "local_paths": list(local_paths)})
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

        -> {"ok", "at", "clock", "tick"}.
        """
        phone = BI.require_e164(to_e164)
        if not local_paths:
            raise BridgeError(f"send_gallery to {phone} carries no files", status_code=CONTRACT_STATUS)
        payload = {"phone": phone, "local_paths": list(local_paths)}
        if caption:
            payload["caption"] = caption
        status, body = self._request("POST", GALLERY_PATH, payload)
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

        -> {"ok", "at", "clock", "tick"}.
        """
        phone = BI.require_e164(to_e164)
        if not local_path:
            raise BridgeError(f"send_document to {phone} carries no file", status_code=CONTRACT_STATUS)
        payload = {"phone": phone, "local_path": local_path}
        if caption:
            payload["caption"] = caption
        status, body = self._request("POST", DOCUMENT_PATH, payload)
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
        TASK-121, and the lane behind the executor has no media send path at all (its ``whatsapp.py``
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
                              f"rail sends text only (buttons are TASK-121, the driver has no media send path)",
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

    def clear_chat(self, *, chat, phone=None, archived=False, expect_messages=None, confirm=False,
                   include_starred=True):
        """Empty one chat, keep the chat. -> the executor's report (what it destroyed, and its
        proof). ``include_starred=False`` leaves starred messages where they are."""
        return self._destroy(CHAT_CLEAR_PATH, "clear_chat", chat=chat, phone=phone, archived=archived,
                             expect_messages=expect_messages, confirm=confirm,
                             extra={"include_starred": bool(include_starred)})

    def delete_chat(self, *, chat, phone=None, archived=False, expect_messages=None, confirm=False):
        """Remove one chat entirely. -> the executor's report (what it destroyed, and its proof)."""
        return self._destroy(CHAT_DELETE_PATH, "delete_chat", chat=chat, phone=phone, archived=archived,
                             expect_messages=expect_messages, confirm=confirm)

    def _destroy(self, path, operation, *, chat, phone, archived, expect_messages, confirm, extra=None):
        """The shared shape of the two destructive operations, and the refusals that make them safe
        to hand to a model:

        * ``confirm=True`` is required HERE, before any POST -- a destructive call that reads as a
          read is the one API shape that gets a chat deleted by autocomplete. The executor demands
          it again on its side; both checks are cheap and neither is the other's excuse.
        * ``chat`` (the title as the handset draws it) is the identity, and it is required: the
          executor matches the row, refuses when two rows carry the name, and refuses when the
          handset's address book ties that name to a different number than ``phone``.
        * ``expect_messages`` is the caller saying what it saw. A conversation that grew a bubble
          between the read and the confirm is not the conversation that was approved, and the
          executor refuses it.
        * the 200 must be FOR this operation and must carry ``verification.verified``. The executor
          verifying its own work is the only proof a destruction happened -- a 200 without it is
          UNCERTAIN, exactly like a send without a tick. (The executor itself raises 504
          ``destruction_unverified`` in that case; this check is what stops a differently-shaped
          answer from reading as success.)

        ``BridgeUnreachable`` is deliberately NOT translated into a 4xx here (unlike a send, where
        the deterministic key makes a retry safe): if the answer to a delete is lost we do not know
        whether the chat is gone, and the honest next step is to press the executor's own record,
        not the button again.

        A LOST ANSWER IS A QUESTION WITH AN ANSWER, and asking it is this method's job rather than
        the caller's. The executor writes its audit row BEFORE the destructive verb
        (``bridge/operations.py``, ``bridge/ledger.py::append_audit``), so when the transport gives
        up -- or the executor answers 404 because the row is no longer on the list -- the record
        says which of four states we are in: destroyed and verified, destroyed and unproved, not
        started, or unreadable. ``_destroy_outcome`` turns those into an answer and never into a
        guess.
        """
        if confirm is not True:
            raise BridgeError(f"{path} needs confirm=True: a destructive operation is never the default",
                              status_code=CONTRACT_STATUS)
        title = BI.require_text(chat, "chat")
        payload = {"chat": title, "archived": bool(archived), "confirm": True, **(extra or {})}
        if phone is not None:
            payload["phone"] = BI.require_e164(phone)
        if expect_messages is not None:
            payload["expect_messages"] = BI.require_index(expect_messages, "expect_messages", 0)
        asked_at = self.now()
        try:
            body = self._require_ok(*self._request("POST", path, payload,
                                                   timeout=self._timeout_for(DESTROY_BUDGET_SEC)),
                                    path)
        except BridgeUnreachable as exc:
            # The connection died. It may have died after the executor took the phone, so "nothing
            # happened" is not ours to assume -- the audit says.
            return self._destroy_outcome(operation, title, asked_at, exc)
        except BridgeError as exc:
            if exc.code == CODE_ANSWER_TIMEOUT:
                return self._destroy_outcome(operation, title, asked_at, exc)
            if exc.code == CHAT_NOT_FOUND:
                # "No chat with that title" is two different facts wearing one sentence. The
                # executor's refusal carries which one this is (bridge/operations.py::_match_row).
                return self._already_destroyed(operation, title, payload.get("phone"),
                                               payload["archived"], exc)
            raise
        verification = body.get("verification")
        if body.get("operation") != operation or not isinstance(verification, dict) \
                or verification.get("verified") is not True:
            raise BridgeError(f"bridge answered 200 to {path} with operation={body.get('operation')!r} and "
                              f"verification={verification!r} -- the destruction is unconfirmed",
                              status_code=UNCERTAIN_STATUS, payload=body, code="destroy_unconfirmed")
        return body

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

    def destructions_of(self, *, chat=None, phone=None, operation=None, since=None):
        """-> ``(tied, undecidable)``: the audit rows the record ties to one conversation, newest
        first, and the rows it can neither tie to it nor rule out.

        Identity is the title the handset draws OR the number the executor resolved the row to --
        both, because an operator names a chat either way and the record has to be findable by what
        they typed. ``since`` is compared as a moment, not as text: the ledger writes milliseconds
        and ``datetime.now()`` carries microseconds, so ``>=`` on the strings would sort
        ``...912345Z`` before ``...912Z``.

        A ROW WITH NO NUMBER IS NOT A ROW ABOUT SOMEBODY ELSE. ``audit.to_phone`` is null whenever
        the handset could not resolve the chat to one, and the live ledger's first row is exactly
        that: "Valentyn NDT", a verified delete of five messages, ``to_phone`` null. Dropping such
        a row from a ``phone=`` lookup made this rail answer "the audit records no destruction of
        it either" about a chat it had destroyed and proved destroyed -- Ivan's sentence, restored.
        So they come back in the second list, as the open question they are, and the caller reports
        them instead of deciding them. ``undecidable`` is empty whenever no number was named:
        nothing can fail a comparison that was not asked for.
        """
        if chat is None and phone is None:
            raise BridgeError("name the chat to look up by chat= (the title) or phone=",
                              status_code=CONTRACT_STATUS)
        floor = _moment(since) if since is not None else None
        tied, undecidable = [], []
        for row in self.audit():
            if chat is not None and row.get("chat_title") != chat:
                continue
            if operation is not None and row.get("operation") != operation:
                continue
            if floor is not None and _moment(row.get("at")) < floor:
                continue
            if phone is not None and row.get("to_phone") != phone:
                if row.get("to_phone") is None:
                    undecidable.append(row)
                continue
            tied.append(row)
        return tied, undecidable

    def _report_from_audit(self, row, *, reported_by, note):
        """-> the same shape ``delete_chat``/``clear_chat`` return on a 200, rebuilt from the audit
        row, plus where it came from. ``verification`` is the executor's own proof object when the
        verification ran; when it did not, the row's ``verified`` column is reported as what it is
        rather than dressed up as a proof."""
        detail = row.get("detail") or {}
        proof = detail.get("proof")
        return {"ok": True, "operation": row.get("operation"), "at": row.get("at"),
                "chat": {"title": row.get("chat_title"), "phone": row.get("to_phone")},
                "destroyed": detail.get("destroyed"), "ui": detail.get("ui"),
                "verification": proof if isinstance(proof, dict) else
                                {"verified": bool(row.get("verified")), "method": "audit_row",
                                 "why": f"audit state {detail.get('state')!r}"},
                "audit_id": row.get("id"), "reported_by": reported_by, "note": note}

    def _destroy_outcome(self, operation, title, asked_at, lost):
        """The answer to a destructive call never arrived. -> what the executor's record says, or a
        refusal naming exactly which question stayed open. Never "may still be sending" and stop.

        Four states, and they are the four the write-ahead row can be in:
          * a verified row -> the destruction HAPPENED and is proved. That is a success and is
            returned as one: the caller asked for this and got it, and an error here is the lie
            that cost an hour on 2026-09-21.
          * an unfinished or unproved row -> the verb started and its result is not proved. 504,
            same rule as the executor's own ``destruction_unverified``.
          * no row at all -> the verb had not begun when we looked. Said with the moment we looked,
            because the executor may still hold the phone and the row can appear a minute later.
          * the audit itself unreadable -> we cannot tell, and we say so with the command to run.
        """
        reason = str(lost)
        try:
            # By title, not by number: this call's own write-ahead row is the one being looked for
            # and ``since`` already pins it to the seconds we were waiting. Filtering by number
            # here would lose the row whose number the handset never resolved -- the live ledger
            # holds one -- which is the very report this method exists to make. Nothing lands in
            # the second list when no number is named.
            rows, _ = self.destructions_of(chat=title, operation=operation, since=asked_at)
        except BridgeError as unreadable:
            raise BridgeError(
                f"the bridge answer was lost ({reason}) and the audit could not be read either "
                f"({unreadable}), so whether {title!r} was destroyed is NOT KNOWN from here. "
                f"Check with: tools/wa_bridge.py audit --title {title!r}",
                status_code=UNCERTAIN_STATUS, code=CODE_DESTROY_OUTCOME_UNKNOWN) from lost
        if not rows:
            raise BridgeError(
                f"the bridge answer was lost ({reason}) and the executor's audit records no "
                f"{operation} of {title!r} since {_stamp(asked_at)}: nothing had been destroyed as "
                f"of {_stamp(self.now())}. The audit row is written before the first tap, so if "
                f"the executor is still working one will appear -- check with: "
                f"tools/wa_bridge.py audit --title {title!r}",
                status_code=UNCERTAIN_STATUS, code=CODE_DESTROY_NOT_STARTED) from lost
        row = rows[0]
        if not row.get("verified"):
            state = (row.get("detail") or {}).get("state")
            raise BridgeError(
                f"the bridge answer was lost ({reason}) and the audit says this {operation} of "
                f"{title!r} DID start at {row.get('at')} (audit {row.get('id')}, state "
                f"{state!r}): the handset was tapped and the result is not proved. Read the list "
                f"with: tools/wa_bridge.py chats",
                status_code=UNCERTAIN_STATUS, code=CODE_DESTROY_UNVERIFIED,
                payload={"audit": row}) from lost
        return self._report_from_audit(
            row, reported_by=FROM_AUDIT_AFTER_LOSS,
            note=f"the bridge answer was lost ({reason}), and the executor's audit says this "
                 f"{operation} finished and was verified at {row.get('at')} (audit "
                 f"{row.get('id')}). The destruction below is the record, not the answer")

    def _already_destroyed(self, operation, title, phone, archived, refusal):
        """The executor answered 404: that title is not on the handset's list. -> a success report
        when the reason is that WE removed the caller's conversation, or a refusal naming what
        cannot be established.

        The evidence travels in the refusal itself (``bridge/operations.py::_match_row`` puts it
        there), so this costs no second call. A ``delete_chat`` whose target is already gone BY OUR
        OWN HAND is the outcome the caller asked for and is reported as done. A ``clear_chat`` is
        not: it promises to empty a conversation and keep it, and there is no conversation left --
        that stays an error, with the reason named.

        BY OUR OWN HAND, AND ABOUT THIS CONVERSATION. A title is not an identity: two contacts can
        wear one display name, which is a refusal on every other path in this package
        (``_match_row``, ``bridge/operations.py``). A row found by title alone therefore answers
        about the caller's chat only when the executor says the numbers are the same word for word
        -- and when they are not, or when the row never recorded one, this says so instead of
        deciding. The comparison happens on the executor because neither number may travel.
        """
        detail = ((refusal.payload or {}).get("error") or {}).get("detail") or {}
        record = detail.get("destroyed_by_us")
        if not isinstance(record, dict):
            raise refusal
        when, audit_id = record.get("at"), record.get("audit_id")
        named_number = record.get("named_number")
        if phone is not None and named_number != NUMBER_SAME:
            why = {NUMBER_DIFFERENT: f"the executor says that row is about a different number than "
                                     f"{phone}, and two contacts can share one display name",
                   NUMBER_UNRECORDED: "the handset never resolved that row to a number, so the "
                                      "record cannot say whose conversation it was"}.get(
                named_number, f"the executor said nothing about whose number that row is "
                              f"(named_number={named_number!r})")
            raise BridgeError(
                f"no chat titled {title!r} is on the handset's list, and this rail's record cannot "
                f"be tied to {phone}: it holds a {record.get('operation')} of that title at {when} "
                f"(audit {audit_id}) and {why}. Whether {phone}'s conversation was destroyed is "
                f"NOT KNOWN from here. Check with: tools/wa_bridge.py audit --title {title!r}",
                status_code=UNCERTAIN_STATUS, code=CODE_DESTROY_OUTCOME_UNKNOWN,
                payload=refusal.payload) from refusal
        if operation != "delete_chat":
            raise BridgeError(
                f"{title!r} is not on the handset because this rail deleted it at {when} (audit "
                f"{audit_id}): {operation} empties a conversation and keeps it, and there is no "
                f"conversation left to empty. Nothing was touched",
                status_code=refusal.status_code, code=CODE_CHAT_ALREADY_DELETED,
                payload=refusal.payload) from refusal
        report = self._report_from_audit(
            {"id": audit_id, "at": when, "operation": record.get("operation"), "chat_title": title,
             # Never invented from the caller's argument: the executor omits the number from this
             # detail on purpose, and filling it in here would make a report about one conversation
             # read as a report about another.
             "to_phone": record.get("to_phone"), "verified": record.get("verified"),
             "detail": record.get("detail") or {}},
            reported_by=FROM_AUDIT_ALREADY_GONE,
            note=f"{title!r} was already deleted by this rail at {when} (audit {audit_id}, "
                 f"verified={record.get('verified')}) and is not on the handset's list now. "
                 f"Nothing was touched by this call"
                 + (". The audit records no folder, so that row cannot confirm the chat it is "
                    "about was the archived one named" if archived else ""))
        # The proof is THIS call's own scan, not the old row's: the executor walked the whole list
        # -- both folders -- looking for that title and found no row, which is the same evidence
        # ``_verify_deleted`` accepts. What that proves is the ABSENCE, now. Whether the recorded
        # destruction itself was ever proved is a different question with its own answer in the
        # row, and it is carried here rather than overwritten -- an unproved row dressed up as a
        # proof is the tool asserting an outcome it did not see.
        report["verification"] = {
            "verified": True, "method": "chat_list_rescan", "chat_present": False,
            "proves": "the chat is absent from both folders now, not that the recorded destruction "
                      "was verified when it ran",
            "audit_row": {"verified": bool(record.get("verified")),
                          "state": (record.get("detail") or {}).get("state")},
            "why": "the list the executor scanned for this call carries no row with that title"}
        return report
