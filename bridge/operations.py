"""The handset operations as callable code, not as a script somebody rewrites (TASK-376).

WHY THIS MODULE EXISTS. Every time the handset had to be listed, read or tidied up, somebody wrote
a fresh UI-automation script, ran it once and threw it away. That is a fine way to do a thing once
and a terrible way to do it forever -- a script written in the moment has no identity check and no
record. These operations are the same work, written once, with the discipline attached to the
operation instead of to whoever is driving it.

    list_chats     read-only: the chat list as the handset draws it
    read_thread    read-only: the visible bubbles of one chat
    send_message   one bubble, through the executor, with every guarantee it already had
                   (send_broadcast is a fourth and lives in bridge/broadcast.py: it needs a
                    ledger-backed run and a runner thread, which is a module's worth of its own.)

clear_chat/delete_chat used to live here too (TASK-376's original four). Ivan's ruling, TASK-289,
2026-09-24: a phone-side chat delete let the real WhatsApp screen and the DB drift apart, twice in
one night, through a mechanism this module's own audit trail could not fully explain. This rail
never deletes conversation state again, in either direction -- so the capability, not just its use,
is gone: the two operations, their destructive-preview/confirm/verify machinery, and the audit
WRITE path (bridge/ledger.py::append_audit/finish_audit) were removed entirely once both live test
threads had their corrupted phone-side chats cleared through the tool one last time. The audit
READ side (``ledger.audit_rows``, ``GET /v1/audit``, ``tools/wa_bridge.py audit``) stays: the six
destructions already on record are history, not a live capability, and remain readable.

PII: a chat title is a contact's display name and it is load-bearing here -- it is the identity
``read_thread``'s callers name. Message bodies are not: ``list_chats`` reports that a preview line
exists and never what it says.
"""
from __future__ import annotations

from contextlib import ExitStack

from . import driver as D
from . import errors as E
from . import inbound as I
from . import ledger as L

#: What ``read_thread`` can see. WhatsApp draws the bottom of a conversation and this code does not
#: scroll, so every count here is the VISIBLE count and says so in its own field name. A scrolled
#: count would be a different, slower, and much more fragile promise, and inventing it silently
#: would make "12 messages" mean two different things.
VISIBILITY = "visible_without_scrolling"


