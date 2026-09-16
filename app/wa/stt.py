"""Speech-to-text for candidate voice notes (TASK-107), WA_BRAIN=luna only.

The old system's approach (apps/connectors/candidate_audio_stt.py on tasker-dispatcher-01, Ivan 2026-09-14: do it
like the old system): OpenAI's transcription endpoint, model whisper-1 (C.STT_MODEL), key from OPENAI_API_KEY, no
language sent (the model detects it), and its suffix rules for the upload name: the endpoint identifies the format by
the file name, and Meta names a voice note ``audio.bin`` or nothing. No openai SDK: one multipart POST over urllib,
with the transport injectable like app/wa/meta.py, so tests use a fake.

Every failure raises TranscriptionError: no key, HTTP or network error, a reply without text, an empty transcript.
The caller (app/wa/api.py) lets it propagate into the TASK-99 recovery path; nothing here guesses a transcript.
"""
import json
import pathlib
import urllib.error
import urllib.request
import uuid

from . import config as C

TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"

# File name extensions the endpoint accepts (API reference, createTranscription ``file``).
ACCEPTED_SUFFIXES = (".flac", ".m4a", ".mp3", ".mp4", ".mpeg", ".mpga", ".ogg", ".wav", ".webm")
# The old system's mime map (_MIME_EXT). WhatsApp voice notes are audio/ogg (codecs=opus).
_MIME_SUFFIX = {"audio/ogg": ".ogg", "audio/opus": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".mp4",
                "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/webm": ".webm", "audio/flac": ".flac",
                "audio/m4a": ".m4a"}
# The old system's last rule: no accepted extension and no known mime type -> .ogg ("WhatsApp voice notes are almost
# always ogg/opus"). A wrong guess is a loud API error, never a transcript.
DEFAULT_SUFFIX = ".ogg"
_SUFFIX_CONTENT_TYPE = {".flac": "audio/flac", ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".mp4": "audio/mp4",
                        ".mpeg": "audio/mpeg", ".mpga": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav",
                        ".webm": "audio/webm"}


class TranscriptionError(RuntimeError):
    """A transcription failed. Carries the HTTP status and parsed body when the API answered."""

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def upload_suffix(filename=None, mime_type=None):
    """The upload name's extension, by the old system's resolve_stt_suffix: the WhatsApp filename's extension when
    the endpoint accepts it, else the mime type's, else DEFAULT_SUFFIX. Only this extension of the untrusted filename
    is used, and only from ACCEPTED_SUFFIXES."""
    suffix = pathlib.PurePath(str(filename or "").strip() or "x").suffix.lower()
    if suffix in ACCEPTED_SUFFIXES:
        return suffix
    return _MIME_SUFFIX.get((mime_type or "").split(";")[0].strip().lower(), DEFAULT_SUFFIX)


def _parse(body):
    """-> the JSON object of a reply body, or {"raw": text} when it is not one."""
    try:
        out = json.loads(body.decode("utf-8")) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        out = None
    return out if isinstance(out, dict) else {"raw": body.decode("utf-8", errors="replace")[:500]}


def _default_transport(method, url, headers=None, data=None, timeout=None):
    """-> the parsed JSON reply. Raises TranscriptionError on an HTTP error (OpenAI's error message included; it masks
    the key), a network error or timeout, and a reply that is not JSON."""
    req = urllib.request.Request(url=url, data=data, method=method.upper())
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        payload = _parse(exc.read())
        error = payload.get("error")
        message = (error.get("message") if isinstance(error, dict) else None) or payload.get("raw")
        raise TranscriptionError(f"OpenAI transcription HTTP {exc.code}" + (f": {message}" if message else ""),
                                 status_code=exc.code, payload=payload) from exc
    except urllib.error.URLError as exc:
        raise TranscriptionError(f"OpenAI transcription network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise TranscriptionError(f"OpenAI transcription did not answer within {timeout}s") from exc
    out = _parse(body)
    if "raw" in out:
        raise TranscriptionError(f"OpenAI transcription did not return JSON: {out['raw'][:300]!r}", payload=out)
    return out


def _multipart(fields, file_field, file_name, content_type, blob):
    """-> (body bytes, Content-Type header) of a multipart/form-data request with ``fields`` and one file part."""
    boundary = f"----pflege-stt-{uuid.uuid4().hex}"
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
                 f"Content-Type: {content_type}\r\n\r\n".encode() + blob + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class Client:
    """One transcription call per voice note. ``transport(method, url, headers, data, timeout)`` -> parsed JSON."""

    def __init__(self, transport=None, api_key=None, model=None, timeout=None):
        self.transport = transport or _default_transport
        self.api_key = C.OPENAI_API_KEY if api_key is None else api_key
        self.model = model or C.STT_MODEL
        self.timeout = timeout or C.STT_TIMEOUT_SEC

    def transcribe(self, blob, filename=None, mime_type=None):
        """-> {text, model, upload_name} for the audio bytes ``blob``. Raises TranscriptionError without a key (no
        request made), on any API failure, and when the transcript is empty."""
        if not self.api_key:
            raise TranscriptionError("OPENAI_API_KEY is not set: the voice note cannot be transcribed")
        suffix = upload_suffix(filename, mime_type)
        upload_name = "voice-note" + suffix
        body, content_type = _multipart({"model": self.model}, "file", upload_name, _SUFFIX_CONTENT_TYPE[suffix],
                                        blob)
        out = self.transport(method="POST", url=TRANSCRIPTIONS_URL, data=body, timeout=self.timeout,
                             headers={"Authorization": "Bearer " + self.api_key, "Content-Type": content_type})
        text = out.get("text") if isinstance(out, dict) else None
        if not isinstance(text, str):
            raise TranscriptionError(f"OpenAI transcription ({self.model}) returned no text: {str(out)[:300]}",
                                     payload=out)
        if not text.strip():
            raise TranscriptionError(f"OpenAI transcription ({self.model}) returned an empty transcript for "
                                     f"{len(blob)} bytes ({upload_name})", payload=out)
        return {"text": text.strip(), "model": self.model, "upload_name": upload_name}
