"""CV/Urkunde intake for the WhatsApp harness (TASK-67): for WA_BRAIN=luna threads only, a
document/image message is downloaded (Meta's two-step media API, app/wa/meta.py:Client) and its
text merged onto the Luna card as cv_text/urkunde_text before the normal LB.turn() call -- instead
of the flat MEDIA_REPLY acknowledgement every other kind, and every kind on the deterministic
brain, still gets untouched. No network, no real `claude` CLI: the Meta client, CV.extract_text_vision
and luna_brain.Client are all fakes here, same seam convention as tests/test_wa_harness.py and
tests/test_wa_luna_brain.py.

TASK-95: every media message, both brains, has its original stored under C.DOCUMENTS_DIR (a tmp
dir here) and linked by a wa_documents row before anything reads it.

TASK-96 (bottom section): the card key follows the classification, not the extraction method, and
card.documents / documents_just_received record what arrived.
"""
import hashlib
import json
import pathlib
import re
import stat
import time
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import cv as CV
from app import data as D
from app.wa import api as WAPI
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import meta as M
from app.wa import store as ST
from tests.test_cv_intake import pdf_bytes_with_text

PHONE_ID = "111222333"
LEAD = "+491701234567"


class FakeMetaMedia:
    """Same shape as tests/test_wa_harness.py:FakeMeta, extended with the two-step media download
    this task adds. ``media_by_id``/``url_bytes`` stand in for Meta's own two calls."""

    def __init__(self, media_by_id=None, url_bytes=None):
        self.sent = []
        self.n = 0
        self.media_by_id = media_by_id or {}
        self.url_bytes = url_bytes or {}
        self.download_calls = []

    def _next(self):
        self.n += 1
        return f"wamid.out.{self.n}"

    def send_text(self, to_e164, body):
        self.sent.append({"to": to_e164, "body": body, "buttons": None})
        return self._next()

    def send_buttons(self, to_e164, body, buttons):
        self.sent.append({"to": to_e164, "body": body, "buttons": buttons})
        return self._next()

    def media_url(self, media_id):
        if media_id not in self.media_by_id:
            raise M.MetaError(f"unknown test media id {media_id!r}")
        return self.media_by_id[media_id]

    def download_media(self, url):
        self.download_calls.append(url)
        return self.url_bytes[url]


class _FakeLBClient:
    """Same seam as tests/test_wa_luna_brain.py:fake_client, but as a class so
    ``monkeypatch.setattr(LB, "Client", ...)`` can stand in for ``luna_brain.Client()`` the way
    app/wa/api.py's own ``_handle_one`` -> ``LB.turn()`` instantiates it (no ``client=`` is threaded
    through that call today -- this is the same seam test_wa_harness.py uses for app.wa.meta.Client
    at the API layer)."""

    def __init__(self, capture=None, **out_kw):
        self.capture = capture
        self.out_kw = out_kw

    def reply(self, system_text, user_text, session_id=None):
        if self.capture is not None:
            import json
            self.capture["system"] = system_text
            self.capture["user"] = json.loads(user_text)
        out = {"action": "reply_now_conversational", "bubbles": ["Danke, angekommen 🙂"], "rationale": "",
               "escalate_to_manager": False, "escalate_reason": None, "no_send": False,
               "next_ask": None, "card_patch": {}}
        out.update(self.out_kw)
        return out, (session_id or "sess-1")


def payload(kind, media_id="media-1", mime_type="application/pdf", filename=None,
            wamid="wamid.1", phone="491701234567"):
    media = {"id": media_id, "mime_type": mime_type}
    if filename:
        media["filename"] = filename
    message = {"id": wamid, "from": phone, "type": kind, kind: media}
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID},
                "messages": [message]}}]}]}


@pytest.fixture()
def luna_wa(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "BRAIN", "luna")
    # TASK-81: every _ingest_media call now also classifies the document -- a fixed, harmless
    # default here (individual tests override it via monkeypatch when the classification itself
    # is what they are checking).
    monkeypatch.setattr(CV, "classify_document",
                        lambda text, client=None: {"document_type": "lebenslauf", "certificate_level": "unknown"})


@pytest.fixture()
def deterministic_wa(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(C, "AUTOSEND", True)
    # C.BRAIN left at its default ("deterministic") on purpose -- this is the regression check.


# --- extraction dispatch: which path (the card key is the classification's, TASK-96) ----------

def test_extract_media_text_dispatch(monkeypatch):
    pdf_with_text = pdf_bytes_with_text("Hallo, das ist ein echter Lebenslauf-Text")
    text = WAPI._extract_media_text("document", pdf_with_text, "cv.pdf", "application/pdf")
    assert "echter Lebenslauf-Text" in text

    monkeypatch.setattr(CV, "extract_text_vision", lambda blob, suffix=".png", client=None: "VISION:" + suffix)

    empty_pdf = pdf_bytes_with_text("")
    text = WAPI._extract_media_text("document", empty_pdf, "scan.pdf", "application/pdf")
    assert text == "VISION:.pdf", "a scanned (text-layer-less) PDF falls through to vision"

    assert WAPI._extract_media_text("image", b"whatever", None, "image/jpeg") == "VISION:.jpg"

    # A document picked from a photo library, mime type says image regardless of the "document" kind:
    assert WAPI._extract_media_text("document", b"whatever", "photo.png", "image/png") == "VISION:.png"


def test_suffix_for_prefers_the_filename_extension_over_the_mime_guess():
    """The vision temp file only; a stored original is named by _mime_suffix (TASK-95 review)."""
    assert WAPI._suffix_for("scan.jpeg", "application/octet-stream") == ".jpeg"
    assert WAPI._suffix_for(None, "image/webp") == ".webp"
    assert WAPI._suffix_for(None, "application/unknown") == ".bin"
    assert WAPI._mime_suffix("image/webp; q=1") == ".webp" and WAPI._mime_suffix(None) == ".bin"


# --- WA_BRAIN=luna: document/image are downloaded, extracted, merged, then LB.turn() runs -----

def test_document_with_a_real_text_layer_becomes_cv_text_and_valentina_still_replies(luna_wa, monkeypatch):
    pdf_bytes = pdf_bytes_with_text("Lebenslauf 5 Jahre Intensivstation")
    meta = FakeMetaMedia(media_by_id={"m1": {"url": "https://cdn.example/m1", "mime_type": "application/pdf"}},
                         url_bytes={"https://cdn.example/m1": pdf_bytes})
    seen = {}
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _FakeLBClient(capture=seen))

    body = payload("document", media_id="m1", mime_type="application/pdf", filename="cv.pdf")
    out = WAPI.handle_payload(body, client=meta)

    assert out["results"][0]["action"] == "reply_now_conversational"
    assert meta.sent and meta.sent[0]["body"] == "Danke, angekommen 🙂"
    assert meta.download_calls == ["https://cdn.example/m1"]
    with ST.db() as c:
        t = ST.thread(c, LEAD)
    assert "Lebenslauf" in t["slots"]["cv_text"]
    assert "urkunde_text" not in t["slots"]
    card_sent_to_model = seen["user"]["card"]
    assert "Lebenslauf" in card_sent_to_model["cv_text"], "the model must see what was just extracted"


