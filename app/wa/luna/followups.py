"""Proactive follow-up nudges (TASK-85): the real production system re-engages a candidate who
went silent after our last message, at tiered intervals (15m/1h/4h, capped per streak) -- named
explicitly as something this harness lacked (docs/whatsapp.md, "no proactive re-engagement
messages"). This is the scaled-down equivalent: same tiered idea, a much simpler implementation --
no wake-fingerprint dedup, no quiet-hours window, fixed reviewable German nudge text rather than a
model call. An unprompted, system-initiated message is not what app.wa.luna_brain.turn()'s "the
candidate just said X" contract was built for, and a fixed, reviewable nudge is the safer choice
for something nobody asked the model to say.

A thread is eligible once: not stopped, reporting.ball_for()=='them' (we already answered, no
reply since), and enough time has passed since our last outbound message to cross a tier it has
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

from .. import api as API
from .. import config as C
from .. import store as ST
from . import reporting as REP

_EPOCH = "0001-01-01T00:00:00+00:00"


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
    Runs against the real, configured database, same as catchup.py -- no dry-run mode here."""
    results = []
    with ST._lock, ST.db() as c:
        targets = phones if phones is not None else ST.candidate_phones(c)
        for phone in targets:
            t = ST.thread(c, phone)
            if t["stopped"]:
                continue
            if REP.ball_for(c, phone) != "them":
                continue
            tier = _eligible_tier(c, phone, t.get("last_outbound_at"), t.get("last_inbound_at"))
            if tier is None:
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
    results = run(phones=phones)
    print(f"{len(results)} nudge(s) sent")
    for r in results:
        print(f"  {r['phone']}: tier {r['tier']} -- {r['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
