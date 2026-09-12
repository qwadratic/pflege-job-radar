"""The webhook: Meta's two routes, plus a small owner-only read of what the harness has been saying.

Order of business on an inbound POST, and the reason for each step:
1. verify the signature over the raw bytes -- everything after this trusts the payload;
2. check the phone-number id, so a webhook wired to a second WhatsApp number is ignored, not answered;
3. insert by ``wamid``, which is UNIQUE -- a Meta redelivery is dropped here and answered once;
4. decide the reply -- app/wa/brain.py (deterministic, default) or app/wa/luna_brain.py
   (WA_BRAIN=luna, Claude-driven), picked once in config.py so this route does not care which;
5. send it, and only then write the outbound rows.

Step 5 fails loudly: a Meta error propagates, the route answers 502 and the turn is *not* recorded as
sent, so the redelivery Meta then makes finds no outbound row and the lead does get an answer.
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from . import brain as B
from . import config as C
from . import meta as M
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

    Text and button/list replies are answered. Media arrives as a placeholder: the harness has no
    document pipeline, so it says so rather than staying silent on a CV a lead just sent.
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
        return {"wamid": wamid, "phone": phone, "kind": kind, "button_id": None, "text": ""}
    return None


MEDIA_REPLY = ("Danke, angekommen – Dateien kann ich hier noch nicht lesen. Ein Kollege schaut "
               "sie sich an.")


def handle_payload(payload, client=None):
    """Every message in one webhook call. -> a per-message result list, which is also the route's body."""
    messages, skipped = inbound_messages(payload)
    results = []
    with ST._lock, ST.db() as c:
        for m in messages:
            results.append(_handle_one(c, m, client=client))
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
        sent = _send(c, t, [MEDIA_REPLY], [], client=client, action="media_ack")
        ST.save_thread(c, t)
        return {"wamid": m["wamid"], "status": sent, "action": "media_ack"}

    if C.BRAIN == "luna":
        from . import luna_brain as LB          # imported lazily: only touched when selected
        d = LB.turn(m["text"], t, button_id=m["button_id"])
    else:
        d = B.turn(m["text"], t, button_id=m["button_id"])
    t["slots"], t["asked"] = d["slots"], d["asked"]
    if d["stopped"]:
        t["stopped"], t["stopped_reason"] = True, ST.STOPPED
        ST.save_thread(c, t)
        return {"wamid": m["wamid"], "status": "stopped", "action": "stopped"}
    if d["matches"]:
        t["matches_sent_at"] = ST.now_iso()
    sent = _send(c, t, d["bubbles"], d["buttons"], client=client, action=d["action"])
    ST.save_thread(c, t)
    return {"wamid": m["wamid"], "status": sent, "action": d["action"],
            "slots": {k: v for k, v in d["slots"].items() if v is not None},
            "matches": [r.get("posting_id") for r in d["matches"]]}


def _send(c, t, bubbles, buttons, client=None, action=None):
    """Send the turn and record it. Buttons ride on the last bubble, which is the question.

    With WA_AUTOSEND unset nothing is handed to Meta and the bubbles are stored as 'draft' -- the
    same rows, marked for what they are, so a new deployment can be pointed at the live webhook and
    read back what it *would* have said.
    """
    if not bubbles:
        return "nothing_to_send"
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


@router.get("/wa/threads")
def wa_threads(request: Request, limit: int = 50):
    """Owner-only: the threads with their slots, and one thread's messages with ?phone=.

    Gated in app/auth.py the same way /api/autopilot is -- a lead's phone number and what they told
    us is the most personal data this repo holds.
    """
    with ST._lock, ST.db() as c:
        phone = request.query_params.get("phone")
        if phone:
            return {"phone": phone, "thread": ST.thread(c, phone), "messages": ST.history(c, phone)}
        rows = ST.threads(c, max(1, min(limit, 500)))
    return {"total": len(rows), "rows": rows}
