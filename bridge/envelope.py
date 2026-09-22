"""The Meta-shaped envelope one inbound message travels in (TASK-143).

Shape fidelity is the whole trick and it is not cosmetic: ``app/wa/api.py`` parses
``entry[].changes[].value.messages[]`` (``inbound_messages``, ``accept_payload``, ``parse_message``,
``record_inbound_pending``) and so do 31 ``tests/test_wa_*.py`` files. If the server had to be
taught a second payload shape, the adapter would not be thin enough and the design would be wrong.
So the phone rail speaks Meta's dialect, and nothing downstream of the webhook knows a handset was
involved.

WHAT IS HONESTLY DIFFERENT FROM A REAL META PAYLOAD, and is not papered over:
  * ``messages[].id`` is the id WE minted (``wab.i....``), because this rail has no provider id.
    It lands in ``wa_messages.wamid``, which is UNIQUE, so a re-push is dropped as a redelivery --
    the same behaviour a Meta redelivery gets, for free.
  * ``metadata.phone_number_id`` is whatever the server checks against (``api._number_matches``).
    The relay reads it from our own config rather than inventing one, because a value that does not
    match makes the server skip the message as "another WhatsApp number's change" -- silently, as
    far as a candidate is concerned.
  * a media message (document/image/audio/video) carries the id our own executor pulled off the
    handset and can resolve, exactly like a real ``image``/``document`` object Meta would nest under
    the type name (``app/wa/bridge.Client.media_url``/``download_media``, TASK-131). The id is
    content-addressed -- ``wab.m.<sha256(bytes)[:20]>`` (``bridge/media.py``) -- so a retried
    ``download_media`` is byte-identical. It is minted ONLY once the file has been linked to THIS
    message -- automatically (``bridge/identity.py::decide``, TASK-131 round 6) or, when nothing of
    the right kind has a candidate at all, by a human (``Executor.attach_media``). Until either
    happens -- or when the kind is one this rail cannot fetch bytes for at all (location/contact) --
    the message still arrives as its notification placeholder text ("\U0001f4f7 Foto"), ``type``
    staying ``text``, with ``media_kind`` recorded so a human reading the journal knows a file was
    involved.
  * no ``context``, and no ``interactive.button_reply``. A candidate who types "1" typed "1"; the
    button id is recovered server-side (TASK-121). Rewriting it here would record a tap that never
    happened.
"""
from __future__ import annotations

import re


def wa_id(phone):
    """Meta writes ``from``/``wa_id`` as bare digits, no plus."""
    return re.sub(r"\D", "", str(phone or ""))


def timestamp(payload):
    """Meta's ``timestamp`` is epoch SECONDS as a string. The shade gives us milliseconds."""
    return str(int(payload.get("time_ms") or 0) // 1000)


def profile_name(payload):
    """The notification title, unless it is just the number again."""
    title = str(payload.get("title") or "").strip()
    return "" if wa_id(title) == wa_id(payload.get("from")) else title


#: The only kinds this rail can ever fetch bytes for -- matches ``app/wa/api.py:_MEDIA_KINDS`` and
#: ``bridge/media.py:DOWNLOADABLE_KINDS``. ``media_kind`` can also be "location"/"contact"
#: (``bridge/inbound.py:MEDIA_HINTS``), neither of which is ever a file on disk to pull, so neither
#: is ever eligible for a media id and both always fall through to the placeholder text below.
_DOWNLOADABLE_KINDS = ("image", "video", "document", "audio")


def _media_object(payload):
    """-> the ``{"id": ..., "mime_type": ..., "filename": ...}`` object Meta nests media under,
    read back verbatim by ``app/wa/api.py::parse_message`` (``media.get("id")``,
    ``media.get("mime_type")``, ``media.get("filename")``). Optional fields are omitted rather than
    sent as ``null`` -- the same shape Meta itself uses when it has nothing to say about one.

    ``link_strength`` is honestly NOT a Meta field (TASK-131 round 6, Ivan's ruling 2026-09-22): it
    is how ``bridge/identity.py::decide`` attributed this file -- 'strong' (sole candidate, or one
    hard attribute confirmed it), 'weak' (nothing distinguished it from another candidate; picked
    deterministically anyway) or 'human' (the escape hatch, a person named the phone directly).
    Carried here rather than looked up again downstream because THIS is the one payload
    ``app/wa/api.py::parse_message`` already parses field-by-field; a second lookup would be a
    second place to get it wrong. ``app/wa/api.py`` reads it to keep a weakly-attributed document's
    text from ever reaching the model.
    """
    obj = {"id": payload["media_id"]}
    if payload.get("media_mime_type"):
        obj["mime_type"] = payload["media_mime_type"]
    if payload.get("media_filename"):
        obj["filename"] = payload["media_filename"]
    if payload.get("media_link_strength"):
        obj["link_strength"] = payload["media_link_strength"]
    return obj


def meta_envelope(payload, *, phone_number_id, display_phone_number, waba_id):
    """-> one webhook payload carrying exactly one inbound message.

    One message per payload on purpose: the cursor is acked per item, so a payload the server
    rejects halfway would otherwise leave us unable to say which half arrived.
    """
    sender = wa_id(payload.get("from"))
    if not sender:
        raise ValueError("inbound payload has no counterparty number -- it cannot become an envelope")
    if not payload.get("inbound_id"):
        raise ValueError("inbound payload has no id -- refusing to mint one on the server side")
    kind = payload.get("media_kind")
    if kind in _DOWNLOADABLE_KINDS and payload.get("media_id"):
        message = {"from": sender, "id": payload["inbound_id"], "timestamp": timestamp(payload),
                   "type": kind, kind: _media_object(payload)}
    else:
        message = {"from": sender, "id": payload["inbound_id"], "timestamp": timestamp(payload),
                   "type": "text", "text": {"body": payload.get("text") or ""}}
    value = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": display_phone_number,
                     "phone_number_id": phone_number_id},
        "contacts": [{"profile": {"name": profile_name(payload)}, "wa_id": sender}],
        "messages": [message],
    }
    return {"object": "whatsapp_business_account",
            "entry": [{"id": waba_id, "changes": [{"field": "messages", "value": value}]}]}
