"""TASK-107 live: one synthetic German voice note (espeak-ng speech, ffmpeg ogg/opus -- the format of a WhatsApp voice
note) goes through the webhook path with a fake Meta client and a fake Luna model, and the real OpenAI transcription
endpoint (C.STT_MODEL, WA_STT_MODEL to compare models). Marked ``network``; skipped without OPENAI_API_KEY in the
environment, or without espeak-ng/ffmpeg. Nothing reaches Meta or the claude CLI; synthetic speech, synthetic phone.

Run: ``set -a; . ./.env; set +a; pytest -q -m network tests/test_wa_stt_live.py -s``.
"""
import os
import re
import shutil
import subprocess
import time

import pytest

from app import data as D
from app.wa import api as WAPI
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import store as ST
from tests.test_wa_media_intake import _meta_with, _PayloadLog
from tests.test_wa_voice_notes import LEAD, PHONE_ID, voice_message, webhook

pytestmark = pytest.mark.network

SPOKEN = "Hallo, ich bin Pflegefachfrau und möchte gern in München arbeiten."


def _synthetic_voice_note(tmp_path):
    wav, ogg = tmp_path / "note.wav", tmp_path / "note.ogg"
    subprocess.run(["espeak-ng", "-v", "de", "-s", "150", "-w", str(wav), SPOKEN], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libopus", "-b:a", "24k",
                    "-ar", "48000", "-ac", "1", str(ogg)], check=True, capture_output=True)
    blob = ogg.read_bytes()
    assert blob[:4] == b"OggS"
    return blob


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY", "").strip(), reason="OPENAI_API_KEY is not set")
@pytest.mark.skipif(not (shutil.which("espeak-ng") and shutil.which("ffmpeg")), reason="needs espeak-ng and ffmpeg")
def test_a_synthetic_german_voice_note_is_transcribed_live_and_answered(tmp_path, monkeypatch):
    blob = _synthetic_voice_note(tmp_path)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "BRAIN", "luna")
    monkeypatch.setattr(C, "OPENAI_API_KEY", os.environ["OPENAI_API_KEY"].strip())
    monkeypatch.setattr(C, "STT_MODEL", os.environ.get("WA_STT_MODEL", "").strip() or "whisper-1")
    payloads = []
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _PayloadLog(payloads))
    meta = _meta_with(("a1", "audio/ogg", blob))

    started = time.monotonic()
    (result,) = WAPI.handle_payload(webhook(voice_message()), client=meta)["results"]
    seconds = time.monotonic() - started

    (turn,) = payloads
    transcript = turn["latest_inbound"]
    print(f"\nmodel {C.STT_MODEL}, {len(blob)} bytes, {seconds:.1f}s: {transcript!r}")
    assert result["status"] == "sent" and turn["voice_note"] is True
    words = set(re.findall(r"\w+", transcript.lower()))
    assert "münchen" in words and any(w.startswith("pflege") for w in words), transcript
    with ST.db() as c:
        (doc,) = ST.documents_for(c, LEAD)
    assert (doc["text"], doc["text_key"]) == (transcript, "voice_transcript")
