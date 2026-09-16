"""The webhook: Meta's two routes, plus a small owner-only read of what the harness has been saying.

Order of business on an inbound POST (TASK-99), and the reason for each step:
1. verify the signature over the raw bytes -- everything after this trusts the payload;
2. check the phone-number id: a change for a second WhatsApp number is stored raw, not answered;
3. record, inside the request (``accept_payload``): each message by ``wamid`` (UNIQUE, a Meta redelivery
   stops here) plus its ``wa_inbound_pending`` row; each delivery status per (wamid, status, timestamp),
   a ``failed`` one also as a send failure; every other object raw in ``wa_webhook_events``. No download,
   no brain, no send;
4. hand the phones to the one background worker thread and answer 200. The route only reads the body
   on the event loop; steps 1-4 run in the threadpool, so a slow turn never stalls /wa/health or a
   concurrent webhook;
5. the worker (``drain_pending`` -> ``finish_inbound``) takes each phone's pending messages oldest first:
   arrival bookkeeping; media: store the original under C.DOCUMENTS_DIR (``_store_original``, TASK-95,
   both brains, stopped threads too); a WA_BRAIN=luna document/image is read and classified onto the card
   before the brain runs (``_ingest_media``, TASK-67/TASK-96); other media kinds, and every kind on the
   deterministic brain, get the flat ``MEDIA_REPLY`` ack; decide the reply (app/wa/brain.py or
   app/wa/luna_brain.py, picked in config.py); send, then write the outbound rows; delete the pending row.

A step-5 failure never reaches Meta (it has its 200 already): it is logged, kept on the pending row and in
wa_send_failures (GET /wa/threads), and app/wa/luna/catchup.py finishes the message later through the same
``finish_inbound`` -- re-downloading a media original from the media_id kept in wa_messages.meta, or
re-reading a stored original that never reached the card. A crash or restart mid-turn leaves the pending
row for catch-up too. ``handle_payload`` runs steps 3 and 5 in the caller's thread and re-raises (tests,
scripts).
"""
import hashlib
import json
import logging
import os
import pathlib
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from .. import cv as CV
from . import brain as B
from . import config as C
from . import meta as M
from . import queue as Q
from . import store as ST

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/wa/health")
def wa_health():
    """Non-secret readiness, for a deploy check. Public like the other self-describing reads."""
    return C.readiness()


@router.get("/wa/webhook", include_in_schema=False)
def wa_verify(request: Request):
    p = request.query_params
    challenge = M.verify_webhook_challenge(p.get("hub.mode"), p.get("hub.verify_token"),
                                           p.get("hub.challenge"), C.VERIFY_TOKEN)
    if challenge is None:
        raise HTTPException(403, "verification failed")
    return PlainTextResponse(content=challenge)


@router.post("/wa/webhook", include_in_schema=False)
async def wa_webhook(request: Request):
    raw = await request.body()
    return await run_in_threadpool(receive_webhook, raw, request.headers.get("X-Hub-Signature-256"))


def receive_webhook(raw, signature):
    """POST /wa/webhook off the event loop: verify, record (``accept_payload``), submit, answer."""
    if not M.validate_webhook_signature(raw, signature, C.APP_SECRET):
        raise HTTPException(403, "invalid signature")
    accepted = accept_payload(parse_webhook_body(raw))
    submit_accepted(accepted)
    return accepted_summary(accepted)


def parse_webhook_body(raw):
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "invalid json")
    if not isinstance(payload, dict):
        raise HTTPException(400, "invalid payload")
    return payload


def inbound_messages(payload):
    """The messages in a webhook payload, already flattened and parsed.

    Meta nests them entry[].changes[].value.messages[]; statuses and call events share that envelope
    and are counted, not answered. Interactive replies keep their button id, because a tap has a
    stable id ('dept:OP') while its title is display text.
    """
    out, skipped = [], 0
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            if not _number_matches(value):
                skipped += len(value.get("messages") or [])
                continue
            for m in value.get("messages") or []:
                parsed = parse_message(m)
                if parsed is None:
                    skipped += 1
                    continue
                out.append(parsed)
    return out, skipped


def _number_matches(value):
    """A payload for another WhatsApp number is not ours to answer."""
    if not C.PHONE_NUMBER_ID:
        return True
    return str((value.get("metadata") or {}).get("phone_number_id") or "") == C.PHONE_NUMBER_ID


# A template quick-reply tap's button_id is this prefix + Meta's payload (TASK-100), so it can never equal
# an interactive button id this harness sends itself (luna_brain.CONSENT_BUTTONS 'consent:yes', 'dept:OP').
TEMPLATE_BUTTON_PREFIX = "tpl:"


def parse_message(m):
    """-> {wamid, phone, text, button_id, kind} (+ reply_to_wamid, context) or None for a type this harness
    does not answer.

    Text and button/list replies are answered, so are reactions, stickers, locations, contact cards and messages
    WhatsApp marks unsupported, from a text summary (``SUMMARIZED_KINDS``). A template quick-reply tap (type ``button``) keeps Meta's
    payload as button_id ``tpl:<payload>`` (None when Meta sent none) and the label as text. A message
    with a ``context`` object, any kind, keeps it raw (forwarded flags included) plus the message it
    replies to (``context.id``) as ``reply_to_wamid`` (TASK-100). Media (document/image/audio/video)
    additionally carries ``media_id``/``media_mime_type``/``media_filename`` -- Meta nests those under a field
    keyed by the type name itself, e.g. ``{"image": {"id": "...", "mime_type": "..."}}`` -- so a
    downstream step can fetch and read the actual bytes (app/wa/meta.py:Client.media_url/
    download_media, TASK-67); by itself this function still only parses the payload, it does not
    fetch anything.
    """
    wamid = str(m.get("id") or "").strip()
    phone = M.sender_e164(m.get("from"))
    kind = str(m.get("type") or "")
    if not wamid or not phone:
        return None
    base = {"wamid": wamid, "phone": phone, "kind": kind}
    context = m.get("context")
    if isinstance(context, dict):
        base.update(reply_to_wamid=str(context.get("id") or "").strip() or None, context=context)
    if kind == "text":
        return {**base, "button_id": None, "text": str((m.get("text") or {}).get("body") or "")}
    if kind == "button":                                    # template quick-reply tap
        b = m.get("button") or {}
        payload = str(b.get("payload") or "").strip()
        return {**base, "button_id": TEMPLATE_BUTTON_PREFIX + payload if payload else None,
                "text": str(b.get("text") or b.get("payload") or "")}
    if kind == "interactive":
        i = m.get("interactive") or {}
        reply = i.get("button_reply") or i.get("list_reply") or {}
        bid, title = str(reply.get("id") or "").strip(), str(reply.get("title") or "").strip()
        if not bid and not title:
            return None
        return {**base, "button_id": bid or None, "text": title}
    if kind in ("document", "image", "audio", "video"):
        media = m.get(kind) or {}
        return {**base, "button_id": None, "text": "",
                "media_id": str(media.get("id") or "").strip() or None,
                "media_mime_type": str(media.get("mime_type") or "").strip() or None,
                "media_filename": str(media.get("filename") or "").strip() or None}
    if kind in SUMMARIZED_KINDS:
        return {**base, "button_id": None, **_summarized(kind, m)}
    return None


