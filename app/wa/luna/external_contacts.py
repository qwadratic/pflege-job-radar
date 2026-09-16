"""Optional external clinic-contact CRM (TASK-69, follow-up to TASK-64) -- unset by default. If
an operator has a real, separately-run contact CRM for their clinics (human/agent-collected
contacts, ideally with a source/evidence trail per contact), pointing WA_EXTERNAL_CONTACT_DB at
its sqlite file lets `app/wa/luna/contacts.py:discover_contact` prefer a real, role-classified
contact over a guessed website scrape. With the env var unset (the default), this module is a
complete no-op: `contact_for_clinic()` returns None immediately, and `discover_contact()` falls
through to its other sources exactly as it did before this file existed.

The database is read-only here (this repo's own process is not assumed to have direct filesystem
access to it -- it may need a privileged read path, e.g. `sudo sqlite3`, depending on how the
operator's CRM is deployed relative to this app; see WA_EXTERNAL_CONTACT_READER below) and is
never written to, never copied into this repo.

Expected schema (an integration contract, not a description of any specific product): a
``companies`` table with ``company_type`` (this module only ever queries
``company_type='clinic_location'``), ``bundesland`` and ``name``; a ``people`` table with
``company_id`` and ``role_category`` (this module prefers a person tagged with a value in
PREFERRED_ROLE_CATEGORIES below); a ``contact_channels`` table with ``owner_type``, ``owner_id``,
``channel_type`` (only ``'email'`` rows are read), ``value`` and ``is_generic``.
"""
import json
import os
import subprocess

from rapidfuzz import fuzz

DB_PATH = os.environ.get("WA_EXTERNAL_CONTACT_DB", "").strip()
# The command used to read DB_PATH, as a list prefix -- "sudo sqlite3" by default (matches a CRM
# deployed with more restrictive file permissions than this app's own process), overridable if an
# operator's deployment can read it directly (WA_EXTERNAL_CONTACT_READER="sqlite3").
READER_CMD = (os.environ.get("WA_EXTERNAL_CONTACT_READER", "sudo sqlite3").split() or ["sudo", "sqlite3"])
QUERY_TIMEOUT_SEC = 15
MATCH_THRESHOLD = 88  # rapidfuzz WRatio -- conservative on purpose: a wrong clinic match is worse than none
PREFERRED_ROLE_CATEGORIES = ("pflege_leadership", "hr_leadership", "hr")


def _sql_str(s):
    return "'" + str(s).replace("'", "''") + "'"


def _query(sql, run=None):
    """One read-only query, JSON out. ``.mode json`` is passed as a trailing argument (not a
    leading ``-json`` flag) deliberately: it keeps the database path as the reader command's first
    positional argument, matching the narrowest possible allowlist an operator would grant this
    exact read path. ``run=`` is swappable so tests never spawn a subprocess or need real access to
    any external database."""
    runner = run or subprocess.run
    proc = runner([*READER_CMD, DB_PATH, ".mode json", sql],
                  capture_output=True, text=True, timeout=QUERY_TIMEOUT_SEC)
    if proc.returncode != 0:
        raise RuntimeError(f"external contact CRM query failed: {proc.stderr.strip()[:300]}")
    out = proc.stdout.strip()
    return json.loads(out) if out else []


def _best_clinic_match(name, run=None):
    """The best-matching Bavarian clinic_location company id for this pflege-board clinic name,
    or None below MATCH_THRESHOLD. Scoped to bundesland='Bayern' first: an operator's CRM may cover
    clinics nationwide, and this board only ever covers Bavaria -- without the scope, a
    same-named clinic elsewhere in Germany could be picked by mistake."""
    rows = _query("select id, name from companies where company_type='clinic_location' "
                  "and bundesland='Bayern';", run=run)
    best_id, best_score = None, 0
    for r in rows:
        score = fuzz.WRatio(name or "", r.get("name") or "")
        if score > best_score:
            best_id, best_score = r.get("id"), score
    return best_id if best_score >= MATCH_THRESHOLD else None


def _best_contact(company_id, run=None):
    """-> (email, role_category) for this company, preferring a person tagged with a role in
    PREFERRED_ROLE_CATEGORIES, then any other person-level email, then a generic company mailbox.
    Never an unverified/hypothesis-style row -- only a channel_type of exactly 'email'."""
    role_pref = ",".join(_sql_str(r) for r in PREFERRED_ROLE_CATEGORIES)
    rows = _query(
        f"select cc.value as email, p.role_category as role_category "
        f"from contact_channels cc join people p on p.id=cc.owner_id and cc.owner_type='person' "
        f"where p.company_id={int(company_id)} and cc.channel_type='email' "
        f"order by case when p.role_category in ({role_pref}) then 0 else 1 end limit 5;", run=run)
    for r in rows:
        if r.get("role_category") in PREFERRED_ROLE_CATEGORIES and r.get("email"):
            return r["email"], r["role_category"]
    if rows and rows[0].get("email"):
        return rows[0]["email"], rows[0].get("role_category") or "unknown_role"
    generic = _query(
        f"select value as email from contact_channels where owner_type='company' "
        f"and owner_id={int(company_id)} and channel_type='email' and is_generic=1 limit 1;", run=run)
    if generic and generic[0].get("email"):
        return generic[0]["email"], "generic_company_mailbox"
    return None, None


def contact_for_clinic(clinic_name, run=None):
    """-> {email, source, confidence} or None. Returns None immediately, without querying
    anything, when WA_EXTERNAL_CONTACT_DB is unset -- this integration is opt-in. source is always
    'external_crm'; confidence is 'high' only for a role-matched leadership contact, 'medium' for
    anything else this found (another person at the matched clinic, or a generic mailbox)."""
    if not DB_PATH:
        return None
    company_id = _best_clinic_match(clinic_name, run=run)
    if company_id is None:
        return None
    email, role = _best_contact(company_id, run=run)
    if not email:
        return None
    confidence = "high" if role in PREFERRED_ROLE_CATEGORIES else "medium"
    return {"email": email.strip().lower(), "source": "external_crm", "confidence": confidence}