class Operations:
    """The operations, bound to one executor (which owns the ledger, the governor and the driver)."""

    def __init__(self, executor):
        self.executor = executor
        self.driver = executor.driver
        self.ledger = executor.ledger
        self.clock = executor.clock

    # --- read-only ---------------------------------------------------------------------------
    def list_chats(self, *, include_archived=True):
        """-> the chat list, with the identity needed to act on a row safely.

        ``phone`` is what the handset itself can say about a row, and ``phone_source`` says how it
        knows: the title IS the number for an unsaved contact, the address book has it for a saved
        one, or nobody does. ``ambiguous`` is the case that matters -- two contacts sharing a
        display name -- and it is reported rather than resolved.
        """
        now = self.clock()
        with ExitStack() as phone_held:
            self.executor.take_phone(phone_held, "chat-list")
            rows = self.driver.list_chats(include_archived=include_archived)
            self.driver.park()
        chats = [self._identify(row) for row in rows]
        self.ledger.note(now, "list_chats", None, chats=len(chats),
                         archived=sum(1 for c in chats if c["archived"]))
        return {"ok": True, "at": L.utc(now), "include_archived": include_archived,
                "count": len(chats), "chats": chats}

    def _identify(self, row):
        """One row, plus what the handset can prove about whose row it is."""
        title = row.title
        entry = {"title": title, "unread": row.unread, "stamp": row.stamp,
                 "archived": row.archived, "has_preview": row.has_preview,
                 "phone": None, "phone_source": "unknown", "ambiguous": False}
        if I.looks_like_number(title):
            entry["phone"], entry["phone_source"] = "+" + I.digits(title), "title_is_number"
            return entry
        try:
            resolved = self.driver.resolve_counterparty(title)
        except I.Unresolvable:
            # Two contacts share this display name. The handset cannot say which chat this row is,
            # so neither can we, and the destructive path refuses on exactly this flag.
            entry["ambiguous"], entry["phone_source"] = True, "ambiguous_display_name"
            return entry
        if resolved:
            entry["phone"], entry["phone_source"] = resolved, "address_book"
        return entry

    def reconcile_unread(self):
        """The third door (TASK-234). -> {"chats", "reconciled", "skipped"}.

        The notification shade sees a message only when it is posted (never while the app is
        foregrounded -- ``AdbDriver.pull_inbound``'s own docstring), and every piggyback read
        (``read_thread``, ``send``, ``send_photos``/``send_gallery``, ``_read_evidence_for``,
        TASK-231) sees one only because SOME operation already had its own reason to open that
        exact chat. A chat nobody sends to, reads from or attaches media for gets neither door --
        its unread badge (``list_chats``'s own ``ChatRow.unread``) is the only place the loss is
        visible, and nothing before this read it back. This is that read: every unread,
        unambiguous row gets exactly the piggyback ``read_thread`` already gives any other caller
        -- no new day-derivation here, ``read_cold_thread`` is reused whole (TASK-231's own fix for
        a caller with no just-sent bubble to anchor "today" against).

        A row whose identity this cannot prove (no resolvable phone, or a display name two
        contacts share) is skipped and journalled rather than guessed -- the same refusal
        ``_identify`` already makes for every other caller of ``list_chats``. A row whose open
        or read fails is journalled and left for the next cycle; one bad row never ends the sweep.

        UNREAD ONLY, NOT "OR AN OUTBOUND-LESS TAIL" (a proposed widening, not built): the wider
        signal needs a bubble read of every chat, not only the unread ones, which is a full
        chat-by-chat open on every cycle instead of one open per genuine unread badge -- a
        materially bigger blast radius this task's own finding did not ask for. Said here rather
        than silently narrowed.
        """
        now = self.clock()
        listing = self.list_chats(include_archived=True)
        reconciled = skipped = 0
        for chat in listing["chats"]:
            if not chat["unread"]:
                continue
            if chat["phone"] is None or chat["ambiguous"]:
                skipped += 1
                self.ledger.note(now, "reconcile_skipped", None,
                                 title_tag=L.thread_tag(chat["title"]), reason=chat["phone_source"])
                continue
            try:
                self.read_thread(phone=chat["phone"], include_text=False,
                                 archived=chat["archived"])
            except (D.DriverError, E.BridgeRefusal) as exc:
                self.ledger.note(self.clock(), "reconcile_read_failed", None,
                                 thread=L.thread_tag(chat["phone"]), error=str(exc))
                continue
            reconciled += 1
        return {"ok": True, "at": L.utc(now), "chats": listing["count"],
                "reconciled": reconciled, "skipped": skipped}

    def read_thread(self, *, phone=None, chat=None, include_text=True, archived=False):
        """-> the visible bubbles of one conversation, oldest first.

        Addressed by number (which lets WhatsApp's own header be checked against the address book)
        or by the title on the chat list (which cannot be, and is therefore read-only: a chat opened
        by name is not sendable -- see ``AdbDriver.open_chat_row``).
        """
        now = self.clock()
        title, phone = self._one_address(phone, chat)
        with ExitStack() as phone_held:
            self.executor.take_phone(phone_held, phone or title)
            header = self._open(title, phone, archived=archived)
            bubbles = self.driver.read_bubbles()
            # Opening a chat clears its notification and WhatsApp posts no shade record while it
            # is foregrounded, same as a send (executor.py's own piggyback, TASK-231) -- so
            # whatever arrived in the seconds before or during this open is gone for good unless
            # read here, where the lock is already ours. Only possible when the caller named a
            # number: a chat opened by title alone has no E.164 for record_inbound to mint an id
            # against, and stays read-only for that (``_one_address``).
            if phone:
                try:
                    messages, unresolved = self.driver.read_cold_thread(phone)
                    seen = self.executor.record_inbound(messages, unresolved, self.clock())
                    self.ledger.note(self.clock(), "thread_read_inbound", None, **seen)
                except D.DriverError as exc:
                    self.ledger.note(self.clock(), "thread_read_failed", None, error=str(exc))
            self.driver.park()
        return {"ok": True, "at": L.utc(now), "chat": {"title": header, "phone": phone},
                "visibility": VISIBILITY, "count": len(bubbles),
                "messages": [_bubble(b, include_text=include_text) for b in bubbles],
                **_tally(bubbles)}

    # --- one message -------------------------------------------------------------------------
    def send_message(self, *, to, body, client_msg_id, action, constraints=None):
        """One bubble. -> the executor's own 200 body.

        This is the operation as a FUNCTION: the same call the HTTP route makes, callable in one
        step by anything running on this machine. It adds nothing and weakens nothing -- ledger
        first, one flock acquisition, the bubble re-read off the thread, a delivery tick or a 504.
        The guarantees live in bridge/executor.py and this is deliberately too thin to bend them.
        """
        request = {"client_msg_id": client_msg_id, "to": to, "kind": "text", "body": body,
                   "trace": {"action": action}}
        if constraints:
            request["constraints"] = constraints
        _status, payload = self.executor.send(request)
        return payload

    def _open(self, title, phone, *, archived):
        """-> the header the handset drew. By number when we have one: that path compares the header
        against the address book before anything else happens."""
        try:
            if phone is not None:
                return self.driver.open_chat(phone)
            return self.driver.open_chat_row(title, archived=archived)
        except D.DriverError as exc:
            raise E.not_on_whatsapp(
                "the conversation would not open; the driver's message is in the executor ledger",
                chat_tag=L.thread_tag(title)) from exc

    def _one_address(self, phone, chat):
        """A chat is addressed by number or by title, and naming both means naming neither."""
        if phone is None and chat is None:
            raise E.invalid_request("name the chat: pass 'phone' (E.164) or 'chat' (the title)")
        if phone is not None and chat is not None:
            raise E.invalid_request("pass 'phone' or 'chat', not both: two identities cannot be "
                                    "checked against one another before the chat is open")
        if phone is not None:
            if not isinstance(phone, str) or not phone.startswith("+") or not phone[1:].isdigit():
                raise E.invalid_request("phone must be an E.164 string (+digits)")
            return None, phone
        return _require_title(chat), None


def _require_title(chat):
    if not isinstance(chat, str) or not chat.strip():
        raise E.invalid_request("chat must be the chat's title as the handset draws it")
    return chat.strip()


def _bubble(bubble, *, include_text):
    out = {"direction": bubble.direction, "clock": bubble.clock,
           "tick": bubble.tick or None, "tick_state": bubble.tick_state,
           "body_sha256": D.body_sha256(bubble.text), "body_len": len(bubble.text)}
    if include_text:
        out["body"] = bubble.text
    return out


def _tally(bubbles):
    clocks = [b.clock for b in bubbles if b.clock]
    return {"incoming": sum(1 for b in bubbles if b.direction == "in"),
            "outgoing": sum(1 for b in bubbles if b.direction == "out"),
            "oldest_clock": clocks[0] if clocks else None,
            "newest_clock": clocks[-1] if clocks else None}