# Message kinds answered from a text summary (review 2026-09-14: a thumbs-up on the campaign template was stored raw
# only -- no turn, reported as no reply, the candidate counted as a non-responder). The raw object stays in meta.
SUMMARIZED_KINDS = ("reaction", "sticker", "location", "contacts", "unsupported", "unknown")


def _summarized(kind, m):
    """-> {text, reply_to_wamid?, <kind>: raw} for a SUMMARIZED_KINDS message. A reaction replies to the message it
    is on (reaction.message_id); its text is the emoji, '[reaction removed]' when the emoji is empty."""
    raw = m.get(kind)
    if kind == "reaction":
        r = raw if isinstance(raw, dict) else {}
        target = str(r.get("message_id") or "").strip() or None
        return {"text": str(r.get("emoji") or "") or "[reaction removed]", "reply_to_wamid": target, kind: raw}
    if kind == "sticker":
        s = raw if isinstance(raw, dict) else {}
        return {"text": "[sticker]", "media_id": str(s.get("id") or "").strip() or None,
                "media_mime_type": str(s.get("mime_type") or "").strip() or None, kind: raw}
    if kind == "location":
        loc = raw if isinstance(raw, dict) else {}
        place = ", ".join(str(loc[k]) for k in ("name", "address") if loc.get(k))
        coords = f"{loc.get('latitude')}, {loc.get('longitude')}"
        return {"text": f"[location: {place + ' ' if place else ''}({coords})]", kind: raw}
    if kind == "contacts":
        cards = []
        for card in raw if isinstance(raw, list) else []:
            name = str(((card or {}).get("name") or {}).get("formatted_name") or "").strip()
            phones = [str(p.get("phone") or p.get("wa_id") or "") for p in (card or {}).get("phones") or []]
            cards.append(" ".join(filter(None, [name, *phones])) or "?")
        return {"text": f"[contact card: {'; '.join(cards)}]", kind: raw}
    return {"text": "[unsupported message]", "errors": m.get("errors")}


MEDIA_REPLY = ("Danke, angekommen – Dateien kann ich hier noch nicht lesen. Ein Kollege schaut "
               "sie sich an.")

# Media kinds this harness actually extracts text from today, WA_BRAIN=luna only (TASK-67).
# audio/video still get the flat MEDIA_REPLY ack for both brains -- nothing here transcribes
# audio/video, so treating them like "read" would be exactly the invented-safety-net kind of
# silent pretending CLAUDE.md rules out.
_EXTRACTABLE_KINDS = ("document", "image")

# A PDF's extract_text() result this short (or shorter) is treated as "no real text layer" (a
# scanned Urkunde saved as PDF) and falls through to the vision path -- the same "< 20 chars ==
# no readable text" convention CV.analyse()/CV.analyse_llm() already use for a CV upload.
_NEAR_EMPTY_CHARS = 20

_MEDIA_KINDS = ("document", "image", "audio", "video")

# Covers the media types the WhatsApp Cloud API delivers (image, sticker, audio, video, document).
_MIME_SUFFIX = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                "image/gif": ".gif", "application/pdf": ".pdf",
                "audio/aac": ".aac", "audio/amr": ".amr", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
                "audio/ogg": ".ogg", "video/mp4": ".mp4", "video/3gpp": ".3gp", "text/plain": ".txt",
                "application/msword": ".doc", "application/vnd.ms-excel": ".xls",
                "application/vnd.ms-powerpoint": ".ppt",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx"}

# The only part of an untrusted WhatsApp filename that may reach a path: the vision temp file's extension
# (_suffix_for). A stored original never uses it (TASK-95 review, _mime_suffix).
_SAFE_EXTENSION = re.compile(r"\.([A-Za-z0-9]{1,5})\Z")


def _mime_suffix(mime_type):
    """The stored original's extension (TASK-95): the mime type's from _MIME_SUFFIX, else .bin."""
    return _MIME_SUFFIX.get((mime_type or "").split(";")[0].strip().lower(), ".bin")


def _suffix_for(filename, mime_type):
    """A file extension for the temp file CV.extract_text_vision writes, so the CLI's Read tool has a
    hint about what it is opening. Only a trailing 1-5 character alphanumeric extension of the WhatsApp
    filename is used (lowercased), else the mime type's, else .bin."""
    ext = _SAFE_EXTENSION.search(filename or "")
    if ext:
        return "." + ext.group(1).lower()
    return _mime_suffix(mime_type)


def _write_original(phone, media_id, blob, suffix):
    """-> absolute path of ``blob`` stored as <C.DOCUMENTS_DIR>/<phone digits>/<UTC timestamp>-<media
    id alphanumerics><suffix> (TASK-95). No part of the WhatsApp filename is used: the caller passes
    ``_mime_suffix`` (TASK-95 review: '../../evil.sh' used to be stored as '.sh').

    Directories are chmod 0700, the file is mkstemp's 0600. Written to a temp file in the same
    directory, then hard-linked to its name: the name never shows a half-written file, and unlike
    os.replace, os.link raises FileExistsError rather than overwrite an existing original.
    """
    root = pathlib.Path(C.DOCUMENTS_DIR).absolute()
    folder = root / re.sub(r"\D", "", phone)
    for d in (root, folder):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = folder / f"{stamp}-{re.sub(r'[^A-Za-z0-9]', '', media_id)}{suffix}"
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".partial-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, target)
    finally:
        os.unlink(tmp)
    return target


