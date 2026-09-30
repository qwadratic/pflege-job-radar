"""Pydantic response shapes for the Pro API (TASK-395/396, Ivan 2026-09-29/30). One place the
contract's field names and types live as code, not just as prose in docs/wa-dashboard.md -- every
route in app/wa/pro_api.py declares one of these as its ``response_model``, so a field this module
drops or mistypes fails the request loudly (a 500 from FastAPI's own validation) instead of quietly
shipping the wrong shape to pflege-fe. tests/test_wa_pro_fixtures.py validates
tests/fixtures/wa_pro_api/threads.json against these same models, so the fixture and the contract
cannot drift apart unnoticed.

Most models set ``extra="forbid"``: every field we serialize is named here on purpose, and an
untracked extra key appearing in a hand-built dict is a bug, not a feature -- CLAUDE.md's "no silent
fallbacks" applies to outgoing shapes too. The two exceptions are HealthResponse (a passthrough of
app/wa/config.py:readiness(), whose own key set already varies with WA_BRAIN) and CrmCase (a
passthrough of the colleague's sales_brain columns "as they are", TASK-396's own instruction) --
both declare ``extra="allow"`` instead.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

# --- shared enums / literals --------------------------------------------------------------------

Rail = Literal["meta", "bridge"]
Ball = Literal["us", "them", "silent", "none"]
Stage = Literal["contact", "qualification", "matching", "cv", "documents", "consent", "submitted"]
Outcome = Literal["declined", "already_placed", "not_placeable"]
GateState = Literal["satisfied", "open", "blocked"]
MessageDirection = Literal["in", "out"]
DeliveryStatus = Literal["sent", "delivered", "read", "failed"]
ThreadHandoffStatus = Literal["queued", "attention", "in_progress", "signed", "closed"]
#: The closed list of statuses POST /api/wa/pro/handoffs accepts (TASK-396). Any other value is a
#: 400, never silently coerced to the nearest one.
HandoffStatus = Literal["sent_to_clinic", "followup_sent", "clinic_replied", "interview_scheduled",
                        "trial_scheduled", "offer", "contract_signed", "declined", "closed", "halted"]
CrmMatch = Literal["none", "ambiguous", "unavailable"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- small nested shapes -------------------------------------------------------------------------

class CardSummary(_Strict):
    region: str | None = None
    city: str | None = None
    department: str | None = None
    qualification_path: str | None = None
    housing_needed: bool | None = None
    people_count: int | None = None
    campaign: dict[str, Any] | None = None
    match_branch: str | None = None


class Gates(_Strict):
    """Exactly the 7 contract gate keys (wa-dashboard.md) -- requirement_scoreboard() itself carries
    an 8th ("documents") plus non-gate hints (next_objective/stage/stage_since) mixed into the same
    dict; the route selects only these 7 before this model ever sees them."""
    region: GateState
    qualification: GateState
    city_or_department: GateState
    housing: GateState
    cv_document: GateState
    qualification_document: GateState
    handoff_consent: GateState


class SuppressionInfo(_Strict):
    reason: str
    lane: str
    at: str


class PendingInbound(_Strict):
    count: int
    oldest_recorded_at: str | None = None
    last_error: str | None = None


class SendError(_Strict):
    error: str
    at: str


class LastMessage(_Strict):
    direction: MessageDirection
    kind: str
    preview: str | None = None
    at: str


class HandoffClinicStatus(_Strict):
    """One target clinic's current status inside a thread-level/lead-level handoff summary."""
    clinic_id: str | None = None
    clinic_name: str | None = None
    external_ref: str | None = None
    status: HandoffStatus
    ts: str
    attention: bool


class ThreadHandoff(_Strict):
    status: ThreadHandoffStatus
    consented_at: str | None = None
    clinics: list[HandoffClinicStatus] = []


class DocumentMeta(_Strict):
    """Metadata only -- the whitelist (id, kind, document_type, mime_type, size_bytes, received_at,
    reuse_state). Never path/sha256/media_id/original_filename/text/text_key/import_*."""
    id: int
    kind: str
    document_type: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    received_at: str
    reuse_state: str | None = None


class MatchedClinic(_Strict):
    clinic_id: str
    clinic_name: str | None = None
    town: str | None = None
    score: int


# --- GET /api/wa/pro/threads ----------------------------------------------------------------------

class ThreadRow(_Strict):
    thread_id: str
    phone_masked: str | None = None
    is_test: bool
    rail: Rail | None = None
    opened_at: str | None = None
    last_inbound_at: str | None = None
    last_outbound_at: str | None = None
    turns: int
    ball: Ball
    stage: Stage
    stage_since: str | None = None
    outcome: Outcome | None = None
    gates: Gates
    card: CardSummary
    stopped: bool
    stopped_reason: str | None = None
    suppression: SuppressionInfo | None = None
    escalation_codes: list[str] = []
    flag_codes: list[str] = []
    escalated_at: str | None = None
    stuck_reply: bool
    pending_inbound: PendingInbound | None = None
    last_send_error: SendError | None = None
    handoff: ThreadHandoff | None = None
    last_message: LastMessage | None = None
    #: TASK-316 (P4), not built -- always null until it ships (wa-dashboard.md).
    lead_status: Any | None = None


