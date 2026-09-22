"""Content-addressed inbound media: ids, the handset's own media folders, and reading a file's own
kind off its own bytes (TASK-131). WHO a file is from is decided by ``bridge/identity.py``, not here
-- this module never reads a candidate's identity, only a file's.

FOUR ROUNDS TRIED TO INFER THE SENDER BY TIME AND FAILED, IN ORDER:
  round 1: unique-in-a-time-window. Two people replying to one broadcast 200-400s apart could
    collide (defect M2).
  round 2: widen the window to 600s. Traded that failure for its mirror image -- widened enough
    that BOTH people now sat in each other's window and neither was unique any more.
  round 3: window back to 180s plus a "confirmation" read of the candidate's own thread. Looked
    like independent evidence; was the same coincidence a second time (the bubble read never named
    a file, only "something of this kind arrived about now") and could never fire for a voice note.
  round 4: a catch-up gate (was the candidate set even complete yet?) plus filename evidence. Still
    depended on a handset stat mtime and a mini-side clock read never agreeing across two hosts,
    still had a residual gap it admitted in its own docstring (a file whose mtime drifts past the
    window), and verification found three fresh ways to attach the wrong file besides.
  round 5 (decision-9, 2026-09-22): gave up on automatic attribution entirely -- every pulled file
    sat in a human queue, attached by one command. Correct on its own terms, and Ivan overruled it
    the same day: a human queue for every inbound file costs more than the occasional wrong pick it
    was bought to avoid. The autopilot matters more.

ROUND 6 (2026-09-22, Ivan's ruling, supersedes decision-9): match on what a file IS, never on when
it arrived. Exact size (always on disk), duration for audio (derivable from the .opus bytes
themselves, ``bridge/identity.py::opus_duration_seconds``), filename for a document (the one kind
WhatsApp keeps the sender's own name for). Time only narrows which threads
``bridge/executor.py::Executor.auto_match_media`` opens to look for corroborating evidence -- it
never decides, and this module still does not read it. Where nothing distinguishes two candidates
(two images in one chat minute; an image bubble shows neither size nor a name) the pick is
deterministic and marked weak rather than refused -- see ``bridge/identity.py::decide`` for the
whole rule. The human queue (``Executor.attach_media``) stays for whatever automatic matching still
cannot place at all (zero same-kind candidates): it is the fallback now, not the front door.

WHAT THIS FILE STILL DOES. WhatsApp writes every received file into its own flat media tree on the
handset (``WhatsApp Images``, ``WhatsApp Documents``, ...); a ``find``+``stat`` pass lists it
(``bridge/adb_driver.py::list_media``) and ``adb pull`` copies it off
(``bridge/adb_driver.py::pull_media``). What is left here is only what that pull needs: a
content-addressed id so a retry or a resend is byte-identical and never stored twice
(``content_media_id``), the listing parser, and reading a file's own kind off its own extension
(``kind_for_path`` -- the folder it sits in is not trusted for this; a document forwarded as a
"document" can still be a .jpg, and that mismatch is exactly the kind of thing a human sees on the
queue rather than a matcher silently mishandling).
"""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

#: Our media id space. Content-addressed, not minted from anything about the message: two
#: candidates who happen to send byte-identical files (a shared template CV, a stock photo) get
#: the same id on purpose -- it is the same bytes, and re-downloading them twice would be the
#: harness inventing a distinction the content itself does not have.
MEDIA_ID_PREFIX = "wab.m."
#: Matches the backlog's own spec for this task (TASK-131 plan note): enough of the digest to make
#: a collision practically impossible for the volume this rail will ever see, short enough to stay
#: readable in a journal line.
CONTENT_ID_CHARS = 20


def content_media_id(sha256_hex):
    """-> the id ``app/wa/bridge.Client.media_url`` is handed and must resolve."""
    return MEDIA_ID_PREFIX + str(sha256_hex)[:CONTENT_ID_CHARS]


def sha256_bytes(blob):
    return hashlib.sha256(blob).hexdigest()


# --- what WhatsApp's own media folders hold, and how to read them ------------------------------

#: Only the folders that carry RECEIVED bytes worth reading (TASK-96/TASK-107 read document, image
#: and audio; video gets the flat ack on both brains -- see app/wa/api.py:_READ_KINDS). Stickers and
#: wallpapers are on this phone's tree too and are never candidate content, so they are not listed.
WA_MEDIA_DIRS = ("WhatsApp Images", "WhatsApp Video", "WhatsApp Documents",
                 "WhatsApp Voice Notes", "WhatsApp Audio", "WhatsApp Animated Gifs")
WA_MEDIA_ROOT = "/sdcard/WhatsApp/Media"

#: A file's kind for the QUEUE listing, read off its own extension -- the folder it happens to sit
#: in is not trusted for this (a document forwarded as a "document" can still be a .jpg). Anything
#: not in this map is "document", WhatsApp's own catch-all bucket for a file type it does not have
#: a player for.
_KIND_BY_SUFFIX = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".gif": "image",
    ".mp4": "video", ".3gp": "video", ".mkv": "video", ".mov": "video",
    ".opus": "audio", ".ogg": "audio", ".m4a": "audio", ".mp3": "audio", ".aac": "audio",
    ".amr": "audio", ".wav": "audio",
    ".pdf": "document", ".doc": "document", ".docx": "document", ".xls": "document",
    ".xlsx": "document", ".ppt": "document", ".pptx": "document", ".txt": "document",
}
DEFAULT_KIND = "document"

#: The four kinds this rail can ever fetch bytes for -- a Meta media message is document/image/
#: audio/video and nothing else (app/wa/api.py:_MEDIA_KINDS). ``media_kind`` on the notification
#: side can also be "location"/"contact" (bridge/inbound.py:MEDIA_HINTS); those are never files on
#: disk and are never queued.
DOWNLOADABLE_KINDS = ("image", "video", "document", "audio")


def kind_for_path(rel_path):
    return _KIND_BY_SUFFIX.get(PurePosixPath(rel_path).suffix.lower(), DEFAULT_KIND)


def source_dir_for_path(rel_path):
    """-> the top-level WhatsApp media folder a pulled file came from (``WA_MEDIA_DIRS``'s own
    names, e.g. "WhatsApp Documents"), for the queue listing -- a fact about where the file sat on
    the handset, not a claim about who sent it."""
    parts = PurePosixPath(rel_path).parts
    return parts[0] if parts else ""


def parse_stat_listing(output):
    """-> {rel_path: (size, mtime_epoch)} from ``find <dirs> -exec stat -c '%s %Y %n' {} +``.

    One line per file, fields separated by single spaces the format string fixes; the path itself
    (``%n``) may still contain spaces (WhatsApp keeps the sender's own filename for a document), so
    it is everything after the SECOND space, taken with ``maxsplit=2`` rather than assumed to be the
    last whitespace-delimited token. A line that does not start with two integers is not a stat line
    (a stderr line that slipped through, a `find` warning) and is skipped rather than raising --
    this is a listing, and one unreadable file must not blind the pass to every other one.
    """
    files = {}
    for line in (output or "").splitlines():
        parts = line.split(" ", 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        size, mtime, rel = parts
        rel = rel.strip()
        if rel:
            files[rel] = (int(size), int(mtime))
    return files
