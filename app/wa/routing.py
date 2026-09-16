"""Conversation ownership (TASK-75): which system -- this harness ("us") or the real production
bot ("them") -- currently owns a WhatsApp phone number's conversation.

This is deliberately the safe, in-repo, reversible slice of a bigger idea: route genuinely NEW
leads to this harness and leave existing/older conversations with the real system, except one an
operator of this harness reopens themselves (TASK-70's reopen template) -- that one explicit act
hands the conversation to us from then on. Actually pointing Meta's webhook at a router that
consults this table is a separate, production-infrastructure change needing its own coordination
and sign-off with whoever owns that Meta app; nothing here does that, and nothing here changes
which system Meta currently sends a message to. What this module gives that future router (or a
human deciding by hand today) is the decision itself, durably recorded and inspectable.

Three ways a phone gets an owner, and only three -- no other code path may write to wa_ownership:
1. route_decision(): a phone with no record yet. Whether it defaults to "us" or "them" depends on
   whether it is already known to the real system -- see _is_known_to_real_system, a pluggable,
   EXPLICITLY CONFIGURED seam (WA_REAL_SYSTEM_PHONES_FILE). Unset, this refuses to guess rather
   than silently defaulting either way: getting this wrong has a real consequence (an existing
   real customer treated as a cold lead, or vice versa), so an unconfigured check is a loud error,
   not a coin flip.
2. flip_to_us_on_reopen(): called from app/wa/api.py's _send_reopen_template the moment THIS
   harness sends a reopen template to a phone -- regardless of whatever owned it before, that is
   the one explicit act that hands the conversation to us.
3. flip_to_us_for_campaign() / restore_after_campaign_failure() (TASK-103, app/wa/luna/campaign.py):
   the campaign sender hands a phone to us (reason 'campaign:<id>') before it posts the template, and
   puts the prior record back when Meta rejects that send. Neither commits: the sender commits the
   flip together with its send claim, and the restore together with the failed claim.
"""
import pathlib
from datetime import datetime, timezone

from . import config as C
from . import store as ST

SCHEMA = """
create table if not exists wa_ownership (
  phone text primary key,
  owner text not null check (owner in ('us', 'them')),
  reason text not null,
  since text not null
);
"""


def db():
    """Same connection/pragma setup as app.wa.store.db() (same wa.sqlite file), plus this
    module's own table."""
    c = ST.db()
    c.executescript(SCHEMA)
    return c


def _is_known_to_real_system(phone):
    """True if this phone already has a conversation history in the real production system --
    the one piece of information this harness cannot derive on its own. WA_REAL_SYSTEM_PHONES_FILE
    is a plain, newline-delimited, operator-produced export (an authorized, periodic sync from
    wherever the real system's own data lives) -- same genericize-the-real-system discipline as
    app/wa/luna/external_contacts.py (TASK-69): this module never names or queries any specific
    real system directly."""
    if not C.REAL_SYSTEM_PHONES_FILE:
        raise RuntimeError(
            "cannot route a phone with no ownership record yet: WA_REAL_SYSTEM_PHONES_FILE is not "
            "configured, so this harness has no way to tell a genuinely new lead from an existing "
            "real-system candidate -- configure it (a periodically synced, newline-delimited file "
            "of known phone numbers) before routing depends on this")
    path = pathlib.Path(C.REAL_SYSTEM_PHONES_FILE)
    if not path.exists():
        raise RuntimeError(f"WA_REAL_SYSTEM_PHONES_FILE={C.REAL_SYSTEM_PHONES_FILE!r} does not exist")
    known = {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    return phone in known


def _set_ownership(conn, phone, owner, reason):
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn.execute(
        "insert into wa_ownership (phone, owner, reason, since) values (?,?,?,?) "
        "on conflict(phone) do update set owner=excluded.owner, reason=excluded.reason, since=excluded.since",
        (phone, owner, reason, now))
    conn.commit()


def route_decision(conn, phone):
    """-> 'us' or 'them'. An existing ownership record is authoritative and durable -- only
    flip_to_us_on_reopen() and the campaign pair below may ever change it once set. A brand-new phone is
    decided (and the decision recorded) here: unknown to the real system -> 'us' (a genuinely new lead); known to
    the real system -> 'them' (an existing conversation, left alone unless we reopen it)."""
    row = conn.execute("select owner from wa_ownership where phone=?", (phone,)).fetchone()
    if row is not None:
        return row["owner"]
    if _is_known_to_real_system(phone):
        _set_ownership(conn, phone, "them", reason="known_to_real_system")
        return "them"
    _set_ownership(conn, phone, "us", reason="new_lead")
    return "us"


def flip_to_us_on_reopen(conn, phone):
    """The one explicit trigger that hands an existing conversation to us regardless of its prior
    owner: this harness itself just sent that phone a reopen template (TASK-70)."""
    _set_ownership(conn, phone, "us", reason="reopened_by_us")


# --- campaign sends (TASK-103) ---------------------------------------------------------------------

CAMPAIGN_REASON_PREFIX = "campaign:"


def campaign_reason(campaign_id):
    return CAMPAIGN_REASON_PREFIX + campaign_id


def ownership(conn, phone):
    """{phone, owner, reason, since} or None when no system owns the phone yet."""
    row = conn.execute("select phone, owner, reason, since from wa_ownership where phone=?", (phone,)).fetchone()
    return dict(row) if row else None


def flip_to_us_for_campaign(conn, phone, campaign_id, since):
    """Hand ``phone`` to us for a campaign send, whatever owned it before (None = no record). -> the record
    before the flip. No commit: app/wa/luna/campaign.py commits it with the send claim."""
    prior = ownership(conn, phone)
    conn.execute(
        "insert into wa_ownership (phone, owner, reason, since) values (?,?,?,?) "
        "on conflict(phone) do update set owner=excluded.owner, reason=excluded.reason, since=excluded.since",
        (phone, "us", campaign_reason(campaign_id), since))
    return prior


def restore_after_campaign_failure(conn, phone, campaign_id, prior):
    """Meta rejected the campaign send: put ``prior`` (a record, or None = no record) back. -> 'restored', or
    'changed' when the row is no longer this campaign's flip (another explicit write happened meanwhile; left
    as it is). No commit: the sender commits it with the failed claim."""
    current = ownership(conn, phone)
    if current is None or current["owner"] != "us" or current["reason"] != campaign_reason(campaign_id):
        return "changed"
    if prior is None:
        conn.execute("delete from wa_ownership where phone=?", (phone,))
    else:
        conn.execute("update wa_ownership set owner=?, reason=?, since=? where phone=?",
                     (prior["owner"], prior["reason"], prior["since"], phone))
    return "restored"
