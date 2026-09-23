"""The handset operations as one-step commands (TASK-147): send, broadcast, read, list, clear, delete.

Usage:
    python tools/wa_bridge.py health
    python tools/wa_bridge.py chats [--json]
    python tools/wa_bridge.py read (--phone +49... | --title "Name") [--archived] [--no-text] [--json]
    python tools/wa_bridge.py send --to +49... (--body TEXT | --body-file F) [--key LABEL] [--attempt N] [--dry-run]
    python tools/wa_bridge.py broadcast --id RUN --file RECIPIENTS.csv|.json [--body TEXT | --body-file F]
        [--attempt N] [--pacing JSON] [--note TEXT] [--send]
    python tools/wa_bridge.py broadcast --id RUN (--status | --stop) | --runs
    python tools/wa_bridge.py clear-chat  (--phone +49... | --title "Name") [--archived]
        [--confirm [--expect-messages N] [--keep-starred]]
    python tools/wa_bridge.py delete-chat (--phone +49... | --title "Name") [--archived]
        [--confirm [--expect-messages N]]
    python tools/wa_bridge.py audit [--title "Name"] [--limit N] [--json]
    python tools/wa_bridge.py media-list [--json]
    python tools/wa_bridge.py media-attach --id wab.q.xxxxxxxxxxxxxxxxxxxx --phone +49...

Load .env first (set -a; . ./.env; set +a): WA_BRIDGE_URL, WA_BRIDGE_TOKEN, WA_AUTOSEND.

WHY THIS EXISTS. Ivan, 2026-09-21: these operations must be code on the mini that a model or an
operator calls in one step, not an adb sequence rewritten by hand every time. Every command here is
one call into ``app/wa/bridge.Client``, which is one HTTP call to the executor on the handset
machine, which owns the phone, the flock, the ledger and the governor. Nothing about the phone is
decided in this file.

KEYS, AND WHY A RE-RUN IS SAFE. Every outbound message carries a ``client_msg_id`` this side mints
deterministically (``app/wa/bridge_ids.py``): ``send`` keys on ``--key`` (default: a digest of the
body) plus the recipient and ``--attempt``, ``broadcast`` keys on ``--id`` plus each recipient and
``--attempt``. Re-running the same command therefore re-posts the same keys and the executor's
ledger replays them -- it sends nothing a second time. To deliberately send the same text again,
change ``--key`` or raise ``--attempt``. Both commands are paced as ``first_touch``: an operator's
message is not an answer to an inbound turn the harness is holding, and the cold-contact gaps and
caps are the stricter ones.

DRY RUN. ``broadcast`` plans and prints; it queues the run only with ``--send`` (campaign CLI
convention, app/wa/luna/campaign.py). ``send`` is one deliberate message and sends; ``--dry-run``
shows what it would do. Both need ``WA_AUTOSEND=1``, the harness-wide switch the Meta rail already
obeys -- no new cap is introduced here, and the governor on the mini stays the only pacing
authority (``--pacing`` can ask it for a bigger gap, never a smaller one).

A BROADCAST IS A RUN, NOT A LOOP. ``--send`` writes the run into the executor's ledger and returns;
its runner thread works through the items one at a time, paced, and survives a restart of anything
on either side. So the items come back ``queued`` (exit 3) and the run is followed with ``--status``
until every item is ``sent``; ``--stop`` halts it between items. Queued items on a run that is NOT
open are exit 1, not exit 3: the executor picks items out of open runs only, so asking again about
a stopped run is a loop whose answer can never change. Re-running the same ``--id`` with
the same recipients is a replay, not a second message: the keys are deterministic.

DESTRUCTIVE COMMANDS. ``clear-chat`` empties a chat and keeps it; ``delete-chat`` removes it. Both
read the chat list first and print the row they are about to destroy; without ``--confirm`` they
also open the chat and print how many messages are in it (no bodies), and destroy nothing. With
``--confirm`` the row's own title and resolved number travel to the executor, which matches the row
again under the lock it destroys it with, refuses when the title is ambiguous or the number is not
the one on the handset, and must answer with its own verification (chat empty / chat gone) or the
command fails loudly. ``--expect-messages N`` asserts the count the preview printed, so a
conversation that grew a bubble since is refused rather than destroyed. A chat the list does not
show is never destroyed -- it is an error, not a no-op, unless the audit says THIS RAIL deleted it
(see below). ``--archived`` is an ASSERTION about which list the chat is on, checked against the row
and never filled in from it: the archive holds chats nobody authorised touching, so reaching one
takes typing the flag, and a row that disagrees with what was typed is refused before anything is
posted.

WHEN THE ANSWER IS LOST, THE RECORD IS ASKED (2026-09-21). A destruction owns the handset for about
two minutes and its HTTP call is given a budget derived from that (``app/wa/bridge.py``,
``DESTROY_BUDGET_SEC``), not the generic 90 s that expired one second before the executor answered
and made a completed deletion look like a failure. If the answer is lost anyway, the executor's
audit -- written BEFORE the first tap -- is read and reported: destroyed and verified (exit 0, with
a NOTE saying the report is the record and not the answer), destroyed and unproved (exit 1), or not
started (exit 1). If the audit cannot be read either, that is said in those words, with the command
to run, and neither outcome is claimed. Asking again for a chat this rail already deleted is
reported as done by ``delete-chat`` (exit 0) and as an error by ``clear-chat`` (exit 1: there is no
conversation left to empty). ``audit`` prints that record at any time and touches no phone.

WHAT THE RECORD CANNOT ANSWER IS SAID, NEVER GUESSED. A destruction is tied to a chat by the title
the handset drew or by the number the executor resolved it to, and ``audit.to_phone`` is null
whenever the handset could not resolve one -- the live ledger's first row is exactly that. Such a
row is neither this chat nor somebody else's, so a ``--phone`` lookup reports it as the open
question it is (exit 1) instead of answering "we never destroyed it" about a chat this rail
destroyed. The same rule covers the rest: an audit that will not read (the chat list still answered,
so what it established is still printed), a record whose number is not the one named, a title two
contacts share, and ``--archived`` (the audit has no folder column, so no row can corroborate it).
Each prints what is known, what is not, and the command that settles it. And the executor's own two
504s -- ``destruction_unverified``, ``send_unconfirmed`` -- are never printed as a refusal: their
message says the handset WAS touched and the result is not proved, which is its opposite.

RECIPIENT FILE. ``.csv`` with a ``phone`` header column, or ``.json`` as a list of objects with a
``phone`` key; an optional ``body`` per row overrides ``--body`` / ``--body-file``. A number that
does not canonicalize, a repeated recipient, a row with no body and no common body: the whole file
is refused, naming the line. Nothing is sent from a file we cannot read completely -- a broadcast
that silently skips a recipient is a broadcast nobody can audit.

THE HUMAN ESCAPE HATCH (TASK-131 round 5, decision-9 2026-09-22). Automatic attachment is gone:
nothing on this rail can prove who sent a pulled file (four rounds tried; see ``bridge/media.py``'s
own module docstring for why). Every pulled file sits in the QUEUE until a human attaches it.
``media-list`` prints what is known about each queued file -- its id, kind, age, size, the handset
folder it came from, and which threads plausibly relate to it in that period (a hint, never a
decision) -- and deliberately nothing that could identify whose file it might be: no filename, no
phone. ``media-attach`` is how a human closes the gap, once they know the answer from something off
this machine: it ties one file to one phone's own thread through the executor's own ``link_media``
-- the exact call an automatic link used to make, before round 5 removed automatic linking entirely
-- so the document reaches the card exactly as before.

PII. Chat titles, numbers and message bodies are printed to stdout, because reading a thread is the
point of ``read``. ``media-list`` is the one read command that deliberately prints none of that --
see above. Nothing is written to a file and nothing is logged.

EXIT. 0 done (including a chat this rail had already deleted: the handset is in the state that was
asked for and the audit says why); 1 something needs attention (a refused or failed item, a bridge
refusal, a destruction the executor could not verify, a lost answer whose record says the verb
started or did not); 2 usage, configuration or input error (nothing was attempted); 3 not finished (a send the executor accepted but has not sent, queued broadcast items)
-- ask again with ``broadcast --id RUN --status``; 130 interrupted.
"""
import argparse
import csv
import hashlib
import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.wa import bridge as BR          # noqa: E402
from app.wa import bridge_ids as BI      # noqa: E402
from app.wa import config as C           # noqa: E402
from app.wa import phones as PH          # noqa: E402

