"""Mark a phone as a test number (TASK-109): the operator's own number, used to test the live harness by hand.

Usage:
    python -m app.wa.luna.test_threads --list
    python -m app.wa.luna.test_threads --mark +4915550000001
    python -m app.wa.luna.test_threads --unmark +4915550000001

Load .env first (set -a; . ./.env; set +a) so this writes the same database the service reads.

The flag is ``wa_threads.is_test`` (app/wa/store.py), so it survives a restart and every reader sees it:

- the campaign sender never sends to it, in the plan and in --send (campaign.decide -> skip_test_number),
- candidate reports and the consent-queue views leave it out (reporting.report_row, shadow_run, GET /api/wa/queue),
- GET /api/wa/threads shows ``is_test`` per row plus a ``test_threads`` count,
- app/wa/luna/purge_test_history.py wipes its history on a timer, so the next manual test starts from nothing.

What does NOT change: the conversation. A test thread is answered by the webhook, catch-up and the follow-up
nudger exactly like a real lead -- that is the whole point of testing with it.

--mark creates the thread row when the number has never written, so a number can be marked before the first
test message instead of after it. Nothing in this repo marks a number by itself: an operator runs this.
"""
import argparse
import sys

from .. import meta as M
from .. import store as ST

MIN_PHONE_DIGITS = 8  # as migrate_candidates.py: canonicalize_phone("not a number") -> "+49"


def canonical_phone(raw):
    """-> +E.164. Raises on anything that does not canonicalize to a real number: a typo must not
    silently mark (or leave unmarked) a different number than the operator meant."""
    canon = M.canonicalize_phone(str(raw))
    if not canon or len(canon.lstrip("+")) < MIN_PHONE_DIGITS:
        raise ValueError(f"phone did not canonicalize to a real number: {raw!r} -> {canon!r}")
    return canon


def mark(phone, is_test):
    """-> the thread row after the change (``is_test``, ``test_marked_at``)."""
    with ST._lock, ST.db() as c:
        return ST.mark_test_thread(c, canonical_phone(phone), is_test)


def listing():
    """-> [{phone, test_marked_at, opened_at, turns, stopped}] for every marked number."""
    with ST.db() as c:
        return [{k: t[k] for k in ("phone", "test_marked_at", "opened_at", "turns", "stopped")}
                for t in (ST.thread(c, p) for p in ST.test_phones(c))]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--mark", metavar="PHONE", help="mark this number as a test number")
    what.add_argument("--unmark", metavar="PHONE", help="make it an ordinary thread again")
    what.add_argument("--list", action="store_true", help="every number currently marked as a test number")
    args = ap.parse_args(argv)

    if args.list:
        rows = listing()
        print(f"{len(rows)} test number(s)")
        for r in rows:
            print(f"  {r['phone']}: marked {r['test_marked_at']}, thread opened {r['opened_at']}, "
                  f"{r['turns']} turn(s){', stopped' if r['stopped'] else ''}")
        return 0

    phone, is_test = (args.mark, True) if args.mark else (args.unmark, False)
    try:
        t = mark(phone, is_test)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"{t['phone']}: is_test={t['is_test']}"
          + (f", marked {t['test_marked_at']}" if t["is_test"] else "")
          + f" (thread opened {t['opened_at']}, {t['turns']} turn(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
