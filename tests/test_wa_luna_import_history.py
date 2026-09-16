"""TASK-102 offline: importing a campaign recipient's history from an earlier system, and the reuse question for
imported documents.

The source is a synthetic SQLite file with the old system's tables (the columns deploy/import-history.example.sql
reads) plus synthetic files under temp media roots; the queries are that example file itself. Our side is a
tmp_path wa.sqlite and DOCUMENTS_DIR; Meta, text extraction, classification and the model are fakes. Synthetic
personas and phone numbers only.
"""
import hashlib
import json
import os
import pathlib
import sqlite3
import stat
import time

import pytest

from app import cv as CV
from app import data as D
from app.wa import api as WAPI
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import meta as M
from app.wa import store as ST
from app.wa.luna import import_history as IH
from app.wa.luna import reporting as REP

REPO = pathlib.Path(__file__).resolve().parents[1]
QUERIES = REPO / "deploy" / "import-history.example.sql"
PHONE_ID = "555000333"
LEAD = "+4915550001234"
CV_ONLY = "+4915550005678"
SOURCE = "old-system-test"

OLD_SCHEMA = """
create table candidates (id integer primary key, workspace_id integer, full_name text, name_normalized text,
  pipeline text, external_key text, metadata_json text, created_at text, updated_at text);
create table candidate_whatsapp_messages (id integer primary key autoincrement, workspace_id integer not null,
  candidate_id integer, phone_e164 text not null, wamid text not null unique, direction text not null,
  message_type text not null, body text, caption text, context_wamid text, contact_name text, media_id text,
  media_mime text, media_sha256 text, media_filename text, attachment_id integer, latest_status text,
  occurred_at text not null);
create table candidate_attachments (id integer primary key autoincrement, workspace_id integer not null,
  candidate_id integer, source_system text not null, external_ref text not null, storage_path text not null,
  original_filename text, mime_type text, sha256 text, size_bytes integer, metadata_json text, created_at text,
  unique(source_system, external_ref));
create table candidate_clinic_cases (id integer primary key autoincrement, workspace_id integer not null,
  candidate_id integer not null, company_id integer, clinic_key text not null, status text not null default 'unknown',
  register_signed integer not null default 0, contract_start_date text, primary_contact_email text,
  metadata_json text, created_at text, updated_at text);
create table candidate_recruitment_state (id integer primary key autoincrement, workspace_id integer not null,
  candidate_id integer not null, placement_stage text not null, waiting_for text not null default 'none',
  next_action text, due_at text, scheduled_event_at text, cv_ready integer, contact_state text,
  source_kind text not null default 'manual', updated_at text);
create table job_wohnung_outreach (id integer primary key autoincrement, workspace_id integer not null,
  phone_e164 text not null, phone_key text not null unique, honorific text, full_name text, gender text,
  zoho_record_id text, land text, region text, lead_status text, source text, status text not null default 'queued',
  candidate_id integer, template_name text, wamid text, last_error text, metadata_json text,
  created_at text not null default current_timestamp, updated_at text not null default current_timestamp,
  sent_at text, replied_at text, decline_ack_sent_at text);
create table suppression_list (id integer primary key autoincrement, workspace_id integer,
  scope text not null check(scope in ('workspace','global')), channel_type text not null, value text not null,
  reason text not null, source_system text, raw_text text, raw_payload_json text,
  created_at text not null default current_timestamp, unique(workspace_id, scope, channel_type, value));
"""

CV_TEXT = "Lebenslauf Anna Beispiel, Pflegefachfrau, Intensivstation 2015-2026"
URKUNDE_TEXT = "Urkunde über die Erlaubnis zum Führen der Berufsbezeichnung Pflegefachfrau, Anna Beispiel"
DIENSTPLAN_TEXT = "Dienstplan September Station 3, Anna Beispiel, Frühdienst"
STANDARD_CV_TEXT = "Lebenslauf (standardisiert) Anna Beispiel, Pflegefachfrau"


