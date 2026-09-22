"""Offline proof for bridge/identity.py (TASK-131 round 6, Ivan's ruling 2026-09-22): match a
pulled file to a message on what it IS -- size, duration, filename -- never on when it arrived.
"""
import struct

import pytest

from bridge import identity as ID

PHONE_A = "+491512000001"
PHONE_B = "+491512000002"


# --- opus_duration_seconds: no library, no ffprobe, just the Ogg container -----------------------
def _ogg_page(granule, payload, *, seq=0, first=False, last=False):
    header_type = (0x02 if first else 0) | (0x04 if last else 0)
    segments, rest = [], payload
    while len(rest) >= 255:
        segments.append(255)
        rest = rest[255:]
    segments.append(len(rest))
    head = struct.pack("<4sBBqIIIB", b"OggS", 0, header_type, granule, 1, seq, 0, len(segments))
    return head + bytes(segments) + payload


def test_opus_duration_is_the_last_pages_granule_over_48k():
    blob = _ogg_page(0, b"OpusHead" + b"\x00" * 10, seq=0, first=True)
    blob += _ogg_page(48000 * 7, b"\x00" * 20, seq=1, last=True)   # 7.0s at Opus's fixed clock
    assert ID.opus_duration_seconds(blob) == pytest.approx(7.0)


def test_opus_duration_takes_the_last_page_not_the_first():
    blob = _ogg_page(48000 * 2, b"x" * 10, seq=0, first=True)
    blob += _ogg_page(48000 * 9, b"y" * 10, seq=1)
    blob += _ogg_page(48000 * 12, b"z" * 10, seq=2, last=True)
    assert ID.opus_duration_seconds(blob) == pytest.approx(12.0)


def test_opus_duration_raises_on_a_non_ogg_blob():
    with pytest.raises(ValueError):
        ID.opus_duration_seconds(b"%PDF-1.4 not an ogg file at all")


def test_opus_duration_raises_when_no_page_carries_a_granule():
    # every page's granule is -1 ("no packet ends on this page") -- legal Ogg, no duration evidence
    blob = _ogg_page(-1, b"partial", seq=0, first=True)
    with pytest.raises(ValueError):
        ID.opus_duration_seconds(blob)


# --- parse_bubble_evidence: whatever text+content-desc a bubble's node cluster carries ------------
def test_duration_is_read_as_minutes_and_seconds():
    assert ID.parse_bubble_evidence(["Sprachnachricht", "0:07"])["duration_sec"] == 7
    assert ID.parse_bubble_evidence(["1:23"])["duration_sec"] == 83


def test_size_is_read_in_kb_or_mb_comma_or_dot_decimal():
    assert ID.parse_bubble_evidence(["Lebenslauf.pdf", "165 KB"])["size_bytes"] == 165000
    assert ID.parse_bubble_evidence(["1,2 MB"])["size_bytes"] == pytest.approx(1_200_000)
    assert ID.parse_bubble_evidence(["1.2 MB"])["size_bytes"] == pytest.approx(1_200_000)


def test_pages_is_read_in_german_or_english():
    assert ID.parse_bubble_evidence(["3 Seiten"])["pages"] == 3
    assert ID.parse_bubble_evidence(["2 pages"])["pages"] == 2


def test_filename_is_read_as_a_dot_extension_token():
    ev = ID.parse_bubble_evidence(["Anna Musterfrau Lebenslauf.pdf", "165 KB", "3 Seiten"])
    assert ev["filename"] == "Anna Musterfrau Lebenslauf.pdf"
    assert ev["size_bytes"] == 165000 and ev["pages"] == 3


def test_absence_is_none_never_a_zero():
    ev = ID.parse_bubble_evidence(["just a photo, no drawn attributes at all"])
    assert ev == {"duration_sec": None, "size_bytes": None, "pages": None, "filename": None}


# --- decide: the whole matching rule --------------------------------------------------------------
def _cand(phone, inbound_id, time_ms=1000):
    return {"phone": phone, "inbound_id": inbound_id, "time_ms": time_ms}


def test_no_candidates_is_no_decision():
    assert ID.decide({"kind": "image", "size": 100}, []) is None


