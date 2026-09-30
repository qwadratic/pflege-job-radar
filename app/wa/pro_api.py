"""The Pro API (TASK-395/396, Ivan 2026-09-29/30): a read-only, bearer-token-gated surface the
board's Pro frontend proxies server-side (topology B -- WA_API_BASE/WA_API_TOKEN on the board side,
never reaching the browser; docs/wa-dashboard.md is the binding contract for every field name and
shape here), plus the one write path this harness has: Daria (the email-harness digital employee,
TASK-345/396) recording a handoff status per (lead, clinic) after she acts on a consented lead.

Mounted ONLY here (app/wa/asgi.py), under /api/wa/pro/*: nginx exposes just that prefix to the
board, and the existing unauthenticated loopback routes (app/wa/api.py, queue_api.py, ...) are
untouched -- this module adds a surface, it does not replace one.

AUTH. Every route calls ``_authorize`` first. Read routes accept ``Authorization: Bearer
<WA_API_TOKEN>`` or ``<WA_API_WRITE_TOKEN>`` (the write token may also read); the one write route,
POST .../handoffs, accepts only the write token -- a read token can never write. Compared with
``hmac.compare_digest`` (constant-time, same discipline as app/wa/bridge_api.py's own token check).
An unconfigured token env is 503 ``{"detail": "pro api not configured"}`` -- fail CLOSED, never
open, same as every other "secret not set" branch in app/wa/config.py.

THREAD IDENTITY. Every route is keyed on the opaque ``thread_id`` app/wa/store.py mints
(``thread_id_for_phone``/``phone_for_thread_id``), never the raw phone: the raw phone is not
serialized in any response here (tests/test_wa_pro_api.py asserts this directly on the JSON body).
``app/wa/phones.py:phone_masked`` is the one place a phone appears at all, and only its country
code plus last 4 digits.

HANDOFF STATUS (thread-level ``handoff.status`` in a ThreadRow, see app/wa/pro_models.py): it is
this harness's own summary of a lead's per-clinic handoff rows, and it is what "needs a human"
means from here on --

  queued      consented, no handoff row yet                      -- needs a human
  attention   some target clinic's attention is open (see below)  -- needs a human
  signed      no attention open, at least one contract_signed     -- not
  in_progress no attention open, at least one sent_to_clinic/followup_sent, none signed -- not
  closed      every target is declined or closed                  -- not

Per-target "attention" (``HandoffClinicStatus.attention``) is STICKY and derived from the full
``wa_handoff_events`` audit trail, never from the current row alone (Ivan's email-harness amendment,
2026-09-30: a clinic reply can land in the same minute as a scheduled follow-up, so out-of-order
arrival must resolve correctly): open when some event in {clinic_replied, interview_scheduled,
trial_scheduled, offer, halted} has no LATER-ts event in {contract_signed, declined, closed,
sent_to_clinic}. ``followup_sent`` is in neither set -- it never opens attention and never clears
it. docs/whatsapp.md repeats this table for pflege-fe.
"""
import hmac
import json
import pathlib
import re
import socket
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from .. import data as D
from . import api as API   # reuses _is_stuck/_hours_since -- the exact stuck_reply logic GET /wa/threads already has
from . import config as C
from . import phones as P
from . import pro_models as M
from . import queue as Q
from . import store as ST
from . import suppression as SUP
from .luna import reporting as REP
from .luna_brain import requirement_scoreboard

router = APIRouter()

#: Exactly the 7 gate keys the contract names (wa-dashboard.md) -- requirement_scoreboard() also
#: carries an 8th ("documents", the cv_document+qualification_document combo) plus the non-gate
#: hints next_objective/stage/stage_since mixed into the same dict; this list is what selects only
#: the 7 before pro_models.Gates ever sees the rest.
CONTRACT_GATES = ("region", "qualification", "city_or_department", "housing", "cv_document",
                  "qualification_document", "handoff_consent")
TERMINAL_OUTCOMES = ("declined", "already_placed", "not_placeable")
DEFAULT_THREADS_LIMIT = 500   # matches the contract's own example envelope (wa-dashboard.md:55)

