"""Proactive follow-up nudges (TASK-85): the real production system re-engages a candidate who
went silent after our last message, at tiered intervals (15m/1h/4h, capped per streak) -- named
explicitly as something this harness lacked (docs/whatsapp.md, "no proactive re-engagement
messages"). This is the scaled-down equivalent: same tiered idea, a much simpler implementation --
fixed reviewable German nudge text rather than a model call. An unprompted, system-initiated
message is not what app.wa.luna_brain.turn()'s "the candidate just said X" contract was built for,
and a fixed, reviewable nudge is the safer choice for something nobody asked the model to say. A
quiet-hours window (TASK-92) and a durable cross-process dedup claim (TASK-93, ST.claim_nudge) ARE
ported, though: see _in_quiet_hours and run() below.

A thread is eligible once: not stopped, not finished (reporting.stage_for() not in TERMINAL_STAGES --
TASK-94, TASK-101), reporting.ball_for()=='them' (we already answered, no reply since; a recorded no_send
is 'silent', not 'them'), the candidate wrote at least once (TASK-101) and after the latest campaign template
on the card (TASK-103), their last message is not unread media (card._unread_media: a voice note got the
MEDIA_REPLY promise that a colleague looks at it), and enough time has passed since our last outbound message to cross a tier it has
not already gotten a nudge for in this streak. A streak resets the moment the candidate replies --
derived from how many nudges have been sent since their own last message, not a separate counter
column that could drift out of sync with reality.

Sends go through the exact same app.wa.api.send_and_record() the webhook/catch-up paths use, so
the TASK-70 24h-window/reopen-template gate applies here too -- not a second copy of that logic.

Usage: ``python -m app.wa.luna.followups [--phones p1,p2,...]``. No systemd timer is installed as
part of this task -- deploy cadence is an operational decision, out of scope here.
"""
import argparse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .. import api as API
from .. import config as C
from .. import store as ST
from . import reporting as REP

_EPOCH = "0001-01-01T00:00:00+00:00"

# TASK-94: stages where the conversation is over from the candidate's side -- consent given (a
# human takes it from here) or told they are not placeable. Such a thread always ends with our own
# message, so ball_for()=='them' alone would nudge it; found live on a consented thread that got
# "sind Sie noch da?" twice the next morning. TASK-101: declined (after the one fixed ack) and
# already placed without new interest (Ivan 2026-09-14: silence after a decline).
TERMINAL_STAGES = ("consented", "not_placeable", "declined", "already_placed")


def _in_quiet_hours(now=None):
    """True if `now` (defaults to the real current time) falls inside the configured local
    quiet-hours window (TASK-92). A global time check, not per-candidate -- this board has no
    per-candidate timezone data, so one fixed timezone (C.QUIET_HOURS_TZ) stands in for all of
    them. Handles a window that wraps past midnight (START > END, e.g. 21 -> 9); START == END is
    a zero-width window, read as 'no quiet hours configured' (disabled) rather than 'quiet all
    day', so a config typo that sets both to the same value fails open (nudges keep sending, the
    pre-TASK-92 behavior) instead of silently and permanently suppressing every nudge."""
    now = now or datetime.now(timezone.utc)
    hour = now.astimezone(ZoneInfo(C.QUIET_HOURS_TZ)).hour
    start, end = C.QUIET_HOURS_START, C.QUIET_HOURS_END
    if start < end:
        return start <= hour < end
    if start > end:
        return hour >= start or hour < end
    return False


