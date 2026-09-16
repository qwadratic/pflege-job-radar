"""Import a campaign recipient's history from an earlier system (TASK-102): facts, clinic/placement record, prior
messages and document originals, so Luna does not re-ask what the candidate already told, and asks before reusing
an earlier CV/Urkunde.

Same discipline as export_known_phones.py / migrate_candidates.py / external_contacts.py: no external schema in
this module. The operator supplies the source database (opened SQLite ``mode=ro``) and a queries file; this module
only knows the column contract below. deploy/import-history.example.sql is a worked set for one real source.

Usage:
    python -m app.wa.luna.import_history --db SOURCE.sqlite --queries QUERIES.sql --source LABEL
        [--media-root DIR ...] (--phone +49... ... | --phones-file FILE) [--apply] [--json]

Dry-run by default: reads the source (files included, sha256 checked) and our database read-only, writes nothing,
calls no LLM and no Meta. ``--apply`` writes. Campaign sender (TASK-103): ``Source.open(...)`` once, then
``import_phone(source, phone, apply=True)`` per phone.

QUERIES FILE: four queries, each after a line ``-- query: <name>``. Each runs with the named parameters ``:phone``
(+E.164) and ``:phone_digits`` (digits only) and returns columns with exactly these names (NULL = unknown). A
missing required column, an unknown column or a value outside its contract raises.

  facts      source_ref (required); region, city, department_pref, qualification_path (urkunde|defizit|
             kenntnispruefung|reject|unknown), qualification_ok (0/1), urkunde_status, housing_known (0/1),
             people_count (integer > 0). Several rows (duplicate person records) merge; a key with two different
             values is reported as conflicting and not imported.
  placement  source_ref (required); clinic, status, stage, contract_start_date, updated_at, submitted (0/1),
             placed (0/1).
  messages   source_ref (required, unique per message), direction ('in'|'out', required), at (required); kind, body.
  documents  source_ref (required, unique per file), origin (required): candidate = a file the candidate sent,
             forwarded = a file someone forwarded for them, derived_cv = a CV the source generated from theirs,
             skip = not a candidate document (reported only); path (required unless skip): absolute, or relative
             to one of --media-root (tried in order; '..' raises); original_filename, mime_type, sha256 (the
             source's; verified), sent_at, old_class, old_type (the source's classification, kept as metadata),
             media_id (Meta media id: a file missing on disk is downloaded again on --apply), source_system.

WHAT --apply WRITES (data/wa.sqlite, C.DOCUMENTS_DIR):
- card facts: only keys the card does not have yet (a card value is kept and reported);
- card.prior_contact: deterministic summary {source, imported_at, first/last contact, message counts,
  facts_imported, documents_not_recoverable, summary}; card.prior_placement {records, submitted, placed};
- wa_imported_messages: one row per source message (never wa_messages: ball, follow-ups and the Luna payload
  would read them as this chat);
- documents: candidate/forwarded files first, then derived CVs (only when no lebenslauf is held for the phone).
  Bytes from disk (or Meta by media_id), sha256 checked against the source, same bytes already held for the
  phone -> not stored again; stored with api._write_original, a wa_documents row with import_source/import_ref/
  import_meta and reuse_state=pending, then api.read_and_classify (our extraction + classification). The card
  entry is {id, document_type, certificate_level, imported: true, reuse: pending, sent_at}: it counts for the
  documents gate only after the candidate confirms reuse (luna_brain, prompts EARLIER DOCUMENTS).

Idempotent: a source message or document already imported (source label + source_ref) is not written again; a
stored import whose classification failed is re-read and classified on the next run. Fails loudly: an unreadable
database, media root or file raises SourceAccessError naming the path and the grant the OS user needs (no sudo
here); so does a Meta download that fails without an HTTP answer (no token, network). A file that is missing with
no media_id, that Meta refuses (HTTP error: expired, deleted), that is empty or whose sha256 differs from the
source is reported per phone as not recoverable and makes the CLI exit 1.
"""
import argparse
import getpass
import hashlib
import json
import mimetypes
import os
import pathlib
import re
import sqlite3
import stat
import sys

