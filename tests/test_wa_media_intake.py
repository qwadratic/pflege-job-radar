"""CV/Urkunde intake for the WhatsApp harness (TASK-67): for WA_BRAIN=luna threads only, a
document/image message is downloaded (Meta's two-step media API, app/wa/meta.py:Client) and its
text merged onto the Luna card as cv_text/urkunde_text before the normal LB.turn() call -- instead
of the flat MEDIA_REPLY acknowledgement every other kind, and every kind on the deterministic
brain, still gets untouched. No network, no real `claude` CLI: the Meta client, CV.extract_text_vision
and luna_brain.Client are all fakes here, same seam convention as tests/test_wa_harness.py and
tests/test_wa_luna_brain.py.
"""
import time

import pytest

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
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "BRAIN", "luna")


@pytest.fixture()
def deterministic_wa(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(C, "AUTOSEND", True)
    # C.BRAIN left at its default ("deterministic") on purpose -- this is the regression check.


# --- extraction dispatch: which path, which card key ------------------------------------------

def test_extract_media_text_dispatch(monkeypatch):
    pdf_with_text = pdf_bytes_with_text("Hallo, das ist ein echter Lebenslauf-Text")
    text, key = WAPI._extract_media_text("document", pdf_with_text, "cv.pdf", "application/pdf")
    assert key == "cv_text" and "echter Lebenslauf-Text" in text

    monkeypatch.setattr(CV, "extract_text_vision", lambda blob, suffix=".png", client=None: "VISION:" + suffix)

    empty_pdf = pdf_bytes_with_text("")
    text, key = WAPI._extract_media_text("document", empty_pdf, "scan.pdf", "application/pdf")
    assert key == "urkunde_text" and text == "VISION:.pdf", "a scanned (text-layer-less) PDF falls through to vision"

    text, key = WAPI._extract_media_text("image", b"whatever", None, "image/jpeg")
    assert key == "urkunde_text" and text == "VISION:.jpg"

    # A document picked from a photo library, mime type says image regardless of the "document" kind:
    text, key = WAPI._extract_media_text("document", b"whatever", "photo.png", "image/png")
    assert key == "urkunde_text" and text == "VISION:.png"


def test_suffix_for_prefers_the_filename_extension_over_the_mime_guess():
    assert WAPI._suffix_for("scan.jpeg", "application/octet-stream") == ".jpeg"
    assert WAPI._suffix_for(None, "image/webp") == ".webp"
    assert WAPI._suffix_for(None, "application/unknown") == ".bin"


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


def test_image_message_goes_through_vision_and_becomes_urkunde_text(luna_wa, monkeypatch):
    meta = FakeMetaMedia(media_by_id={"m2": {"url": "https://cdn.example/m2", "mime_type": "image/jpeg"}},
                         url_bytes={"https://cdn.example/m2": b"\xff\xd8\xff\xe0fakejpeg"})
    monkeypatch.setattr(CV, "extract_text_vision",
                        lambda blob, suffix=".png", client=None: "Urkunde: Pflegefachfrau, anerkannt")
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
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _FakeLBClient())

    body = payload("document", media_id="m3", mime_type="application/pdf", filename="urkunde_scan.pdf",
                   wamid="wamid.3")
    WAPI.handle_payload(body, client=meta)

    assert calls == [".pdf"]
    with ST.db() as c:
        t = ST.thread(c, LEAD)
    assert t["slots"]["urkunde_text"] == "Urkunde erkannt (gescannt)"


def test_vision_extraction_failure_propagates_not_swallowed(luna_wa, monkeypatch):
    """CLAUDE.md, 'no invented safety nets': a document that fails vision extraction must raise,
    never be silently treated as an empty CV."""
    meta = FakeMetaMedia(media_by_id={"m4": {"url": "https://cdn.example/m4", "mime_type": "image/png"}},
                         url_bytes={"https://cdn.example/m4": b"blank-image-bytes"})

    def raising_vision(blob, suffix=".png", client=None):
        raise RuntimeError("vision extraction found no readable text in the image/scanned document")

    monkeypatch.setattr(CV, "extract_text_vision", raising_vision)

    body = payload("image", media_id="m4", mime_type="image/png", wamid="wamid.4")
    with pytest.raises(RuntimeError, match="no readable text"):
        WAPI.handle_payload(body, client=meta)
    with ST.db() as c:
        rows = ST.history(c, LEAD)
    assert [r["direction"] for r in rows] == ["in"], "the inbound is kept, no reply is claimed"


def test_audio_and_video_under_luna_still_get_the_flat_ack(luna_wa):
    """Nothing in this task transcribes audio/video -- treating them like 'read' would be exactly
    the invented-safety-net kind of silent pretending CLAUDE.md rules out."""
    meta = FakeMetaMedia()  # one instance across both kinds: its wamid.out.N counter must not collide
    for kind in ("audio", "video"):
        body = payload(kind, media_id="a1", mime_type="audio/ogg" if kind == "audio" else "video/mp4",
                       wamid=f"wamid.{kind}")
        out = WAPI.handle_payload(body, client=meta)
        assert out["results"][0]["action"] == "media_ack"
        assert meta.sent[-1]["body"] == WAPI.MEDIA_REPLY
        assert meta.download_calls == []


# --- the deterministic brain's existing media handling is completely untouched ----------------

def test_deterministic_brain_document_handling_is_unaffected(deterministic_wa):
    meta = FakeMetaMedia()
    body = payload("document", media_id="d1", mime_type="application/pdf", filename="cv.pdf")
    out = WAPI.handle_payload(body, client=meta)
    assert out["results"][0]["action"] == "media_ack"
    assert meta.sent[-1]["body"] == WAPI.MEDIA_REPLY
    assert meta.download_calls == [], "the deterministic brain must never trigger a media download"


def test_deterministic_brain_image_handling_is_unaffected(deterministic_wa):
    meta = FakeMetaMedia()
    body = payload("image", media_id="i1", mime_type="image/jpeg")
    out = WAPI.handle_payload(body, client=meta)
    assert out["results"][0]["action"] == "media_ack"
    assert meta.download_calls == []
