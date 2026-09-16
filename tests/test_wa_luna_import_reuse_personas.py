"""TASK-102 through the real claude CLI: a campaign recipient whose earlier contact was imported (facts, CV and
Urkunde) is not re-asked known facts, is asked one yes/no whether the earlier documents may be used, and both
answers are recorded. Marked ``llm`` (real model, real cost); every test runs twice (``run``).

The history comes from app/wa/luna/import_history.py with --apply against a synthetic old-system database and
synthetic files (the offline fixture's schema and deploy/import-history.example.sql); text extraction and document
classification are faked there, the conversation is not. Messages go through ``api.handle_payload`` with
WA_BRAIN=luna, WA_AUTOSEND=1, a fake Meta client and a tmp_path SQLite; the persona board serves the MCP tools.
Fictional persona and phone number only.

Run: ``pytest -q -m llm tests/test_wa_luna_import_reuse_personas.py -s``.
"""
import json
import re
import shutil
import sqlite3

import pytest

from app import cv as CV
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import store as ST
from app.wa.luna import import_history as IH
from tests.test_wa_luna_campaign_personas import BAYERN_QUESTION_RE, LEAD, PHONE_ID, Chat
from tests.test_wa_luna_import_history import OLD_SCHEMA, QUERIES, _fake_classify, _sha
from tests.test_wa_luna_personas import board  # noqa: F401  (fixture: persona board, tools included)

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not shutil.which(C.LUNA_CLAUDE_BIN),
                       reason=f"{C.LUNA_CLAUDE_BIN!r} is not on PATH -- WA_BRAIN=luna needs it installed"),
]

RUNS = pytest.mark.parametrize("run", [1, 2])
SOURCE = "old-system-persona"
CV_TEXT = "Lebenslauf Anna Kowalska (fiktiv), Pflegefachfrau, 2012-2026 Innere Medizin"
URKUNDE_TEXT = "Urkunde über die Erlaubnis zum Führen der Berufsbezeichnung Pflegefachfrau, Anna Kowalska (fiktiv)"

# The Urkunde yes/no gate question ("Haben Sie die deutsche Urkunde schon?"), not the reuse question.
URKUNDE_GATE_RE = re.compile(r"\b(haben|besitzen) sie\b[^?]*\b(urkunde|anerkennung)\b[^?]*\?", re.I)
HOUSING_RE = re.compile(r"wie viele personen|wohnung[^?]*\?", re.I)
REUSE_RE = re.compile(r"\b(verwenden|nutzen|benutzen|übernehmen|zurückgreifen|weiterverwenden)\b[^?]*\?", re.I)
EARLIER_RE = re.compile(r"\b(früher|bereits|schon|damals|letztes mal|zuvor|vorher)\b", re.I)
NEW_FILES_RE = re.compile(r"\b(schicken|senden|zusenden|hochladen|foto|pdf)\b", re.I)


@pytest.fixture()
def chat(board, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(C, "BRAIN", "luna")
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 0)
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    monkeypatch.setattr(CV, "extract_text", lambda filename, blob: blob.decode("utf-8"))
    monkeypatch.setattr(CV, "extract_text_vision", lambda blob, suffix=".png", client=None: blob.decode("utf-8"))
    monkeypatch.setattr(CV, "classify_document", lambda text, client=None: _fake_classify(text))
    _import_history(tmp_path)
    chat = Chat()
    yield chat
    print(chat.dump())


def _import_history(tmp_path):
    """The old system knew: Bayern, German Urkunde, one person for the flat; it holds her CV and Urkunde."""
    root = tmp_path / "old" / "media"
    (root / "c1").mkdir(parents=True)
    (root / "c1" / "cv.pdf").write_bytes(CV_TEXT.encode())
    (root / "c1" / "urkunde.jpg").write_bytes(URKUNDE_TEXT.encode())
    db = tmp_path / "old" / "sales_brain.sqlite"
    meta = {"phone": LEAD, "answers": {"region": "Bayern"},
            "wa_agent": {"slots": {"region": "Bayern", "qualification_path": "urkunde", "qualification_ok": True,
                                   "housing_known": True, "people_count": 1, "urkunde_status": "yes"}}}
    c = sqlite3.connect(db)
    c.executescript(OLD_SCHEMA)
    c.execute("insert into candidates (id, workspace_id, metadata_json) values (1, 1, ?)", (json.dumps(meta),))
    for i, (direction, kind, body, at) in enumerate((
            ("inbound", "text", "Hallo, ich bin Pflegefachfrau und suche in Bayern", "2026-05-04T09:00:00"),
            ("outbound", "text", "Schicken Sie mir gern Lebenslauf und Urkunde.", "2026-05-04T09:02:00"),
            ("inbound", "document", None, "2026-05-05T18:00:00"),
            ("inbound", "image", None, "2026-05-05T18:01:00")), 1):
        c.execute("""insert into candidate_whatsapp_messages (workspace_id, candidate_id, phone_e164, wamid, direction,
                     message_type, body, occurred_at) values (1, 1, ?, ?, ?, ?, ?, ?)""",
                  (LEAD, f"wamid.old.{i}", direction, kind, body, at))
    for aid, path, mime, text, old_class in ((1, "c1/cv.pdf", "application/pdf", CV_TEXT, "cv"),
                                             (2, "c1/urkunde.jpg", "image/jpeg", URKUNDE_TEXT, "urkunde")):
        c.execute("""insert into candidate_attachments (id, workspace_id, candidate_id, source_system, external_ref,
                     storage_path, mime_type, sha256, metadata_json, created_at)
                     values (?, 1, 1, 'meta_whatsapp_cloud', ?, ?, ?, ?, ?, '2026-05-05T18:00:00')""",
                  (aid, f"wamid.old.{aid + 2}", path, mime, _sha(text), json.dumps({"crm_doc_class": old_class})))
    c.commit()
    c.close()
    with IH.Source.open(db, QUERIES, SOURCE, [root]) as source:
        report = IH.import_phone(source, LEAD, apply=True)
    assert [d["action"] for d in report["documents"]] == ["imported", "imported"], report


