"""The webhook: Meta's two routes, plus a small owner-only read of what the harness has been saying.

Order of business on an inbound POST, and the reason for each step:
1. verify the signature over the raw bytes -- everything after this trusts the payload;
2. check the phone-number id, so a webhook wired to a second WhatsApp number is ignored, not answered;
3. insert by ``wamid``, which is UNIQUE -- a Meta redelivery is dropped here and answered once;
3b. a document/image on a WA_BRAIN=luna thread is downloaded and its text extracted onto the
    card (cv_text/urkunde_text, see ``_ingest_media``, TASK-67) before the brain ever runs, so it
    reacts to what it just read; every other media kind, and every kind on the deterministic
    brain, still gets the flat ``MEDIA_REPLY`` ack this always had;
4. decide the reply -- app/wa/brain.py (deterministic, default) or app/wa/luna_brain.py
   (WA_BRAIN=luna, Claude-driven), picked once in config.py so this route does not care which;
5. send it, and only then write the outbound rows.

Step 5 fails loudly: a Meta error propagates, the route answers 502 and the turn is *not* recorded as
sent, so the redelivery Meta then makes finds no outbound row and the lead does get an answer.
"""
import pathlib
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .. import cv as CV
from . import brain as B
from . import config as C
from . import meta as M
from . import queue as Q
from . import store as ST

router = APIRouter()


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
async def wa_webhook(request: Request, client=None):
    raw = await request.body()
    if not M.validate_webhook_signature(raw, request.headers.get("X-Hub-Signature-256"), C.APP_SECRET):
        raise HTTPException(403, "invalid signature")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid json")
    if not isinstance(payload, dict):
        raise HTTPException(400, "invalid payload")
    return handle_payload(payload, client=client)


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