EXIT_OK, EXIT_ATTENTION, EXIT_CONFIG, EXIT_NOT_FINISHED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130

# app/wa/luna/campaign.py:119, unchanged: canonicalize_phone("garbage") is "+49", and a broadcast
# must never invent a recipient out of a typo. The same floor here keeps this CLI and the campaign
# sender agreeing on what is a number at all.
MIN_PHONE_DIGITS = 8
# What a chat row is called on screen, in the order worth printing. The row is whatever the executor
# listed; these are the keys we know how to show, and an unknown key is shown as it comes.
ROW_FIELDS = ("title", "phone", "messages", "unread", "stamp", "archived")
# The two per-item statuses that are not a problem: ``sent`` (a verified tick or a ledger replay of
# one) and ``queued`` (the executor has it, the runner has not reached it). Every other status --
# including one this CLI does not know -- is reported as needing attention, which is loud on purpose.
PENDING_STATE = BR.BROADCAST_QUEUED
# A run the executor's runner is still working through. Any other state and its queued items are
# items nothing will attempt (bridge/ledger.py::next_due_item selects out of open runs only).
RUN_OPEN = BR.RUN_OPEN
# What settles each of the executor's "the handset was touched" 504s. Per code, because the next
# move differs: a destruction is settled by the chat list, a send by the thread it went into.
TOUCHED_NEXT_STEP = {BR.CODE_DESTRUCTION_UNVERIFIED: "tools/wa_bridge.py chats",
                     BR.CODE_SEND_UNCONFIRMED: "tools/wa_bridge.py read --phone <the recipient>"}


def canonical_phone(raw, what):
    """-> +E.164, or raise ValueError naming what could not be read as a number."""
    canon = PH.canonicalize_phone(raw)
    if not canon or len(canon.lstrip("+")) < MIN_PHONE_DIGITS:
        raise ValueError(f"{what}: {raw!r} is not a phone number")
    return BI.require_e164(canon)


def read_body(body, body_file, what="--body"):
    """-> the message text from --body or --body-file, or None when neither was given."""
    if body is not None and body_file is not None:
        raise ValueError(f"{what} and {what}-file are alternatives, not both")
    if body_file is not None:
        text = pathlib.Path(body_file).read_text(encoding="utf-8").strip("\n")
        if not text.strip():
            raise ValueError(f"{body_file} is empty")
        return text
    return body


