"""The other half of the operator inbox (Ivan, 2026-09-24): the worker's view of wa_agent_notes.

The gate (app/wa/luna/agent_note_gate.py, called from api.finish_inbound) decides and records; it
implements nothing. This module is what the periodic worker drives, and its only input is a row in
wa_agent_notes -- never a live scan of wa_messages, never a WhatsApp chat, never a language rule.
That split is the design: the component that reads an operator's text runs `claude -p --restricted
--tools ""` and can emit one boolean, and the component that can change code and restart services
takes its input from a schema-bounded internal queue with explicit states and a claim.

WHY A CLI AND NOT FREE-HAND COMMANDS. The completion note Ivan asked for is ONE format -- what was
done, what was not, what is still needed -- and a format typed by hand drifts on the second tick. So
the three fields are arguments, the template lives here, and the worker cannot send the note any other
way. ``--done``/``--blocked`` are the only things that send it, exactly once per note.

THE TURN KEY IS NEVER THE BARE WAMID, AND THAT IS LOAD-BEARING. app/wa/bridge_ids.reply_key hashes
``phone|turn_key|bubble_index`` and deliberately leaves ``action`` out of the material (TASK-245), so
reusing the note's inbound wamid HERE, BARE, would mint the same client_msg_id as the ack that already
went out (app/wa/api.py._route_agent_note) -- the executor's ledger would replay it and the completion
note would be silently swallowed while this row read "done".

THE KEY IS NOW BUILT FROM THE WAMID, NOT THE NOTE'S DATABASE ID (TASK-303, Ivan 2026-09-25, round-1
review finding A3 -- a course change from this file's own earlier ``agent_note:<id>:done``). The id is
a local sequence value; even with AUTOINCREMENT (app/wa/store.py's own migration, so an id is never
reused once this fix ships), the wamid is the one thing that was ALREADY guaranteed unique to this
exact inbound message forever, assigned by WhatsApp itself, independent of anything this app's own id
sequence or purge policy (app/wa/luna/purge_test_history.py, which used to empty this very table
nightly) ever do -- and the bridge's own ledger keeps a key for 30 days regardless (bridge/ledger.py,
LEDGER_RETENTION_DAYS), so a key that could theoretically repeat inside that window is a real replay
risk, not a theoretical one. Hence ``agent_note:<wamid>:done``: still a different STRING from the bare
wamid (the collision above) and from the ack's own ``agent_note:<wamid>:ack``
(app/wa/api.py._route_agent_note) -- both keys differ from each other by their fixed suffix alone, and
both differ from anything the candidate reply path could ever mint, because neither is ever the bare
wamid a candidate turn would key bubble 0 on. Still deterministic, so a retry of a send that genuinely
failed replays rather than duplicates.

Usage (all read WA_* config the same way the service does):
    python -m app.wa.luna.agent_notes --list [--json]
    python -m app.wa.luna.agent_notes --handed-off [--json]
    python -m app.wa.luna.agent_notes --claim <id>
    python -m app.wa.luna.agent_notes --progress <id> --text "..."
    python -m app.wa.luna.agent_notes --done <id> --done-text "..." --not-done-text "..." --needed-text "..."
    python -m app.wa.luna.agent_notes --blocked <id> --reason "..." --needed-text "..."

ENV (TASK-303, Ivan 2026-09-25). This CLI's --done/--blocked ultimately call app.wa.api.send_and_record,
which reads WA_AUTOSEND/META_WHATSAPP_ACCESS_TOKEN/etc off app.wa.config -- module-level constants
computed once, from whatever os.environ held the first time that module was imported in this process.
Run from a plain shell with nothing sourced, those all read as unconfigured, and _send_completion below
raises rather than silently record a message nobody received (see its own docstring). So, like
app/wa/luna/agent_note_worker.py, this file loads the live service's two EnvironmentFiles
(app/wa/envfile.py) from behind ``if __name__ == "__main__":`` below, BEFORE the app.wa.api import a
few lines down -- not merely at the top of main(), which would already be too late for those frozen
constants. See envfile.py's own docstring for why the import-time guard is the part that matters.
"""
import argparse
import json
import sys

if __name__ == "__main__":
    from .. import envfile as _ENV
    _ENV.load_service_env_files()

from .. import api as WAPI
from .. import bridge as BR
from .. import store as ST

# One template, both outcomes. Every line is always present -- an empty field is written as the dash,
# never dropped -- so the shape a reader learns on the first note is the shape of every later one.
DONE_WORD = "готово"
BLOCKED_WORD = "заблокировано"
EMPTY = "—"
FIELD_MAX = 220          # the detail belongs in the session report, not in a WhatsApp message


def _field(text):
    text = (text or "").strip() or EMPTY
    return text if len(text) <= FIELD_MAX else text[:FIELD_MAX - 1].rstrip() + "…"


def completion_note(note_id, word, done, not_done, needed):
    return (f"[агент] Заметка #{note_id} — {word}\n"
            f"Сделано: {_field(done)}\n"
            f"Не сделано: {_field(not_done)}\n"
            f"Нужно: {_field(needed)}")


