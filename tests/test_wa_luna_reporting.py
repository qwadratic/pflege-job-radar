"""Offline tests for app/wa/luna/reporting.py -- pure stage/ball derivation, no real CLI, no real
data."""
import pytest

from app.wa import config as C
from app.wa import store as ST
from app.wa.luna import reporting as REP


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    conn = ST.db()
    yield conn
    conn.close()


def test_stage_new_lead_for_an_empty_card():
    assert REP.stage_for({}) == "new_lead"


def test_stage_not_placeable_wins_over_everything_else():
    assert REP.stage_for({"qualification_ok": False, "anonymous_send_consent": True}) == "not_placeable"


def test_stage_qualifying_once_a_path_is_known():
    assert REP.stage_for({"qualification_path": "urkunde"}) == "qualifying"


def test_stage_documents_in_once_cv_text_present():
    assert REP.stage_for({"qualification_path": "urkunde", "cv_text": "..."}) == "documents_in"


_CV = {"id": 1, "document_type": "lebenslauf", "certificate_level": "unknown"}
_URKUNDE = {"id": 2, "document_type": "urkunde", "certificate_level": "fachkraft"}


def test_stage_ready_once_every_non_consent_requirement_is_satisfied():
    """TASK-91: 'every non-consent requirement' now includes documents -- TASK-96: the CV and the
    qualification document both received (card.documents), not just a verbal qualification claim."""
    card = {"qualification_path": "urkunde", "region": "bayern", "city": "München",
            "housing_needed": False, "cv_text": "Lebenslauf ...", "urkunde_text": "Urkunde ... volle Anerkennung",
            "documents": [_CV, _URKUNDE]}
    assert REP.stage_for(card) == "ready"


@pytest.mark.parametrize("documents", [[_CV], [_URKUNDE], None], ids=["cv_only", "urkunde_only", "legacy_no_list"])
def test_stage_documents_in_not_ready_until_both_documents_are_in(documents):
    """TASK-96: one document, or a legacy card with both text keys but no documents list, is not ready."""
    card = {"qualification_path": "urkunde", "region": "bayern", "city": "München",
            "housing_needed": False, "cv_text": "Lebenslauf ...", "urkunde_text": "Urkunde ..."}
    if documents is not None:
        card["documents"] = documents
    assert REP.stage_for(card) == "documents_in"


def test_stage_qualifying_not_ready_without_a_document():
    """The same card as above, minus a document -- TASK-91's documents gate means this must not
    report 'ready' (it would understate that nothing has actually been verified yet)."""
    card = {"qualification_path": "urkunde", "region": "bayern", "city": "München",
            "housing_needed": False}
    assert REP.stage_for(card) == "qualifying"


def test_stage_consented_once_consent_is_true():
    card = {"qualification_path": "urkunde", "region": "bayern", "city": "München",
            "housing_needed": False, "anonymous_send_consent": True}
    assert REP.stage_for(card) == "consented"


def test_ball_none_for_an_unknown_phone(db):
    assert REP.ball_for(db, "+49123") == "none"


def test_ball_us_when_the_last_message_is_inbound(db):
    ST.record_inbound(db, "+49123", "wamid.1", "Hallo")
    assert REP.ball_for(db, "+49123") == "us"


def test_ball_them_when_the_last_message_is_outbound(db):
    ST.record_inbound(db, "+49123", "wamid.1", "Hallo")
    ST.record_outbound(db, "+49123", "wamid.2", "Willkommen!")
    assert REP.ball_for(db, "+49123") == "them"


def test_report_row_is_none_for_a_phone_with_no_thread_and_creates_nothing(db):
    assert REP.report_row(db, "+49999") is None
    assert db.execute("select count(*) as n from wa_threads where phone=?", ("+49999",)).fetchone()["n"] == 0


def test_report_row_shape_for_a_real_thread(db):
    t = ST.thread(db, "+49123")
    t["slots"] = {"qualification_path": "urkunde", "city": "München"}
    ST.save_thread(db, t)
    ST.record_inbound(db, "+49123", "wamid.1", "Ich suche eine Stelle")
    row = REP.report_row(db, "+49123")
    assert row["phone"] == "+49123"
    assert row["stage"] == "qualifying"
    assert row["ball"] == "us"
    assert row["requirement_scoreboard"]["qualification"] == "satisfied"