def _eligible_tier(conn, phone, last_outbound_at, last_inbound_at):
    """-> the next tier index to nudge at, or None. Tiers fire strictly in order, one at a time --
    even if several tiers' worth of silence has passed since the last check, only the next
    not-yet-sent tier in the sequence is returned."""
    if not last_outbound_at:
        return None
    since = last_inbound_at or _EPOCH
    sent = ST.followup_tiers_sent_since(conn, phone, since)
    if len(sent) >= C.MAX_FOLLOWUPS_PER_STREAK:
        return None
    next_tier = len(sent)
    if next_tier >= len(C.FOLLOWUP_TIER_MINUTES):
        return None
    age_minutes = (datetime.now(timezone.utc) - datetime.fromisoformat(last_outbound_at)).total_seconds() / 60
    return next_tier if age_minutes >= C.FOLLOWUP_TIER_MINUTES[next_tier] else None


def run(client=None, phones=None):
    """-> a list of {"phone": ..., "tier": ..., "status": ...} for every thread actually nudged.
    Runs against the real, configured database, same as catchup.py -- no dry-run mode here.
    [] immediately, with no thread even looked at, during quiet hours (TASK-92) -- a nudge due
    during that window is not lost, just delayed: _eligible_tier is driven by elapsed time since
    last_outbound_at, so the next 15-min timer tick outside the window finds the same tier still
    due and sends it then. Every send is additionally gated on ST.claim_nudge (TASK-93) so a
    second, overlapping invocation of this same sweep (or, in future, a different campaign trigger
    deciding to message the same candidate) cannot both send -- the in-process ST._lock alone
    would not stop that across two separate processes."""
    if _in_quiet_hours():
        return []
    results = []
    with ST._lock, ST.db() as c:
        targets = phones if phones is not None else ST.candidate_phones(c)
        for phone in targets:
            t = ST.thread(c, phone)
            if t["stopped"]:
                continue
            if REP.stage_for(t["slots"]) in TERMINAL_STAGES:
                continue
            if REP.ball_for(c, phone) != "them":
                continue
            if not ST.has_inbound(c, phone):
                # TASK-101: never wrote (a campaign recipient who did not reply) -- no free text, nothing
                # further (Ivan 2026-09-14).
                continue
            campaign = t["slots"].get("campaign") or {}
            if campaign.get("sent_at") and (t.get("last_inbound_at") or "") < campaign["sent_at"]:
                # TASK-103: no reply since our campaign template (the candidate wrote only before it) -- a
                # non-responder, nothing further.
                continue
            last_in = ST.last_inbound(c, phone)
            if any(u["wamid"] == last_in["wamid"] for u in t["slots"].get(API.UNREAD_MEDIA_KEY, [])):
                # Their last message is a voice note/video nobody here can read; MEDIA_REPLY promised a colleague
                # looks at it. Not silence: no nudge (review 2026-09-14).
                continue
            tier = _eligible_tier(c, phone, t.get("last_outbound_at"), t.get("last_inbound_at"))
            if tier is None:
                continue
            # TASK-93: the streak anchor (same value _eligible_tier's own "since" uses) is part of
            # the fingerprint so a later, legitimate streak reusing this same tier index is never
            # falsely blocked by an earlier claim.
            since = t.get("last_inbound_at") or _EPOCH
            if not ST.claim_nudge(c, phone, f"followup:{tier}:{since}"):
                continue
            sent = API.send_and_record(c, t, [C.FOLLOWUP_NUDGE_DE], [], client=client, action="followup")
            if sent != "nothing_to_send":
                ST.record_followup_sent(c, phone, tier)
            ST.save_thread(c, t)
            results.append({"phone": phone, "tier": tier, "status": sent})
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phones", help="comma-separated phones to check, instead of every open thread")
    args = ap.parse_args(argv)

    phones = [p.strip() for p in args.phones.split(",") if p.strip()] if args.phones else None
    if _in_quiet_hours():
        print(f"0 nudge(s) sent (quiet hours: {C.QUIET_HOURS_START}:00-{C.QUIET_HOURS_END}:00 {C.QUIET_HOURS_TZ})")
        return 0
    results = run(phones=phones)
    print(f"{len(results)} nudge(s) sent")
    for r in results:
        print(f"  {r['phone']}: tier {r['tier']} -- {r['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