def _store_original(c, m, client=None):
    """Download one inbound media message (Meta's two-step media API, app/wa/meta.py:Client.media_url/
    download_media), store the original bytes (``_write_original``) and link them to the phone and
    wamid with a wa_documents row (TASK-95). Every media kind, both brains, before any extraction --
    a failed extraction still leaves the original stored and linked.

    -> {"id": wa_documents row id, "blob": bytes, "mime_type": ...}. Raises loudly on any failure (bad
    media id, network, disk, an existing target file); a failure after the write leaves the file
    without a row.
    """
    cl = client or M.Client()
    info = cl.media_url(m["media_id"])
    blob = cl.download_media(info["url"])
    mime_type = m.get("media_mime_type") or info.get("mime_type")
    path = _write_original(m["phone"], m["media_id"], blob, _mime_suffix(mime_type))
    doc_id = ST.record_document(c, m["phone"], m["wamid"], m["media_id"], m["kind"], mime_type,
                                m.get("media_filename"), str(path), hashlib.sha256(blob).hexdigest(), len(blob))
    return {"id": doc_id, "blob": blob, "mime_type": mime_type}


def _extract_media_text(kind, blob, filename, mime_type):
    """-> the file's text. A document with its own text layer is read directly (extract_text); a
    bare image, or a scanned PDF with no text layer, goes through the vision path. Which card key the
    text lands on is decided by the classification, not by this method (TASK-96, ``_ingest_media``).
    """
    mt = (mime_type or "").split(";")[0].strip().lower()
    is_pdf = (filename or "").lower().endswith(".pdf") or mt == "application/pdf" or blob[:5] == b"%PDF-"
    if kind == "image" or (kind == "document" and mt.startswith("image/")):
        return CV.extract_text_vision(blob, suffix=_suffix_for(filename, mime_type))
    text = CV.extract_text(filename, blob)
    if is_pdf and len((text or "").strip()) < _NEAR_EMPTY_CHARS:
        return CV.extract_text_vision(blob, suffix=".pdf")
    return text


# TASK-96: the card key a file's text lands on, by classify_document()'s document_type. Any other
# type (auslaendisches_diplom/aufenthaltstitel/dienstplan/other) touches neither key -- its text stays on
# the wa_documents row only. A key's text is appended to, never replaced (TASK-96 review: a two-page CV
# sent as two photos, or a Defizitbescheid then a helfer certificate, lost the earlier file's text, and
# handle_payload feeds only these card keys to CV.analyse_candidate at consent).
_CARD_TEXT_KEY = {"lebenslauf": "cv_text", "urkunde": "urkunde_text", "defizitbescheid": "urkunde_text"}

# document_type of a file the vision model read and found no legible text in (CV.NoReadableText): a final result,
# counts for no gate; the reply turn runs and the model asks for a clearer file (prompts DOCUMENT TYPE).
UNREADABLE = "unreadable"


def read_and_classify(c, doc_id, kind, blob, filename, mime_type):
    """The row half of ``_ingest_media``, shared with app/wa/luna/import_history.py (TASK-102): extract the text
    (``_extract_media_text``), store it on wa_documents row ``doc_id``, classify it (TASK-81: urkunde/lebenslauf/
    defizitbescheid/..., fachkraft-vs-helfer for an urkunde) and store the classification with the card key it
    maps to. -> (text, document_type, certificate_level, text_key). No readable text (CV.NoReadableText) is stored
    as document_type UNREADABLE -> (None, UNREADABLE, None, None): reading it again gives the same answer (review
    2026-09-14: a selfie was re-read by the vision model on every 3-minute catch-up pass and never answered). Raises
    on every other failure (CLI, network, classification), which catch-up retries."""
    try:
        text = _extract_media_text(kind, blob, filename, mime_type)
    except CV.NoReadableText:
        ST.set_document_classification(c, doc_id, UNREADABLE, None, None)
        return None, UNREADABLE, None, None
    ST.set_document_text(c, doc_id, text)
    classification = CV.classify_document(text)
    document_type, certificate_level = classification["document_type"], classification["certificate_level"]
    text_key = _CARD_TEXT_KEY.get(document_type)
    ST.set_document_classification(c, doc_id, document_type, certificate_level, text_key)
    return text, document_type, certificate_level, text_key


def _ingest_media(c, t, m, doc):
    """Read and classify an already-stored document/image (``doc`` from ``_store_original``) onto the
    Luna card, for WA_BRAIN=luna threads only -- the caller (_handle_one) keeps the deterministic
    brain's flat media ack completely separate from this path.

    Card effects (TASK-96): the text is appended to ``_CARD_TEXT_KEY[document_type]`` (cv_text/urkunde_text,
    or no key); document_type/certificate_level stay the latest file's (prompts.py DOCUMENT TYPE);
    ``documents`` gains {id, document_type, certificate_level} -- the list luna_brain's documents gate
    reads, no text and no path since the card goes to the model; ``_documents_just_received`` gains the
    same summary, popped by luna_brain.turn() on the reply turn so the model can tell a file arrived
    even when it is the wrong type. The wa_documents row gets the text, then the classification and
    text_key (TASK-95).

    A file with no readable text lands as document_type UNREADABLE (no text key, counts for no gate), so the
    reply turn tells the candidate. Raises loudly on any other failure -- a failed vision call, a failed
    classification -- there is no silent 'treat it as an empty CV' fallback here (CLAUDE.md, "no
    invented safety nets"); the caller lets that propagate the same way any other turn-processing
    failure already does. The original stays stored and linked either way.
    """
    text, document_type, certificate_level, text_key = read_and_classify(
        c, doc["id"], m["kind"], doc["blob"], m.get("media_filename"), doc["mime_type"])
    slots = t["slots"]
    if text_key:
        slots[text_key] = "\n\n".join(filter(None, (slots.get(text_key), text)))
    slots["document_type"] = document_type
    slots["certificate_level"] = certificate_level
    summary = {"id": doc["id"], "document_type": document_type, "certificate_level": certificate_level}
    slots["documents"] = [*slots.get("documents", []), summary]
    slots["_documents_just_received"] = [*slots.get("_documents_just_received", []), summary]


# --- webhook intake (TASK-99): record in the request, finish in the background -------------------------

_ENVELOPE_KEYS = ("messaging_product", "metadata")


def _call_counterpart(call):
    """The candidate side of a call event: ``from`` on a user-initiated call, ``to`` on a business-initiated one."""
    return {"USER_INITIATED": call.get("from"), "BUSINESS_INITIATED": call.get("to")}.get(call.get("direction"))