def test_sole_candidate_is_strong_with_no_evidence_needed():
    result = ID.decide({"kind": "image", "size": 100}, [_cand(PHONE_A, "wab.i.a")])
    assert result == (PHONE_A, "wab.i.a", "strong", "sole_candidate")


def test_two_candidates_one_attribute_differs_the_matching_one_wins_strong():
    """THE ACCEPTANCE CASE: two files in one chat minute, distinguished because at least one
    attribute (here, size) differs -- never by time."""
    file_facts = {"kind": "document", "size": 165000, "filename": None}
    candidates = [_cand(PHONE_A, "wab.i.a"), _cand(PHONE_B, "wab.i.b")]
    evidence = {PHONE_A: {"size_bytes": 165000}, PHONE_B: {"size_bytes": 900000}}
    result = ID.decide(file_facts, candidates, evidence)
    assert result == (PHONE_A, "wab.i.a", "strong", "size")


def test_a_voice_note_is_matched_by_duration():
    file_facts = {"kind": "audio", "size": 165803, "duration_sec": 7.0}
    candidates = [_cand(PHONE_A, "wab.i.a"), _cand(PHONE_B, "wab.i.b")]
    evidence = {PHONE_A: {"duration_sec": 42.0}, PHONE_B: {"duration_sec": 7.0}}
    result = ID.decide(file_facts, candidates, evidence)
    assert result == (PHONE_B, "wab.i.b", "strong", "duration")


def test_a_document_is_matched_by_filename():
    file_facts = {"kind": "document", "size": 1, "filename": "Lebenslauf.pdf"}
    candidates = [_cand(PHONE_A, "wab.i.a"), _cand(PHONE_B, "wab.i.b")]
    evidence = {PHONE_A: {"filename": "Zeugnis.pdf"}, PHONE_B: {"filename": "Lebenslauf.pdf"}}
    result = ID.decide(file_facts, candidates, evidence)
    assert result == (PHONE_B, "wab.i.b", "strong", "filename")


def test_two_images_with_no_distinguishing_attribute_are_weak_not_a_refusal():
    """Ivan's own example: an image bubble shows neither size nor a name, so nothing survives to
    distinguish two candidates. Picked deterministically (oldest first) and marked weak -- never
    left stalled."""
    file_facts = {"kind": "image", "size": 122272}
    candidates = [_cand(PHONE_B, "wab.i.b", time_ms=2000), _cand(PHONE_A, "wab.i.a", time_ms=1000)]
    evidence = {PHONE_A: {}, PHONE_B: {}}
    result = ID.decide(file_facts, candidates, evidence)
    assert result == (PHONE_A, "wab.i.a", "weak", "tie_break")


def test_no_evidence_gathered_at_all_still_ties_and_is_weak():
    file_facts = {"kind": "image", "size": 100}
    candidates = [_cand(PHONE_A, "wab.i.a", time_ms=1000), _cand(PHONE_B, "wab.i.b", time_ms=500)]
    result = ID.decide(file_facts, candidates, evidence_by_phone=None)
    assert result == (PHONE_B, "wab.i.b", "weak", "tie_break")


def test_the_tie_break_is_deterministic_across_repeated_calls():
    file_facts = {"kind": "image", "size": 100}
    candidates = [_cand(PHONE_A, "wab.i.a", time_ms=1000), _cand(PHONE_B, "wab.i.b", time_ms=1000)]
    first = ID.decide(file_facts, candidates)
    second = ID.decide(file_facts, list(reversed(candidates)))
    assert first == second == (PHONE_A, "wab.i.a", "weak", "tie_break")


def test_two_attributes_both_confirming_the_same_candidate_is_still_one_strong_reason_string():
    file_facts = {"kind": "document", "size": 165000, "filename": "Lebenslauf.pdf"}
    candidates = [_cand(PHONE_A, "wab.i.a"), _cand(PHONE_B, "wab.i.b")]
    evidence = {PHONE_A: {"size_bytes": 165000, "filename": "Lebenslauf.pdf"}, PHONE_B: {}}
    result = ID.decide(file_facts, candidates, evidence)
    assert result == (PHONE_A, "wab.i.a", "strong", "filename+size")
