"""Candidate queue: what happens once a WhatsApp candidate consents to an anonymized profile send
(app/wa/luna_brain.py's card_patch.anonymous_send_consent), triggered from app/wa/api.py (plan
section 4 -- not from luna_brain.py:turn() itself, which has no DB handle and runs before the
thread is durably saved).

Reuses app.autopilot.matching.rank() (score/rank -- a real, transparent weighted-match engine)
directly against the live app.data snapshot. Deliberately does NOT write into app.autopilot.db's
own SQLite: that system is explicitly labelled a synthetic-PoC-only demo with its one UI entry
point disabled since 2026-09-08 (app/main.py:autopilot_page) -- real candidate PII has no business
in a store built and documented as synthetic. This module owns its own two tables instead, in the
same sqlite file app/wa/store.py already uses (app.wa.config.SQLITE_PATH), the same pattern
app/wa/luna/contacts.py already established for clinic_contacts.
"""
import json

from .. import data as D
from ..autopilot import matching as MATCH
from . import luna_brain as LB
from . import store as ST
from .luna import contacts as CT
from .luna import offer as OF

_QUALIFICATION_PHRASE = {
    "urkunde": "Gesundheits- und Krankenpflegerin, Urkunde anerkannt",
    "defizit": "Krankenpflege, Defizitbescheid erhalten",
    "kenntnispruefung": "Krankenpflege, Kenntnisprüfung bestanden",
}
_ANERKENNUNG_STATUS = {"urkunde": "granted", "defizit": "deficit_notice", "kenntnispruefung": "applied"}

# How many ranked clinics reach the queue for a card that did NOT choose the pool branch: the
# narrow branch, and every card from before card.match_branch existed. This is what every consent
# queued before TASK-144's branches, kept unchanged for those two cases -- it is NOT Ivan's five,
# which is app/wa/luna/offer.py:OFFER_LIMIT and caps one MESSAGE, not the handoff list. Nobody has
# stated a rule for the narrow branch's handoff size; this number is the status quo, not a decision.
MATCH_TOP_N = 5


def match_limit(card):
    """How many ranked clinics this card's consent may queue. ``None`` means no limit at all.

    Ivan, 2026-09-21: five is the cap on how many positions one message may show, however many
    matched; the pool branch -- the candidate answering "then put me forward to all of them" --
    means all X clinics that matched their criteria. So a pool card is queued whole. The message
    promised all of them, and the message is the one the candidate read.

    A card with no ``match_branch`` (a thread from before the field, or a consent that never went
    through the offer turn) keeps the behaviour it had: MATCH_TOP_N. Anything else is a branch value
    this module does not know -- it raises rather than quietly picking one of the two behaviours,
    because both choices would be a guess about what a candidate was told."""
    branch = card.get("match_branch")
    if branch is None:
        return MATCH_TOP_N
    if branch == OF.BRANCH_POOL:
        return None
    if branch == OF.BRANCH_NARROW:
        return MATCH_TOP_N
    raise ValueError(f"unknown card.match_branch {branch!r}: expected {OF.BRANCH_NARROW!r}, "
                     f"{OF.BRANCH_POOL!r} or none at all (app/wa/luna/offer.py:BRANCHES)")


SCHEMA = """
create table if not exists wa_queue_candidates (
  phone text primary key,
  consented_at text not null,
  profile_json text not null,
  status text not null default 'queued'
);
create table if not exists wa_queue_matches (
  id integer primary key,
  phone text not null,
  clinic_id text not null,
  posting_id integer,
  score integer not null,
  reasons_json text not null,
  contact_email text,
  contact_source text,
  unique(phone, clinic_id, posting_id)
);
"""


def db():
    """Same connection/pragma setup as app.wa.store.db() (same wa.sqlite file), plus this
    module's own two tables -- not mixed into wa_threads/wa_messages or autopilot's schema. Also
    applies contacts.py's SCHEMA (its clinic_contacts table, same sqlite file): build_queue_entry
    reads a contact on this same connection, and CREATE TABLE IF NOT EXISTS only ever runs when
    something actually calls it -- a fresh database that nothing has ever pointed app.wa.luna.
    contacts.db() at (e.g. a candidate whose clinic contact was never discovered) would otherwise
    be missing the table the moment get_contact() tries to select from it."""
    c = ST.db()
    c.executescript(SCHEMA)
    c.executescript(CT.SCHEMA)
    return c


