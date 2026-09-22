"""The handset operations as callable code, not as a script somebody rewrites (TASK-147).

WHY THIS MODULE EXISTS. Every time the handset had to be listed, read or tidied up, somebody wrote
a fresh UI-automation script, ran it once and threw it away. That is a fine way to do a thing once
and a terrible way to do a destructive thing at all: a script written in the moment has no identity
check, no record, and no verification -- it taps where it expects the row to be and reports success
because nothing raised. These six operations are the same work, written once, with the discipline
attached to the operation instead of to whoever is driving it.

    list_chats     read-only: the chat list as the handset draws it
    read_thread    read-only: the visible bubbles of one chat
    send_message   one bubble, through the executor, with every guarantee it already had
    clear_chat     empty a conversation, keep it
    delete_chat    remove a conversation
                   (send_broadcast is the seventh and lives in bridge/broadcast.py: it needs a
                    ledger-backed run and a runner thread, which is a module's worth of its own.)

THE DESTRUCTIVE DISCIPLINE, and every clause of it is there because its absence is a way to delete
the wrong conversation:
  1. the caller names the chat it expects, and passes ``confirm`` explicitly. No confirm, no taps.
  2. the tool reads the chat FIRST and reports what it is about to destroy -- name, resolved number,
     how many bubbles are visible. ``expect_messages`` lets a caller assert that count and refuse
     if the conversation moved on since it looked.
  3. the identity on screen is proved against the identity named. Two rows with one name is a
     refusal, not a coin toss. A number is checked by opening the chat by number, which makes
     WhatsApp draw the header and ``_require_thread`` compare it against the address book.
  4. ONE FLOCK ACQUISITION COVERS PREVIEW AND ACT. Releasing in between would let the other lane
     move the screen between what we looked at and what we tap, and that gap is the whole risk.
  5. after acting, the result is verified off the handset -- the chat is gone from the list, or the
     conversation reads back empty. An unverified destruction is a 504 ``destruction_unverified``,
     which is the same rule as ``send_unconfirmed``: this rail does not report outcomes it has not
     seen.
  6. THE AUDIT ROW IS WRITTEN BEFORE THE DESTRUCTIVE VERB, not after it -- the same write-ahead
     shape as ``ledger.begin`` before the first keystroke of a send. Everything between the tap and
     the answer is an adb call that can fail (the rescan, the re-open, park), and a conversation
     destroyed with no record is the one outcome this module exists to prevent. The row says what
     is about to be destroyed; the verification's outcome is written onto it afterwards, passed or
     failed, before the refusal is raised.
  7. AND THAT ROW IS READ BACK BEFORE A 404. A title the list does not carry is answered with the
     reason it does not: there never was such a chat, or this executor removed it at T. On
     2026-09-21 an operator met the bare "no chat with that title" after a client-side timeout had
     hidden a successful delete, and read it as proof that the tool had matched the wrong chat.

PII: a chat title is a contact's display name and it is load-bearing here -- it is the identity the
caller has to name and the audit has to record. Message bodies are not: ``list_chats`` reports that
a preview line exists and never what it says.
"""
from __future__ import annotations

from contextlib import ExitStack

from . import driver as D
from . import errors as E
from . import inbound as I
from . import ledger as L

#: The three answers to "is the record's row about the number the caller named", as they travel in
#: a ``chat_not_found`` detail. One agreement, two halves: ``app/wa/bridge.py`` branches on these
#: words (NUMBER_SAME / NUMBER_DIFFERENT / NUMBER_UNRECORDED) and neither number leaves this
#: machine. ``NUMBER_UNRECORDED`` is a real state and not a gap -- the handset cannot always
#: resolve a saved contact to a number, and ``audit.to_phone`` is null on those rows.
NUMBER_SAME = "same"
NUMBER_DIFFERENT = "different"
NUMBER_UNRECORDED = "unrecorded"

