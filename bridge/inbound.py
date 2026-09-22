"""Inbound parsing and the id we mint for a message nobody gave an id to (TASK-142, TASK-143).

Pure: no adb, no device, no sqlite. It takes the text of ``dumpsys notification --noredact`` or a
list of bubbles read off an open chat and returns records with ids. That is why it is a separate
module -- the parsing is the part that can be wrong, and it has to be testable on a machine with no
phone attached.

THE ID, AND WHY IT LOOKS LIKE THAT
-----------------------------------
There is no provider message id on this rail. The id we mint is the only id an inbound message ever
has, and it lands in ``wa_messages.wamid`` which is UNIQUE -- so a colliding id is not a cosmetic
problem, it is a candidate whose message is dropped as a redelivery and never answered.

The reference implementation on the handset keys on ``sha1(direction|HH:MM|text)``. That omits the
counterparty and the date: two people answering "Ja" in the same minute collapse to one id, and so
do the same person's "Ja" on Monday and on Tuesday. Ours keys on

    counterparty E.164 | local date | HH:MM | whitespace-normalised text | occurrence index

so the two "Ja"s above are two ids, and the same message seen twice is one id.

WHY THE MINUTE AND NOT THE MILLISECOND. The same message reaches us through two doors: the
notification shade (epoch milliseconds) and the visible thread when a chat is open (WhatsApp draws
"HH:MM" and nothing else). If the id carried milliseconds the two doors would mint two ids for one
message and the candidate would be answered twice. So the timestamp is truncated to the local
minute, which is the precision the poorer door actually has, and the millisecond value travels in
the payload where it is information rather than identity.

``occurrence`` is what keeps the minute honest: the n-th identical text from the same number inside
the same minute. It is assigned in reading order within one snapshot, so a re-poll of the same
notification shade assigns the same indices.

KNOWN RESIDUAL, stated and not guarded: if the same person sends the same text twice in one minute
and the thread-read door only sees the second one (the first has scrolled off), the thread door
mints occurrence 0 for what the notification door called occurrence 1. That is one duplicate answer
in a case that needs a human typing the same word twice inside sixty seconds on a scrolled thread.
It is recorded here, not defended against.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

#: Our inbound id space. "wab" = WhatsApp bridge, "i" = inbound -- the same space app/wa/bridge_ids.py
#: mints "wab.o." into, which is why the direction is in the prefix.
INBOUND_PREFIX = "wab.i."
DIGEST_CHARS = 32

WHATSAPP_PKG = "com.whatsapp"


class Unresolvable(ValueError):
    """The address book cannot answer for a notification title, and why (TASK-146).

    ``resolve(title)`` returning "" already means "no number for this name". This is the other
    case: SEVERAL numbers for it. They need different words in the journal because they need
    different answers from a human -- one is a contact to add, the other is two contacts to tell
    apart.
    """

#: WhatsApp echoes our own bubble into the shade with these sender labels (German / English UI).
OWN_SENDERS = ("Du", "Ich", "You", "Me")

#: What the notification text says when the shade has collapsed several messages into a count. It is
#: a summary, never a message: storing it would answer a candidate with a reply to "2 neue Nachrichten".
_SUMMARY = re.compile(r"\d+\s*(neue Nachrichten|new messages|ungelesene)", re.I)

_REC_SPLIT = re.compile(r"^\s{4}NotificationRecord\(", re.M)
_MSG_LINE = re.compile(r"\[\d+\] Bundle\[\{(.*)\}\]\s*$")
_TITLE = re.compile(r"android\.title=String \((.*)\)\s*$", re.M)
_TEXT = re.compile(r"android\.text=String \((.*)\)\s*$", re.M)
_KEY = re.compile(r" key=(\S+?)(?:appImportanceLocked|\s)")
#: When Android posted the record. Stable across polls, unlike a wall clock read at parse time --
#: and the id is keyed on the minute, so a drifting timestamp would mint a new id every minute and
#: answer the same candidate again and again.
_CREATED = re.compile(r"mCreationTimeMs=(\d+)")

#: The media placeholders WhatsApp puts in a notification instead of the bytes. Read off their
#: MEDIA_HINTS map on the handset; we carry the placeholder verbatim and mark the kind, because the
#: bytes themselves are TASK-131 and inventing a caption here would put words in a candidate's mouth.
MEDIA_HINTS = {
    "\U0001f4f7": "image", "Foto": "image", "\U0001f3a5": "video", "Video": "video",
    "\U0001f4c4": "document", "Dokument": "document", "\U0001f3a4": "audio",
    "Sprachnachricht": "audio", "Audio": "audio", "\U0001f4cd": "location", "\U0001f464": "contact",
}


def media_kind(text):
    for hint, kind in MEDIA_HINTS.items():
        if hint in text:
            return kind
    return None


def digits(value):
    return re.sub(r"\D", "", str(value or ""))


def looks_like_number(title):
    """A notification title is either a saved contact's name or the raw number WhatsApp drew.

    ``+49 152 1234567`` is a number; ``Ivan`` is not. Anything with a letter in it is a name, so
    the address book has to answer for it -- see AdbDriver.resolve_counterparty.
    """
    text = str(title or "").strip()
    return bool(re.fullmatch(r"[+\d][\d\s()/-]*", text)) and len(digits(text)) >= 8


def normalize_text(text):
    """The text's identity for the id. Whitespace-normalised, because the notification shade and the
    bubble on screen disagree about line breaks in the same message."""
    return " ".join(str(text or "").split())


@dataclass
class InboundMessage:
    """One inbound message, from either door, before an id is minted."""

    counterparty: str          # E.164, or "" when the address book could not answer for the title
    title: str                 # what Android drew: a number or a saved contact name
    text: str
    local_date: str            # 'YYYY-MM-DD' in the handset's own timezone
    clock: str                 # 'HH:MM'
    source: str                # 'notification' | 'thread'
    time_ms: int = 0           # epoch ms when the shade gave us one, else 0
    media: str | None = None
    occurrence: int = 0
    inbound_id: str = ""

    def payload(self):
        """What lands in the mini outbox. No candidate number in a log line -- this is the row, not a log."""
        return {"envelope": "wa_bridge.inbound.v1", "inbound_id": self.inbound_id,
                "from": self.counterparty, "title": self.title, "text": self.text,
                "local_date": self.local_date, "clock": self.clock, "time_ms": self.time_ms,
                "media_kind": self.media, "source": self.source, "occurrence": self.occurrence}


def mint(message):
    """-> the inbound id for this message. Pure function of the five identity components."""
    if not message.counterparty:
        raise ValueError("cannot mint an inbound id without a counterparty number")
    material = "|".join([message.counterparty, message.local_date, message.clock,
                         normalize_text(message.text), str(message.occurrence)])
    return INBOUND_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()[:DIGEST_CHARS]


def assign_ids(messages):
    """Number the identical ones and mint. -> the same list, with occurrence and inbound_id filled.

    Reading order decides the occurrence index, so the same snapshot always numbers them the same.
    """
    seen = {}
    for message in messages:
        slot = (message.counterparty, message.local_date, message.clock, normalize_text(message.text))
        message.occurrence = seen.get(slot, 0)
        seen[slot] = message.occurrence + 1
        message.inbound_id = mint(message)
    return messages


# --- door 1: the notification shade -----------------------------------------------------------
@dataclass
class NotifRecord:
    key: str
    title: str
    is_group: bool
    summary_text: str = ""
    created_ms: int = 0
    messages: list = field(default_factory=list)   # [(sender, text, time_ms)]


def parse_notifications(dump, pkg=WHATSAPP_PKG):
    """-> [NotifRecord] for ``pkg`` out of ``dumpsys notification --noredact``."""
    records = []
    for block in _REC_SPLIT.split(dump or "")[1:]:
        head = block.splitlines()[0]
        if f"pkg={pkg} " not in head:
            continue
        key = _KEY.search(head)
        title = _TITLE.search(block)
        text = _TEXT.search(block)
        created = _CREATED.search(block)
        rec = NotifRecord(key=key.group(1) if key else head[:80],
                          title=title.group(1).strip() if title else "",
                          is_group="android.isGroupConversation=Boolean (true)" in block,
                          summary_text=text.group(1).strip() if text else "",
                          created_ms=int(created.group(1)) if created else 0)
        for line in block.splitlines():
            hit = _MSG_LINE.search(line)
            if not hit:
                continue
            body = hit.group(1)
            stamp = re.search(r"time=(\d+)", body)
            sender = re.search(r"(?:^|, )sender=([^,]*?)(?:, |$)", body)
            said = re.search(r"(?:^|, )text=(.*?)(?:, time=|$)", body)
            if said:
                rec.messages.append(((sender.group(1) if sender else "").strip(),
                                     said.group(1).strip(),
                                     int(stamp.group(1)) if stamp else 0))
        if rec.title or rec.messages or rec.summary_text:
            records.append(rec)
    return records


def notification_messages(dump, *, tz, resolve, pkg=WHATSAPP_PKG):
    """-> ([InboundMessage] with ids, [(title, reason)] for what could not be minted).

    ``resolve(title)`` answers the address book: a title -> E.164, or "" when it cannot.
    ``tz`` is the handset's timezone, because the local date and HH:MM in the id are the ones a human
    reads off the screen.

    ONE MESSAGE, TWO RECORDS. WhatsApp posts a MessagingStyle record for the message AND a plain
    group-summary record (``groupKey=group_key_messages``, no ``[n] Bundle`` lines) carrying the same
    ``android.text``. Both were observed on this handset for a single inbound "tst". Taking the
    summary's text as a message means the candidate is answered twice, so the MessagingStyle lines
    are the only source of a message -- and ``android.text`` is read only for a title that has no
    MessagingStyle record anywhere in this dump, which is how the first message of a brand-new
    thread arrives on some builds.

    NO WALL CLOCK EVER REACHES AN ID. A record with no timestamp is reported as unresolved rather
    than stamped with "now": the id is keyed on the local minute, so a clock read at parse time
    would mint a fresh id on every poll and re-answer the same message once a minute.
    """
    parsed = parse_notifications(dump, pkg=pkg)
    # Computed BEFORE groups are dropped: a title whose MessagingStyle record is a group chat must
    # not fall through to its own summary record, which carries no isGroupConversation line and
    # would smuggle a group message in as a 1:1.
    with_messages = {rec.title for rec in parsed if rec.messages}
    out, unresolved = [], []
    for rec in (rec for rec in parsed if not rec.is_group):
        lines = [(text, stamp) for sender, text, stamp in rec.messages
                 if sender not in OWN_SENDERS and not _SUMMARY.search(text)]
        if not lines and rec.title not in with_messages and rec.summary_text \
                and not _SUMMARY.search(rec.summary_text):
            lines = [(rec.summary_text, rec.created_ms)]
        reason = "address book has no number for this title"
        try:
            phone = resolve(rec.title) if lines else ""
        except Unresolvable as exc:
            phone, reason = "", str(exc)
        for text, stamp in lines:
            if not phone:
                unresolved.append((rec.title, reason))
                continue
            if not stamp:
                unresolved.append((rec.title, "notification record carries no timestamp"))
                continue
            moment = datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).astimezone(tz)
            out.append(InboundMessage(counterparty=phone, title=rec.title, text=text,
                                      local_date=moment.strftime("%Y-%m-%d"),
                                      clock=moment.strftime("%H:%M"), source="notification",
                                      time_ms=stamp, media=media_kind(text)))
    return assign_ids(out), unresolved


# --- door 2: the thread on screen -------------------------------------------------------------
def thread_messages(bubbles, *, counterparty, local_date, older=0):
    """-> ([InboundMessage] with ids, [(title, reason)]) for the incoming bubbles of an open chat.

    The same message the shade already gave us mints the same id here, by construction: both doors
    key on the local minute. That is the whole reason the id is not keyed on milliseconds.

    ``local_date`` IS AN ASSERTION ABOUT THESE BUBBLES, NOT A WALL CLOCK (TASK-146). WhatsApp draws
    only 'HH:MM' on a bubble, so a message from an earlier day stamped with today's date mints a
    different id from the one the shade minted for it -- a different id passes the UNIQUE column
    and is answered as a fresh message, which on the first send of day two re-answers every one of
    the candidate's still-visible messages from day one. The caller establishes the day and says
    how many of the leading bubbles it could NOT place (``older``); those are reported as
    unresolved and no id is minted for them.
    """
    incoming = [b for b in bubbles if b.direction == "in" and b.text.strip() and b.clock]
    placed = [b for b in bubbles[older:] if b.direction == "in" and b.text.strip() and b.clock]
    unresolved = [(counterparty, "bubble is above a day separator: its date is not today")
                  for _ in range(len(incoming) - len(placed))]
    out = [InboundMessage(counterparty=counterparty, title=counterparty, text=b.text,
                          local_date=local_date, clock=b.clock, source="thread",
                          media=media_kind(b.text))
           for b in placed]
    return assign_ids(out), unresolved
