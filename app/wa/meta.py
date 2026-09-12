"""Meta WhatsApp Cloud API: webhook trust boundary and the two sends this harness needs.

Signature and challenge verification are copied from the production client
(apps/connectors/meta_whatsapp_cloud.py on tasker-dispatcher-01) because they *are* the inbound
trust boundary; the client itself is trimmed to text and reply buttons. urllib, not requests, so the
transport can be swapped for a fake in tests (``transport=``) without a network stub.
"""
import hashlib
import hmac
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import config as C


class MetaError(RuntimeError):
    """A Meta call failed. Carries the status and the parsed body so a log line can name the cause."""

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def validate_webhook_signature(raw_body, signature_header, app_secret):
    """X-Hub-Signature-256 over the *raw* bytes. No secret or no header means no, never yes."""
    if not app_secret or not signature_header:
        return False
    header = str(signature_header).strip()
    if not header.lower().startswith("sha256="):
        return False
    expected = header.split("=", 1)[1].strip()
    digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


def verify_webhook_challenge(mode, token, challenge, verify_token):
    """Meta's GET handshake: echo hub.challenge only for mode=subscribe with the right token."""
    if mode != "subscribe" or not challenge:
        return None
    if not verify_token or token != verify_token:
        return None
    return str(challenge)


def sender_e164(sender):
    """Meta sends the wa_id as bare digits; the harness keys threads on +digits."""
    digits = re.sub(r"\D", "", str(sender or ""))
    return "+" + digits if digits else ""


def canonicalize_phone(raw, default_country_code=None):
    """One identity per human: '0170…', '0049170…', '+49170…' and '49170…' all become '+49170…'."""
    text = str(raw or "").strip()
    if not text:
        return ""
    cc = re.sub(r"\D", "", default_country_code or C.DEFAULT_COUNTRY_CODE) or "49"
    if text.startswith("+"):
        digits = re.sub(r"\D", "", text)
    elif text.startswith("00"):
        digits = re.sub(r"\D", "", text[2:])
    else:
        digits = re.sub(r"\D", "", text)
        if digits.startswith("0") and len(digits) >= 10:
            digits = cc + digits[1:]
        elif len(digits) <= 11 and not digits.startswith(cc):
            digits = cc + digits
    return "+" + digits if digits else ""


def _default_transport(method, url, headers=None, data=None, timeout=C.HTTP_TIMEOUT_SEC):
    req = urllib.request.Request(url=url, data=data, method=method.upper())
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {"raw": body.decode("utf-8", errors="replace")}
        raise MetaError(f"Meta HTTP {exc.code}", status_code=exc.code, payload=payload) from exc
    except urllib.error.URLError as exc:
        raise MetaError(f"Meta network error: {exc.reason}") from exc
    try:
        return json.loads(body.decode("utf-8")) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"raw": body.decode("utf-8", errors="replace"), "status_code": status}


class Client:
    """Outbound half of the Cloud API. One instance per request; state lives in SQLite, not here."""

    def __init__(self, transport=None, access_token=None, phone_number_id=None):
        self.transport = transport or _default_transport
        self.access_token = C.ACCESS_TOKEN if access_token is None else access_token
        self.phone_number_id = C.PHONE_NUMBER_ID if phone_number_id is None else phone_number_id

    def _post(self, payload):
        if not self.access_token or not self.phone_number_id:
            raise MetaError("META_WHATSAPP_ACCESS_TOKEN / META_WHATSAPP_PHONE_NUMBER_ID are not set")
        url = f"https://graph.facebook.com/{C.GRAPH_API_VERSION}/{self.phone_number_id}/messages"
        body = json.dumps(payload).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.access_token, "Content-Type": "application/json"}
        out = self.transport(method="POST", url=url, headers=headers, data=body)
        wamid = (((out or {}).get("messages") or [{}])[0] or {}).get("id")
        if not wamid:
            # The call came back 2xx without a message id: we do not know whether it went out, and
            # saying "sent" here is the one lie that would corrupt the thread. Fail loudly instead.
            raise MetaError("Meta accepted the call but returned no message id", payload=out)
        return wamid

    def send_text(self, to_e164, body):
        return self._post({"messaging_product": "whatsapp", "to": re.sub(r"\D", "", to_e164),
                           "type": "text", "text": {"body": body}})

    def send_buttons(self, to_e164, body, buttons):
        """Reply buttons: Meta allows at most 3, titles at most 20 characters. Both are hard errors
        here rather than a silent trim, because a trimmed title changes what the candidate answers."""
        if not buttons:
            raise MetaError("send_buttons needs at least one button")
        if len(buttons) > 3:
            raise MetaError(f"Meta allows 3 reply buttons, got {len(buttons)}")
        rows = []
        for b in buttons:
            bid, title = str(b.get("id") or "").strip(), str(b.get("title") or "").strip()
            if not bid or not title:
                raise MetaError(f"button needs id and title: {b!r}")
            if len(title) > 20:
                raise MetaError(f"button title over 20 chars: {title!r}")
            rows.append({"type": "reply", "reply": {"id": bid, "title": title}})
        return self._post({"messaging_product": "whatsapp", "recipient_type": "individual",
                           "to": re.sub(r"\D", "", to_e164), "type": "interactive",
                           "interactive": {"type": "button", "body": {"text": body},
                                           "action": {"buttons": rows}}})