def read_recipients(path):
    """-> [{"line", "to", "body"}] in file order. Raises ValueError for anything it cannot read."""
    path = pathlib.Path(path)
    text = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise ValueError(f"{path}: a JSON recipient file is a list of objects")
        records = [(i + 1, r) for i, r in enumerate(data)]
    elif suffix == ".csv":
        reader = csv.DictReader(io.StringIO(text))
        header = reader.fieldnames or []
        if "phone" not in header:
            raise ValueError(f"{path}: the CSV header needs a 'phone' column, got {header}")
        records = []
        for row in reader:
            if None in row:
                raise ValueError(f"{path} line {reader.line_num}: more cells than header columns")
            records.append((reader.line_num, dict(row)))
    else:
        raise ValueError(f"{path}: a recipient file is .csv or .json")
    if not records:
        raise ValueError(f"{path}: no recipients")
    rows, seen = [], {}
    for line, record in records:
        to = canonical_phone(record.get("phone"), f"{path} line {line}")
        if to in seen:
            raise ValueError(f"{path} line {line}: {to} is already in the file at line {seen[to]}")
        seen[to] = line
        body = record.get("body")
        rows.append({"line": line, "to": to,
                     "body": str(body).strip() if body is not None and str(body).strip() else None})
    return rows


def plan_broadcast(rows, common_body):
    """-> [{"line", "to", "body"}] with every body resolved, or raise naming the line that has none."""
    items = []
    for row in rows:
        body = row["body"] or common_body
        if not body:
            raise ValueError(f"line {row['line']} ({row['to']}) has no body column and no --body/--body-file")
        items.append({"line": row["line"], "to": row["to"], "body": body})
    return items


def read_pacing(raw):
    """-> the --pacing object, or None. A usage error here is a usage error, not a traceback.

    The executor names the constraints it reads (``bridge/governor.py::CONSTRAINT_TYPES``) and
    refuses the rest; this end only insists the thing is a JSON OBJECT, so ``--pacing '[1,2]'``
    ends as "ERROR: ..." and exit 2 instead of a TypeError out of the client.
    """
    if not raw:
        return None
    try:
        pacing = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"--pacing is not JSON: {exc}") from exc
    if not isinstance(pacing, dict):
        raise ValueError(f"--pacing must be a JSON object, got {type(pacing).__name__}")
    return pacing


def require_target(args):
    """-> ``(phone, title)``, exactly one of them set. Both would let the two disagree, and on a
    destructive command a disagreement is the wrong chat destroyed. ``--title`` is the identity the
    chat list itself offers; ``--phone`` is for a chat the executor can open by number."""
    if (args.phone is None) == (args.title is None):
        raise ValueError("name the chat with exactly one of --phone or --title")
    return (canonical_phone(args.phone, "--phone") if args.phone else None), args.title


class ChatNotOnHandset(ValueError):
    """The named chat is not on the list the executor just drew. A ``ValueError`` like every other
    refusal this file raises before it posts anything, so an uninterested caller still gets exit 2;
    named apart because a destructive command has one more question to ask about it -- did WE
    remove it -- and the audit is what answers that (2026-09-21)."""


def find_chat(chats, *, phone=None, title=None, archived=False):
    """-> the one chat the operator named. Two matches or none is an error, never a guess: this is
    what a destructive command is about to act on.

    ``archived`` is the OPERATOR'S ASSERTION, not a field read off the row: seven archived chats
    are on that handset and nobody authorised touching them, so reaching one has to take typing
    ``--archived``. A row that disagrees with what was typed is a refusal for the same reason
    ``--phone`` disagreeing with the handset is one -- the caller and the handset are describing
    two different conversations."""
    if title is not None:
        hits, named = [c for c in chats if c.get("title") == title], f"title {title!r}"
    else:
        hits, named = [c for c in chats if c.get("phone") == phone], phone
    where = "the archive" if archived else "the main chat list"
    if not hits:
        raise ChatNotOnHandset(
            f"no chat on the handset for {named} -- nothing was touched. Run `chats` to see the list")
    if len(hits) > 1:
        raise ValueError(f"{len(hits)} chats match {named}: which one is meant is not guessable")
    if bool(hits[0].get("archived")) != bool(archived):
        raise ValueError(
            f"{named} was named as a chat in {where} and the handset draws it in "
            f"{'the archive' if hits[0].get('archived') else 'the main chat list'} -- nothing was "
            f"touched. Add --archived (or drop it) once you know which chat you mean")
    return hits[0]


def describe_chat(chat):
    """One chat row on one line: the fields we know, then anything else the executor listed."""
    known = [f"{k}={chat[k]!r}" for k in ROW_FIELDS if k in chat]
    rest = [f"{k}={v!r}" for k, v in sorted(chat.items()) if k not in ROW_FIELDS]
    return "  ".join(known + rest)


# --- the commands ---------------------------------------------------------------------------------

