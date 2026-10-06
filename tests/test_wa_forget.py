"""TASK-289 (Ivan, 2026-09-24): a phone-side chat delete let the real WhatsApp screen and this DB
drift apart, twice, the same night. Nothing here hard-deletes a row again -- "forget" sets
deleted_at instead, and every read the model reaches filters it out by default."""
import pytest

from app.wa import config as C
from app.wa import store as ST


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    conn = ST.db()
    yield conn
    conn.close()


# --- wa_messages ----------------------------------------------------------------------------------

def test_forget_message_hides_it_from_messages_for(db):
    ST.record_inbound(db, "+49111", "wamid.1", "hello")
    ST.record_inbound(db, "+49111", "wamid.2", "world")
    assert ST.forget_message(db, "wamid.1") is True
    seen = [m["wamid"] for m in ST.messages_for(db, "+49111")]
    assert seen == ["wamid.2"]


def test_forget_message_hides_it_from_message_by_wamid(db):
    ST.record_inbound(db, "+49111", "wamid.1", "hello")
    ST.forget_message(db, "wamid.1")
    assert ST.message_by_wamid(db, "wamid.1") is None


def test_include_deleted_still_reaches_a_forgotten_message(db):
    """Audit/admin tooling only (TASK-289) -- the model never passes include_deleted=True."""
    ST.record_inbound(db, "+49111", "wamid.1", "hello")
    ST.forget_message(db, "wamid.1")
    assert ST.message_by_wamid(db, "wamid.1", include_deleted=True) is not None
    seen = [m["wamid"] for m in ST.messages_for(db, "+49111", include_deleted=True)]
    assert seen == ["wamid.1"]


def test_forgetting_an_unknown_wamid_marks_nothing(db):
    assert ST.forget_message(db, "wamid.does-not-exist") is False


def test_forgetting_an_already_forgotten_message_is_not_reapplied(db):
    ST.record_inbound(db, "+49111", "wamid.1", "hello")
    assert ST.forget_message(db, "wamid.1", at="2026-09-24T00:00:00Z") is True
    assert ST.forget_message(db, "wamid.1", at="2026-09-24T01:00:00Z") is False
    row = ST.message_by_wamid(db, "wamid.1", include_deleted=True)
    assert row["deleted_at"] == "2026-09-24T00:00:00Z"


def test_forget_message_stamps_the_given_at_not_now(db):
    ST.record_inbound(db, "+49111", "wamid.1", "hello")
    ST.forget_message(db, "wamid.1", at="2026-09-23T21:19:00Z")
    row = ST.message_by_wamid(db, "wamid.1", include_deleted=True)
    assert row["deleted_at"] == "2026-09-23T21:19:00Z"


# --- wa_documents -----------------------------------------------------------------------------------

def _doc(db, phone="+49111", wamid="wamid.doc1", sha="sha-a"):
    return ST.record_document(db, phone, wamid, "media-1", "document", "application/pdf",
                              "cv.pdf", "/tmp/cv.pdf", sha, 1024)


def test_forget_document_hides_it_from_documents_for(db):
    _doc(db)
    doc_id = _doc(db, wamid="wamid.doc2", sha="sha-b")
    ST.forget_document(db, doc_id)
    seen = [d["sha256"] for d in ST.documents_for(db, "+49111")]
    assert seen == ["sha-a"]


def test_forget_document_hides_it_from_document_for_wamid(db):
    _doc(db)
    ST.forget_document(db, ST.document_for_wamid(db, "wamid.doc1")["id"])
    assert ST.document_for_wamid(db, "wamid.doc1") is None


def test_forget_document_hides_it_from_document_by_id(db):
    doc_id = _doc(db)
    ST.forget_document(db, doc_id)
    assert ST.document_by_id(db, doc_id) is None
    assert ST.document_by_id(db, doc_id, include_deleted=True) is not None


def test_a_forgotten_document_never_dedupes_a_fresh_upload_of_the_same_bytes(db):
    """The same file sent again after being forgotten is a new upload, not a repeat (TASK-289)."""
    doc_id = _doc(db)
    ST.forget_document(db, doc_id)
    assert ST.document_with_sha256(db, "+49111", "sha-a") is None
    assert ST.document_with_sha256(db, "+49111", "sha-a", include_deleted=True) is not None


def test_forgetting_an_unknown_document_id_marks_nothing(db):
    assert ST.forget_document(db, 999999) is False