#: Metadata-only whitelist for every wa_documents row this API ever serializes (wa-dashboard.md,
#: design decision 5) -- never path/sha256/media_id/original_filename/text/text_key/import_*.
DOCUMENT_FIELDS = ("id", "kind", "document_type", "mime_type", "size_bytes", "received_at", "reuse_state")

#: The closed list POST /api/wa/pro/handoffs accepts (TASK-396 implementation notes) -- anything
#: else is a 400, never silently coerced.
HANDOFF_STATUSES = ("sent_to_clinic", "followup_sent", "clinic_replied", "interview_scheduled",
                    "trial_scheduled", "offer", "contract_signed", "declined", "closed", "halted")
#: Sticky-attention sets (see module docstring). A status in neither (only followup_sent today)
#: is neutral: it neither opens nor clears attention.
ATTENTION_OPENS = {"clinic_replied", "interview_scheduled", "trial_scheduled", "offer", "halted"}
ATTENTION_CLOSES = {"contract_signed", "declined", "closed", "sent_to_clinic"}
_NEG_INF = datetime.min.replace(tzinfo=timezone.utc)

#: Fields Daria's TASK-396 spec asked for that genuinely do not exist in any source this harness or
#: sales_brain holds (the field-map research this task was built from confirmed each one architecturally
#: absent, not merely unsurfaced) -- named here rather than silently missing from GET /api/wa/pro/leads.
BASE_GAPS = (
    "structured per-role CV (roles with from/to dates, employer, ward, tasks) -- app/cv.py only stores "
    "flat category lists (roles/departments/qualifications/cities/...), never a per-role work-history array",
    "current/past employers (hard exclusion) -- no such field exists anywhere in this repo",
    "document-verified flag -- wa_documents has document_type/certificate_level (what kind of document, an "
    "LLM classification) but no authenticity check",
    "consent scope (a named clinic vs. generic) -- anonymous_send_consent is a single boolean on the card, "
    "never qualified by which clinic(s) were named when it was given",
    "per-field provenance (which message said this) for card values other than consent -- the card is a "
    "single JSON blob code overwrites in place turn by turn, with no per-field audit trail",
)

SCHEMA = """
-- Handoff write-back (TASK-396). One current row per (lead_key, target_key); wa_handoff_events is
-- the append-only audit trail everything else is derived from (see module docstring, "attention").
create table if not exists wa_handoffs (
  lead_key text not null,
  target_key text not null,
  thread_id text,
  crm_candidate_id text,
  clinic_id text,
  clinic_name text,
  external_ref text,
  status text not null,
  sender_box text,
  message_id text,
  batch_id text,
  note text,
  ts text not null,
  updated_at text not null,
  primary key (lead_key, target_key)
);
create table if not exists wa_handoff_events (
  id integer primary key,
  lead_key text not null,
  target_key text not null,
  thread_id text,
  crm_candidate_id text,
  clinic_id text,
  clinic_name text,
  external_ref text,
  status text not null,
  prev_status text,
  sender_box text,
  message_id text,
  batch_id text,
  note text,
  ts text not null,
  who text not null,
  recorded_at text not null
);
create index if not exists idx_wa_handoff_events_lead on wa_handoff_events(lead_key, target_key, id);
-- Idempotency (TASK-396): a re-POST of the same (lead_key, target_key, status, message_id) is a
-- no-op. message_id is nullable and a null counts as a value -- ifnull() folds every null to '' so
-- two null-message_id events for the same (lead,target,status) collide too, not just literal ones.
create unique index if not exists idx_wa_handoff_events_dedup
  on wa_handoff_events(lead_key, target_key, status, ifnull(message_id, ''));
"""


def db():
    """One connection carrying every table this module reads or writes: store.py's own (wa_threads,
    wa_messages, wa_thread_ids, ...), queue.py's (wa_queue_candidates, wa_queue_matches -- TASK-326,
    consent already ran this), and this module's own two handoff tables. Every CREATE is idempotent
    (IF NOT EXISTS), so layering them here costs nothing once they exist."""
    c = ST.db()
    c.executescript(Q.SCHEMA)
    c.executescript(SCHEMA)
    return c