def cmd_health(args, client):
    health = client.health()
    if args.json:
        print(json.dumps(health, ensure_ascii=False, indent=2))
        return EXIT_OK
    rail, queue = health.get("rail") or {}, health.get("queue") or {}
    print(f"bridge {health.get('version')} at {health.get('at')}")
    print(f"  rail number: {rail.get('number') or 'UNVERIFIED'}   driver: {(rail.get('driver') or {}).get('kind')}")
    print(f"  queue: {queue}   quota: {health.get('quota')}")
    # TASK-131: health keeps reporting the queue -- surfaced here rather than only in --json, so an
    # operator sees it without asking twice.
    backlog = ((health.get("media_watcher") or {}).get("unresolved_backlog")) or {}
    if backlog.get("unresolved"):
        print(f"  media queue: {backlog['unresolved']} unattached, oldest "
              f"{backlog.get('oldest_unresolved_sec') or 0:.0f}s, by kind {backlog.get('by_kind')} "
              f"-- see `media-list`")
    if backlog.get("duplicate_content"):
        print(f"  {backlog['duplicate_content']} file(s) share bytes with another pull "
              f"(a resend, or two people sending one identical file) -- visible on `media-list`")
    if backlog.get("weak_links"):
        print(f"  {backlog['weak_links']} attribution(s) marked WEAK -- an automatic pick with "
              f"nothing to confirm it; audit with `--json`")
    # TASK-131 round 6: the one watcher that opens a chat, so its own count of what it attached
    # (strong vs weak) is worth a line an operator does not have to compute from the journal.
    identity = health.get("identity_watcher") or {}
    if identity:
        print(f"  identity watcher: {identity.get('attached_total', 0)} attached "
              f"({identity.get('weak_total', 0)} weak), {identity.get('errors', 0)} errors")
    return EXIT_OK


def cmd_chats(args, client):
    chats = client.list_chats()
    if args.json:
        print(json.dumps(chats, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"{len(chats)} chat(s) on the handset")
    for chat in chats:
        print(f"  {describe_chat(chat)}")
    return EXIT_OK


def cmd_read(args, client):
    phone, title = require_target(args)
    thread = client.read_thread(phone=phone, chat=title, archived=args.archived,
                                include_text=not args.no_text)
    if args.json:
        print(json.dumps(thread, ensure_ascii=False, indent=2))
        return EXIT_OK
    messages = thread["messages"]
    print(f"{describe_chat(thread.get('chat') or {})} -- {len(messages)} message(s) "
          f"({thread.get('visibility')})")
    for message in messages:
        arrow = ">>" if message.get("direction") == "out" else "<<"
        text = message["body"] if "body" in message else f"({message.get('body_len')} characters)"
        print(f"  {arrow} {message.get('clock') or '?'}  [{message.get('tick') or '-'}]  {text}")
    return EXIT_OK


def cmd_send(args, client):
    to = canonical_phone(args.to, "--to")
    body = read_body(args.body, args.body_file)
    if not body:
        raise ValueError("--body or --body-file is required")
    campaign_id = f"cli.{args.key or hashlib.sha256(body.encode('utf-8')).hexdigest()[:12]}"
    key = BI.campaign_key(campaign_id=campaign_id, phone=to, attempt=args.attempt)
    print(f"to {to}, {len(body)} character(s), key {key}")
    if args.dry_run:
        print("dry run: nothing was sent")
        return EXIT_OK
    if not C.AUTOSEND:
        print("ERROR: sending needs WA_AUTOSEND=1 (load .env first); nothing was sent", file=sys.stderr)
        return EXIT_CONFIG
    client.begin_campaign_attempt(campaign_id, to, args.attempt)
    try:
        client_msg_id = client.send_text(to, body)
    except BR.BridgeAccepted as exc:
        print(f"ACCEPTED, not yet sent: {exc}. Ask again with `read --phone {to}`", file=sys.stderr)
        return EXIT_NOT_FINISHED
    tick = ((client.last_send or {}).get("verified") or {}).get("tick")
    if args.json:
        print(json.dumps({"to": to, "client_msg_id": client_msg_id, "tick": tick}, ensure_ascii=False))
    else:
        print(f"sent {client_msg_id}, tick {tick!r}")
    return EXIT_OK


def cmd_send_photos(args, client):
    """TASK-131 round 7, Ivan 2026-09-22: mechanism proof, not production-ready (client.send_photos'
    own docstring) -- no idempotency key, a re-run sends the photos again. ``--files`` names paths
    already on the MINI's own filesystem, not this machine's."""
    to = canonical_phone(args.to, "--to")
    files = [f.strip() for f in args.files.split(",") if f.strip()]
    if not files:
        raise ValueError("--files must name at least one path (comma-separated)")
    print(f"to {to}, {len(files)} file(s): {files}")
    if args.dry_run:
        print("dry run: nothing was sent")
        return EXIT_OK
    if not C.AUTOSEND:
        print("ERROR: sending needs WA_AUTOSEND=1 (load .env first); nothing was sent", file=sys.stderr)
        return EXIT_CONFIG
    result = client.send_photos(to, files)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for i, item in enumerate(result.get("sent") or [], start=1):
            print(f"  photo {i}: clock {item.get('clock')!r}, tick {item.get('tick')!r}")
    return EXIT_OK


def cmd_send_gallery(args, client):
    """TASK-131 round 7 gallery redesign, Ivan 2026-09-22: one message, several photos, a shared
    caption -- mechanism proof, not production-ready (client.send_gallery's own docstring) -- no
    idempotency key, a re-run sends the album again. ``--files`` names paths already on the MINI's
    own filesystem, not this machine's."""
    to = canonical_phone(args.to, "--to")
    files = [f.strip() for f in args.files.split(",") if f.strip()]
    if not files:
        raise ValueError("--files must name at least one path (comma-separated)")
    caption = read_body(args.caption, args.caption_file) if (args.caption or args.caption_file) else ""
    print(f"to {to}, {len(files)} file(s): {files}, caption {caption!r}")
    if args.dry_run:
        print("dry run: nothing was sent")
        return EXIT_OK
    if not C.AUTOSEND:
        print("ERROR: sending needs WA_AUTOSEND=1 (load .env first); nothing was sent", file=sys.stderr)
        return EXIT_CONFIG
    result = client.send_gallery(to, files, caption=caption)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"sent: clock {result.get('clock')!r}, tick {result.get('tick')!r}")
    return EXIT_OK


