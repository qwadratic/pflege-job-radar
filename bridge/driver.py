"""The driver boundary: what the executor is allowed to ask of a phone (TASK-130, TASK-142).

Two implementations sit behind it: ``bridge/adb_driver.AdbDriver``, which talks adb to the handset,
and ``FakeDriver`` below, which exists so the failure modes are reachable in a test with no phone in
the room.

HISTORY, kept because it is the reason for a rule here. This file used to wrap ``apps.wa_phone`` out
of a colleague's agent worktree on the mini. TASK-142 replaced that with our own adb driver: the
worktree is disposable (a ``git worktree remove`` swaps every signature under us) and their send
path reports an unmatched bubble as "sent (unverified)", which fired on 2 of 23 live sends. We own
this rail end to end now and import nothing from that tree.

THE INVARIANT THIS PACKAGE EXISTS TO HOLD (decision-8, 2026-09-21): on this rail there is no provider
message id, so "no sent without a confirmed provider message id" becomes **no sent without a
verified delivery tick**. ``unverified`` is a 504 and never a ``sent``.

Deliberately few verbs. Anything richer would put decisions in the driver, and the driver is the one
layer we cannot unit-test.
"""
from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import errors as E
from . import inbound as I

#: WhatsApp's delivery ticks, verbatim off the bubble's content-desc on the German UI.
TICKS = {"Gesendet": "sent", "Zugestellt": "delivered", "Gelesen": "read"}
#: The state a bubble we could not find would have. It is never a success here -- it is the 504.
UNVERIFIED = "unverified"

#: Their PhoneLock default, adopted: long enough to outlast one of their daemon's 15 s cycles, short
#: enough that a stuck holder surfaces as a 503 instead of a hung request.
LOCK_TIMEOUT_SEC = 30.0
#: How long the executor waits for a tick to be drawn on a bubble that is already on the thread.
TICK_WAIT_SEC = 30.0
#: TASK-130 AC#9: escalation screenshots are kept for 7 days.
SCREENSHOT_RETENTION_DAYS = 7
#: Ivan's own cap, 2026-09-22 (VOLUME-style -- a candidate never gets a wall of anything): the
#: most photos one send_photos() call attaches at once. Shared by every PhoneDriver implementation
#: (bridge/adb_driver.py's own copy would drift from this one otherwise).
MAX_PHOTOS_PER_SEND = 5


class DriverError(RuntimeError):
    """Anything the phone did that we cannot act on. The executor decides what it means."""


class PhoneBusy(DriverError):
    """The flock is held by the other lane. NOTHING WAS TYPED, which is what makes it a 503 on a
    send (``executor.take_phone``) and a counted skip in the watcher rather than an incident.

    Its own class rather than a substring in a message (TASK-146): the watcher used to decide by
    ``"lock busy" in str(exc)``, so rewording the refusal would have turned every busy cycle into a
    logged error on a rail where the other lane takes the same lock every 15 s.
    """


@dataclass(frozen=True)
class ChatRow:
    """One row of the handset's chat list, as WhatsApp draws it (TASK-147).

    ``title`` is the identity the UI offers and therefore the identity a caller has to name; it is a
    display name for a saved contact and the number itself for an unsaved one. ``preview`` is
    deliberately absent: the last-message line is a message body, and bodies do not leave the phone
    through a listing call.
    """

    title: str
    unread: int             # 0 when no badge is drawn
    stamp: str              # the row's own date column: 'HH:MM', 'GESTERN', '18.08.26'
    archived: bool
    has_preview: bool       # whether a last-message line is drawn at all, never what it says


@dataclass(frozen=True)
class BubbleView:
    """One bubble as the phone draws it. This is the whole evidence base on this rail."""

    direction: str          # 'in' | 'out'
    text: str
    clock: str              # 'HH:MM' exactly as WhatsApp renders it -- no date, no seconds
    tick: str               # '' | 'Gesendet' | 'Zugestellt' | 'Gelesen' | 'unverified'

    @property
    def tick_state(self):
        """-> 'sent' | 'delivered' | 'read', or None when there is nothing to believe.

        '' (no tick drawn yet) and 'unverified' both return None. A None here may never become a
        ``sent`` row.
        """
        return TICKS.get(self.tick)