def test_image_of_an_urkunde_goes_through_vision_and_becomes_urkunde_text(luna_wa, monkeypatch):
    meta = FakeMetaMedia(media_by_id={"m2": {"url": "https://cdn.example/m2", "mime_type": "image/jpeg"}},
                         url_bytes={"https://cdn.example/m2": b"\xff\xd8\xff\xe0fakejpeg"})
    monkeypatch.setattr(CV, "extract_text_vision",
                        lambda blob, suffix=".png", client=None: "Urkunde: Pflegefachfrau, anerkannt")
    _classify_by_text(monkeypatch)
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _FakeLBClient())

    body = payload("image", media_id="m2", mime_type="image/jpeg", wamid="wamid.2")
    WAPI.handle_payload(body, client=meta)

    with ST.db() as c:
        t = ST.thread(c, LEAD)
    assert t["slots"]["urkunde_text"] == "Urkunde: Pflegefachfrau, anerkannt"
    assert "cv_text" not in t["slots"]


def test_scanned_pdf_with_no_text_layer_falls_through_to_vision(luna_wa, monkeypatch):
    empty_pdf = pdf_bytes_with_text("")
    meta = FakeMetaMedia(media_by_id={"m3": {"url": "https://cdn.example/m3", "mime_type": "application/pdf"}},
                         url_bytes={"https://cdn.example/m3": empty_pdf})
    calls = []

    def fake_vision(blob, suffix=".png", client=None):
        calls.append(suffix)
        return "Urkunde erkannt (gescannt)"

    monkeypatch.setattr(CV, "extract_text_vision", fake_vision)
    _classify_by_text(monkeypatch)
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _FakeLBClient())

    body = payload("document", media_id="m3", mime_type="application/pdf", filename="urkunde_scan.pdf",
                   wamid="wamid.3")
    WAPI.handle_payload(body, client=meta)

    assert calls == [".pdf"]
    with ST.db() as c:
        t = ST.thread(c, LEAD)
    assert t["slots"]["urkunde_text"] == "Urkunde erkannt (gescannt)"


def test_vision_extraction_failure_propagates_not_swallowed(luna_wa, monkeypatch):
    """CLAUDE.md, 'no invented safety nets': a document whose vision call fails must raise,
    never be silently treated as an empty CV."""
    meta = FakeMetaMedia(media_by_id={"m4": {"url": "https://cdn.example/m4", "mime_type": "image/png"}},
                         url_bytes={"https://cdn.example/m4": b"blank-image-bytes"})

    def raising_vision(blob, suffix=".png", client=None):
        raise RuntimeError("claude -p exited 1: vision call failed")

    monkeypatch.setattr(CV, "extract_text_vision", raising_vision)

    body = payload("image", media_id="m4", mime_type="image/png", wamid="wamid.4")
    with pytest.raises(RuntimeError, match="vision call failed"):
        WAPI.handle_payload(body, client=meta)
    with ST.db() as c:
        rows = ST.history(c, LEAD)
        docs = ST.documents_for(c, LEAD)
    assert [r["direction"] for r in rows] == ["in"], "the inbound is kept, no reply is claimed"
    # TASK-95: the original was stored and linked before extraction ran, and stays.
    assert [d["wamid"] for d in docs] == ["wamid.4"]
    assert pathlib.Path(docs[0]["path"]).read_bytes() == b"blank-image-bytes"
    assert docs[0]["text"] is None and docs[0]["text_key"] is None and docs[0]["document_type"] is None


def _classify_by_text(monkeypatch):
    """A classify_document fake keyed on a marker word in the (fake) extracted text, first match wins."""
    rules = (("Lebenslauf", "lebenslauf", "unknown"), ("Pflegehelfer", "urkunde", "helfer"),
             ("Urkunde", "urkunde", "fachkraft"), ("Defizitbescheid", "defizitbescheid", "unknown"),
             ("Aufenthaltstitel", "aufenthaltstitel", "unknown"), ("Dienstplan", "dienstplan", "unknown"))

    def classify(text, client=None):
        document_type, level = next((t, lv) for marker, t, lv in rules if marker in text)
        return {"document_type": document_type, "certificate_level": level}

    monkeypatch.setattr(CV, "classify_document", classify)


def _meta_with(*items):
    """FakeMetaMedia serving (media_id, mime_type, bytes) triples."""
    return FakeMetaMedia(media_by_id={mid: {"url": f"https://cdn.example/{mid}", "mime_type": mt}
                                      for mid, mt, _ in items},
                         url_bytes={f"https://cdn.example/{mid}": blob for mid, _, blob in items})