class Env:
    """Paths of one fixture: the source database, media roots, our database and documents dir."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.db = tmp_path / "old" / "sales_brain.sqlite"
        self.root_a = tmp_path / "old" / "media_a"
        self.root_b = tmp_path / "old" / "media_b"
        self.lebenslauf = tmp_path / "old" / "lebenslauf"
        for d in (self.db.parent, self.root_a, self.root_b, self.lebenslauf):
            d.mkdir(parents=True, exist_ok=True)
        self.calls = {"extract": 0, "classify": 0, "meta": []}

    def file(self, root, rel, text):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def source(self, roots=None):
        return IH.Source.open(self.db, QUERIES, SOURCE, [self.root_a, self.root_b] if roots is None else roots)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_old_db(env):
    """Two synthetic people: LEAD with a full history, CV_ONLY with a CV and nothing else."""
    cv = env.file(env.root_a, "clinics_recruiting_de/1/wamid.old.3/lebenslauf.pdf", CV_TEXT)
    env.file(env.root_b, "clinics_recruiting_de/1/wamid.old.4/image.jpg", URKUNDE_TEXT)
    standard = env.file(env.lebenslauf, "1/lebenslauf_standard.pdf", STANDARD_CV_TEXT)
    env.file(env.root_a, "clinics_recruiting_de/2/wamid.old.20/cv.pdf", CV_TEXT + " (zweite Person)")
    lead_meta = {"phone": LEAD, "primary_phone": LEAD, "answers": {"phone": LEAD, "region": "Bayern",
                                                                 "departments": ["Intensiv"]},
                 "wa_agent": {"slots": {"region": "Bayern", "qualification_path": "urkunde", "qualification_ok": True,
                                        "housing_known": True, "people_count": 2, "urkunde_status": "yes"}}}
    c = sqlite3.connect(env.db)
    c.executescript(OLD_SCHEMA)
    c.execute("insert into candidates (id, workspace_id, full_name, metadata_json) values (1, 1, 'Anna Beispiel', ?)",
              (json.dumps(lead_meta),))
    c.execute("insert into candidates (id, workspace_id, full_name, metadata_json) values (2, 1, 'Bea Beispiel', ?)",
              (json.dumps({"phone": CV_ONLY.lstrip("+"), "wa_agent": {"slots": {"housing_known": False}}}),))
    messages = [
        (1, LEAD, "wamid.old.1", "inbound", "text", "Hallo, ich suche eine Stelle in Bayern", None, None,
         "2026-06-01T09:00:00+00:00"),
        (1, LEAD, "wamid.old.2", "outbound", "text", "Haben Sie die deutsche Urkunde?", None, None,
         "2026-06-01T09:01:00+00:00"),
        (1, LEAD, "wamid.old.3", "inbound", "document", None, "media-cv", 1, "2026-06-02T10:00:00+00:00"),
        (1, LEAD, "wamid.old.4", "inbound", "image", None, "media-urkunde", 2, "2026-06-02T10:05:00+00:00"),
        (1, LEAD, "wamid.old.5", "inbound", "image", None, "media-gone", 3, "2026-06-03T08:00:00+00:00"),
        (1, LEAD, "wamid.old.6", "inbound", "image", None, "media-ok", 4, "2026-06-03T08:01:00+00:00"),
        (1, LEAD, "wamid.old.7", "outbound", "image", "Klinikkarte", None, 8, "2026-06-04T12:00:00+00:00"),
        (2, CV_ONLY, "wamid.old.20", "inbound", "document", None, "media-cv2", 9, "2026-07-01T10:00:00+00:00"),
    ]
    for cid, phone, wamid, direction, kind, body, media_id, attachment, at in messages:
        c.execute("""insert into candidate_whatsapp_messages (workspace_id, candidate_id, phone_e164, wamid, direction,
                     message_type, body, media_id, attachment_id, occurred_at) values (1,?,?,?,?,?,?,?,?,?)""",
                  (cid, phone, wamid, direction, kind, body, media_id, attachment, at))
    attachments = [
        (1, 1, "meta_whatsapp_cloud", "wamid.old.3", "clinics_recruiting_de/1/wamid.old.3/lebenslauf.pdf",
         "lebenslauf.pdf", "application/pdf", _sha(CV_TEXT), {"crm_doc_class": "cv", "crm_doc_type": "lebenslauf"}),
        (2, 1, "meta_whatsapp_cloud", "wamid.old.4", "clinics_recruiting_de/1/wamid.old.4/image.jpg", None,
         "image/jpeg", _sha(URKUNDE_TEXT), {"crm_doc_class": "urkunde", "crm_doc_type": "urkunde"}),
        (3, 1, "meta_whatsapp_cloud", "wamid.old.5", "clinics_recruiting_de/1/wamid.old.5/image.jpg", None,
         "image/jpeg", None, {"crm_doc_class": "other"}),
        (4, 1, "meta_whatsapp_cloud", "wamid.old.6", "clinics_recruiting_de/1/wamid.old.6/image.jpg", None,
         "image/jpeg", _sha(DIENSTPLAN_TEXT), {"crm_doc_class": "other", "crm_doc_type": "dienstplan"}),
        (5, 1, "manager_crm_standardized_cv", "std-cv-1", str(standard), "lebenslauf_standard.pdf",
         "application/pdf", _sha(STANDARD_CV_TEXT), {"crm_doc_class": "cv_standardized"}),
        (6, 1, "clinic_inbound_packet", "packet-1", "/nonexistent/packet.pdf", "packet.pdf", "application/pdf", None,
         {}),
        (7, 1, "telegram_bot", "tg-1", "telegram/1/tg-1.jpg", None, "image/jpeg", None, {"crm_doc_class": "urkunde"}),
        (8, 1, "meta_whatsapp_cloud", "wamid.old.7", "clinics_recruiting_de/1/wamid.old.7/card.jpg", None,
         "image/jpeg", None, {"direction": "outbound"}),
        (9, 2, "meta_whatsapp_cloud", "wamid.old.20", "clinics_recruiting_de/2/wamid.old.20/cv.pdf", "cv.pdf",
         "application/pdf", _sha(CV_TEXT + " (zweite Person)"), {"crm_doc_class": "cv"}),
    ]
    for aid, cid, system, ref, path, filename, mime, sha, meta in attachments:
        c.execute("""insert into candidate_attachments (id, workspace_id, candidate_id, source_system, external_ref,
                     storage_path, original_filename, mime_type, sha256, metadata_json, created_at)
                     values (?,1,?,?,?,?,?,?,?,?,?)""",
                  (aid, cid, system, ref, path, filename, mime, sha, json.dumps(meta), f"2026-06-0{min(aid, 9)}T10:00:00"))
    c.execute("insert into candidate_clinic_cases (workspace_id, candidate_id, clinic_key, status, updated_at) "
              "values (1, 1, 'klinikum_beispielstadt', 'submitted', '2026-06-10T12:00:00')")
    c.execute("insert into candidate_recruitment_state (workspace_id, candidate_id, placement_stage, updated_at) "
              "values (1, 1, 'submitted_waiting_clinic', '2026-06-10T12:00:00')")
    c.commit()
    c.close()


class FakeMeta:
    """Meta for the importer's re-download and for the webhook turns: media-gone is refused, media-ok is served."""

    def __init__(self, env):
        self.env, self.sent, self.n = env, [], 0
        self.served = {"media-ok": DIENSTPLAN_TEXT.encode()}

    def media_url(self, media_id):
        self.env.calls["meta"].append(media_id)
        if media_id not in self.served:
            raise M.MetaError("Meta HTTP 400", status_code=400, payload={"error": {"code": 100}})
        return {"url": f"https://cdn.example/{media_id}", "mime_type": "image/jpeg"}

    def download_media(self, url):
        return self.served[url.rsplit("/", 1)[1]]

    def send_text(self, to_e164, body):
        self.n += 1
        self.sent.append(body)
        return f"wamid.out.{self.n}"

    def send_buttons(self, to_e164, body, buttons):
        return self.send_text(to_e164, body)


def _fake_classify(text):
    for word, doc_type, level in (("Dienstplan", "dienstplan", "unknown"), ("Lebenslauf", "lebenslauf", "unknown"),
                                  ("Urkunde", "urkunde", "fachkraft"), ("Defizitbescheid", "defizitbescheid",
                                                                        "unknown")):
        if word in text:
            return {"document_type": doc_type, "certificate_level": level}
    return {"document_type": "other", "certificate_level": "unknown"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    e = Env(tmp_path)
    _build_old_db(e)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {}, "taxonomy": {},
                    "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "BRAIN", "luna")
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 0)

    def extract(*args, **kw):
        e.calls["extract"] += 1
        blob = args[1] if len(args) > 1 else args[0]
        return blob.decode("utf-8")

    def classify(text, client=None):
        e.calls["classify"] += 1
        return _fake_classify(text)

    monkeypatch.setattr(CV, "extract_text", lambda filename, blob: extract(filename, blob))
    monkeypatch.setattr(CV, "extract_text_vision", lambda blob, suffix=".png", client=None: extract(blob))
    monkeypatch.setattr(CV, "classify_document", classify)
    monkeypatch.setattr(M, "Client", lambda *a, **k: pytest.fail("the importer must use the Meta client it is given"))
    e.meta = FakeMeta(e)
    return e


def _apply(env, phone=LEAD):
    with env.source() as source:
        return IH.import_phone(source, phone, apply=True, client=env.meta)


def _dry(env, phone=LEAD):
    with env.source() as source:
        return IH.import_phone(source, phone)


def _card(phone=LEAD):
    with ST.db() as c:
        return ST.thread(c, phone)["slots"]


def _docs(phone=LEAD):
    with ST.db() as c:
        return ST.documents_for(c, phone)


def _actions(report):
    return {d["source_ref"]: d["action"] for d in report["documents"]}


def _tree(path):
    return sorted(str(p.relative_to(path)) for p in pathlib.Path(path).rglob("*")) if pathlib.Path(path).exists() else []


# --- the importer: dry-run ---------------------------------------------------------------------------------------

def test_the_example_queries_file_is_the_documented_contract():
    queries = IH.load_queries(QUERIES)
    assert list(queries) == list(IH.QUERY_NAMES) == ["facts", "placement", "messages", "documents", "opt_outs"]


