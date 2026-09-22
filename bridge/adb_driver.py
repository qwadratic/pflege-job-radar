"""Our own adb driver for the handset (TASK-142). Stdlib only, and it imports nothing of theirs.

WHY THIS EXISTS AT ALL. bridge/driver.py used to wrap ``apps.wa_phone`` out of
``~/wa-phone-outreach``, which is a disposable agent worktree: a ``git worktree remove`` or a branch
checkout swaps every signature under us without a warning, and our service would stop being able to
answer a candidate because someone else's agent tidied up. We own this rail end to end, so the adb
technique is ours to hold. The technique below was read off that tree and reimplemented; nothing is
imported from it, and this file keeps working if that directory disappears tonight.

WHAT IS DELIBERATELY DIFFERENT FROM WHAT WE READ THERE:
  * their send path ends in ``Bubble("out", text, "", "unverified", 0)`` -- "composer empty after
    send but bubble not matched, treating as sent". It fired on 2 of 23 live sends. Here an
    unmatched bubble is a DriverError; the executor turns that into a 504 that is never auto-resent.
  * their open_chat accepts a header that is a saved contact's NAME without checking whose name it
    is (``if not expect_name and _digits(header)`` -- a name has no digits, so the guard is skipped).
    Here a name header is verified against the handset's own address book, and an unverifiable
    header refuses before anything is typed.
  * their Device screenshots before every tap, swipe, key and type, with no off switch (91 MB from
    16 h of one test chat). Here a screenshot is an escalation artefact and nothing else.
  * their flock is held across the brain call. Here the lock is taken per bubble by the executor and
    nothing slow happens inside it.

WHAT IS ADOPTED WHOLE, because one handset means one house rule and theirs is already running on it:
the pinned serial, the forbidden serial, the lock path, the ADBKeyboard IME, and the typing speed
range from their PACING (3.2-5.5 chars/sec). The pacing fuse itself lives in bridge/governor.py.

THE IME IS BORROWED PROPERTY. The active keyboard on this phone is SwiftKey and a human picks the
phone up. Every code path that switches to ADBKeyboard restores the previous IME in a ``finally``,
including the path where the send raises -- ``adb_keyboard()`` is the only way to type here and it
is a context manager for exactly that reason.
"""
from __future__ import annotations

import base64
import fcntl
import os
import random
import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import driver as D
from . import inbound as I

# --- the handset, pinned ------------------------------------------------------------------------
#: Huawei P30 Lite "01", the phone that holds the WhatsApp account.
SERIAL = "L2N4C19B14054874"
#: "02". It runs the ChatGPT farm. Constructing a driver for it raises before adb is even called.
FORBIDDEN_SERIALS = frozenset({"L2N4C19B14054035"})
ADB_CANDIDATES = (
    Path.home() / "agentos-phone/bin/platform-tools/adb",
    Path("/usr/bin/adb"),
    Path("/usr/local/bin/adb"),
)
WHATSAPP = "com.whatsapp"
ADB_IME = "com.android.adbkeyboard/.AdbIME"
#: The same flock their daemon takes, on purpose: two drivers on one phone must queue, not race.
LOCK_PATH = Path(os.path.expanduser("~/.local/share/wa_phone/huawei01.lock"))
#: The handset is set to Europe/Berlin and the clock on a bubble is the one a human reads.
DEVICE_TZ = "Europe/Berlin"

#: Their PACING["chars_per_sec"], adopted. Typing at machine speed is the one tell that cannot be
#: explained away on a consumer account.
CHARS_PER_SEC = (3.2, 5.5)
PAUSE_BEFORE_SEND = (0.8, 2.5)
PAUSE_AFTER_OPEN = (1.5, 4.0)

#: WhatsApp 2.26.36 (German UI) resource ids, verified on this handset.
RID_COMPOSER = "entry"
RID_SEND = "send"
RID_HEADER = "conversation_contact_name"
RID_MESSAGE = "message_text"
RID_DATE = "date"
RID_STATUS = "status"
RID_DIALOG_BUTTON = "button1"

#: The chat list, read off L2N4C19B14054874 on 2026-09-21 with WhatsApp 2.26.36.74 (TASK-147).
RID_CHAT_ROW = "contact_row_container"
RID_ROW_NAME = "conversations_row_contact_name"
RID_ROW_DATE = "conversations_row_date"
RID_ROW_PREVIEW = "single_msg_tv"
RID_ROW_UNREAD = "conversations_row_message_count"
RID_ARCHIVE_HEADER = "conversations_archive_header"
RID_ARCHIVE_COUNT = "archive_row_counter"
RID_OVERFLOW = "menuitem_overflow"
#: The selection action bar that a long press raises. The delete verb has its own id, so it is
#: found without reading a label; "Chat leeren" lives one level down in the overflow and has not.
RID_CAB_DELETE = "menuitem_conversations_delete"
RID_CAB_CLOSE = "action_mode_close_button"
RID_MENU_TITLE = "title"
LABEL_CLEAR_CHAT = "Chat leeren"
#: The clear sheet: a radio for the scope and one primary button. "Alle Nachrichten" is the scope
#: this operation means -- "Nur Mediendateien" would leave the text behind and still say it worked.
RID_CLEAR_ALL_ROW = "dialog_clear_messages_all_container_layout"
RID_CLEAR_ALL_RADIO = "dialog_clear_messages_all_text"
RID_CLEAR_STARRED = "media_clear_chats_bottom_sheet_dialog_item_layout_checkbox"
RID_CLEAR_STARRED_ROW = "media_clear_chats_bottom_sheet_dialog_starred_messages_checkbox"
RID_PRIMARY_BUTTON = "primary_button"
#: The delete dialog: a plain AlertDialog, positive on button1 ("Chat löschen"), negative button2.
RID_ALERT_TITLE = "alertTitle"
#: The new-chat button. It is on the chat list and on no other screen, so it is how this code
#: proves the list is really in front instead of assuming it: a handset with no chats at all draws
#: no rows, and "no rows" must never be what an unreadable dump looks like.
RID_NEW_CHAT_FAB = "fab"