def _no_extraction(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("this path must not read the file")
    monkeypatch.setattr(CV, "extract_text", boom)
    monkeypatch.setattr(CV, "extract_text_vision", boom)
    monkeypatch.setattr(CV, "classify_document", boom)


def test_video_under_luna_still_gets_the_flat_ack(luna_wa, monkeypatch, tmp_path):
    """Nothing reads a video -- treating it like 'read' would be exactly the invented-safety-net kind of silent
    pretending CLAUDE.md rules out. TASK-95: the original is still stored, named with the mime type's extension.
    Audio is transcribed since TASK-107 (tests/test_wa_voice_notes.py)."""
    _no_extraction(monkeypatch)
    meta = _meta_with(("v1", "video/mp4", b"\x00\x00\x00 ftypmp4"))
    out = WAPI.handle_payload(payload("video", media_id="v1", mime_type="video/mp4", wamid="wamid.video"), client=meta)
    assert out["results"][0]["action"] == "media_ack"
    assert meta.sent[-1]["body"] == WAPI.MEDIA_REPLY
    with ST.db() as c:
        (doc,) = ST.documents_for(c, LEAD)
    assert (doc["wamid"], doc["kind"], doc["mime_type"]) == ("wamid.video", "video", "video/mp4")
    assert doc["path"].endswith("-v1.mp4") and doc["text"] is None
    assert meta.download_calls == ["https://cdn.example/v1"]


# --- the deterministic brain: still only the flat ack, but the original is stored (TASK-95) ------

def test_deterministic_brain_document_is_stored_and_acked_never_read(deterministic_wa, monkeypatch):
    _no_extraction(monkeypatch)
    meta = _meta_with(("d1", "application/pdf", b"%PDF-1.4 cv"))
    body = payload("document", media_id="d1", mime_type="application/pdf", filename="cv.pdf")
    out = WAPI.handle_payload(body, client=meta)
    assert out["results"][0]["action"] == "media_ack"
    assert meta.sent[-1]["body"] == WAPI.MEDIA_REPLY
    assert meta.download_calls == ["https://cdn.example/d1"]
    with ST.db() as c:
        docs = ST.documents_for(c, LEAD)
        t = ST.thread(c, LEAD)
    assert len(docs) == 1 and pathlib.Path(docs[0]["path"]).read_bytes() == b"%PDF-1.4 cv"
    assert docs[0]["text"] is None and docs[0]["document_type"] is None
    assert "cv_text" not in t["slots"] and "urkunde_text" not in t["slots"]


def test_deterministic_brain_image_is_stored_and_acked_never_read(deterministic_wa, monkeypatch):
    _no_extraction(monkeypatch)
    meta = _meta_with(("i1", "image/jpeg", b"\xff\xd8\xff\xe0jpeg"))
    body = payload("image", media_id="i1", mime_type="image/jpeg")
    out = WAPI.handle_payload(body, client=meta)
    assert out["results"][0]["action"] == "media_ack"
    with ST.db() as c:
        docs = ST.documents_for(c, LEAD)
    assert [(d["kind"], d["media_id"]) for d in docs] == [("image", "i1")]
    assert docs[0]["path"].endswith("-i1.jpg")


@pytest.mark.parametrize("kind, media_id, mime_type, blob, ext", [
    ("audio", "a1", "audio/ogg", b"OggS voice", ".ogg"),
    ("video", "v1", "video/mp4", b"\x00\x00\x00 ftypmp4", ".mp4"),
])
def test_deterministic_brain_audio_and_video_are_stored_and_acked(deterministic_wa, monkeypatch, kind, media_id,
                                                                  mime_type, blob, ext):
    """TASK-95 AC1/AC5, the non-luna audio/video path (verifier round 2: only luna audio/video had a test)."""
    _no_extraction(monkeypatch)
    meta = _meta_with((media_id, mime_type, blob))
    out = WAPI.handle_payload(payload(kind, media_id=media_id, mime_type=mime_type), client=meta)
    assert out["results"][0]["action"] == "media_ack"
    assert meta.sent[-1]["body"] == WAPI.MEDIA_REPLY
    with ST.db() as c:
        docs = ST.documents_for(c, LEAD)
    assert [(d["kind"], d["mime_type"], d["text"]) for d in docs] == [(kind, mime_type, None)]
    assert docs[0]["path"].endswith(f"-{media_id}{ext}") and pathlib.Path(docs[0]["path"]).read_bytes() == blob


# --- TASK-95: originals on disk, linked to the phone in wa_documents -----------------------------

# <UTC %Y%m%dT%H%M%S%f>Z-<media id alphanumerics><extension>
_STORED_NAME = re.compile(r"\d{8}T\d{12}Z-[A-Za-z0-9]+\.[a-z0-9]{1,5}")


def test_document_original_is_stored_and_linked_with_its_extraction(luna_wa, monkeypatch, tmp_path):
    pdf = pdf_bytes_with_text("Lebenslauf 5 Jahre Intensivstation")
    meta = _meta_with(("m1", "application/pdf", pdf))
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _FakeLBClient())

    WAPI.handle_payload(payload("document", media_id="m1", filename="Lebenslauf Anna.pdf", wamid="wamid.cv"),
                        client=meta)

    with ST.db() as c:
        docs = ST.documents_for(c, LEAD)
        inbound_meta = json.loads(c.execute("select meta from wa_messages where wamid='wamid.cv'").fetchone()["meta"])
    assert len(docs) == 1
    d = docs[0]
    path = pathlib.Path(d["path"])
    assert path.is_absolute() and path.parent == tmp_path / "wa_documents" / "491701234567"
    assert _STORED_NAME.fullmatch(path.name) and path.name.endswith("-m1.pdf")
    assert path.read_bytes() == pdf
    assert (d["phone"], d["wamid"], d["media_id"], d["kind"], d["mime_type"], d["original_filename"]) == \
        (LEAD, "wamid.cv", "m1", "document", "application/pdf", "Lebenslauf Anna.pdf")
    assert d["sha256"] == hashlib.sha256(pdf).hexdigest() and d["size_bytes"] == len(pdf)
    assert datetime.fromisoformat(d["received_at"]).tzinfo is not None
    assert "Lebenslauf" in d["text"] and d["text_key"] == "cv_text"
    assert (d["document_type"], d["certificate_level"]) == ("lebenslauf", "unknown")
    assert inbound_meta["media_id"] == "m1", "a download that failed would still be re-fetchable"


