"""Wipe the history of a test number (TASK-109), so every manual test starts from nothing.

Usage:
    python -m app.wa.luna.purge_test_history [--older-than-hours N] [--phones p1,p2] [--json]   # dry run
    python -m app.wa.luna.purge_test_history --apply [--older-than-hours N] [--phones p1,p2]

Load .env first (set -a; . ./.env; set +a): database and document paths. Runs on a timer
(deploy/pflege-wa-purge-test.service|.timer, daily 03:00 Europe/Berlin, --older-than-hours 0 --apply).

WHAT IT TOUCHES. Only threads with ``wa_threads.is_test`` (set by app/wa/luna/test_threads.py). A phone named
with --phones that is not marked is a problem, reported and exit 1 -- never a wipe. Per test thread:

- messages (wa_messages) and imported prior messages (wa_imported_messages), delivery statuses
  (wa_message_statuses), raw webhook objects (wa_webhook_events), unfinished inbound work (wa_inbound_pending);
- model call records (wa_luna_calls), send failures (wa_send_failures), follow-up nudges (wa_followups_sent),
  reply-turn and nudge claims (wa_reply_turn_claims, wa_nudge_claims);
- campaign send rows (wa_campaign_sends) and the consent-queue entry (wa_queue_candidates, wa_queue_matches);
- stored documents: the wa_documents rows AND their files under C.DOCUMENTS_DIR, then the phone's own document
  directory when it is empty;
- the Claude Code session transcript of the card's ``_session_id`` (C.LUNA_SESSION_STORE) -- it holds the whole
  conversation in plain text, so clearing the card alone would leave the test's PII on disk;
- the card itself: slots, asked, matches_sent_at, turns, last_inbound_at/last_outbound_at, and ``stopped``
  (a Stopp typed during a test must not silence the next one).

WHAT SURVIVES. The wa_threads row itself, still marked as a test number (is_test, test_marked_at) and not
stopped, and the ownership record (wa_ownership): deleting that would hand the next test message back to the
old system instead of this harness. Nothing of any other phone: every delete is keyed by this phone, and a
document file is only ever unlinked when its wa_documents row names it and the path is inside C.DOCUMENTS_DIR
(a row pointing outside it fails that phone loudly instead -- nothing of it is deleted).

RETENTION. ``--older-than-hours 0`` (the default, Ivan 2026-09-16) wipes every test thread. With N > 0 a test
thread whose last activity (its own messages, documents, last inbound/outbound) is younger than N hours is left
alone completely -- a half-wiped thread whose card outlives the messages it was built from is worse than a full
one. A phone with a turn in flight (ST.claim_in_flight) is skipped the same way: the webhook worker or the
catch-up driver is holding the card right now, and it would write it back after the wipe. That check reads
inside the wipe's own ``begin immediate`` transaction (_wipe_transaction), so no other process can take the
claim between the check and the deletes.

DRY RUN is the default: it reads a read-only in-memory copy of the database (shadow_run.db_copy), so it cannot
write a row even by accident, and it deletes no file. It reports exactly what --apply would delete.
"""
import argparse
import json
import os
import pathlib
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .. import config as C
from .. import store as ST
from . import shadow_run as SR
from . import test_threads as TT

# Every table this harness keys by phone. The card (wa_threads) is reset, not deleted; wa_ownership is
# deliberately absent (see the module docstring).
CORE_TABLES = ("wa_messages", "wa_imported_messages", "wa_message_statuses", "wa_webhook_events",
               "wa_inbound_pending", "wa_reply_turn_claims", "wa_nudge_claims", "wa_luna_calls",
               "wa_send_failures", "wa_followups_sent", "wa_documents")
# Created by the module that first uses them (store.ensure_campaign_schema, queue.db), not by store.SCHEMA:
# on a database where no campaign ever ran and nobody ever consented they do not exist at all.
OPTIONAL_TABLES = ("wa_campaign_sends", "wa_queue_candidates", "wa_queue_matches")
TABLES = CORE_TABLES + OPTIONAL_TABLES