from .. import config as C
from .. import meta as M
from .. import slots as SL
from .. import store as ST

QUERY_NAMES = ("facts", "placement", "messages", "documents")
COLUMNS = {
    "facts": (("source_ref",), ("region", "city", "department_pref", "qualification_path", "qualification_ok",
                                "urkunde_status", "housing_known", "people_count")),
    "placement": (("source_ref",), ("clinic", "status", "stage", "contract_start_date", "updated_at", "submitted",
                                    "placed")),
    "messages": (("source_ref", "direction", "at"), ("kind", "body")),
    "documents": (("source_ref", "origin"), ("path", "original_filename", "mime_type", "sha256", "sent_at",
                                             "old_class", "old_type", "media_id", "source_system")),
}
FACT_KEYS = COLUMNS["facts"][1]
QUALIFICATION_PATHS = ("urkunde", "defizit", "kenntnispruefung", "reject", "unknown")
ORIGINS = ("candidate", "forwarded", "derived_cv", "skip")
EXTRACTABLE_KINDS = ("document", "image")
IMPORT_CLAIM_PREFIX = "import:"   # wa_reply_turn_claims.turn_key while the card merge runs
MIN_PHONE_DIGITS = 8              # as migrate_candidates.py: canonicalize_phone("garbage") -> "+49"

_QUERY_HEADER = re.compile(r"^--\s*query:\s*(\S+)\s*$", re.M)


class SourceAccessError(RuntimeError):
    """The OS user running the import cannot read a source path."""


def _access_error(path, what, exc):
    user = getpass.getuser()
    return SourceAccessError(
        f"cannot read {what} {path}: {exc}. The user {user!r} needs read access: search (x) on every directory "
        f"down to it and read (r) on the file, e.g. setfacl -m u:{user}:--x <each parent dir>; "
        f"setfacl -R -m u:{user}:rX,d:u:{user}:rX <media root> (docs/rollout-runbook.md, TASK-102)")


