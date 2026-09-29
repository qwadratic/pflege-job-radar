"""The handset operations as one-step commands (TASK-376): send, broadcast, read, list.

Usage:
    python tools/wa_bridge.py health
    python tools/wa_bridge.py chats [--json]
    python tools/wa_bridge.py read (--phone +49... | --title "Name") [--archived] [--no-text] [--json]
    python tools/wa_bridge.py send --to +49... (--body TEXT | --body-file F) [--key LABEL] [--attempt N] [--dry-run]
    python tools/wa_bridge.py broadcast --id RUN --file RECIPIENTS.csv|.json [--body TEXT | --body-file F]
        [--attempt N] [--pacing JSON] [--note TEXT] [--send]
    python tools/wa_bridge.py broadcast --id RUN --status [--file RECIPIENTS.csv|.json
        [--body TEXT | --body-file F] [--attempt N]] | --stop | --runs
    python tools/wa_bridge.py audit [--title "Name"] [--limit N] [--json]
    python tools/wa_bridge.py media-list [--json]
    python tools/wa_bridge.py media-attach --id wab.q.xxxxxxxxxxxxxxxxxxxx --phone +49...
    python tools/wa_bridge.py unresolved-list [--json]
    python tools/wa_bridge.py reconcile --keys wab.o.xxx,wab.o.yyy [--json]
    python tools/wa_bridge.py ops-resolve --id op.xxxxxxxxxxxxxxxxxxxxxxxx [--json]

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

THE BRAIN'S MEMORY OF A BROADCAST (TASK-284). The executor's run view never carries a phone or a
body back -- ``bridge/broadcast.py``'s own view says why: "bodies stay on the handset machine". So
``--status --file RECIPIENTS...`` (the same ``--file``/``--body``/``--body-file``/``--attempt`` a
``--send`` of this run used) rebuilds the phone/body pairing the way ``--send`` built it, checks
each ``sent`` item's ``body_sha256`` against the body it rebuilt (a mismatch is a different file than
what actually went out, and is refused rather than recorded wrong), and writes the ones not already
there into ``data/wa.sqlite::wa_messages`` -- the store ``app/wa/luna_brain.py::turn_context`` reads
to know what was just sent. ``wamid`` is UNIQUE, so re-polling the same run re-records nothing.
Plain ``--status`` (no ``--file``) still reads the run back; it just cannot write what it was never
told.

DESTRUCTIVE COMMANDS ARE GONE (TASK-289, 2026-09-24). ``clear-chat``/``delete-chat`` used to live
here -- Ivan's ruling, after a phone-side chat delete let the real WhatsApp screen and this rail's
own records drift apart twice in one night through a mechanism the audit trail could not fully
explain: this rail never deletes conversation state again, in either direction. ``audit`` still
prints the historical destruction record (six rows, all from before the removal) and touches no
phone; the six-hundred-word discipline that used to sit here (preview/confirm/expect-messages,
lost-answer recovery, identity-mismatch refusals) went with the capability -- see
``bridge/operations.py``'s own module docstring for the full story.

RECIPIENT FILE. ``.csv`` with a ``phone`` header column, or ``.json`` as a list of objects with a
``phone`` key; an optional ``body`` per row overrides ``--body`` / ``--body-file``. A number that
does not canonicalize, a repeated recipient, a row with no body and no common body: the whole file
is refused, naming the line. Nothing is sent from a file we cannot read completely -- a broadcast
that silently skips a recipient is a broadcast nobody can audit.

THE HUMAN ESCAPE HATCH (TASK-360 round 5, decision-9 2026-09-22). Automatic attachment is gone:
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

EXIT. 0 done; 1 something needs attention (a refused or failed item, a bridge refusal, or -- TASK-264
-- ``health`` finding a dead/stale watcher, a dead ops dispatcher, a stale inbound backlog or a
retention error); 2 usage, configuration or input error (nothing was attempted); 3 not finished (a
send the executor accepted but has not sent, queued broadcast items) -- ask again with
``broadcast --id RUN --status``; 130 interrupted.
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
from app.wa import broadcast_template as BT
from app.wa import store as ST           # noqa: E402
from bridge import driver as D           # noqa: E402
from bridge import ledger as BL          # noqa: E402
from bridge import relay_pull as RP      # noqa: E402

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
# What settles the executor's "the handset was touched" 504: the thread the send went into.
TOUCHED_NEXT_STEP = {BR.CODE_SEND_UNCONFIRMED: "tools/wa_bridge.py read --phone <the recipient>"}


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
        name = record.get("name")
        rows.append({"line": line, "to": to,
                     "body": str(body).strip() if body is not None and str(body).strip() else None,
                     "name": str(name).strip() if name is not None and str(name).strip() else None})
    return rows


def plan_broadcast(rows, common_body, template=False):
    """-> [{"line", "to", "body"}] with every body resolved, or raise naming the line that has none.

    ``template=True`` (--template) renders app/wa/broadcast_template.py per recipient from their
    ``name`` and ignores every other source of text, so the approved first-touch wording cannot be
    retyped, paraphrased or improvised at the command line -- the whole point of freezing it
    (Ivan, 2026-09-24). A per-row ``body`` is refused rather than silently overridden: a file that
    carries both is a file whose author expected one of them to win, and guessing which is how the
    wrong text goes to a stranger."""
    items = []
    for row in rows:
        if template:
            if row["body"]:
                raise ValueError(f"line {row['line']} ({row['to']}) has its own body, which --template "
                                 f"would override -- drop one of the two")
            body = BT.render(row["name"])
        else:
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
    """-> ``(phone, title)``, exactly one of them set. ``--title`` is the identity the chat list
    itself offers; ``--phone`` is for a chat the executor can open by number."""
    if (args.phone is None) == (args.title is None):
        raise ValueError("name the chat with exactly one of --phone or --title")
    return (canonical_phone(args.phone, "--phone") if args.phone else None), args.title


def describe_chat(chat):
    """One chat row on one line: the fields we know, then anything else the executor listed."""
    known = [f"{k}={chat[k]!r}" for k in ROW_FIELDS if k in chat]
    rest = [f"{k}={v!r}" for k, v in sorted(chat.items()) if k not in ROW_FIELDS]
    return "  ".join(known + rest)


# --- the commands ---------------------------------------------------------------------------------

# TASK-264: `health`'s own help text is "is the rail alive" -- these are the same thresholds
# bridge/relay_pull.py::Relay.check_watcher_alarm already judges a live executor's own heartbeat
# against, every ALARM_CHECK_INTERVAL_SEC. Reused rather than reinvented, so a human running this
# by hand gets the same verdict an automated alarm would, not a second number nobody reviewed.
WATCHER_STALE_SEC = RP.WATCHER_STALE_SEC
INBOUND_BACKLOG_STALE_SEC = RP.INBOUND_BACKLOG_STALE_SEC


def _age(stamp, now):
    """-> seconds since ``stamp`` (an RFC3339 string), or None when there is nothing to age."""
    return BL.age_sec(stamp, now) if stamp else None


def _liveness_problems(health, now):
    """-> what makes `health`'s own exit code say something needs attention (TASK-264): the facts
    the design already names as decisive, judged only where the body actually says something --
    a field this call never asked about is unknown, not bad, and is left out of this list rather
    than guessed at. ``phone_ops`` depth/age is printed by the caller but not judged here: unlike
    watcher/inbound staleness, there is no reviewed number for it to be judged against, and this
    file does not invent one."""
    problems = []
    if "watcher" in health:
        watcher = health.get("watcher") or {}
        if watcher.get("alive") is False:
            problems.append("watcher.alive is false")
        age = _age(watcher.get("last_ok_at"), now)
        if age is None:
            problems.append("watcher.last_ok_at has never been set")
        elif age > WATCHER_STALE_SEC:
            problems.append(f"watcher.last_ok_at is {age:.0f}s old (over {WATCHER_STALE_SEC:.0f}s)")
    if (health.get("ops_dispatcher") or {}).get("alive") is False:
        problems.append("ops_dispatcher.alive is false")
    inbound_age = _age((health.get("inbound") or {}).get("oldest_unacked_at"), now)
    if inbound_age is not None and inbound_age > INBOUND_BACKLOG_STALE_SEC:
        problems.append(f"oldest unacked inbound row is {inbound_age:.0f}s old "
                        f"(over {INBOUND_BACKLOG_STALE_SEC:.0f}s)")
    retention_errors = (health.get("retention") or {}).get("errors")
    if retention_errors:
        problems.append(f"retention has {retention_errors} error(s)")
    return problems


def cmd_health(args, client):
    health = client.health()
    if args.json:
        print(json.dumps(health, ensure_ascii=False, indent=2))
        return EXIT_OK
    rail, queue = health.get("rail") or {}, health.get("queue") or {}
    print(f"bridge {health.get('version')} at {health.get('at')}")
    print(f"  rail number: {rail.get('number') or 'UNVERIFIED'}   driver: {(rail.get('driver') or {}).get('kind')}")
    print(f"  queue: {queue}   quota: {health.get('quota')}")
    # TASK-261: "two unconfirmed" and "two unconfirmed, oldest six days" are different information --
    # the count alone was already in `queue`, the age was not.
    stuck = sum(queue.get(s, 0) for s in ("attempting", "unconfirmed"))
    if stuck:
        print(f"  unresolved sends: {stuck} (oldest {health.get('oldest_unresolved_sec') or 0:.0f}s) "
              f"-- see `unresolved-list`")
    # TASK-264: printed every time, not only when it looks bad -- a liveness check that only speaks
    # up when something is wrong reads exactly like one that never runs at all, which is the whole
    # finding. `queue`/`quota` above answer "is the rail sending"; these answer "is the rail alive".
    now = client.now()
    watcher = health.get("watcher") or {}
    watcher_age = _age(watcher.get("last_ok_at"), now)
    print(f"  watcher: alive={watcher.get('alive')}  last_ok_at={watcher.get('last_ok_at')!r}"
          + (f"  age={watcher_age:.0f}s" if watcher_age is not None else ""))
    ops_dispatcher = health.get("ops_dispatcher") or {}
    print(f"  ops_dispatcher: alive={ops_dispatcher.get('alive')}  "
          f"last_ok_at={ops_dispatcher.get('last_ok_at')!r}")
    phone_ops = health.get("phone_ops") or {}
    print(f"  phone_ops: queued={phone_ops.get('queued')}  "
          f"oldest_queued_at={phone_ops.get('oldest_queued_at')!r}")
    inbound = health.get("inbound") or {}
    inbound_age = _age(inbound.get("oldest_unacked_at"), now)
    print(f"  inbound: unacked={inbound.get('unacked')}  "
          f"oldest_unacked_at={inbound.get('oldest_unacked_at')!r}"
          + (f"  age={inbound_age:.0f}s" if inbound_age is not None else ""))
    retention = health.get("retention") or {}
    print(f"  retention: last_ok_at={retention.get('last_ok_at')!r}  errors={retention.get('errors')}"
          + (f"  last_error={retention.get('last_error')!r} at {retention.get('last_error_at')!r}"
             if retention.get("errors") else ""))
    # TASK-360: health keeps reporting the queue -- surfaced here rather than only in --json, so an
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
    # TASK-360 round 6: the one watcher that opens a chat, so its own count of what it attached
    # (strong vs weak) is worth a line an operator does not have to compute from the journal.
    identity = health.get("identity_watcher") or {}
    if identity:
        print(f"  identity watcher: {identity.get('attached_total', 0)} attached "
              f"({identity.get('weak_total', 0)} weak), {identity.get('errors', 0)} errors")
    problems = _liveness_problems(health, now)
    if problems:
        print(f"  ATTENTION: {'; '.join(problems)}")
    return EXIT_ATTENTION if problems else EXIT_OK


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


def _media_sent_or_answer_lost(send, to):
    """Call one of client.send_photos/send_gallery/send_document and -> its result, or print the
    "answer lost" line and -> None on CODE_ANSWER_TIMEOUT (TASK-247).

    OPS_PATH's own contract (bridge.py:100-104) is that these three routes only ever reach
    CODE_ANSWER_TIMEOUT after the executor already answered 200 {"state": "queued"} -- this call WAS
    queued for the handset, so "bridge refused" is never true of it, and the send may well still be
    landing while this prints (GALLERY_BUDGET_SEC alone is 187s). None of the three mint an
    idempotency key (their own docstrings), so the caller must not resend on a guess."""
    try:
        return send()
    except BR.BridgeError as exc:
        if exc.code != BR.CODE_ANSWER_TIMEOUT:
            raise
        print(f"ERROR: {exc} -- this is not a refusal; do not resend. Read what the handset shows: "
              f"tools/wa_bridge.py read --phone {to}", file=sys.stderr)
        return None


def cmd_send_photos(args, client):
    """TASK-360 round 7, Ivan 2026-09-22: mechanism proof, not production-ready (client.send_photos'
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
    result = _media_sent_or_answer_lost(lambda: client.send_photos(to, files), to)
    if result is None:
        return EXIT_ATTENTION
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for i, item in enumerate(result.get("sent") or [], start=1):
            print(f"  photo {i}: clock {item.get('clock')!r}, tick {item.get('tick')!r}")
    return EXIT_OK


def cmd_send_gallery(args, client):
    """TASK-360 round 7 gallery redesign, Ivan 2026-09-22: one message, several photos, a shared
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
    result = _media_sent_or_answer_lost(lambda: client.send_gallery(to, files, caption=caption), to)
    if result is None:
        return EXIT_ATTENTION
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"sent: clock {result.get('clock')!r}, tick {result.get('tick')!r}")
    return EXIT_OK


def cmd_send_document(args, client):
    """TASK-360 round 7, Ivan 2026-09-23: one file, any type -- mechanism proof, not
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
    result = _media_sent_or_answer_lost(lambda: client.send_document(to, args.file, caption=caption), to)
    if result is None:
        return EXIT_ATTENTION
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"sent: clock {result.get('clock')!r}, tick {result.get('tick')!r}")
    return EXIT_OK