# change.value keys whose items each belong to one candidate: the item's phone field. app/wa/router.py splits
# a payload by these; accept_payload keeps what it does not act on raw, with that phone.
PHONE_OF_ITEM = {
    "messages": lambda m: m.get("from"),
    "statuses": lambda s: s.get("recipient_id"),
    "calls": _call_counterpart,
    "contacts": lambda c: c.get("wa_id"),
    "user_preferences": lambda p: p.get("wa_id"),
    "message_echoes": lambda e: e.get("to"),
    "smb_message_echoes": lambda e: e.get("to"),
}


def item_phone(key, item):
    """+E.164 of the candidate one ``PHONE_OF_ITEM`` item belongs to, '' when it names none."""
    return M.sender_e164(PHONE_OF_ITEM[key](item)) if isinstance(item, dict) else ""


def is_call_object(key, item):
    """A WhatsApp Calling object: a call event (``calls``), a call lifecycle status (``statuses`` with type=call,
    id = the call id) or a call-permission reply (interactive ``call_permission_reply``). This harness places and
    answers no calls; app/wa/router.py sends these to the real system whatever the owner."""
    if not isinstance(item, dict):
        return False
    if key == "calls":
        return True
    if key == "statuses":
        return str(item.get("type") or "").strip().lower() == "call"
    if key == "messages":
        return item.get("type") == "interactive" and \
            str((item.get("interactive") or {}).get("type") or "") == "call_permission_reply"
    return False


def inbound_meta(m):
    """wa_messages.meta of a parsed inbound message: every parse_message field beyond the row's own columns
    (button_id, media_id/mime type/filename, ...), so ``message_from_row`` rebuilds the same message later."""
    return {k: v for k, v in m.items() if k not in ("wamid", "phone", "kind", "text")}


def message_from_row(row):
    """The parse_message-shaped dict of a stored inbound wa_messages row."""
    return {"button_id": None, **json.loads(row["meta"] or "{}"), "wamid": row["wamid"], "phone": row["phone"],
            "kind": row["kind"], "text": row["body"]}


def accept_payload(payload):
    """Steps 2-3 of the module docstring for one payload: record, answer nothing. -> {"results": [{wamid,
    status: accepted|duplicate}], "phones": phones with a message, in order, "skipped", "statuses", "events"}.

    Messages parse_message does not answer (reactions, stickers, ...), statuses without id/status/recipient,
    calls, contacts, every other value key, and changes for another phone_number_id go to wa_webhook_events
    raw -- nothing in a signed payload is dropped."""
    out = {"results": [], "phones": [], "skipped": 0, "statuses": 0, "events": 0}
    with ST.db() as c:
        for key, value in payload.items():
            if key not in ("object", "entry"):
                out["events"] += ST.record_webhook_event(c, None, None, key, value)
        entries = payload.get("entry") or []
        if not isinstance(entries, list):
            out["events"] += ST.record_webhook_event(c, None, None, "entry", entries)
            entries = []
        for entry in entries:
            changes = entry.get("changes") if isinstance(entry, dict) else None
            if not isinstance(changes, list):
                out["events"] += ST.record_webhook_event(c, None, None, "entry", entry)
                continue
            for change in changes:
                _accept_change(c, change, out)
    return out


def _accept_change(c, change, out):
    field = change.get("field") if isinstance(change, dict) else None
    value = change.get("value") if isinstance(change, dict) else None
    if not isinstance(value, dict):
        out["events"] += ST.record_webhook_event(c, None, field, "change", change)
        return
    if not _number_matches(value):
        # another WhatsApp number's change, or one without a number at all (template/account updates)
        foreign = bool((value.get("metadata") or {}).get("phone_number_id"))
        out["skipped"] += len(value.get("messages") or [])
        out["events"] += ST.record_webhook_event(c, None, field, "foreign_phone_number_id" if foreign
                                                 else (field or "change"), change)
        return
    for key, items in value.items():
        if key in _ENVELOPE_KEYS:
            continue
        if key not in PHONE_OF_ITEM or not isinstance(items, list):
            out["events"] += ST.record_webhook_event(c, None, field, key, items)
            continue
        for item in items:
            phone = item_phone(key, item)
            if key == "messages":
                parsed = parse_message(item) if isinstance(item, dict) else None
                if parsed is not None:
                    fresh = ST.record_inbound_pending(c, parsed["phone"], parsed["wamid"], parsed["text"],
                                                      kind=parsed["kind"], meta=inbound_meta(parsed))
                    out["results"].append({"wamid": parsed["wamid"], "status": "accepted" if fresh else "duplicate"})
                    if parsed["phone"] not in out["phones"]:
                        out["phones"].append(parsed["phone"])
                    continue
                out["skipped"] += 1
            elif key == "statuses" and phone and item.get("id") and item.get("status") and \
                    not is_call_object(key, item):   # a call status is no message delivery status
                out["statuses"] += ST.record_message_status(c, phone, item)
                continue
            out["events"] += ST.record_webhook_event(c, phone or None, field, key, item)


def accepted_summary(accepted):
    """The route's 200 body. No phone numbers: Meta reads nothing from it."""
    return {"ok": True, "handled": len(accepted["results"]), "skipped": accepted["skipped"],
            "statuses": accepted["statuses"], "events": accepted["events"], "results": accepted["results"]}


def handle_payload(payload, client=None):
    """accept_payload, then finish every phone it named in the caller's thread; the first failure is recorded
    and re-raised. -> the per-message results (a finished message's own result). Tests and scripts; the
    webhook routes use accept_payload + submit_accepted."""
    accepted = accept_payload(payload)
    finished = {r["wamid"]: r for r in process_phones(accepted["phones"], client=client)}
    results = [finished.get(r["wamid"], r) for r in accepted["results"]]
    return {"ok": True, "handled": len(results), "skipped": accepted["skipped"], "results": results}


# One worker thread: turns stay serialized in this process (ST._lock), and nothing slow runs on the event loop
# or holds up the 200. Created on first use, so a process that never receives a webhook starts no thread. At
# interpreter exit the executor still runs every queued job; a killed process leaves pending rows for catch-up.
_background = None
_background_guard = threading.Lock()


