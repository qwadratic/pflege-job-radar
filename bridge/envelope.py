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
  * a media message arrives as its notification placeholder text ("\U0001f4f7 Foto") with
    ``media_kind`` recorded alongside. The bytes are not on this path (TASK-131), and synthesising
    an ``image`` object with an id nothing can fetch would make ``download_media`` fail later
    instead of now. ``type`` stays ``text`` and the text is what the phone actually told us.
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