def _record_broadcast_sent(client, run_id, attempt, file, body, body_file, view, template=False):
    """Every item ``view`` reports ``sent`` and ``wa_messages`` does not hold yet, written there
    (TASK-284) -> ``(recorded, already)`` client_msg_ids. Raises naming any ``sent`` item whose
    ``body_sha256`` disagrees with what ``file``/``body``/``body_file`` rebuild for it -- that is a
    different file than the one this run was sent from, and recording the wrong text into Luna's
    memory of what she said is worse than recording nothing.

    The pairing (phone, body) is rebuilt exactly as ``--send`` built it, because the run view itself
    never carries either back (see the module docstring, "THE BRAIN'S MEMORY OF A BROADCAST")."""
    items = plan_broadcast(read_recipients(file), read_body(body, body_file), template=template)
    keys = client.broadcast_keys(run_id, [i["to"] for i in items], attempt=attempt)
    by_key = {keys[i["to"]]: i for i in items}
    sent_rows = [row for row in view["items"] if row.get("status") == BR.BROADCAST_SENT]
    recorded, already, mismatched, unmatched = [], [], [], []
    c = ST.db()
    try:
        for row in sent_rows:
            item = by_key.get(row["client_msg_id"])
            if item is None:
                # Opus review, 2026-09-23 (post-fix pass): this used to be a silent `continue` -- a
                # WRONG --file (typo'd path, an old recipients list, someone else's run) would then
                # match nothing, print nothing, and exit 0, indistinguishable from "already
                # recorded". A sent item this file cannot explain is exactly as loud a problem as a
                # body mismatch below -- it means this is not really the file that run went out
                # from, or --attempt is wrong, and staying quiet about it is how the 2026-09-23
                # incident (a broadcast the brain never learned about) would have kept happening.
                unmatched.append(row["client_msg_id"])
                continue
            if row.get("body_sha256") not in (None, D.body_sha256(item["body"])):
                mismatched.append(row["client_msg_id"])
                continue
            if ST.message_by_wamid(c, row["client_msg_id"]) is not None:
                already.append(row["client_msg_id"])
                continue
            # updated_at, not now_iso(): this backfill usually runs well after the real send (a
            # human runs --status later, or -- 2026-09-23 -- the executor was mid-restart when the
            # send happened). Stamping "now" would insert this row out of chronological order
            # against whatever the candidate said in between, corrupting turn_context()'s reading
            # of what was said when -- the same class of defect TASK-284 exists to fix, just moved
            # into the timestamp instead of the row's existence.
            ST.record_outbound(c, item["to"], row["client_msg_id"], item["body"], kind="text",
                               meta={"action": "broadcast", "run_id": run_id}, at=row.get("updated_at"))
            recorded.append(row["client_msg_id"])
    finally:
        c.close()
    if mismatched or unmatched:
        problems = []
        if mismatched:
            problems.append(f"{len(mismatched)} item(s) whose body does not match what {file} "
                            f"names ({', '.join(mismatched)})")
        if unmatched:
            problems.append(f"{len(unmatched)} sent item(s) this file/attempt cannot explain at "
                            f"all ({', '.join(unmatched)})")
        raise ValueError(f"broadcast {run_id!r}: {' and '.join(problems)} -- this is not the "
                         f"file/attempt this run was sent from. {len(recorded)} other item(s) were "
                         f"recorded; these were not")
    return recorded, already


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
        view = client.broadcast_status(args.id)
        if args.file:
            recorded, already = _record_broadcast_sent(client, args.id, args.attempt, args.file,
                                                        args.body, args.body_file, view,
                                                        template=args.template)
            if not args.json and (recorded or already):
                print(f"wa_messages: {len(recorded)} sent item(s) newly recorded, "
                      f"{len(already)} already there")
        elif not args.json and any(i.get("status") == BR.BROADCAST_SENT for i in view["items"]):
            print("NOTE: --file was not given, so the sent item(s) above were not recorded into "
                  "wa_messages -- turn_context() will not see them. Re-run with the same --file "
                  "(and --body/--body-file/--attempt as at --send) to record them")
        return _print_run(view, args.json)
    if not args.file:
        raise ValueError("broadcast needs --file RECIPIENTS.csv|.json (or --status / --stop / --runs)")
    items = plan_broadcast(read_recipients(args.file), read_body(args.body, args.body_file),
                           template=args.template)
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