def test_stored_original_is_owner_only_and_leaves_no_temp_file(deterministic_wa, tmp_path):
    meta = _meta_with(("p1", "image/jpeg", b"\xff\xd8jpeg"))
    WAPI.handle_payload(payload("image", media_id="p1", mime_type="image/jpeg"), client=meta)
    with ST.db() as c:
        path = pathlib.Path(ST.documents_for(c, LEAD)[0]["path"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "wa_documents").stat().st_mode) == 0o700
    assert [p.name for p in path.parent.iterdir()] == [path.name]


# TASK-95 review: the extension is the mime type's too -- '../../evil.sh' was stored as '.sh'.
@pytest.mark.parametrize("filename, mime_type, suffix", [
    ("../../evil.sh", "application/pdf", ".pdf"),
    ("a/b.pdf", "application/pdf", ".pdf"),
    ("..\\..\\win.exe", "application/pdf", ".pdf"),
    ("/etc/cron.d/x", "application/pdf", ".pdf"),
    ("cv.pdf/../../../../escape", "image/png", ".png"),
    ("lebenslauf.p df", "application/pdf", ".pdf"),
    ("x.verylongext", None, ".bin"),
    ("scan.jpeg", "application/octet-stream", ".bin"),
    ("run.sh", None, ".bin"),
])
def test_untrusted_filename_never_names_or_places_the_stored_file(deterministic_wa, tmp_path, filename,
                                                                  mime_type, suffix):
    meta = _meta_with(("777", mime_type, b"payload"))
    WAPI.handle_payload(payload("document", media_id="777", mime_type=mime_type, filename=filename), client=meta)
    with ST.db() as c:
        doc = ST.documents_for(c, LEAD)[0]
    path = pathlib.Path(doc["path"])
    assert path.parent == tmp_path / "wa_documents" / "491701234567"
    assert _STORED_NAME.fullmatch(path.name) and path.name.endswith("-777" + suffix)
    assert doc["original_filename"] == filename, "kept as data only"
    stored = {p for p in tmp_path.rglob("*") if p.is_file() and not p.name.startswith("wa.sqlite")}
    assert stored == {path}, "nothing written anywhere else"


def test_classification_failure_keeps_the_original_and_its_text(luna_wa, monkeypatch):
    pdf = pdf_bytes_with_text("Lebenslauf 5 Jahre Intensivstation")
    meta = _meta_with(("m5", "application/pdf", pdf))

    def raising_classify(text, client=None):
        raise RuntimeError("classification call failed")

    monkeypatch.setattr(CV, "classify_document", raising_classify)
    with pytest.raises(RuntimeError, match="classification call failed"):
        WAPI.handle_payload(payload("document", media_id="m5", filename="cv.pdf", wamid="wamid.5"), client=meta)
    with ST.db() as c:
        (doc,) = ST.documents_for(c, LEAD)
    assert pathlib.Path(doc["path"]).read_bytes() == pdf
    assert "Lebenslauf" in doc["text"] and doc["document_type"] is None
    assert doc["text_key"] is None, "TASK-96: the card key comes from the classification, which failed"


def test_download_failure_stores_nothing_but_keeps_the_media_id_on_the_message(luna_wa, tmp_path):
    meta = FakeMetaMedia()   # knows no media id: media_url raises
    with pytest.raises(M.MetaError, match="unknown test media id"):
        WAPI.handle_payload(payload("document", media_id="gone", filename="cv.pdf", wamid="wamid.gone"),
                            client=meta)
    with ST.db() as c:
        assert ST.documents_for(c, LEAD) == []
        row = c.execute("select meta from wa_messages where wamid='wamid.gone'").fetchone()
    # media_link_strength (TASK-131 round 6, phone rail only): None here -- this payload came
    # through the real Meta client, which never sets it.
    assert json.loads(row["meta"]) == {"button_id": None, "media_id": "gone", "media_mime_type": "application/pdf",
                                       "media_filename": "cv.pdf", "media_link_strength": None}
    assert not (tmp_path / "wa_documents").exists()


def test_two_documents_from_one_phone_get_two_files_and_two_rows(luna_wa, monkeypatch):
    pdf = pdf_bytes_with_text("Lebenslauf 5 Jahre Intensivstation")
    meta = _meta_with(("cv1", "application/pdf", pdf), ("uk1", "image/jpeg", b"\xff\xd8urkunde"))
    monkeypatch.setattr(CV, "extract_text_vision", lambda blob, suffix=".png", client=None: "Urkunde Pflegefachfrau")
    _classify_by_text(monkeypatch)
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _FakeLBClient())

    WAPI.handle_payload(payload("document", media_id="cv1", filename="cv.pdf", wamid="wamid.a"), client=meta)
    WAPI.handle_payload(payload("image", media_id="uk1", mime_type="image/jpeg", wamid="wamid.b"), client=meta)

    with ST.db() as c:
        docs = ST.documents_for(c, LEAD)
    assert [(d["wamid"], d["text_key"]) for d in docs] == [("wamid.a", "cv_text"), ("wamid.b", "urkunde_text")]
    assert docs[0]["path"] != docs[1]["path"]
    assert [pathlib.Path(d["path"]).read_bytes() for d in docs] == [pdf, b"\xff\xd8urkunde"]


def test_stopped_thread_still_stores_the_original_and_sends_nothing(deterministic_wa):
    meta = _meta_with(("s1", "application/pdf", b"%PDF-1.4 late cv"))
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        t["stopped"], t["stopped_reason"] = True, ST.STOPPED
        ST.save_thread(c, t)
    out = WAPI.handle_payload(payload("document", media_id="s1", filename="cv.pdf"), client=meta)
    assert out["results"][0]["status"] == "stopped" and meta.sent == []
    with ST.db() as c:
        assert [d["media_id"] for d in ST.documents_for(c, LEAD)] == ["s1"]