def test_dry_run_reports_what_would_be_imported_and_writes_nothing(env):
    source_before = hashlib.sha256(env.db.read_bytes()).hexdigest()
    report = _dry(env)
    assert not C.SQLITE_PATH.exists() and not C.DOCUMENTS_DIR.exists()
    assert env.calls == {"extract": 0, "classify": 0, "meta": []}, "a dry run calls no LLM and no Meta"
    assert hashlib.sha256(env.db.read_bytes()).hexdigest() == source_before
    assert report["applied"] is False
    assert report["facts"]["known"] == {"region": "Bayern", "department_pref": "Intensiv",
                                        "qualification_path": "urkunde", "qualification_ok": True,
                                        "urkunde_status": "yes", "housing_known": True, "people_count": 2}
    assert report["facts"]["imported"] == report["facts"]["known"] and report["facts"]["absent"] == ["city"]
    assert report["messages"] == {"found": 7, "already_imported": 0}
    assert _actions(report) == {
        "candidate_attachments:1": "would_import", "candidate_attachments:2": "would_import",
        "candidate_attachments:3": "would_download_from_meta", "candidate_attachments:4": "would_download_from_meta",
        "candidate_attachments:7": "not_recoverable", "candidate_attachments:5": "would_import",
        "candidate_attachments:6": "skipped", "candidate_attachments:8": "skipped"}
    assert [d["source_ref"] for d in report["documents"]][-1] == "candidate_attachments:5", "derived CVs come last"
    assert report["not_recoverable"] == 1
    assert {p["source_ref"]: (p["submitted"], p["placed"]) for p in report["placement"]} == {
        "candidate_clinic_cases:1": (True, False), "candidate_recruitment_state:1": (True, False)}
    assert report["opt_outs"] == [] and report["decline_import"]["new"] == []


def test_a_stopp_in_the_imported_history_is_reported_in_dry_run_and_apply(env):
    """Review 2026-09-14: a Stopp written to the old bot was invisible to the campaign sender."""
    assert _dry(env)["stop_messages"] == []
    c = sqlite3.connect(env.db)
    c.execute("""insert into candidate_whatsapp_messages (workspace_id, candidate_id, phone_e164, wamid, direction,
                 message_type, body, occurred_at) values (1, 1, ?, 'wamid.old.stop', 'inbound', 'text',
                 'Bitte STOPP, keine Nachrichten mehr', '2026-07-10T08:00:00+00:00')""", (LEAD,))
    c.execute("""insert into candidate_whatsapp_messages (workspace_id, candidate_id, phone_e164, wamid, direction,
                 message_type, body, occurred_at) values (1, 1, ?, 'wamid.old.stopfen', 'inbound', 'text',
                 'Intensivstation, Stopfen', '2026-07-10T08:01:00+00:00')""", (LEAD,))
    c.commit()
    c.close()
    expected = [{"source_ref": "candidate_whatsapp_messages:wamid.old.stop", "at": "2026-07-10T08:00:00+00:00",
                 "body": "Bitte STOPP, keine Nachrichten mehr"}]
    assert _dry(env)["stop_messages"] == expected
    assert _apply(env)["stop_messages"] == expected
    card = _card()   # TASK-105: a Stopp to the old bot marks the card declined like a recorded opt-out
    assert (card["declined"], card["declined_at"]) == (True, "2026-07-10T08:00:00+00:00")
    assert card["declined_reason"] == ("stop_message recorded by the earlier system old-system-test on 2026-07-10: "
                                       "Stopp in the chat: 'Bitte STOPP, keine Nachrichten mehr'")
    assert [(e["kind"], e["source_ref"]) for e in card["prior_opt_outs"]] == [
        ("stop_message", "candidate_whatsapp_messages:wamid.old.stop")]


def test_dry_run_against_an_existing_database_leaves_it_untouched(env):
    _apply(env, CV_ONLY)
    before = hashlib.sha256(C.SQLITE_PATH.read_bytes()).hexdigest(), _tree(C.DOCUMENTS_DIR)
    report = _dry(env, CV_ONLY)
    assert (hashlib.sha256(C.SQLITE_PATH.read_bytes()).hexdigest(), _tree(C.DOCUMENTS_DIR)) == before
    assert _actions(report) == {"candidate_attachments:9": "already_imported"}
    assert report["messages"]["already_imported"] == 1


# --- the importer: apply -------------------------------------------------------------------------------------------

def test_apply_seeds_the_card_history_and_documents(env):
    source_before = hashlib.sha256(env.db.read_bytes()).hexdigest()
    report = _apply(env)
    assert hashlib.sha256(env.db.read_bytes()).hexdigest() == source_before, "the source is never written"
    assert _actions(report) == {
        "candidate_attachments:1": "imported", "candidate_attachments:2": "imported",
        "candidate_attachments:3": "not_recoverable", "candidate_attachments:4": "downloaded_from_meta",
        "candidate_attachments:7": "not_recoverable", "candidate_attachments:5": "derived_cv_not_needed",
        "candidate_attachments:6": "skipped", "candidate_attachments:8": "skipped"}
    refused = next(d for d in report["documents"] if d["source_ref"] == "candidate_attachments:3")
    assert "Meta refused media media-gone" in refused["reason"]
    assert env.calls["meta"] == ["media-gone", "media-ok"]

    card = _card()
    for key, value in report["facts"]["known"].items():
        assert card[key] == value
    rows = {r["import_ref"]: r for r in _docs()}
    assert set(rows) == {"candidate_attachments:1", "candidate_attachments:2", "candidate_attachments:4"}
    cv, urkunde, dienstplan = (rows[f"candidate_attachments:{i}"] for i in (1, 2, 4))
    assert (cv["document_type"], urkunde["document_type"], urkunde["certificate_level"], dienstplan["document_type"]) \
        == ("lebenslauf", "urkunde", "fachkraft", "dienstplan")
    for row, text in ((cv, CV_TEXT), (urkunde, URKUNDE_TEXT), (dienstplan, DIENSTPLAN_TEXT)):
        path = pathlib.Path(row["path"])
        assert path.read_bytes() == text.encode() and row["sha256"] == _sha(text)
        assert path.is_relative_to(C.DOCUMENTS_DIR.absolute())
        assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert row["wamid"] is None and row["import_source"] == SOURCE and row["reuse_state"] == "pending"
    assert (cv["kind"], urkunde["kind"]) == ("document", "image")
    cv_meta = json.loads(cv["import_meta"])
    assert cv_meta["origin"] == "candidate" and cv_meta["source_system"] == "meta_whatsapp_cloud"
    assert (cv_meta["old_class"], cv_meta["old_type"], cv_meta["obtained"]) == ("cv", "lebenslauf", "disk")
    assert cv_meta["source_sha256"] == _sha(CV_TEXT) and cv_meta["sha256_verified"] is True
    assert json.loads(dienstplan["import_meta"])["obtained"] == "meta"
    assert cv["text"] == CV_TEXT and cv["text_key"] == "cv_text"

    assert [(d["id"], d["document_type"], d["imported"], d["reuse"]) for d in card["documents"]] == [
        (cv["id"], "lebenslauf", True, "pending"), (urkunde["id"], "urkunde", True, "pending"),
        (dienstplan["id"], "dienstplan", True, "pending")]
    assert "cv_text" not in card and "urkunde_text" not in card, "imported text reaches the card only on confirmation"
    assert "_documents_just_received" not in card and "document_type" not in card

    prior = card["prior_contact"]
    assert (prior["source"], prior["messages_from_candidate"], prior["messages_to_candidate"]) == (SOURCE, 5, 2)
    assert prior["first_contact_at"].startswith("2026-06-01") and prior["last_inbound_at"].startswith("2026-06-03")
    assert prior["facts_imported"] == sorted(report["facts"]["known"])
    assert len(prior["documents_not_recoverable"]) == 2
    assert prior["summary"] == (
        "Earlier contact on this number: 5 messages from the candidate and 2 to them between 2026-06-01 and "
        "2026-06-04; the candidate last wrote on 2026-06-03. Known from then: region Bayern, department Intensiv, "
        "qualification path urkunde, qualification accepted, Urkunde status yes, housing need known, 2 people for "
        "the flat. Documents from then held: lebenslauf, urkunde, dienstplan. 2 earlier file(s) "
        "could not be recovered. Clinic record from then: klinikum_beispielstadt status submitted updated "
        "2026-06-10; no clinic stage submitted_waiting_clinic updated 2026-06-10; placed: no.")
    assert card["prior_placement"]["submitted"] is True and card["prior_placement"]["placed"] is False

    with ST.db() as c:
        history = ST.imported_messages_for(c, LEAD)
        assert len(history) == 7 and history[0]["body"] == "Hallo, ich suche eine Stelle in Bayern"
        assert ST.messages_for(c, LEAD) == [], "imported history never becomes chat rows"
        assert REP.ball_for(c, LEAD) == "none" and not ST.has_inbound(c, LEAD)
        t = ST.thread(c, LEAD)
        assert t["last_inbound_at"] is None and t["turns"] == 0