def cmd_send_document(args, client):
    """TASK-131 round 7, Ivan 2026-09-23: one file, any type -- mechanism proof, not
    production-ready (client.send_document's own docstring) -- no idempotency key, a re-run sends
    the file again. ``--file`` names a path already on the MINI's own filesystem, not this
    machine's."""
    to = canonical_phone(args.to, "--to")
    if not args.file.strip():
        raise ValueError("--file must name a path")
    caption = read_body(args.caption, args.caption_file) if (args.caption or args.caption_file) else ""
    print(f"to {to}, file {args.file!r}, caption {caption!r}")
    if args.dry_run:
        print("dry run: nothing was sent")
        return EXIT_OK
    if not C.AUTOSEND:
        print("ERROR: sending needs WA_AUTOSEND=1 (load .env first); nothing was sent", file=sys.stderr)
        return EXIT_CONFIG
    result = client.send_document(to, args.file, caption=caption)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"sent: clock {result.get('clock')!r}, tick {result.get('tick')!r}")
    return EXIT_OK


def cmd_broadcast(args, client):
    if args.runs:
        runs = client.broadcast_runs()
        if args.json:
            print(json.dumps(runs, ensure_ascii=False, indent=2))
            return EXIT_OK
        print(f"{len(runs)} run(s) on the executor")
        for run in runs:
            print(f"  {run.get('run_id')}  {run.get('state')}  {run.get('counts')}  {run.get('created_at') or ''}")
        return EXIT_OK
    if not args.id:
        raise ValueError("broadcast needs --id RUN_ID (or --runs to list the runs)")
    if args.stop:
        return _print_run(client.broadcast_stop(args.id), args.json)
    if args.status:
        return _print_run(client.broadcast_status(args.id), args.json)
    if not args.file:
        raise ValueError("broadcast needs --file RECIPIENTS.csv|.json (or --status / --stop / --runs)")
    items = plan_broadcast(read_recipients(args.file), read_body(args.body, args.body_file))
    keys = client.broadcast_keys(args.id, [i["to"] for i in items], attempt=args.attempt)
    pacing = read_pacing(args.pacing)
    print(f"broadcast {args.id!r}, attempt {args.attempt}: {len(items)} recipient(s)"
          + (f", pacing {pacing}" if pacing else ""))
    for item in items:
        print(f"  line {item['line']}: {item['to']}  {len(item['body'])} character(s)  key {keys[item['to']]}")
    if not args.send:
        print("dry run: nothing was queued or sent. Add --send to run it")
        return EXIT_OK
    if not C.AUTOSEND:
        print("ERROR: --send needs WA_AUTOSEND=1 (load .env first); nothing was sent", file=sys.stderr)
        return EXIT_CONFIG
    run = client.broadcast(args.id, [{"to": i["to"], "body": i["body"]} for i in items],
                           attempt=args.attempt, pacing=pacing, note=args.note)
    return _print_run(run, args.json)


def _print_run(view, as_json):
    """-> the exit code the run's state and per-item statuses add up to. `queued` is a run still
    going, not a problem; anything that is neither `sent` nor `queued` is something to look at.

    A QUEUED ITEM ON A RUN THAT IS NOT OPEN IS NOT PENDING. The executor's runner selects only out
    of open runs, so telling an operator to ask again about a stopped run would send them round a
    loop whose answer can never change."""
    statuses = [item.get("status") for item in view["items"]]
    run = view.get("run") or {}
    open_run = run.get("state") == RUN_OPEN
    waiting = statuses.count(PENDING_STATE)
    if as_json:
        print(json.dumps(view, ensure_ascii=False, indent=2))
    else:
        print(f"run {run.get('run_id')!r} state={run.get('state')!r} counts={view.get('counts')}")
        for item in view["items"]:
            print(f"  {item.get('position', '?')}  {item.get('thread') or item['client_msg_id']}  "
                  f"{item.get('status')}  code={item.get('code')!r} detail={item.get('detail')!r}"
                  + (f" next={item['next_attempt_at']}" if item.get("next_attempt_at") else ""))
        if waiting and open_run:
            print(f"{waiting} item(s) still queued; the executor's runner sends them. "
                  f"Follow with: broadcast --id {run.get('run_id')} --status")
        elif waiting:
            print(f"{waiting} item(s) were never attempted: this run is {run.get('state')!r} and its "
                  f"runner will not pick them up. A new run id would send them")
    if any(s != BR.BROADCAST_SENT and s != PENDING_STATE for s in statuses):
        return EXIT_ATTENTION
    if not waiting:
        return EXIT_OK
    return EXIT_NOT_FINISHED if open_run else EXIT_ATTENTION


