"""Replay a real WhatsApp thread through the Luna brain, cut at turn N, nothing sent (TASK-313; Ivan,
2026-10-06: run our reply bot over the chat histories and look at the gap to what was actually answered).
For every turn: what Luna says now next to what really followed in the chat.

SOURCE. The colleague's CRM, ``candidate_whatsapp_messages`` in sales_brain.sqlite (old bot and humans,
not this harness). Opened ``mode=ro`` only (``fetch_rows``/``list_candidates``/``fetch_attachment``). Columns
read: id, direction, message_type, body, caption, media_filename, attachment_id, occurred_at; of
``candidate_attachments`` only id, storage_path, sha256, mime_type. NEVER ``provider_metadata_json`` (the
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
conversation is left). A chosen number past the thread's last turn is a loud error, after the file is
written. Exclusive with ``max_turns``. On its own the card is advanced only by turns that really run, so a
chosen turn after skipped ones sees a card they did not update: CAPTURE and SEEDS below are the cure.

CAPTURE AND SEEDS (one-time preparation, then free). ``capture_before={T, ...}``: the walk runs the REAL
brain on every turn before the last chosen one and, if ``media_roots`` is given, reads every inbound
document/image the thread held (FILES). Right before the brain would run turn T, AFTER T's own inbound rows
and files are recorded and ingested (exactly the state the brain sees), it captures a seed and goes on; it
stops at the last chosen turn WITHOUT running it. ``result["seeds"]`` is ``{T: seed}``, JSON-serialisable,
one object per turn: ``slots`` (the whole card, underscore keys included: ``_session_id``, the LAST_TURN_KEY
marker, ``_documents_just_received`` ... nothing is dropped; ``_run_turn`` drops ``_session_id`` itself),
``asked``, ``stopped``/``stopped_reason``, ``documents`` (every wa_documents row of the scratch phone, all
columns: text, classification, path) and ``files`` (the turn's file entries, FILES). ``seeds={T: seed}`` is the
run side: the walk records every earlier row as plain history (no model call, no file read), and right
before turn T replaces the card, ``asked`` and stop flags with the seed, replaces the phone's wa_documents
rows with the seed's (explicit ids, the card refers to them; each ``path`` points at the read-only source
file) and runs the brain on T. A seed is only applied to a turn in ``at_turns``; a seed whose turn the
walk never reaches is a loud error. A chosen turn without a seed behaves as before. Capture is exclusive
with ``at_turns``/``max_turns``/``seeds``. The ids in a seed's card (``LAST_TURN_KEY`` marker, documents)
hold in the seed run because both walks record the same rows in the same order.

FILES. ``media_roots`` (a list of directories this user can read) turns file handling on. An inbound
document/image row resolves like ``import_history.resolve_path``: ``candidate_attachments.storage_path``
under the roots; when not there, any OTHER attachment row with the same sha256 whose file is on a root. No readable file (no
attachment row, not on a given root) means UNAVAILABLE: the row keeps its placeholder text and the turn's
JSONL line carries ``file_unavailable: true`` plus ``files``, one entry per file row
``{source_id, kind, status, reason, found_via, document_id, document_type, certificate_level}`` (status
``attached``/``unavailable``/``not_read``; no name, no path). A voice note (audio row, or a document with an
audio mime type) is never read here (no STT): its old transcript in ``body`` is the text, without one it is
an unavailable entry. A root this user cannot read is a loud error, not a skip: give only roots you can read.
A file whose bytes do not match the recorded sha256 is a loud error. In capture mode the file is read
(read-only on the source), stored as a wa_documents row (``ST.record_document``, ``path`` = the source file,
nothing copied) and run through ``api._ingest_media``, the function a live media message goes through:
real ``read_and_classify``, card slots updated the same way, ``_documents_just_received`` set. The brain
then gets the turn text a live media message gets: nothing (``""``) for an attached file, the file's
placeholder otherwise. A file that fails to read or classify raises (the capture is not a seed then).
Without ``media_roots`` nothing of this happens (placeholders, as before). With roots but without a seed, a
chosen turn that holds a readable file is a loud error: the file must go through a capture.

TURNS. One turn per inbound burst, as ``app/wa/luna/catchup.py``'s owed pass does it (not one per
message, as the webhook path does). Consecutive inbound rows accumulate and are joined with ``"\\n"`` into
the turn's text; the first real outbound row after them is where production would have answered, so
``turn_context`` + ``turn`` run right before that row is inserted. Each JSONL line says
``turn_kind``: ``"single"`` (one inbound message) or ``"burst"`` (more). A trailing burst with no outbound
after it still gets a turn; its ``actual_reply`` is empty. A STOP turn ends the thread as ``app/wa/api.py``
does: every later burst is a ``skipped_reason: "stopped"`` line (rows still recorded, brain not called).

ROWS. Inbound text/button/interactive rows are recorded as given. Media (document/image/audio) with an
empty body gets a bracketed placeholder and the turn carries ``media_placeholder: true`` (the placeholder is
also what the row records when its file is attached: FILES). A
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
  - a file the CRM holds no readable copy of (FILES) is a placeholder: any document-gated rule runs
    against that, and the turn says so (``file_unavailable``); voice notes are never read;
  - every candidate starts from FRESH state (no card, no session): a history that began mid-relationship
    (an import, a resumed campaign) replays as a first contact;
  - every board tool call (search_postings, get_posting, market_snapshot, ...) answers from the board as
    it is NOW, not on the replayed date;
  - the card carries forward whatever Luna's own turn wrote to it (``card_patch``, warming stamps,
    funnel stage), not what the real replies imply; later turns read that card (a seed is such a card);
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

import copy
import hashlib
import json
import pathlib
import sqlite3
import subprocess
from datetime import datetime

from .. import config as C
from .. import store as ST
from .. import luna_brain as LB
from .import_history import resolve_path

#: message_type values that are recorded but never drive turn logic in either direction (ROWS).
NO_TURN_TYPES = frozenset({"reaction", "unsupported"})
#: Kinds whose empty body is a real file this replay cannot see (ROWS, KNOWN LIMITS).
MEDIA_TYPES = frozenset({"document", "image", "audio"})
#: Inbound kinds ``read_and_classify`` can read: the rows FILES resolves a file for.
FILE_KINDS = frozenset({"document", "image"})

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
            "select id, direction, message_type, body, caption, media_filename, attachment_id, occurred_at "
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


# --- files (FILES in the module docstring) -------------------------------------------------------------

def _attachment_query(sales_brain_path, sql, params):
    conn = sqlite3.connect(f"file:{sales_brain_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def fetch_attachment(attachment_id, sales_brain_path):
    """-> {id, storage_path, sha256, mime_type} of one ``candidate_attachments`` row, or None. Read-only; no
    other column is read."""
    rows = _attachment_query(sales_brain_path, "select id, storage_path, sha256, mime_type "
                             "from candidate_attachments where id=?", (attachment_id,))
    return rows[0] if rows else None


def fetch_attachments_by_sha(sha256, sales_brain_path, exclude_id):
    """-> the OTHER attachment rows holding the same bytes (same sha256), same columns, oldest id first."""
    return _attachment_query(sales_brain_path, "select id, storage_path, sha256, mime_type from "
                             "candidate_attachments where sha256=? and id<>? order by id", (sha256, exclude_id))


def _wants_file_entry(row):
    """True for an inbound row FILES reports on: every document/image, and a voice note without a transcript."""
    if row["direction"] != "inbound":
        return False
    return row["message_type"] in FILE_KINDS or (row["message_type"] == "audio" and not (row["body"] or "").strip())


def _file_entry(row, status, reason=None, found_via=None):
    return {"source_id": row["id"], "kind": row["message_type"], "status": status, "reason": reason,
            "found_via": found_via, "document_id": None, "document_type": None, "certificate_level": None}


def _locate_row_file(row, sales_brain_path, media_roots):
    """-> (entry, located). ``located`` is {path, sha256, mime_type} when a readable file exists (entry status
    ``found``, which the caller turns into ``attached`` or ``not_read``), else None with an ``unavailable``
    entry that says why. Opens nothing but the attachment tables, read-only."""
    if row["message_type"] == "audio":
        return _file_entry(row, "unavailable", "voice_note_not_transcribed"), None
    if row["attachment_id"] is None:
        return _file_entry(row, "unavailable", "no_attachment_row"), None
    att = fetch_attachment(row["attachment_id"], sales_brain_path)
    if att is None:
        return _file_entry(row, "unavailable", "attachment_row_missing"), None
    if (att["mime_type"] or "").strip().lower().startswith("audio/"):
        return _file_entry(row, "unavailable", "voice_note_not_transcribed"), None
    roots = [pathlib.Path(r).resolve() for r in media_roots]
    path, via = resolve_path(att["storage_path"], roots), "own_path"
    if path is None:
        for alt in fetch_attachments_by_sha(att["sha256"], sales_brain_path, att["id"]):
            path = resolve_path(alt["storage_path"], roots)
            if path is not None:
                via = "same_sha256"
                break
    if path is None:
        return _file_entry(row, "unavailable", "not_on_a_readable_root"), None
    return _file_entry(row, "found", found_via=via), {"path": path, "sha256": att["sha256"],
                                                      "mime_type": att["mime_type"]}


def _ingest_file(conn, t, candidate_id, row, located, entry):
    """Capture mode: read the file (read-only), store it as a wa_documents row and run it through the live
    media path (``api._ingest_media``: ``read_and_classify``, card slots, ``_documents_just_received``).
    Fills ``entry`` in place. Raises on a sha256 mismatch and on every read or classification failure."""
    from .. import api as WAPI   # imported lazily: api pulls in FastAPI
    blob = pathlib.Path(located["path"]).read_bytes()
    sha256 = hashlib.sha256(blob).hexdigest()
    if sha256 != located["sha256"]:
        raise RuntimeError(f"file of source row {row['id']} (candidate_id={candidate_id}) does not match the "
                           f"sha256 sales_brain records for it")
    # A live image message carries no filename (api: ``media.get("filename") or None``); the old system stored
    # every image as "<id>.bin". Handed on, that ".bin" became the vision temp file's extension, which the
    # CLI's Read tool rejects, so the photo read hung until the timeout. Images go in as the live path sees them.
    filename = None if row["message_type"] == "image" else row["media_filename"]
    doc_id = ST.record_document(conn, t["phone"], _wamid(candidate_id, row["id"]), None, row["message_type"],
                                located["mime_type"], filename, str(located["path"]), sha256, len(blob))
    conn.execute("update wa_documents set received_at=? where id=?", (row["occurred_at"], doc_id))
    conn.commit()
    WAPI._ingest_media(conn, t, {"kind": row["message_type"], "media_filename": filename},
                       {"id": doc_id, "blob": blob, "mime_type": located["mime_type"]})
    ST.save_thread(conn, t)   # as the live path does, before the reply turn
    summary = t["slots"]["documents"][-1]
    entry.update(status="attached", document_id=doc_id, document_type=summary["document_type"],
                 certificate_level=summary["certificate_level"])


def _json_copy(obj):
    """A deep copy that proves ``obj`` is JSON-serialisable (loudly, never by dropping what is not)."""
    return json.loads(json.dumps(obj, ensure_ascii=False))


def _capture_seed(conn, t, burst):
    """The state the brain would see right now (CAPTURE AND SEEDS): the whole card, ``asked``, the stop
    flags, every wa_documents row of the phone, and this turn's file entries."""
    return _json_copy({"slots": t["slots"], "asked": t["asked"], "stopped": bool(t.get("stopped")),
                       "stopped_reason": t.get("stopped_reason"),
                       "documents": ST.documents_for(conn, t["phone"], include_deleted=True),
                       "files": [m["file"] for m in burst if m.get("file")]})