def parse_message(m):
    """-> {wamid, phone, text, button_id, kind} or None for a type this harness does not answer.

    Text and button/list replies are answered. Media (document/image/audio/video) additionally
    carries ``media_id``/``media_mime_type``/``media_filename`` -- Meta nests those under a field
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
    if kind == "text":
        return {"wamid": wamid, "phone": phone, "kind": kind, "button_id": None,
                "text": str((m.get("text") or {}).get("body") or "")}
    if kind == "button":                                    # template quick-reply tap
        b = m.get("button") or {}
        return {"wamid": wamid, "phone": phone, "kind": kind, "button_id": None,
                "text": str(b.get("text") or b.get("payload") or "")}
    if kind == "interactive":
        i = m.get("interactive") or {}
        reply = i.get("button_reply") or i.get("list_reply") or {}
        bid, title = str(reply.get("id") or "").strip(), str(reply.get("title") or "").strip()
        if not bid and not title:
            return None
        return {"wamid": wamid, "phone": phone, "kind": kind, "button_id": bid or None, "text": title}
    if kind in ("document", "image", "audio", "video"):
        media = m.get(kind) or {}
        return {"wamid": wamid, "phone": phone, "kind": kind, "button_id": None, "text": "",
                "media_id": str(media.get("id") or "").strip() or None,
                "media_mime_type": str(media.get("mime_type") or "").strip() or None,
                "media_filename": str(media.get("filename") or "").strip() or None}
    return None


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

_MIME_SUFFIX = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                "image/gif": ".gif", "application/pdf": ".pdf"}


def _suffix_for(filename, mime_type):
    """A file extension for the temp file CV.extract_text_vision writes -- from the WhatsApp
    filename when Meta sent one, else guessed from the mime type, so the CLI's Read tool has a
    hint about what kind of file it is opening."""
    ext = pathlib.Path(filename or "").suffix
    if ext:
        return ext
    return _MIME_SUFFIX.get((mime_type or "").split(";")[0].strip().lower(), ".bin")


def _extract_media_text(kind, blob, filename, mime_type):
    """-> (text, card_key). card_key is 'cv_text' for a document whose own text layer was read
    directly (extract_text), or 'urkunde_text' for anything that needed the vision path -- a bare
    image, or a scanned PDF with no text layer, treated the same as an image per this task's
    design (see backlog TASK-67 notes): a photographed/scanned document is far more often the
    Urkunde/certificate than the CV itself.
    """
    mt = (mime_type or "").split(";")[0].strip().lower()
    is_pdf = (filename or "").lower().endswith(".pdf") or mt == "application/pdf" or blob[:5] == b"%PDF-"
    if kind == "image" or (kind == "document" and mt.startswith("image/")):
        return CV.extract_text_vision(blob, suffix=_suffix_for(filename, mime_type)), "urkunde_text"
    text = CV.extract_text(filename, blob)
    if is_pdf and len((text or "").strip()) < _NEAR_EMPTY_CHARS:
        return CV.extract_text_vision(blob, suffix=".pdf"), "urkunde_text"
    return text, "cv_text"


def _ingest_media(c, t, m, client=None):
    """Download this document/image (Meta's two-step media API, app/wa/meta.py:Client.media_url/
    download_media) and merge its extracted text onto the Luna card as cv_text/urkunde_text, for
    WA_BRAIN=luna threads only -- the caller (_handle_one) keeps the deterministic brain's flat
    media ack completely separate from this path.

    Raises loudly on any failure -- a bad media id, a network error, or a vision call that found
    no readable text -- there is no silent 'treat it as an empty CV' fallback here (CLAUDE.md, "no
    invented safety nets"); the caller lets that propagate the same way any other turn-processing
    failure already does.
    """
    cl = client or M.Client()
    info = cl.media_url(m["media_id"])
    blob = cl.download_media(info["url"])
    mime_type = m.get("media_mime_type") or info.get("mime_type")
    text, card_key = _extract_media_text(m["kind"], blob, m.get("media_filename"), mime_type)
    t["slots"][card_key] = text
    # TASK-81: classify what was actually just sent (urkunde/lebenslauf/defizitbescheid/
    # aufenthaltstitel/dienstplan/other, plus fachkraft-vs-helfer for an urkunde) -- surfaced onto
    # the card so the model sees it in its normal payload (luna_brain._user_payload's "card" is the
    # whole slots dict, no separate wiring needed) and prompts.py's DOCUMENT TYPE rule tells it a
    # helfer-level certificate must never read as satisfying a Fachkraft qualification path.
    classification = CV.classify_document(text)
    t["slots"]["document_type"] = classification["document_type"]
    t["slots"]["certificate_level"] = classification["certificate_level"]


def handle_payload(payload, client=None):
    """Every message in one webhook call. -> a per-message result list, which is also the route's body."""
    messages, skipped = inbound_messages(payload)
    results = []
    with ST._lock, ST.db() as c:
        for m in messages:
            results.append(_handle_one(c, m, client=client))
    # Queue building (TASK-66) happens here, deliberately outside the lock just released above:
    # app.autopilot.matching.rank() walks the whole clinic/posting snapshot plus a contact-table
    # read per ranked clinic, and a CV/Urkunde reasoning pass (TASK-67) is a real claude CLI call --
    # doing either inside the per-message critical section would serialize every other inbound
    # WhatsApp thread behind one candidate's match build.
    for r in results:
        newly_consented_phone = r.pop("_newly_consented_phone", None)
        if not newly_consented_phone:
            continue
        card = r.pop("_card_at_consent")
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
    return {"ok": True, "handled": len(results), "skipped": skipped, "results": results}


def _handle_one(c, m, client=None):
    fresh = ST.record_inbound(c, m["phone"], m["wamid"], m["text"], kind=m["kind"],
                              meta={"button_id": m["button_id"]})
    if not fresh:
        return {"wamid": m["wamid"], "status": "duplicate"}

    t = ST.thread(c, m["phone"])
    t["last_inbound_at"] = ST.now_iso()
    t["turns"] = int(t.get("turns") or 0) + 1

    if t["stopped"]:
        # Opted out earlier. The message is stored (it is the lead's own word) and nothing goes out.
        ST.save_thread(c, t)
        return {"wamid": m["wamid"], "status": "stopped"}

    if m["kind"] in ("document", "image", "audio", "video"):
        if not (C.BRAIN == "luna" and m["kind"] in _EXTRACTABLE_KINDS):
            sent = _send_and_record(c, t, [MEDIA_REPLY], [], client=client, action="media_ack")
            ST.save_thread(c, t)
            return {"wamid": m["wamid"], "status": sent, "action": "media_ack"}
        _ingest_media(c, t, m, client=client)

    result = process_owed_turn(c, t, m["text"], m["button_id"], m["wamid"], client=client)
    ST.save_thread(c, t)
    result["wamid"] = m["wamid"]
    return result


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
    this returns, exactly as before this function existed.

    -> a result dict with at least ``{"status": ...}``. A claim miss or a rate-cap skip returns
    immediately without calling the brain at all -- the caller treats that the same as any other
    "not handled this pass" outcome.
    """
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
        ST.record_luna_call(c, t["phone"])
        d = LB.turn(text, t, button_id=button_id)
    else:
        d = B.turn(text, t, button_id=button_id)
    t["slots"], t["asked"] = d["slots"], d["asked"]
    if d["stopped"]:
        t["stopped"], t["stopped_reason"] = True, ST.STOPPED
        ST.finish_reply_turn_claim(c, t["phone"], turn_key, "skipped_stopped")
        return {"status": "stopped", "action": "stopped"}
    if d["matches"]:
        t["matches_sent_at"] = ST.now_iso()

    try:
        sent = _send_and_record(c, t, d["bubbles"], d["buttons"], client=client, action=d["action"])
    except Exception:
        ST.finish_reply_turn_claim(c, t["phone"], turn_key, "skipped_error")
        raise
    ST.finish_reply_turn_claim(c, t["phone"], turn_key,
                               "skipped_no_send" if sent == "nothing_to_send" else "sent")

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
    within C.FREEFORM_WINDOW_HOURS of the candidate's last message. A thread that has never heard
    from anyone yet (last_inbound_at unset) is not a "reopen" case -- treat it as open."""
    last_inbound = t.get("last_inbound_at")
    if not last_inbound:
        return True
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


def _send_and_record(c, t, bubbles, buttons, client=None, action=None):
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


@router.get("/wa/threads")
def wa_threads(request: Request, limit: int = 50):
    """Owner-only: the threads with their slots, and one thread's messages with ?phone=.

    Gated in app/auth.py the same way /api/autopilot is -- a lead's phone number and what they told
    us is the most personal data this repo holds. Each row also carries ``stuck_reply`` and, when
    one exists, ``last_send_error`` (TASK-79) -- computed here, not stored on the row itself, so
    they always reflect the current time and the latest failure rather than a stale snapshot.
    """
    with ST._lock, ST.db() as c:
        phone = request.query_params.get("phone")
        if phone:
            return {"phone": phone, "thread": ST.thread(c, phone), "messages": ST.history(c, phone)}
        rows = ST.threads(c, max(1, min(limit, 500)))
        for row in rows:
            row["stuck_reply"] = _is_stuck(c, row["phone"], row.get("last_inbound_at"), row.get("stopped"))
            failure = ST.recent_send_failure(c, row["phone"])
            if failure:
                row["last_send_error"] = failure
    return {"total": len(rows), "rows": rows}
