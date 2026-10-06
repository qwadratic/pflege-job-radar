"""Rehearse a conversation turn by turn, sending nothing (Ivan, 2026-09-23).

    python tools/wa_rehearse.py open  --phone +43... --name Valy
    python tools/wa_rehearse.py say   --phone +43... --text "Passau"
    python tools/wa_rehearse.py show  --phone +43...
    python tools/wa_rehearse.py reset --phone +43...

WHY THIS AND NOT shadow_run. ``app/wa/luna/shadow_run.py`` answers "what would this thread get
next", once, from whatever the live database already holds. This answers a different question --
"what does the whole conversation look like if the candidate says THIS, then THIS" -- which is the
only way to read the funnel the way a candidate experiences it, before a single message reaches a
real person. Ivan, 2026-09-23: "прогони, чтоб я просто видел, какие вообще сообщения ты можешь мне
присылать... я буду отвечать."

NOTHING LEAVES THIS MACHINE. Two separate reasons, because one of them is not obvious:
  * the rail is never called -- this module has no bridge client and never reaches app/wa/api.py's
    send path, so no text message can go out;
  * WA_LUNA_NO_SEND is set before the brain runs, because show_clinic_photos
    (``app/wa/luna/tools_server.py``) is an MCP tool that sends by calling the phone rail from
    INSIDE the tools server -- the model reaching for it mid-rehearsal would otherwise put real
    photos in a real candidate's chat, which is exactly the hole this flag was added to close.

ITS OWN DATABASE. The rehearsal lives in ``data/wa_rehearsal.sqlite``, never the operational one:
a rehearsed card must not become the card a real turn then answers from. The card, the asked-list
and the brain's session id all accumulate there exactly as they would in production, so turn N+1
sees what turn N decided.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

#: Set before app.wa.config is imported anywhere: the brain's tools server reads it too.
os.environ["WA_LUNA_NO_SEND"] = "1"

from app.wa import config as C                                            # noqa: E402

REHEARSAL_DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "wa_rehearsal.sqlite"
C.SQLITE_PATH = REHEARSAL_DB

from app.wa import luna_brain as LB                                       # noqa: E402
from app.wa import store as ST                                            # noqa: E402

#: The approved first-touch template, verbatim (recruitment_bayern_stellen_interesse_de) -- header,
#: body with {{1}} filled, and the two quick replies. Rehearsing a paraphrase would rehearse a
#: conversation that cannot happen: this exact text is what a cold lead actually receives.
TEMPLATE_HEADER = "Neue Stellen in Bayern für Pflegekräfte"
TEMPLATE_BODY = ("Hallo, {name}. Sie haben sich als Pflegekraft in Bayern beworben. Aktuell haben "
                 "wir viele neue Stellen in Bayern. Haben Sie noch Interesse?")
TEMPLATE_BUTTONS = ["Ja, ich habe Interesse", "Nein, kein Interesse"]


def _show(conn, phone):
    for row in ST.history(conn, phone, limit=200):
        arrow = ">>" if row["direction"] == "out" else "<<"
        print(f"  {arrow} {row['body']}")


def cmd_open(args, conn):
    t = ST.thread(conn, args.phone)
    body = TEMPLATE_BODY.format(name=args.name)
    ST.record_outbound(conn, args.phone, f"rehearse.tpl.{args.phone}", body, kind="template")
    ST.save_thread(conn, t)
    print(f"[{TEMPLATE_HEADER}]")
    print(f"  >> {body}")
    print(f"  [buttons] {' | '.join(TEMPLATE_BUTTONS)}")
    return 0


def cmd_say(args, conn):
    t = ST.thread(conn, args.phone)
    n = len(ST.history(conn, args.phone, limit=200))
    ST.record_inbound(conn, args.phone, f"rehearse.in.{args.phone}.{n}", args.text)
    t["turn_context"] = LB.turn_context(conn, t, f"rehearse.in.{args.phone}.{n}")
    d = LB.turn(args.text, t, button_id=None, client=None)
    t["slots"], t["asked"] = d.get("slots", t["slots"]), d.get("asked", t["asked"])
    print(f"  << {args.text}")
    for i, bubble in enumerate(d.get("bubbles") or []):
        ST.record_outbound(conn, args.phone, f"rehearse.out.{args.phone}.{n}.{i}", bubble)
        print(f"  >> {bubble}")
    if d.get("buttons"):
        print(f"  [buttons] {' | '.join(str(b) for b in d['buttons'])}")
    ST.save_thread(conn, t)
    print(f"\n  action={d.get('action')}  stage-slots={ {k: v for k, v in t['slots'].items() if not k.startswith('_')} }")
    if d.get("stopped"):
        print("  (thread would stop here)")
    return 0


def cmd_show(args, conn):
    _show(conn, args.phone)
    t = ST.thread(conn, args.phone)
    print(f"\n  card: { {k: v for k, v in t['slots'].items() if not k.startswith('_')} }")
    print(f"  asked: {t['asked']}")
    return 0


def cmd_reset(args, conn):
    for table in ("wa_messages", "wa_threads"):
        conn.execute(f"delete from {table} where phone=?", (args.phone,))
    conn.commit()
    print(f"rehearsal for {args.phone} cleared")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="wa_rehearse", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("open", cmd_open), ("say", cmd_say), ("show", cmd_show), ("reset", cmd_reset)):
        s = sub.add_parser(name)
        s.add_argument("--phone", required=True)
        s.set_defaults(fn=fn)
        if name == "open":
            s.add_argument("--name", required=True)
        if name == "say":
            s.add_argument("--text", required=True)
    args = p.parse_args(argv)
    conn = ST.db()
    return args.fn(args, conn)


if __name__ == "__main__":
    raise SystemExit(main())