def submit_accepted(accepted, client=None):
    """Queue accept_payload's phones for the background worker. -> the Future, or None with no message. The
    Meta client is built here, in the request, and handed to the job."""
    global _background
    if not accepted["phones"]:
        return None
    client = client or M.Client()
    with _background_guard:
        if _background is None:
            _background = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wa-inbound")
        return _background.submit(_process_in_background, list(accepted["phones"]), client)


def _process_in_background(phones, client):
    try:
        return process_phones(phones, client=client, raise_errors=False)
    except Exception:
        log.exception("background processing of %d phone(s) stopped; their pending rows stay for catch-up",
                      len(phones))
        raise


def wait_for_background(timeout=None):
    """Block until every job submitted so far ran (tests, before their monkeypatches are undone)."""
    with _background_guard:
        executor = _background
    if executor is not None:
        executor.submit(lambda: None).result(timeout)


def process_phones(phones, client=None, raise_errors=True):
    """``drain_pending`` for each phone under ST._lock, then its consent queue builds outside the lock. ->
    every result. Shared by handle_payload, the background worker and catch-up."""
    results = []
    for phone in phones:
        with ST._lock, ST.db() as c:
            done = drain_pending(c, phone, client=client, raise_errors=raise_errors)
        build_consent_queues(done, raise_errors=raise_errors)
        results.extend(done)
    return results


def build_consent_queues(results, raise_errors=True):
    """TASK-66 queue build for every result that flipped consent, popping the internal keys. Called outside
    ST._lock: app.autopilot.matching.rank() walks the whole clinic/posting snapshot plus a contact-table read
    per ranked clinic, and a CV/Urkunde reasoning pass (TASK-67) is a real claude CLI call -- inside the lock
    every other thread would wait behind one candidate's match build. A failure is logged and recorded as a
    send failure (raise_errors re-raises it)."""
    for r in results:
        newly_consented_phone = r.pop("_newly_consented_phone", None)
        if not newly_consented_phone:
            continue
        card = r.pop("_card_at_consent")
        try:
            cv_profile = None
            if card.get("cv_text") or card.get("urkunde_text"):
                # "use all chat history + CV as matching input": analyse_candidate folds this thread's
                # own history in alongside whatever was extracted from an upload (TASK-67). No CV/
                # Urkunde text on the card at all -> skip the call rather than feed analyse_llm empty
                # input (it raises ValueError below ~20 chars) -- a normal, common case, not an error.
                with ST.db() as cv_conn:
                    cv_profile = CV.analyse_candidate(newly_consented_phone, cv_conn,
                                                      cv_text=card.get("cv_text"),
                                                      urkunde_text=card.get("urkunde_text"))["profile"]
            Q.build_queue_entry(newly_consented_phone, card, cv_profile)
        except Exception as exc:
            error = f"queue build after consent failed: {type(exc).__name__}: {exc}"
            log.exception(error)
            with ST.db() as c:
                ST.record_send_failure(c, newly_consented_phone, error)
            if raise_errors:
                raise


def drain_pending(c, phone, client=None, raise_errors=True):
    """Finish this phone's pending inbound messages, oldest first. -> one result per message looked at.

    Stops at the first message while any claim of the phone is in flight (``ST.claim_in_flight``: the other
    process -- webhook worker or catch-up -- is working on this phone and continues in order), so two
    processes never run turns for one phone at once. A message is done, and its pending row deleted, unless
    its status is in KEEP_PENDING; claimed_elsewhere with the reply claim already 'sent' is done too. A
    failure is logged, kept on the pending row, recorded in wa_send_failures once per distinct error, and
    re-raised with ``raise_errors``; otherwise the next message runs and the failed one waits for catch-up."""
    results = []
    for row in ST.pending_inbound(c, phone):
        wamid = row["wamid"]
        if ST.claim_in_flight(c, phone):
            results.append({"wamid": wamid, "status": "claimed_elsewhere"})
            break
        if not ST.inbound_is_pending(c, wamid):
            continue   # the other process finished it after this list was read
        try:
            result = finish_inbound(c, message_from_row(row), client=client)
        except Exception as exc:
            error = f"inbound {wamid} not finished: {type(exc).__name__}: {exc}"
            log.exception(error)
            if ST.record_pending_attempt(c, wamid, error) != error:
                ST.record_send_failure(c, phone, error)
            if raise_errors:
                raise
            results.append({"wamid": wamid, "status": "error", "error": error})
            continue
        results.append(result)
        answered = ST.reply_turn_claim_state(c, phone, wamid) == "sent"
        if result["status"] not in KEEP_PENDING or answered:
            ST.finish_pending_inbound(c, wamid)
        elif result["status"] == "claimed_elsewhere":
            break
    return results


MEDIA_CLAIM_PREFIX = "media:"   # wa_reply_turn_claims.turn_key held while one process stores/reads a media message


def finish_inbound(c, m, client=None):
    """Everything after the webhook recorded one inbound message (``m`` as parse_message/message_from_row
    returns it). Shared by the background worker and catch-up, and safe to repeat:

    - arrival bookkeeping is derived from the message row (``_note_arrival``), never incremented twice;
    - media runs under the claim ``media:<wamid>``: no wa_documents row yet -> download with the media_id
      kept in meta and store the original (TASK-95); a WA_BRAIN=luna document/image not on the saved card
      yet -> read and classify it (from the bytes just downloaded, or the stored original re-read and
      checked against its sha256) and save the card before the claim is released;
    - the flat media ack and the reply turn run under the reply claim ``<wamid>`` (process_owed_turn).

    -> the result dict with ``wamid``. Raises on any failure; a brain failure releases the reply claim
    ('skipped_error') so the next attempt need not wait STALE_CLAIM_SECONDS."""
    phone, wamid = m["phone"], m["wamid"]
    t = ST.thread(c, phone)
    dirty = _note_arrival(c, t, wamid)
    is_media = m["kind"] in _MEDIA_KINDS
    reads = is_media and C.BRAIN == "luna" and m["kind"] in _EXTRACTABLE_KINDS
    if is_media:
        media_key = MEDIA_CLAIM_PREFIX + wamid
        if not ST.claim_reply_turn(c, phone, media_key):
            return {"wamid": wamid, "status": "claimed_elsewhere"}
        try:
            if _store_and_read(c, t, m, reads, client):
                ST.save_thread(c, t)
                dirty = False
        except Exception:
            ST.finish_reply_turn_claim(c, phone, media_key, "media_error")
            raise
        ST.finish_reply_turn_claim(c, phone, media_key, "media_done")

    if dirty:
        # TASK-96 review: bookkeeping and ingest are saved before the reply attempt, so a brain or Meta
        # failure below does not lose them; the retry starts from this card.
        ST.save_thread(c, t)
    if t["stopped"]:
        # Opted out earlier. The message (and a media original) is stored and nothing goes out.
        return {"wamid": wamid, "status": "stopped"}
    if is_media and not reads:
        return _media_ack(c, t, m, client)
    try:
        result = process_owed_turn(c, t, m["text"], m["button_id"], wamid, client=client)
    except Exception:
        if ST.reply_turn_claim_state(c, phone, wamid) == "in_progress":
            ST.finish_reply_turn_claim(c, phone, wamid, "skipped_error")
        raise
    if result["status"] not in TURN_NOT_RUN:
        ST.save_thread(c, t)
    result["wamid"] = wamid
    return result