# --- auth ------------------------------------------------------------------------------------------

def _bearer(request):
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[len("bearer "):].strip()
    return token or None


def _authorize(request, need_write):
    """Fail-closed token check (Ivan 2026-09-29/30): an unconfigured token env is 503, never treated
    as "open". need_write=True (the one write route) accepts only WA_API_WRITE_TOKEN; every read
    route accepts either token (a read token may never write, the write token may also read)."""
    read_token, write_token = C.pro_api_token(), C.pro_api_write_token()
    if need_write:
        if not write_token:
            raise HTTPException(503, "pro api not configured")
        got = _bearer(request)
        if not got or not hmac.compare_digest(got, write_token):
            raise HTTPException(401, "invalid or missing bearer token")
        return
    if not read_token and not write_token:
        raise HTTPException(503, "pro api not configured")
    got = _bearer(request)
    if got and read_token and hmac.compare_digest(got, read_token):
        return
    if got and write_token and hmac.compare_digest(got, write_token):
        return
    raise HTTPException(401, "invalid or missing bearer token")


def _source():
    return f"harness@{socket.gethostname()}"


# --- shared row/detail builders --------------------------------------------------------------------

def _document_summary(row):
    return {k: row.get(k) for k in DOCUMENT_FIELDS}


def _suppression_summary(record):
    if not record:
        return None
    return {"reason": record["reason"], "lane": record["lane"], "at": record["at"]}


def _pending_inbound_summary(record):
    if not record:
        return None
    return {"count": record["count"], "oldest_recorded_at": record["oldest_recorded_at"],
            "last_error": record["last_error"]}


def _matched_clinics(c, phone):
    """Every clinic this phone's consent matched (wa_queue_matches, TASK-326), deduplicated to one
    row per clinic (a candidate can match several postings of the same clinic; the contract's own
    shape has no posting_id to tell those apart, so the best score wins), name/town joined from the
    live app.data snapshot the way queue.py:build_queue_entry already joins it when it first ranks."""
    rows = c.execute("select clinic_id, score from wa_queue_matches where phone=? order by score desc",
                     (phone,)).fetchall()
    best = {}
    for r in rows:
        cid = r["clinic_id"]
        if cid not in best or r["score"] > best[cid]:
            best[cid] = r["score"]
    out = []
    for cid, score in best.items():
        info = D.clinic(cid) or {}
        out.append({"clinic_id": cid, "clinic_name": info.get("name"), "town": info.get("town"), "score": score})
    out.sort(key=lambda row: -row["score"])
    return out


def _handoff_rows(c, lead_key):
    rows = c.execute("select * from wa_handoffs where lead_key=? order by target_key", (lead_key,)).fetchall()
    return [dict(r) for r in rows]


def _handoff_events(c, lead_key):
    rows = c.execute("select * from wa_handoff_events where lead_key=? order by id", (lead_key,)).fetchall()
    return [dict(r) for r in rows]