def _destroy(args, client, what):
    """The shared body of clear-chat and delete-chat: read the list, print the row, refuse without
    --confirm, act, print the executor's own verification."""
    phone, title = require_target(args)
    try:
        row = find_chat(client.list_chats(), phone=phone, title=title, archived=args.archived)
    except ChatNotOnHandset as missing:
        return _already_gone(client, what, missing, phone=phone, title=title,
                             archived=args.archived)
    if row.get("ambiguous"):
        raise ValueError(f"the handset ties {row['title']!r} to more than one number "
                         f"(phone_source={row.get('phone_source')!r}): whose chat this is cannot be established")
    verb = "emptied" if what == "clear" else "deleted"
    print(f"about to be {verb}: {describe_chat(row)}")
    if not args.confirm:
        # The deliberate preview opens the chat and counts, without the bodies: the count is the
        # number that tells an operator whether this is the conversation they meant, and it is what
        # --expect-messages then asserts. The confirmed path does not repeat it -- the executor
        # reads the chat itself, under the same lock it destroys it with.
        thread = client.read_thread(chat=row["title"], archived=args.archived, include_text=False)
        print(f"it holds {thread['count']} message(s) ({thread.get('visibility')}); nothing was {verb}. "
              f"To run it: --confirm [--expect-messages {thread['count']}]")
        return EXIT_OK
    extra = {"include_starred": not args.keep_starred} if what == "clear" else {}
    call = client.clear_chat if what == "clear" else client.delete_chat
    # args.archived, never row['archived']: the executor matches the row again under its own lock
    # and refuses when the two disagree. Filling this in from the row we just listed would post an
    # assertion the operator never made, and the archive gate would pass itself.
    report = call(chat=row["title"], phone=row.get("phone"), archived=args.archived,
                  expect_messages=args.expect_messages, confirm=True, **extra)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK
    if report.get("note"):
        # The report did not come from the answer to this call: the answer was lost and the
        # executor's audit was read instead. Saying so first is the difference between an operator
        # who knows what happened and one who presses the button again (2026-09-21).
        print(f"NOTE: {report['note']}")
    print(f"{verb}: {json.dumps(report.get('destroyed'), ensure_ascii=False)}")
    print(f"verification: {json.dumps(report['verification'], ensure_ascii=False)}"
          + (f"  audit={report['audit_id']}" if report.get("audit_id") else ""))
    return EXIT_OK


def _already_gone(client, what, missing, *, phone, title, archived):
    """The chat the operator named is not on the list. -> the exit code that fact deserves.

    Two different facts wear that one sentence, and until 2026-09-21 this command told them apart
    by leaving it to the operator to guess: there never was such a chat, or THIS RAIL deleted it --
    which, for `delete-chat`, is the outcome that was asked for, reported as a failure.

    THE EXIT CODES ARE CHOSEN, not inherited:
      delete-chat, and the audit ties a deletion to it -> 0. The goal is the state of the handset,
        the handset is in that state, and the record says we are why. An error here is what turned
        a successful deletion into an hour of doubt.
      clear-chat, same record                        -> 1. `clear-chat` promises to empty a
        conversation and KEEP it; there is no conversation left, so the operator's intent was not
        met and something needs looking at, even though nothing is wrong with the handset.
      no record at all                               -> 2. Unchanged: the operator named a chat
        that is not there and that we never destroyed, which is a typo or a wrong list.
      the record cannot answer                       -> 1. Either the audit could not be read, or
        it holds deletions whose number the handset never resolved and one of them may be this
        chat. Both are open questions, and the command that closes them is named. Neither is
        allowed to be printed as "we never destroyed it": that sentence, said about a chat this
        rail had in fact destroyed, is the whole of the 2026-09-21 incident.
    """
    try:
        tied, undecidable = client.destructions_of(chat=title, phone=phone,
                                                   operation="delete_chat")
    except BR.BridgeError as unreadable:
        # The executor ANSWERED: the chat list it drew is how we know the chat is missing, and only
        # the audit read died. Letting this escape to main() printed a reachability message and
        # dropped the fact we already held -- a destructive command saying nothing about the chat
        # it was asked about.
        print(f"ERROR: {missing}. Whether this rail destroyed it is NOT KNOWN from here: the "
              f"executor's audit could not be read ({unreadable}). Check with: "
              f"tools/wa_bridge.py audit", file=sys.stderr)
        return EXIT_ATTENTION
    if not tied and not undecidable:
        print(f"ERROR: {missing}. The executor's audit records no destruction of it either",
              file=sys.stderr)
        return EXIT_CONFIG
    if not tied:
        rows = "; ".join(f"audit {r['id']} {r['chat_title']!r} at {r['at']} "
                         f"(verified={r['verified']})" for r in undecidable)
        print(f"ERROR: {missing}. Whether this rail destroyed it is NOT KNOWN from here: the audit "
              f"holds {len(undecidable)} delete_chat row(s) whose number the handset never "
              f"resolved, so none of them can be tied to {phone} or ruled out -- {rows}. Check "
              f"with: tools/wa_bridge.py audit", file=sys.stderr)
        return EXIT_ATTENTION
    row = tied[0]
    named = (f"title {title!r}" if title is not None
             else f"{phone} (the row the audit titles {row['chat_title']!r})")
    # The audit has no folder column, so no row can corroborate --archived. Saying which chat it
    # was about is not ours to do from a record that never wrote it down.
    folder = (". The audit records no folder, so that row cannot confirm the chat it is about was "
              "the archived one you named" if archived else "")
    if what != "delete":
        print(f"ERROR: no chat on the handset for {named} because this rail DELETED it at "
              f"{row['at']} (audit {row['id']}) -- clear-chat empties a chat and keeps it, and "
              f"there is no chat left to empty. Nothing was touched{folder}", file=sys.stderr)
        return EXIT_ATTENTION
    print(f"already deleted: {named} was removed by this rail at {row['at']} (audit {row['id']}, "
          f"verified={row['verified']}) and is not on the handset's list now. Nothing was "
          f"touched{folder}")
    print(f"destroyed then: {json.dumps((row.get('detail') or {}).get('destroyed'), ensure_ascii=False)}")
    return EXIT_OK