def load_queries(path):
    """{name: sql} from a queries file; raises unless it holds exactly the four QUERY_NAMES, each non-empty."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    headers = list(_QUERY_HEADER.finditer(text))
    names = [h.group(1) for h in headers]
    unknown = sorted(set(names) - set(QUERY_NAMES))
    duplicated = sorted({n for n in names if names.count(n) > 1})
    missing = [n for n in QUERY_NAMES if n not in names]
    if unknown or duplicated or missing:
        raise ValueError(f"{path}: queries must be exactly {QUERY_NAMES} (unknown {unknown}, duplicated "
                         f"{duplicated}, missing {missing})")
    queries = {}
    for i, h in enumerate(headers):
        sql = text[h.end():headers[i + 1].start() if i + 1 < len(headers) else len(text)].strip()
        if not sql:
            raise ValueError(f"{path}: query {h.group(1)!r} is empty")
        queries[h.group(1)] = sql
    return queries


def canonical_phone(raw):
    canon = M.canonicalize_phone(str(raw or ""))
    if not canon or len(canon.lstrip("+")) < MIN_PHONE_DIGITS:
        raise ValueError(f"phone did not canonicalize to a real number: {raw!r} -> {canon!r}")
    return canon


class Source:
    """One source system: read-only database connection, the four queries, a label, media roots."""

    def __init__(self, conn, queries, label, media_roots):
        self.conn, self.queries, self.label, self.media_roots = conn, queries, label, media_roots

    @classmethod
    def open(cls, db_path, queries_path, label, media_roots=()):
        if not str(label or "").strip():
            raise ValueError("a source label is required (recorded as import_source on every imported row)")
        db_path = pathlib.Path(db_path).absolute()
        try:
            st = os.stat(db_path)
        except FileNotFoundError:
            raise FileNotFoundError(f"source database {db_path} does not exist")
        except PermissionError as exc:
            raise _access_error(db_path, "source database", exc)
        if not stat.S_ISREG(st.st_mode) or not os.access(db_path, os.R_OK):
            raise _access_error(db_path, "source database", "not a readable regular file")
        conn = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("select 1 from sqlite_master limit 1").fetchall()
        except sqlite3.Error as exc:
            conn.close()
            raise _access_error(db_path, "source database", exc)
        roots = []
        for root in media_roots:
            root = pathlib.Path(root).absolute()
            try:
                st = os.stat(root)
            except FileNotFoundError:
                conn.close()
                raise FileNotFoundError(f"media root {root} does not exist")
            except PermissionError as exc:
                conn.close()
                raise _access_error(root, "media root", exc)
            if not stat.S_ISDIR(st.st_mode) or not os.access(root, os.X_OK):
                conn.close()
                raise _access_error(root, "media root", "not a directory this user can search")
            roots.append(root)
        return cls(conn, load_queries(queries_path), label.strip(), roots)

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def rows(self, name, phone):
        """The rows of query ``name`` for ``phone`` as dicts; raises on a column outside the contract."""
        cur = self.conn.execute(self.queries[name], {"phone": phone, "phone_digits": phone.lstrip("+")})
        columns = [d[0] for d in cur.description or ()]
        required, optional = COLUMNS[name]
        unknown = [col for col in columns if col not in required + optional]
        missing = [col for col in required if col not in columns]
        if unknown or missing:
            raise ValueError(f"query {name!r} must return {required} plus any of {optional}; unknown columns "
                             f"{unknown}, missing {missing}")
        out = []
        for row in cur.fetchall():
            d = dict(zip(columns, row))
            for col in required:
                if d[col] is None or str(d[col]).strip() == "":
                    raise ValueError(f"query {name!r} returned a row without {col}: {d!r}")
            out.append(d)
        return out


# --- values -------------------------------------------------------------------------------------------------

def _text(value):
    return None if value is None or str(value).strip() == "" else str(value).strip()


def _flag(name, value):
    if value is None:
        return None
    if value in (0, 1, True, False) or str(value).strip().lower() in ("0", "1", "true", "false"):
        return str(value).strip().lower() in ("1", "true")
    raise ValueError(f"{name} must be 0/1, got {value!r}")


def _fact_value(key, value):
    if key in ("qualification_ok", "housing_known"):
        return _flag(key, value)
    if key == "people_count":
        if value is None:
            return None
        if isinstance(value, bool) or not str(value).strip().isdigit() or int(value) < 1:
            raise ValueError(f"people_count must be an integer > 0, got {value!r}")
        return int(value)
    value = _text(value)
    if key == "qualification_path" and value is not None and value not in QUALIFICATION_PATHS:
        raise ValueError(f"qualification_path must be one of {QUALIFICATION_PATHS}, got {value!r}")
    return value


def resolve_facts(rows):
    """-> (known {key: value}, conflicting {key: [values]}) over every facts row."""
    known, conflicting = {}, {}
    for key in FACT_KEYS:
        values = []
        for row in rows:
            v = _fact_value(key, row.get(key))
            if v is not None and v not in values:
                values.append(v)
        if len(values) == 1:
            known[key] = values[0]
        elif values:
            conflicting[key] = values
    return known, conflicting


def _kind_for(mime_type):
    major = (mime_type or "").split("/")[0].lower()
    return major if major in ("image", "audio", "video") else "document"


def _document_row(row):
    origin = _text(row["origin"])
    if origin not in ORIGINS:
        raise ValueError(f"documents origin must be one of {ORIGINS}, got {row['origin']!r} ({row['source_ref']})")
    if origin != "skip" and not _text(row.get("path")):
        raise ValueError(f"documents row {row['source_ref']} ({origin}) has no path")
    doc = {k: _text(row.get(k)) for k in COLUMNS["documents"][1]}
    doc.update(source_ref=str(row["source_ref"]), origin=origin)
    doc["mime_type"] = doc["mime_type"] or mimetypes.guess_type(doc["original_filename"] or doc["path"] or "")[0]
    if doc["sha256"]:
        doc["sha256"] = doc["sha256"].lower()
    return doc


def _message_row(row):
    direction = _text(row["direction"])
    if direction not in ("in", "out"):
        raise ValueError(f"messages direction must be 'in' or 'out', got {row['direction']!r} ({row['source_ref']})")
    return {"source_ref": str(row["source_ref"]), "direction": direction, "at": str(row["at"]),
            "kind": _text(row.get("kind")), "body": row.get("body")}


def _placement_row(row):
    return {"source_ref": str(row["source_ref"]), **{k: _text(row.get(k)) for k in
                                                     ("clinic", "status", "stage", "contract_start_date",
                                                      "updated_at")},
            "submitted": bool(_flag("submitted", row.get("submitted"))),
            "placed": bool(_flag("placed", row.get("placed")))}


# --- files ------------------------------------------------------------------------------------------------

def resolve_path(value, roots):
    """-> the existing file for a documents path, or None when it is on no root / not on disk. Raises on '..', a
    relative path without roots, a symlink leaving its root, a non-file, or a path this user cannot stat."""
    pure = pathlib.PurePosixPath(value)
    if pure.is_absolute():
        tries = [(pathlib.Path(value), None)]
    else:
        if ".." in pure.parts:
            raise ValueError(f"document path {value!r} leaves its media root")
        if not roots:
            raise ValueError(f"document path {value!r} is relative and no --media-root was given")
        tries = [(root / pure, root) for root in roots]
    for path, root in tries:
        try:
            st = os.stat(path)
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            raise _access_error(path, "document", exc)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"document path {path} is not a regular file")
        if root is not None and not pathlib.Path(os.path.realpath(path)).is_relative_to(os.path.realpath(root)):
            raise ValueError(f"document path {path} resolves outside its media root {root}")
        return path
    return None


def _read(path):
    try:
        return pathlib.Path(path).read_bytes()
    except PermissionError as exc:
        raise _access_error(path, "document", exc)


# --- our side, read-only (dry-run) ------------------------------------------------------------------------

def _our_view(phone, source_label):
    """(card or None, {source_ref: doc id} imported from this source, {sha256: doc id}, {source_ref} messages
    imported) from C.SQLITE_PATH opened read-only; empty when the file does not exist yet."""
    if not pathlib.Path(C.SQLITE_PATH).exists():
        return None, {}, {}, set()
    c = sqlite3.connect(pathlib.Path(C.SQLITE_PATH).absolute().as_uri() + "?mode=ro", uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        tables = {r["name"] for r in c.execute("select name from sqlite_master where type='table'")}
        card = None
        if "wa_threads" in tables:
            row = c.execute("select slots from wa_threads where phone=?", (phone,)).fetchone()
            card = json.loads(row["slots"] or "{}") if row else None
        refs, shas, messages = {}, {}, set()
        if "wa_documents" in tables:
            columns = {r[1] for r in c.execute("pragma table_info(wa_documents)")}
            for r in c.execute("select * from wa_documents where phone=? order by id", (phone,)):
                shas.setdefault(r["sha256"], r["id"])
                if "import_ref" in columns and r["import_source"] == source_label:
                    refs[r["import_ref"]] = r["id"]
        if "wa_imported_messages" in tables:
            messages = {r["import_ref"] for r in c.execute(
                "select import_ref from wa_imported_messages where phone=? and import_source=?", (phone, source_label))}
        return card, refs, shas, messages
    finally:
        c.close()


# --- the import ---------------------------------------------------------------------------------------------

def _document_order(doc):
    return (doc["origin"] == "derived_cv", doc["sent_at"] or "", doc["source_ref"])


def import_phone(source, phone, apply=False, client=None):
    """Import one phone from ``source``. -> the report:

        {phone, source, applied, found, facts: {known, conflicting, absent, imported, kept}, placement: [...],
         messages: {found, already_imported, imported}, stop_messages: [{source_ref, at, body}] (stop_messages()),
         documents: [{source_ref, origin, old_class, old_type,
         action, reason?, doc_id?, document_type?, certificate_level?}], not_recoverable: n, card_documents_added}

    Document actions -- dry-run: would_import, would_download_from_meta, already_imported, same_bytes_held,
    not_recoverable, skipped; --apply: imported, downloaded_from_meta, classified (an earlier import read now),
    already_imported, same_bytes_held, derived_cv_not_needed, not_recoverable, skipped. found=false (no row in any
    query): nothing else is reported or written."""
    phone = canonical_phone(phone)
    facts_rows = source.rows("facts", phone)
    placement = [_placement_row(r) for r in source.rows("placement", phone)]
    messages = [_message_row(r) for r in source.rows("messages", phone)]
    documents = sorted((_document_row(r) for r in source.rows("documents", phone)), key=_document_order)
    known, conflicting = resolve_facts(facts_rows)
    report = {"phone": phone, "source": source.label, "applied": bool(apply),
              "found": bool(facts_rows or placement or messages or documents),
              "facts": {"known": known, "conflicting": conflicting,
                        "absent": [k for k in FACT_KEYS if k not in known and k not in conflicting]},
              "placement": placement,
              "messages": {"found": len(messages)}, "stop_messages": stop_messages(messages), "documents": [],
              "not_recoverable": 0}
    if not report["found"]:   # the source does not know this phone: no thread, no prior_contact
        return report
    if apply:
        with ST.db() as c:
            _apply(c, source, phone, known, placement, messages, documents, report, client)
    else:
        _dry_run(source, phone, known, messages, documents, report)
    report["not_recoverable"] = sum(d["action"] == "not_recoverable" for d in report["documents"])
    return report


def stop_messages(messages):
    """The candidate's messages in the imported history that are a Stopp by this harness's own rule (slots.is_stop,
    the rule that stops a thread here): [{source_ref, at, body}]. The campaign sender never sends to such a phone
    (review 2026-09-14: a Stopp written to the old bot was invisible to the sender)."""
    return [{"source_ref": m["source_ref"], "at": m["at"], "body": m["body"]}
            for m in messages if m["direction"] == "in" and SL.is_stop(m["body"] or "")]


def _document_entry(doc, action, **extra):
    return {"source_ref": doc["source_ref"], "origin": doc["origin"], "source_system": doc["source_system"],
            "old_class": doc["old_class"], "old_type": doc["old_type"], "sent_at": doc["sent_at"], "action": action,
            **extra}


def _dry_run(source, phone, known, messages, documents, report):
    card, refs, shas, imported_messages = _our_view(phone, source.label)
    card = card or {}
    report["facts"]["imported"] = {k: v for k, v in known.items() if card.get(k) is None}
    report["facts"]["kept"] = {k: {"card": card[k], "source": v} for k, v in known.items()
                               if card.get(k) is not None}
    report["messages"]["already_imported"] = sum(m["source_ref"] in imported_messages for m in messages)
    for doc in documents:
        if doc["origin"] == "skip":
            report["documents"].append(_document_entry(doc, "skipped", reason="not a candidate document"))
        elif doc["source_ref"] in refs:
            report["documents"].append(_document_entry(doc, "already_imported", doc_id=refs[doc["source_ref"]]))
        else:
            report["documents"].append(_bytes_check(source, doc, shas))


def _bytes_check(source, doc, shas):
    """Dry-run view of one document's bytes: on disk (sha256 checked), downloadable, or not recoverable."""
    path = resolve_path(doc["path"], source.media_roots)
    if path is None:
        if doc["media_id"]:
            return _document_entry(doc, "would_download_from_meta", reason=f"not on disk: {doc['path']}")
        return _document_entry(doc, "not_recoverable", reason=f"not on disk and no media_id: {doc['path']}")
    blob = _read(path)
    problem = _bytes_problem(doc, blob)
    if problem:
        return _document_entry(doc, "not_recoverable", reason=problem)
    sha = hashlib.sha256(blob).hexdigest()
    if sha in shas:
        return _document_entry(doc, "same_bytes_held", doc_id=shas[sha])
    if doc["origin"] == "derived_cv":
        return _document_entry(doc, "would_import", size_bytes=len(blob), path=str(path),
                               reason="derived CV: --apply imports it only when no lebenslauf is held by then")
    return _document_entry(doc, "would_import", size_bytes=len(blob), path=str(path))