def test_a_second_apply_writes_nothing_new(env):
    _apply(env)
    card, rows, files = _card(), _docs(), _tree(C.DOCUMENTS_DIR)
    calls = dict(env.calls, meta=list(env.calls["meta"]))
    report = _apply(env)
    assert _docs() == rows and _tree(C.DOCUMENTS_DIR) == files
    assert _card() == card
    assert report["messages"] == {"found": 7, "already_imported": 7, "imported": 0}
    actions = _actions(report)
    assert {actions[f"candidate_attachments:{i}"] for i in (1, 2, 4)} == {"already_imported"}
    assert (env.calls["extract"], env.calls["classify"]) == (calls["extract"], calls["classify"])
    assert report["card_documents_added"] == []


def test_the_same_bytes_already_received_on_whatsapp_are_not_stored_twice(env):
    with ST.db() as c:
        doc_id = ST.record_document(c, LEAD, "wamid.in.1", "media-x", "document", "application/pdf", "cv.pdf",
                                    "/stored/cv.pdf", _sha(CV_TEXT), len(CV_TEXT))
    report = _apply(env)
    entry = next(d for d in report["documents"] if d["source_ref"] == "candidate_attachments:1")
    assert (entry["action"], entry["doc_id"]) == ("same_bytes_held", doc_id)
    assert [r["id"] for r in _docs() if r["sha256"] == _sha(CV_TEXT)] == [doc_id]


def test_a_candidate_cv_classified_earlier_makes_the_derived_cv_unneeded_and_without_one_it_is_imported(env):
    c = sqlite3.connect(env.db)
    c.execute("delete from candidate_attachments where id=1")
    c.commit()
    c.close()
    report = _apply(env)
    assert _actions(report)["candidate_attachments:5"] == "imported"
    derived = next(r for r in _docs() if r["import_ref"] == "candidate_attachments:5")
    assert derived["document_type"] == "lebenslauf" and json.loads(derived["import_meta"])["origin"] == "derived_cv"


def test_card_values_win_and_are_reported(env):
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        t["slots"].update(region="Bayern", qualification_path="defizit", people_count=None)
        ST.save_thread(c, t)
    report = _apply(env)
    assert report["facts"]["kept"] == {"region": {"card": "Bayern", "source": "Bayern"},
                                       "qualification_path": {"card": "defizit", "source": "urkunde"}}
    assert "qualification_path" not in report["facts"]["imported"] and report["facts"]["imported"]["people_count"] == 2
    card = _card()
    assert card["qualification_path"] == "defizit" and card["people_count"] == 2
    assert "qualification_path" not in card["prior_contact"]["facts_imported"]


def test_two_person_records_with_different_values_import_neither(env):
    c = sqlite3.connect(env.db)
    meta = {"phone": LEAD, "wa_agent": {"slots": {"qualification_path": "defizit", "region": "Bayern"}}}
    c.execute("insert into candidates (id, workspace_id, metadata_json) values (3, 1, ?)", (json.dumps(meta),))
    c.commit()
    c.close()
    report = _dry(env)
    assert report["facts"]["conflicting"] == {"qualification_path": ["urkunde", "defizit"]}
    assert report["facts"]["known"]["region"] == "Bayern" and "qualification_path" not in report["facts"]["imported"]


def test_a_cv_only_person_gets_one_imported_cv(env):
    report = _apply(env, CV_ONLY)
    assert _actions(report) == {"candidate_attachments:9": "imported"}
    assert report["facts"]["known"] == {} and report["placement"] == []
    card = _card(CV_ONLY)
    assert [d["document_type"] for d in card["documents"]] == ["lebenslauf"]
    assert "prior_placement" not in card and card["prior_contact"]["summary"].endswith(
        "No facts on record from then. Documents from then held: lebenslauf.")


def test_a_classification_failure_leaves_the_original_and_the_next_run_classifies_it(env, monkeypatch):
    def broken(text, client=None):
        raise RuntimeError("classification failed")

    monkeypatch.setattr(CV, "classify_document", broken)
    with pytest.raises(RuntimeError, match="classification failed"):
        _apply(env, CV_ONLY)
    [row] = _docs(CV_ONLY)
    assert row["document_type"] is None and pathlib.Path(row["path"]).exists()
    assert "documents" not in _card(CV_ONLY)
    monkeypatch.setattr(CV, "classify_document", lambda text, client=None: _fake_classify(text))
    report = _apply(env, CV_ONLY)
    assert _actions(report) == {"candidate_attachments:9": "classified"}
    assert [r["id"] for r in _docs(CV_ONLY)] == [row["id"]]
    assert [d["id"] for d in _card(CV_ONLY)["documents"]] == [row["id"]]


def test_a_sha256_that_differs_from_the_source_is_not_recoverable(env):
    (env.root_a / "clinics_recruiting_de/2/wamid.old.20/cv.pdf").write_bytes(b"other bytes")
    report = _apply(env, CV_ONLY)
    [entry] = report["documents"]
    assert entry["action"] == "not_recoverable" and "differs from the source" in entry["reason"]
    assert _docs(CV_ONLY) == [] and not C.DOCUMENTS_DIR.exists()


def test_a_meta_failure_without_an_http_answer_fails_the_run(env):
    def no_token(media_id):
        raise M.MetaError("META_WHATSAPP_ACCESS_TOKEN is not set")

    env.meta.media_url = no_token
    with pytest.raises(M.MetaError, match="not set"):
        _apply(env)


def test_the_card_merge_waits_for_a_turn_in_flight(env):
    with ST.db() as c:
        assert ST.claim_reply_turn(c, CV_ONLY, "wamid.in.busy")
    with pytest.raises(RuntimeError, match="in flight"):
        _apply(env, CV_ONLY)


# --- the importer: loud failures ----------------------------------------------------------------------------------

def test_an_unreadable_source_database_names_the_path_and_the_grant(env):
    os.chmod(env.db, 0)
    try:
        with pytest.raises(IH.SourceAccessError) as exc:
            env.source()
    finally:
        os.chmod(env.db, 0o600)
    assert str(env.db) in str(exc.value) and "setfacl" in str(exc.value)


