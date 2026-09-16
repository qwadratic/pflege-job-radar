"""TASK-107: a WA_BRAIN=luna voice note (audio, or audio sent as a document) is transcribed from its stored original and
Luna answers the transcript; a failed transcription is recorded, sends nothing and catch-up retries it. Video and the
deterministic brain keep the flat ack. Fake OpenAI transport, fake Meta client, fake Luna model, tmp SQLite, synthetic
phones and audio bytes -- nothing reaches OpenAI, Meta or the claude CLI.
"""
import email.parser
import email.policy
import io
import json
import time
import urllib.error

import pytest
from fastapi.testclient import TestClient

from app import cv as CV
from app import data as D
from app.wa import api as WAPI
from app.wa import asgi
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import meta as M
from app.wa import store as ST
from app.wa import stt as STT
from app.wa.luna import catchup as CU
from app.wa.luna import shadow_run as SR
from tests.test_wa_media_intake import FakeMetaMedia, _meta_with, _PayloadLog

PHONE_ID = "111222333"
LEAD_DIGITS = "491701234567"
LEAD = "+" + LEAD_DIGITS
APP_SECRET = "test-app-secret"
KEY = "sk-test-voice"
VOICE = b"OggS\x00synthetic opus voice note"
TRANSCRIPT = "Ja, ich habe Interesse. Ich bin examinierte Pflegefachfrau und suche in München."
REPLY = "Danke für Ihre Sprachnachricht! Haben Sie die deutsche Urkunde schon?"