def _table_exists(c, table):
    return c.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone() is not None


def _tables(c):
    return [t for t in TABLES if t in CORE_TABLES or _table_exists(c, t)]


def _row_counts(c, phone):
    """{table: rows} for this phone, empty tables left out."""
    out = {}
    for table in _tables(c):
        n = c.execute(f"select count(*) as n from {table} where phone=?", (phone,)).fetchone()["n"]
        if n:
            out[table] = n
    unkeyed = len(_unkeyed_webhook_events(c, phone))
    if unkeyed:
        out["wa_webhook_events (phone null, number in payload)"] = unkeyed
    return out


def _unkeyed_webhook_events(c, phone):
    """-> ids of wa_webhook_events rows this harness could not key to a phone (an unparsed change shape, a
    value key that carries no phone) whose raw payload still holds this number: app/wa/api.py stores those
    with phone NULL, so the phone-keyed deletes leave the number on disk (final-verifier finding,
    2026-09-16). Matched on the bare digits, the form Meta uses inside a payload."""
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return []
    rows = c.execute("select id from wa_webhook_events where phone is null and raw like ?",
                     (f"%{digits}%",)).fetchall()
    return [r["id"] for r in rows]


def _delete_unkeyed_webhook_events(c, phone):
    """-> {label: rows} for the report, after deleting them."""
    ids = _unkeyed_webhook_events(c, phone)
    if not ids:
        return {}
    c.execute(f"delete from wa_webhook_events where id in ({','.join('?' * len(ids))})", ids)
    return {"wa_webhook_events (phone null, number in payload)": len(ids)}


def last_activity(c, phone):
    """The latest UTC ISO timestamp this thread produced (its own messages, stored documents, last
    inbound/outbound), or None for a thread that never did anything. Imported prior messages are not
    activity: they carry the old system's timestamps and describe a conversation that happened elsewhere."""
    t = ST.thread(c, phone)
    stamps = [t.get("last_inbound_at"), t.get("last_outbound_at"),
              c.execute("select max(at) as at from wa_messages where phone=?", (phone,)).fetchone()["at"],
              c.execute("select max(received_at) as at from wa_documents where phone=?", (phone,)).fetchone()["at"]]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def session_files(session_id):
    """Every Claude Code transcript of this session id under C.LUNA_SESSION_STORE, oldest path first.

    The CLI stores a session as ``<store>/<directory name derived from the cwd it started in>/<id>.jsonl``.
    That name is the CLI's own encoding of the path, not a documented contract, so this globs for the id
    instead of recomputing the name: a session id is a uuid4 the harness itself generated for exactly one
    phone, so any transcript carrying it is that phone's conversation, whatever the directory is called."""
    if not session_id:
        return []
    root = pathlib.Path(C.LUNA_SESSION_STORE)
    if not root.is_dir():
        return []
    return sorted(root.glob(f"*/{session_id}.jsonl"))


def _document_plan(c, phone):
    """-> ([{doc_id, path, exists}], [problems]). A stored path outside C.DOCUMENTS_DIR is a problem: this
    job unlinks files, and the only files it may unlink are the ones this harness itself wrote."""
    root = pathlib.Path(C.DOCUMENTS_DIR).absolute()
    files, problems = [], []
    for row in c.execute("select id, path from wa_documents where phone=? order by id", (phone,)).fetchall():
        path = pathlib.Path(row["path"]).absolute()
        if not path.is_relative_to(root):
            problems.append(f"wa_documents {row['id']} path {row['path']} is outside {root}: not deleted")
            continue
        files.append({"doc_id": row["id"], "path": str(path), "exists": path.exists()})
    return files, problems