def cmd_unresolved_list(args, client):
    """The rows a reconcile still has to answer for, read-only (TASK-261): id, thread, state, age --
    no phone (bridge/executor.py::unresolved_sends is the only source). Feeds straight into
    `reconcile --keys`."""
    rows = client.unresolved_sends()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"{len(rows)} unresolved send(s)")
    for r in rows:
        escalated = "  ESCALATED (stopped retrying)" if r.get("escalated") else ""
        print(f"  {r['client_msg_id']}  state={r['state']}  thread={r['thread_tag']}  "
              f"age={r['age_sec']:.0f}s{escalated}")
    if rows:
        print(f"reconcile them: reconcile --keys {','.join(r['client_msg_id'] for r in rows)}")
    return EXIT_OK


def cmd_reconcile(args, client):
    """Ask the executor what actually happened to sends still sitting UNCONFIRMED/ATTEMPTING in
    its ledger (TASK-230) -- the step that lets a failed send's own retention (bridge/retention.py)
    ever leave 'held' on its own, without a human resolving anything by hand. Only confirmed_absent
    authorises a resend; nothing here resends by itself."""
    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    if not keys:
        print("--keys names at least one client_msg_id")
        return EXIT_CONFIG
    results = client.reconcile(keys)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return EXIT_OK
    for r in results:
        print(f"  {r.get('client_msg_id')}  {r.get('verdict')}  {r.get('evidence', '')}")
    return EXIT_OK


