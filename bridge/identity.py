"""Identity matching: attribute a pulled file to a message by what it IS, not when it arrived
(TASK-131 round 6, decision-9 superseded 2026-09-22 by Ivan's own ruling).

FOUR ROUNDS TRIED TIME-PROXIMITY AND FAILED (bridge/media.py's own history, kept there). Round 5's
answer was to stop deciding at all -- every pulled file sits in a human queue. Ivan's ruling
(2026-09-22) overrides that: an occasional misattribution is an acceptable cost, a human queue for
every inbound file is not. So automatic attribution is back, on different evidence: exact size
(always on disk), duration for audio (derivable from the .opus bytes themselves), and filename for
a document (WhatsApp preserves the sender's own name there, and only there). Time is never a
decider here -- it is not even read by this module -- it may only narrow which threads a caller
opens to look for corroborating evidence, and that narrowing lives in the caller (bridge/executor.py),
not here.

WHAT "STRONG" AND "WEAK" MEAN. A file is the sole candidate of its kind -> strong, UNLESS its own
kind can carry a comparable bubble attribute (audio's duration, a document's filename/size) and that
candidate's own thread actively contradicts the file (round 6 blocker B2, fixed) -- then weak, never
silently strong. Among several candidates of the same kind, exactly one is confirmed by a hard attribute
(duration, size or filename actually matching) -> strong. Nothing distinguishes them -- e.g. two
images in the same chat minute, WhatsApp's image bubble shows neither size nor a filename -- ->
picked deterministically and marked weak. Ivan: an occasional wrong pick is cheap; a stall is not.
A weak pick is never silent: bridge/ledger.py records the strength and the reason on the row, and
GET /v1/health counts it, so a human can audit it. bridge/envelope.py and app/wa/api.py gate a weak
DOCUMENT's text from ever reaching the model -- the one place a wrong pick would otherwise leak one
candidate's CV into a conversation with another.

WHAT THIS FILE DOES NOT KNOW. It never touches adb, sqlite or the network. ``decide()`` is a pure
function of facts already gathered elsewhere: bridge/adb_driver.py reads a chat bubble's own node
cluster (content-desc included, not just message_text -- see its own docstring for what is and is
not verified on the live handset today) into the same shape ``parse_bubble_evidence`` below reads
off a plain list of strings, and bridge/executor.py is the only caller that decides whose thread to
open at all.
"""
from __future__ import annotations

import re
import struct

# --- an Opus file's own duration, no library, no ffprobe -----------------------------------------
#: RFC 3533 (Ogg) + RFC 7845 (Opus-in-Ogg): every page starts with this 4-byte magic.
_OGG_MAGIC = b"OggS"
#: Opus's own fixed internal clock (RFC 7845 SS3.1) -- the granule position of the LAST page is the
#: total sample count at this rate, pre-skip included. A voice note's pre-skip is a few ms; at the
#: whole-second precision a chat bubble displays duration at, ignoring it is not a source of error
#: that changes a match decision.
_OPUS_CLOCK_HZ = 48000


def opus_duration_seconds(blob):
    """-> seconds of audio in an Ogg/Opus byte string, from the last page's granule position.

    Raises ValueError when ``blob`` is not a readable Ogg stream (no leading OggS page, or no page
    ever carried a granule position) -- a caller treats that the same as "no duration evidence",
    never as a reason to guess one.
    """
    if blob[:4] != _OGG_MAGIC:
        raise ValueError("not an Ogg container: no leading OggS page")
    pos, n, last_granule = 0, len(blob), None
    while pos + 27 <= n and blob[pos:pos + 4] == _OGG_MAGIC:
        granule = struct.unpack_from("<q", blob, pos + 6)[0]
        num_segments = blob[pos + 26]
        table_start = pos + 27
        if table_start + num_segments > n:
            break
        data_len = sum(blob[table_start:table_start + num_segments])
        if granule >= 0:
            last_granule = granule
        pos = table_start + num_segments + data_len
    if last_granule is None:
        raise ValueError("no Ogg page in this stream carried a granule position")
    return last_granule / _OPUS_CLOCK_HZ


def opus_duration_seconds_of_file(path):
    with open(path, "rb") as fh:
        return opus_duration_seconds(fh.read())


# --- reading a chat bubble's own node cluster into evidence strings ------------------------------
#: 'M:SS', the shape WhatsApp draws a voice note's duration in (and the composer's own record timer,
#: which is why the bound is two digits: nothing on this rail sends a voice note over 99 minutes).
_DURATION_RE = re.compile(r"\b([0-9]{1,2}):([0-5][0-9])\b")
#: A file size as WhatsApp's document bubble draws it -- comma is the German decimal separator, dot
#: is also accepted (a build or a locale this was not verified against might use either).
_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s?(B|KB|MB|GB)\b", re.I)
#: 'N Seiten' (German) / 'N pages' (English) -- the document bubble's own page count.
_PAGES_RE = re.compile(r"(\d+)\s*(?:Seiten?|pages?)\b", re.I)
#: A filename token: something dot-extension shaped, the extensions this rail ever pulls bytes for
#: (bridge/media.py::_KIND_BY_SUFFIX). Allows internal spaces -- WhatsApp keeps a sender's own
#: filename verbatim, spaces and all (bridge/watcher.py's own note on this) -- bounded by the ``|``
#: this module's own caller joins separate node strings on, not by whitespace.
_FILENAME_RE = re.compile(
    r"[^|]{1,120}?\.(?:pdf|docx?|xlsx?|pptx?|txt|jpe?g|png|gif|webp|mp4|3gp|mkv|mov|opus|ogg|m4a"
    r"|mp3|aac|amr|wav)\b", re.I)