def body_sha256(text):
    """The body's identity for first-body-wins and for the read-back match.

    Whitespace-normalised, because a bubble read back off the screen and the body we were handed
    disagree about line breaks in the same message.
    """
    return hashlib.sha256(" ".join(str(text).split()).encode("utf-8")).hexdigest()


class PhoneDriver:
    """The verbs. Implemented by AdbDriver against the handset and by FakeDriver in tests."""

    def lock(self, *, timeout=LOCK_TIMEOUT_SEC):
        """Context manager around huawei01.lock. ONE BUBBLE PER ACQUISITION -- see executor.py."""
        raise NotImplementedError

    def open_chat(self, phone):
        """-> the header title. Raises DriverError. Guarantee: types nothing, on any path."""
        raise NotImplementedError

    def send_bubble(self, text):
        """-> BubbleView read back off the thread. Keys MAY have been pressed if this raises."""
        raise NotImplementedError

    def send_photo(self, phone, local_path):
        """Share one local image file into the thread for ``phone`` (TASK-131 round 7, outbound
        media). -> (clock, tick) the newest outgoing bubble reads after sending. Same contract as
        send_bubble: requires an already-open, already-verified thread for ``phone``."""
        raise NotImplementedError

    def send_photos(self, phone, local_paths):
        """send_photo, once per path, in order. -> [(clock, tick), ...]. Raises at the first
        failure rather than sending the rest silently."""
        raise NotImplementedError

    def send_gallery(self, phone, local_paths, caption=""):
        """Share up to MAX_PHOTOS_PER_SEND local image files as ONE message -- a photo album with
        a single shared caption -- via WhatsApp's own in-chat gallery picker (TASK-131 round 7
        gallery redesign). -> (clock, tick) the newest outgoing bubble reads after sending. Same
        contract as send_photo: requires an already-open, already-verified thread for ``phone``."""
        raise NotImplementedError

    def send_document(self, phone, local_path, caption=""):
        """Share ONE local file, any type, as WhatsApp's own document attachment (TASK-131 round 7,
        Ivan 2026-09-23: a future resume-update flow needs files, not photos alone). -> (clock,
        tick) the newest outgoing bubble reads after sending. Same contract as send_photo: requires
        an already-open, already-verified thread for ``phone``."""
        raise NotImplementedError

    def read_bubbles(self):
        """-> the visible bubbles of the open chat, oldest first."""
        raise NotImplementedError

    def pull_inbound(self):
        """-> ([InboundMessage] with ids, [(title, reason)] that could not be minted)."""
        raise NotImplementedError

    def read_open_thread(self, phone):
        """-> ([InboundMessage], [(title, reason)]) for the chat that is open right now."""
        raise NotImplementedError

    def read_media_evidence(self):
        """-> [{"clock", "evidence"}] for the chat that is open right now (TASK-131 round 6): one
        entry per bubble, ``evidence`` the pool of every text and content-desc string in that
        bubble's own node cluster -- not just ``message_text`` (bridge/adb_driver.py's own docstring
        on why a voice note used to be invisible to this reader). ``bridge/identity.py::
        parse_bubble_evidence`` turns the pool into duration/size/pages/filename; this method only
        gathers it. Caller holds the lock and has already opened the thread, same contract as
        ``read_bubbles``."""
        raise NotImplementedError

    # --- inbound media (TASK-131) --------------------------------------------------------------
    def list_media(self):
        """-> {rel_path: (size, mtime_epoch)} for every file under the handset's WhatsApp media
        tree. Filesystem-level (``find``+``stat``), touches no UI, takes no lock."""
        raise NotImplementedError

    def pull_media(self, rel_path, dest_path):
        """Copy one file off the handset to ``dest_path``. -> the path written. Raises
        ``DriverError`` on any failure; nothing was written on that path in that case.
        Filesystem-level (``adb pull``), touches no UI, takes no lock."""
        raise NotImplementedError

    # --- the chat list (TASK-147) -------------------------------------------------------------
    def list_chats(self, *, include_archived=True):
        """-> [ChatRow] as the handset draws them, top row first. Read-only, taps nothing but the
        archive folder when ``include_archived``."""
        raise NotImplementedError

    def open_chat_row(self, title, *, archived=False):
        """-> the header WhatsApp drew, after opening the chat by tapping its row.

        The sibling of ``open_chat``, for a chat the handset knows by name and we cannot address by
        number. Raises when two rows carry that title: which conversation is meant is not guessable.
        """
        raise NotImplementedError

    def resolve_counterparty(self, title):
        """-> E.164 for a display name, "" when the handset cannot say, raising ``Unresolvable``
        when two contacts share the name. Implemented against the handset's own address book."""
        raise NotImplementedError

    def clear_chat_history(self, title, *, archived=False, include_starred=True):
        """Clear one chat's history and KEEP the chat. -> dict describing the taps taken.

        The verbs are the taps and nothing else: whether the chat really is empty afterwards is
        established by re-reading it, and that belongs to the caller (bridge/operations.py).
        """
        raise NotImplementedError

    def delete_chat_row(self, title, *, archived=False):
        """Delete one chat entirely. -> dict describing the taps taken. Verification is the
        caller's, for the same reason as above."""
        raise NotImplementedError

    def park(self):
        """Back out to the chat list and onto the launcher, so notifications fire again."""
        raise NotImplementedError

    def escalation_shot(self, tag):
        """-> path of one screenshot, taken only when a send could not be confirmed."""
        raise NotImplementedError

    def sweep_screenshots(self, now, *, days=SCREENSHOT_RETENTION_DAYS):
        """-> how many escalation screenshots the retention sweep removed (TASK-130 AC#9)."""
        raise NotImplementedError

    def describe(self):
        """-> dict identifying the driver code actually loaded (TASK-140 drift monitor)."""
        raise NotImplementedError