def _apply_seed(conn, t, seed):
    """Replace the card, ``asked``, the stop flags and the phone's wa_documents rows with ``seed``'s, and save
    the thread, so the brain and the MCP tools server see the captured state."""
    t["slots"], t["asked"] = copy.deepcopy(seed["slots"]), copy.deepcopy(seed["asked"])
    t["stopped"], t["stopped_reason"] = bool(seed["stopped"]), seed["stopped_reason"]
    known = {r[1] for r in conn.execute("pragma table_info(wa_documents)")}
    conn.execute("delete from wa_documents where phone=?", (t["phone"],))
    for doc in seed["documents"]:
        unknown = sorted(set(doc) - known)
        if unknown:
            raise RuntimeError(f"seed document row has columns wa_documents lacks: {unknown}")
        conn.execute(f"insert into wa_documents ({', '.join(doc)}) values ({', '.join('?' for _ in doc)})",
                     tuple(doc.values()))
    conn.commit()
    ST.save_thread(conn, t)


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
    """One brain turn for ``burst`` (a non-empty list of {"text","kind","at","media_placeholder","wamid",
    "turn_text","file","row"} items, oldest first). -> the JSON line's fields up to, not including,
    ``actual_reply``, which the caller adds once it knows what really followed. Never raises: a brain
    exception is caught and recorded as ``error`` (loud, not skipped), leaving ``t`` as it was before the call (``turn()``
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

    # An attached file contributes no text, as on the live path (a media message's text is empty).
    text = "\n".join(m["turn_text"] for m in burst if m["turn_text"])
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


def _seed_burst(burst, seed, turn_idx, candidate_id):
    """Seed mode: the turn's file entries come from the seed (no file is read) and an attached file gives
    no turn text. A seed that does not list exactly the files the turn holds is stale: loud."""
    entries = {f["source_id"]: f for f in seed["files"]}
    wanted = [m for m in burst if _wants_file_entry(m["row"])]
    if sorted(entries) != sorted(m["row"]["id"] for m in wanted):
        raise RuntimeError(f"seed of turn {turn_idx} of candidate_id={candidate_id!r} lists files "
                           f"{sorted(entries)} but the turn holds {sorted(m['row']['id'] for m in wanted)}: "
                           f"the seed is stale, prepare again")
    for m in wanted:
        m["file"] = entries[m["row"]["id"]]
        if m["file"]["status"] == "attached":
            m["turn_text"] = ""


def _check_unseeded_files(burst, turn_idx, candidate_id, sales_brain_path, media_roots):
    """A chosen turn with no seed runs on placeholders; that is only honest for a file nobody can read. A
    readable one must go through a capture first (FILES): loud."""
    for m in burst:
        if not _wants_file_entry(m["row"]):
            continue
        entry, located = _locate_row_file(m["row"], sales_brain_path, media_roots)
        if located is not None:
            raise RuntimeError(f"turn {turn_idx} of candidate_id={candidate_id!r} holds a readable file (source "
                               f"row {m['row']['id']}) and has no seed: the file is read once, in a preparation "
                               f"(evals/wa_brain/run.py --prepare)")
        m["file"] = entry


def _file_fields(line, burst):
    """FILES: the turn's file entries and the unavailable flag onto its JSONL line (nothing when it has none)."""
    files = [m["file"] for m in burst if m.get("file")]
    if files:
        line["files"] = files
        line["file_unavailable"] = any(f["status"] != "attached" for f in files)