_SIZE_UNIT_BYTES = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3}


def parse_bubble_evidence(strings):
    """-> {"duration_sec", "size_bytes", "pages", "filename"} from the text+content-desc pool of one
    bubble's node cluster. Every field is None when its pattern was not found -- absence of evidence,
    never a zero. First match wins per field; a bubble carries at most one of each shape in practice.
    """
    text = " | ".join(s for s in strings if s and str(s).strip())
    duration = None
    m = _DURATION_RE.search(text)
    if m:
        duration = int(m.group(1)) * 60 + int(m.group(2))
    size = None
    m = _SIZE_RE.search(text)
    if m:
        size = float(m.group(1).replace(",", ".")) * _SIZE_UNIT_BYTES[m.group(2).upper()]
    pages = None
    m = _PAGES_RE.search(text)
    if m:
        pages = int(m.group(1))
    filename = None
    m = _FILENAME_RE.search(text)
    if m:
        filename = m.group(0).strip()
    return {"duration_sec": duration, "size_bytes": size, "pages": pages, "filename": filename}


# --- the decision --------------------------------------------------------------------------------
#: How close a bubble-read duration has to land to the file's own computed one to count as a match.
#: WhatsApp draws a duration rounded to the second; a couple of seconds of slack covers rounding at
#: both ends without being wide enough to confirm two genuinely different voice notes.
DURATION_TOLERANCE_SEC = 2.0
#: How close a bubble-read size has to land to the file's own stat size. WhatsApp's size text is
#: rounded for display (and the local repo has not verified which base, 1000 or 1024, this build
#: uses -- see bridge/adb_driver.py's own docstring), so this is generous on purpose: wide enough to
#: absorb rounding at either base, narrow enough that two files of very different size never tie.
_SIZE_REL_TOL = 0.10
_SIZE_ABS_TOL = 4096


def _size_close(bubble_size, file_size):
    if bubble_size is None or file_size is None:
        return False
    return abs(bubble_size - file_size) <= max(_SIZE_ABS_TOL, _SIZE_REL_TOL * max(bubble_size, file_size))


def _contradicts(file_facts, evidence):
    """-> True when a candidate's own bubble evidence actively DISAGREES with the file's own facts
    on an attribute both sides have a value for. Never True just because evidence is silent (absence
    is not disagreement -- an image bubble carries none of this, by design, and a sole image
    candidate stays strong exactly as before). Used only for the sole-candidate path (TASK-131
    round 6 blocker B2): round 6 shipped 'sole candidate -> strong, no evidence needed' as an
    unconditional rule, which also meant unconditionally UNCHECKED -- a document's own bubble could
    read a flatly different filename and 'strong' would still leak its text to the model."""
    if file_facts.get("duration_sec") is not None and evidence.get("duration_sec") is not None:
        if abs(evidence["duration_sec"] - file_facts["duration_sec"]) > DURATION_TOLERANCE_SEC:
            return True
    if file_facts.get("size") is not None and evidence.get("size_bytes") is not None:
        if not _size_close(evidence["size_bytes"], file_facts["size"]):
            return True
    filename = (file_facts.get("filename") or "").strip().lower()
    ev_filename = (evidence.get("filename") or "").strip().lower()
    if filename and ev_filename and ev_filename != filename:
        return True
    return False


def _confirming_attributes(file_facts, evidence):
    """-> the sorted names of the attributes that agree between a file's own facts and one
    candidate's bubble evidence. Empty when none of them do, or when there was no evidence to check
    (a kind whose bubble carries none, e.g. an image today -- bridge/adb_driver.py's own docstring)."""
    hits = []
    if file_facts.get("duration_sec") is not None and evidence.get("duration_sec") is not None:
        if abs(evidence["duration_sec"] - file_facts["duration_sec"]) <= DURATION_TOLERANCE_SEC:
            hits.append("duration")
    if file_facts.get("size") is not None and _size_close(evidence.get("size_bytes"), file_facts["size"]):
        hits.append("size")
    filename = (file_facts.get("filename") or "").strip().lower()
    if filename and (evidence.get("filename") or "").strip().lower() == filename:
        hits.append("filename")
    return sorted(hits)


def decide(file_facts, candidates, evidence_by_phone=None):
    """-> (phone, inbound_id, strength, reason) for the message this file belongs to, or None when
    there is not even one same-kind candidate. ``candidates`` is already kind-filtered by the caller
    (bridge/executor.py) -- this function does not know what kind means, only whether facts agree.

    ``strength`` is 'strong' (sole candidate, or exactly one candidate confirmed by a hard
    attribute) or 'weak' (more than one candidate and nothing tells them apart -- picked
    deterministically, on the OLDEST candidate's own time so a re-run is stable). Never a third
    value, never a refusal: Ivan's ruling is that a stall costs more than an occasional wrong pick.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        c = candidates[0]
        evidence = (evidence_by_phone or {}).get(c["phone"]) or {}
        if _contradicts(file_facts, evidence):
            return c["phone"], c["inbound_id"], "weak", "sole_candidate_contradicted"
        return c["phone"], c["inbound_id"], "strong", "sole_candidate"
    if evidence_by_phone:
        confirmed = []
        for c in candidates:
            hits = _confirming_attributes(file_facts, evidence_by_phone.get(c["phone"]) or {})
            if hits:
                confirmed.append((c, hits))
        if len(confirmed) == 1:
            c, hits = confirmed[0]
            return c["phone"], c["inbound_id"], "strong", "+".join(hits)
    pick = sorted(candidates, key=lambda c: (c.get("time_ms") or 0, c["phone"]))[0]
    return pick["phone"], pick["inbound_id"], "weak", "tie_break"
