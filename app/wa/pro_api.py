"""The Pro API (TASK-395/396, Ivan 2026-09-29/30): a read-only, bearer-token-gated surface the
board's Pro frontend proxies server-side (topology B -- WA_API_BASE/WA_API_TOKEN on the board side,
never reaching the browser; docs/wa-dashboard.md is the binding contract for every field name and
shape here), plus the one write path this harness has: Daria (the email-harness digital employee,
TASK-345/396) recording a handoff status per (lead, clinic) after she acts on a consented lead.

Mounted ONLY here (app/wa/asgi.py), under /api/wa/pro/*: nginx exposes just that prefix to the
board, and the existing unauthenticated loopback routes (app/wa/api.py, queue_api.py, ...) are
untouched -- this module adds a surface, it does not replace one.

AUTH. Every route calls ``_authorize`` first, with one of two scopes (review item 12, Ivan
2026-09-30): SCOPE_BOARD (threads/detail/messages/health) accepts ``WA_API_TOKEN`` (the board's) or
``WA_API_WRITE_TOKEN`` (Daria's -- the write token may also read everything); SCOPE_DARIA (leads,
both handoff routes) accepts ONLY the write token -- a valid board token there is 403 (recognised,
wrong scope), not 401. A compromised board VM can read thread/message state but never Daria's CRM
matches, consent text or handoff writes. Compared with ``hmac.compare_digest`` on the UTF-8 bytes of
both operands, never the ``str`` values directly (review item 12's non-ASCII-token note: comparing
``str`` raises ``TypeError`` on a non-ASCII token, which -- uncaught -- would be a 500 instead of a
401; encoding first makes it simply never equal). An unconfigured token env is 503
``{"detail": "pro api not configured"}`` -- fail CLOSED, never open, same as every other "secret not
set" branch in app/wa/config.py.

READS NEVER LOCK, NEVER WRITE (review item 7). Every GET route opens its own read-only connection
(``db_ro``, SQLite ``mode=ro``) and never takes ``store.ST._lock`` -- WAL mode gives a consistent
snapshot without either, so a read here never waits behind ``api.process_phones``'s model call. The
one write route, POST .../handoffs, still takes a normal read-write connection (``db``) but not the
lock either: a short transaction of its own, protected by SQLite's own busy_timeout. Both this
module's own schema (wa_handoffs/wa_handoff_events) and store.py's are created exactly once, eagerly,
by app/wa/asgi.py's startup hook -- a ``mode=ro`` connection cannot run CREATE TABLE, so nothing here
may be the first connection this process ever opens.

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
from pydantic import ValidationError as PydanticValidationError

from .. import data as D
from . import api as API   # reuses _is_stuck/_hours_since -- the exact stuck_reply logic GET /wa/threads already has
from . import config as C
from . import phones as P
from . import pro_models as M
from . import queue as Q
from . import store as ST
from . import suppression as SUP
from .luna import reporting as REP
from .luna_brain import CONSENT_YES_ID, requirement_scoreboard

router = APIRouter()

#: Token scopes (review item 12, 2026-09-30) -- see the module docstring's AUTH section.
SCOPE_BOARD = "board"
SCOPE_DARIA = "daria"

#: A Meta message id base64-encodes the recipient's raw phone number (review items 1/2/6, 2026-09-30
#: -- every wamid in a live copy of data/wa.sqlite decoded to its thread's real phone). This pattern
#: must never survive into any Pro/Daria response, in any field -- see _scrub_wamids below.
_WAMID_RE = re.compile(r"wamid\.[A-Za-z0-9+/=_-]+")

#: Messages endpoint meta whitelist (review item 2, 2026-09-30). Everything else a stored wa_messages
#: row's meta can carry -- reply_to_wamid, context (can nest a wamid/phone in context.from),
#: button_recovery (offer_wamid), media_id/media_filename (the documents rule forbids these), raw
#: contacts/location objects, gallery/document action metadata from app/wa/luna/tools_server.py, ... --
#: is dropped, not merely scrubbed.
ALLOWED_META_KEYS = ("buttons", "scope_refusal", "action", "template", "transcript")

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
    # Review item 10 (2026-09-30): both of these were served from the consent-time matching snapshot
    # (queue.card_to_candidate) under misleading names -- cv_profile was that snapshot's dict, not
    # app/cv.py's actual profile (stored nowhere this harness can read live), and german_level was
    # derived (app/wa/queue.py:_german_level) from the same vanished CV-analysis step. Dropped from
    # LeadRow/LeadCardValues rather than served stale.
    "cv profile (app/cv.py's structured analysis) -- not stored anywhere this harness can read live",
    "german level -- derived only at consent time from the same CV analysis that is not stored live",
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
    """One read-WRITE connection carrying every table this module writes: store.py's own, queue.py's
    (TASK-326, consent already ran this), and this module's own two handoff tables. Every CREATE is
    idempotent (IF NOT EXISTS). Used by the one write route (POST .../handoffs) and, once, by app/wa/
    asgi.py's startup hook to create the schema eagerly -- NOT by any GET route (review item 7): see
    db_ro below."""
    c = ST.db()
    c.executescript(Q.SCHEMA)
    c.executescript(SCHEMA)
    return c


def db_ro():
    """Read-only connection for every GET route in this module (review item 7, 2026-09-30): never
    ST._lock, never capable of writing -- WAL mode already gives a consistent snapshot without
    either. Assumes db() has already created the schema at least once (app/wa/asgi.py's startup
    hook): a mode=ro connection cannot CREATE TABLE."""
    return ST.db_ro()


# --- wamid / meta scrubbing (review items 1/2/6, 2026-09-30) ---------------------------------------
# A wamid base64-encodes the recipient's raw phone number (module docstring, review finding behind
# items 1-3/6) -- so no wamid may ever reach a Pro/Daria response, in ANY field, not only the ones a
# reviewer happened to check. _scrub_wamids is the last-step safety net applied to every route's
# response dict, on top of (not instead of) building each field from a wamid-free source in the
# first place (ids are wa_messages.id; campaign is a string; meta is whitelisted).

def _scrub_wamids(value):
    """Recursively redact ``wamid.<...>`` out of every string this touches -- dict values and list
    items included; dict/list keys are left alone (nothing in this codebase ever puts a wamid in a
    key)."""
    if isinstance(value, str):
        return _WAMID_RE.sub("wamid.…", value)
    if isinstance(value, dict):
        return {k: _scrub_wamids(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_wamids(v) for v in value]
    return value


def _filter_meta(meta):
    """The messages endpoint's meta whitelist (review item 2) -- see ALLOWED_META_KEYS above."""
    return {k: v for k, v in (meta or {}).items() if k in ALLOWED_META_KEYS}