def _parse_ts(value):
    """A required, timezone-aware ISO-8601 timestamp -- raises ValueError for anything else (missing,
    unparseable, or naive/no-offset), which the write route turns into a 400."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("ts is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"{value!r} has no timezone offset")
    return dt


def _attention_open(events):
    """See the module docstring's "attention" section. events: this one (lead_key, target_key)'s own
    wa_handoff_events rows, any order -- computed from the full trail, never the current row alone,
    so an out-of-order arrival still resolves correctly."""
    opens_ts = [_parse_ts(e["ts"]) for e in events if e["status"] in ATTENTION_OPENS]
    if not opens_ts:
        return False
    closes_ts = [_parse_ts(e["ts"]) for e in events if e["status"] in ATTENTION_CLOSES]
    return max(closes_ts, default=_NEG_INF) <= max(opens_ts)


def _clinic_statuses(rows, events):
    """[{clinic_id, clinic_name, external_ref, status, ts, attention}] for a lead's current handoff
    rows -- the shape both ThreadHandoff.clinics and LeadRow.handoffs share."""
    out = []
    for row in rows:
        target_events = [e for e in events if e["target_key"] == row["target_key"]]
        out.append({"clinic_id": row["clinic_id"], "clinic_name": row["clinic_name"],
                    "external_ref": row["external_ref"], "status": row["status"], "ts": row["ts"],
                    "attention": _attention_open(target_events)})
    return out


def _thread_handoff(c, phone, thread_id):
    """ThreadRow.handoff -- None while the candidate has not consented at all (wa_queue_candidates
    has no row); the status-mapping table is in this module's own docstring."""
    cand = c.execute("select consented_at from wa_queue_candidates where phone=?", (phone,)).fetchone()
    if cand is None:
        return None
    lead_key = f"thread:{thread_id}"
    rows = _handoff_rows(c, lead_key)
    events = _handoff_events(c, lead_key)
    clinics = _clinic_statuses(rows, events)
    if not rows:
        status = "queued"
    elif any(clinic["attention"] for clinic in clinics):
        status = "attention"
    elif any(r["status"] == "contract_signed" for r in rows):
        status = "signed"
    elif any(r["status"] in ("sent_to_clinic", "followup_sent") for r in rows):
        status = "in_progress"
    elif all(r["status"] in ("declined", "closed") for r in rows):
        status = "closed"
    else:
        # Exhaustive per this module's docstring: every one of the 10 closed-list statuses is
        # either in ATTENTION_OPENS (caught by the "attention" branch above, since a target whose
        # CURRENT status is an opens-status has, by definition, no later event at all) or in
        # ATTENTION_CLOSES/followup_sent (the remaining 4 branches). Reaching here means a status
        # outside that table slipped through validation -- fail loudly (CLAUDE.md: no silent
        # fallbacks) rather than mislabel the thread.
        raise RuntimeError(f"thread handoff status mapping has no branch for "
                           f"{sorted({r['status'] for r in rows})!r}")
    return {"status": status, "consented_at": cand["consented_at"], "clinics": clinics}


def _thread_row_dict(c, t):
    phone = t["phone"]
    thread_id = ST.thread_id_for_phone(c, phone)
    card = t["slots"]
    board = requirement_scoreboard(card)
    outcome_raw = REP.stage_for(card)
    pending = ST.pending_inbound_summary(c, phone)
    stuck = API._is_stuck(c, phone, t.get("last_inbound_at"), t.get("stopped"))
    oldest = pending["oldest_recorded_at"] if pending else None
    stuck_reply = stuck or bool(oldest and API._hours_since(oldest) > C.STUCK_REPLY_HOURS)
    return {
        "thread_id": thread_id,
        "phone_masked": P.phone_masked(phone),
        "is_test": bool(t["is_test"]),
        "rail": t.get("rail"),
        "opened_at": t.get("opened_at"),
        "last_inbound_at": t.get("last_inbound_at"),
        "last_outbound_at": t.get("last_outbound_at"),
        "turns": t.get("turns") or 0,
        "ball": REP.ball_for(c, phone),
        "stage": board["stage"],
        "stage_since": board["stage_since"],
        "outcome": outcome_raw if outcome_raw in TERMINAL_OUTCOMES else None,
        "gates": {g: board[g] for g in CONTRACT_GATES},
        "card": {
            "region": card.get("region"), "city": card.get("city"), "department": card.get("department_pref"),
            "qualification_path": card.get("qualification_path"), "housing_needed": card.get("housing_needed"),
            "people_count": card.get("people_count"), "campaign": card.get("campaign"),
            "match_branch": card.get("match_branch"),
        },
        "stopped": bool(t.get("stopped")),
        "stopped_reason": t.get("stopped_reason"),
        "suppression": _suppression_summary(SUP.suppression(c, phone)),
        "escalation_codes": card.get("_escalation_codes") or [],
        "flag_codes": card.get("_flag_codes") or [],
        "escalated_at": card.get("_escalated_at"),
        "stuck_reply": stuck_reply,
        "pending_inbound": _pending_inbound_summary(pending),
        "last_send_error": ST.recent_send_failure(c, phone),
        "handoff": _thread_handoff(c, phone, thread_id),
        "last_message": ST.last_message(c, phone),
        "lead_status": None,   # TASK-316 (P4), not built.
    }