class ThreadsEnvelope(_Strict):
    total: int
    limit: int
    offset: int
    next_offset: int | None = None
    test_threads: int
    generated_at: str
    source: str
    rows: list[ThreadRow]


class ThreadDetailResponse(_Strict):
    thread: ThreadRow
    escalation_notes: list[str] = []
    flag_notes: list[str] = []
    next_objective: str | None = None
    documents: list[DocumentMeta] = []
    send_failures: list[SendError] = []
    handoff_matches: list[MatchedClinic] = []


# --- GET /api/wa/pro/threads/{id}/messages ---------------------------------------------------------

class MessageRow(_Strict):
    id: int
    direction: MessageDirection
    kind: str
    body: str | None = None
    at: str
    deleted: bool
    status: DeliveryStatus | None = None
    meta: dict[str, Any] = {}


class MessagesEnvelope(_Strict):
    rows: list[MessageRow]
    next_before_id: int | None = None


# --- GET /api/wa/pro/health -------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """Proxied unchanged from config.readiness() + rails (same body as the harness's own, public
    GET /wa/health) -- extra="allow" because readiness()'s key set grows by three (luna_model,
    luna_ready, refusal_model) only when WA_BRAIN=luna."""
    model_config = ConfigDict(extra="allow")
    checks: dict[str, bool]
    webhook_ready: bool
    outbound_ready: bool
    autosend: bool
    graph_api_version: str
    brain: str
    transport: str
    reply_scope: str
    meta_scope: str
    bridge_ready: bool
    bridge_inbound_ready: bool
    bridge_phone_number_id: str
    luna_media_host: str
    luna_media_dir: str
    stt_ready: bool
    stt_model: str
    rails: dict[str, int]


# --- handoff write-back (TASK-396) ------------------------------------------------------------------

class HandoffWriteResponse(_Strict):
    applied: bool
    duplicate: bool
    #: True when the posted (lead_key, target_key, status) is the row's CURRENT status after this
    #: write -- False for a genuinely older event (by ts) that only reached the audit trail.
    current: bool
    lead_key: str
    target_key: str
    #: The target's status as it stands after this write (not necessarily the posted one: an
    #: out-of-order older event leaves the existing current status in place).
    status: HandoffStatus


class HandoffRow(_Strict):
    """One (lead_key, target_key)'s current row, as GET /api/wa/pro/handoffs returns it."""
    lead_key: str
    target_key: str
    thread_id: str | None = None
    crm_candidate_id: str | None = None
    clinic_id: str | None = None
    clinic_name: str | None = None
    external_ref: str | None = None
    status: HandoffStatus
    sender_box: str | None = None
    message_id: str | None = None
    batch_id: str | None = None
    note: str | None = None
    ts: str
    updated_at: str
    attention: bool


class HandoffEventRow(_Strict):
    """One append-only audit-trail row (wa_handoff_events)."""
    id: int
    lead_key: str
    target_key: str
    thread_id: str | None = None
    crm_candidate_id: str | None = None
    clinic_id: str | None = None
    clinic_name: str | None = None
    external_ref: str | None = None
    status: HandoffStatus
    prev_status: HandoffStatus | None = None
    sender_box: str | None = None
    message_id: str | None = None
    batch_id: str | None = None
    note: str | None = None
    ts: str
    who: str
    recorded_at: str


class HandoffGetResponse(_Strict):
    lead_key: str
    rows: list[HandoffRow]
    events: list[HandoffEventRow]


# --- GET /api/wa/pro/leads (Daria, TASK-396) ---------------------------------------------------------

class ConsentInfo(_Strict):
    offer_text: str | None = None
    offer_message_id: str | None = None
    answer_message_id: str | None = None
    answered_at: str | None = None
    #: Not recorded anywhere (TASK-396 gap) -- always null; scope_note says where to read it instead.
    scope: Any | None = None
    scope_note: str


class LeadCardValues(_Strict):
    region: str | None = None
    city: str | None = None
    department: str | None = None
    qualification_path: str | None = None
    housing_needed: bool | None = None
    people_count: int | None = None
    german_level: str | None = None
    #: Per-field provenance (which message said this) is not tracked anywhere -- always null.
    provenance: Any | None = None


class CrmCase(BaseModel):
    """One candidate_clinic_cases x placement_case_state row, read-only, columns as sales_brain has
    them (TASK-396's own instruction) -- extra="allow" because this is a passthrough of the
    colleague's CRM schema, not a shape this repo owns."""
    model_config = ConfigDict(extra="allow")


class LeadRow(_Strict):
    thread_id: str
    crm_candidate_id: str | None = None
    crm_match: CrmMatch | None = None
    crm_cases: list[CrmCase] = []
    crm_freshness: str | None = None
    consent: ConsentInfo
    card: LeadCardValues
    cv_profile: dict[str, Any] | None = None
    documents: list[DocumentMeta] = []
    matched_clinics: list[MatchedClinic] = []
    handoffs: list[HandoffClinicStatus] = []
    updated_at: str | None = None


class LeadsEnvelope(_Strict):
    generated_at: str
    source: str
    rows: list[LeadRow]
    #: Fields Daria asked for that no source in this repo (or sales_brain) has -- structured per-role
    #: CV, employers, a document-verified flag, consent scope, per-field provenance -- named here
    #: instead of silently absent, per TASK-396's implementation notes.
    gaps: list[str]