def _send_completion(c, note_id, word, done, not_done, needed):
    """Sends the note's one completion message. -> "sent" | "already_notified".

    The conditional mark comes first so two workers that both reached the end of the same note still
    send once; a send that then raises clears it again, so a genuine failure stays retryable rather
    than burning the single delivery."""
    if not ST.mark_agent_note_notified(c, note_id):
        return "already_notified"
    row = ST.agent_note(c, note_id)
    t = ST.thread(c, row["phone"])
    try:
        status = WAPI.send_and_record(c, t, [completion_note(note_id, word, done, not_done, needed)], [],
                                      action="agent_note_done", turn_key=f"agent_note:{row['wamid']}:done",
                                      origin=BR.ORIGIN_AGENT_NOTES)
        if status != "sent":
            # "draft" (WA_AUTOSEND unset -- the ORIGINAL failure this hit, TASK-303: this CLI ran from
            # a plain shell with none of the service's env loaded; fixed by the __main__ guard above,
            # but still the shape of failure a caller who bypasses that guard hits), or a reopen
            # template instead of the note. Anything but a real send has to raise, or the row keeps
            # notified_at, the one completion is spent on a message nobody received, and the promise
            # in the ack is silently broken.
            raise RuntimeError(
                f"the completion note for #{note_id} was not sent ({status!r}) -- if this ran outside "
                f"`python -m app.wa.luna.agent_notes` or the worker (agent_note_worker.py), the live "
                f"service's env never loaded: those two load it themselves (app/wa/envfile.py) before "
                f"app.wa.config is first imported, which a plain `set -a; . ./.env` does NOT achieve "
                f"reliably here -- .env's own KNOWN_PHONES_SOURCE_QUERY line is not valid shell (see "
                f"envfile.py's docstring) -- so import and call ST/WAPI from a process that ran this "
                f"module's own env loading first, rather than sourcing .env by hand")
    except Exception:
        ST.clear_agent_note_notified(c, note_id)
        raise
    # Re-read rather than writing back the snapshot taken before a send that can take minutes: the
    # service is writing this same row while a burst is being answered, and save_thread rewrites every
    # column -- the stale copy would silently rewind a slot, a stop, or a newer last_inbound_at.
    fresh = ST.thread(c, row["phone"])
    fresh["last_outbound_at"] = t["last_outbound_at"]
    ST.save_thread(c, fresh)
    return "sent"


def _row_view(row):
    return {"id": row["id"], "phone": row["phone"], "kind": row["kind"], "status": row["status"],
            "created_at": row["created_at"], "attempts": row["attempts"], "body": row["body"],
            "progress": row["progress"], "task_id": row["task_id"]}


def main(argv=None):
    from .. import envfile as ENV
    ENV.load_service_env_files()   # redundant with the __main__ guard above; explicit here too, and
                                    # what a caller that reaches main() programmatically still gets
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--list", action="store_true", help="notes a worker may take, oldest first")
    p.add_argument("--handed-off", action="store_true",
                   help="notes handed off to this session and not yet closed (TASK-303)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--claim", type=int, metavar="ID")
    p.add_argument("--progress", type=int, metavar="ID")
    p.add_argument("--text", help="the progress line to append")
    p.add_argument("--done", type=int, metavar="ID")
    p.add_argument("--blocked", type=int, metavar="ID")
    p.add_argument("--done-text", default="")
    p.add_argument("--not-done-text", default="")
    p.add_argument("--needed-text", default="")
    p.add_argument("--reason", default="", help="why it is blocked; goes in the 'Не сделано' line")
    a = p.parse_args(argv)

    with ST.db() as c:
        if a.list:
            rows = [_row_view(r) for r in ST.open_agent_notes(c)]
            if a.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            elif not rows:
                print("no open notes")
            else:
                for r in rows:
                    print(f"#{r['id']} {r['phone']} {r['status']} attempts={r['attempts']} "
                          f"{r['created_at']}\n  {r['body'][:300]}")
            return 0
        if a.handed_off:
            rows = [_row_view(r) for r in ST.handed_off_agent_notes(c)]
            if a.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            elif not rows:
                print("no notes handed off")
            else:
                for r in rows:
                    print(f"#{r['id']} {r['phone']} card={r['task_id'] or '-'} {r['created_at']}\n"
                          f"  {r['body'][:300]}")
            return 0
        if a.claim is not None:
            won = ST.claim_agent_note(c, a.claim)
            print("claimed" if won else "not claimable (another worker holds it, or it is finished)")
            return 0 if won else 1
        if a.progress is not None:
            if not a.text:
                p.error("--progress needs --text")
            ST.append_agent_note_progress(c, a.progress, a.text)
            print("recorded")
            return 0
        if a.done is not None:
            ST.finish_agent_note(c, a.done, "done", a.done_text, a.not_done_text, a.needed_text)
            print(_send_completion(c, a.done, DONE_WORD, a.done_text, a.not_done_text, a.needed_text))
            return 0
        if a.blocked is not None:
            if not a.reason:
                p.error("--blocked needs --reason")
            ST.finish_agent_note(c, a.blocked, "blocked", a.done_text, a.reason, a.needed_text,
                                 blocked_reason=a.reason)
            print(_send_completion(c, a.blocked, BLOCKED_WORD, a.done_text, a.reason, a.needed_text))
            return 0
    p.error("nothing to do: pass --list, --handed-off, --claim, --progress, --done or --blocked")


if __name__ == "__main__":
    sys.exit(main())