def test_an_unreadable_document_or_media_root_fails_loudly_in_dry_run_and_apply(env):
    cv = env.root_a / "clinics_recruiting_de/2/wamid.old.20/cv.pdf"
    os.chmod(cv, 0)
    try:
        with pytest.raises(IH.SourceAccessError, match="cv.pdf"):
            _dry(env, CV_ONLY)
        with pytest.raises(IH.SourceAccessError, match="cv.pdf"):
            _apply(env, CV_ONLY)
    finally:
        os.chmod(cv, 0o600)
    os.chmod(env.root_b, 0)
    try:
        with pytest.raises(IH.SourceAccessError, match="media root"):
            env.source()
    finally:
        os.chmod(env.root_b, 0o700)


def test_missing_database_queries_and_contract_violations_fail_loudly(env, tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        IH.Source.open(tmp_path / "nope.sqlite", QUERIES, SOURCE)
    partial = tmp_path / "partial.sql"
    partial.write_text("-- query: facts\nselect 1 as source_ref\n-- query: extra\nselect 1\n")
    with pytest.raises(ValueError, match="missing"):
        IH.Source.open(env.db, partial, SOURCE)
    bad = tmp_path / "bad.sql"
    bad.write_text(QUERIES.read_text().replace("AS qualification_path", "AS qualification_route"))
    with IH.Source.open(env.db, bad, SOURCE, [env.root_a]) as source, \
            pytest.raises(ValueError, match="unknown columns \\['qualification_route'\\]"):
        IH.import_phone(source, LEAD)
    wrong = tmp_path / "wrong.sql"
    wrong.write_text(QUERIES.read_text().replace("WHEN 'reject' THEN 'reject' END", "ELSE 'maybe' END"))
    with IH.Source.open(env.db, wrong, SOURCE, [env.root_a]) as source, \
            pytest.raises(ValueError, match="qualification_path must be one of"):
        IH.import_phone(source, CV_ONLY)
    with env.source() as source, pytest.raises(ValueError, match="did not canonicalize"):
        IH.import_phone(source, "not a number")


def test_a_document_path_leaving_its_media_root_raises(env):
    with pytest.raises(ValueError, match="leaves its media root"):
        IH.resolve_path("../../etc/passwd", [env.root_a])
    link = env.root_a / "escape.pdf"
    link.symlink_to(env.lebenslauf / "1" / "lebenslauf_standard.pdf")
    with pytest.raises(ValueError, match="outside its media root"):
        IH.resolve_path("escape.pdf", [env.root_a])


def test_cli_dry_run_exit_codes(env, tmp_path, monkeypatch, capsys):
    args = ["--db", str(env.db), "--queries", str(QUERIES), "--source", SOURCE, "--media-root", str(env.root_a),
            "--media-root", str(env.root_b)]
    assert IH.main(args + ["--phone", CV_ONLY]) == 0
    assert IH.main(args + ["--phone", LEAD, "--json"]) == 1, "a document that cannot be recovered is not a success"
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["phone"] == LEAD and report["not_recoverable"] == 1
    assert not C.SQLITE_PATH.exists()
    os.chmod(env.db, 0)
    try:
        assert IH.main(args + ["--phone", LEAD]) == 2
    finally:
        os.chmod(env.db, 0o600)
    assert "setfacl" in capsys.readouterr().err


# --- store migration ------------------------------------------------------------------------------------------------

def test_an_existing_wa_documents_table_gets_the_import_columns(tmp_path, monkeypatch):
    path = tmp_path / "wa.sqlite"
    c = sqlite3.connect(path)
    c.executescript("""create table wa_documents (id integer primary key, phone text not null, wamid text unique,
      media_id text, kind text not null, mime_type text, original_filename text, path text not null,
      sha256 text not null, size_bytes integer not null, received_at text not null, text text, text_key text,
      document_type text, certificate_level text);
      insert into wa_documents (phone, wamid, kind, path, sha256, size_bytes, received_at, document_type)
      values ('+4915550009999', 'wamid.in.1', 'document', '/x.pdf', 'ab', 1, '2026-09-01T00:00:00+00:00', 'lebenslauf');""")
    c.commit()
    c.close()
    monkeypatch.setattr(C, "SQLITE_PATH", path)
    with ST.db() as c, ST.db() as c2:
        [row] = ST.documents_for(c2, "+4915550009999")
        assert row["document_type"] == "lebenslauf" and row["import_ref"] is None and row["reuse_state"] is None
        a = ST.record_imported_document(c, "+4915550009999", None, "document", None, None, "/y.pdf", "cd", 1, "s",
                                        "ref-1", {})
        with pytest.raises(sqlite3.IntegrityError):
            ST.record_imported_document(c, "+4915550009999", None, "document", None, None, "/z.pdf", "ef", 1, "s",
                                        "ref-1", {})
        assert ST.imported_document(c, "s", "ref-1")["id"] == a


# --- Luna: the reuse question and the gate (TASK-102) ----------------------------------------------------------------

def _out(**kw):
    base = {"action": "reply_now_conversational", "bubbles": ["Dürfen wir Ihre früheren Unterlagen verwenden?"],
            "rationale": "", "escalate_to_manager": False, "escalate_reason": None, "no_send": False,
            "next_ask": None, "card_patch": {}}
    base.update(kw)
    return base


class Model:
    def __init__(self, monkeypatch, *outs):
        self.outs, self.payloads = list(outs), []
        real = LB.Client
        monkeypatch.setattr(LB, "Client", lambda: real(reply=self._reply))

    def _reply(self, system, user, session_id):
        self.payloads.append(json.loads(user))
        return self.outs.pop(0), session_id or "session-1"


def _deliver(env, message):
    body = {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID}, "messages": [message]}}]}]}
    return WAPI.handle_payload(body, client=env.meta)["results"][0]


def _say(env, phone, wamid, text):
    return _deliver(env, {"id": wamid, "from": phone[1:], "type": "text", "text": {"body": text}})


def _ready_for_documents(phone):
    with ST.db() as c:
        t = ST.thread(c, phone)
        t["slots"].update(region="Bayern", qualification_path="urkunde", qualification_ok=True, city="München",
                          housing_known=True)
        ST.save_thread(c, t)


def _imported_ids(phone=LEAD):
    return {d["document_type"]: d["id"] for d in _card(phone)["documents"]}


def test_imported_documents_count_for_nothing_until_confirmed(env):
    _apply(env)
    _ready_for_documents(LEAD)
    card = _card()
    board = LB.requirement_scoreboard(card)
    assert (board["cv_document"], board["qualification_document"], board["documents"]) == ("open", "open", "open")
    assert LB.market_snapshot(card)["shortlist"] == []
    assert REP.stage_for(card) == "qualifying"
    ids = _imported_ids()
    objective = board["next_objective"]
    assert objective.startswith("ask ONE plain yes/no whether we may use the CV (Lebenslauf) AND the German Urkunde "
                                "they sent us earlier")
    assert f"ids {ids['lebenslauf']}, {ids['urkunde']}" in objective and "newer ones" in objective
    assert "we do not hold" not in objective and str(ids["dienstplan"]) not in objective


def test_partial_holdings_ask_to_reuse_the_cv_and_name_the_urkunde_as_still_needed(env):
    _apply(env, CV_ONLY)
    _ready_for_documents(CV_ONLY)
    [cv] = _card(CV_ONLY)["documents"]
    objective = LB.requirement_scoreboard(_card(CV_ONLY))["next_objective"]
    assert f"whether we may use the CV (Lebenslauf) they sent us earlier (card.documents ids {cv['id']}" in objective
    assert "we do not hold the German Urkunde (not a home-country diploma): name it as still needed" in objective


