"""Replay a real WhatsApp thread through the Luna brain, cut at turn N, nothing sent (TASK-313; Ivan,
2026-10-06: run our reply bot over the chat histories and look at the gap to what was actually answered).
For every turn: what Luna says now next to what really followed in the chat.

SOURCE. The colleague's CRM, ``candidate_whatsapp_messages`` in sales_brain.sqlite (old bot and humans,
not this harness). Opened ``mode=ro`` only (``fetch_rows``/``list_candidates``). Columns read: id,
direction, message_type, body, caption, media_filename, occurred_at. NEVER ``provider_metadata_json`` (the
raw Meta webhook envelope: it holds a real phone number and user id even on a row with an empty body),
never ``phone_e164``/``contact_name``/``wamid``. A synthetic phone (``synthetic_phone``) stands in for
every candidate, so the brain and its MCP tools never see a real number.

NO SEND. Every turn runs ``luna_brain.turn(..., no_send=True)``, which ``Client.no_send`` hands to the
spawned MCP tools server as ``WA_LUNA_NO_SEND=1`` (``luna_brain._mcp_config_path``) whatever this
process's own env says. Of the MCP tools in ``app/wa/luna/tools_server.py`` exactly two reach the phone
rail (the only ``BR.Client()`` call sites), ``show_clinic_photos`` and ``send_updated_cv``; both check that
flag first and return a ``dry_run`` result before touching the bridge, ``C.AUTOSEND`` or the filesystem.
``tools/wa_replay.py`` also sets the flag on its own one-shot process, like tools/wa_rehearse.py. This
module's import graph does load ``app.wa.bridge`` (``luna_brain.turn_context``/``_user_payload`` lazily
import ``app.wa.api``, which imports it), but nothing here calls its send function.
``record_outbound`` writes only HISTORICAL rows that already went out in reality.

ISOLATION. ``replay_candidate`` resolves ``out_dir`` and points ``config.SQLITE_PATH`` at
``out_dir/<candidate_id>.sqlite`` and ``config.LUNA_SESSION_DIR`` at ``out_dir/sessions``. ``luna_brain``
keeps every per-turn file under the latter (``tool_calls.jsonl``, ``board_snapshot.json``,
``board_vocabulary.json``, ``mcp_config/``, ``tools_ready/``, and the CLI subprocess's cwd), so nothing of
a replay lands in data/wa.sqlite or data/wa_luna_sessions/. Both attributes stay pointed at ``out_dir``
for the rest of the process (a test restores them; the CLI exits). Refusing an ``--out`` inside a repo
checkout is the CLI's job, once, at the boundary.

PROD SETTINGS. This module reads whatever ``config.LUNA_MODEL``/``LUNA_EFFORT`` and the board snapshot
already are in the process. ``tools/wa_replay.py`` is what loads production's model, effort and
Supabase key and points the board snapshot at a read-only copy of the board DB before calling in; a
direct caller (every test) gets library defaults, so a test supplies its own fake ``LB.turn``.

CUT AT TURN N. Every turn is a brand-new, memoryless Claude Code session: ``_run_turn`` drops
``_session_id`` from the card before ``LB.turn``. That is the state production falls back to after a
restart (``luna_brain.turn``'s ``SessionNotFound`` recovery). The model sees the earlier conversation the
way it does there: ``outbound_since_last_turn``/``last_turn_at``/``introduced`` in the payload
(``luna_brain.turn_context``, keyed off the card's ``LAST_TURN_KEY`` marker) plus its ``read_history`` tool
over the scratch DB. Luna's own predicted bubbles never enter the scratch DB or a later turn: turn N+1
sees the REAL outbound that followed turn N, as if a colleague (or the old bot) had answered.

AT TURNS. ``at_turns`` (a list of 1-based turn numbers, as ``--list-turns``-style listings count them:
a listing is ``at_turns=[]``) runs the brain ONLY on those turns. Every other turn is recorded as plain
history, inbound rows and the real reply rows exactly as the full replay records them, with NO model
call; its JSONL line carries ``skipped_reason: "not_in_at_turns"`` (inbound, actual_reply and
``prior_context_len`` still there). The walk stops after the last chosen turn (a ``truncated`` line when
conversation is left). KNOWN LIMIT: the card is advanced only by turns that really run, so a late chosen
turn sees a card the skipped turns before it did not update (and no LAST_TURN_KEY marker for them: the
brain sees every outbound row since its last real turn). A chosen number past the thread's last turn is
a loud error, after the file is written. Exclusive with ``max_turns``.

TURNS. One turn per inbound burst, as ``app/wa/luna/catchup.py``'s owed pass does it (not one per
message, as the webhook path does). Consecutive inbound rows accumulate and are joined with ``"\\n"`` into
the turn's text; the first real outbound row after them is where production would have answered, so
``turn_context`` + ``turn`` run right before that row is inserted. Each JSONL line says
``turn_kind``: ``"single"`` (one inbound message) or ``"burst"`` (more). A trailing burst with no outbound
after it still gets a turn; its ``actual_reply`` is empty. A STOP turn ends the thread as ``app/wa/api.py``
does: every later burst is a ``skipped_reason: "stopped"`` line (rows still recorded, brain not called).

ROWS. Inbound text/button/interactive rows are recorded as given. Media (document/image/audio) with an
empty body gets a bracketed placeholder and the turn carries ``media_placeholder: true`` (no file to
re-derive text from, so any document/photo gate behaves differently than on the real attachment). A
DOCUMENT placeholder keeps only the extension, ``"[document].pdf"`` or plain ``"[document]"`` (the stored
filename never appears: PII); image/audio keep their ``media_filename``. A non-empty body on an audio row
is the old system's STT transcript and counts as real text. A button tap with no stored text (the real
payload lives only in the unread metadata column) gets the placeholder too, so every replayed tap reaches
the brain as free text, ``is_button_reply: false``. Reaction and unsupported rows, either direction, are
recorded for history and never drive turn logic.

ACTUAL REPLY. The outbound rows after a turn, up to the next real inbound. An inbound reaction/unsupported
row between two outbound rows of one reply is recorded but does not end the collection. Every item keeps
its own ``at``, so a judge can tell the reply from a later nudge; ``actual_reply_delay_s`` is last inbound
to first reply.

TIMESTAMPS. Every recorded row, inbound and outbound, carries sales_brain's historical ``occurred_at``
(``record_inbound``/``record_outbound`` ``at=``). ``turn()`` stamps its own ``turn_at`` as now (see KNOWN
LIMITS).

COLD OPEN. When the first outbound rows of a thread (before any turn) are a template, the thread is marked
the way ``app/wa/luna/campaign.py`` marks a campaign send (``ST.record_campaign_send``): ``card.campaign``
and ``meta.action = "campaign"``. The stub carries only what sales_brain has (``rendered_text``,
``wamid``, ``sent_at``); ``campaign_id``/``template_name``/``language`` are ``None`` and ``buttons`` is
``[]``, never invented.

ESCALATION. ``line["luna"]["escalation"]`` is a diff, ``{field: {"before", "after"}}``, over the
escalation/flag card keys (``app/wa/luna/escalation.py``) THIS turn changed, like ``slots_diff``. Not the
card's current value, which would repeat an old escalation on every later line.

INSTRUMENTATION (AC#3). Every line carries ``model``/``effort`` (``config.LUNA_MODEL``/``LUNA_EFFORT``) and
``git_sha`` (HEAD of the checkout this file lives in). ``timings`` and ``tokens`` are ``None`` plus a note:
``luna_brain.turn()`` returns neither (``Client._live_reply`` only LOGS ``duration_ms``/``num_turns``).
They need a change to its return contract first; nothing is invented here.

KNOWN LIMITS, read before trusting a report built on this:
  - sales_brain has no media files: any document/photo-gated rule, and ``show_clinic_photos``'s "has a
    photo" branch, run against metadata and today's board, not the real attachment;
  - every candidate starts from FRESH state (no card, no session): a history that began mid-relationship
    (an import, a resumed campaign) replays as a first contact;
  - every board tool call (search_postings, get_posting, market_snapshot, ...) answers from the board as
    it is NOW, not on the replayed date;
  - the card carries forward whatever Luna's own turn wrote to it (``card_patch``, warming stamps,
    funnel stage), not what the real replies imply; later turns read that card;
  - a brain exception on turn N leaves the card and ``LAST_TURN_KEY`` as they were before N (``turn()``
    works on a copy and returns it only on success), so turn N+1 reads turn N-1's checkpoint;
  - a button tap replays as free text (see ROWS);
  - ``turn()`` stamps its own ``turn_at`` as the wall-clock moment of the replay: the card's own
    timestamps (warming stamp/note, ``stage_at``, ``declined_at``, ...) and the ``last_turn_at`` the next
    payload shows are today's date, while the recorded rows carry the replayed dates;
  - an empty template body replays as ``"[template]"``, never the real wording (the cold-open stub's
    ``rendered_text`` and the model both see a template that says nothing).
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import subprocess
from datetime import datetime

from .. import config as C
from .. import store as ST
from .. import luna_brain as LB

#: message_type values that are recorded but never drive turn logic in either direction (ROWS).
NO_TURN_TYPES = frozenset({"reaction", "unsupported"})
#: Kinds whose empty body is a real file this replay cannot see (ROWS, KNOWN LIMITS).
MEDIA_TYPES = frozenset({"document", "image", "audio"})

#: Prefix of every synthetic id written into the scratch store: never a real wamid from sales_brain.
_WAMID_PREFIX = "replay"

#: Card keys escalation.py writes (``_append``/``record_escalation``/``record_flag``): what ESCALATION diffs.
_ESCALATION_KEYS = ("_escalated", "_escalated_at", "_escalation_codes", "_escalate_reason",
                    "_escalate_reason_notes", "_flag_codes", "_flags", "_flags_notes")

#: This checkout's root (app/wa/luna/replay.py is three directories below it), for ``_git_sha``.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def synthetic_phone(candidate_id):
    """-> an obviously fake E.164-shaped number per candidate, deterministic, never the real
    ``phone_e164``. +49000 is not an assignable German prefix (mobiles start +4915/+4916/+4917), so it
    cannot collide with or pass for a real number."""
    return f"+49000{int(candidate_id):08d}"


def _placeholder_text(message_type, caption, media_filename):
    """Bracketed stand-in for a row with no real text (ROWS). The caption rides along. A document keeps
    only its extension (``"[document].pdf"``, or ``"[document]"`` with none), never the filename; any
    other kind keeps its ``media_filename``."""
    if message_type == "document":
        suffix = pathlib.Path(media_filename).suffix if media_filename else ""
        parts = [f"[document]{suffix}"]
    else:
        parts = [f"[{message_type}]"]
        if media_filename:
            parts.append(media_filename)
    if caption:
        parts.append(caption)
    return " ".join(parts)


def _display_text(row):
    """-> (text, was_placeholder): the row's own body when non-empty (exactly what was sent or typed),
    else a bracketed placeholder."""
    body = (row["body"] or "").strip()
    if body:
        return body, False
    caption = (row["caption"] or "").strip()
    return _placeholder_text(row["message_type"], caption, row["media_filename"]), True


def _wamid(candidate_id, source_id):
    return f"{_WAMID_PREFIX}:{candidate_id}:{source_id}"


def _meta(row):
    return {"message_type": row["message_type"], "source_id": row["id"]}


def fetch_rows(candidate_id, sales_brain_path):
    """Every message of one candidate in replay order (occurred_at, id). Read-only, and only the columns
    the module docstring names (SOURCE)."""
    conn = sqlite3.connect(f"file:{sales_brain_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "select id, direction, message_type, body, caption, media_filename, occurred_at "
            "from candidate_whatsapp_messages where candidate_id=? order by occurred_at, id",
            (candidate_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def list_candidates(sales_brain_path):
    """-> one row per candidate_id: counts in/out, first/last occurred_at. Rows with a null candidate_id
    belong to no thread and are left out, same scope ``--candidate ID`` can address."""
    conn = sqlite3.connect(f"file:{sales_brain_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            select candidate_id,
                   sum(case when direction='inbound' then 1 else 0 end) as n_in,
                   sum(case when direction='outbound' then 1 else 0 end) as n_out,
                   min(occurred_at) as first_at, max(occurred_at) as last_at
            from candidate_whatsapp_messages
            where candidate_id is not null
            group by candidate_id
            order by candidate_id
        """).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _unlink_sqlite(path):
    """Remove a scratch file and its sidecars: every replay starts from fresh state (KNOWN LIMITS)."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = pathlib.Path(str(path) + suffix)
        if p.exists():
            p.unlink()


def _strip_underscore(card):
    return {k: v for k, v in (card or {}).items() if not str(k).startswith("_")}


def _escalation_diff(before, after):
    """-> {field without its leading underscore: {"before", "after"}} for the escalation/flag card keys
    this turn changed, {} when none (ESCALATION). ``before``/``after`` are the full cards, underscore keys
    included."""
    diff = {}
    for k in _ESCALATION_KEYS:
        b, a = (before or {}).get(k), (after or {}).get(k)
        if b != a:
            diff[k.lstrip("_")] = {"before": b, "after": a}
    return diff


def _diff_slots(before, after):
    """-> {key: {"before", "after"}} for every card key (underscore keys already stripped) this turn
    changed."""
    diff = {}
    for k in sorted(set(before) | set(after)):
        if before.get(k) != after.get(k):
            diff[k] = {"before": before.get(k), "after": after.get(k)}
    return diff


def _delay_s(earlier_iso, later_iso):
    if not earlier_iso or not later_iso:
        return None
    return (datetime.fromisoformat(later_iso) - datetime.fromisoformat(earlier_iso)).total_seconds()


def _git_sha(repo_dir=_REPO_ROOT):
    """HEAD commit of ``repo_dir`` (INSTRUMENTATION). Raises rather than write a run that cannot be
    matched back to the code that produced it."""
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_dir), capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git rev-parse HEAD failed in {repo_dir}: {out.stderr.strip()}")
    return out.stdout.strip()


def _pending_turn_work(rows_tail):
    """True when the unconsumed ``rows_tail`` still holds an INBOUND row outside ``NO_TURN_TYPES``, i.e.
    conversation a further turn would answer. ``truncated`` means exactly this: a tail of only
    reaction/unsupported rows or bare outbound rows would never drive another turn."""
    return any(r["direction"] == "inbound" and r["message_type"] not in NO_TURN_TYPES for r in rows_tail)


def _run_turn(conn, t, candidate_id, turn_idx, burst, git_sha, client=None):
    """One brain turn for ``burst`` (a non-empty list of {"text","kind","at","media_placeholder",
    "wamid"} items, oldest first). -> the JSON line's fields up to, not including, ``actual_reply``,
    which the caller adds once it knows what really followed. Never raises: a brain exception is caught
    and recorded as ``error`` (loud, not skipped), leaving ``t`` as it was before the call (``turn()``
    works on its own copy of the card and returns it only on success)."""
    phone = t["phone"]
    prior_card = dict(t.get("slots") or {})
    prior_slots = _strip_underscore(prior_card)
    prior_context_len = ST.last_message_id(conn, phone)
    line = {"candidate_id": candidate_id, "turn": turn_idx,
            "turn_kind": "burst" if len(burst) > 1 else "single",
            "inbound": [{"text": m["text"], "kind": m["kind"], "at": m["at"]} for m in burst],
            "media_placeholder": any(m["media_placeholder"] for m in burst),
            "prior_context_len": prior_context_len,
            "model": C.LUNA_MODEL, "effort": C.LUNA_EFFORT, "git_sha": git_sha,
            "timings": None, "tokens": None,
            "instrumentation_note": "luna_brain.turn() returns neither per-stage timings nor token "
                                    "counts (module docstring, INSTRUMENTATION)"}

    if t.get("stopped"):
        line["skipped_reason"] = "stopped"
        return line

    # CUT AT TURN N: a fresh, memoryless session every turn. Dropped BEFORE turn_context, whose
    # no-marker branch reads the card's _session_id.
    t["slots"].pop("_session_id", None)

    text = "\n".join(m["text"] for m in burst)
    turn_key = burst[-1]["wamid"]
    try:
        t["turn_context"] = LB.turn_context(conn, t, turn_key)
        d = LB.turn(text, t, button_id=None, client=client, no_send=True)
    except Exception as exc:
        line["error"] = f"{type(exc).__name__}: {exc}"
        return line
    finally:
        t.pop("turn_context", None)

    t["slots"], t["asked"] = d.get("slots", t["slots"]), d.get("asked", t["asked"])
    if d.get("luna_turn"):
        t["slots"][LB.LAST_TURN_KEY] = LB.turn_marker(conn, phone, d["luna_turn"], d["action"])
    if d.get("document_reuse"):
        LB.apply_document_reuse(conn, t, d["document_reuse"])
    if d.get("stopped"):
        t["stopped"], t["stopped_reason"] = True, ST.STOPPED

    new_card = t.get("slots") or {}
    line["luna"] = {"bubbles": d.get("bubbles") or [], "buttons": d.get("buttons") or [],
                    "action": d.get("action"), "stopped": bool(d.get("stopped")),
                    "escalation": _escalation_diff(prior_card, new_card),
                    "slots_diff": _diff_slots(prior_slots, _strip_underscore(new_card))}
    return line


def _unchosen_turn_line(conn, t, candidate_id, turn_idx, burst, git_sha):
    """The JSONL line of a turn ``at_turns`` did not choose: what ``_run_turn`` records before the model runs,
    plus ``skipped_reason``. No brain call, the card and the thread are untouched (AT TURNS)."""
    return {"candidate_id": candidate_id, "turn": turn_idx,
            "turn_kind": "burst" if len(burst) > 1 else "single",
            "inbound": [{"text": m["text"], "kind": m["kind"], "at": m["at"]} for m in burst],
            "media_placeholder": any(m["media_placeholder"] for m in burst),
            "prior_context_len": ST.last_message_id(conn, t["phone"]),
            "model": C.LUNA_MODEL, "effort": C.LUNA_EFFORT, "git_sha": git_sha,
            "skipped_reason": "not_in_at_turns"}


def _record_row(conn, phone, candidate_id, row, text, meta=None):
    """Write one sales_brain row into the scratch store with its historical ``occurred_at``."""
    record = ST.record_inbound if row["direction"] == "inbound" else ST.record_outbound
    record(conn, phone, _wamid(candidate_id, row["id"]), text, kind=row["message_type"],
           meta=meta or _meta(row), at=row["occurred_at"])


def replay_candidate(candidate_id, out_dir, max_turns=None, sales_brain_path=None, client=None, at_turns=None):
    """Replay one candidate's whole recorded history. -> {"candidate_id", "turns_run", "truncated",
    "errors", "jsonl_path", "sqlite_path"}. ``turns_run`` counts the turns walked; with ``at_turns`` the brain
    ran on the chosen ones only (AT TURNS in the module docstring).

    ``errors`` counts the turns whose JSONL line carries ``"error"``. The run never stops on one (it
    continues, as a real restart would); the caller must still be able to tell a clean run from a broken
    one without opening the JSONL, and ``tools/wa_replay.py`` turns the count into a nonzero exit.

    The scratch SQLite (``<out_dir>/<candidate_id>.sqlite``, always fresh) and the JSONL report
    (``<out_dir>/<candidate_id>.jsonl``, overwritten) go under the RESOLVED ``out_dir``, and so do
    ``config.SQLITE_PATH`` and ``config.LUNA_SESSION_DIR`` (ISOLATION). ``max_turns``: stop after this many
    turns; the last line is then ``{"truncated": true, ...}``, written only when an unconsumed inbound row
    outside ``NO_TURN_TYPES`` remains (``_pending_turn_work``), never at the natural end of history."""
    sales_brain_path = sales_brain_path or C.sales_brain_path()
    rows = fetch_rows(candidate_id, sales_brain_path)
    if not rows:
        raise RuntimeError(f"no sales_brain rows for candidate_id={candidate_id!r} in {sales_brain_path!r}")
    git_sha = _git_sha()
    if at_turns is not None and max_turns is not None:
        raise ValueError("at_turns and max_turns are exclusive")
    at = None if at_turns is None else frozenset(int(n) for n in at_turns)
    stop_after = max(at) if at else max_turns   # an empty ``at`` is a listing: no model turn, no early stop

    out_dir = pathlib.Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path = out_dir / f"{candidate_id}.sqlite"
    jsonl_path = out_dir / f"{candidate_id}.jsonl"
    _unlink_sqlite(sqlite_path)

    # Read at call time by ST.db() and luna_brain (ISOLATION): never data/wa.sqlite or wa_luna_sessions/.
    C.SQLITE_PATH = sqlite_path
    C.LUNA_SESSION_DIR = out_dir / "sessions"
    phone = synthetic_phone(candidate_id)
    conn = ST.db()
    t = ST.thread(conn, phone)

    turn_idx = 0
    truncated = False
    n_errors = 0
    burst = []
    n = len(rows)
    i = 0
    with jsonl_path.open("w", encoding="utf-8") as fh:
        while i < n:
            row = rows[i]
            text, was_placeholder = _display_text(row)

            if row["message_type"] in NO_TURN_TYPES:
                _record_row(conn, phone, candidate_id, row, text)
                i += 1
                continue

            if row["direction"] == "inbound":
                _record_row(conn, phone, candidate_id, row, text)
                burst.append({"text": text, "kind": row["message_type"], "at": row["occurred_at"],
                              "media_placeholder": was_placeholder and row["message_type"] in MEDIA_TYPES,
                              "wamid": _wamid(candidate_id, row["id"])})
                i += 1
                continue

            # Outbound, not a reaction/unsupported row.
            if not burst:
                # Not anyone's answer to a turn (e.g. the cold-open template before any reply exists).
                is_cold_open_template = turn_idx == 0 and row["message_type"] == "template"
                _record_row(conn, phone, candidate_id, row, text,
                            meta={**_meta(row), "action": "campaign"} if is_cold_open_template else None)
                if is_cold_open_template:
                    # COLD OPEN: only what sales_brain gives, nothing invented.
                    t["slots"]["campaign"] = {"campaign_id": None, "template_name": None, "language": None,
                                              "rendered_text": text, "buttons": [],
                                              "sent_at": row["occurred_at"],
                                              "wamid": _wamid(candidate_id, row["id"])}
                i += 1
                continue

            turn_idx += 1
            if at is not None and turn_idx not in at:
                line = _unchosen_turn_line(conn, t, candidate_id, turn_idx, burst, git_sha)
            else:
                line = _run_turn(conn, t, candidate_id, turn_idx, burst, git_sha, client=client)
                if "error" in line:
                    n_errors += 1
            last_inbound_at = burst[-1]["at"]
            burst = []

            # ACTUAL REPLY: outbound rows, plus inbound reaction/unsupported rows between them (recorded,
            # they do not end it). A real inbound row starts the next burst and ends it.
            actual_reply = []
            while i < n and (rows[i]["direction"] == "outbound" or rows[i]["message_type"] in NO_TURN_TYPES):
                r2 = rows[i]
                t2, _ = _display_text(r2)
                _record_row(conn, phone, candidate_id, r2, t2)
                if r2["direction"] == "outbound" and r2["message_type"] not in NO_TURN_TYPES:
                    actual_reply.append({"body": t2, "kind": r2["message_type"], "at": r2["occurred_at"]})
                i += 1
            line["actual_reply"] = actual_reply
            line["actual_reply_delay_s"] = _delay_s(last_inbound_at,
                                                    actual_reply[0]["at"] if actual_reply else None)
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            ST.save_thread(conn, t)

            if stop_after is not None and turn_idx >= stop_after and _pending_turn_work(rows[i:]):
                truncated = True
                break

        if not truncated and burst:
            # A trailing burst at the end of recorded history: a real turn with nothing to compare to.
            if stop_after is not None and turn_idx >= stop_after:
                truncated = True
            else:
                turn_idx += 1
                if at is not None and turn_idx not in at:
                    line = _unchosen_turn_line(conn, t, candidate_id, turn_idx, burst, git_sha)
                else:
                    line = _run_turn(conn, t, candidate_id, turn_idx, burst, git_sha, client=client)
                    if "error" in line:
                        n_errors += 1
                line["actual_reply"] = []
                line["actual_reply_delay_s"] = None
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
                ST.save_thread(conn, t)

        if truncated:
            fh.write(json.dumps({"candidate_id": candidate_id, "truncated": True,
                                 "turns_run": turn_idx}, ensure_ascii=False) + "\n")

    conn.close()
    missing = sorted(at - set(range(1, turn_idx + 1))) if at else []
    if missing:
        raise RuntimeError(f"at_turns {missing} are past the last turn of candidate_id={candidate_id!r} "
                           f"({turn_idx} turn(s)); the JSONL up to there is at {jsonl_path}")
    return {"candidate_id": candidate_id, "turns_run": turn_idx, "truncated": truncated,
            "errors": n_errors, "jsonl_path": str(jsonl_path), "sqlite_path": str(sqlite_path)}