def cmd_audit(args, client):
    """The destruction record, read-only. This is the command every refusal about a lost answer
    tells an operator to run, so it has to exist and it has to be one call."""
    rows = client.audit(limit=args.limit)
    if args.title is not None:
        rows = [r for r in rows if r.get("chat_title") == args.title]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"{len(rows)} destruction(s) recorded")
    for row in rows:
        detail = row.get("detail") or {}
        destroyed = detail.get("destroyed") or {}
        print(f"  {row.get('id')}  {row.get('at')}  {row.get('operation')}  "
              f"{row.get('chat_title')!r}  verified={row.get('verified')}  "
              f"state={detail.get('state')!r}  "
              f"{destroyed.get('visible_messages')} message(s)  "
              f"{(detail.get('proof') or {}).get('method')}")
    return EXIT_OK


def cmd_media_list(args, client):
    """The queue itself (read-only): every unattached file, kind/size/age/folder/related threads --
    no filename, no phone (bridge/executor.py::unresolved_media is the only source)."""
    files = client.unresolved_media()
    if args.json:
        print(json.dumps(files, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"{len(files)} file(s) in the queue")
    for f in files:
        dup = f" DUPLICATE_CONTENT(x{f['content_pull_count']})" if f.get("content_pull_count", 1) > 1 else ""
        print(f"  {f['queue_id']}  kind={f['kind']}  size={f['size']}B  "
              f"age={f['age_sec']:.0f}s  folder={f['source_dir']!r}  "
              f"related_threads={f['related_threads']}{dup}")
    return EXIT_OK


def cmd_media_attach(args, client):
    """The escape hatch itself: tie one queued file to one phone's own thread, through the same
    path (``bridge/ledger.py::link_media``) an automatic link used to make."""
    phone = canonical_phone(args.phone, "--phone")
    report = client.attach_media(args.id, phone)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"attached {report['queue_id']} (kind={report['kind']}) to {report['thread']}")
    return EXIT_OK


def cmd_clear_chat(args, client):
    return _destroy(args, client, "clear")


def cmd_delete_chat(args, client):
    return _destroy(args, client, "delete")


# --- the argument surface -------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(prog="wa_bridge", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def chat_target(p):
        p.add_argument("--phone", help="the chat's number, +E.164 or a local spelling")
        p.add_argument("--title", help="the chat's title exactly as `chats` printed it")
        p.add_argument("--archived", action="store_true", help="the chat lives in the archive folder")
        p.add_argument("--json", action="store_true")
        return p

    health = sub.add_parser("health", help="is the rail alive")
    health.add_argument("--json", action="store_true")
    health.set_defaults(fn=cmd_health)

    chats = sub.add_parser("chats", help="list the chats on the handset (read-only)")
    chats.add_argument("--json", action="store_true")
    chats.set_defaults(fn=cmd_chats)

    audit = sub.add_parser("audit", help="what this rail has destroyed, newest first (read-only)")
    audit.add_argument("--title", help="only the rows about this chat title")
    audit.add_argument("--limit", type=int, help="how many rows to read back")
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(fn=cmd_audit)

    media_list = sub.add_parser("media-list", help="the unattached-file queue: id, kind, size, age, "
                                                    "folder, related threads -- no filename, no "
                                                    "phone (read-only)")
    media_list.add_argument("--json", action="store_true")
    media_list.set_defaults(fn=cmd_media_list)

    media_attach = sub.add_parser("media-attach", help="attach one queued file to a phone by id "
                                                        "(the human escape hatch)")
    media_attach.add_argument("--id", required=True, help="the queue id, from `media-list`")
    media_attach.add_argument("--phone", required=True, help="the recipient's number, +E.164 or a "
                                                              "local spelling")
    media_attach.add_argument("--json", action="store_true")
    media_attach.set_defaults(fn=cmd_media_attach)

    read = chat_target(sub.add_parser("read", help="read one thread (read-only)"))
    read.add_argument("--no-text", action="store_true", help="counts, clocks and ticks without the message bodies")
    read.set_defaults(fn=cmd_read)

    send = sub.add_parser("send", help="send one message")
    send.add_argument("--to", required=True)
    send.add_argument("--body")
    send.add_argument("--body-file")
    send.add_argument("--key", help="key label; default a digest of the body, so a re-run is a replay, not a "
                                    "second message")
    send.add_argument("--attempt", type=int, default=1, help="raise it to deliberately send the same text again")
    send.add_argument("--dry-run", action="store_true", help="print the plan, post nothing")
    send.add_argument("--json", action="store_true")
    send.set_defaults(fn=cmd_send)

    photos = sub.add_parser("send-photos", help="attach up to 5 local images (mechanism proof, TASK-131 round 7)")
    photos.add_argument("--to", required=True)
    photos.add_argument("--files", required=True, help="comma-separated paths already on the mini's own disk")
    photos.add_argument("--dry-run", action="store_true", help="print the plan, post nothing")
    photos.add_argument("--json", action="store_true")
    photos.set_defaults(fn=cmd_send_photos)

    gallery = sub.add_parser("send-gallery", help="one message: up to 5 local images + a shared caption "
                                                   "(TASK-131 round 7 gallery redesign)")
    gallery.add_argument("--to", required=True)
    gallery.add_argument("--files", required=True, help="comma-separated paths already on the mini's own disk")
    gallery.add_argument("--caption", help="shared caption text")
    gallery.add_argument("--caption-file", help="read the caption from a file instead of --caption")
    gallery.add_argument("--dry-run", action="store_true", help="print the plan, post nothing")
    gallery.add_argument("--json", action="store_true")
    gallery.set_defaults(fn=cmd_send_gallery)

    document = sub.add_parser("send-document", help="one file, any type, as WhatsApp's own document "
                                                     "attachment (TASK-131 round 7)")
    document.add_argument("--to", required=True)
    document.add_argument("--file", required=True, help="a path already on the mini's own disk")
    document.add_argument("--caption", help="caption text")
    document.add_argument("--caption-file", help="read the caption from a file instead of --caption")
    document.add_argument("--dry-run", action="store_true", help="print the plan, post nothing")
    document.add_argument("--json", action="store_true")
    document.set_defaults(fn=cmd_send_document)

    bcast = sub.add_parser("broadcast", help="one message to many recipients, paced by the executor")
    bcast.add_argument("--id", help="run id ([A-Za-z0-9._-]); part of every recipient's key")
    bcast.add_argument("--file", help="recipients, .csv (phone[,body] header) or .json (list of objects)")
    bcast.add_argument("--body")
    bcast.add_argument("--body-file")
    bcast.add_argument("--attempt", type=int, default=1)
    bcast.add_argument("--pacing", help="JSON object of executor constraints; the governor may only make them "
                                        "slower, never faster")
    bcast.add_argument("--note", help="what this run is, recorded with it")
    bcast.add_argument("--send", action="store_true", help="queue the run; without it it is planned and printed only")
    bcast.add_argument("--status", action="store_true", help="read the run's per-item status back")
    bcast.add_argument("--stop", action="store_true", help="hard stop; takes effect between items")
    bcast.add_argument("--runs", action="store_true", help="list every run on the executor")
    bcast.add_argument("--json", action="store_true")
    bcast.set_defaults(fn=cmd_broadcast)

    def destructive(p):
        chat_target(p)
        p.add_argument("--confirm", action="store_true", help="required: without it nothing is destroyed")
        p.add_argument("--expect-messages", type=int,
                       help="the visible message count you saw in the preview; the executor refuses if the "
                            "conversation has moved on since")
        return p

    clear = destructive(sub.add_parser("clear-chat", help="empty one chat, keep the chat"))
    clear.add_argument("--keep-starred", action="store_true", help="leave starred messages on the thread")
    clear.set_defaults(fn=cmd_clear_chat)

    delete = destructive(sub.add_parser("delete-chat", help="remove one chat entirely"))
    delete.set_defaults(fn=cmd_delete_chat)
    return ap