def test_redelivered_media_message_is_stored_once(deterministic_wa, tmp_path):
    """record_inbound drops the duplicate wamid before ingest, so wa_documents.wamid UNIQUE is never hit."""
    meta = _meta_with(("r1", "application/pdf", b"%PDF-1.4 once"))
    body = payload("document", media_id="r1", filename="cv.pdf", wamid="wamid.r")
    WAPI.handle_payload(body, client=meta)
    again = WAPI.handle_payload(body, client=meta)
    assert again["results"][0]["status"] == "duplicate"
    assert meta.download_calls == ["https://cdn.example/r1"]
    with ST.db() as c:
        assert len(ST.documents_for(c, LEAD)) == 1
    assert len(list((tmp_path / "wa_documents").rglob("*"))) == 2   # the phone dir + one file


def test_existing_target_file_is_never_overwritten(deterministic_wa, monkeypatch, tmp_path):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 14, 12, 0, 0, 123456, tzinfo=timezone.utc)

    monkeypatch.setattr(WAPI, "datetime", _Frozen)
    first = WAPI._write_original(LEAD, "m1", b"first", ".pdf")
    with pytest.raises(FileExistsError):
        WAPI._write_original(LEAD, "m1", b"second", ".pdf")
    assert first.name == "20260914T120000123456Z-m1.pdf"
    assert first.read_bytes() == b"first"
    assert [p.name for p in first.parent.iterdir()] == [first.name], "the temp file is cleaned up"


def test_threads_api_lists_documents_as_metadata_only(luna_wa, monkeypatch):
    pdf = pdf_bytes_with_text("Lebenslauf 5 Jahre Intensivstation")
    meta = _meta_with(("m1", "application/pdf", pdf))
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _FakeLBClient())
    WAPI.handle_payload(payload("document", media_id="m1", filename="cv.pdf", wamid="wamid.cv"), client=meta)

    app = FastAPI()
    app.include_router(WAPI.router, prefix="/api")
    with TestClient(app) as client:
        r = client.get("/api/wa/threads", params={"phone": LEAD})
    assert r.status_code == 200
    body = r.json()
    (doc,) = body["documents"]
    with ST.db() as c:
        (row,) = ST.documents_for(c, LEAD)
    assert doc == {k: v for k, v in row.items() if k != "text"}
    assert doc["wamid"] == "wamid.cv" and doc["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert body["thread"]["phone"] == LEAD


# --- TASK-96: the card key follows the classification; documents accumulate on the card ---------

_CV_PDF = pdf_bytes_with_text("Lebenslauf 5 Jahre Intensivstation")


def _luna_ingest(monkeypatch, *messages, capture=None):
    """Run (kind, media_id, mime_type, bytes, filename) messages through the webhook one by one with
    a by-text classifier. -> (thread, wa_documents rows, per-message results)."""
    meta = _meta_with(*[(mid, mt, blob) for _, mid, mt, blob, _ in messages])
    # vision "reads" an image's bytes as its text, so each test names what the photo shows
    monkeypatch.setattr(CV, "extract_text_vision", lambda blob, suffix=".png", client=None: blob.decode("latin-1"))
    _classify_by_text(monkeypatch)
    payloads = capture if capture is not None else []
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _PayloadLog(payloads))
    results = [WAPI.handle_payload(payload(kind, media_id=mid, mime_type=mt, filename=filename, wamid=f"wamid.{i}"),
                                   client=meta)["results"][0]
               for i, (kind, mid, mt, _, filename) in enumerate(messages)]
    with ST.db() as c:
        return ST.thread(c, LEAD), ST.documents_for(c, LEAD), results


class _PayloadLog(_FakeLBClient):
    """_FakeLBClient that appends every turn's user payload to ``into``."""

    def __init__(self, into):
        super().__init__()
        self.into = into

    def reply(self, system_text, user_text, session_id=None):
        self.into.append(json.loads(user_text))
        return super().reply(system_text, user_text, session_id)


def test_photo_of_a_cv_becomes_cv_text_not_urkunde_text(luna_wa, monkeypatch):
    t, docs, _ = _luna_ingest(monkeypatch, ("image", "p1", "image/jpeg", b"Lebenslauf (Foto)", None))
    assert t["slots"]["cv_text"] == "Lebenslauf (Foto)" and "urkunde_text" not in t["slots"]
    assert (docs[0]["text_key"], docs[0]["document_type"]) == ("cv_text", "lebenslauf")


def test_text_pdf_urkunde_becomes_urkunde_text_not_cv_text(luna_wa, monkeypatch):
    pdf = pdf_bytes_with_text("Urkunde Gesundheits- und Krankenpflegerin")
    t, docs, _ = _luna_ingest(monkeypatch, ("document", "u1", "application/pdf", pdf, "urkunde.pdf"))
    assert "Urkunde" in t["slots"]["urkunde_text"] and "cv_text" not in t["slots"]
    assert (docs[0]["text_key"], docs[0]["document_type"], docs[0]["certificate_level"]) == \
        ("urkunde_text", "urkunde", "fachkraft")


def test_defizitbescheid_becomes_urkunde_text(luna_wa, monkeypatch):
    t, docs, _ = _luna_ingest(monkeypatch, ("image", "d1", "image/jpeg", b"Defizitbescheid Regierung", None))
    assert t["slots"]["urkunde_text"] == "Defizitbescheid Regierung" and "cv_text" not in t["slots"]
    assert docs[0]["text_key"] == "urkunde_text"


@pytest.mark.parametrize("marker, document_type", [("Dienstplan", "dienstplan"),
                                                   ("Aufenthaltstitel", "aufenthaltstitel")])
def test_a_non_cv_non_qualification_document_touches_neither_text_key(luna_wa, monkeypatch, marker,
                                                                        document_type):
    blob = f"{marker} September".encode()
    t, docs, _ = _luna_ingest(monkeypatch, ("image", "x1", "image/jpeg", blob, None))
    assert "cv_text" not in t["slots"] and "urkunde_text" not in t["slots"]
    assert t["slots"]["document_type"] == document_type
    assert t["slots"]["documents"] == [{"id": docs[0]["id"], "document_type": document_type,
                                        "certificate_level": "unknown"}]
    assert (docs[0]["text"], docs[0]["text_key"]) == (f"{marker} September", None), "the text stays on the row"