#: A long press. ``input swipe x y x y`` is delivered as a tap on this build and raises no
#: selection, which is why the press is two motion events with a hold between them.
LONG_PRESS_HOLD_SEC = 1.2
#: How long a chat-list screen is given to redraw after a tap on a menu or a dialog button.
UI_SETTLE_SEC = 1.5

#: How long we re-read the thread looking for the bubble we just typed, before calling it unverified.
BUBBLE_APPEAR_SEC = 30.0
#: Composer placeholder text on the German UI: an empty composer, not leftover text.
COMPOSER_EMPTY = ("", "Nachricht")

#: What a bubble's own timestamp looks like. A ``date`` node that is NOT this is a day divider.
_CLOCK_RE = re.compile(r"\d{1,2}:\d{2}\Z")


def _is_clock(text):
    return bool(_CLOCK_RE.match(str(text or "").strip()))


class AdbUnavailable(D.DriverError):
    """adb is missing, or the handset is not in ``device`` state. Nothing was typed."""


@dataclass(frozen=True)
class Node:
    cls: str
    rid: str
    text: str
    desc: str
    bounds: tuple
    clickable: bool
    pkg: str = ""
    #: uiautomator's own ``checked``. Read so a checkbox is only tapped when tapping it moves it the
    #: way we want: a toggle tapped blind is a coin toss, and one of these toggles is "delete the
    #: starred messages too" (TASK-147).
    checked: bool = False

    @property
    def center(self):
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def short_rid(self):
        return self.rid.split("/")[-1] if self.rid else ""

    @property
    def label(self):
        return self.text or self.desc


def parse_ui_xml(out):
    """-> [Node] from a uiautomator dump. Junk in, empty list out: the caller retries the dump."""
    text = out or ""
    start = text.find("<?xml")
    if start < 0:
        start = text.find("<hierarchy")
    if start < 0:
        return []
    try:
        root = ET.fromstring(text[start:])
    except ET.ParseError:
        return []
    nodes = []
    for element in root.iter("node"):
        box = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", element.get("bounds") or "")
        if not box:
            continue
        nodes.append(Node(cls=element.get("class") or "", rid=element.get("resource-id") or "",
                          text=element.get("text") or "", desc=element.get("content-desc") or "",
                          bounds=tuple(int(v) for v in box.groups()),
                          clickable=element.get("clickable") == "true",
                          pkg=element.get("package") or "",
                          checked=element.get("checked") == "true"))
    return nodes


def inside(outer, inner):
    """-> True when ``inner``'s centre lies in ``outer``'s box. A uiautomator dump is flat, so this
    is how a row's own name, date and badge are told apart from the next row's."""
    x, y = inner.center
    return outer.bounds[0] <= x <= outer.bounds[2] and outer.bounds[1] <= y <= outer.bounds[3]


def _overlap(have, page):
    """-> how many of ``page``'s leading rows are the tail of ``have`` already.

    Pure, and the whole of the scroll's identity logic. Rows are matched on (title, date column)
    POSITIONALLY -- the same pair that is a useless identity on its own is a perfectly good one
    once it has to line up as a run, which is what makes two chats called Anna, both stamped
    GESTERN and pages apart, stay two rows.
    """
    if not have:
        return 0
    key = lambda rows: [(r.title, r.stamp) for r in rows]        # noqa: E731
    for size in range(min(len(have), len(page)), 0, -1):
        if key(have[-size:]) == key(page[:size]):
            return size
    raise D.DriverError(
        "the chat list moved further than one screen between swipes: its pages do not overlap and "
        "which rows have already been read cannot be established")


def covers(node, point):
    """-> True when ``point`` (an x, y we tapped or pressed) lies in ``node``'s box."""
    x, y = point
    return node.bounds[0] <= x <= node.bounds[2] and node.bounds[1] <= y <= node.bounds[3]


def parse_contacts(out):
    """-> [(digits, display name)] from ``content query`` on the contacts provider.

    One row per line, ``Row: 0 display_name=Ivan, data1=+49 152 ...``. Pure so a test can feed it a
    captured row without a phone in the room.
    """
    book = []
    for line in (out or "").splitlines():
        name = re.search(r"display_name=(.*?)(?:, data1=|$)", line)
        number = re.search(r"data1=(.+?)\s*$", line)
        if not name or not number:
            continue
        cleaned = I.digits(number.group(1))
        if len(cleaned) >= 8:
            book.append((cleaned, name.group(1).strip()))
    return book


def parse_chat_rows(nodes, *, archived=False):
    """-> [(row node, D.ChatRow)] for the chat list on screen, top row first.

    Pure, so the row shape is testable off a captured dump. The preview line (``single_msg_tv``) is
    read for its EXISTENCE only: it is the last message of the conversation, and a listing call is
    not a way to read messages.

    The unread badge is a content-desc, "1 ungelesene Nachricht" / "11 ungelesene Nachrichten", and
    the integer in front of it is the count. The archive folder's own row is not a chat and is not
    returned here.
    """
    rows = []
    for row in sorted(find(nodes, rid=RID_CHAT_ROW), key=lambda n: n.bounds[1]):
        mine = [n for n in nodes if n is not row and inside(row, n)]
        names = [n for n in mine if n.short_rid == RID_ROW_NAME]
        if not names:
            continue
        stamps = [n for n in mine if n.short_rid == RID_ROW_DATE]
        badges = [n for n in mine if n.short_rid == RID_ROW_UNREAD]
        unread = re.search(r"(\d+)", badges[0].label) if badges else None
        rows.append((row, D.ChatRow(
            title=names[0].text.strip(),
            unread=int(unread.group(1)) if unread else 0,
            stamp=stamps[0].text.strip() if stamps else "",
            archived=archived,
            has_preview=any(n.short_rid == RID_ROW_PREVIEW and n.text.strip() for n in mine))))
    return rows