def test_confirm_reuse_counts_the_named_documents_and_records_the_decision(env, monkeypatch):
    _apply(env)
    _ready_for_documents(LEAD)
    ids = _imported_ids()
    model = Model(monkeypatch,
                  _out(bubbles=["Sie hatten uns früher Ihren Lebenslauf und Ihre Urkunde geschickt. Dürfen wir diese "
                                "verwenden? Gern können Sie auch neuere schicken."]),
                  _out(bubbles=["Danke! Dann nutzen wir Ihre Unterlagen."],
                       document_reuse={"confirmed_ids": [ids["lebenslauf"], ids["urkunde"]]}))
    assert _say(env, LEAD, "wamid.in.1", "Hallo")["status"] == "sent"
    first = model.payloads[0]
    assert first["requirement_scoreboard"]["next_objective"].startswith("ask ONE plain yes/no whether we may use")
    assert first["card"]["prior_contact"]["summary"].startswith("Earlier contact on this number")
    assert _say(env, LEAD, "wamid.in.2", "Ja, gerne")["status"] == "sent"
    card = _card()
    states = {d["document_type"]: (d["reuse"], bool(d.get("reuse_decided_at"))) for d in card["documents"]}
    assert states == {"lebenslauf": ("confirmed", True), "urkunde": ("confirmed", True), "dienstplan": ("pending", False)}
    rows = {r["document_type"]: r for r in _docs()}
    assert (rows["lebenslauf"]["reuse_state"], rows["urkunde"]["reuse_state"], rows["dienstplan"]["reuse_state"]) == \
        ("confirmed", "confirmed", "pending")
    assert rows["lebenslauf"]["reuse_decided_at"] == next(d["reuse_decided_at"] for d in card["documents"]
                                                          if d["document_type"] == "lebenslauf")
    assert card["cv_text"] == CV_TEXT and card["urkunde_text"] == URKUNDE_TEXT
    board = LB.requirement_scoreboard(card)
    assert board["documents"] == "satisfied" and REP.stage_for(card) == "ready"


def test_decline_reuse_then_a_new_upload_follows_the_normal_gate(env, monkeypatch):
    _apply(env)
    _ready_for_documents(LEAD)
    ids = _imported_ids()
    model = Model(monkeypatch,
                  _out(bubbles=["Alles klar, dann schicken Sie mir bitte Lebenslauf und Urkunde als Foto oder PDF."],
                       document_reuse={"declined_ids": [ids["lebenslauf"], ids["urkunde"]]}),
                  _out(bubbles=["Danke für den Lebenslauf! Jetzt fehlt noch die Urkunde."]))
    _say(env, LEAD, "wamid.in.1", "Nein, ich schicke neue")
    card = _card()
    assert {d["document_type"]: d["reuse"] for d in card["documents"]}["lebenslauf"] == "declined"
    assert {r["document_type"]: r["reuse_state"] for r in _docs()}["urkunde"] == "declined"
    assert "cv_text" not in card
    board = LB.requirement_scoreboard(card)
    assert board["documents"] == "open"
    assert board["next_objective"].startswith("ask for BOTH the CV (Lebenslauf) AND the German Urkunde")

    env.meta.served["media-new-cv"] = b"Lebenslauf Anna Beispiel 2026, neu"
    result = _deliver(env, {"id": "wamid.in.2", "from": LEAD[1:], "type": "document",
                            "document": {"id": "media-new-cv", "mime_type": "application/pdf", "filename": "cv.pdf"}})
    assert result["status"] == "sent"
    card = _card()
    new = [d for d in card["documents"] if not d.get("imported")]
    assert [d["document_type"] for d in new] == ["lebenslauf"]
    assert model.payloads[1]["documents_just_received"] == [{"id": new[0]["id"], "document_type": "lebenslauf",
                                                              "certificate_level": "unknown"}]
    board = LB.requirement_scoreboard(card)
    assert (board["cv_document"], board["qualification_document"]) == ("satisfied", "open")
    assert "still-missing German Urkunde" in board["next_objective"]
    assert {d["document_type"]: d["reuse"] for d in card["documents"] if d.get("imported")} == \
        {"lebenslauf": "declined", "urkunde": "declined", "dienstplan": "pending"}
    assert card["cv_text"] == "Lebenslauf Anna Beispiel 2026, neu"


def test_a_new_upload_while_the_reuse_answer_is_pending_leaves_the_imported_one_pending(env, monkeypatch):
    _apply(env, CV_ONLY)
    _ready_for_documents(CV_ONLY)
    Model(monkeypatch, _out(bubbles=["Danke für die Urkunde! Dürfen wir Ihren früheren Lebenslauf verwenden?"]))
    env.meta.served["media-urkunde-new"] = URKUNDE_TEXT.encode()
    _deliver(env, {"id": "wamid.in.1", "from": CV_ONLY[1:], "type": "image",
                   "image": {"id": "media-urkunde-new", "mime_type": "image/jpeg"}})
    card = _card(CV_ONLY)
    board = LB.requirement_scoreboard(card)
    assert (board["cv_document"], board["qualification_document"]) == ("open", "satisfied")
    [imported] = [d for d in card["documents"] if d.get("imported")]
    assert imported["reuse"] == "pending"
    assert board["next_objective"].startswith("ask ONE plain yes/no whether we may use the CV (Lebenslauf)")


def test_document_reuse_must_name_imported_documents_of_the_card(env, monkeypatch):
    _apply(env, CV_ONLY)
    [cv] = _card(CV_ONLY)["documents"]
    for reuse, error in (({"confirmed_ids": [9999]}, "not imported documents on the card"),
                         ({"confirmed_ids": [cv["id"]], "declined_ids": [cv["id"]]}, "both confirmed and declined")):
        with ST.db() as c:
            t = ST.thread(c, CV_ONLY)
        with pytest.raises(RuntimeError, match=error):
            LB.turn("Ja", t, client=LB.Client(reply=lambda s, u, i: (_out(document_reuse=reuse), "session-1")))


def test_the_model_cannot_patch_documents_or_the_imported_history(env):
    _apply(env, CV_ONLY)
    with ST.db() as c:
        t = ST.thread(c, CV_ONLY)
    patch = {"documents": [{"id": 1, "document_type": "lebenslauf"}], "prior_contact": {}, "prior_placement": {}}
    d = LB.turn("Ja", t, client=LB.Client(reply=lambda s, u, i: (_out(card_patch=patch), "session-1")))
    assert d["slots"]["documents"] == t["slots"]["documents"] and d["slots"]["prior_contact"]["source"] == SOURCE


def test_a_withdrawn_confirmation_takes_the_text_off_the_card_again(env, monkeypatch):
    _apply(env, CV_ONLY)
    [cv] = _card(CV_ONLY)["documents"]
    with ST.db() as c:
        t = ST.thread(c, CV_ONLY)
        t["slots"]["cv_text"] = "Lebenslauf aus WhatsApp"
        ST.save_thread(c, t)
    Model(monkeypatch, _out(document_reuse={"confirmed_ids": [cv["id"]]}),
          _out(document_reuse={"declined_ids": [cv["id"]]}))
    _say(env, CV_ONLY, "wamid.in.1", "Ja, nehmen Sie den alten")
    assert _card(CV_ONLY)["cv_text"] == "Lebenslauf aus WhatsApp\n\n" + CV_TEXT + " (zweite Person)"
    _say(env, CV_ONLY, "wamid.in.2", "Doch nicht, ich schicke einen neuen")
    card = _card(CV_ONLY)
    assert card["cv_text"] == "Lebenslauf aus WhatsApp" and card["documents"][0]["reuse"] == "declined"
    assert _docs(CV_ONLY)[0]["reuse_state"] == "declined"


