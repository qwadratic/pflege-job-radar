"""Dry-run shadow report (TASK-72): "what would the agent say next, without sending" -- the same
safety contract the real production team already built and uses for exactly this purpose
(``wa_shadow_run.py`` on tasker-dispatcher-01): report the proposed next reply for every thread
that is owed one, always against a copy of the operational database, and never call WhatsApp send
-- regardless of what ``WA_AUTOSEND`` is set to.

"Owed a reply" is ``reporting.ball_for(conn, phone) == "us"``: the candidate's last message has no
reply behind it yet. In this harness that is a normal, momentary state during a real webhook call
(the reply is computed and sent synchronously, in the same request) -- a thread stuck this way in
the live database means something failed partway through a prior turn, which is exactly the kind
of situation a human wants a safe way to inspect before manually intervening.

One safety point this module owns beyond "never call Meta send": WA_BRAIN=luna threads carry a
persistent, resumable Claude Code session id on the card (``_session_id``, see luna_brain.py's
module docstring). Resuming that real session from a dry run would durably append this shadow
turn to shared external session state that the live webhook resumes from on the next real
message -- an irreversible side effect a report-only tool must never risk. shadow_turn() always
strips ``_session_id`` before calling the brain, so a luna reply here is always generated from a
fresh, throwaway session. The card/slots state -- what actually drives the gates and the matching
-- is identical to production; only the model's in-session conversational memory is not replayed,
so the exact wording may read a little colder than the live thread's real reply would. The
decision structure (gate, action, whether it would send at all) is not affected by that gap.
"""
import argparse
import json
import pathlib
import sqlite3
from contextlib import contextmanager

from .. import config as C
from .. import store as ST
from . import reporting as REP


@contextmanager
def db_copy(db_path=None):
    """The live database, opened strictly read-only (SQLite URI ``mode=ro`` -- refuses to write,
    and refuses to even create the file if it does not exist yet, unlike a plain ``connect()``),
    backed up via SQLite's own online-backup API into a fresh in-memory database. Everything from
    here on reads and writes only that in-memory copy; the source connection is closed the moment
    the backup finishes."""
    src_path = db_path or C.SQLITE_PATH
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(":memory:")
    dst.row_factory = sqlite3.Row
    try:
        src.backup(dst)
    finally:
        src.close()
    try:
        yield dst
    finally:
        dst.close()


def _last_inbound(conn, phone):
    row = conn.execute(
        "select wamid, body, meta from wa_messages where phone=? and direction='in' order by id desc limit 1",
        (phone,)).fetchone()
    if row is None:
        return None, "", None
    return row["wamid"], row["body"], json.loads(row["meta"] or "{}").get("button_id")


def phones_owed_a_reply(conn):
    """Every phone whose most recent message (by id, across the whole thread) is inbound and has no
    recorded no_send (ST.NO_SEND_STATE, TASK-101) -- one query, not N -- the same condition
    reporting.ball_for() == "us" checks per-phone."""
    rows = conn.execute("""
        select m.phone from wa_messages m
        join (select phone, max(id) as last_id from wa_messages group by phone) latest
          on m.phone = latest.phone and m.id = latest.last_id
        left join wa_reply_turn_claims k on k.phone = m.phone and k.turn_key = m.wamid
        where m.direction = 'in' and (k.state is null or k.state != ?)
    """, (ST.NO_SEND_STATE,)).fetchall()
    return [r["phone"] for r in rows]


def shadow_turn(conn, phone, client=None):
    """-> a report row for one thread, or None if it does not exist in this copy at all. Reads
    the copy; never writes to it (a dry run over a dry run's own dry run is still not a write
    anyone asked for) and never touches the real database this copy came from."""
    exists = conn.execute("select 1 from wa_threads where phone=?", (phone,)).fetchone()
    if exists is None:
        return None
    t = ST.thread(conn, phone)
    if t["stopped"]:
        return {"phone": phone, "stage": REP.stage_for(t["slots"]), "action": "stopped",
                "bubbles": [], "buttons": [], "would_stop": True, "window_open": None, "gate": "stopped"}

    wamid, text, button_id = _last_inbound(conn, phone)
    if C.BRAIN == "luna":
        from .. import luna_brain as LB
        if wamid:
            t["turn_context"] = LB.turn_context(conn, t, wamid)   # TASK-100, before the session id is stripped
        t["slots"] = {k: v for k, v in t["slots"].items() if k != "_session_id"}
        d = LB.turn(text, t, button_id=button_id, client=client)
    else:
        from .. import brain as B
        from ..api import TEMPLATE_BUTTON_PREFIX   # a template tap is read as its label, as api.process_owed_turn
        d = B.turn(text, t, button_id=None if str(button_id or "").startswith(TEMPLATE_BUTTON_PREFIX) else button_id)

    from .. import api as API           # imported lazily: only needed for the window gate check
    window_open = API._freeform_window_open(t)
    if not d["bubbles"]:
        gate = "no_send"
    elif not window_open:
        gate = "reopen_template" if C.WA_REOPEN_TEMPLATE_NAME else "reopen_template_missing"
    else:
        gate = "freeform"

    return {"phone": phone, "stage": REP.stage_for(t["slots"]), "action": d["action"],
            "bubbles": d["bubbles"], "buttons": d["buttons"], "would_stop": d["stopped"],
            "window_open": window_open, "gate": gate}


def run(db_path=None, phones=None, client=None):
    """The full dry run: back up the database (db_copy), find every thread owed a reply (or just
    ``phones``, when given), report what each would get. -> a list of shadow_turn() rows (None
    results, for a requested phone this copy has no thread for at all, are dropped)."""
    with db_copy(db_path) as conn:
        targets = phones if phones is not None else phones_owed_a_reply(conn)
        return [row for row in (shadow_turn(conn, p, client=client) for p in targets) if row is not None]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="path to the wa sqlite file to copy (defaults to the configured one)")
    ap.add_argument("--phones", help="comma-separated phones to check, instead of every thread owed a reply")
    ap.add_argument("--json", action="store_true", help="print the raw JSON report instead of a summary")
    args = ap.parse_args(argv)

    phones = [p.strip() for p in args.phones.split(",") if p.strip()] if args.phones else None
    rows = run(db_path=pathlib.Path(args.db) if args.db else None, phones=phones)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{len(rows)} thread(s) owed a reply")
    for row in rows:
        note = "" if row["bubbles"] else "  (nothing to send)"
        print(f"  {row['phone']}: [{row['stage']}] {row['action']} -- gate={row['gate']}{note}")
        for b in row["bubbles"]:
            print(f"      -> {b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