def _audit_of(exc):
    """-> ' (audit N)' when the executor's refusal names the row it wrote before the taps, else ''.

    That row is the record of a destruction whose outcome is open, and printing its id is what
    makes `audit` one step instead of a hunt.
    """
    audit_id = (((exc.payload or {}).get("error") or {}).get("detail") or {}).get("audit_id")
    return f" (audit {audit_id})" if audit_id is not None else ""


def _server_detail(exc):
    """-> ' -- <executor message>' when the executor's own envelope says more than ``str(exc)``.

    A real HTTP error status raises inside ``app/wa/bridge.py::_default_transport`` before the
    client ever sees the parsed body, so every such ``BridgeError`` carries the same generic
    ``"bridge HTTP {code}"`` as its own message -- the executor's actual sentence (how many photos
    went out before it broke, which thread, etc.) only reaches this process inside ``.payload``.
    Printing nothing here is how an operator ends up reading the handset directly to learn what the
    server already knew.
    """
    message = (((exc.payload or {}).get("error") or {}).get("message") or "").strip()
    return f" -- {message}" if message and message not in str(exc) else ""


def main(argv=None, client=None):
    args = build_parser().parse_args(argv)
    cl = client if client is not None else BR.Client()
    if not cl.base_url or not cl.token:
        print("ERROR: WA_BRIDGE_URL / WA_BRIDGE_TOKEN are not set; load .env first", file=sys.stderr)
        return EXIT_CONFIG
    try:
        return args.fn(args, cl)
    except ValueError as exc:                     # our own refusals: nothing was attempted
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except BR.BridgeUnreachable as exc:
        print(f"ERROR: {exc} -- the executor never answered. Run `chats` before trying again",
              file=sys.stderr)
        return EXIT_ATTENTION
    except BR.BridgeError as exc:
        if exc.code in BR.LOST_ANSWER_CODES:
            # The executor refused nothing: its answer was lost, and the client has already read
            # the audit to say what that means. Printing "bridge refused" over that sentence would
            # put the word the operator misread on 2026-09-21 back on the screen.
            print(f"ERROR: {exc}", file=sys.stderr)
        elif exc.code in BR.HANDSET_TOUCHED_CODES:
            # The executor's own 504s, and they are not refusals either: their message says the
            # handset WAS tapped and the result could not be proved. "bridge refused" over "was
            # confirmed on the handset" is the same lie in the executor's vocabulary.
            print(f"ERROR: the handset was touched and the result is not proved -- this is not a "
                  f"refusal (status {exc.status_code}, code {exc.code!r}): {exc}{_server_detail(exc)}"
                  f"{_audit_of(exc)}. Read what the handset shows: {TOUCHED_NEXT_STEP[exc.code]}",
                  file=sys.stderr)
        else:
            print(f"ERROR: bridge refused (status {exc.status_code}, code {exc.code!r}): "
                  f"{exc}{_server_detail(exc)}", file=sys.stderr)
        return EXIT_ATTENTION
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    raise SystemExit(main())