def _bytes_problem(doc, blob):
    if not blob:
        return "empty file"
    sha = hashlib.sha256(blob).hexdigest()
    if doc["sha256"] and sha != doc["sha256"]:
        return f"sha256 {sha} differs from the source's {doc['sha256']}"
    return None


def _obtain(source, doc, client):
    """-> (blob, obtained, path) or (None, reason, None) when the file cannot be recovered."""
    path = resolve_path(doc["path"], source.media_roots)
    if path is not None:
        return _read(path), "disk", path
    if not doc["media_id"]:
        return None, f"not on disk and no media_id: {doc['path']}", None
    cl = client or M.Client()
    try:
        info = cl.media_url(doc["media_id"])
        blob = cl.download_media(info["url"])
    except M.MetaError as exc:
        if exc.status_code is None:   # no token, network: not an answer about this media, fail the run
            raise
        # Meta answered and refused (expired or deleted media): reported per phone, the CLI exits 1
        return None, (f"not on disk ({doc['path']}) and Meta refused media {doc['media_id']}: {exc} "
                      f"{json.dumps(exc.payload, ensure_ascii=False)}"), None
    doc["mime_type"] = doc["mime_type"] or info.get("mime_type")
    return blob, "meta", None


def _apply(c, source, phone, known, placement, messages, documents, report, client):
    from .. import api as WAPI   # imported lazily: api pulls in FastAPI

    new_messages = sum(ST.record_imported_message(c, phone, source.label, m["source_ref"], m["direction"],
                                                  m["kind"], m["body"], m["at"]) for m in messages)
    c.commit()
    report["messages"].update(already_imported=len(messages) - new_messages, imported=new_messages)

    for doc in documents:
        if doc["origin"] == "skip":
            report["documents"].append(_document_entry(doc, "skipped", reason="not a candidate document"))
            continue
        existing = ST.imported_document(c, source.label, doc["source_ref"])
        if existing is not None:
            report["documents"].append(_complete_existing(c, WAPI, doc, existing))
            continue
        if doc["origin"] == "derived_cv":
            cv = c.execute("select id from wa_documents where phone=? and document_type='lebenslauf' order by id "
                           "limit 1", (phone,)).fetchone()
            if cv is not None:
                report["documents"].append(_document_entry(doc, "derived_cv_not_needed", doc_id=cv["id"],
                                                           reason="a CV of the candidate is already held"))
                continue
        blob, obtained, path = _obtain(source, doc, client)
        problem = _bytes_problem(doc, blob) if blob is not None else obtained
        if problem:
            report["documents"].append(_document_entry(doc, "not_recoverable", reason=problem))
            continue
        sha = hashlib.sha256(blob).hexdigest()
        held = ST.document_with_sha256(c, phone, sha)
        if held is not None:
            report["documents"].append(_document_entry(doc, "same_bytes_held", doc_id=held["id"]))
            continue
        kind = _kind_for(doc["mime_type"])
        stored = WAPI._write_original(phone, "import" + sha[:16], blob, WAPI._mime_suffix(doc["mime_type"]))
        meta = {"origin": doc["origin"], "source_system": doc["source_system"], "old_class": doc["old_class"],
                "old_type": doc["old_type"], "sent_at": doc["sent_at"], "source_path": str(path or doc["path"]),
                "obtained": obtained, "source_sha256": doc["sha256"], "sha256_verified": bool(doc["sha256"]),
                "media_id": doc["media_id"]}
        doc_id = ST.record_imported_document(c, phone, doc["media_id"], kind, doc["mime_type"],
                                             doc["original_filename"], str(stored), sha, len(blob), source.label,
                                             doc["source_ref"], meta)
        entry = _document_entry(doc, "downloaded_from_meta" if obtained == "meta" else "imported", doc_id=doc_id,
                                sha256_verified=bool(doc["sha256"]))
        if kind in EXTRACTABLE_KINDS:
            _, entry["document_type"], entry["certificate_level"], _ = WAPI.read_and_classify(
                c, doc_id, kind, blob, doc["original_filename"], doc["mime_type"])
        report["documents"].append(entry)

    report["facts"].update(_merge_card(c, source, phone, known, placement, messages, report))