def test_later_uploads_keep_the_earlier_documents_and_the_documents_list_grows(luna_wa, monkeypatch):
    t, docs, _ = _luna_ingest(monkeypatch,
                           ("document", "cv1", "application/pdf", _CV_PDF, "cv.pdf"),
                           ("image", "uk1", "image/jpeg", b"Urkunde Pflegefachfrau", None),
                           ("image", "at1", "image/jpeg", b"Aufenthaltstitel", None))
    slots = t["slots"]
    assert "Lebenslauf" in slots["cv_text"], "the Urkunde and the Aufenthaltstitel did not erase the CV"
    assert slots["urkunde_text"] == "Urkunde Pflegefachfrau", "the Aufenthaltstitel did not erase the Urkunde"
    assert (slots["document_type"], slots["certificate_level"]) == ("aufenthaltstitel", "unknown"), "latest file"
    assert slots["documents"] == [{"id": d["id"], "document_type": d["document_type"],
                                   "certificate_level": d["certificate_level"]} for d in docs]
    assert [d["document_type"] for d in slots["documents"]] == ["lebenslauf", "urkunde", "aufenthaltstitel"]
    assert [d["text_key"] for d in docs] == ["cv_text", "urkunde_text", None]
    assert "_documents_just_received" not in slots, "consumed by the reply, never saved back"


def test_the_model_sees_which_file_just_arrived_and_the_gate_opens_only_with_both(luna_wa, monkeypatch):
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        t["slots"] = {"region": "Bayern", "qualification_path": "urkunde", "qualification_ok": True,
                      "city": "München", "housing_needed": False}
        ST.save_thread(c, t)
    payloads = []
    _, docs, _ = _luna_ingest(monkeypatch,
                           ("image", "cv1", "image/jpeg", b"Lebenslauf (Foto)", None),
                           ("image", "dp1", "image/jpeg", b"Dienstplan", None),
                           ("image", "uk1", "image/jpeg", b"Urkunde Pflegefachfrau", None),
                           capture=payloads)
    cv, dienstplan, urkunde = ({"id": d["id"], "document_type": d["document_type"],
                                "certificate_level": d["certificate_level"]} for d in docs)
    assert [p["documents_just_received"] for p in payloads] == [[cv], [dienstplan], [urkunde]]
    assert [p["latest_inbound"] for p in payloads] == ["", "", ""]
    boards = [p["requirement_scoreboard"] for p in payloads]
    assert [(b["cv_document"], b["qualification_document"], b["documents"]) for b in boards] == [
        ("satisfied", "open", "open"), ("satisfied", "open", "open"), ("satisfied", "satisfied", "satisfied")]
    assert boards[1]["next_objective"].startswith("ask for the still-missing German Urkunde as a photo/PDF")
    assert "_documents_just_received" not in payloads[0]["card"]


def test_a_rate_limited_media_turn_keeps_documents_just_received_for_the_catch_up_reply(luna_wa, monkeypatch):
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 1)
    with ST.db() as c:
        ST.record_luna_call(c, LEAD)
    t, docs, results = _luna_ingest(monkeypatch, ("image", "cv1", "image/jpeg", b"Lebenslauf (Foto)", None))
    assert results[0]["status"] == "rate_limited"
    summary = {"id": docs[0]["id"], "document_type": "lebenslauf", "certificate_level": "unknown"}
    assert t["slots"]["documents"] == [summary]
    assert t["slots"]["_documents_just_received"] == [summary], "no reply ran, the next one still owes the thanks"


# --- TASK-96 review 2026-09-14: the ingest result survives a failed reply; catch-up never answers blind --

_READY_BUT_DOCUMENTS = {"region": "Bayern", "qualification_path": "urkunde", "qualification_ok": True,
                        "city": "München", "housing_needed": False}


def _seed_card(slots):
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        t["slots"] = dict(slots)
        ST.save_thread(c, t)


def _saved_thread():
    with ST.db() as c:
        return ST.thread(c, LEAD), ST.history(c, LEAD), ST.documents_for(c, LEAD)


class _RaisingLBClient:
    def reply(self, system_text, user_text, session_id=None):
        raise RuntimeError("claude -p did not answer within 120s")


class _FailingSendMeta(FakeMetaMedia):
    def send_text(self, to_e164, body):
        raise M.MetaError("Meta HTTP 500", status_code=500, payload={})


@pytest.mark.parametrize("failure", ["brain", "meta_send"])
def test_a_failed_reply_after_ingest_keeps_the_file_on_the_card_for_the_catch_up_reply(luna_wa, monkeypatch,
                                                                                         failure):
    """The review's replay: _ingest_media only changed the in-memory card and the save ran after the reply, so a
    claude CLI timeout or a Meta error lost card.documents while the wa_documents row stayed classified. The
    catch-up reply then saw cv_document=open and asked for the CV that was already stored."""
    from app.wa.luna import catchup as CU
    _seed_card(_READY_BUT_DOCUMENTS)
    monkeypatch.setattr(CV, "extract_text_vision", lambda blob, suffix=".png", client=None: blob.decode("latin-1"))
    _classify_by_text(monkeypatch)
    items = [("cv1", "image/jpeg", b"Lebenslauf (Foto)")]
    if failure == "brain":
        meta = _meta_with(*items)
        monkeypatch.setattr(LB, "Client", lambda *a, **k: _RaisingLBClient())
        expected = RuntimeError
    else:
        base = _meta_with(*items)
        meta = _FailingSendMeta(media_by_id=base.media_by_id, url_bytes=base.url_bytes)
        monkeypatch.setattr(LB, "Client", lambda *a, **k: _FakeLBClient())
        expected = M.MetaError
    with pytest.raises(expected):
        WAPI.handle_payload(payload("image", media_id="cv1", mime_type="image/jpeg", wamid="wamid.cv"), client=meta)

    t, messages, (doc,) = _saved_thread()
    summary = {"id": doc["id"], "document_type": "lebenslauf", "certificate_level": "unknown"}
    assert t["slots"]["documents"] == [summary] and t["slots"]["_documents_just_received"] == [summary]
    assert t["slots"]["cv_text"] == "Lebenslauf (Foto)"
    assert [m["direction"] for m in messages] == ["in"]

    monkeypatch.setattr(ST, "STALE_CLAIM_SECONDS", 0)   # the brain case leaves its claim in_progress
    payloads = []
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _PayloadLog(payloads))
    (result,) = CU.run(client=FakeMetaMedia(), phones=[LEAD])
    assert result["status"] == "sent"
    (seen,) = payloads
    assert seen["documents_just_received"] == [summary] and seen["card"]["documents"] == [summary]
    assert seen["requirement_scoreboard"]["cv_document"] == "satisfied"
    assert seen["requirement_scoreboard"]["next_objective"].startswith("ask for the still-missing German Urkunde")
    t, _, _ = _saved_thread()
    assert "_documents_just_received" not in t["slots"] and t["last_outbound_at"]


