"""Catch-up driver (TASK-78, TASK-99): finishes inbound messages the webhook did not. Meta delivery is not
guaranteed, the webhook's background worker can fail or be killed mid-turn, and a rate-capped turn waits
for the next hour -- this is the resilient second path, like the real production system's own 3-minute
--catchup poller.

Two passes, one pipeline: app.wa.api.finish_inbound, the same the webhook's background worker runs.
1. pending: every phone with wa_inbound_pending rows (recorded by a webhook, not finished yet) is drained
   oldest first (API.process_phones). Media included: a message with no stored original is downloaded again
   with the media_id kept in wa_messages.meta, a stored original that never reached a luna card is re-read
   and classified, a luna voice note without a transcript is transcribed from its stored original (TASK-107),
   media nothing reads gets its flat ack. A phone with a claim in flight (the webhook worker,
   or a still-running earlier pass, is on it) is reported ``claimed_elsewhere`` and left to that process --
   the ``media:<wamid>`` claim covers a running download/ingest, so this never replies to a file it has not
   read.
2. owed: every other thread whose last message is inbound (shadow_run.phones_owed_a_reply with
   include_test=True -- only the report leaves a test number out, TASK-109 -- the same
   reporting.ball_for() == "us" rule, or just ``--phones``) and not stopped: its last inbound message goes
   through finish_inbound once. Covers messages recorded before wa_inbound_pending existed.

Same reply-turn claims (TASK-77), rate cap (TASK-76) and failure recording (TASK-79) as the webhook, and a
consent reached here builds its queue entry the same way (API.build_consent_queues). A failing message is
logged, recorded (pending row, wa_send_failures once per distinct error), reported as status ``error``, and
the pass goes on; main() exits 1 when any message failed.

Runs against the REAL database and, with WA_AUTOSEND on, really calls Meta -- shadow_run.py is the dry run.
Usage: ``python -m app.wa.luna.catchup [--phones p1,p2,...]``; deploy/pflege-wa-catchup.timer runs it every
3 minutes.
"""
import argparse
import logging

from .. import api as API
from .. import store as ST
from . import shadow_run as SR

log = logging.getLogger(__name__)


def _last_inbound(conn, phone):
    return conn.execute("select * from wa_messages where phone=? and direction='in' order by id desc limit 1",
                        (phone,)).fetchone()


def run(client=None, phones=None):
    """Finishes every pending inbound message, then every owed thread's last inbound message (or only those of
    ``phones``). -> a list of {"phone", "wamid", "status", ...} -- finish_inbound's result, or ``error``.
    The thread is saved only when a turn ran (API.TURN_NOT_RUN), so a skipped pass never writes an old copy
    over the webhook's."""
    results = []
    with ST.db() as c:
        pending = [p for p in ST.phones_with_pending_inbound(c) if phones is None or p in phones]
    for phone in pending:
        try:
            results.extend({"phone": phone, **r}
                           for r in API.process_phones([phone], client=client, raise_errors=False))
        except Exception as exc:
            results.append({"phone": phone, **_failed(phone, None, exc)})

    owed = []
    with ST._lock, ST.db() as c:
        # include_test: a test number (TASK-109) is answered like every other thread -- only reports leave it out.
        targets = phones if phones is not None else SR.phones_owed_a_reply(c, include_test=True)
        for phone in targets:
            if phone in pending or ST.pending_inbound(c, phone):
                continue
            row = _last_inbound(c, phone)
            if row is None or ST.thread(c, phone)["stopped"]:
                continue
            try:
                owed.append({"phone": phone, **API.finish_inbound(c, API.message_from_row(row), client=client)})
            except Exception as exc:
                owed.append({"phone": phone, **_failed(phone, row["wamid"], exc, c)})
    API.build_consent_queues(owed, raise_errors=False)
    return results + owed


def _failed(phone, wamid, exc, c=None):
    """Log and record one failure (once per distinct error). -> the result fields."""
    error = f"catch-up {wamid or phone} not finished: {type(exc).__name__}: {exc}"
    log.error(error, exc_info=exc)
    if c is None:
        with ST.db() as conn:
            _record_once(conn, phone, error)
    else:
        _record_once(c, phone, error)
    return {"wamid": wamid, "status": "error", "error": error}


def _record_once(c, phone, error):
    latest = ST.recent_send_failure(c, phone)
    if not latest or latest["error"] != error:
        ST.record_send_failure(c, phone, error)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phones", help="comma-separated phones to retry, instead of every owed thread")
    args = ap.parse_args(argv)

    phones = [p.strip() for p in args.phones.split(",") if p.strip()] if args.phones else None
    results = run(phones=phones)
    print(f"{len(results)} message(s) attempted")
    for r in results:
        print(f"  {r['phone']} {r.get('wamid')}: {r['status']}" + (f" -- {r['error']}" if r.get("error") else ""))
    return 1 if any(r["status"] == "error" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