def _complete_existing(c, WAPI, doc, row):
    """An earlier run stored this document: classify it now when that run failed before classification."""
    if row["document_type"] is not None or row["kind"] not in EXTRACTABLE_KINDS:
        return _document_entry(doc, "already_imported", doc_id=row["id"], document_type=row["document_type"])
    blob = WAPI._read_original(row)
    _, document_type, certificate_level, _ = WAPI.read_and_classify(c, row["id"], row["kind"], blob,
                                                                    row["original_filename"], row["mime_type"])
    return _document_entry(doc, "classified", doc_id=row["id"], document_type=document_type,
                           certificate_level=certificate_level)


def _merge_card(c, source, phone, known, placement, messages, report):
    """The card half, short and under a phone claim (the webhook worker and catch-up stop at an in-flight claim):
    facts into empty keys, prior_contact, prior_placement, card entries for classified imported documents.
    -> {imported, kept}."""
    key = IMPORT_CLAIM_PREFIX + source.label
    with ST._lock:
        if ST.claim_in_flight(c, phone) or not ST.claim_reply_turn(c, phone, key):
            raise RuntimeError(f"{phone} has a turn or import in flight; run the import again once it finished")
        try:
            t = ST.thread(c, phone)
            card = t["slots"]
            imported = {k: v for k, v in known.items() if card.get(k) is None}
            kept = {k: {"card": card[k], "source": v} for k, v in known.items() if card.get(k) is not None}
            card.update(imported)
            on_card = {d["id"] for d in card.get("documents", [])}
            rows = [r for r in ST.documents_for(c, phone)
                    if r["import_source"] == source.label and r["document_type"] is not None]
            added = [{"id": r["id"], "document_type": r["document_type"], "certificate_level": r["certificate_level"],
                      "imported": True, "reuse": r["reuse_state"], "sent_at": json.loads(r["import_meta"])["sent_at"]}
                     for r in rows if r["id"] not in on_card]
            if added:
                card["documents"] = [*card.get("documents", []), *added]
            previous = card.get("prior_contact") or {}
            facts_imported = sorted(set(previous.get("facts_imported", [])) | set(imported))
            card["prior_contact"] = prior_contact(source.label, previous.get("imported_at") or ST.now_iso(),
                                                  messages, known, facts_imported, card.get("documents", []),
                                                  [d for d in report["documents"] if d["action"] == "not_recoverable"],
                                                  placement)
            if placement:
                card["prior_placement"] = {"records": placement, "submitted": any(p["submitted"] for p in placement),
                                           "placed": any(p["placed"] for p in placement)}
            ST.save_thread(c, t)
        except BaseException:
            ST.finish_reply_turn_claim(c, phone, key, "import_error")
            raise
        ST.finish_reply_turn_claim(c, phone, key, "import_done")
    report["card_documents_added"] = [d["id"] for d in added]
    return {"imported": imported, "kept": kept}