class _CatchUpDuringDownloadMeta(FakeMetaMedia):
    """Runs a catch-up pass (its own sqlite connection, as the timer process has) while the webhook downloads."""

    def __init__(self, catch_up_results, **kw):
        super().__init__(**kw)
        self.catch_up_results = catch_up_results

    def download_media(self, url):
        from app.wa.luna import catchup as CU
        self.catch_up_results.append(CU.run(client=self.catch_up_meta))
        return super().download_media(url)


def test_a_catch_up_pass_during_the_webhook_ingest_leaves_the_turn_to_the_webhook(luna_wa, monkeypatch):
    """The review's replay: record_inbound had committed the media row (ball on us), and a catch-up pass inside
    the vision call claimed the turn, replied with latest_inbound '' and cv_document=open, and the webhook then
    got claimed_elsewhere and wrote its older thread copy over catch-up's save (last_outbound_at back to None)."""
    from app.wa.luna import catchup as CU
    _seed_card(_READY_BUT_DOCUMENTS)
    catch_up_results = []
    catch_up_meta = FakeMetaMedia()
    base = _meta_with(("cv1", "image/jpeg", b"Lebenslauf (Foto)"))
    meta = _CatchUpDuringDownloadMeta(catch_up_results, media_by_id=base.media_by_id, url_bytes=base.url_bytes)
    meta.catch_up_meta = catch_up_meta

    def vision_while_catch_up_runs(blob, suffix=".png", client=None):
        catch_up_results.append(CU.run(client=catch_up_meta))
        return blob.decode("latin-1")

    monkeypatch.setattr(CV, "extract_text_vision", vision_while_catch_up_runs)
    _classify_by_text(monkeypatch)
    payloads = []
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _PayloadLog(payloads))
    out = WAPI.handle_payload(payload("image", media_id="cv1", mime_type="image/jpeg", wamid="wamid.cv"), client=meta)

    skipped = [{"phone": LEAD, "wamid": "wamid.cv", "status": "claimed_elsewhere"}]
    assert catch_up_results == [skipped, skipped], "the webhook holds media:wamid.cv through download and vision"
    assert catch_up_meta.sent == []
    assert out["results"][0]["status"] == "sent"
    t, messages, (doc,) = _saved_thread()
    summary = {"id": doc["id"], "document_type": "lebenslauf", "certificate_level": "unknown"}
    (seen,) = payloads
    assert seen["documents_just_received"] == [summary]
    assert [m["direction"] for m in messages] == ["in", "out"]
    assert t["last_outbound_at"] and t["slots"]["_session_id"] == "sess-1"
    assert t["slots"]["documents"] == [summary]
    assert CU.run(client=catch_up_meta) == [], "answered: nothing owed any more"


@pytest.mark.parametrize("failing_step", ["vision", "classification"])
def test_catch_up_rereads_the_stored_original_of_a_media_turn_whose_ingest_raised(luna_wa, monkeypatch, failing_step):
    """TASK-99: the file never reached the card, so no reply goes out while reading fails; each catch-up pass
    re-reads the stored original (no second download) and records the failure once. Once reading works, the
    file lands on the card and the reply knows it arrived."""
    from app.wa.luna import catchup as CU
    _seed_card(_READY_BUT_DOCUMENTS)

    def boom(*a, **k):
        raise RuntimeError(f"{failing_step} failed")

    if failing_step == "vision":
        monkeypatch.setattr(CV, "extract_text_vision", boom)
    else:
        monkeypatch.setattr(CV, "extract_text_vision", lambda blob, suffix=".png", client=None: blob.decode("latin-1"))
        monkeypatch.setattr(CV, "classify_document", boom)
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _RaisingLBClient())
    meta = _meta_with(("cv1", "image/jpeg", b"Lebenslauf (Foto)"))
    with pytest.raises(RuntimeError, match=f"{failing_step} failed"):
        WAPI.handle_payload(payload("image", media_id="cv1", mime_type="image/jpeg", wamid="wamid.cv"), client=meta)

    catch_up_meta = FakeMetaMedia()   # serves no media: a second download would raise "unknown test media id"
    error = f"inbound wamid.cv not finished: RuntimeError: {failing_step} failed"
    for _ in range(2):
        assert CU.run(client=catch_up_meta) == [{"phone": LEAD, "wamid": "wamid.cv", "status": "error", "error": error}]
    assert catch_up_meta.sent == [] and catch_up_meta.download_calls == []
    t, messages, _ = _saved_thread()
    assert [m["direction"] for m in messages] == ["in"] and "documents" not in t["slots"]
    with ST.db() as c:
        assert [f["error"] for f in c.execute("select error from wa_send_failures")] == [error]
        assert ST.pending_inbound_summary(c, LEAD)["last_error"] == error

    monkeypatch.setattr(CV, "extract_text_vision", lambda blob, suffix=".png", client=None: blob.decode("latin-1"))
    _classify_by_text(monkeypatch)
    payloads = []
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _PayloadLog(payloads))
    (result,) = CU.run(client=catch_up_meta)
    assert (result["wamid"], result["status"]) == ("wamid.cv", "sent")
    t, messages, (doc,) = _saved_thread()
    summary = {"id": doc["id"], "document_type": "lebenslauf", "certificate_level": "unknown"}
    assert t["slots"]["documents"] == [summary] and payloads[0]["documents_just_received"] == [summary]
    assert catch_up_meta.download_calls == [] and [m["direction"] for m in messages] == ["in", "out"]
    with ST.db() as c:
        assert ST.pending_inbound_summary(c, LEAD) is None