class FakeDriver(PhoneDriver):
    """The test phone. It exists to make the failure modes reachable, not to be realistic.

    Every field is a script the test writes: ``ticks`` is consumed one per send, ``fail_on_send``
    and ``fail_on_open`` raise, and ``thread`` is what read_bubbles returns. ``lock_events`` is the
    proof for the flock rule: one acquire/release pair per bubble, nothing in between.
    """

    def __init__(self, *, ticks=None, thread=None, inbound=None, open_thread=None, chats=None,
                 media_files=None):
        self.ticks = list(ticks or [])
        self.thread = list(thread or [])
        self.inbound = list(inbound or [])
        self.open_thread = list(open_thread or [])   # what the post-send thread read hands back
        self.unresolved = []
        # what read_media_evidence() hands back for the chat currently open (TASK-131 round 6) --
        # a test scripts it per phone via ``media_evidence_by_phone``, keyed the same way open_chat
        # keys ``chats``.
        self.media_evidence_by_phone = {}
        # --- media (TASK-131) -------------------------------------------------------------------
        # rel_path -> bytes, scriptable per test. mtimes default to 0 for every file and a test
        # that cares about the linking window sets media_mtimes[rel] explicitly.
        self.media_files = dict(media_files or {})
        self.media_mtimes = {}
        self.pulled_media = []     # rel paths this driver was asked to pull, in call order
        self.fail_pull = None      # a rel path that raises when pulled, or None
        self.sent = []            # bodies that reached the phone -- the "did it send" assertion
        # --- outbound media: photos (TASK-131 round 7) ------------------------------------------
        self.sent_photos = []     # (phone, local_path) pairs that reached the phone
        self.fail_on_send_photo = None
        # --- outbound media: one gallery message (TASK-131 round 7 gallery redesign) -------------
        self.sent_galleries = []  # (phone, local_paths, caption) tuples that reached the phone
        self.fail_on_send_gallery = None
        # --- outbound media: one document (TASK-131 round 7, Ivan 2026-09-23) --------------------
        self.sent_documents = []  # (phone, local_path, caption) tuples that reached the phone
        self.fail_on_send_document = None
        self.opened = []
        self.lock_events = []
        self.lock_held = False
        self.parked = 0
        self.shots = []
        self.busy = False         # the other lane holds huawei01.lock
        self.fail_on_open = None
        self.fail_on_send = None
        self.hook = None          # called with (driver) inside the lock, before send_bubble
        self.read_hook = None     # called with (driver) before read_bubbles: the phone redrawing
        # --- the chat book (TASK-147) ---------------------------------------------------------
        # title -> {"phone", "bubbles", "unread", "archived"}. Empty by default, and while it is
        # empty this driver behaves exactly as it did before: one thread, held in ``thread``. A
        # test that cares about chats fills it, and then the open chat follows the book.
        self.chats = {k: dict(v) for k, v in (chats or {}).items()}
        self.open_title = None
        self._open_phone = None
        self.cleared = []         # titles a clear was tapped on
        self.deleted = []         # titles a delete was tapped on
        self.clear_leaves = 0     # bubbles a clear leaves behind: the verification-failure script
        self.delete_leaves_row = False   # the phone said yes and the row is still there
        self.duplicate_titles = []       # titles list_chats draws twice: an ambiguous identity
        self.ambiguous_titles = []       # display names the address book ties to two numbers
        self.fail_on_list = None         # the chat list will not come to the front (adb_driver's
                                         # own refusal), which is how a destruction goes unproved
        self.vanished_titles = []        # rows the list no longer draws while the NUMBER still
                                         # opens a thread: a clear whose tap removed the chat

    @contextmanager
    def lock(self, *, timeout=LOCK_TIMEOUT_SEC):
        if self.busy:
            # The other lane holds the flock. Reachable in a test because it is the documented
            # normal case on this handset, not an edge (TASK-146).
            raise PhoneBusy(f"phone lock busy for {timeout:.0f}s")
        if self.lock_held:
            raise AssertionError("re-entrant lock: a bubble is already holding the phone")
        self.lock_held = True
        self.lock_events.append("acquire")
        try:
            yield
        finally:
            self.lock_held = False
            self.lock_events.append("release")

    def open_chat(self, phone):
        if not self.lock_held:
            raise AssertionError("open_chat outside the lock")
        if self.fail_on_open:
            raise DriverError(self.fail_on_open)
        self.opened.append(phone)
        self._open_phone = phone
        for title, chat in self.chats.items():
            if chat.get("phone") == phone:
                self.open_title = title
                return title
        if self.chats:
            raise DriverError(f"no chat on this handset for {phone}")
        return phone

    def send_bubble(self, text):
        if not self.lock_held:
            raise AssertionError("send_bubble outside the lock")
        if self.hook is not None:
            self.hook(self)
        if self.fail_on_send:
            self.sent.append(text)  # keys were pressed: that is exactly what makes it uncertain
            raise DriverError(self.fail_on_send)
        self.sent.append(text)
        tick = self.ticks.pop(0) if self.ticks else "Gesendet"
        bubble = BubbleView("out", text, "09:15", tick)
        self._bubbles().append(bubble)
        return bubble

    def send_photo(self, phone, local_path):
        if not self.lock_held:
            raise AssertionError("send_photo outside the lock")
        if self._open_phone != phone:
            raise DriverError("send_photo without a verified open chat for this phone")
        if self.hook is not None:
            self.hook(self)
        if self.fail_on_send_photo:
            self.sent_photos.append((phone, local_path))
            raise DriverError(self.fail_on_send_photo)
        self.sent_photos.append((phone, local_path))
        tick = self.ticks.pop(0) if self.ticks else "Gesendet"
        self._bubbles().append(BubbleView("out", "", "09:15", tick))
        return ("09:15", tick)

    def send_photos(self, phone, local_paths):
        if not local_paths:
            raise DriverError("send_photos called with no files")
        if len(local_paths) > MAX_PHOTOS_PER_SEND:
            raise DriverError(f"{len(local_paths)} photos requested, this rail sends at most "
                              f"{MAX_PHOTOS_PER_SEND} at once")
        return [self.send_photo(phone, p) for p in local_paths]

    def send_gallery(self, phone, local_paths, caption=""):
        if not self.lock_held:
            raise AssertionError("send_gallery outside the lock")
        if self._open_phone != phone:
            raise DriverError("send_gallery without a verified open chat for this phone")
        if not local_paths:
            raise DriverError("send_gallery called with no files")
        if len(local_paths) > MAX_PHOTOS_PER_SEND:
            raise DriverError(f"{len(local_paths)} photos requested, this rail sends at most "
                              f"{MAX_PHOTOS_PER_SEND} at once")
        if self.hook is not None:
            self.hook(self)
        if self.fail_on_send_gallery:
            self.sent_galleries.append((phone, local_paths, caption))
            raise DriverError(self.fail_on_send_gallery)
        self.sent_galleries.append((phone, local_paths, caption))
        tick = self.ticks.pop(0) if self.ticks else "Gesendet"
        self._bubbles().append(BubbleView("out", caption, "09:15", tick))
        return ("09:15", tick)

    def send_document(self, phone, local_path, caption=""):
        if not self.lock_held:
            raise AssertionError("send_document outside the lock")
        if self._open_phone != phone:
            raise DriverError("send_document without a verified open chat for this phone")
        if self.hook is not None:
            self.hook(self)
        if self.fail_on_send_document:
            self.sent_documents.append((phone, local_path, caption))
            raise DriverError(self.fail_on_send_document)
        self.sent_documents.append((phone, local_path, caption))
        tick = self.ticks.pop(0) if self.ticks else "Gesendet"
        self._bubbles().append(BubbleView("out", caption, "09:15", tick))
        return ("09:15", tick)

    def read_bubbles(self):
        if not self.lock_held:
            raise AssertionError("read_bubbles outside the lock")
        if self.read_hook is not None:
            self.read_hook(self)
        return list(self._bubbles())

    def _bubbles(self):
        """The open chat's bubbles: the chat book's when there is one, ``thread`` otherwise."""
        if self.open_title is not None and self.open_title in self.chats:
            return self.chats[self.open_title].setdefault("bubbles", [])
        return self.thread

    def pull_inbound(self):
        # NO lock assertion (TASK-131 round 6): the notification shade is a pure read and
        # bridge/executor.py::Executor.drain_inbound no longer takes huawei01.lock for it -- this
        # must be reachable and correct whether or not something else holds the lock right now.
        out, self.inbound = list(self.inbound), []
        unresolved, self.unresolved = list(self.unresolved), []
        return out, unresolved

    def read_open_thread(self, phone):
        if not self.lock_held:
            raise AssertionError("read_open_thread outside the lock")
        out, self.open_thread = list(self.open_thread), []
        return out, []

    def read_media_evidence(self):
        if not self.lock_held:
            raise AssertionError("read_media_evidence outside the lock")
        return list(self.media_evidence_by_phone.get(self._open_phone) or [])

    # --- media (TASK-131) ------------------------------------------------------------------------
    def list_media(self):
        return {rel: (len(blob), self.media_mtimes.get(rel, 0))
                for rel, blob in self.media_files.items()}

    def pull_media(self, rel_path, dest_path):
        if self.fail_pull == rel_path:
            raise DriverError(f"adb pull of {rel_path!r} failed (scripted)")
        if rel_path not in self.media_files:
            raise DriverError(f"no such file on the handset: {rel_path!r}")
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.media_files[rel_path])
        self.pulled_media.append(rel_path)
        return dest

    # --- the chat list (TASK-147) -------------------------------------------------------------
    def list_chats(self, *, include_archived=True):
        if not self.lock_held:
            raise AssertionError("list_chats outside the lock")
        if self.fail_on_list:
            raise DriverError(self.fail_on_list)
        rows = []
        for title, chat in self.chats.items():
            if chat.get("archived") and not include_archived or title in self.vanished_titles:
                continue
            bubbles = chat.get("bubbles") or []
            row = ChatRow(title=title, unread=int(chat.get("unread", 0)),
                          stamp=chat.get("stamp", "09:15"), archived=bool(chat.get("archived")),
                          has_preview=bool(bubbles))
            rows.append(row)
            if title in self.duplicate_titles:
                rows.append(row)
        return rows

    def _one_row(self, title):
        if title in self.duplicate_titles:
            raise DriverError("two rows carry this title: which chat is meant is not guessable")
        if title not in self.chats:
            raise DriverError(f"no chat row with this title on the list")
        return self.chats[title]

    def open_chat_row(self, title, *, archived=False):
        if not self.lock_held:
            raise AssertionError("open_chat_row outside the lock")
        if self.fail_on_open:
            raise DriverError(self.fail_on_open)
        self._one_row(title)
        self.open_title = title
        self.opened.append(title)
        return title

    def resolve_counterparty(self, title):
        if title in self.ambiguous_titles:
            raise I.Unresolvable("2 contacts share this display name")
        if I.looks_like_number(title):
            return "+" + I.digits(title)
        return (self.chats.get(title) or {}).get("phone") or ""

    def clear_chat_history(self, title, *, archived=False, include_starred=True):
        if not self.lock_held:
            raise AssertionError("clear_chat_history outside the lock")
        chat = self._one_row(title)
        bubbles = chat.get("bubbles") or []
        # ``clear_leaves`` is the phone saying yes and leaving something behind -- a starred message
        # the sheet's checkbox did not cover. It has to be reachable: the whole point of verifying
        # afterwards is that this can happen.
        chat["bubbles"] = list(bubbles[:self.clear_leaves])
        chat["unread"] = 0
        self.cleared.append(title)
        return {"menu_item": "Chat leeren", "confirmed_with": "primary_button",
                "starred_included": include_starred}

    def delete_chat_row(self, title, *, archived=False):
        if not self.lock_held:
            raise AssertionError("delete_chat_row outside the lock")
        self._one_row(title)
        if not self.delete_leaves_row:
            self.chats.pop(title)
            if self.open_title == title:
                self.open_title = None
        self.deleted.append(title)
        return {"menu_item": "Chat löschen", "confirmed_with": "button1"}

    def park(self):
        self.parked += 1
        self.open_title = None

    def escalation_shot(self, tag):
        self.shots.append(tag)
        return f"/fake/shots/{tag}.png"

    def sweep_screenshots(self, now, *, days=SCREENSHOT_RETENTION_DAYS):
        return 0

    def describe(self):
        return {"kind": "fake", "serial": None, "modules": {}}