# --- GET /api/wa/pro/threads ------------------------------------------------------------------------

@router.get("/wa/pro/threads", response_model=M.ThreadsEnvelope)
def pro_threads(request: Request):
    _authorize(request, need_write=False)
    include_test = request.query_params.get("include_test") == "1"
    with ST._lock, db() as c:
        raw = ST.all_threads(c)
        if not include_test:
            raw = [t for t in raw if not t["is_test"]]
        rows = [_thread_row_dict(c, t) for t in raw]
    envelope = D.page(rows, request.query_params, DEFAULT_THREADS_LIMIT)
    envelope["test_threads"] = sum(1 for r in envelope["rows"] if r["is_test"])
    envelope["generated_at"] = ST.now_iso()
    envelope["source"] = _source()
    return envelope


@router.get("/wa/pro/threads/{thread_id}", response_model=M.ThreadDetailResponse)
def pro_thread_detail(thread_id: str, request: Request):
    _authorize(request, need_write=False)
    with ST._lock, db() as c:
        phone = ST.phone_for_thread_id(c, thread_id)
        if phone is None:
            raise HTTPException(404, f"unknown thread_id {thread_id!r}")
        t = ST.thread(c, phone)
        row = _thread_row_dict(c, t)
        card = t["slots"]
        documents = [_document_summary(d) for d in ST.documents_for(c, phone)]
        send_failures = ST.send_failures_for(c, phone)
        handoff_matches = _matched_clinics(c, phone)
    return {"thread": row, "escalation_notes": card.get("_escalate_reason_notes") or [],
            "flag_notes": card.get("_flags_notes") or [],
            "next_objective": requirement_scoreboard(card).get("next_objective"),
            "documents": documents, "send_failures": send_failures, "handoff_matches": handoff_matches}


# --- GET /api/wa/pro/threads/{id}/messages -----------------------------------------------------------

def _messages_page(c, phone, limit, before_id, after_id):
    """-> (rows as sqlite3.Row, next_before_id). See the module for the three modes (no cursor =
    newest ``limit``, before_id = older page, after_id = everything newer / the 5s poll) -- the
    contract's own wording in docs/wa-dashboard.md."""
    if after_id is not None:
        rows = c.execute("select * from wa_messages where phone=? and id>? order by id asc",
                         (phone, after_id)).fetchall()
        return rows, None
    sql, args = "select * from wa_messages where phone=?", [phone]
    if before_id is not None:
        sql += " and id<?"
        args.append(before_id)
    sql += " order by id desc limit ?"
    args.append(limit + 1)
    desc_rows = c.execute(sql, args).fetchall()
    has_more = len(desc_rows) > limit
    page = list(desc_rows[:limit])
    page.reverse()
    next_before_id = page[0]["id"] if (page and has_more) else None
    return page, next_before_id


@router.get("/wa/pro/threads/{thread_id}/messages", response_model=M.MessagesEnvelope)
def pro_thread_messages(thread_id: str, request: Request, limit: int = 50,
                        before_id: int | None = None, after_id: int | None = None):
    _authorize(request, need_write=False)
    if limit <= 0:
        raise HTTPException(400, f"limit must be a positive integer, got {limit}")
    if before_id is not None and after_id is not None:
        raise HTTPException(400, "before_id and after_id may not both be given")
    with ST._lock, db() as c:
        phone = ST.phone_for_thread_id(c, thread_id)
        if phone is None:
            raise HTTPException(404, f"unknown thread_id {thread_id!r}")
        rows, next_before_id = _messages_page(c, phone, limit, before_id, after_id)
        # Batched, once per request -- not once per message (wa-dashboard.md's own instruction).
        status_by_wamid = {s["wamid"]: s["status"] for s in ST.latest_message_statuses_for(c, phone)}
    out_rows = []
    for r in rows:
        deleted = bool(r["deleted_at"])
        out_rows.append({
            "id": r["id"], "direction": r["direction"], "kind": "deleted" if deleted else r["kind"],
            "body": None if deleted else r["body"], "at": r["at"], "deleted": deleted,
            "status": (status_by_wamid.get(r["wamid"]) if (r["direction"] == "out" and not deleted) else None),
            "meta": {} if deleted else json.loads(r["meta"] or "{}"),
        })
    return {"rows": out_rows, "next_before_id": next_before_id}