def test_a_photo_with_no_readable_text_is_answered_once_and_never_read_again(luna_wa, monkeypatch):
    """Review 2026-09-14: NO_TEXT_FOUND (a selfie, a blurry Urkunde) raised, the turn never ran, and catch-up re-read
    the stored original with a new vision call every 3 minutes. Now it is the file's final classification."""
    from app.wa.luna import catchup as CU
    _seed_card(_READY_BUT_DOCUMENTS)
    vision_calls, real_vision = [], CV.extract_text_vision

    def no_text(blob, suffix=".png", client=None):   # the real extraction, the model answering NO_TEXT_FOUND
        vision_calls.append(suffix)
        return real_vision(blob, suffix=suffix, client=CV.VisionClient(call=lambda path: "NO_TEXT_FOUND"))

    monkeypatch.setattr(CV, "extract_text_vision", no_text)
    monkeypatch.setattr(CV, "classify_document", lambda text, client=None: pytest.fail("nothing to classify"))
    payloads = []
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _PayloadLog(payloads))
    meta = _meta_with(("selfie", "image/jpeg", b"a photo without text"))
    (result,) = WAPI.handle_payload(payload("image", media_id="selfie", mime_type="image/jpeg", wamid="wamid.selfie"),
                                    client=meta)["results"]
    assert result["status"] == "sent" and len(meta.sent) == 1 and vision_calls == [".jpg"]
    t, messages, (doc,) = _saved_thread()
    summary = {"id": doc["id"], "document_type": "unreadable", "certificate_level": None}
    assert payloads[0]["documents_just_received"] == [summary] and t["slots"]["documents"] == [summary]
    assert (doc["document_type"], doc["text"], doc["text_key"]) == ("unreadable", None, None)
    assert "cv_text" not in t["slots"] and "urkunde_text" not in t["slots"]
    assert LB.requirement_scoreboard(t["slots"])["documents"] == "open"
    with ST.db() as c:
        assert ST.pending_inbound(c, LEAD) == [] and ST.recent_send_failure(c, LEAD) is None
    assert CU.run(client=meta) == [] and vision_calls == [".jpg"], "no second vision call"


def test_files_with_the_same_card_key_append_their_text_never_replace_it(luna_wa, monkeypatch):
    """The review's replay on the defizit path: a Defizitbescheid, a helfer certificate, then a CV as two page
    photos left urkunde_text = the helfer text and cv_text = page 2 only; consent passes only these two card keys
    to CV.analyse_candidate."""
    _seed_card({**_READY_BUT_DOCUMENTS, "qualification_path": "defizit"})
    t, docs, _ = _luna_ingest(monkeypatch,
                              ("image", "d1", "image/jpeg", b"Defizitbescheid Regierung", None),
                              ("image", "h1", "image/jpeg", b"Pflegehelfer Zeugnis", None),
                              ("image", "p1", "image/jpeg", b"Lebenslauf Seite 1", None),
                              ("image", "p2", "image/jpeg", b"Lebenslauf Seite 2", None))
    assert t["slots"]["urkunde_text"] == "Defizitbescheid Regierung\n\nPflegehelfer Zeugnis"
    assert t["slots"]["cv_text"] == "Lebenslauf Seite 1\n\nLebenslauf Seite 2"
    assert [d["text_key"] for d in docs] == ["urkunde_text", "urkunde_text", "cv_text", "cv_text"]
    assert LB.requirement_scoreboard(t["slots"])["documents"] == "satisfied"


def test_a_foreign_diploma_keeps_both_text_keys_untouched_and_does_not_open_the_gate(luna_wa, monkeypatch):
    """TASK-96 review: classified auslaendisches_diplom (app/cv.py), a home-country diploma is neither the
    German Urkunde nor its text."""
    _seed_card(_READY_BUT_DOCUMENTS)
    monkeypatch.setattr(CV, "extract_text_vision", lambda blob, suffix=".png", client=None: blob.decode("latin-1"))
    monkeypatch.setattr(CV, "classify_document", lambda text, client=None: (
        {"document_type": "lebenslauf", "certificate_level": "unknown"} if "Lebenslauf" in text
        else {"document_type": "auslaendisches_diplom", "certificate_level": "unknown"}))
    payloads = []
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _PayloadLog(payloads))
    meta = _meta_with(("cv1", "image/jpeg", b"Lebenslauf (Foto)"), ("dp1", "image/jpeg", b"Diploma Nurse"))
    for i, mid in enumerate(("cv1", "dp1")):
        WAPI.handle_payload(payload("image", media_id=mid, mime_type="image/jpeg", wamid=f"wamid.{i}"), client=meta)
    t, _, docs = _saved_thread()
    assert "urkunde_text" not in t["slots"] and docs[1]["text_key"] is None
    board = payloads[-1]["requirement_scoreboard"]
    assert (board["cv_document"], board["qualification_document"]) == ("satisfied", "open")
    assert payloads[-1]["market_snapshot"]["shortlist"] == []


# --- 2026-09-14: the standalone harness app keeps thread reads open (local-only process) --------------

def test_the_standalone_harness_app_serves_thread_reads_without_a_session(luna_wa, monkeypatch):
    """app/wa/asgi.py binds 127.0.0.1 and nginx forwards only the webhook path (Ivan, 2026-09-14): no app.auth
    middleware, so the operator can read threads on the harness host with plain curl. AUTH_DISABLED=0 so a
    re-added middleware would answer 401 here."""
    monkeypatch.setenv("AUTH_DISABLED", "0")
    from app.wa import asgi
    with TestClient(asgi.app, base_url="https://testserver", follow_redirects=False) as client:
        r = client.get("/api/wa/threads", params={"phone": LEAD})
        assert r.status_code == 200 and r.json()["phone"] == LEAD
        assert client.get("/api/wa/health").status_code == 200