def replay_candidate(candidate_id, out_dir, max_turns=None, sales_brain_path=None, client=None, at_turns=None,
                     capture_before=None, seeds=None, media_roots=None):
    """Replay one candidate's whole recorded history. -> {"candidate_id", "turns_run", "truncated",
    "errors", "jsonl_path", "sqlite_path"}, plus {"seeds", "git_sha", "model", "effort"} in capture mode.
    ``turns_run`` counts the turns walked; with ``at_turns`` the brain ran on the chosen ones only (AT TURNS),
    with ``capture_before`` on every one before the last chosen (CAPTURE AND SEEDS), ``seeds`` is the run side
    of that and ``media_roots`` turns FILES on.

    ``errors`` counts the turns whose JSONL line carries ``"error"``. The run never stops on one (it
    continues, as a real restart would); the caller must still be able to tell a clean run from a broken
    one without opening the JSONL, and ``tools/wa_replay.py`` turns the count into a nonzero exit. A capture
    with errors holds a card that lacks those turns: the caller must not keep its seeds.

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
    capture = None if capture_before is None else frozenset(int(n) for n in capture_before)
    seeds = {int(k): v for k, v in (seeds or {}).items()}
    if capture is not None:
        if not capture:
            raise ValueError("capture_before is empty")
        if at is not None or max_turns is not None or seeds:
            raise ValueError("capture_before is exclusive with at_turns, max_turns and seeds")
        if media_roots is None:
            raise ValueError("capture_before needs media_roots (a list of directories to read files from)")
    if seeds and not set(seeds) <= (at or frozenset()):
        raise ValueError(f"seeds for turns {sorted(set(seeds) - (at or frozenset()))} that are not in at_turns")
    stop_after = max(at) if at else max_turns   # an empty ``at`` is a listing: no model turn, no early stop
    last_capture = max(capture) if capture else None

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
    done = False          # a capture reached its last turn: nothing after it is walked
    n_errors = 0
    captured, applied = {}, set()
    burst = []
    n = len(rows)
    i = 0

    def do_turn():
        """Capture, seed, skip or run the burst just completed (``turn_idx`` already counts it). -> the JSONL
        line, ``actual_reply`` not yet added."""
        nonlocal n_errors, done
        if capture is not None:
            if turn_idx in capture:
                captured[turn_idx] = _capture_seed(conn, t, burst)
            if turn_idx == last_capture:
                done = True
                line = _unchosen_turn_line(conn, t, candidate_id, turn_idx, burst, git_sha)
                line["skipped_reason"] = "captured_not_run"
                _file_fields(line, burst)
                return line
        elif at is not None and turn_idx not in at:
            return _unchosen_turn_line(conn, t, candidate_id, turn_idx, burst, git_sha)
        elif turn_idx in seeds:
            _seed_burst(burst, seeds[turn_idx], turn_idx, candidate_id)
            _apply_seed(conn, t, seeds[turn_idx])
            applied.add(turn_idx)
        elif media_roots is not None:
            _check_unseeded_files(burst, turn_idx, candidate_id, sales_brain_path, media_roots)
        line = _run_turn(conn, t, candidate_id, turn_idx, burst, git_sha, client=client)
        _file_fields(line, burst)
        if "error" in line:
            n_errors += 1
        return line

    with jsonl_path.open("w", encoding="utf-8") as fh:
        while i < n and not done:
            row = rows[i]
            text, was_placeholder = _display_text(row)

            if row["message_type"] in NO_TURN_TYPES:
                _record_row(conn, phone, candidate_id, row, text)
                i += 1
                continue

            if row["direction"] == "inbound":
                _record_row(conn, phone, candidate_id, row, text)
                item = {"text": text, "kind": row["message_type"], "at": row["occurred_at"],
                        "media_placeholder": was_placeholder and row["message_type"] in MEDIA_TYPES,
                        "wamid": _wamid(candidate_id, row["id"]), "turn_text": text, "file": None, "row": row}
                if capture is not None and _wants_file_entry(row):
                    entry, located = _locate_row_file(row, sales_brain_path, media_roots)
                    if located is not None and t.get("stopped"):
                        entry.update(status="not_read", reason="thread_stopped")
                    elif located is not None:
                        _ingest_file(conn, t, candidate_id, row, located, entry)
                    if entry["status"] == "attached":
                        item["turn_text"] = ""
                    item["file"] = entry
                burst.append(item)
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
            line = do_turn()
            if done:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
                break
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

        if not truncated and not done and burst:
            # A trailing burst at the end of recorded history: a real turn with nothing to compare to.
            if stop_after is not None and turn_idx >= stop_after:
                truncated = True
            else:
                turn_idx += 1
                line = do_turn()
                if not done:
                    line["actual_reply"] = []
                    line["actual_reply_delay_s"] = None
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
                ST.save_thread(conn, t)

        if truncated:
            fh.write(json.dumps({"candidate_id": candidate_id, "truncated": True,
                                 "turns_run": turn_idx}, ensure_ascii=False) + "\n")

    conn.close()
    missing = sorted((at or capture or frozenset()) - set(range(1, turn_idx + 1)))
    if missing:
        kind = "capture_before" if capture else "at_turns"
        raise RuntimeError(f"{kind} {missing} are past the last turn of candidate_id={candidate_id!r} "
                           f"({turn_idx} turn(s)); the JSONL up to there is at {jsonl_path}")
    if set(seeds) - applied:
        raise RuntimeError(f"seeds for turns {sorted(set(seeds) - applied)} of candidate_id={candidate_id!r} "
                           f"were never applied: the walk did not reach them")
    result = {"candidate_id": candidate_id, "turns_run": turn_idx, "truncated": truncated,
              "errors": n_errors, "jsonl_path": str(jsonl_path), "sqlite_path": str(sqlite_path)}
    if capture is not None:
        result.update(seeds=captured, git_sha=git_sha, model=C.LUNA_MODEL, effort=C.LUNA_EFFORT)
    return result