def find(nodes, *, rid=None, text=None, contains=None, clickable=None):
    hits = []
    for node in nodes:
        if rid is not None and node.short_rid != rid and node.rid != rid:
            continue
        if text is not None and node.text != text:
            continue
        if contains is not None and contains.lower() not in node.label.lower():
            continue
        if clickable is not None and node.clickable != clickable:
            continue
        hits.append(node)
    return hits


class PhoneLock:
    """flock on huawei01.lock. Non-blocking with a deadline, so a stuck holder is a 503, not a hang."""

    def __init__(self, path=LOCK_PATH, timeout=D.LOCK_TIMEOUT_SEC):
        self.path = Path(path)
        self.timeout = float(timeout)
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    self._fh.close()
                    self._fh = None
                    raise D.PhoneBusy(f"phone lock busy for {self.timeout:.0f}s: {self.path}")
                time.sleep(0.5)
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"{datetime.now().astimezone().isoformat()} pflege-wa-bridge pid={os.getpid()}\n")
        self._fh.flush()
        return self

    def __exit__(self, *exc):
        if self._fh:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


class Adb:
    """The command surface. One process per call; no persistent shell to go stale."""

    def __init__(self, serial=SERIAL, *, adb=None, log=None):
        if serial in FORBIDDEN_SERIALS:
            raise D.DriverError(f"{serial} is the ChatGPT farm handset -- refusing")
        if serial != SERIAL:
            raise D.DriverError(f"this rail drives {SERIAL} only, got {serial}")
        self.serial = serial
        self.binary = str(adb) if adb else next((str(p) for p in ADB_CANDIDATES if p.exists()), "adb")
        self._log = log or (lambda msg: None)

    def log(self, msg):
        self._log(msg)

    def run(self, *args, timeout=60):
        """-> the finished process. An adb that does not return in ``timeout`` is a DriverError.

        subprocess raises TimeoutExpired, which is nobody's DriverError: it escaped the driver
        boundary raw and reached the server's generic handler as a 500 executor_error -- wrong on
        the send path (a timeout on `input text` is send_unconfirmed, keys may have been pressed)
        and wrong on the destructive path (park() and the verification rescan are both adb calls
        made after a conversation has already been destroyed). Every failure this class can have
        is a DriverError, and the callers above already know what to do with one.
        """
        try:
            return subprocess.run([self.binary, "-s", self.serial, *args],
                                  capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise D.DriverError(f"adb {args[0]} did not return within {timeout:.0f}s") from exc

    def shell(self, cmd, *, timeout=40):
        return self.run("shell", cmd, timeout=timeout).stdout

    def connected(self):
        try:
            out = subprocess.run([self.binary, "devices"], capture_output=True, text=True,
                                 timeout=30, check=False).stdout
        except subprocess.TimeoutExpired as exc:
            raise AdbUnavailable("adb devices did not return within 30s") from exc
        return any(line.split("\t")[0] == self.serial and line.strip().endswith("device")
                   for line in out.splitlines()[1:])

    def require_device(self):
        if not self.connected():
            raise AdbUnavailable(f"{self.serial} is not in adb 'device' state")

    # --- screen ---------------------------------------------------------------------------------
    def awake(self):
        return "mWakefulness=Awake" in self.shell("dumpsys power | grep -m1 mWakefulness")

    def wake(self):
        if self.awake():
            return
        self.key("KEYCODE_WAKEUP")
        time.sleep(0.6)
        self.shell("input swipe 540 1900 540 900 250")
        time.sleep(0.6)

    def focus(self):
        out = self.shell("dumpsys activity activities 2>/dev/null | "
                         "grep -m1 -E 'ResumedActivity|mFocusedActivity'")
        hit = re.search(r"u0 ([A-Za-z0-9_.]+/[A-Za-z0-9_.$]+)", out)
        return hit.group(1) if hit else out.strip()[:100]

    def dump(self, *, tries=3, required=True):
        """-> [Node] for what is on screen. Raises after ``tries`` unless ``required=False``.

        ``required=True`` is the default because of what an empty list means downstream (TASK-146):
        ``uiautomator`` answering "ERROR: could not get idle state." and a chat with nothing in it
        both parse to ``[]``, and on the inbound path those two are a candidate's message being
        lost versus a quiet chat. Three tries is already the retry; failing after them is the
        honest end of it.

        ``required=False`` is for the pollers that have a deadline of their own and for which an
        unreadable frame is just a frame to re-take (``wait_for``).
        """
        for _ in range(tries):
            out = self.shell("uiautomator dump /sdcard/pflege_ui.xml >/dev/null 2>&1; "
                             "cat /sdcard/pflege_ui.xml", timeout=40)
            nodes = parse_ui_xml(out)
            if nodes:
                return nodes
            time.sleep(0.7)
        if required:
            raise AdbUnavailable(f"uiautomator returned nothing readable {tries} times in a row")
        return []

    def wait_for(self, predicate, *, timeout=10.0, poll=0.7):
        deadline = time.monotonic() + timeout
        nodes = []
        while True:
            nodes = self.dump(required=False)
            if predicate(nodes):
                return nodes
            if time.monotonic() >= deadline:
                return nodes
            time.sleep(poll)

    # --- input ----------------------------------------------------------------------------------
    def tap(self, x, y):
        # a few pixels of jitter: never the same pixel twice.
        self.shell(f"input tap {x + random.randint(-6, 6)} {y + random.randint(-4, 4)}")
        time.sleep(0.7)

    def tap_node(self, node):
        self.tap(*node.center)

    def long_press(self, x, y, *, hold=LONG_PRESS_HOLD_SEC):
        """A real long press. ``input swipe x y x y`` arrives as a tap on this build -- measured on
        the handset, 2026-09-21: it opened nothing and raised no selection -- so the press is a DOWN,
        a hold, and an UP.  A long press on a chat row is what raises the selection action bar."""
        self.shell(f"input motionevent DOWN {x} {y}")
        time.sleep(hold)
        self.shell(f"input motionevent UP {x} {y}")
        time.sleep(0.8)

    def long_press_node(self, node):
        self.long_press(*node.center)

    def key(self, name):
        self.shell(f"input keyevent {name}")
        time.sleep(0.5)

    def screenshot(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            subprocess.run([self.binary, "-s", self.serial, "exec-out", "screencap", "-p"],
                           stdout=fh, timeout=30, check=False)
        return str(path)

    # --- the keyboard, which is borrowed ----------------------------------------------------------
    def current_ime(self):
        return self.shell("settings get secure default_input_method").strip()

    def set_ime(self, ime):
        self.shell(f"ime set {ime}")
        time.sleep(0.6)
        return self.current_ime() == ime

    @contextmanager
    def adb_keyboard(self):
        """Switch to ADBKeyboard for the body of the ``with``, then put the human's IME back.

        The restore is in a ``finally`` and runs even when the body raises, because the failure mode
        we are protecting against is exactly "the send blew up and the phone is left with a keyboard
        that types nothing when a person taps it".
        """
        previous = self.current_ime()
        if previous != ADB_IME:
            if ADB_IME not in self.shell("ime list -s"):
                self.shell(f"ime enable {ADB_IME}")
            if not self.set_ime(ADB_IME):
                raise D.DriverError(
                    f"ADBKeyboard ({ADB_IME}) would not activate; refusing to type through "
                    "`input text`, which mangles German umlauts")
        try:
            yield
        finally:
            if previous and previous != ADB_IME:
                self.set_ime(previous)

    def type_verbatim(self, chunk):
        blob = base64.b64encode(chunk.encode("utf-8")).decode("ascii")
        self.shell(f"am broadcast -a ADB_INPUT_B64 --es msg '{blob}' >/dev/null", timeout=60)

    def clear_composer(self):
        self.shell("am broadcast -a ADB_CLEAR_TEXT >/dev/null")
        time.sleep(0.4)

    def type_human(self, text, *, rng=random):
        """Word-sized chunks at a speed drawn once per message. Requires the ADBKeyboard context."""
        speed = rng.uniform(*CHARS_PER_SEC)
        words = text.split(" ")
        i = 0
        while i < len(words):
            take = rng.choice((1, 1, 2, 2, 3))
            chunk = " ".join(words[i:i + take])
            if i + take < len(words):
                chunk += " "
            self.type_verbatim(chunk)
            delay = len(chunk) / speed
            if chunk.rstrip().endswith((",", ".", "?", "!")):
                delay += rng.uniform(0.25, 0.9)
            if rng.random() < 0.07:
                delay += rng.uniform(0.6, 1.8)
            time.sleep(max(0.15, delay))
            i += take


class AdbDriver(D.PhoneDriver):
    """The five verbs of bridge.driver.PhoneDriver, spoken to a real handset."""

    def __init__(self, *, shots_dir, adb=None, serial=SERIAL, lock_path=LOCK_PATH,
                 device_tz=DEVICE_TZ, log=None, rng=None):
        self.adb = adb if isinstance(adb, Adb) else Adb(serial, adb=adb, log=log)
        self.shots_dir = Path(shots_dir)
        self.lock_path = Path(lock_path)
        self.tz = ZoneInfo(device_tz)
        self.rng = rng or random.Random()
        self._log = log or (lambda msg: None)
        self._shot_n = 0
        self._contacts = None       # address book cache, filled on first name-header
        self._open_phone = None     # the number of the chat we verified open

    def log(self, msg):
        self._log(msg)

    # --- the flock ---------------------------------------------------------------------------------
    @contextmanager
    def lock(self, *, timeout=D.LOCK_TIMEOUT_SEC):
        with PhoneLock(self.lock_path, timeout=timeout):
            yield

    # --- who is on the other side ------------------------------------------------------------------
    def address_book(self, *, refresh=False):
        """-> [(full digits, display name)] off the handset's own contacts provider.

        Only read when a notification title or a chat header is a NAME, which is the case this has
        to answer: the phone knows which number that name belongs to and we do not. Cached for the
        life of the process and re-read on ``refresh``; a contact added mid-run is a restart away.
        """
        if self._contacts is not None and not refresh:
            return self._contacts
        out = self.adb.shell(
            "content query --uri content://com.android.contacts/data/phones "
            "--projection display_name:data1", timeout=60)
        self._contacts = parse_contacts(out)
        return self._contacts

    def resolve_counterparty(self, title):
        """-> E.164 for a notification title, or "" when the handset cannot say who that is.

        A display name shared by two contacts is UNRESOLVABLE, not a coin toss (TASK-146). Taking
        the first row silently answered candidate A with a reply to candidate B's message and keyed
        the whole opt-out story on the wrong human. It raises rather than returning "", so the
        journal says which of the two things went wrong.
        """
        text = str(title or "").strip()
        if I.looks_like_number(text):
            return "+" + I.digits(text)
        if not text:
            return ""
        matches = sorted({number for number, name in self.address_book() if name == text})
        if len(matches) > 1:
            raise I.Unresolvable(f"{len(matches)} contacts share this display name")
        return "+" + matches[0] if matches else ""

    def name_for_tail(self, tail):
        """-> the display name the handset stores for a number ending in ``tail``, or ""."""
        for number, name in self.address_book():
            if number.endswith(tail):
                return name
        return ""

    # --- open a chat, and prove it is the right one ---------------------------------------------------
    def open_chat(self, phone):
        """-> the header WhatsApp drew. Types NOTHING, on any path, including the refusals."""
        self.adb.require_device()
        wanted = "+" + I.digits(phone)
        tail = I.digits(wanted)[-8:]
        if len(tail) < 8:
            raise D.DriverError(f"{len(tail)} digits is not enough to identify a thread")
        self._open_phone = None
        self.adb.wake()
        self.adb.shell(f"am start -a android.intent.action.SENDTO "
                       f"-d smsto:{shlex.quote(wanted)} {WHATSAPP} >/dev/null 2>&1")
        nodes = self.adb.wait_for(
            lambda ns: bool(find(ns, rid=RID_HEADER) or find(ns, rid=RID_DIALOG_BUTTON)), timeout=12)
        if self._dismiss_dialog(nodes):
            nodes = self.adb.wait_for(lambda ns: bool(find(ns, rid=RID_HEADER)), timeout=10)
        header = find(nodes, rid=RID_HEADER)
        if not header:
            raise D.DriverError(f"conversation did not open (focus={self.adb.focus()})")
        drawn = header[0].text.strip()
        self._require_thread(drawn, wanted, tail)
        self._open_phone = wanted
        time.sleep(self.rng.uniform(*PAUSE_AFTER_OPEN))
        return drawn

    def _require_thread(self, drawn, wanted, tail):
        """The guard their version does not have: a NAME header is checked against the address book.

        Three outcomes and no fourth: the header is the number we asked for, the header is the name
        the handset has stored for that number, or we refuse and type nothing.
        """
        if I.digits(drawn) and I.digits(drawn)[-8:] == tail:
            return
        if I.digits(drawn):
            raise D.DriverError("wrong thread: the header is a different number")
        stored = self.name_for_tail(tail)
        if stored and stored == drawn:
            return
        raise D.DriverError(
            "wrong thread: the header is a name the address book does not tie to this number "
            f"(stored={'yes' if stored else 'no'})")

    def _dismiss_dialog(self, nodes):
        """WhatsApp's 'Chat mit +49...?' confirmation for an unsaved number. Tapping it opens the
        chat; it types nothing and sends nothing."""
        for label in ("OK", "Chat", "Fortfahren", "Weiter", "Continue"):
            hit = (find(nodes, rid=RID_DIALOG_BUTTON, text=label)
                   or [n for n in find(nodes, text=label, clickable=True) if n.pkg == WHATSAPP])
            if hit:
                self.adb.tap_node(hit[0])
                return True
        return False

    # --- read what is on screen ---------------------------------------------------------------------
    def read_bubbles(self):
        """-> [BubbleView] for the VISIBLE bubbles, oldest first. Does not scroll."""
        return self._bubbles(self.adb.dump())

    def _bubbles(self, nodes):
        return [view for _y, view in self._placed_bubbles(nodes)]

    def _placed_bubbles(self, nodes):
        """-> [(top y, BubbleView)] oldest first. The y is what ``day_separator_y`` is compared to."""
        width = 1080
        for node in nodes:
            if node.short_rid == "conversation_layout" or node.cls.endswith("FrameLayout"):
                width = max(width, node.bounds[2])
        texts = [n for n in nodes if n.short_rid == RID_MESSAGE]
        dates = [n for n in nodes if n.short_rid == RID_DATE and _is_clock(n.text)]
        states = [n for n in nodes if n.short_rid == RID_STATUS]
        out = []
        for node in sorted(texts, key=lambda n: n.bounds[1]):
            x1, y1, x2, y2 = node.bounds
            # Outgoing bubbles are drawn hard against the right edge; incoming hug the left.
            direction = "out" if x2 > width * 0.9 else "in"
            near = [d for d in dates if y1 <= d.bounds[1] <= y2 + 80]
            clock = min(near, key=lambda d: abs(d.bounds[1] - y2)).text if near else ""
            tick = ""
            if direction == "out":
                close = [s for s in states if abs(s.bounds[1] - y2) < 90]
                tick = close[0].desc if close else ""
            out.append((y1, D.BubbleView(direction, node.text, clock, tick)))
        return out

    @staticmethod
    def day_separator_y(nodes):
        """-> the top y of the LOWEST day separator on screen, or None when none is drawn.

        WhatsApp puts a ``date`` TextView on every bubble (always 'HH:MM' -- verified on this
        handset, 2026-09-21, including the one that belongs to a voice note rather than to a text
        row) and the same id on the day divider between two days, where the text is a day name
        instead ('HEUTE', 'GESTERN', '20. September'). The text is therefore the discriminator, not
        a resource id we would have to guess at.
        """
        ys = [n.bounds[1] for n in nodes if n.short_rid == RID_DATE and not _is_clock(n.text)]
        return max(ys) if ys else None

    # --- send one bubble, and prove it landed ---------------------------------------------------------
    def send_bubble(self, text):
        """-> the BubbleView we read back off the thread. Raises rather than guess.

        The caller holds the flock and has already had open_chat verify the thread. If the bubble
        cannot be found in the thread afterwards, that is a DriverError and the executor records it
        as unconfirmed -- it is never reported as sent.
        """
        body = text.strip()
        if not body:
            raise D.DriverError("empty body reached the driver")
        if self._open_phone is None:
            raise D.DriverError("send_bubble without a verified open chat")
        nodes = self.adb.dump()
        composer = find(nodes, rid=RID_COMPOSER)
        if not composer:
            raise D.DriverError("composer not found -- the conversation is not on screen")
        # How many bubbles with THIS body are already on the thread before we type a character
        # (TASK-146). Several send bodies are constants -- api.MEDIA_REPLY fires for every
        # unreadable media message and C.FOLLOWUP_NUDGE_DE is identical on every nudge tier -- so
        # "a bubble with this body exists" was never evidence that OUR bubble exists. A previous
        # turn's identical bubble already carries a tick, which made require_tick accept it and the
        # executor write ``sent`` with a clock from an earlier turn. Now the match has to be one
        # that was not there before.
        already = self._count_matching(self._bubbles(nodes), D.body_sha256(body))
        with self.adb.adb_keyboard():
            if composer[0].text not in COMPOSER_EMPTY:
                self.adb.tap_node(composer[0])
                self.adb.clear_composer()
            self.adb.tap_node(composer[0])
            self.adb.type_human(body, rng=self.rng)
            time.sleep(self.rng.uniform(*PAUSE_BEFORE_SEND))
            nodes = self.adb.dump()
            button = find(nodes, rid=RID_SEND)
            if not button:
                raise D.DriverError("send button not visible after typing")
            self.adb.tap_node(button[0])
        return self._verify(body, already)

    @staticmethod
    def _count_matching(bubbles, want_sha256):
        return sum(1 for b in bubbles
                   if b.direction == "out" and D.body_sha256(b.text) == want_sha256)

    def _verify(self, body, already=0):
        """Re-read the thread until a bubble with THIS body that was NOT there before is on it.

        ``already`` is the count taken before the send tap. A body hash alone cannot tell
        "delivered now" from "delivered last Tuesday", and this module exists to refuse exactly
        that class of lie -- so the test is that the count went up, and the bubble we return is the
        bottom-most one, which is the one that was just drawn.
        """
        want = D.body_sha256(body)
        deadline = time.monotonic() + BUBBLE_APPEAR_SEC
        hid_keyboard = False
        while True:
            nodes = self.adb.dump()
            mine = [b for b in self._bubbles(nodes)
                    if b.direction == "out" and D.body_sha256(b.text) == want]
            if len(mine) > already:
                return mine[-1]
            if not hid_keyboard:
                # The keyboard covers the bottom of the list, which is where a fresh bubble is.
                # BACK closes the IME, it does not leave the chat.
                self.adb.key("KEYCODE_BACK")
                hid_keyboard = True
                continue
            if time.monotonic() >= deadline:
                raise D.DriverError(
                    f"the bubble was not on the thread {BUBBLE_APPEAR_SEC:.0f}s after send")
            time.sleep(1.0)

    # --- inbound ---------------------------------------------------------------------------------------
    def pull_inbound(self):
        """-> ([InboundMessage] with ids, [(title, reason)] we could not mint).

        WhatsApp only posts MessagingStyle notifications while it is in the background, which is why
        park() puts the phone on the launcher after every action.
        """
        dump = self.adb.shell("dumpsys notification --noredact 2>/dev/null", timeout=60)
        return I.notification_messages(dump, tz=self.tz, resolve=self.resolve_counterparty)

    def read_open_thread(self, phone):
        """-> ([InboundMessage], [(title, reason)]) for the chat that is open right now.

        The second door. Its ids are identical to the notification door's for the same message, by
        construction (bridge/inbound.py mints on the local minute) -- but ONLY if the date is the
        same, and a bubble carries no date. So which bubbles are from today is derived rather than
        assumed (TASK-146):

        CALLED ONLY RIGHT AFTER OUR OWN SEND, which is what makes the derivation sound. Our own
        bubble is the newest thing in the chat and it is seconds old, so the bottom of the visible
        thread is today. WhatsApp draws a divider at every day change, so if a divider is visible
        the bubbles BELOW the lowest one are today's and the ones above it are not; if no divider
        is visible there was no day change in the visible window at all, so all of it is today.
        A bubble that cannot be placed on today is reported unresolved -- it is not stamped with a
        date that would mint an id the shade never minted, and be answered as a new message.
        """
        nodes = self.adb.dump()
        placed = self._placed_bubbles(nodes)
        cut = self.day_separator_y(nodes)
        older = 0 if cut is None else sum(1 for y, _view in placed if y < cut)
        today = datetime.now(self.tz).strftime("%Y-%m-%d")
        return I.thread_messages([view for _y, view in placed],
                                 counterparty="+" + I.digits(phone), local_date=today, older=older)

    # --- the chat list, and the two destructive verbs (TASK-147) -----------------------------------------
    def _chat_list(self, *, archived=False):
        """Bring the chat list to the front and -> its nodes, scrolled to the top.

        Proves the screen instead of assuming it. WhatsApp resumes wherever it was left, which may
        be a conversation, and a dump taken there parses to zero chat rows -- indistinguishable from
        an empty handset if nobody checks.
        """
        self.adb.require_device()
        self.adb.wake()
        nodes = []
        for _ in range(4):
            focus = self.adb.focus()
            if focus.endswith("Conversation"):
                self.adb.key("KEYCODE_BACK")
                continue
            if "HomeActivity" not in focus:
                self.adb.shell(f"monkey -p {WHATSAPP} -c android.intent.category.LAUNCHER 1 "
                               ">/dev/null 2>&1")
                time.sleep(1.5)
                continue
            nodes = self.adb.wait_for(lambda ns: bool(find(ns, rid=RID_NEW_CHAT_FAB)), timeout=12)
            if find(nodes, rid=RID_NEW_CHAT_FAB):
                break
        if not find(nodes, rid=RID_NEW_CHAT_FAB):
            raise D.DriverError(f"the chat list would not come to the front "
                                f"(focus={self.adb.focus()})")
        nodes = self._scroll_to_top(nodes)
        if archived:
            nodes = self._enter_archive(nodes)
        return nodes

    def _scroll_to_top(self, nodes):
        """Swipe down until the list stops changing. The stop condition is the list's own: a swipe
        that moves nothing. There is no page count here to invent."""
        while True:
            before = [row.title for _n, row in parse_chat_rows(nodes)]
            self.adb.shell("input swipe 540 700 540 1700 400")
            time.sleep(0.9)
            nodes = self.adb.dump()
            if [row.title for _n, row in parse_chat_rows(nodes)] == before:
                return nodes

    def _enter_archive(self, nodes):
        header = find(nodes, rid=RID_ARCHIVE_HEADER)
        if not header:
            raise D.DriverError("there is no archive folder on this chat list")
        self.adb.tap_node(header[0])
        nodes = self.adb.wait_for(lambda ns: bool(find(ns, rid=RID_CHAT_ROW)), timeout=10)
        if not find(nodes, rid=RID_CHAT_ROW):
            raise D.DriverError("the archive folder opened and drew no rows")
        return nodes

    def _rows_everywhere(self, nodes, *, archived):
        """-> [ChatRow] from here to the end of the list, scrolling.

        THE PAGES ARE STITCHED, NOT DEDUPED. A swipe moves the list by less than a screen, so each
        page repeats the tail of the one before it, and the pages are joined on that overlap --
        the longest suffix of what we have that is also the head of the new page. Identity by
        (title, date) would be the easy key and it is the wrong one: "GESTERN" or "18.08.26" is
        what every row older than today carries, so two different people with one display name
        collapse into a single row and the handset silently reports one chat where it has two.
        That pair is precisely what the destructive path has to see in order to refuse.

        A page that does not overlap at all is a list that moved further than one screen (an
        inbound message reordering it mid-scroll is the ordinary way) and the rows cannot be joined
        without guessing where they belong. That is a DriverError, not a best effort.
        """
        out = []
        while True:
            page = [row for _node, row in parse_chat_rows(nodes, archived=archived)]
            fresh = page[_overlap(out, page):]
            out += fresh
            if not fresh:
                return out
            self.adb.shell("input swipe 540 1600 540 700 400")
            time.sleep(0.9)
            nodes = self.adb.dump()

    def list_chats(self, *, include_archived=True):
        nodes = self._chat_list()
        rows = self._rows_everywhere(nodes, archived=False)
        if include_archived and find(nodes, rid=RID_ARCHIVE_HEADER):
            rows += self._rows_everywhere(self._chat_list(archived=True), archived=True)
            self.adb.key("KEYCODE_BACK")
        return rows

    def _locate(self, title, *, archived=False):
        """-> (nodes, row node, ChatRow) for the one row carrying this title, scrolling to find it.

        PII: the title is a contact's name and never reaches a message. The refusals below say how
        many rows answered, not which.
        """
        nodes = self._chat_list(archived=archived)
        last = None
        while True:
            hits = [(n, r) for n, r in parse_chat_rows(nodes, archived=archived) if r.title == title]
            if len(hits) > 1:
                raise D.DriverError(f"{len(hits)} rows on screen carry this title: which "
                                    "conversation is meant is not guessable")
            if hits:
                return nodes, hits[0][0], hits[0][1]
            # The list's own end signal: a swipe that changes nothing. Read as an ordered page and
            # not as a set of (title, date) pairs -- on a list where every older row is stamped
            # "GESTERN" a set stops being able to tell one screen from the next.
            here = [(r.title, r.stamp) for _n, r in parse_chat_rows(nodes, archived=archived)]
            if here == last:
                raise D.DriverError("no chat row with this title is on the list")
            last = here
            self.adb.shell("input swipe 540 1600 540 700 400")
            time.sleep(0.9)
            nodes = self.adb.dump()

    def open_chat_row(self, title, *, archived=False):
        """Open a chat by tapping its row. -> the header WhatsApp drew.

        ``_open_phone`` stays None on purpose: a chat opened by name is readable and NOT sendable.
        ``send_bubble`` refuses without a chat opened by number, because the number is the only
        identity ``_require_thread`` can check a header against.
        """
        nodes, node, _row = self._locate(title, archived=archived)
        self._open_phone = None
        self.adb.tap_node(node)
        nodes = self.adb.wait_for(lambda ns: bool(find(ns, rid=RID_HEADER)), timeout=12)
        header = find(nodes, rid=RID_HEADER)
        if not header:
            raise D.DriverError(f"the row did not open a conversation (focus={self.adb.focus()})")
        drawn = header[0].text.strip()
        if drawn != title:
            raise D.DriverError("wrong thread: the row opened a conversation whose header is a "
                                "different name")
        time.sleep(self.rng.uniform(*PAUSE_AFTER_OPEN))
        return drawn

    def _selection_up(self, nodes):
        return bool(find(nodes, rid=RID_CAB_CLOSE) or find(nodes, rid=RID_CAB_DELETE))

    def _leave_selection(self):
        """Back out of the selection action bar, however this ended. A chat left selected on a
        handset a human picks up is one stray tap away from being the wrong chat's delete."""
        for _ in range(3):
            if not self._selection_up(self.adb.dump(required=False)):
                return
            self.adb.key("KEYCODE_BACK")

    def _select_row(self, title, *, archived):
        """Long press the named row and prove the press landed on it. -> (nodes, ChatRow).

        The press is aimed with bounds from a dump taken a moment ago, and this list reorders on
        its own: one inbound message moves that chat to the top and shifts every row below it by a
        row's height. Neither the selection action bar nor the delete dialog ("Diesen Chat
        löschen?") names a chat, so nothing further down the menu can notice. The answering dump
        is the evidence and it is already in hand -- the rows are drawn behind the action bar -- so
        the row under the pressed POINT is read back and has to still be the one that was named.
        """
        nodes, node, row = self._locate(title, archived=archived)
        point = node.center
        self.adb.long_press(*point)
        nodes = self.adb.wait_for(self._selection_up, timeout=8)
        if not self._selection_up(nodes):
            raise D.DriverError("the long press raised no selection action bar")
        pressed = [r for n, r in parse_chat_rows(nodes, archived=archived) if covers(n, point)]
        if [r.title for r in pressed] != [title]:
            self._leave_selection()
            raise D.DriverError(
                f"the long press did not land on the row that was named: {len(pressed)} row(s) "
                "cover the pressed point now and the list has moved under it")
        return nodes, row

    def clear_chat_history(self, title, *, archived=False, include_starred=True):
        """Long press -> overflow -> 'Chat leeren' -> the scope sheet -> confirm. Keeps the chat.

        The scope is chosen rather than accepted: the sheet also offers "Nur Mediendateien", which
        would leave every word of the conversation on the phone and still look like success.
        ``include_starred`` ticks the sheet's own "Mit Stern markierte Nachrichten löschen" box,
        because this verb's caller verifies the chat is EMPTY afterwards and starred messages left
        behind would fail that check truthfully.
        """
        nodes, _row = self._select_row(title, archived=archived)
        try:
            overflow = find(nodes, rid=RID_OVERFLOW)
            if not overflow:
                raise D.DriverError("the selection action bar has no overflow button")
            self.adb.tap_node(overflow[-1])
            nodes = self.adb.wait_for(
                lambda ns: bool(find(ns, rid=RID_MENU_TITLE, text=LABEL_CLEAR_CHAT)), timeout=8)
            item = find(nodes, rid=RID_MENU_TITLE, text=LABEL_CLEAR_CHAT)
            if not item:
                raise D.DriverError(
                    f"the selection overflow has no {LABEL_CLEAR_CHAT!r} item; it offers "
                    f"{[n.text for n in find(nodes, rid=RID_MENU_TITLE)]}")
            self.adb.tap_node(item[0])
            nodes = self.adb.wait_for(lambda ns: bool(find(ns, rid=RID_PRIMARY_BUTTON)), timeout=12)
            if not find(nodes, rid=RID_PRIMARY_BUTTON):
                raise D.DriverError("the clear-chat sheet did not open")
            nodes = self._choose_all_messages(nodes)
            starred = self._tick_starred(nodes) if include_starred else False
            # Re-read: the confirm button carries the chosen scope in its own label
            # ("CHAT LEEREN (2,0 MB)"), so the node found before the scope was set is stale.
            confirm = find(self.adb.dump(), rid=RID_PRIMARY_BUTTON)
            if not confirm:
                raise D.DriverError("the clear-chat sheet lost its confirm button")
            label = confirm[0].label
            self.adb.tap_node(confirm[0])
            time.sleep(UI_SETTLE_SEC)
            return {"menu_item": LABEL_CLEAR_CHAT, "scope": "all_messages",
                    "starred_included": starred, "confirmed_with": label}
        finally:
            self._leave_selection()

    def _choose_all_messages(self, nodes):
        scope = find(nodes, rid=RID_CLEAR_ALL_RADIO)
        if not scope:
            raise D.DriverError("the clear-chat sheet offers no 'all messages' scope: refusing to "
                                "confirm a scope this code did not choose")
        if scope[0].checked:
            return nodes
        self.adb.tap_node((find(nodes, rid=RID_CLEAR_ALL_ROW) or scope)[0])
        nodes = self.adb.dump()
        scope = find(nodes, rid=RID_CLEAR_ALL_RADIO)
        if not scope or not scope[0].checked:
            raise D.DriverError("the 'all messages' scope would not select")
        return nodes

    def _tick_starred(self, nodes):
        box = find(nodes, rid=RID_CLEAR_STARRED)
        if not box:
            return False          # this sheet has no starred option: there is nothing to tick
        if not box[0].checked:
            self.adb.tap_node((find(nodes, rid=RID_CLEAR_STARRED_ROW) or box)[0])
            box = find(self.adb.dump(), rid=RID_CLEAR_STARRED)
        return bool(box and box[0].checked)

    def delete_chat_row(self, title, *, archived=False):
        """Long press -> 'Chat löschen' -> the confirmation dialog -> confirm. Removes the chat."""
        nodes, _row = self._select_row(title, archived=archived)
        try:
            action = find(nodes, rid=RID_CAB_DELETE)
            if not action:
                raise D.DriverError("the selection action bar has no delete button")
            self.adb.tap_node(action[0])
            nodes = self.adb.wait_for(lambda ns: bool(find(ns, rid=RID_ALERT_TITLE)), timeout=12)
            prompt = find(nodes, rid=RID_ALERT_TITLE)
            button = find(nodes, rid=RID_DIALOG_BUTTON)
            if not prompt or not button:
                raise D.DriverError("the delete confirmation did not open")
            label = button[0].label
            self.adb.tap_node(button[0])
            time.sleep(UI_SETTLE_SEC)
            return {"menu_item": "Chat löschen", "prompt": prompt[0].text.strip(),
                    "confirmed_with": label}
        finally:
            self._leave_selection()

    # --- housekeeping ------------------------------------------------------------------------------------
    def park(self):
        """Back out of the conversation and onto the launcher, so notifications fire again."""
        self._open_phone = None
        for _ in range(4):
            focus = self.adb.focus()
            if focus.endswith("HomeActivity"):
                break
            if focus.endswith("Conversation"):
                self.adb.key("KEYCODE_BACK")
                continue
            self.adb.shell(f"monkey -p {WHATSAPP} -c android.intent.category.LAUNCHER 1 "
                           ">/dev/null 2>&1")
            time.sleep(1.2)
        self.adb.key("KEYCODE_HOME")

    def escalation_shot(self, tag):
        self._shot_n += 1
        slug = re.sub(r"[^a-z0-9_]+", "_", str(tag).lower())[:40]
        stamp = datetime.now(self.tz).strftime("%Y%m%d_%H%M%S")
        return self.adb.screenshot(self.shots_dir / f"{stamp}_{self._shot_n:03d}_{slug}.png")

    def sweep_screenshots(self, now, *, days=D.SCREENSHOT_RETENTION_DAYS):
        cutoff = now.timestamp() - days * 86400
        removed = 0
        for path in sorted(self.shots_dir.rglob("*.png")):
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        return removed

    def describe(self):
        """What the drift monitor reads. Ours is our own file's hash: there is no third party left."""
        import hashlib
        me = Path(__file__)
        version = ""
        out = self.adb.shell(f"dumpsys package {WHATSAPP} | grep -m1 versionName")
        hit = re.search(r"versionName=(\S+)", out)
        if hit:
            version = hit.group(1)
        return {"kind": "adb", "serial": self.adb.serial, "adb": self.adb.binary,
                "whatsapp_version": version, "connected": self.adb.connected(),
                "modules": {"adb_driver.py": {"path": str(me),
                                              "sha256": hashlib.sha256(me.read_bytes()).hexdigest()}}}
