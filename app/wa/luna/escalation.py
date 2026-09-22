"""Why a thread needs a human -- a closed, named list, never free text the model invents (Ivan,
2026-09-22 after the multi-city UAT round: "чтобы у меня был предсказуемый список ситуаций, когда
идет эскалация на человека... не было ситуаций, в которых человек вмешивается в чат, а там реально
какая-то херня, которую мог бы решить искусственный интеллект").

TWO TIERS, kept apart on purpose:

ESCALATE (``card._escalated``): a human genuinely needs to look at this thread. Every reason is one
of the named codes below -- CODES, the closed set -- never a sentence the model composed on the
spot. Three sources today, together naming eight codes:
  - the model's own judgement, MODEL_CODES (EXPLICIT_HUMAN_REQUEST / PET_POLICY_QUESTION /
    VISA_OR_IMMIGRATION_SPECIFICS / LEGAL_OR_CONTRACT_POLICY_QUESTION / UNREADABLE_ATTACHMENT /
    PRIOR_APPLICATION_STATUS_QUESTION), gated by ``record_model_escalation`` -- a code outside this
    list is NOT honoured (see there)
  - a media type the harness does not process at all (UNREAD_MEDIA, app/wa/api.py)
  - two consecutive replies that broke a checked dialog rule (GROUNDING_RULE_VIOLATED_TWICE,
    app/wa/luna_brain.py's own two-strikes mechanism -- a harness limitation, not a topic)

FLAG (``card._flags``): worth a look, nothing broken, the conversation is fine as it stands. Never
sets ``_escalated`` -- these used to be folded into the same field an escalation uses, which is
exactly what let something unremarkable pull a human in (round 2026-09-22's own finding: a bare
disagreement between the decline flag and the refusal classifier, or the exhaustive-claim heuristic
merely being suspicious, both used to read as "escalated"). Two sources today:
  - the model raised decline=true but the independent refusal classifier disagreed, or could not run
    (DECLINE_CLASSIFIER_DISAGREEMENT) -- the conversation keeps going either way
  - the exhaustive-claim heuristic suspected a reply without blocking it (EXHAUSTIVE_CLAIM_SUSPECTED,
    round 5, grounding.py's own module docstring)
  - the model tried to escalate for a reason outside the closed set (UNRECOGNIZED_ESCALATION_ATTEMPT)
    -- the escalation itself is refused (the reply still goes out, no human is pulled in on an
    unaudited reason), but the attempt is kept visible: if this code appears often, the closed set
    is missing a real category and belongs back in front of Ivan, not silently expanded by the model.

Both tiers append (never overwrite): several facts about one turn all survive, each under its own
code, the same discipline TASK-156 already established for the single string field this splits.
"""
from __future__ import annotations

# --- ESCALATE: the model's own four judgement calls (gated, see record_model_escalation) -----------
EXPLICIT_HUMAN_REQUEST = "explicit_human_request"
PET_POLICY_QUESTION = "pet_policy_question"
VISA_OR_IMMIGRATION_SPECIFICS = "visa_or_immigration_specifics"
LEGAL_OR_CONTRACT_POLICY_QUESTION = "legal_or_contract_policy_question"
UNREADABLE_ATTACHMENT = "unreadable_attachment"
#: prompts.py's own PRIOR CONTACT rule: asked about an earlier application or clinic submission's
#: outcome -- only a human can check the real status, the card's own prior_placement is a record,
#: never a live status the model may report as current.
PRIOR_APPLICATION_STATUS_QUESTION = "prior_application_status_question"
MODEL_CODES = (EXPLICIT_HUMAN_REQUEST, PET_POLICY_QUESTION, VISA_OR_IMMIGRATION_SPECIFICS,
              LEGAL_OR_CONTRACT_POLICY_QUESTION, UNREADABLE_ATTACHMENT, PRIOR_APPLICATION_STATUS_QUESTION)

# --- ESCALATE: code-decided, never the model's call ------------------------------------------------
UNREAD_MEDIA = "unread_media"
GROUNDING_RULE_VIOLATED_TWICE = "grounding_rule_violated_twice"
CODE_CODES = (UNREAD_MEDIA, GROUNDING_RULE_VIOLATED_TWICE)