def _delete_files(files):
    """Unlink every planned file. A file already gone is recorded, not an error: its row is deleted in the
    same run, so a re-run after a crash between the unlink and the commit finishes the job."""
    for f in files:
        try:
            os.unlink(f["path"])
            f["deleted"] = True
        except FileNotFoundError:
            f["deleted"], f["reason"] = False, "already gone"


def _prune_dirs(files):
    """Remove the directories the deleted files sat in (api._write_original gives each phone its own),
    read off those files rather than rebuilt from the phone number, so this never has to guess a layout.
    Empty directories only: one that still holds something -- another phone's file, a file no row names --
    stays and says what is left. -> [{path, removed, left?}]."""
    root = pathlib.Path(C.DOCUMENTS_DIR).absolute()
    out = []
    for folder in sorted({pathlib.Path(f["path"]).parent for f in files}):
        if folder == root or not folder.is_relative_to(root) or not folder.is_dir():
            continue
        rest = sorted(p.name for p in folder.iterdir())
        if rest:
            out.append({"path": str(folder), "removed": False, "left": rest})
            continue
        folder.rmdir()
        out.append({"path": str(folder), "removed": True})
    return out


@contextmanager
def _wipe_transaction(c, apply, out):
    """The per-phone wipe's write transaction. ``begin immediate`` takes SQLite's write lock BEFORE the
    in-flight check reads the claims, so no other process (this purge is its own systemd unit, running next
    to pflege-wa.service and the catch-up timer -- ST._lock is in-process only) can claim a turn between
    that check and the deletes and then save the pre-wipe card back over the wipe. Commits when the wipe
    ran (``out["wiped"]``), rolls back otherwise and on any exception. The dry run takes no lock: it reads
    a read-only copy of the database."""
    if not apply:
        yield
        return
    c.commit()
    c.execute("begin immediate")
    try:
        yield
    except BaseException:
        c.rollback()
        raise
    c.commit() if out["wiped"] else c.rollback()


def purge_thread(c, phone, apply=False, cutoff=None):
    """Wipe one test thread. -> a report row; ``wiped`` says whether anything was (or would be) deleted.
    ``cutoff`` (UTC ISO) leaves a thread whose last activity is newer than it completely untouched.
    ``apply=False`` only reads -- the caller passes a read-only copy of the database then."""
    out = {"phone": phone, "wiped": False, "reason": None, "last_activity_at": None, "deleted": {},
           "documents": [], "directories": [], "session": None, "problems": []}
    if not ST.is_test_thread(c, phone):
        out["reason"] = "not a test number"
        out["problems"].append(f"{phone} is not marked as a test number (app/wa/luna/test_threads.py --mark)")
        return out
    out["last_activity_at"] = last_activity(c, phone)
    if cutoff and out["last_activity_at"] and out["last_activity_at"] > cutoff:
        out["reason"] = f"last activity {out['last_activity_at']} is newer than {cutoff}"
        return out

    with _wipe_transaction(c, apply, out):
        if ST.claim_in_flight(c, phone):
            # Read inside the write transaction: a turn claimed a moment ago still holds this card and would
            # save it back after the wipe. No claim can appear between this check and the deletes.
            out["reason"] = "a turn is in flight: the card is being written right now"
            return out

        files, problems = _document_plan(c, phone)
        if problems:
            out["reason"], out["problems"] = "stored document outside the documents directory", problems
            return out
        card = ST.thread(c, phone)["slots"]
        out["deleted"] = _row_counts(c, phone)
        out["documents"] = files
        out["session"] = {"session_id": card.get("_session_id"), "store": str(C.LUNA_SESSION_STORE),
                          "transcripts": [str(p) for p in session_files(card.get("_session_id"))]}
        if card.get("_session_id") and not out["session"]["transcripts"]:
            # The card names a session but its transcript is not under this store: the conversation stays
            # readable on disk somewhere else. Reported (exit 1), the rest of the wipe still runs -- the usual
            # cause is CLAUDE_CONFIG_DIR not being the one the `claude` CLI runs the Luna turns with.
            out["problems"].append(f"session {card['_session_id']} has no transcript under {C.LUNA_SESSION_STORE}: "
                                   f"nothing deleted for it")
        out["wiped"] = True
        if not apply:
            return out

        # Files first, rows second: a crash in between leaves the rows that name the already-gone files, so
        # the next run finishes the job ("already gone"). The other order would orphan the files. The session
        # transcript is unlinked here too, not after the commit: the card reset below erases _session_id, the
        # only pointer to it, so a crash in that window would leave the whole conversation readable on disk
        # with nothing left naming it (final-verifier finding, 2026-09-16).
        _delete_files(files)
        for path in out["session"]["transcripts"]:
            os.unlink(path)
        for table in _tables(c):
            c.execute(f"delete from {table} where phone=?", (phone,))
        out["deleted"].update(_delete_unkeyed_webhook_events(c, phone))
        c.execute("""update wa_threads set slots='{}', asked='[]', matches_sent_at=null, stopped=0,
                     stopped_reason=null, turns=0, last_inbound_at=null, last_outbound_at=null where phone=?""",
                  (phone,))
    out["directories"] = _prune_dirs(files)
    return out