def _german_level(cv_profile):
    for lang in (cv_profile or {}).get("languages") or []:
        low = str(lang).lower()
        if "deutsch" in low:
            for tok in str(lang).split():
                if tok.upper() in ("A1", "A2", "B1", "B2", "C1", "C2"):
                    return tok.upper()
    return None


def card_to_candidate(card, cv_profile=None):
    """Maps a Luna card (+ optional CV profile from app.cv.analyse_llm/analyse) into the
    candidate-dict shape app.autopilot.matching.score/rank expect: role_class, city, region,
    qualification, departments, german_level, anerkennung_status -- plus needs_housing/people_count
    (TASK-108), which matching.score does not read but build_queue_entry and the human who picks the
    queue up do.

    needs_housing is luna_brain.housing_needed(card), the same predicate the shortlist Luna named in
    the chat was built from, so the handoff cannot offer a clinic the conversation ruled out.
    housing_flexible (luna_brain.housing_flexible) is the separate "would also take a clinic without a flat"
    answer: it widens the matching below, and it leaves needs_housing standing, so the human reads "wanted a
    flat for 2, accepts one without" instead of "needs none" (review 2026-09-16).

    ``region`` here must be a Bavarian Regierungsbezirk (matching.score compares it against a
    clinic's own regierungsbezirk), derived from the card's city -- NOT card.get("region"), which
    holds an unrelated value: the out-of-scope-Bundesland gate result from
    luna_brain.py:named_non_bavaria_land (e.g. "hessen"), only ever set for a non-Bavaria answer,
    never a Regierungsbezirk.
    """
    cv_profile = cv_profile or {}
    path = card.get("qualification_path")
    city = card.get("city") or next(iter(cv_profile.get("cities") or []), None)

    region = None
    if city:
        from .. import cv as CV
        bez = CV._regierungsbezirke_for_cities([city])
        region = bez[0] if bez else None
    if region is None:
        region = next(iter(cv_profile.get("regierungsbezirke") or []), None)

    role_class = next(iter(cv_profile.get("roles") or []), None)
    if not role_class and path in ("urkunde", "defizit", "kenntnispruefung"):
        role_class = "pflegefachkraft"

    departments = list(cv_profile.get("departments") or [])
    if card.get("department_pref") and card["department_pref"] not in departments:
        departments = [card["department_pref"]] + departments

    qualification_bits = list(cv_profile.get("qualifications") or [])
    if path in _QUALIFICATION_PHRASE:
        qualification_bits.append(_QUALIFICATION_PHRASE[path])

    return {
        "role_class": role_class,
        "city": city,
        "region": region,
        "qualification": " ".join(qualification_bits) or None,
        "departments": departments,
        "german_level": _german_level(cv_profile),
        "anerkennung_status": _ANERKENNUNG_STATUS.get(path, "none"),
        "needs_housing": LB.housing_needed(card),
        "housing_flexible": LB.housing_flexible(card),
        "people_count": card.get("people_count"),
    }