def test_the_prompt_explains_prior_contact_and_the_reuse_question():
    rules = {r.split(" (TASK-102)")[0]: r for r in LB.P.RULES if "(TASK-102)" in r}
    assert set(rules) == {"PRIOR CONTACT", "EARLIER DOCUMENTS"}
    earlier = rules["EARLIER DOCUMENTS"]
    for phrase in ("reuse=pending counts for nothing", "ONE plain yes/no", "confirmed_ids", "declined_ids",
                   "A new upload confirms or declines nothing", "neuere schicken"):
        assert phrase in earlier
    assert "never state it as the current status" in rules["PRIOR CONTACT"]
    assert "document_reuse" in LB.P.OUTPUT_INSTRUCTION and "prior_contact" in LB.P.OUTPUT_INSTRUCTION
    assert "document_reuse" in LB.OUTPUT_SCHEMA["properties"]


def test_the_threads_endpoint_lists_the_imported_history(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _apply(env, CV_ONLY)
    app = FastAPI()
    app.include_router(WAPI.router, prefix="/api")
    body = TestClient(app).get("/api/wa/threads", params={"phone": CV_ONLY}).json()
    assert [m["import_ref"] for m in body["imported_messages"]] == ["candidate_whatsapp_messages:wamid.old.20"]
    assert body["documents"][0]["import_source"] == SOURCE and "text" not in body["documents"][0]


def test_the_reuse_question_names_the_document_we_hold_not_the_one_the_path_asks_for(env):
    card = {"region": "Bayern", "qualification_path": "defizit", "qualification_ok": True, "city": "München",
            "housing_known": True,
            "documents": [{"id": 7, "document_type": "urkunde", "certificate_level": "fachkraft", "imported": True,
                           "reuse": "pending", "sent_at": None}]}
    objective = LB.requirement_scoreboard(card)["next_objective"]
    assert "whether we may use the German Urkunde they sent us earlier (card.documents ids 7" in objective
    assert "we do not hold the CV (Lebenslauf): name it as still needed" in objective


def test_a_phone_the_source_does_not_know_writes_nothing(env):
    report = _apply(env, "+4915550009876")
    assert report["found"] is False and report["documents"] == [] and report["not_recoverable"] == 0
    with ST.db() as c:
        assert c.execute("select count(*) from wa_threads").fetchone()[0] == 0
    assert _dry(env, LEAD)["found"] is True


# --- opt-outs and declines the source recorded (TASK-105) -----------------------------------------------------------

OUTREACH_ONLY = "+4915550007001"   # only in the Job+Wohnung blast table (no candidate card there)
SUPPRESSED = "+4915550007002"      # only on the suppression list, stored there as a national number
REOPENED = "+4915550007003"        # a lifecycle opt-out whose reopen_after has passed
LIFECYCLE_REASON = ('lifecycle closed declined_opt_out (source sync_portfolio_lifecycle); evidence '
                    '["correspondence_decline_or_polite_close"]')


def _old_sql(env, sql, *params):
    c = sqlite3.connect(env.db)
    c.execute(sql, params)
    c.commit()
    c.close()


def _old_meta(env, candidate_id, **keys):
    c = sqlite3.connect(env.db)
    meta = json.loads(c.execute("select metadata_json from candidates where id=?", (candidate_id,)).fetchone()[0])
    meta.update(keys)
    c.execute("update candidates set metadata_json=?, updated_at='2026-07-20T00:00:00' where id=?",
              (json.dumps(meta), candidate_id))
    c.commit()
    c.close()


def _record_opt_outs(env):
    """What the old system's code writes (candidate_lifecycle, candidate_bayern_housing_offer, placement stages,
    candidate_job_wohnung_outreach, suppression_list), plus rows that must not match."""
    _old_meta(env, 1, lifecycle={"status": "closed", "reason": "declined_opt_out",
                                 "closed_at": "2026-07-01T08:00:00+00:00", "reopen_after": None,
                                 "evidence": ["correspondence_decline_or_polite_close"],
                                 "source": "sync_portfolio_lifecycle", "updated_at": "2026-07-01T08:00:00+00:00"})
    _old_meta(env, 2, bayern_housing_offer={"status": "declined", "declined": True, "treat_as_ad_lead": False,
                                            "template_name": "synthetic_job_wohnung_de",
                                            "replied_at": "2026-07-02T09:00:00+00:00"})
    _old_sql(env, "insert into candidate_recruitment_state (workspace_id, candidate_id, placement_stage, next_action, "
                  "updated_at) values (1, 2, 'withdrawn', 'archive_candidate', '2026-07-05T12:00:00')")
    _old_sql(env, "insert into job_wohnung_outreach (workspace_id, phone_e164, phone_key, status, template_name, "
                  "replied_at) values (1, ?, ?, 'declined', 'synthetic_job_wohnung_de', '2026-07-03 10:00:00')",
             OUTREACH_ONLY, OUTREACH_ONLY[1:])
    _old_sql(env, "insert into job_wohnung_outreach (workspace_id, phone_e164, phone_key, status) "
                  "values (1, '+4915550007009', '4915550007009', 'sent')")
    _old_sql(env, "insert into suppression_list (workspace_id, scope, channel_type, value, reason, source_system, "
                  "raw_payload_json, created_at) values (1, 'workspace', 'phone', '0155-50007002', "
                  "'do_not_contact_request', 'clinic_connector', ?, '2026-07-04 11:00:00')",
             json.dumps({"channels": "phone"}))
    _old_sql(env, "insert into suppression_list (workspace_id, scope, channel_type, value, reason, source_system, "
                  "raw_payload_json, created_at) values (1, 'workspace', 'email', '015550007002', 'unsubscribed', "
                  "'snov', ?, '2026-07-04 11:00:00')", json.dumps({"channels": "email"}))
    _old_sql(env, "insert into suppression_list (scope, channel_type, value, reason, created_at) "
                  "values ('global', 'all', '+49 155 5000 9999', 'legal_block', '2026-07-04 11:00:00')")
    _old_sql(env, "insert into candidates (id, workspace_id, metadata_json) values (5, 1, ?)", json.dumps(
        {"phone": REOPENED, "wa_agent": {"slots": {"region": "Bayern"}},
         "lifecycle": {"status": "closed", "reason": "declined_opt_out", "closed_at": "2026-01-01T00:00:00+00:00",
                       "reopen_after": "2026-02-01T00:00:00+00:00"}}))


def test_opt_out_and_decline_records_are_reported_per_phone_with_what_and_when(env, capsys):
    _record_opt_outs(env)
    assert _dry(env)["opt_outs"] == [
        {"source_ref": "candidates:1:lifecycle", "kind": "opt_out", "at": "2026-07-01T08:00:00+00:00",
         "reason": LIFECYCLE_REASON, "phone": LEAD}]
    assert _dry(env, CV_ONLY)["opt_outs"] == [
        {"source_ref": "candidates:2:bayern_housing_offer", "kind": "decline", "at": "2026-07-02T09:00:00+00:00",
         "reason": "Job+Wohnung template answered No (synthetic_job_wohnung_de)", "phone": CV_ONLY},
        {"source_ref": "candidate_recruitment_state:2:withdrawn", "kind": "decline", "at": "2026-07-05T12:00:00+00:00",
         "reason": "placement stage withdrawn (next action archive_candidate)", "phone": CV_ONLY}]
    outreach = _dry(env, OUTREACH_ONLY)
    assert outreach["found"] is True and outreach["messages"] == {"found": 0, "already_imported": 0}
    assert outreach["opt_outs"] == [
        {"source_ref": "job_wohnung_outreach:1", "kind": "decline", "at": "2026-07-03T10:00:00+00:00",
         "reason": "job_wohnung_outreach declined (synthetic_job_wohnung_de)", "phone": OUTREACH_ONLY}]
    assert _dry(env, SUPPRESSED)["opt_outs"] == [
        {"source_ref": "suppression_list:1", "kind": "opt_out", "at": "2026-07-04T11:00:00+00:00",
         "reason": "suppression do_not_contact_request (clinic_connector)", "phone": "0155-50007002"}]
    reopened = _dry(env, REOPENED)
    assert reopened["found"] is True and reopened["opt_outs"] == []
    assert _dry(env, "+4915550007009")["found"] is False, "a sent blast row is not a decline"
    assert not C.SQLITE_PATH.exists()
    capsys.readouterr()
    assert IH.main(["--db", str(env.db), "--queries", str(QUERIES), "--source", SOURCE, "--phone", SUPPRESSED]) == 0
    out = capsys.readouterr().out
    assert ("  no contact wanted: opt_out 2026-07-04T11:00:00+00:00 suppression do_not_contact_request "
            "(clinic_connector) [suppression_list:1] (phone 0155-50007002)") in out
    assert ("  card declined by --apply: opt_out recorded by the earlier system old-system-test on 2026-07-04: "
            "suppression do_not_contact_request (clinic_connector)") in out


def test_an_imported_opt_out_marks_the_card_declined_and_luna_stays_silent_until_re_engagement(env, monkeypatch):
    _record_opt_outs(env)
    preview = _dry(env)["decline_import"]
    assert (preview["marked"], preview["declined_at"]) == (True, "2026-07-01T08:00:00+00:00")
    report = _apply(env)
    assert report["decline_import"]["marked"] is True
    card = _card()
    assert (card["declined"], card["declined_at"]) == (True, "2026-07-01T08:00:00+00:00")
    assert card["declined_reason"] == ("opt_out recorded by the earlier system old-system-test on 2026-07-01: "
                                       + LIFECYCLE_REASON)
    assert card["prior_opt_outs"] == [{"source_ref": "candidates:1:lifecycle", "kind": "opt_out",
                                       "at": "2026-07-01T08:00:00+00:00", "reason": LIFECYCLE_REASON, "phone": LEAD,
                                       "source": SOURCE}]
    assert card["prior_contact"]["summary"].endswith(
        "Recorded then, no contact wanted: opted out on 2026-07-01 (" + LIFECYCLE_REASON + ").")
    assert REP.stage_for(card) == "declined"

    model = Model(monkeypatch, _out(bubbles=[], no_send=True),
                  _out(re_engaged=True, bubbles=["Schön, von Ihnen zu hören! In welcher Stadt möchten Sie arbeiten?"]))
    result = _say(env, LEAD, "wamid.in.1", "Danke")
    assert env.meta.sent == [] and result["action"] == "declined_no_send"
    assert model.payloads[0]["card"]["declined"] is True and model.payloads[0]["card"]["prior_opt_outs"]
    with ST.db() as c:
        assert REP.ball_for(c, LEAD) == "silent"
    monkeypatch.setattr(C, "QUIET_HOURS_START", 0)
    monkeypatch.setattr(C, "QUIET_HOURS_END", 0)
    monkeypatch.setattr(C, "FOLLOWUP_TIER_MINUTES", [0])
    from app.wa.luna import followups as FU
    assert FU.run(client=env.meta) == [] and env.meta.sent == []

    _say(env, LEAD, "wamid.in.2", "Doch, ich suche jetzt eine Stelle in München")
    assert env.meta.sent == ["Schön, von Ihnen zu hören! In welcher Stadt möchten Sie arbeiten?"]
    card = _card()
    assert card["declined"] is False and card["re_engaged_at"]

    again = _apply(env)
    assert again["decline_import"]["new"] == [] and again["decline_import"]["marked"] is False
    assert _card()["declined"] is False, "a record already imported never undoes the re-engagement"
    assert len(_card()["prior_opt_outs"]) == 1


def test_a_decline_older_than_the_candidates_last_message_here_is_recorded_but_does_not_silence_them(env):
    with ST.db() as c:
        ST.record_inbound(c, OUTREACH_ONLY, "wamid.in.early", "Hallo, ich suche eine Stelle")
        t = ST.thread(c, OUTREACH_ONLY)
        t["last_inbound_at"] = "2026-08-01T09:00:00+00:00"
        ST.save_thread(c, t)
    _record_opt_outs(env)
    expected = "the candidate wrote here after the latest record: last_inbound_at 2026-08-01T09:00:00+00:00"
    assert _dry(env, OUTREACH_ONLY)["decline_import"]["not_marked"] == expected
    report = _apply(env, OUTREACH_ONLY)
    assert (report["decline_import"]["marked"], report["decline_import"]["not_marked"]) == (False, expected)
    card = _card(OUTREACH_ONLY)
    assert "declined" not in card and [e["source_ref"] for e in card["prior_opt_outs"]] == ["job_wohnung_outreach:1"]


def test_a_card_declined_here_keeps_its_own_decline(env):
    with ST.db() as c:
        t = ST.thread(c, SUPPRESSED)
        t["slots"].update(declined=True, declined_reason="kein Interesse", declined_at="2026-09-01T10:00:00+00:00")
        ST.save_thread(c, t)
    _record_opt_outs(env)
    report = _apply(env, SUPPRESSED)
    assert report["decline_import"]["not_marked"] == "the card is already declined (declined_at 2026-09-01T10:00:00+00:00)"
    card = _card(SUPPRESSED)
    assert (card["declined_reason"], card["declined_at"]) == ("kein Interesse", "2026-09-01T10:00:00+00:00")
    assert card["prior_opt_outs"][0]["source_ref"] == "suppression_list:1"


def test_opt_outs_contract_violations_fail_loudly(env, tmp_path):
    text = QUERIES.read_text()
    base = text[:text.index("-- query: opt_outs")]
    for select, error in (
            ("select 'r:1' as source_ref, 'maybe' as kind, '2026-07-01' as at, 'x' as reason", "kind must be one of"),
            ("select 'r:1' as source_ref, 'decline' as kind, 'yesterday' as at, 'x' as reason", "ISO 8601"),
            ("select 'r:1' as source_ref, 'decline' as kind, '2026-07-01' as at, null as reason", "without reason"),
            ("select 'r:1' as source_ref, 'decline' as kind, '2026-07-01' as at", "missing \\['reason'\\]")):
        path = tmp_path / "opt_outs.sql"
        path.write_text(base + "-- query: opt_outs\n" + select + "\n")
        with IH.Source.open(env.db, path, SOURCE, [env.root_a]) as source, pytest.raises(ValueError, match=error):
            IH.import_phone(source, CV_ONLY)