def _text(bubbles):
    return " ".join(bubbles)


def _imported(chat):
    return {d["document_type"]: d for d in chat.card()["documents"] if d.get("imported")}


def _until_the_reuse_question(chat):
    """Campaign, then the candidate answers what Luna asks until Luna asks about the earlier documents. Known facts
    (Urkunde, housing, Bayern) must never be asked; 4 candidate turns are the test's own budget."""
    chat.campaign()
    bubbles, _ = chat.say("Ja, gerne")
    for _ in range(4):
        said, dump = _text(bubbles), chat.dump()
        assert bubbles, dump
        assert not URKUNDE_GATE_RE.search(said), f"the imported Urkunde answer was asked again: {dump}"
        assert not HOUSING_RE.search(said), f"the imported housing answer was asked again: {dump}"
        assert not BAYERN_QUESTION_RE.search(said), f"Bayern asked again: {dump}"
        if re.search(r"lebenslauf|urkunde|unterlagen|dokument", said, re.I):
            assert REUSE_RE.search(said) and EARLIER_RE.search(said), \
                f"documents came up without the reuse question about the earlier ones: {dump}"
            assert re.search(r"lebenslauf", said, re.I) and re.search(r"urkunde", said, re.I), \
                f"the reuse question does not name both documents we hold: {dump}"
            assert all(d["reuse"] == "pending" for d in _imported(chat).values()), dump
            return bubbles
        bubbles, _ = chat.say("Am liebsten in München.")
    pytest.fail(f"no reuse question within the budget: {chat.dump()}")


@RUNS
def test_imported_cv_and_urkunde_yes_confirms_both(chat, run):
    _until_the_reuse_question(chat)
    bubbles, _ = chat.say("Ja, die können Sie gern verwenden.")
    card, dump = chat.card(), chat.dump()
    assert {t: d["reuse"] for t, d in _imported(chat).items()} == {"lebenslauf": "confirmed", "urkunde": "confirmed"}, dump
    with ST.db() as c:
        rows = ST.documents_for(c, LEAD)
    assert {r["document_type"]: r["reuse_state"] for r in rows} == {"lebenslauf": "confirmed", "urkunde": "confirmed"}, dump
    assert LB.requirement_scoreboard(card)["documents"] == "satisfied", dump
    assert card.get("cv_text") == CV_TEXT and card.get("urkunde_text") == URKUNDE_TEXT, dump
    said = _text(bubbles)
    assert bubbles and not (re.search(r"lebenslauf|urkunde", said, re.I) and NEW_FILES_RE.search(said)
                            and not re.search(r"klinik", said, re.I)), f"new files asked after a yes: {dump}"


@RUNS
def test_imported_cv_and_urkunde_no_asks_for_new_files(chat, run):
    _until_the_reuse_question(chat)
    bubbles, _ = chat.say("Nein, ich schicke Ihnen lieber aktuelle Unterlagen.")
    card, dump = chat.card(), chat.dump()
    assert {t: d["reuse"] for t, d in _imported(chat).items()} == {"lebenslauf": "declined", "urkunde": "declined"}, dump
    assert LB.requirement_scoreboard(card)["documents"] == "open" and "cv_text" not in card, dump
    said = _text(bubbles)
    assert re.search(r"lebenslauf", said, re.I) and re.search(r"urkunde", said, re.I) and NEW_FILES_RE.search(said), \
        f"the new CV and Urkunde were not asked for: {dump}"
    bubbles, _ = chat.upload("lebenslauf", "unknown", "Lebenslauf Anna Kowalska (fiktiv), aktualisiert 2026")
    said, dump = _text(bubbles), chat.dump()
    board = LB.requirement_scoreboard(chat.card())
    assert (board["cv_document"], board["qualification_document"]) == ("satisfied", "open"), dump
    assert re.search(r"urkunde", said, re.I) and not REUSE_RE.search(said), \
        f"after the new CV Luna did not ask for the new Urkunde: {dump}"