def cmd_ops_resolve(args, client):
    """Mark one failed phone op reviewed and safe to delete (TASK-230) -- the escape hatch for a
    failure that minted no client_msg_id (read_thread, send_photos/gallery/document), so reconcile
    has nothing to read a verdict off. Look at the op's own artefacts first
    (WA_BRIDGE_STATE/shots/{op_id}_*.png, recordings/{op_id}.mp4) -- this command only records that
    a human already did."""
    report = client.resolve_op(args.id)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"resolved {report['op_id']}")
    return EXIT_OK


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

    unresolved_list = sub.add_parser("unresolved-list", help="sends still ATTEMPTING/UNCONFIRMED: "
                                                              "id, thread, state, age (read-only, "
                                                              "TASK-261)")
    unresolved_list.add_argument("--json", action="store_true")
    unresolved_list.set_defaults(fn=cmd_unresolved_list)

    reconcile = sub.add_parser("reconcile", help="ask the executor what happened to sends still "
                                                 "unconfirmed in its ledger (TASK-230)")
    reconcile.add_argument("--keys", required=True, help="comma-separated client_msg_ids")
    reconcile.add_argument("--json", action="store_true")
    reconcile.set_defaults(fn=cmd_reconcile)

    ops_resolve = sub.add_parser("ops-resolve", help="mark one failed phone op reviewed and safe "
                                                      "to delete (TASK-230)")
    ops_resolve.add_argument("--id", required=True, help="the op_id, from a phone_ops row or its "
                                                          "artefact filenames")
    ops_resolve.add_argument("--json", action="store_true")
    ops_resolve.set_defaults(fn=cmd_ops_resolve)

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

    photos = sub.add_parser("send-photos", help="attach up to 5 local images (mechanism proof, TASK-360 round 7)")
    photos.add_argument("--to", required=True)
    photos.add_argument("--files", required=True, help="comma-separated paths already on the mini's own disk")
    photos.add_argument("--dry-run", action="store_true", help="print the plan, post nothing")
    photos.add_argument("--json", action="store_true")
    photos.set_defaults(fn=cmd_send_photos)

    gallery = sub.add_parser("send-gallery", help="one message: up to 5 local images + a shared caption "
                                                   "(TASK-360 round 7 gallery redesign)")
    gallery.add_argument("--to", required=True)
    gallery.add_argument("--files", required=True, help="comma-separated paths already on the mini's own disk")
    gallery.add_argument("--caption", help="shared caption text")
    gallery.add_argument("--caption-file", help="read the caption from a file instead of --caption")
    gallery.add_argument("--dry-run", action="store_true", help="print the plan, post nothing")
    gallery.add_argument("--json", action="store_true")
    gallery.set_defaults(fn=cmd_send_gallery)

    document = sub.add_parser("send-document", help="one file, any type, as WhatsApp's own document "
                                                     "attachment (TASK-360 round 7)")
    document.add_argument("--to", required=True)
    document.add_argument("--file", required=True, help="a path already on the mini's own disk")
    document.add_argument("--caption", help="caption text")
    document.add_argument("--caption-file", help="read the caption from a file instead of --caption")
    document.add_argument("--dry-run", action="store_true", help="print the plan, post nothing")
    document.add_argument("--json", action="store_true")
    document.set_defaults(fn=cmd_send_document)

    bcast = sub.add_parser("broadcast", help="one message to many recipients, paced by the executor")
    bcast.add_argument("--id", help="run id ([A-Za-z0-9._-]); part of every recipient's key")
    bcast.add_argument("--file", help="recipients, .csv (phone[,body] header) or .json (list of objects); "
                                      "with --status, the same file this run was --send with records its "
                                      "sent items into wa_messages")
    bcast.add_argument("--template", action="store_true",
                       help="send the frozen first-touch template (app/wa/broadcast_template.py), "
                            "rendered per recipient from their 'name' column; refuses --body")
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
    return ap


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
        if exc.code in BR.HANDSET_TOUCHED_CODES:
            # The executor's own 504, and it is not a refusal either: its message says the
            # handset WAS tapped and the result could not be proved. "bridge refused" over "was
            # confirmed on the handset" is the same lie in the executor's vocabulary.
            print(f"ERROR: the handset was touched and the result is not proved -- this is not a "
                  f"refusal (status {exc.status_code}, code {exc.code!r}): {exc}{_server_detail(exc)}"
                  f". Read what the handset shows: {TOUCHED_NEXT_STEP[exc.code]}",
                  file=sys.stderr)
        else:
            print(f"ERROR: bridge refused (status {exc.status_code}, code {exc.code!r}): "
                  f"{exc}{_server_detail(exc)}", file=sys.stderr)
        return EXIT_ATTENTION
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    raise SystemExit(main())