def require_tick(bubble):
    """The single line TASK-130 exists for. -> tick state, or a 504 refusal. Never a guess."""
    state = bubble.tick_state
    if state is None:
        raise E.send_unconfirmed(
            "no verified delivery tick was read off the bubble",
            tick=bubble.tick or "(none drawn)")
    return state


def wait_for_tick(driver, want_sha256, *, deadline_sec=TICK_WAIT_SEC, sleep=time.sleep,
                  monotonic=time.monotonic):
    """Re-read the open thread until our bubble carries a tick. -> BubbleView or None.

    A bubble can be on the thread with status '' (drawn, not yet acked by the server) and that is
    not a confirmation. Called inside the same lock acquisition as the send -- re-opening the chat
    later would read a thread someone else may have moved.

    IT IS THE BOTTOM-MOST MATCH THAT COUNTS, and that is what the reversed scan below is for
    (TASK-146). Identical bodies do recur on this rail (the media acknowledgement and the follow-up
    nudge are constants), and an older identical bubble already carries a tick. This is only ever
    reached after ``AdbDriver._verify`` has established that a bubble with this body appeared that
    was not there before the send tap, so the newest match IS ours; scanning from the top would
    have found the old one first and confirmed a message from another day.
    """
    end = monotonic() + deadline_sec
    while True:
        for bubble in reversed(driver.read_bubbles()):
            if bubble.direction != "out" or body_sha256(bubble.text) != want_sha256:
                continue
            if bubble.tick_state is not None:
                return bubble
            break
        if monotonic() >= end:
            return None
        sleep(1.0)