_FACT_WORDS = {"region": "region {}", "city": "city {}", "department_pref": "department {}",
               "qualification_path": "qualification path {}", "urkunde_status": "Urkunde status {}",
               "people_count": "{} people for the flat"}


def _fact_phrase(key, value):
    if key == "qualification_ok":
        return "qualification accepted" if value else "qualification not accepted (not placeable)"
    if key == "housing_known":
        return "housing need known" if value else "housing need not known"
    return _FACT_WORDS[key].format(value)


def prior_contact(label, imported_at, messages, known, facts_imported, card_documents, not_recoverable, placement):
    """The deterministic card.prior_contact for the model: dates, counts, facts, documents, clinic record."""
    ins = sorted(m["at"] for m in messages if m["direction"] == "in")
    outs = sorted(m["at"] for m in messages if m["direction"] == "out")
    every = sorted(ins + outs)
    held = [d for d in card_documents if d.get("imported")]
    out = {"source": label, "imported_at": imported_at,
           "first_contact_at": every[0] if every else None, "last_contact_at": every[-1] if every else None,
           "last_inbound_at": ins[-1] if ins else None, "last_outbound_at": outs[-1] if outs else None,
           "messages_from_candidate": len(ins), "messages_to_candidate": len(outs),
           "facts_imported": facts_imported,
           "documents_not_recoverable": [{"old_class": d["old_class"], "sent_at": d["sent_at"]}
                                         for d in not_recoverable]}
    parts = []
    if every:
        parts.append(f"Earlier contact on this number: {len(ins)} messages from the candidate and {len(outs)} to "
                     f"them between {every[0][:10]} and {every[-1][:10]}"
                     + (f"; the candidate last wrote on {ins[-1][:10]}." if ins else "; the candidate never wrote."))
    else:
        parts.append("Earlier contact on this number; no messages on record.")
    parts.append("Known from then: " + ", ".join(_fact_phrase(k, known[k]) for k in FACT_KEYS if k in known) + "."
                 if known else "No facts on record from then.")
    if held:   # no reuse state here: card.documents carries the live one
        parts.append("Documents from then held: " + ", ".join(d["document_type"] for d in held) + ".")
    if not_recoverable:
        parts.append(f"{len(not_recoverable)} earlier file(s) could not be recovered.")
    if placement:
        parts.append("Clinic record from then: " + "; ".join(
            " ".join(filter(None, (p["clinic"] or "no clinic", f"status {p['status']}" if p["status"] else None,
                                   f"stage {p['stage']}" if p["stage"] else None,
                                   f"updated {p['updated_at'][:10]}" if p["updated_at"] else None)))
            for p in placement) + f"; placed: {'yes' if any(p['placed'] for p in placement) else 'no'}.")
    out["summary"] = " ".join(parts)
    return out