def _note_arrival(c, t, wamid):
    """"A message arrived" bookkeeping for ``wamid``: last_inbound_at is its recorded time (never moved back),
    turns the number of inbound messages up to it (never lowered). -> True when ``t`` changed."""
    at, position = ST.inbound_position(c, wamid)
    last_inbound_at = max(t.get("last_inbound_at") or "", at)
    turns = max(int(t.get("turns") or 0), position)
    changed = (last_inbound_at, turns) != (t.get("last_inbound_at"), t.get("turns"))
    t["last_inbound_at"], t["turns"] = last_inbound_at, turns
    return changed


def _store_and_read(c, t, m, reads, client):
    """The media half of ``finish_inbound``, caller holds ``media:<wamid>``. -> True when the card changed."""
    doc = ST.document_for_wamid(c, m["wamid"])
    if doc is None:
        if not m.get("media_id"):
            raise RuntimeError(f"inbound {m['kind']} {m['wamid']} has no stored original and no media_id to "
                               f"download it with")
        stored = _store_original(c, m, client=client)
    elif reads and not t["stopped"] and not any(d["id"] == doc["id"] for d in t["slots"].get("documents", [])):
        stored = {"id": doc["id"], "blob": _read_original(doc), "mime_type": doc["mime_type"]}
    else:
        return False
    if not reads or t["stopped"]:
        return False
    _ingest_media(c, t, m, stored)
    return True


def _read_original(doc):
    """A stored original's bytes; raises when the file is gone or no longer matches its sha256."""
    blob = pathlib.Path(doc["path"]).read_bytes()
    if hashlib.sha256(blob).hexdigest() != doc["sha256"]:
        raise RuntimeError(f"stored original {doc['path']} no longer matches its sha256")
    return blob


UNREAD_MEDIA_KEY = "_unread_media"   # card: [{wamid, kind, document_id, received_at}] media nobody here reads


def _media_ack(c, t, m, client):
    """Media nothing reads (audio/video; every kind on the deterministic brain), under the reply claim like any other
    reply. MEDIA_REPLY promises that a colleague looks at it, so the card records it for a human first
    (UNREAD_MEDIA_KEY, ``_escalated``; GET /wa/threads ``unread_media``, campaign --status). A declined Luna card
    gets no MEDIA_REPLY: the message is stored, the silence recorded (ST.NO_SEND_STATE) like a model no_send
    (TASK-101; review 2026-09-14: a voice note after the decline ack got MEDIA_REPLY and nobody was flagged)."""
    wamid = m["wamid"]
    if not ST.claim_reply_turn(c, t["phone"], wamid):
        return {"wamid": wamid, "status": "claimed_elsewhere"}
    slots = t["slots"]
    if not any(u["wamid"] == wamid for u in slots.get(UNREAD_MEDIA_KEY, [])):
        doc = ST.document_for_wamid(c, wamid)
        received_at, _ = ST.inbound_position(c, wamid)
        slots[UNREAD_MEDIA_KEY] = [*slots.get(UNREAD_MEDIA_KEY, []), {
            "wamid": wamid, "kind": m["kind"], "document_id": doc["id"] if doc else None, "received_at": received_at}]
    slots["_escalated"] = True
    slots.setdefault("_escalate_reason", f"unread {m['kind']} from the candidate: a colleague must look at it")
    if C.BRAIN == "luna" and slots.get("declined"):
        ST.finish_reply_turn_claim(c, t["phone"], wamid, ST.NO_SEND_STATE)
        ST.save_thread(c, t)
        return {"wamid": wamid, "status": "nothing_to_send", "action": "declined_no_send"}
    try:
        sent = send_and_record(c, t, [MEDIA_REPLY], [], client=client, action="media_ack")
    except Exception:
        ST.finish_reply_turn_claim(c, t["phone"], wamid, "skipped_error")
        raise
    ST.finish_reply_turn_claim(c, t["phone"], wamid, "sent")
    ST.save_thread(c, t)
    return {"wamid": wamid, "status": sent, "action": "media_ack"}


# process_owed_turn statuses that leave ``t`` untouched. The caller skips its save: on claimed_elsewhere
# the other process (webhook or catch-up) saves its own copy, and an earlier copy written over it lost
# last_outbound_at and _session_id (TASK-96 review).
TURN_NOT_RUN = ("claimed_elsewhere", "rate_limited")
# finish_inbound statuses after which the message still owes work: another process holds it, or the rate
# cap deferred the turn. Every other status finishes its wa_inbound_pending row (TASK-99).
KEEP_PENDING = ("claimed_elsewhere", "rate_limited")