# --- auth (review item 12, 2026-09-30 -- see the module docstring's AUTH section) ------------------

def _bearer(request):
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[len("bearer "):].strip()
    return token or None


def _compare(a, b):
    """Constant-time compare on UTF-8 bytes, never on the ``str`` values directly: hmac.compare_digest
    raises TypeError on a non-ASCII str, which would otherwise surface as an uncaught 500 instead of
    the fail-closed 401 every other bad-token path returns (review item 12's LOW note)."""
    return hmac.compare_digest(a.encode("utf-8", "surrogateescape"), b.encode("utf-8", "surrogateescape"))


def _authorize(request, scope):
    """Fail-closed token check. An unconfigured token env is 503, never treated as "open".

    SCOPE_BOARD (threads/detail/messages/health): either token opens it (the write token may also
    read). SCOPE_DARIA (leads, both handoff routes): only WA_API_WRITE_TOKEN opens it -- a valid
    WA_API_TOKEN there is 403 (recognised, wrong scope), never 401, and is checked only once the
    write token itself has failed to match, so a request carrying a copy of the write token can never
    be misdiagnosed as "board-scoped" by mistake."""
    read_token, write_token = C.pro_api_token(), C.pro_api_write_token()
    if scope == SCOPE_DARIA:
        if not write_token:
            raise HTTPException(503, "pro api not configured")
        got = _bearer(request)
        if got and _compare(got, write_token):
            return
        if got and read_token and _compare(got, read_token):
            raise HTTPException(403, "this route requires the write token")
        raise HTTPException(401, "invalid or missing bearer token")
    if not read_token and not write_token:
        raise HTTPException(503, "pro api not configured")
    got = _bearer(request)
    if got and write_token and _compare(got, write_token):
        return
    if got and read_token and _compare(got, read_token):
        return
    raise HTTPException(401, "invalid or missing bearer token")