ESCALATE_CODES = MODEL_CODES + CODE_CODES

# --- FLAG: worth a look, the conversation is fine -------------------------------------------------
DECLINE_CLASSIFIER_DISAGREEMENT = "decline_classifier_disagreement"
EXHAUSTIVE_CLAIM_SUSPECTED = "exhaustive_claim_suspected"
UNRECOGNIZED_ESCALATION_ATTEMPT = "unrecognized_escalation_attempt"
FLAG_CODES = (DECLINE_CLASSIFIER_DISAGREEMENT, EXHAUSTIVE_CLAIM_SUSPECTED, UNRECOGNIZED_ESCALATION_ATTEMPT)

#: A short English label per code, for anything that renders the list for a human rather than
#: reading it as data (an operator view, a report) -- never shown to the candidate.
LABELS = {
    EXPLICIT_HUMAN_REQUEST: "candidate explicitly asked for a human",
    PET_POLICY_QUESTION: "pet policy in staff housing (board has no data)",
    VISA_OR_IMMIGRATION_SPECIFICS: "visa / immigration specifics",
    LEGAL_OR_CONTRACT_POLICY_QUESTION: "legal or contract policy question",
    UNREADABLE_ATTACHMENT: "a sent attachment could not be read",
    PRIOR_APPLICATION_STATUS_QUESTION: "asked about an earlier application/clinic's real status",
    UNREAD_MEDIA: "a message type the harness does not process",
    GROUNDING_RULE_VIOLATED_TWICE: "two replies running broke a checked dialog rule",
    DECLINE_CLASSIFIER_DISAGREEMENT: "decline flagged, refusal classifier disagreed (flag only)",
    EXHAUSTIVE_CLAIM_SUSPECTED: "reply may have implied 'all of them' (flag only)",
    UNRECOGNIZED_ESCALATION_ATTEMPT: "model tried to escalate for an unlisted reason (suppressed)",
}


def _append(card, escalated_key, codes_key, reason_key, code, detail):
    codes = card.get(codes_key) or []
    card[codes_key] = [*codes, code]
    notes_key = f"{reason_key}_notes"
    notes = card.get(notes_key) or []
    note = f"{code}: {detail}" if detail else code
    card[notes_key] = [*notes, note]
    card[reason_key] = "; ".join(card[notes_key])
    if escalated_key:
        card[escalated_key] = True


def record_escalation(card, code, detail=None):
    """A human genuinely needs this thread. ``code`` must be one of ESCALATE_CODES -- raises
    otherwise (CLAUDE.md: no invented reasons slip through as a silent default), so a new trigger
    is always added here first, never called ad hoc from wherever it happens to fire."""
    if code not in ESCALATE_CODES:
        raise RuntimeError(f"{code!r} is not one of the closed escalation codes: {ESCALATE_CODES!r}")
    _append(card, "_escalated", "_escalation_codes", "_escalate_reason", code, detail)


def record_flag(card, code, detail=None):
    """Worth a look, never pulls a human in -- never sets ``_escalated``. ``code`` must be one of
    FLAG_CODES."""
    if code not in FLAG_CODES:
        raise RuntimeError(f"{code!r} is not one of the closed flag codes: {FLAG_CODES!r}")
    _append(card, None, "_flag_codes", "_flags", code, detail)


def record_model_escalation(card, code, detail=None):
    """The model asked to escalate, naming ``code`` itself. -> True if it was honoured (``code`` is
    one of MODEL_CODES: a real escalation, ``_escalated`` set), False if not (an unrecognized code
    is never honoured -- the escalation is refused, not the reply: the candidate still gets the
    turn's own bubbles, and the attempt is recorded as a flag instead, so a code missing from the
    closed set stays visible without ever letting the model route around the list on its own)."""
    if code in MODEL_CODES:
        record_escalation(card, code, detail)
        return True
    reason = f"model asked to escalate with code {code!r} (not in the closed set): {detail}" if detail \
        else f"model asked to escalate with code {code!r}, not in the closed set"
    record_flag(card, UNRECOGNIZED_ESCALATION_ATTEMPT, reason)
    return False