def process_owed_turn(c, t, text, button_id, turn_key, client=None):
    """The core decide-and-send pipeline: claim the reply, dispatch to whichever brain is
    configured, send, and detect a fresh consent. Shared by ``_handle_one`` (the webhook path,
    ``turn_key`` = the message that just arrived) and ``app/wa/luna/catchup.py`` (TASK-78,
    ``turn_key`` = the last inbound message a thread is still owed a reply for) -- both must reach
    exactly one reply attempt per inbound message, never two, so both go through the same claim
    (``ST.claim_reply_turn``, TASK-77) instead of duplicating this logic.

    Does not touch ``t["last_inbound_at"]``/``t["turns"]`` or run media ingestion -- those are
    "a new message just arrived" bookkeeping that only ``_handle_one`` owns; a catch-up retry is
    not a new message. Does not call ``ST.save_thread`` either -- the caller does, once, after
    this returns, unless the status is in ``TURN_NOT_RUN`` (``t`` unchanged).

    -> a result dict with at least ``{"status": ...}``. A claim miss or a rate-cap skip returns
    immediately without calling the brain at all -- the caller treats that the same as any other
    "not handled this pass" outcome. An inbound message whose silence is already recorded
    (ST.NO_SEND_STATE, TASK-101) returns ``no_send_recorded``: answered, no claim, no brain call.

    WA_BRAIN=luna (TASK-100): the brain gets ``luna_brain.turn_context`` for ``turn_key`` on
    ``t["turn_context"]`` (removed again afterwards), and after the send the card's LAST_TURN_KEY marker
    records which outbound rows the model itself wrote. A no_send ends the claim in ST.NO_SEND_STATE.
    """
    if ST.reply_turn_claim_state(c, t["phone"], turn_key) == ST.NO_SEND_STATE:
        return {"status": "no_send_recorded"}
    if not ST.claim_reply_turn(c, t["phone"], turn_key):
        return {"status": "claimed_elsewhere"}

    if C.BRAIN == "luna" and C.LUNA_MAX_CALLS_PER_HOUR > 0 and \
            ST.count_recent_luna_calls(c, t["phone"]) >= C.LUNA_MAX_CALLS_PER_HOUR:
        # The message stays recorded and the claim is left in a reclaimable state (TASK-77) --
        # the catch-up driver (TASK-78) is what actually answers it once the window rolls over.
        ST.finish_reply_turn_claim(c, t["phone"], turn_key, "skipped_rate_cap")
        return {"status": "rate_limited"}

    was_consented = C.BRAIN == "luna" and bool((t.get("slots") or {}).get("anonymous_send_consent"))

    if C.BRAIN == "luna":
        from . import luna_brain as LB          # imported lazily: only touched when selected
        t["turn_context"] = LB.turn_context(c, t, turn_key)
        ST.record_luna_call(c, t["phone"])
        try:
            d = LB.turn(text, t, button_id=button_id)
        finally:
            t.pop("turn_context", None)
    else:
        # The deterministic brain knows only its own button ids; a template tap is read as its label, as
        # before TASK-100.
        is_template_tap = str(button_id or "").startswith(TEMPLATE_BUTTON_PREFIX)
        d = B.turn(text, t, button_id=None if is_template_tap else button_id)
    t["slots"], t["asked"] = d["slots"], d["asked"]
    if d["stopped"]:
        t["stopped"], t["stopped_reason"] = True, ST.STOPPED
        ST.finish_reply_turn_claim(c, t["phone"], turn_key, "skipped_stopped")
        return {"status": "stopped", "action": "stopped"}
    if d["matches"]:
        t["matches_sent_at"] = ST.now_iso()

    try:
        sent = send_and_record(c, t, d["bubbles"], d["buttons"], client=client, action=d["action"])
    except Exception:
        ST.finish_reply_turn_claim(c, t["phone"], turn_key, "skipped_error")
        raise
    ST.finish_reply_turn_claim(c, t["phone"], turn_key,
                               ST.NO_SEND_STATE if sent == "nothing_to_send" else "sent")
    if d.get("luna_turn"):   # after the claim is final: a failure here must not make the sent reply retryable
        t["slots"][LB.LAST_TURN_KEY] = LB.turn_marker(c, t["phone"], d["luna_turn"], d["action"])
    if d.get("document_reuse"):   # TASK-102: the candidate's answer on imported documents -> wa_documents, card text
        LB.apply_document_reuse(c, t, d["document_reuse"])

    result = {"status": sent, "action": d["action"],
              "slots": {k: v for k, v in d["slots"].items() if v is not None},
              "matches": [r.get("posting_id") for r in d["matches"]]}
    # TASK-66: a consent flip is durably saved above before this is ever set, so the caller
    # (handle_payload, once the per-message lock is released) can safely build the queue entry --
    # see that function's own comment for why this doesn't happen right here instead.
    now_consented = C.BRAIN == "luna" and bool(d["slots"].get("anonymous_send_consent"))
    if now_consented and not was_consented:
        result["_newly_consented_phone"] = t["phone"]
        result["_card_at_consent"] = dict(d["slots"])
    return result


def _freeform_window_open(t):
    """Meta's own policy, not this repo's choice (TASK-70): free-form text is only deliverable
    within C.FREEFORM_WINDOW_HOURS of the candidate's last message. No message from the candidate
    ever (last_inbound_at unset, e.g. a campaign recipient who never replied) means no window at all
    (TASK-101): Meta does not deliver free text there. Every reply path sets last_inbound_at before it
    sends (finish_inbound -> _note_arrival)."""
    last_inbound = t.get("last_inbound_at")
    if not last_inbound:
        return False
    age_hours = (datetime.now(timezone.utc) - datetime.fromisoformat(last_inbound)).total_seconds() / 3600
    return age_hours < C.FREEFORM_WINDOW_HOURS


def _send(c, t, bubbles, buttons, client=None, action=None):
    """Send the turn and record it. Buttons ride on the last bubble, which is the question.

    With WA_AUTOSEND unset nothing is handed to Meta and the bubbles are stored as 'draft' -- the
    same rows, marked for what they are, so a new deployment can be pointed at the live webhook and
    read back what it *would* have said.

    The free-form-vs-template choice (TASK-70) is made here, in code, never by the brain: whichever
    brain ran still decides *what* to say and produces bubbles normally, but if the 24h window has
    closed since the candidate's last message, those bubbles are not deliverable at all -- Meta
    rejects free-form text outside the window. This swaps in the configured reopen template
    instead of the bubbles, or fails loudly if none is configured, rather than silently trying (and
    having Meta reject it) or silently doing nothing.
    """
    if not bubbles:
        return "nothing_to_send"
    if not _freeform_window_open(t):
        return _send_reopen_template(c, t, client=client, action=action)
    if not C.AUTOSEND:
        for b in bubbles:
            ST.record_outbound(c, t["phone"], None, b, kind="draft", meta={"action": action})
        return "draft"
    cl = client or M.Client()
    for i, b in enumerate(bubbles):
        last = i == len(bubbles) - 1
        if last and buttons:
            wamid = cl.send_buttons(t["phone"], b, buttons)
            ST.record_outbound(c, t["phone"], wamid, b, kind="buttons",
                               meta={"action": action, "buttons": buttons})
        else:
            wamid = cl.send_text(t["phone"], b)
            ST.record_outbound(c, t["phone"], wamid, b, kind="text", meta={"action": action})
    t["last_outbound_at"] = ST.now_iso()
    return "sent"