def _source():
    return f"harness@{socket.gethostname()}"


def _rail_sync_summary(c):
    """{"synced_at", "synced_source"} (review item 13, 2026-09-30) -- both null without a
    wa_rail_sync row (a fresh database, or bridge/relay_pull.py has never run yet); never invented."""
    row = ST.rail_sync(c)
    return {"synced_at": row["synced_at"] if row else None, "synced_source": row["source"] if row else None}


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
    has no row); the status-mapping table is in this module's own docstring.

    ``clinics`` is the INTEGER count of matched clinics (wa_queue_candidates + wa_queue_matches),
    per the binding contract and the already-built frontend (review item 5, 2026-09-30) -- never the
    per-target list, which is now its own key, ``targets``."""
    cand = c.execute("select consented_at from wa_queue_candidates where phone=?", (phone,)).fetchone()
    if cand is None:
        return None
    lead_key = f"thread:{thread_id}"
    rows = _handoff_rows(c, lead_key)
    events = _handoff_events(c, lead_key)
    targets = _clinic_statuses(rows, events)
    if not rows:
        status = "queued"
    elif any(target["attention"] for target in targets):
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
    return {"status": status, "consented_at": cand["consented_at"], "clinics": len(_matched_clinics(c, phone)),
            "targets": targets}


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
            "people_count": card.get("people_count"),
            # The campaign_id string, not the whole stored dict (review item 3): that dict carries a
            # wamid (store.record_campaign_send's own card.campaign shape).
            "campaign": (card.get("campaign") or {}).get("campaign_id"),
            "match_branch": card.get("match_branch"),
            # docs/wa-dashboard.md's own board-scope contract (Ivan, 2026-10-01): these two belong on
            # every board card, not only on the Daria-scope /leads one (_lead_row below reads the same
            # two live-card keys). Never cv_text or any document text, per that same contract line.
            "housing_flexible": card.get("housing_flexible"),
            "anonymous_send_offered": card.get("anonymous_send_offered"),
        },
        "stopped": bool(t.get("stopped")),
        "stopped_reason": t.get("stopped_reason"),
        "suppression": _suppression_summary(SUP.suppression(c, phone)),
        "escalation_codes": card.get("_escalation_codes") or [],
        "flag_codes": card.get("_flag_codes") or [],
        "escalated_at": card.get("_escalated_at"),
        "stuck_reply": stuck_reply,
        "pending_inbound": _pending_inbound_summary(pending),
        # Review item 1: delivery_failure_text embeds the failed message's own wamid -- _scrub_wamids
        # (applied to the whole response by every route below) is the net under this already-narrow
        # field, not a substitute for it.
        "last_send_error": ST.recent_send_failure(c, phone),
        "handoff": _thread_handoff(c, phone, thread_id),
        "last_message": ST.last_message(c, phone),
        "lead_status": None,   # TASK-316 (P4), not built.
    }


# --- GET /api/wa/pro/threads ------------------------------------------------------------------------

@router.get("/wa/pro/threads", response_model=M.ThreadsEnvelope)
def pro_threads(request: Request):
    _authorize(request, SCOPE_BOARD)
    include_test = request.query_params.get("include_test") == "1"
    with db_ro() as c:
        raw = ST.all_threads(c)
        if not include_test:
            raw = [t for t in raw if not t["is_test"]]
        rows = [_thread_row_dict(c, t) for t in raw]
        synced = _rail_sync_summary(c)
    envelope = D.page(rows, request.query_params, DEFAULT_THREADS_LIMIT)
    envelope["test_threads"] = sum(1 for r in envelope["rows"] if r["is_test"])
    envelope["generated_at"] = ST.now_iso()
    envelope["source"] = _source()
    envelope.update(synced)
    return _scrub_wamids(envelope)


@router.get("/wa/pro/threads/{thread_id}", response_model=M.ThreadDetailResponse)
def pro_thread_detail(thread_id: str, request: Request):
    _authorize(request, SCOPE_BOARD)
    with db_ro() as c:
        phone = ST.phone_for_thread_id(c, thread_id)
        if phone is None:
            raise HTTPException(404, f"unknown thread_id {thread_id!r}")
        t = ST.existing_thread(c, phone)
        row = _thread_row_dict(c, t)
        card = t["slots"]
        documents = [_document_summary(d) for d in ST.documents_for(c, phone)]
        send_failures = ST.send_failures_for(c, phone)
        handoff_matches = _matched_clinics(c, phone)
        synced = _rail_sync_summary(c)
    out = {"thread": row, "escalation_notes": card.get("_escalate_reason_notes") or [],
           "flag_notes": card.get("_flags_notes") or [],
           "next_objective": requirement_scoreboard(card).get("next_objective"),
           "documents": documents, "send_failures": send_failures, "handoff_matches": handoff_matches,
           **synced}
    return _scrub_wamids(out)


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
    _authorize(request, SCOPE_BOARD)
    if limit <= 0:
        raise HTTPException(400, f"limit must be a positive integer, got {limit}")
    if before_id is not None and after_id is not None:
        raise HTTPException(400, "before_id and after_id may not both be given")
    with db_ro() as c:
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
            # Review item 2: only buttons/scope_refusal/action/template/transcript ever reach Pro --
            # everything else (reply_to_wamid, context, button_recovery, media_id/filename,
            # contacts/location, ...) can carry a wamid or a raw phone and is dropped outright.
            "meta": {} if deleted else _filter_meta(json.loads(r["meta"] or "{}")),
        })
    return _scrub_wamids({"rows": out_rows, "next_before_id": next_before_id})


# --- GET /api/wa/pro/health -------------------------------------------------------------------------

@router.get("/wa/pro/health", response_model=M.HealthResponse)
def pro_health(request: Request):
    _authorize(request, SCOPE_BOARD)
    with db_ro() as c:
        rails = ST.rail_counts(c)
        synced = _rail_sync_summary(c)
    return _scrub_wamids({**C.readiness(), "rails": rails, **synced})


# --- handoff write-back (TASK-396) ------------------------------------------------------------------

def _norm_target_name(name):
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


@router.post("/wa/pro/handoffs", response_model=M.HandoffWriteResponse)
async def pro_handoffs_write(request: Request):
    _authorize(request, SCOPE_DARIA)
    raw = await D.json_body(request)
    # Parsed and validated BY HAND (review item 8, 2026-09-30), never as a FastAPI request-body
    # parameter: that would make a bad body FastAPI's own default 422, which must never happen here
    # -- this route's 400 is the only error shape a caller ever sees for bad input.
    try:
        req = M.HandoffWriteRequest.model_validate(raw)
    except PydanticValidationError as exc:
        raise HTTPException(400, f"invalid handoff body: {exc}")

    status = req.status
    if status not in HANDOFF_STATUSES:
        raise HTTPException(400, f"status must be one of {HANDOFF_STATUSES}, got {status!r}")

    try:
        ts_dt = _parse_ts(req.ts)
    except ValueError as exc:
        raise HTTPException(400, f"ts: {exc}")
    ts_raw = req.ts.strip()

    thread_id, crm_candidate_id = req.thread_id, req.crm_candidate_id
    if not thread_id and not crm_candidate_id:
        raise HTTPException(400, "thread_id or crm_candidate_id is required")

    clinic_id, clinic_name, external_ref = req.clinic_id, req.clinic_name, req.external_ref
    if not clinic_id and not clinic_name:
        raise HTTPException(400, "clinic_name is required when clinic_id is not given")
    if clinic_id:
        target_key = f"clinic:{clinic_id}"
    elif external_ref:
        target_key = f"ext:{external_ref}"
    else:
        target_key = f"name:{_norm_target_name(clinic_name)}"

    message_id, batch_id, note = req.message_id, req.batch_id, req.note
    sender_box, who = req.sender_box, req.who or "daria"

    # Its own short read-write transaction, not ST._lock (review item 7): SQLite's own busy_timeout
    # (db()'s connect(..., timeout=30), inherited via ST.db()) is what absorbs a collision with
    # api.process_phones's writes -- a handful of statements, committed immediately below, never
    # held open across a model call the way the old lock-holding read routes were.
    with db() as c:
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
            return _scrub_wamids({"applied": False, "duplicate": True,
                                  "current": bool(existing) and existing["status"] == status,
                                  "lead_key": lead_key, "target_key": target_key,
                                  "status": existing["status"] if existing else status})

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

    return _scrub_wamids({"applied": True, "duplicate": False, "current": becomes_current,
                          "lead_key": lead_key, "target_key": target_key, "status": current_status})


@router.get("/wa/pro/handoffs", response_model=M.HandoffGetResponse)
def pro_handoffs_read(request: Request):
    _authorize(request, SCOPE_DARIA)
    thread_id = request.query_params.get("thread_id")
    crm_candidate_id = request.query_params.get("crm_candidate_id")
    if not thread_id and not crm_candidate_id:
        raise HTTPException(400, "thread_id or crm_candidate_id query parameter is required")
    with db_ro() as c:
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
    return _scrub_wamids({"lead_key": lead_key, "rows": rows, "events": events})


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
    (TASK-396); zero or more-than-one distinct candidate_id for this phone is 'none'/'ambiguous'.
    Cast to str (review item 4, 2026-09-30): sales_brain's candidate_id is INTEGER, and
    LeadRow.crm_candidate_id is str -- returning the bare int made FastAPI's own response validation
    500 on every lead with a clear CRM match."""
    rows = sb_conn.execute(
        "select distinct candidate_id from candidate_whatsapp_messages where phone_e164=? "
        "and candidate_id is not null", (phone,)).fetchall()
    ids = {r["candidate_id"] for r in rows}
    if len(ids) == 1:
        return str(next(iter(ids))), None
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