# --- CLI ------------------------------------------------------------------------------------------------------

def _print_report(r):
    verb = "imported" if r["applied"] else "would import"
    print(f"{r['phone']} [{r['source']}] {'APPLIED' if r['applied'] else 'dry-run'}")
    if not r["found"]:
        print("  not found in the source: nothing to import")
        return
    facts = r["facts"]
    print(f"  facts {verb}: {facts.get('imported')}; kept (card has a value): {facts.get('kept')}; "
          f"conflicting: {facts['conflicting']}; absent: {facts['absent']}")
    print(f"  placement records: {len(r['placement'])}"
          + (f" (submitted: {any(p['submitted'] for p in r['placement'])}, "
             f"placed: {any(p['placed'] for p in r['placement'])})" if r["placement"] else ""))
    print(f"  messages: {r['messages']}")
    by_class = {}
    for d in r["documents"]:
        counts = by_class.setdefault(d["old_class"] or "-", {})
        counts[d["action"]] = counts.get(d["action"], 0) + 1
    print(f"  documents by old class: {by_class}")
    for d in r["documents"]:
        if d["action"] in ("not_recoverable", "skipped") or d.get("document_type"):
            detail = d.get("reason") or f"doc {d.get('doc_id')} -> {d.get('document_type')}"
            print(f"    {d['action']}: {d['source_ref']} ({d['origin']}, old {d['old_class']}/{d['old_type']}): "
                  f"{detail}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="source sqlite database, opened read-only")
    ap.add_argument("--queries", required=True, help="queries file (see deploy/import-history.example.sql)")
    ap.add_argument("--source", required=True, help="source label recorded on every imported row")
    ap.add_argument("--media-root", action="append", default=[], help="root for relative document paths, in order")
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--phone", action="append", help="+E.164 phone to import (repeatable)")
    who.add_argument("--phones-file", help="file with one phone per line")
    ap.add_argument("--apply", action="store_true", help="write; without it nothing is written")
    ap.add_argument("--json", action="store_true", help="print each report as one JSON line")
    args = ap.parse_args(argv)

    phones = args.phone or [line.strip() for line in pathlib.Path(args.phones_file).read_text().splitlines()
                            if line.strip()]
    failed = 0
    try:
        with Source.open(args.db, args.queries, args.source, args.media_root) as source:
            for phone in phones:
                report = import_phone(source, phone, apply=args.apply)
                failed += report["not_recoverable"] > 0
                print(json.dumps(report, ensure_ascii=False)) if args.json else _print_report(report)
    except SourceAccessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if failed:
        print(f"{failed} phone(s) with documents that could not be recovered", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