#: What ``read_thread`` and the destructive preview can see. WhatsApp draws the bottom of a
#: conversation and this code does not scroll, so every count here is the VISIBLE count and says so
#: in its own field name. A scrolled count would be a different, slower, and much more fragile
#: promise, and inventing it silently would make "12 messages" mean two different things.
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

    # --- destructive -------------------------------------------------------------------------
    def clear_chat(self, *, chat=None, phone=None, confirm=False, expect_messages=None,
                   archived=False, include_starred=True):
        """Empty a conversation and keep it. -> what was destroyed, and the proof it is gone."""
        return self._destroy("clear_chat", chat=chat, phone=phone, confirm=confirm,
                             expect_messages=expect_messages, archived=archived,
                             include_starred=include_starred)

    def delete_chat(self, *, chat=None, phone=None, confirm=False, expect_messages=None,
                    archived=False):
        """Remove a conversation from the handset. -> what was destroyed, and the proof."""
        return self._destroy("delete_chat", chat=chat, phone=phone, confirm=confirm,
                             expect_messages=expect_messages, archived=archived)

    def _destroy(self, operation, *, chat, phone, confirm, expect_messages, archived,
                 include_starred=True):
        now = self.clock()
        title = _require_title(chat)
        if confirm is not True:
            raise E.invalid_request(
                f"{operation} needs confirm=true: this removes messages from the handset and there "
                "is no undo. Read the chat first with read_thread.", chat_tag=L.thread_tag(title))
        with ExitStack() as phone_held:
            self.executor.take_phone(phone_held, phone or title)
            row = self._match_row(title, phone, archived=archived)
            before = self._preview(title, phone, archived=archived)
            self._require_expected(operation, title, before, expect_messages)
            destroyed = {**before, "unread_before": row["unread"]}
            # WRITE-AHEAD, like ledger.begin before the first keystroke on a send. Everything after
            # the destructive tap can raise -- the rescan needs the chat list to come to the front,
            # the re-open needs a dump, park() needs adb -- and a conversation destroyed with no
            # audit row is the one state this tool exists to prevent. So the row exists BEFORE the
            # verb, saying what is about to be destroyed and that nothing is proved yet; the
            # verification outcome is written onto it afterwards.
            audit_id = self.ledger.append_audit(
                # The number the HANDSET says this chat belongs to, not only the one the caller
                # named: a delete by title alone still has to be answerable for whose conversation
                # it was.
                self.clock(), operation=operation, chat_title=title, phone=row["phone"] or phone,
                verified=False, detail={"state": "attempted", "destroyed": destroyed})
            taps = None
            try:
                if operation == "clear_chat":
                    taps = self.driver.clear_chat_history(title, archived=archived,
                                                          include_starred=include_starred)
                    proof = self._verify_cleared(title, phone, archived=archived)
                else:
                    taps = self.driver.delete_chat_row(title, archived=archived)
                    proof = self._verify_deleted(title, archived=archived)
            except D.DriverError as exc:
                # Raised by the VERB itself (the menu did not walk) or by anything the verifiers
                # do not already catch. Either way the taps may have landed, so this is the
                # unverified answer and not a 500 -- and the audit row below records why.
                proof = {"verified": False, "method": "none", "chat_present": None,
                         "why": f"the driver raised after the operation began: {exc}"}
            # The ledger row is the durable truth and it is written before we tidy up -- the same
            # rule executor.send applies to a delivered message (park_failed is journalled, never
            # promoted into the caller's answer).
            try:
                self.driver.park()
            except D.DriverError as exc:
                self.ledger.note(self.clock(), "park_failed", None, error=str(exc),
                                 chat=L.thread_tag(title))
        self.ledger.finish_audit(audit_id, self.clock(), verified=proof["verified"],
                                 detail={"state": "verified" if proof["verified"] else "unproved",
                                         "destroyed": destroyed, "ui": taps, "proof": proof})
        result = {"ok": True, "operation": operation, "at": L.utc(now), "chat": row,
                  "destroyed": destroyed, "ui": taps, "verification": proof, "audit_id": audit_id}
        if not proof["verified"]:
            # The taps happened. Saying so loudly is the only honest end to this, and the audit row
            # above is already written -- the record does not depend on the caller catching this.
            raise E.destruction_unverified(
                f"{operation} was confirmed on the handset and the result could not be proved: "
                f"{proof['why']}", chat_tag=L.thread_tag(title), audit_id=audit_id,
                verification=proof)
        return result

    def _match_row(self, title, phone, *, archived):
        """The row this operation is about, or a refusal. Reads the list; taps nothing."""
        rows = [self._identify(row) for row in self.driver.list_chats(include_archived=True)]
        hits = [r for r in rows if r["title"] == title and r["archived"] == archived]
        if not hits:
            raise self._not_found(title, rows, archived=archived, phone=phone)
        if len(hits) > 1:
            raise E.chat_identity_mismatch(
                f"{len(hits)} chats carry that title: which conversation is meant is not guessable",
                chat_tag=L.thread_tag(title), matches=len(hits))
        row = hits[0]
        if row["ambiguous"]:
            raise E.chat_identity_mismatch(
                "the handset's address book ties that display name to more than one number, so "
                "this row's owner cannot be established", chat_tag=L.thread_tag(title))
        if phone is not None and row["phone"] != phone:
            raise E.chat_identity_mismatch(
                "the chat on the handset is not the number the caller named",
                chat_tag=L.thread_tag(title), named=L.thread_tag(phone),
                on_handset=L.thread_tag(row["phone"] or ""), phone_source=row["phone_source"])
        return row

    def _not_found(self, title, rows, *, archived, phone=None):
        """-> the 404 for a title this folder does not carry, carrying WHY it does not carry it.

        "No chat with that title is on the handset" is true of a conversation that never existed,
        of one that is simply in the other folder, and of one this executor deleted ten minutes
        ago. On 2026-09-21 an operator met the bare sentence -- after a client-side timeout had
        already hidden the successful delete -- and read it as the tool having matched the wrong
        chat. The three are distinguishable on this machine, so they are distinguished here.

        The order matters. A row in the OTHER folder is checked first, because the archive holding
        that title means the conversation still exists and no audit row may be offered as the
        reason it is missing. Only when the title is on NEITHER list is the record consulted, and
        only ``delete_chat`` rows explain an absence -- a cleared chat stays on the list, which is
        what ``clear_chat`` promises, so a clear proves nothing about a missing row.

        WHOSE CONVERSATION THAT ROW IS ABOUT IS DECIDED HERE, because this is the only machine that
        holds both numbers: the one the caller named and the one the row recorded. Two people can
        share a display name, so a row found by title alone is not by itself an answer about the
        conversation that was named -- and the client cannot check it, since neither number may
        travel. ``named_number`` is that comparison as one word (``NUMBER_SAME`` /
        ``NUMBER_DIFFERENT`` / ``NUMBER_UNRECORDED``, read by ``app/wa/bridge.py``), and it is
        ``None`` when the caller named no number for this to be about.

        PII: the detail carries the record's id, moment and counts, never the number or the title.
        """
        common = {"chat_tag": L.thread_tag(title), "archived": archived, "chats_on_list": len(rows)}
        here, there = ("the archive", "the main chat list") if archived else \
                      ("the main chat list", "the archive")
        if any(r["title"] == title for r in rows):
            return E.chat_not_found(
                f"no chat with that title is on {here}: the handset draws that conversation in "
                f"{there}", in_other_folder=True, **common)
        removed = self.ledger.audit_for_chat(title, operation="delete_chat")
        if not removed:
            return E.chat_not_found(
                "no chat with that title is on the handset's list, and this executor's audit "
                "records no destruction of it either", **common)
        row = removed[0]
        return E.chat_not_found(
            f"no chat with that title is on the handset's list: this executor deleted it at "
            f"{row['at']} (audit {row['id']})",
            destroyed_by_us={"audit_id": row["id"], "at": row["at"], "operation": row["operation"],
                             "verified": row["verified"], "detail": row["detail"],
                             "named_number": _named_number(phone, row["to_phone"])}, **common)

    def _require_expected(self, operation, title, before, expect_messages):
        """``expect_messages`` is the caller saying what it saw. A conversation that grew a bubble
        between the read and the confirm is a different conversation than the one approved.

        AND WHEN IT SHRANK, THE RECORD IS ASKED WHY -- the same question rule 7 above applies to a
        missing row. "I approved 3 and the handset shows 0" is precisely the sentence an operator
        reads as "this tool matched the wrong chat", and the likeliest reason there is less there
        than when they looked is a clear_chat of this very title whose answer they never saw. That
        is on this machine, so it is said here instead of being left to be guessed at.
        """
        if expect_messages is None:
            return
        if not isinstance(expect_messages, int):
            raise E.invalid_request("expect_messages must be an integer or absent")
        if expect_messages == before["visible_messages"]:
            return
        because, evidence = "", {}
        if before["visible_messages"] < expect_messages:
            emptied = self.ledger.audit_for_chat(title, operation="clear_chat")
            if emptied:
                row = emptied[0]
                because = (f": this executor cleared that conversation at {row['at']} (audit "
                           f"{row['id']}, verified={row['verified']}), which is why there is less "
                           f"there than when it was read")
                evidence = {"emptied_by_us": {"audit_id": row["id"], "at": row["at"],
                                              "verified": row["verified"]}}
        raise E.chat_identity_mismatch(
            f"{operation} was approved for {expect_messages} visible messages and the handset now "
            f"shows {before['visible_messages']}{because}", chat_tag=L.thread_tag(title),
            expected=expect_messages, on_handset=before["visible_messages"], **evidence)

    def _preview(self, title, phone, *, archived):
        """Open it and count what is there. This is the 'what am I about to destroy' answer, and
        it is taken inside the same lock acquisition as the destruction."""
        self._open(title, phone, archived=archived)
        bubbles = self.driver.read_bubbles()
        return {"visibility": VISIBILITY, "visible_messages": len(bubbles), **_tally(bubbles)}

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

    def _verify_cleared(self, title, phone, *, archived):
        """The chat has to be STILL THERE and read back empty. Both halves, because ``clear_chat``
        promises to keep the conversation and an empty read alone cannot tell the two apart:
        opening a number WhatsApp has no thread for draws a new empty one (see ``_verify_deleted``),
        so a chat that was deleted rather than emptied -- the menu item that landed one row down,
        the sheet whose scope removed the entry -- would read back as nought bubbles and pass.
        A number can only ever prove that a thread is empty, never that it is this one."""
        try:
            rows = self.driver.list_chats(include_archived=True)
            present = any(r.title == title for r in rows)
            if not present:
                return {"verified": False, "method": "reopen_and_count", "chat_present": False,
                        "why": "the row is gone from the chat list: the conversation was removed, "
                               "not emptied"}
            self._open(title, phone, archived=archived)
            left = len(self.driver.read_bubbles())
        except E.BridgeRefusal as refusal:
            return {"verified": False, "method": "reopen_and_count", "chat_present": None,
                    "why": f"the conversation would not re-open: {refusal.message}"}
        except D.DriverError as exc:
            # The taps happened and the handset will not answer for them. 504, not 500: an
            # unprovable destruction is exactly what destruction_unverified is for.
            return {"verified": False, "method": "reopen_and_count", "chat_present": None,
                    "why": f"the verification could not run: {exc}"}
        return {"verified": left == 0, "method": "reopen_and_count", "chat_present": True,
                "messages_left": left, "visibility": VISIBILITY,
                "why": "" if left == 0 else f"{left} messages are still on the thread"}

    def _verify_deleted(self, title, *, archived):
        """The row has to be gone from the list. Not 'the chat does not open': opening a number
        WhatsApp has no thread for creates an empty one, which would prove the opposite of what it
        looks like."""
        try:
            rows = self.driver.list_chats(include_archived=True)
        except D.DriverError as exc:
            return {"verified": False, "method": "chat_list_rescan", "chat_present": None,
                    "why": f"the verification could not run: {exc}"}
        still = [r for r in rows if r.title == title]
        return {"verified": not still, "method": "chat_list_rescan", "chat_present": bool(still),
                "chats_on_list": len(rows),
                "why": "" if not still else "the row is still on the chat list"}

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


def _named_number(named, recorded):
    """-> which of the three the row is, or None when the caller named no number to compare."""
    if named is None:
        return None
    if recorded is None:
        return NUMBER_UNRECORDED
    return NUMBER_SAME if recorded == named else NUMBER_DIFFERENT


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