def _consent_tap(c, phone, consented_at):
    """The inbound wa_messages row whose resolved button_id is CONSENT_YES_ID, at or before
    consented_at -- newest first (review item 6, 2026-09-30). Two storage shapes, both checked in
    one query: a genuine Meta-rail tap stores button_id directly on the inbound row itself
    (kind='interactive' -- 'buttons' is only ever an OUTBOUND kind, which is why the original query
    never matched anything in production); a bridge-rail typed reply recovered by
    app/wa/luna/choices.py stores it nested under button_recovery.button_id (kind='text') and
    already carries its own offer_wamid."""
    return c.execute(
        """select * from wa_messages where phone=? and direction='in' and at<=? and
           (json_extract(meta,'$.button_id')=? or json_extract(meta,'$.button_recovery.button_id')=?)
           order by at desc, id desc limit 1""",
        (phone, consented_at, CONSENT_YES_ID, CONSENT_YES_ID)).fetchone()


def _consent_offer(c, phone, tap):
    """The outbound 'buttons' offer this tap answered. choices.py's own words: "the offer is read,
    not reconstructed" -- a recovered typed reply already carries its own offer_wamid, read
    straight back; a genuine Meta-rail tap carries no such pointer (it IS the tap on the rendered
    buttons), so the newest outbound buttons row at-or-before the tap is the best available link."""
    meta = json.loads(tap["meta"] or "{}")
    offer_wamid = (meta.get("button_recovery") or {}).get("offer_wamid")
    if offer_wamid:
        return c.execute("select * from wa_messages where wamid=? and direction='out'",
                         (offer_wamid,)).fetchone()
    return c.execute(
        "select * from wa_messages where phone=? and direction='out' and kind='buttons' and at<=? "
        "order by at desc, id desc limit 1", (phone, tap["at"])).fetchone()


