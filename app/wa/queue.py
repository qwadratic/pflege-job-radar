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
from . import store as ST
from .luna import contacts as CT

_QUALIFICATION_PHRASE = {
    "urkunde": "Gesundheits- und Krankenpflegerin, Urkunde anerkannt",
    "defizit": "Krankenpflege, Defizitbescheid erhalten",
    "kenntnispruefung": "Krankenpflege, Kenntnisprüfung bestanden",
}
_ANERKENNUNG_STATUS = {"urkunde": "granted", "defizit": "deficit_notice", "kenntnispruefung": "applied"}
MATCH_TOP_N = 5

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
    qualification, departments, german_level, anerkennung_status.

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
    }


def build_queue_entry(phone, card, cv_profile=None):
    """Ranks this candidate against every clinic in the live snapshot (app.autopilot.matching.rank,
    reused as-is -- job-matching scoring is not something this task reinvents), resolves each
    ranked clinic's contact via TASK-64's clinic_contacts table, and upserts both the candidate and
    its matches into this module's own tables. Idempotent: re-running for the same phone (e.g. a
    later CV upload, or a repeat consent) replaces the candidate row and upserts matches on the
    (phone, clinic_id, posting_id) unique key rather than duplicating rows."""
    candidate = card_to_candidate(card, cv_profile)
    snap = D.snapshot()
    ranked = MATCH.rank(candidate, snap["clinics"], snap["jobs"], n=MATCH_TOP_N)

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
    clinics they matched, contact included where known."""
    cands = [dict(r) for r in conn.execute(
        "select phone, consented_at, profile_json, status from wa_queue_candidates order by consented_at desc").fetchall()]
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
    end-to-end test asserts against. Never sends anything; this is a read-only preview."""
    rows = conn.execute(
        """select m.phone, m.clinic_id, m.posting_id, m.score, m.contact_email, m.contact_source
           from wa_queue_matches m
           order by m.clinic_id, m.score desc""").fetchall()
    return [dict(r) for r in rows]