def parse_multipart(headers, data):
    """-> ({field: value}, {field: (filename, content type, bytes)}) of a multipart/form-data request body."""
    raw = f"Content-Type: {headers['Content-Type']}\r\n\r\n".encode() + data
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(raw)
    fields, files = {}, {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if part.get_filename():
            files[name] = (part.get_filename(), part.get_content_type(), part.get_payload(decode=True))
        else:
            fields[name] = part.get_payload(decode=True).decode()
    return fields, files


class FakeOpenAI:
    """Stands in for stt._default_transport: records every request, answers from ``replies`` in order (a dict is the
    JSON reply, an exception is raised); the last reply repeats."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies) or [{"text": TRANSCRIPT}], []

    def __call__(self, method, url, headers=None, data=None, timeout=None):
        fields, files = parse_multipart(headers, data)
        self.requests.append({"method": method, "url": url, "authorization": headers["Authorization"],
                              "fields": fields, "files": files, "timeout": timeout})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def uploads(self):
        return [r["files"]["file"] for r in self.requests]


def use_openai(monkeypatch, *replies, key=KEY):
    """Installs FakeOpenAI as the transcription transport and ``key`` as OPENAI_API_KEY. -> the fake."""
    fake = FakeOpenAI(*replies)
    monkeypatch.setattr(STT, "_default_transport", fake)
    monkeypatch.setattr(C, "OPENAI_API_KEY", key)
    return fake


def voice_message(wamid="wamid.voice", kind="audio", media_id="a1", mime_type="audio/ogg; codecs=opus", filename=None):
    media = {"id": media_id, "mime_type": mime_type, **({"filename": filename} if filename else {})}
    return {"id": wamid, "from": LEAD_DIGITS, "type": kind, kind: media}


def text_message(text, wamid):
    return {"id": wamid, "from": LEAD_DIGITS, "type": "text", "text": {"body": text}}


def webhook(*messages):
    return {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID}, "messages": list(messages)}}]}]}


def _no_document_reading(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a voice note is transcribed, never read as a document")
    monkeypatch.setattr(CV, "extract_text", boom)
    monkeypatch.setattr(CV, "extract_text_vision", boom)
    monkeypatch.setattr(CV, "classify_document", boom)


def _model(monkeypatch, payloads, bubbles=(REPLY,)):
    class Model(_PayloadLog):
        def reply(self, system_text, user_text, session_id=None):
            out, session = super().reply(system_text, user_text, session_id)
            return {**out, "bubbles": list(bubbles)}, session
    monkeypatch.setattr(LB, "Client", lambda *a, **k: Model(payloads))


@pytest.fixture()
def wa(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "APP_SECRET", APP_SECRET)
    monkeypatch.setattr(C, "ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "BRAIN", "luna")
    monkeypatch.setattr(C, "OPENAI_API_KEY", "")
    monkeypatch.setattr(C, "STT_MODEL", "whisper-1")
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 0)

    def no_openai(*a, **k):
        raise AssertionError("this test configured no transcription transport")
    monkeypatch.setattr(STT, "_default_transport", no_openai)


def _stored():
    with ST.db() as c:
        return (ST.thread(c, LEAD), ST.documents_for(c, LEAD),
                {m["wamid"]: m for m in ST.messages_for(c, LEAD)}, ST.pending_inbound(c, LEAD))


# --- the transcription client -----------------------------------------------------------------------------------

def test_transcribe_posts_the_audio_with_model_and_key_and_returns_the_stripped_text(monkeypatch):
    fake = FakeOpenAI({"text": "  Ja, gerne.\n"})
    out = STT.Client(transport=fake, api_key=KEY, model="whisper-1", timeout=77).transcribe(
        VOICE, filename="audio.bin", mime_type="audio/ogg; codecs=opus")
    assert out == {"text": "Ja, gerne.", "model": "whisper-1", "upload_name": "voice-note.ogg"}
    (request,) = fake.requests
    assert (request["method"], request["url"], request["authorization"], request["timeout"]) == \
        ("POST", "https://api.openai.com/v1/audio/transcriptions", "Bearer " + KEY, 77)
    assert request["fields"] == {"model": "whisper-1"}, "no language: the model detects it, as in the old system"
    assert request["files"] == {"file": ("voice-note.ogg", "audio/ogg", VOICE)}


def test_the_model_and_key_come_from_the_environment_config(monkeypatch):
    monkeypatch.setattr(C, "OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setattr(C, "STT_MODEL", "gpt-transcribe")
    fake = FakeOpenAI({"text": "Hallo"})
    assert STT.Client(transport=fake).transcribe(VOICE, mime_type="audio/mpeg")["model"] == "gpt-transcribe"
    assert fake.requests[0]["fields"] == {"model": "gpt-transcribe"}
    assert fake.requests[0]["authorization"] == "Bearer sk-from-env"
    assert fake.uploads == [("voice-note.mp3", "audio/mpeg", VOICE)]


@pytest.mark.parametrize("filename, mime_type, suffix", [
    ("audio.bin", "audio/ogg; codecs=opus", ".ogg"),       # Meta's voice-note name: the mime type decides
    (None, "audio/mpeg", ".mp3"),
    (None, "audio/mp4", ".mp4"),
    ("Aufnahme.M4A", "application/octet-stream", ".m4a"),  # an accepted filename extension wins
    ("../../memo.wav", None, ".wav"),                        # only the extension of the filename is used
    ("note.oga", "audio/ogg", ".ogg"),
    ("run.exe", "audio/webm", ".webm"),
    (None, "audio/aac", ".ogg"),                             # the old system's last rule
    (None, None, ".ogg"),
])
def test_the_upload_suffix_follows_the_old_systems_rules(filename, mime_type, suffix):
    assert STT.upload_suffix(filename, mime_type) == suffix


def test_no_key_raises_before_any_request():
    fake = FakeOpenAI()
    with pytest.raises(STT.TranscriptionError, match="OPENAI_API_KEY is not set"):
        STT.Client(transport=fake, api_key="").transcribe(VOICE, mime_type="audio/ogg")
    assert fake.requests == []


@pytest.mark.parametrize("reply, message", [({"text": "  \n"}, "empty transcript"),
                                            ({"usage": {}}, "returned no text")])
def test_an_empty_or_missing_transcript_raises(reply, message):
    with pytest.raises(STT.TranscriptionError, match=message):
        STT.Client(transport=FakeOpenAI(reply), api_key=KEY).transcribe(VOICE, mime_type="audio/ogg")


def test_the_default_transport_names_the_http_status_and_openais_message(monkeypatch):
    body = json.dumps({"error": {"message": "Incorrect API key provided: sk-te****oice.", "code": "invalid_api_key"}})

    def refuse(req, timeout=None):
        assert req.full_url == STT.TRANSCRIPTIONS_URL and timeout == 5
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(body.encode()))

    monkeypatch.setattr(STT.urllib.request, "urlopen", refuse)
    with pytest.raises(STT.TranscriptionError) as err:
        STT.Client(api_key=KEY, timeout=5).transcribe(VOICE, mime_type="audio/ogg")
    assert str(err.value) == "OpenAI transcription HTTP 401: Incorrect API key provided: sk-te****oice."
    assert err.value.status_code == 401 and KEY not in str(err.value)


def test_health_reports_whether_transcription_is_configured(wa, monkeypatch):
    with TestClient(asgi.app) as client:
        before = client.get("/api/wa/health").json()
        monkeypatch.setattr(C, "OPENAI_API_KEY", KEY)
        monkeypatch.setattr(C, "STT_MODEL", "gpt-transcribe")
        after = client.get("/api/wa/health").json()
    assert (before["stt_ready"], before["checks"]["openai_api_key"], before["stt_model"]) == (False, False, "whisper-1")
    assert (after["stt_ready"], after["checks"]["openai_api_key"], after["stt_model"]) == (True, True, "gpt-transcribe")
    assert KEY not in json.dumps(after)


# --- a voice note on a luna thread ---------------------------------------------------------------------------------

def test_a_voice_note_is_transcribed_from_its_stored_original_and_luna_answers_the_transcript(wa, monkeypatch):
    _no_document_reading(monkeypatch)
    openai = use_openai(monkeypatch)
    payloads = []
    _model(monkeypatch, payloads)
    meta = _meta_with(("a1", "audio/ogg", VOICE))

    WAPI.handle_payload(webhook(text_message("Hallo", "wamid.text")), client=meta)
    (result,) = WAPI.handle_payload(webhook(voice_message()), client=meta)["results"]

    assert (result["status"], result["action"]) == ("sent", "reply_now_conversational")
    assert [s["body"] for s in meta.sent][-1] == REPLY
    assert WAPI.MEDIA_REPLY not in [s["body"] for s in meta.sent]
    assert openai.uploads == [("voice-note.ogg", "audio/ogg", VOICE)]
    text_turn, voice_turn = payloads
    assert (text_turn["latest_inbound"], text_turn["voice_note"]) == ("Hallo", False)
    assert (voice_turn["latest_inbound"], voice_turn["voice_note"]) == (TRANSCRIPT, True)
    assert voice_turn["reply_context"]["kind"] == "audio" and voice_turn["documents_just_received"] == []

    t, (doc,), messages, pending = _stored()
    assert (doc["kind"], doc["text"], doc["text_key"], doc["document_type"]) == \
        ("audio", TRANSCRIPT, "voice_transcript", None)
    inbound = messages["wamid.voice"]
    assert (inbound["kind"], inbound["body"], inbound["meta"]["transcript"], inbound["meta"]["transcript_model"]) == \
        ("audio", TRANSCRIPT, TRANSCRIPT, "whisper-1")
    assert inbound["meta"]["transcribed_at"] and inbound["meta"]["media_id"] == "a1"
    assert "documents" not in t["slots"] and "_unread_media" not in t["slots"] and "_escalated" not in t["slots"]
    assert pending == []
    with TestClient(asgi.app) as client:
        history = client.get("/api/wa/threads", params={"phone": LEAD}).json()["messages"]
    assert [(m["direction"], m["kind"], m["body"]) for m in history][2:4] == \
        [("in", "audio", TRANSCRIPT), ("out", "text", REPLY)]


def test_audio_sent_as_a_document_is_transcribed_the_same_way(wa, monkeypatch):
    _no_document_reading(monkeypatch)
    openai = use_openai(monkeypatch, {"text": "Ich wohne allein."})
    payloads = []
    _model(monkeypatch, payloads)
    meta = _meta_with(("d1", "audio/mpeg", VOICE))
    message = voice_message("wamid.doc", kind="document", media_id="d1", mime_type="audio/mpeg",
                            filename="Sprachnachricht.mp3")
    (result,) = WAPI.handle_payload(webhook(message), client=meta)["results"]

    assert result["status"] == "sent" and openai.uploads == [("voice-note.mp3", "audio/mpeg", VOICE)]
    (turn,) = payloads
    assert (turn["latest_inbound"], turn["voice_note"], turn["reply_context"]["kind"]) == \
        ("Ich wohne allein.", True, "document")
    t, (doc,), messages, _ = _stored()
    assert (doc["kind"], doc["text_key"], doc["path"][-4:]) == ("document", "voice_transcript", ".mp3")
    assert messages["wamid.doc"]["meta"]["transcript"] == "Ich wohne allein." and "documents" not in t["slots"]


def test_a_video_keeps_the_flat_ack_and_is_never_transcribed(wa, monkeypatch):
    _no_document_reading(monkeypatch)
    meta = _meta_with(("v1", "video/mp4", b"\x00\x00\x00 ftypmp4"))
    (result,) = WAPI.handle_payload(webhook(voice_message("wamid.video", kind="video", media_id="v1",
                                                          mime_type="video/mp4")), client=meta)["results"]
    assert result["action"] == "media_ack" and [s["body"] for s in meta.sent] == [WAPI.MEDIA_REPLY]
    t, (doc,), _, _ = _stored()
    assert [u["wamid"] for u in t["slots"]["_unread_media"]] == ["wamid.video"] and doc["text"] is None


def test_the_deterministic_brain_never_transcribes(wa, monkeypatch):
    monkeypatch.setattr(C, "BRAIN", "deterministic")
    use_openai(monkeypatch, AssertionError("the deterministic brain must not transcribe"))
    meta = _meta_with(("a1", "audio/ogg", VOICE))
    (result,) = WAPI.handle_payload(webhook(voice_message()), client=meta)["results"]
    assert result["action"] == "media_ack" and [s["body"] for s in meta.sent] == [WAPI.MEDIA_REPLY]
    _, (doc,), messages, _ = _stored()
    assert doc["text"] is None and "transcript" not in messages["wamid.voice"]["meta"]


def test_a_stopped_thread_stores_the_voice_note_and_transcribes_nothing(wa, monkeypatch):
    use_openai(monkeypatch, AssertionError("a stopped thread must not be transcribed"))
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        t["stopped"], t["stopped_reason"] = True, ST.STOPPED
        ST.save_thread(c, t)
    meta = _meta_with(("a1", "audio/ogg", VOICE))
    (result,) = WAPI.handle_payload(webhook(voice_message()), client=meta)["results"]
    assert result["status"] == "stopped" and meta.sent == []
    _, (doc,), _, pending = _stored()
    assert doc["text"] is None and pending == []


def test_a_spoken_stop_word_or_bundesland_is_read_like_typed_text(wa, monkeypatch):
    """The transcript is the candidate's words: STOP and the locked out-of-scope text apply as to typed text."""
    use_openai(monkeypatch, {"text": "Ich suche eine Stelle in Hessen."},
               {"text": "Stopp, bitte nicht mehr schreiben."})
    payloads = []
    _model(monkeypatch, payloads)
    meta = _meta_with(("a1", "audio/ogg", VOICE), ("a2", "audio/ogg", VOICE + b"2"))
    (region,) = WAPI.handle_payload(webhook(voice_message("wamid.v1", media_id="a1")), client=meta)["results"]
    (stop,) = WAPI.handle_payload(webhook(voice_message("wamid.v2", media_id="a2")), client=meta)["results"]
    assert region["action"] == "out_of_scope_region" and stop["status"] == "stopped"
    assert [s["body"] for s in meta.sent] == [LB.P.OUT_OF_SCOPE_REGION_DE] and payloads == []
    assert _stored()[0]["stopped"] is True


# --- failures: recorded, nothing sent, catch-up retries from the stored original ---------------------------------

def _post(client, body):
    import hashlib
    import hmac
    raw = json.dumps(body).encode()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/api/wa/webhook", content=raw,
                       headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"})


FAILURES = {
    "no_key": ((), "", "TranscriptionError: OPENAI_API_KEY is not set: the voice note cannot be transcribed"),
    "api_error": ((STT.TranscriptionError("OpenAI transcription HTTP 500: The server had an error", status_code=500),),
                  KEY, "TranscriptionError: OpenAI transcription HTTP 500: The server had an error"),
    "empty_transcript": (({"text": ""},), KEY,
                         "TranscriptionError: OpenAI transcription (whisper-1) returned an empty transcript for "
                         f"{len(VOICE)} bytes (voice-note.ogg)"),
}


@pytest.mark.parametrize("failure", list(FAILURES))
def test_a_failed_transcription_is_recorded_sends_nothing_and_catch_up_retries_from_the_stored_original(
        wa, monkeypatch, failure):
    replies, key, error = FAILURES[failure]
    first = use_openai(monkeypatch, *(replies or ({"text": "never used"},)), key=key)
    payloads = []
    _model(monkeypatch, payloads)
    meta = _meta_with(("a1", "audio/ogg", VOICE))
    monkeypatch.setattr(M, "Client", lambda *a, **k: meta)
    monkeypatch.setattr(C, "STUCK_REPLY_HOURS", 0)

    with TestClient(asgi.app) as client:                  # the webhook route, the background worker
        assert _post(client, webhook(voice_message())).status_code == 200
        WAPI.wait_for_background(30)
        (row,) = client.get("/api/wa/threads").json()["rows"]
    expected = f"inbound wamid.voice not finished: {error}"
    assert meta.sent == [] and payloads == [], "no flat reply, no model turn"
    assert len(first.requests) == (0 if failure == "no_key" else 1)
    assert row["pending_inbound"]["last_error"] == expected and row["stuck_reply"] is True
    assert row["last_send_error"]["error"] == expected
    _, (doc,), messages, (pending,) = _stored()
    assert doc["text"] is None and messages["wamid.voice"]["body"] == "" and pending["attempts"] == 1
    assert "transcript" not in messages["wamid.voice"]["meta"]

    failed_again = CU.run(client=FakeMetaMedia())
    assert [r["status"] for r in failed_again] == ["error"] and _stored()[3][0]["attempts"] == 2

    retry = use_openai(monkeypatch, {"text": TRANSCRIPT})
    no_media = FakeMetaMedia()                            # serves no media id: a second download would raise
    (result,) = CU.run(client=no_media)
    assert result["status"] == "sent" and no_media.download_calls == []
    assert retry.uploads == [("voice-note.ogg", "audio/ogg", VOICE)], "the stored original, sha256 checked"
    assert [s["body"] for s in no_media.sent] == [REPLY]
    (turn,) = payloads
    assert (turn["latest_inbound"], turn["voice_note"]) == (TRANSCRIPT, True)
    _, (doc,), messages, pending = _stored()
    assert doc["text"] == TRANSCRIPT and messages["wamid.voice"]["meta"]["transcript"] == TRANSCRIPT and pending == []
    with ST.db() as c:
        failures = [r["error"] for r in c.execute("select error from wa_send_failures order by id")]
    assert failures == [expected], "recorded once per distinct error"


def test_a_changed_stored_original_is_a_recorded_error_not_a_transcript(wa, monkeypatch):
    use_openai(monkeypatch, {"text": ""})
    meta = _meta_with(("a1", "audio/ogg", VOICE))
    WAPI.accept_payload(webhook(voice_message()))
    WAPI.process_phones([LEAD], client=meta, raise_errors=False)
    _, (doc,), _, _ = _stored()
    with open(doc["path"], "wb") as f:
        f.write(b"something else")
    retry = use_openai(monkeypatch, {"text": TRANSCRIPT})
    (result,) = CU.run(client=FakeMetaMedia())
    assert result["status"] == "error" and "no longer matches its sha256" in result["error"] and retry.requests == []


def test_a_brain_failure_after_transcription_reuses_the_stored_transcript(wa, monkeypatch):
    openai = use_openai(monkeypatch)

    class Timeout:
        def reply(self, system_text, user_text, session_id=None):
            raise RuntimeError("claude -p did not answer within 120s")

    monkeypatch.setattr(LB, "Client", lambda *a, **k: Timeout())
    meta = _meta_with(("a1", "audio/ogg", VOICE))
    WAPI.accept_payload(webhook(voice_message()))
    (failed,) = WAPI.process_phones([LEAD], client=meta, raise_errors=False)
    assert failed["status"] == "error" and "claude -p did not answer" in failed["error"] and meta.sent == []

    payloads = []
    _model(monkeypatch, payloads)
    (result,) = CU.run(client=FakeMetaMedia())
    assert result["status"] == "sent" and len(openai.requests) == 1, "transcribed once"
    assert [(p["latest_inbound"], p["voice_note"]) for p in payloads] == [(TRANSCRIPT, True)]


def test_the_dry_run_answers_a_voice_notes_stored_transcript(wa, monkeypatch):
    real_client = LB.Client
    use_openai(monkeypatch)
    _model(monkeypatch, [])
    WAPI.handle_payload(webhook(voice_message()), client=_meta_with(("a1", "audio/ogg", VOICE)))
    payloads = []

    def dry_model(system_text, user_text, session_id):
        payloads.append(json.loads(user_text))
        return {"action": "reply_now_conversational", "bubbles": ["ok"], "rationale": "", "escalate_to_manager": False,
                "escalate_reason": None, "no_send": False, "next_ask": None, "card_patch": {}}, "dry-session"

    with ST.db() as c:
        report = SR.shadow_turn(c, LEAD, client=real_client(reply=dry_model))
    assert report["bubbles"] == ["ok"]
    assert [(p["latest_inbound"], p["voice_note"]) for p in payloads] == [(TRANSCRIPT, True)]


def test_the_prompt_names_the_payload_marker_the_harness_sends():
    prompt = LB.P.system_prompt("{}", "{}")
    assert "VOICE NOTE (TASK-107): voice_note=true" in prompt
    assert "card._unread_media lists videos" in prompt