def _consent_info(c, phone, consented_at):
    """Recovers the consent record from wa_messages (TASK-396 field map; review item 6 fix,
    2026-09-30): the inbound tap nearest at-or-before consented_at (either storage shape -- see
    _consent_tap), and the outbound buttons bubble it answered (_consent_offer). Ids are
    wa_messages.id, NEVER a wamid (a wamid base64-encodes the raw phone, see the module docstring) --
    a heuristic (no direct link is stored, see the map's own gap note), not a guarantee."""
    tap = _consent_tap(c, phone, consented_at)
    offer = _consent_offer(c, phone, tap) if tap else None
    return {
        "offer_text": offer["body"] if offer else None,
        "offer_message_id": str(offer["id"]) if offer else None,
        "answer_message_id": str(tap["id"]) if tap else None,
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
    """Review item 10 (2026-09-30): card values come from the LIVE card (thread slots), never the
    consent-time matching snapshot (queue.card_to_candidate/profile_json) -- that snapshot's region
    is a Regierungsbezirk, not the card's region, its department is departments[0] not the
    candidate's own department_pref, and it has no housing_flexible/anonymous_send_offered at all.
    app/cv.py's own structured profile is stored nowhere live, so cv_profile/german_level are
    dropped rather than served from that same stale snapshot under a misleading name (see BASE_GAPS)."""
    thread_id = ST.thread_id_for_phone(c, phone)
    t = ST.existing_thread(c, phone)
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
            "region": live_card.get("region"), "city": live_card.get("city"),
            # The raw department_pref (pflege-fe contract, feat/pro-leads-view, 2026-09-30/10-01),
            # not profile_json's departments[0] off the stale snapshot.
            "department": live_card.get("department_pref"),
            "qualification_path": live_card.get("qualification_path"),
            "housing_needed": live_card.get("housing_needed"), "people_count": live_card.get("people_count"),
            "housing_flexible": live_card.get("housing_flexible"),
            "anonymous_send_offered": live_card.get("anonymous_send_offered"),
            "provenance": None,
        },
        "documents": [_document_summary(d) for d in ST.documents_for(c, phone)],
        "matched_clinics": _matched_clinics(c, phone),
        "handoffs": _clinic_statuses(h_rows, h_events),
        "updated_at": _max_iso(t.get("last_inbound_at"), t.get("last_outbound_at")),
    }


@router.get("/wa/pro/leads", response_model=M.LeadsEnvelope)
def pro_leads(request: Request):
    _authorize(request, SCOPE_DARIA)
    sb_conn, sb_freshness = _open_sales_brain()
    gaps = list(BASE_GAPS)
    if sb_conn is None:
        gaps.append(f"crm data (sales_brain.sqlite not found at {C.sales_brain_path()})")
    try:
        with db_ro() as c:
            # profile_json (the consent-time matching snapshot) is deliberately not selected any
            # more -- review item 10: _lead_row reads the live card, never that stale snapshot.
            cands = c.execute(
                "select q.phone, q.consented_at from wa_queue_candidates q "
                "left join wa_threads t on t.phone=q.phone where coalesce(t.is_test,0)=0 "
                "order by q.consented_at desc").fetchall()
            rows = [_lead_row(c, cand["phone"], cand, sb_conn, sb_freshness) for cand in cands]
    finally:
        if sb_conn is not None:
            sb_conn.close()
    return _scrub_wamids({"generated_at": ST.now_iso(), "source": _source(), "rows": rows, "gaps": gaps})