# --- GET /api/wa/pro/health -------------------------------------------------------------------------

@router.get("/wa/pro/health", response_model=M.HealthResponse)
def pro_health(request: Request):
    _authorize(request, need_write=False)
    with ST.db() as c:
        rails = ST.rail_counts(c)
    return {**C.readiness(), "rails": rails}


# --- handoff write-back (TASK-396) ------------------------------------------------------------------

def _norm_target_name(name):
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


@router.post("/wa/pro/handoffs", response_model=M.HandoffWriteResponse)
async def pro_handoffs_write(request: Request):
    _authorize(request, need_write=True)
    body = await D.json_body(request)

    status = body.get("status")
    if status not in HANDOFF_STATUSES:
        raise HTTPException(400, f"status must be one of {HANDOFF_STATUSES}, got {status!r}")

    try:
        ts_dt = _parse_ts(body.get("ts"))
    except ValueError as exc:
        raise HTTPException(400, f"ts: {exc}")
    ts_raw = str(body.get("ts")).strip()

    thread_id = body.get("thread_id")
    crm_candidate_id = body.get("crm_candidate_id")
    if not thread_id and not crm_candidate_id:
        raise HTTPException(400, "thread_id or crm_candidate_id is required")

    clinic_id, clinic_name, external_ref = body.get("clinic_id"), body.get("clinic_name"), body.get("external_ref")
    if not clinic_id and not clinic_name:
        raise HTTPException(400, "clinic_name is required when clinic_id is not given")
    if clinic_id:
        target_key = f"clinic:{clinic_id}"
    elif external_ref:
        target_key = f"ext:{external_ref}"
    else:
        target_key = f"name:{_norm_target_name(clinic_name)}"

    message_id, batch_id, note = body.get("message_id"), body.get("batch_id"), body.get("note")
    sender_box, who = body.get("sender_box"), body.get("who") or "daria"

    with ST._lock, db() as c:
        if thread_id:
            phone = ST.phone_for_thread_id(c, thread_id)
            if phone is None:
                raise HTTPException(404, f"unknown thread_id {thread_id!r}")
            lead_key = f"thread:{thread_id}"
        else:
            lead_key = f"crm:{crm_candidate_id}"

        dup = c.execute(
            "select 1 from wa_handoff_events where lead_key=? and target_key=? and status=? "
            "and ifnull(message_id,'')=ifnull(?,'')", (lead_key, target_key, status, message_id)).fetchone()
        existing = c.execute("select * from wa_handoffs where lead_key=? and target_key=?",
                             (lead_key, target_key)).fetchone()
        if dup:
            # Reruns happen (TASK-396): a no-op, no second event, current status unchanged.
            return {"applied": False, "duplicate": True,
                    "current": bool(existing) and existing["status"] == status,
                    "lead_key": lead_key, "target_key": target_key,
                    "status": existing["status"] if existing else status}

        prev_status = existing["status"] if existing else None
        now = ST.now_iso()
        c.execute(
            """insert into wa_handoff_events (lead_key, target_key, thread_id, crm_candidate_id, clinic_id,
               clinic_name, external_ref, status, prev_status, sender_box, message_id, batch_id, note, ts,
               who, recorded_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lead_key, target_key, thread_id, crm_candidate_id, clinic_id, clinic_name, external_ref,
             status, prev_status, sender_box, message_id, batch_id, note, ts_raw, who, now))

        # Tie -> later arrival wins (TASK-396): ">=", not ">".
        becomes_current = existing is None or ts_dt >= _parse_ts(existing["ts"])
        if becomes_current:
            c.execute(
                """insert into wa_handoffs (lead_key, target_key, thread_id, crm_candidate_id, clinic_id,
                   clinic_name, external_ref, status, sender_box, message_id, batch_id, note, ts, updated_at)
                   values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   on conflict(lead_key, target_key) do update set
                     thread_id=excluded.thread_id, crm_candidate_id=excluded.crm_candidate_id,
                     clinic_id=excluded.clinic_id, clinic_name=excluded.clinic_name,
                     external_ref=excluded.external_ref, status=excluded.status,
                     sender_box=excluded.sender_box, message_id=excluded.message_id,
                     batch_id=excluded.batch_id, note=excluded.note, ts=excluded.ts,
                     updated_at=excluded.updated_at""",
                (lead_key, target_key, thread_id, crm_candidate_id, clinic_id, clinic_name, external_ref,
                 status, sender_box, message_id, batch_id, note, ts_raw, now))
        c.commit()
        current_status = status if becomes_current else existing["status"]

    return {"applied": True, "duplicate": False, "current": becomes_current,
            "lead_key": lead_key, "target_key": target_key, "status": current_status}


@router.get("/wa/pro/handoffs", response_model=M.HandoffGetResponse)
def pro_handoffs_read(request: Request):
    _authorize(request, need_write=False)
    thread_id = request.query_params.get("thread_id")
    crm_candidate_id = request.query_params.get("crm_candidate_id")
    if not thread_id and not crm_candidate_id:
        raise HTTPException(400, "thread_id or crm_candidate_id query parameter is required")
    with ST._lock, db() as c:
        if thread_id:
            phone = ST.phone_for_thread_id(c, thread_id)
            if phone is None:
                raise HTTPException(404, f"unknown thread_id {thread_id!r}")
            lead_key = f"thread:{thread_id}"
        else:
            lead_key = f"crm:{crm_candidate_id}"
        rows = _handoff_rows(c, lead_key)
        events = _handoff_events(c, lead_key)
    for row in rows:
        row["attention"] = _attention_open([e for e in events if e["target_key"] == row["target_key"]])
    return {"lead_key": lead_key, "rows": rows, "events": events}


# --- GET /api/wa/pro/leads (Daria, TASK-396) ---------------------------------------------------------

def _open_sales_brain():
    """-> (read-only connection or None, freshness ISO string or None). Never opens for write; a
    missing file is "unavailable", never an exception (TASK-396: "loud in the response, not an
    exception")."""
    path = pathlib.Path(C.sales_brain_path())
    if not path.exists():
        return None, None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    freshness = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat()
    return conn, freshness


def _crm_match(sb_conn, phone):
    """-> (candidate_id or None, crm_match). Only an UNAMBIGUOUS resolved match gets a candidate_id
    (TASK-396); zero or more-than-one distinct candidate_id for this phone is 'none'/'ambiguous'."""
    rows = sb_conn.execute(
        "select distinct candidate_id from candidate_whatsapp_messages where phone_e164=? "
        "and candidate_id is not null", (phone,)).fetchall()
    ids = {r["candidate_id"] for r in rows}
    if len(ids) == 1:
        return next(iter(ids)), None
    return None, ("ambiguous" if len(ids) > 1 else "none")


def _crm_cases(sb_conn, candidate_id):
    """candidate_clinic_cases x placement_case_state for one candidate, read-only, sales_brain's own
    column names (TASK-396: "columns as they are") -- aliased only where both tables would otherwise
    collide on the same name (id/status)."""
    rows = sb_conn.execute(
        """select cc.id as case_id, cc.workspace_id, cc.candidate_id, cc.company_id, cc.clinic_key,
                  cc.status as case_status, cc.register_signed, cc.contract_start_date,
                  cc.primary_contact_email, cc.metadata_json, cc.created_at, cc.updated_at,
                  ps.placement_stage, ps.waiting_for, ps.next_action, ps.due_at, ps.scheduled_event_at
           from candidate_clinic_cases cc left join placement_case_state ps on ps.case_id = cc.id
           where cc.candidate_id = ?""", (candidate_id,)).fetchall()
    return [dict(r) for r in rows]


def _consent_info(c, phone, consented_at):
    """Recovers the consent record from wa_messages (TASK-396 field map): the inbound button tap
    nearest at-or-before consented_at, and the outbound buttons bubble nearest before that tap --
    a heuristic (no direct link is stored, see the map's own gap note), not a guarantee."""
    tap = c.execute(
        "select wamid, at from wa_messages where phone=? and direction='in' and kind='buttons' and at<=? "
        "order by at desc limit 1", (phone, consented_at)).fetchone()
    offer = None
    if tap:
        offer = c.execute(
            "select wamid, body from wa_messages where phone=? and direction='out' and kind='buttons' "
            "and at<=? order by at desc limit 1", (phone, tap["at"])).fetchone()
    return {
        "offer_text": offer["body"] if offer else None,
        "offer_message_id": offer["wamid"] if offer else None,
        "answer_message_id": tap["wamid"] if tap else None,
        "answered_at": tap["at"] if tap else None,
        "scope": None,
        "scope_note": "consent scope is not recorded; read offer_text",
    }


def _max_iso(a, b):
    """Both operands are store.now_iso() output (consistent +00:00 offset, zero-padded), so a plain
    string max sorts correctly without parsing either one."""
    vals = [v for v in (a, b) if v]
    return max(vals) if vals else None


def _lead_row(c, phone, cand, sb_conn, sb_freshness):
    thread_id = ST.thread_id_for_phone(c, phone)
    profile = json.loads(cand["profile_json"])
    t = ST.thread(c, phone)
    live_card = t["slots"]
    if sb_conn is None:
        crm_candidate_id, crm_match, crm_cases = None, "unavailable", []
    else:
        crm_candidate_id, crm_match = _crm_match(sb_conn, phone)
        crm_cases = _crm_cases(sb_conn, crm_candidate_id) if crm_candidate_id else []
    lead_key = f"thread:{thread_id}"
    h_rows, h_events = _handoff_rows(c, lead_key), _handoff_events(c, lead_key)
    return {
        "thread_id": thread_id, "crm_candidate_id": crm_candidate_id, "crm_match": crm_match,
        "crm_cases": crm_cases, "crm_freshness": sb_freshness,
        "consent": _consent_info(c, phone, cand["consented_at"]),
        "card": {
            "region": profile.get("region"), "city": profile.get("city"),
            "department": (profile.get("departments") or [None])[0],
            "qualification_path": live_card.get("qualification_path"),
            "housing_needed": profile.get("needs_housing"), "people_count": profile.get("people_count"),
            "german_level": profile.get("german_level"), "provenance": None,
        },
        "cv_profile": profile,
        "documents": [_document_summary(d) for d in ST.documents_for(c, phone)],
        "matched_clinics": _matched_clinics(c, phone),
        "handoffs": _clinic_statuses(h_rows, h_events),
        "updated_at": _max_iso(t.get("last_inbound_at"), t.get("last_outbound_at")),
    }


@router.get("/wa/pro/leads", response_model=M.LeadsEnvelope)
def pro_leads(request: Request):
    _authorize(request, need_write=False)
    sb_conn, sb_freshness = _open_sales_brain()
    gaps = list(BASE_GAPS)
    if sb_conn is None:
        gaps.append(f"crm data (sales_brain.sqlite not found at {C.sales_brain_path()})")
    try:
        with ST._lock, db() as c:
            cands = c.execute(
                "select q.phone, q.consented_at, q.profile_json from wa_queue_candidates q "
                "left join wa_threads t on t.phone=q.phone where coalesce(t.is_test,0)=0 "
                "order by q.consented_at desc").fetchall()
            rows = [_lead_row(c, cand["phone"], cand, sb_conn, sb_freshness) for cand in cands]
    finally:
        if sb_conn is not None:
            sb_conn.close()
    return {"generated_at": ST.now_iso(), "source": _source(), "rows": rows, "gaps": gaps}