def send_and_record(c, t, bubbles, buttons, client=None, action=None):
    """Wraps ``_send`` to durably record a Meta send failure before re-raising (TASK-79) -- the
    loud-failure behavior for the caller (a 502, per this module's own docstring) is unchanged;
    what changes is that the failure now leaves a trace (``ST.record_send_failure``, readable via
    GET /wa/threads) instead of vanishing along with the never-persisted thread state."""
    try:
        return _send(c, t, bubbles, buttons, client=client, action=action)
    except Exception as exc:
        ST.record_send_failure(c, t["phone"], str(exc))
        raise


def _send_reopen_template(c, t, client=None, action=None):
    if not C.WA_REOPEN_TEMPLATE_NAME:
        raise RuntimeError(
            f"the WhatsApp free-form window closed for {t['phone']} (last inbound message is over "
            f"{C.FREEFORM_WINDOW_HOURS}h old) and no reopen template is configured -- register a "
            f"template with Meta and set WA_REOPEN_TEMPLATE_NAME before this thread can be reached again")
    label = f"[template:{C.WA_REOPEN_TEMPLATE_NAME}]"
    if not C.AUTOSEND:
        ST.record_outbound(c, t["phone"], None, label, kind="draft_template",
                           meta={"action": action, "template": C.WA_REOPEN_TEMPLATE_NAME})
        return "draft_template"
    cl = client or M.Client()
    wamid = cl.send_template(t["phone"], C.WA_REOPEN_TEMPLATE_NAME, C.WA_REOPEN_TEMPLATE_LANG)
    ST.record_outbound(c, t["phone"], wamid, label, kind="template",
                       meta={"action": action, "template": C.WA_REOPEN_TEMPLATE_NAME})
    t["last_outbound_at"] = ST.now_iso()
    # TASK-75: this harness just reopened a (possibly previously-real-system-owned) conversation
    # with its own template -- that single act hands the conversation to us from now on. A fresh
    # connection (routing.db() applies wa_ownership's own schema, same pattern as queue.py/
    # contacts.py) rather than assuming ``c`` already has that table.
    from . import routing as R
    with R.db() as rc:
        R.flip_to_us_on_reopen(rc, t["phone"])
    return "sent_template"


def _is_stuck(c, phone, last_inbound_at, stopped):
    """True once a thread's ball has been on us (TASK-79) longer than C.STUCK_REPLY_HOURS -- the
    honest, available equivalent of the real system's watchdog: no email/Telegram integration
    exists in this repo, so this is a durable, discoverable flag, not an invented notification."""
    from .luna import reporting as REP   # imported lazily: touches luna_brain, only when read
    if stopped or not last_inbound_at or REP.ball_for(c, phone) != "us":
        return False
    age_hours = (datetime.now(timezone.utc) - datetime.fromisoformat(last_inbound_at)).total_seconds() / 3600
    return age_hours > C.STUCK_REPLY_HOURS


@router.get("/wa/ownership")
def wa_ownership(request: Request):
    """Owner-only: which phones this harness currently owns vs. leaves to the real system
    (app/wa/routing.py, TASK-75). Read-only -- never decides anything itself, only reports what
    route_decision()/flip_to_us_on_reopen() already recorded."""
    from . import routing as R
    phone = request.query_params.get("phone")
    with R.db() as c:
        if phone:
            row = c.execute("select * from wa_ownership where phone=?", (phone,)).fetchone()
            return {"phone": phone, "ownership": dict(row) if row else None}
        rows = c.execute("select * from wa_ownership order by since desc limit 500").fetchall()
    return {"total": len(rows), "rows": [dict(r) for r in rows]}


@router.get("/wa/threads")
def wa_threads(request: Request, limit: int = 50):
    """Owner-only: the threads with their slots, and one thread's messages with ?phone=.

    Gated in app/auth.py the same way /api/autopilot is -- a lead's phone number and what they told
    us is the most personal data this repo holds. Each row also carries ``stuck_reply`` and, when
    one exists, ``last_send_error`` (TASK-79) -- computed here, not stored on the row itself, so
    they always reflect the current time and the latest failure rather than a stale snapshot.

    ``?phone=`` also lists that phone's stored media originals (``documents``, TASK-95): wa_documents
    metadata only -- no file bytes, and no extracted ``text`` (what the brain uses is on the card in
    ``thread.slots``; the per-file text stays in the table).

    TASK-99: a row with unfinished inbound messages carries ``pending_inbound`` (count, oldest, last error),
    and ``stuck_reply`` is also true once the oldest is older than C.STUCK_REPLY_HOURS. ``?phone=`` adds
    ``pending_inbound``, ``message_statuses`` (latest delivery status per wamid, Meta errors included) and
    ``webhook_events`` (the raw objects nothing here acts on: calls, contacts, reactions, ...).
    """
    with ST._lock, ST.db() as c:
        phone = request.query_params.get("phone")
        if phone:
            documents = [{k: v for k, v in d.items() if k != "text"} for d in ST.documents_for(c, phone)]
            return {"phone": phone, "thread": ST.thread(c, phone), "messages": ST.history(c, phone),
                    "documents": documents, "imported_messages": ST.imported_messages_for(c, phone),
                    "pending_inbound": ST.pending_inbound_summary(c, phone),
                    "message_statuses": ST.latest_message_statuses_for(c, phone),
                    "webhook_events": ST.webhook_events_for(c, phone)}
        rows = ST.threads(c, max(1, min(limit, 500)))
        for row in rows:
            pending = ST.pending_inbound_summary(c, row["phone"])
            stuck = _is_stuck(c, row["phone"], row.get("last_inbound_at"), row.get("stopped"))
            oldest = pending["oldest_recorded_at"] if pending else None
            row["stuck_reply"] = stuck or bool(oldest and _hours_since(oldest) > C.STUCK_REPLY_HOURS)
            if pending:
                row["pending_inbound"] = pending
            if row["slots"].get(UNREAD_MEDIA_KEY):
                row["unread_media"] = row["slots"][UNREAD_MEDIA_KEY]   # a colleague was promised to look at these
            failure = ST.recent_send_failure(c, row["phone"])
            if failure:
                row["last_send_error"] = failure
    return {"total": len(rows), "rows": rows}


def _hours_since(iso):
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600
