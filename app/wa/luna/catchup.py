"""Catch-up driver (TASK-78): retries an owed reply the webhook never got to -- either because no
webhook call ever arrived (Meta delivery is not guaranteed) or because a prior attempt failed
before an outbound message was recorded. This harness was purely synchronous before this task
(one webhook call in, one reply attempt out, nothing revisits a thread afterward) -- unlike the
real production system's own 3-minute --catchup poller, whose own --help text says webhook wake
"may fail" and treats the poller as the resilient primary path, not a backup.

Unlike app/wa/luna/shadow_run.py (TASK-72: always a read-only copy, never sends), this is the live
counterpart: it runs against the REAL database and, with WA_AUTOSEND on, actually calls Meta. Both
tools intentionally share phones_owed_a_reply() (reporting.ball_for() == "us") -- one owed-reply
definition, not two.

Goes through the exact same app.wa.api.process_owed_turn() the webhook path uses, keyed on the
same turn_key (the owed message's own wamid) -- so a race between a webhook call and a catch-up
pass for the same thread is resolved by TASK-77's reply-turn claim, never a double reply, and the
same TASK-76 rate cap and TASK-79 failure recording apply here too, not a second, divergent set of
rules.

Known scope limit, named rather than silently patched over: this only retries the BRAIN-decided
reply path. A stuck flat media acknowledgment (MEDIA_REPLY, sent outside process_owed_turn for
audio/video or a non-luna thread) is not retried here -- a real but narrow gap for a later task,
not something this one invents a second send path to cover.

Usage: ``python -m app.wa.luna.catchup [--phones p1,p2,...]``. No systemd timer is installed as
part of this task -- when and how often to run this is an operational decision for whoever
deploys this harness for real, not something to hardcode here.
"""
import argparse
import json

from .. import api as API
from .. import store as ST
from . import shadow_run as SR


def _last_inbound(conn, phone):
    row = conn.execute(
        "select body, wamid, meta from wa_messages where phone=? and direction='in' order by id desc limit 1",
        (phone,)).fetchone()
    if row is None:
        return None
    return {"text": row["body"], "wamid": row["wamid"], "button_id": json.loads(row["meta"] or "{}").get("button_id")}


def run(client=None, phones=None):
    """Attempts a real reply for every thread owed one (or just ``phones``, when given). ->
    a list of {"phone": ..., **process_owed_turn() result}. Runs against the real, configured
    database -- there is no dry-run mode here, that is shadow_run.py's job."""
    results = []
    with ST._lock, ST.db() as c:
        targets = phones if phones is not None else SR.phones_owed_a_reply(c)
        for phone in targets:
            inbound = _last_inbound(c, phone)
            if inbound is None:
                continue
            t = ST.thread(c, phone)
            if t["stopped"]:
                continue
            result = API.process_owed_turn(c, t, inbound["text"], inbound["button_id"],
                                           inbound["wamid"], client=client)
            ST.save_thread(c, t)
            results.append({"phone": phone, **result})
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phones", help="comma-separated phones to retry, instead of every owed thread")
    args = ap.parse_args(argv)

    phones = [p.strip() for p in args.phones.split(",") if p.strip()] if args.phones else None
    results = run(phones=phones)
    print(f"{len(results)} thread(s) attempted")
    for r in results:
        print(f"  {r['phone']}: {r['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