def build_queue_entry(phone, card, cv_profile=None):
    """Ranks this candidate against every clinic in the live snapshot (app.autopilot.matching.rank,
    reused as-is -- job-matching scoring is not something this task reinvents), resolves each
    ranked clinic's contact via TASK-64's clinic_contacts table, and upserts both the candidate and
    its matches into this module's own tables. Idempotent: re-running for the same phone (e.g. a
    later CV upload, or a repeat consent) replaces the candidate row and upserts matches on the
    (phone, clinic_id, posting_id) unique key rather than duplicating rows.

    TASK-108: a candidate who needs a flat is ranked only against the postings the board marks with housing
    (app.data.offers_housing -- the same criterion market_snapshot's shortlist uses) and the clinics those
    postings belong to, so the human handoff gets the clinics Luna was allowed to name, not a wider list. Once
    they said a clinic without a flat is also an option (housing_flexible), the filter drops here exactly as it
    does in the shortlist -- the same rule on both sides, again.

    TASK-144: how many of the ranked clinics are queued is the candidate's own branch choice,
    ``match_limit(card)`` -- a pool card is queued whole (``n=None``, no cut in matching.rank), a narrow or
    branchless card keeps MATCH_TOP_N. Before this, every consent queued five whatever the card said, so a
    candidate who was told "we put you forward to all 224 matching clinics" got five of them."""
    candidate = card_to_candidate(card, cv_profile)
    snap = D.snapshot()
    jobs, clinics = snap["jobs"], snap["clinics"]
    if candidate["needs_housing"] and candidate["housing_flexible"] is not True:
        jobs = [j for j in jobs if D.offers_housing(j)]
        with_housing = {str(j["clinic_id"]) for j in jobs if j.get("clinic_id")}
        clinics = [c for c in clinics if str(c["clinic_id"]) in with_housing]
    # n=None is matching.rank's own "no cut" (its out[:n]); a clinic still has to score at least
    # min_score to be in `ranked` at all, which is what "matched their criteria" means here.
    ranked = MATCH.rank(candidate, clinics, jobs, n=match_limit(card))

    conn = db()
    try:
        conn.execute(
            """insert into wa_queue_candidates (phone, consented_at, profile_json, status)
               values (?,?,?,?)
               on conflict(phone) do update set
                 consented_at=excluded.consented_at, profile_json=excluded.profile_json, status=excluded.status""",
            (phone, ST.now_iso(), json.dumps(candidate, ensure_ascii=False), "queued"))
        for m in ranked:
            contact = CT.get_contact(conn, m["clinic_id"])
            conn.execute(
                """insert into wa_queue_matches
                     (phone, clinic_id, posting_id, score, reasons_json, contact_email, contact_source)
                   values (?,?,?,?,?,?,?)
                   on conflict(phone, clinic_id, posting_id) do update set
                     score=excluded.score, reasons_json=excluded.reasons_json,
                     contact_email=excluded.contact_email, contact_source=excluded.contact_source""",
                (phone, m["clinic_id"], m.get("posting_id"), m["score"],
                 json.dumps(m["reasons"], ensure_ascii=False),
                 (contact or {}).get("email"), (contact or {}).get("source")))
        conn.commit()
    finally:
        conn.close()
    return {"phone": phone, "candidate": candidate, "matches": ranked}


def queue_rows(conn):
    """(candidates, clinics-subset) view for GET /api/wa/queue: every queued candidate with the
    clinics they matched, contact included where known. A phone marked as a test number (TASK-109,
    wa_threads.is_test) is left out: this list is the handoff a human works from, and an operator's
    own test consent is not a candidate. The entry itself is still written when a test thread
    consents -- the whole path stays exercised end to end -- and purge_test_history.py deletes it."""
    cands = [dict(r) for r in conn.execute(
        "select q.phone, q.consented_at, q.profile_json, q.status from wa_queue_candidates q "
        "left join wa_threads t on t.phone = q.phone where coalesce(t.is_test, 0) = 0 "
        "order by q.consented_at desc").fetchall()]
    out = []
    for c in cands:
        c["profile"] = json.loads(c.pop("profile_json"))
        matches = [dict(r) for r in conn.execute(
            "select clinic_id, posting_id, score, reasons_json, contact_email, contact_source "
            "from wa_queue_matches where phone=? order by score desc", (c["phone"],)).fetchall()]
        for m in matches:
            m["reasons"] = json.loads(m.pop("reasons_json"))
        c["matches"] = matches
        out.append(c)
    return out


def mailing_list_rows(conn):
    """Flattened candidate x clinic x contact-email preview -- the report shape TASK-68's
    end-to-end test asserts against. Never sends anything; this is a read-only preview. Test
    numbers (TASK-109) are left out, like in queue_rows: nobody should be preparing an email to a
    clinic about an operator's test persona."""
    rows = conn.execute(
        """select m.phone, m.clinic_id, m.posting_id, m.score, m.contact_email, m.contact_source
           from wa_queue_matches m
           left join wa_threads t on t.phone = m.phone
           where coalesce(t.is_test, 0) = 0
           order by m.clinic_id, m.score desc""").fetchall()
    return [dict(r) for r in rows]