@contextmanager
def _connection(apply):
    """The live database for --apply; a read-only in-memory copy of it for a dry run, so a dry run
    cannot write a row even by accident (the same copy the campaign dry run plans from)."""
    if apply:
        with ST._lock, ST.db() as c:
            yield c
    else:
        with SR.db_copy() as c:
            yield c


def run(older_than_hours=0, apply=False, phones=None):
    """Purge every test thread (or just ``phones``, each of which must be one). -> {apply, older_than_hours,
    cutoff, phones: [report rows], problems: int}."""
    cutoff = None
    if older_than_hours:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).replace(
            microsecond=0).isoformat()
    out = {"apply": apply, "older_than_hours": older_than_hours, "cutoff": cutoff, "phones": []}
    with _connection(apply) as c:
        targets = phones if phones is not None else ST.test_phones(c)
        out["phones"] = [purge_thread(c, p, apply=apply, cutoff=cutoff) for p in targets]
    out["problems"] = sum(len(p["problems"]) for p in out["phones"])
    return out


def _print(report):
    mode = "wiped" if report["apply"] else "would be wiped (dry run, nothing was deleted)"
    print(f"{sum(1 for p in report['phones'] if p['wiped'])} of {len(report['phones'])} test thread(s) {mode}"
          + (f", retention {report['older_than_hours']}h (cutoff {report['cutoff']})" if report["cutoff"]
             else ", full wipe"))
    for p in report["phones"]:
        if not p["wiped"]:
            print(f"  {p['phone']}: skipped -- {p['reason']}")
        else:
            rows = ", ".join(f"{t} {n}" for t, n in p["deleted"].items()) or "no rows"
            print(f"  {p['phone']}: {rows}; {len(p['documents'])} document file(s); "
                  f"{len(p['session']['transcripts'])} session transcript(s); card reset")
            for d in p["directories"]:
                if not d["removed"]:
                    print(f"      {d['path']} kept, still holds: {d['left']}")
        for problem in p["problems"]:
            print(f"      PROBLEM: {problem}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--older-than-hours", type=float, default=0,
                    help="leave a test thread alone while its last activity is younger than this; 0 (default) "
                         "wipes every test thread")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run, writes nothing)")
    ap.add_argument("--phones", help="comma-separated test numbers, instead of every marked one")
    ap.add_argument("--json", action="store_true", help="print the raw JSON report instead of a summary")
    args = ap.parse_args(argv)

    try:   # canonicalized like test_threads.py, so --phones names the same thread --mark created
        phones = [TT.canonical_phone(p) for p in args.phones.split(",") if p.strip()] if args.phones else None
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    report = run(older_than_hours=args.older_than_hours, apply=args.apply, phones=phones)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print(report)
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
