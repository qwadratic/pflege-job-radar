"""The Claude-driven WhatsApp brain (app/wa/luna_brain.py, WA_BRAIN=luna): the code-enforced
gates (opt-out, qualification reject, out-of-scope region), per-thread session persistence, the
CLI call contract, and the webhook wiring. No network and no `claude` subprocess: the model is a
fake ``reply`` callable injected into ``luna_brain.Client``, same seam as ``app/wa/meta.py``'s
fake transport.
"""
import json
import re
import subprocess
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import data as D
from app.wa import api as WAPI
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import store as ST

LEAD = "+491701234567"


def _jobs():
    return [
        {"posting_id": 1, "title": "Pflegefachkraft Intensivstation", "role_class": "pflegefachkraft",
         "department_hint": "Intensiv/IMC", "city": "München", "clinic_town": "München",
         "regierungsbezirk": "Oberbayern", "clinic_name": "Klinikum München Nord",
         "employer": "Klinikum München Nord", "employment_types": ["vollzeit"],
         "enr_housing": True, "verify_status": "live", "status": "open",
         "first_published": "2026-09-01", "fresh": True, "source_url": "https://example.org/job/1"},
        {"posting_id": 2, "title": "Pflegefachkraft OP", "role_class": "pflegefachkraft",
         "department_hint": "OP", "city": "Würzburg", "clinic_town": "Würzburg",
         "regierungsbezirk": "Unterfranken", "clinic_name": "Klinikum Würzburg",
         "employer": "Klinikum Würzburg", "employment_types": ["teilzeit"],
         "enr_housing": False, "verify_status": "live", "status": "open",
         "first_published": "2026-09-02", "fresh": True, "source_url": "https://example.org/job/2"},
    ]


@pytest.fixture()
def luna(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": _jobs(), "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    return {"slots": {}, "asked": []}


def _out(**kw):
    """A minimally valid reply_turn dict, overridable per test."""
    base = {"action": "reply_now_conversational", "bubbles": ["Hallo 🙂"], "rationale": "",
            "escalate_to_manager": False, "escalate_reason": None, "no_send": False,
            "next_ask": None, "card_patch": {}}
    base.update(kw)
    return base


def fake_client(out_or_fn):
    """A Client whose reply() returns a fixed dict (session id echoed back unchanged, as a
    no-op fake session would), or calls a function of (system, user, session_id) -> (dict,
    session_id)."""
    if callable(out_or_fn):
        fn = out_or_fn
    else:
        fn = lambda system, user, session_id: (out_or_fn, session_id)
    return LB.Client(reply=fn)


# --- gates enforced in code, never reaching the model ----------------------------------------

def test_stop_never_reaches_the_model(luna):
    calls = []
    d = LB.turn("STOP", luna, client=fake_client(lambda s, u, sid: calls.append(1) or (_out(), sid)))
    assert d["stopped"] is True and d["bubbles"] == [] and calls == []


def test_out_of_scope_region_never_reaches_the_model(luna):
    calls = []
    d = LB.turn("ich suche in Berlin", luna,
               client=fake_client(lambda s, u, sid: calls.append(1) or (_out(), sid)))
    assert calls == [], "a locked out-of-scope reply needs no model call"
    assert d["action"] == "out_of_scope_region"
    assert "Bayern" in d["bubbles"][0] and d["slots"]["region"] == "berlin"


def test_out_of_scope_region_is_whole_word(luna):
    assert LB.named_non_bavaria_land("ich mag Hessendorf") is None
    assert LB.named_non_bavaria_land("NRW-Fan-Artikel") is None
    assert LB.named_non_bavaria_land("ich suche in Hessen") == "hessen"
    assert LB.named_non_bavaria_land("Baden-Württemberg bitte") == "baden-württemberg"


def test_a_region_already_on_the_card_is_not_re_gated(luna):
    """Once Bayern is recorded, a later off-topic mention of another Land must not derail the chat."""
    luna["slots"]["region"] = "Bayern"
    d = LB.turn("mein Bruder wohnt in Hessen", luna, client=fake_client(_out(bubbles=["ok"])))
    assert d["action"] != "out_of_scope_region"


# --- the qualification-reject gate: locked wording, not the model's own phrasing --------------

def test_first_disqualification_uses_the_locked_reject_text_not_the_models_wording(luna):
    model_said = "Wir können es trotzdem versuchen, kein Problem!"
    out = _out(bubbles=[model_said], card_patch={"qualification_ok": False, "qualification_path": "reject"})
    d = LB.turn("ich bin Pflegehelferin", luna, client=fake_client(out))
    assert d["bubbles"] == [LB.P.REJECT_BODY_DE]
    assert model_said not in d["bubbles"]
    assert d["action"] == "explain_not_placeable"
    assert d["slots"]["qualification_ok"] is False


def test_disqualification_is_only_overridden_once(luna):
    """Already-rejected + still rejected must not keep clobbering the model's own wording."""
    luna["slots"]["qualification_ok"] = False
    out = _out(bubbles=["Wie besprochen können wir Ihnen leider nicht helfen."],
               card_patch={"qualification_ok": False})
    d = LB.turn("und jetzt?", luna, client=fake_client(out))
    assert d["bubbles"] == out["bubbles"]


# --- explicit button-confirmed consent (TASK-80): decided in code, never by the model ----------

def test_offering_the_anonymized_send_attaches_real_buttons(luna):
    out = _out(bubbles=["Darf ich Ihr Profil anonymisiert an diese Kliniken weiterleiten?"],
               card_patch={"anonymous_send_offered": True})
    d = LB.turn("Klingt gut", luna, client=fake_client(out))
    assert d["buttons"] == LB.CONSENT_BUTTONS
    assert d["slots"].get("anonymous_send_consent") is None, "offering is not the same as consenting"


def test_buttons_are_only_attached_on_the_turn_offering_first_flips_true(luna):
    luna["slots"]["anonymous_send_offered"] = True   # already offered on an earlier turn
    out = _out(bubbles=["Wie besprochen, dürfte ich Ihr Profil weiterleiten?"],
               card_patch={"anonymous_send_offered": True})
    d = LB.turn("ok", luna, client=fake_client(out))
    assert d["buttons"] == [], "re-stating an already-made offer must not re-attach the buttons"


def test_a_free_text_yes_does_not_grant_consent(luna):
    """Even if the model itself tries to claim consent from typed text, the harness must not
    trust it -- only an actual button tap may set anonymous_send_consent."""
    luna["slots"]["anonymous_send_offered"] = True
    out = _out(bubbles=["Alles klar, ich leite es weiter!"],
               card_patch={"anonymous_send_consent": True})
    d = LB.turn("Ja, gerne", luna, client=fake_client(out))
    assert d["slots"].get("anonymous_send_consent") is not True


def test_tapping_the_yes_button_grants_consent_in_code(luna):
    luna["slots"]["anonymous_send_offered"] = True
    out = _out(bubbles=["Super, danke für dein Vertrauen!"], card_patch={})
    d = LB.turn("Ja, gerne", luna, button_id=LB.CONSENT_YES_ID, client=fake_client(out))
    assert d["slots"]["anonymous_send_consent"] is True


def test_tapping_the_no_button_records_a_decline_in_code(luna):
    luna["slots"]["anonymous_send_offered"] = True
    out = _out(bubbles=["Kein Problem, melden Sie sich, wenn sich etwas ändert."], card_patch={})
    d = LB.turn("Nein danke", luna, button_id=LB.CONSENT_NO_ID, client=fake_client(out))
    assert d["slots"]["anonymous_send_consent"] is False


def test_a_button_tap_before_any_offer_is_a_no_op_for_consent(luna):
    """A stray/replayed button id on a card that never actually offered must not fabricate
    consent out of nothing."""
    out = _out(bubbles=["Hallo!"], card_patch={})
    d = LB.turn("Ja, gerne", luna, button_id=LB.CONSENT_YES_ID, client=fake_client(out))
    assert "anonymous_send_consent" not in d["slots"]


def test_the_model_sees_is_button_reply_true_only_for_an_actual_tap(luna):
    seen = {}

    def fn(system, user, session_id):
        seen["payload"] = json.loads(user)
        return _out(), session_id

    luna["slots"]["anonymous_send_offered"] = True
    LB.turn("Ja, gerne", luna, button_id=LB.CONSENT_YES_ID, client=fake_client(fn))
    assert seen["payload"]["is_button_reply"] is True

    seen.clear()
    LB.turn("Ja, gerne", luna, client=fake_client(fn))
    assert seen["payload"]["is_button_reply"] is False


# --- a normal turn: the model decides, the harness only supplies state -----------------------

def test_a_normal_turn_updates_the_card_from_card_patch(luna):
    out = _out(bubbles=["Welche Region interessiert Sie?"],
               card_patch={"region": "Bayern", "role_verdict": "unclear"})
    d = LB.turn("Hallo", luna, client=fake_client(out))
    assert d["slots"]["region"] == "Bayern" and d["slots"]["role_verdict"] == "unclear"
    assert d["bubbles"] == out["bubbles"]
    assert d["stopped"] is False


def test_no_send_clears_the_bubbles_but_still_updates_the_card(luna):
    out = _out(bubbles=["would have said something"], no_send=True, card_patch={"region": "Bayern"})
    d = LB.turn("ok danke", luna, client=fake_client(out))
    assert d["bubbles"] == []
    assert d["slots"]["region"] == "Bayern"


def test_an_empty_bubbles_array_is_treated_as_silent_even_without_the_no_send_flag(luna):
    """Regression: a real model turn came back with bubbles=[] but no_send left false/absent
    (tests/test_wa_luna_personas.py surfaced this against the live CLI) -- the harness must not
    crash on that combination. An empty array is unambiguous on its own."""
    out = _out(bubbles=[], no_send=False)
    d = LB.turn("ok danke", luna, client=fake_client(out))
    assert d["bubbles"] == []


def test_escalation_is_recorded_on_the_card_and_still_sends_the_next_ask(luna):
    out = _out(bubbles=["Gute Frage, das gebe ich weiter. Und wo suchen Sie?"],
               escalate_to_manager=True, escalate_reason="asked about visa specifics")
    d = LB.turn("wie ist das mit dem Visum?", luna, client=fake_client(out))
    assert d["slots"]["_escalated"] is True
    assert d["slots"]["_escalate_reason"] == "asked about visa specifics"
    assert d["bubbles"] == out["bubbles"], "escalating must not mean going silent"


def test_the_model_receives_the_market_snapshot_and_scoreboard_but_not_a_history_replay(luna):
    """The resumed Claude Code session already has every earlier turn -- this module must not
    also serialize the thread into the payload, or every turn would pay for it twice."""
    seen = {}

    def capture(system_text, user_text, session_id):
        seen["system"] = system_text
        seen["user"] = json.loads(user_text)
        return _out(), session_id

    luna["slots"] = {"city": "München"}
    LB.turn("Intensivstation bitte", luna, client=fake_client(capture))
    assert seen["user"]["market_snapshot"]["open_jobs"] == 2
    assert seen["user"]["market_snapshot"]["matches"] == [], (
        "TASK-91: no per-city preview list anymore -- a live tool call answers city-specific "
        "questions, market_snapshot only ever gets a shortlist once fully ready to close")
    assert seen["user"]["requirement_scoreboard"]["region"] == "open"
    assert seen["user"]["requirement_scoreboard"]["next_objective"], (
        "TASK-91: a computed next_objective hint must always be present")
    assert seen["user"]["latest_inbound"] == "Intensivstation bitte"
    assert "thread" not in seen["user"], "history now lives in the resumed session, not the payload"
    assert "Valentina" in seen["system"]
    assert "NDT" not in seen["system"], "the vendored prompt must not carry the source's company name"


def test_market_snapshot_matches_only_once_fully_ready_to_close():
    """TASK-91: market_snapshot carries no early per-city/per-department preview anymore -- only
    the aggregate open_jobs total always, and matches/shortlist once qualification, city-or-
    department, housing AND documents are all satisfied. A candidate with city+qualification but
    no document read yet must still see an empty matches list -- that is the new documents gate,
    not a regression."""
    empty = LB.market_snapshot({})
    assert empty["matches"] == [] and empty["shortlist"] == []
    assert empty["open_jobs"] == 2, "the one aggregate number stays present even on an empty card"
    narrowed_no_docs = LB.market_snapshot({"city": "München", "qualification_path": "urkunde",
                                           "qualification_ok": True, "housing_known": True})
    assert narrowed_no_docs["matches"] == [], (
        "qualification/city/housing alone must not populate matches without a document too")
    ready = LB.market_snapshot({"city": "München", "qualification_ok": True, "housing_known": True, **_DOC})
    assert ready["matches"] and all(m["city"] == "München" for m in ready["matches"])


def test_requirement_scoreboard_reflects_the_card():
    assert LB.requirement_scoreboard({})["qualification"] == "open"
    assert LB.requirement_scoreboard({"qualification_path": "urkunde"})["qualification"] == "satisfied"
    assert LB.requirement_scoreboard({"qualification_path": "reject"})["qualification"] == "blocked"
    assert LB.requirement_scoreboard({"region": "Bayern"})["region"] == "satisfied"


def test_requirement_scoreboard_documents_gate_and_next_objective():
    """TASK-91/TASK-96: documents is open until both documents have arrived (card.documents), and
    next_objective names the single highest-priority open gate."""
    board = LB.requirement_scoreboard({})
    assert board["documents"] == "open"
    assert board["next_objective"] == "ask as a plain yes/no whether they are looking for a job in Bayern"
    ready_for_docs = LB.requirement_scoreboard(_ALL_BUT_DOCUMENTS)
    assert ready_for_docs["next_objective"].startswith("ask for BOTH the CV (Lebenslauf) AND the German Urkunde")
    fully_ready = LB.requirement_scoreboard({**_ALL_BUT_DOCUMENTS, "documents": [_CV, _URKUNDE]})
    assert fully_ready["next_objective"].startswith("run the close sequence")


# --- TASK-96: both the CV and the qualification document for the path, or the close stays shut ---
# (Ivan's manual test 2026-09-13: a claimed Urkunde plus a sent Lebenslauf unlocked the close.)

_CV = {"id": 1, "document_type": "lebenslauf", "certificate_level": "unknown"}
_URKUNDE = {"id": 2, "document_type": "urkunde", "certificate_level": "fachkraft"}
_URKUNDE_UNKNOWN_LEVEL = {"id": 3, "document_type": "urkunde", "certificate_level": "unknown"}
_URKUNDE_HELFER = {"id": 4, "document_type": "urkunde", "certificate_level": "helfer"}
_DEFIZITBESCHEID = {"id": 5, "document_type": "defizitbescheid", "certificate_level": "unknown"}
_AUFENTHALTSTITEL = {"id": 6, "document_type": "aufenthaltstitel", "certificate_level": "unknown"}
_DIENSTPLAN = {"id": 7, "document_type": "dienstplan", "certificate_level": "unknown"}
_OTHER = {"id": 8, "document_type": "other", "certificate_level": "unknown"}
_FOREIGN_DIPLOMA = {"id": 9, "document_type": "auslaendisches_diplom", "certificate_level": "unknown"}
_ALL_BUT_DOCUMENTS = {"region": "Bayern", "qualification_path": "urkunde", "qualification_ok": True,
                      "city": "München", "housing_known": True}

_BOTH = ("ask for BOTH the CV (Lebenslauf) AND the German Urkunde (not a home-country diploma) as photos/PDFs, "
         "together in one ask -- ")
_URKUNDE_MISSING = ("ask for the still-missing German Urkunde as a photo/PDF (not a home-country diploma; the CV is "
                    "already in) -- ")
_CV_MISSING = "ask for the still-missing CV (Lebenslauf) as a photo/PDF (the qualification document is already in) -- "
_DEFIZIT_BOTH = ("ask for BOTH the CV (Lebenslauf) AND the Defizitbescheid (an already-issued German Fachkraft "
                 "Urkunde counts too) as photos/PDFs, together in one ask -- ")
_DEFIZIT_MISSING = ("ask for the still-missing Defizitbescheid as a photo/PDF (an already-issued German Fachkraft "
                    "Urkunde counts too; the CV is already in) -- ")

# (id, qualification_path, extra card keys, cv_document, qualification_document, next_objective prefix)
_GATE_CASES = [
    ("nothing", "urkunde", {}, "open", "open", _BOTH),
    ("cv_only", "urkunde", {"documents": [_CV]}, "satisfied", "open", _URKUNDE_MISSING),
    ("urkunde_only", "urkunde", {"documents": [_URKUNDE]}, "open", "satisfied", _CV_MISSING),
    ("cv_urkunde_fachkraft", "urkunde", {"documents": [_CV, _URKUNDE]}, "satisfied", "satisfied", None),
    ("urkunde_fachkraft_then_cv", "urkunde", {"documents": [_URKUNDE, _CV]}, "satisfied", "satisfied", None),
    ("cv_urkunde_unknown_level", "urkunde", {"documents": [_CV, _URKUNDE_UNKNOWN_LEVEL]}, "satisfied",
     "satisfied", None),
    ("cv_urkunde_helfer", "urkunde", {"documents": [_CV, _URKUNDE_HELFER]}, "satisfied", "open", _URKUNDE_MISSING),
    ("cv_defizitbescheid_defizit_path", "defizit", {"documents": [_CV, _DEFIZITBESCHEID]}, "satisfied",
     "satisfied", None),
    ("cv_defizitbescheid_kenntnispruefung_path", "kenntnispruefung", {"documents": [_CV, _DEFIZITBESCHEID]},
     "satisfied", "satisfied", None),
    ("cv_urkunde_fachkraft_defizit_path", "defizit", {"documents": [_CV, _URKUNDE]}, "satisfied", "satisfied",
     None),
    ("cv_urkunde_helfer_defizit_path", "defizit", {"documents": [_CV, _URKUNDE_HELFER]}, "satisfied", "open",
     _DEFIZIT_MISSING),
    ("defizit_path_nothing", "defizit", {}, "open", "open", _DEFIZIT_BOTH),
    ("cv_defizitbescheid_urkunde_path", "urkunde", {"documents": [_CV, _DEFIZITBESCHEID]}, "satisfied", "open",
     _URKUNDE_MISSING),
    ("cv_aufenthaltstitel", "urkunde", {"documents": [_CV, _AUFENTHALTSTITEL]}, "satisfied", "open",
     _URKUNDE_MISSING),
    ("cv_dienstplan", "urkunde", {"documents": [_CV, _DIENSTPLAN]}, "satisfied", "open", _URKUNDE_MISSING),
    ("cv_other", "urkunde", {"documents": [_CV, _OTHER]}, "satisfied", "open", _URKUNDE_MISSING),
    ("aufenthaltstitel_only", "urkunde", {"documents": [_AUFENTHALTSTITEL]}, "open", "open", _BOTH),
    # TASK-96 review: a home-country diploma is not the German Urkunde, and not a Defizitbescheid either.
    ("cv_foreign_diploma", "urkunde", {"documents": [_CV, _FOREIGN_DIPLOMA]}, "satisfied", "open", _URKUNDE_MISSING),
    ("cv_foreign_diploma_defizit_path", "defizit", {"documents": [_CV, _FOREIGN_DIPLOMA]}, "satisfied", "open",
     _DEFIZIT_MISSING),
    ("cv_foreign_diploma_kenntnispruefung_path", "kenntnispruefung", {"documents": [_CV, _FOREIGN_DIPLOMA]},
     "satisfied", "open", _DEFIZIT_MISSING),
    ("foreign_diploma_then_urkunde", "urkunde", {"documents": [_FOREIGN_DIPLOMA, _CV, _URKUNDE]}, "satisfied",
     "satisfied", None),
    ("legacy_text_keys_no_documents_list", "urkunde",
     {"cv_text": "Lebenslauf ...", "urkunde_text": "Urkunde ... volle Anerkennung",
      "document_type": "urkunde", "certificate_level": "fachkraft"}, "open", "open", _BOTH),
]


@pytest.mark.parametrize("path, extra, cv_document, qualification_document, objective",
                         [c[1:] for c in _GATE_CASES], ids=[c[0] for c in _GATE_CASES])
def test_documents_gate_needs_the_cv_and_the_qualification_document_for_the_path(
        path, extra, cv_document, qualification_document, objective):
    card = {**_ALL_BUT_DOCUMENTS, "qualification_path": path, **extra}
    board = LB.requirement_scoreboard(card)
    assert (board["cv_document"], board["qualification_document"]) == (cv_document, qualification_document)
    both = cv_document == qualification_document == "satisfied"
    assert board["documents"] == ("satisfied" if both else "open")
    assert LB._documents_satisfied(card) is both
    if both:
        assert board["next_objective"].startswith("run the close sequence")
    else:
        assert board["next_objective"].startswith(objective), board["next_objective"]
        assert "every turn until it arrives" in board["next_objective"]
    assert "and/or" not in board["next_objective"]


@pytest.mark.parametrize("path, extra, cv_document, qualification_document, objective",
                         [c[1:] for c in _GATE_CASES], ids=[c[0] for c in _GATE_CASES])
def test_shortlist_stays_empty_until_both_documents_are_in(path, extra, cv_document, qualification_document,
                                                           objective):
    snap = LB.market_snapshot({**_ALL_BUT_DOCUMENTS, "qualification_path": path, **extra})
    assert bool(snap["shortlist"]) is (cv_document == qualification_document == "satisfied")
    assert snap["matches"] == snap["shortlist"]


def test_the_photo_pdf_hint_sits_on_the_document_being_asked_for():
    """TASK-96 review: the template read 'ask for the still-missing Urkunde -- the CV is already in as a
    photo/PDF', putting the format hint on the document that had already arrived."""
    for card in ({**_ALL_BUT_DOCUMENTS, "documents": [_CV]}, {**_ALL_BUT_DOCUMENTS, "documents": [_URKUNDE]},
                 {**_ALL_BUT_DOCUMENTS, "qualification_path": "defizit", "documents": [_CV]}):
        objective = LB.requirement_scoreboard(card)["next_objective"]
        assert "already in as a photo/PDF" not in objective, objective
        missing = objective.split(" as a photo/PDF", 1)[0]
        assert missing.startswith("ask for the still-missing ") and "already in" not in missing, objective


_REJECTED = {"region": "Bayern", "qualification_path": "reject", "qualification_ok": False}


@pytest.mark.parametrize("extra", [{}, {"region": None}, {"city": "München", "housing_known": True},
                                   {"city": "München", "housing_known": True, "documents": [_CV]}],
                         ids=["nothing_else", "no_region", "city_and_housing", "city_housing_and_cv"])
def test_a_rejected_candidate_gets_the_not_placeable_objective_never_a_document_ask(extra):
    """TASK-96 review: next_objective skipped the blocked qualification and fell through to 'ask for BOTH the
    CV AND the qualification document ... every turn until it arrives' -- against NOT PLACEABLE."""
    board = LB.requirement_scoreboard({**_REJECTED, **extra})
    assert board["qualification"] == "blocked"
    assert board["next_objective"] == LB._NOT_PLACEABLE_OBJECTIVE
    assert "document" not in board["next_objective"].split(":", 1)[0]


@pytest.mark.parametrize("path", [None, "unknown", "reject"])
def test_no_document_counts_as_the_qualification_document_without_an_accepted_path(path):
    card = {"qualification_path": path, "documents": [_CV, _URKUNDE, _DEFIZITBESCHEID]}
    board = LB.requirement_scoreboard(card)
    assert (board["cv_document"], board["qualification_document"], board["documents"]) == \
        ("satisfied", "open", "open")


def test_documents_just_received_reaches_the_model_once_and_is_never_saved_back(luna):
    seen = []

    def capture(system, user, session_id):
        seen.append(json.loads(user))
        return _out(), session_id

    luna["slots"] = {"documents": [_CV, _AUFENTHALTSTITEL], "_documents_just_received": [_AUFENTHALTSTITEL]}
    d = LB.turn("", luna, client=fake_client(capture))
    assert seen[0]["documents_just_received"] == [_AUFENTHALTSTITEL]
    assert "_documents_just_received" not in seen[0]["card"]
    assert seen[0]["card"]["documents"] == [_CV, _AUFENTHALTSTITEL]
    assert "_documents_just_received" not in d["slots"]

    LB.turn("ok", {"slots": d["slots"], "asked": d["asked"]}, client=fake_client(capture))
    assert seen[1]["documents_just_received"] == []


def test_prompt_document_ask_requires_both_and_re_asks_the_missing_one_every_turn():
    ask = _rule("DOCUMENT ASK (TASK-96)")
    assert "the CV (Lebenslauf) AND the qualification document for their path" in ask
    assert "on the urkunde path the Urkunde; on the defizit or kenntnispruefung path the Defizitbescheid" in ask
    assert "ask for BOTH by name in one request" in ask
    assert "Never \"und/oder\", never \"oder\" between the two" in ask
    assert "UNTIL BOTH ARE IN: every one of your turns names the document(s) still missing" in ask
    for case in ("one document just arrived", "the wrong type arrived", "will send it later"):
        assert case in ask
    assert "a Ja/Ok to your ask is a promise to send, the document is still missing" in ask
    system = LB.P.system_prompt(LB._CONSTITUTION_TEXT, LB._QUALIFICATION_TEXT)
    assert "do not repeat the ask every turn" not in system
    assert "CV and/or Urkunde" not in system
    assert "Asking again for a document that has not arrived is not re-asking a fact" in _rule("LANGUAGE")
    assert "A document still missing per requirement_scoreboard is not such a fact" in _rule("MEMORY")
    assert "documents_just_received" in _rule("CV/URKUNDE TEXT")
    assert "\"other\" means the file is neither a CV nor a qualification document" in _rule("DOCUMENT TYPE")
    # TASK-96 review: a home-country diploma is classified apart from the German Urkunde and never counts.
    assert "document_type=\"auslaendisches_diplom\" is a nursing diploma" in _rule("DOCUMENT TYPE")
    assert "NOT the Urkunde, even when the candidate calls it that" in _rule("DOCUMENT TYPE")
    assert "a home-country nursing diploma (auslaendisches_diplom) is not it, on any path" in ask
    close = _rule("CLOSE SEQUENCE")
    assert "never a document the candidate only said they have" in close
    think7 = next(s for s in LB.P.THINK_ORDER if s.startswith("7) CONVERGE"))
    assert "on every turn until both have arrived" in think7


# --- TASK-97: no either/or question a bare "ja" answers (Ivan's manual test 2026-09-13: "Urkunde
# schon, oder noch im Anerkennungsverfahren (Defizitbescheid/Kenntnisprüfung)?" got "ja" twice).

def _rule(prefix):
    return next(r for r in LB.P.RULES if r.startswith(prefix))


def test_next_objective_for_qualification_is_a_yes_no_urkunde_ask_not_three_options():
    label = LB.requirement_scoreboard({"region": "Bayern"})["next_objective"]
    assert label.startswith("clarify qualification: first a plain yes/no whether the German Urkunde")
    assert "only on no" in label
    assert "Urkunde/Defizitbescheid/Kenntnisprüfung" not in label, "the old label invited one three-way question"


def test_prompt_reads_a_bare_ja_as_yes_only_after_a_yes_no_question():
    think4 = next(s for s in LB.P.THINK_ORDER if s.startswith("4) INTERPRET"))
    assert "after YOUR yes/no question = yes" in think4
    assert "either/or question" in think4 and "ambiguous" in think4
    assert "the very next re-ask is a strict yes/no" in think4 and "never another compound question" in think4
    assert "either/or ask closes nothing" in _rule("CHAT OVER CARD")
    assert "a bare Ja/Ok to an either/or question always is" in _rule("GUESS FREELY")


def test_prompt_forbids_either_or_questions_and_orders_the_qualification_ask():
    yes_no = _rule("YES/NO QUESTIONS (TASK-97)")
    assert "never ask an either/or question" in yes_no and "for any gate" in yes_no
    assert "ONE option as a plain yes/no question" in yes_no and "only after a Nein" in yes_no
    qual = _rule("QUALIFICATION:")
    assert "first whether they already hold the German Urkunde" in qual
    assert "qualification_path=urkunde; only after a Nein" in qual
    assert "Never bundle Urkunde, Anerkennungsverfahren, Defizitbescheid and Kenntnisprüfung" in qual
    system = LB.P.system_prompt(LB._CONSTITUTION_TEXT, LB._QUALIFICATION_TEXT)
    assert yes_no in system and qual in system


def test_the_frozen_system_prompt_carries_no_either_or_example_question():
    """The constitution is injected verbatim; its old examples ('Suchen Sie eher in Bayern, oder in
    einem anderen Bundesland?', 'Pflege-Urkunde oder einen Defizitbescheid?') and its 'confirm with
    Ja/Ok/Passt, do NOT re-ask which of the three' line taught the model the either/or ask."""
    system = LB.P.system_prompt(LB._CONSTITUTION_TEXT, LB._QUALIFICATION_TEXT)
    either_or = re.findall(r'[^.!?"\n]*\boder\b[^.!?"\n]*\?', system)
    assert either_or and all("X oder Y?" in q for q in either_or), (
        f"only the rule's own 'X oder Y?' placeholder may appear: {either_or!r}")
    assert "which of the three" not in system


def test_no_gate_label_or_constitution_line_invites_a_yes_no_frame_around_options():
    """TASK-97 review (live, 3/3 runs each): 'Gibt es eine Stadt ..., z. B. München ... oder Würzburg?',
    '... Stadt im Blick ... oder ist Ihnen der Fachbereich wichtiger?', 'Ziehen Sie allein um, oder ...?'.
    Sources: the city label 'narrow down a city or department preference', constitution live_market
    'ONE question (city size, department, or a named city)' and housing_principle.ask 'allein vs Familie'."""
    labels = dict(LB._OBJECTIVE_ORDER)
    assert labels["region"] == "ask as a plain yes/no whether they are looking for a job in Bayern"
    assert labels["city_or_department"].startswith("ask which city in Bayern they want to work in, as an open question")
    assert "no yes/no frame around a list of cities" in labels["city_or_department"]
    assert labels["housing"] == "ask how many people would live in the flat, as an open question"
    system = LB.P.system_prompt(LB._CONSTITUTION_TEXT, LB._QUALIFICATION_TEXT)
    for gone in ("allein vs Familie", "city size, department, or a named city", "Bayern vs. another Bundesland",
                 "narrow down a city or department preference"):
        assert gone not in system and gone not in json.dumps(LB._OBJECTIVE_ORDER), gone
    assert "one open question: how many people would live in the flat" in LB._CONSTITUTION_TEXT
    assert "never a yes/no frame around a list of cities" in LB._CONSTITUTION_TEXT
    yes_no = _rule("YES/NO QUESTIONS (TASK-97)")
    assert "A yes/no frame around options is the same mistake" in yes_no
    assert "sets a city against a department" in yes_no and "against moving with family" in yes_no


def test_the_constitution_media_rule_no_longer_stops_the_document_ask():
    """TASK-96 review: media_unreadable_rule said 'this assistant cannot read attachments yet' and 'do not
    re-ask for a document they already sent' -- the opposite of DOCUMENT ASK for an unusable file."""
    system = LB.P.system_prompt(LB._CONSTITUTION_TEXT, LB._QUALIFICATION_TEXT)
    assert "cannot read attachments yet" not in system
    assert "re-ask for a document they already sent" not in system
    rule = json.loads((LB._LUNA_DIR / "constitution.json").read_text(encoding="utf-8"))["media_unreadable_rule"]
    assert "ask for that document again" in rule["behavior"] and "DOCUMENT ASK" in rule["behavior"]
    think6 = next(s for s in LB.P.THINK_ORDER if s.startswith("6) UNREADABLE MEDIA"))
    assert "ask for that document again (DOCUMENT ASK)" in think6


# --- TASK-82: market_snapshot's ready_to_close must agree with requirement_scoreboard's own
# city_or_department gate -- a live e2e run found a candidate genuinely flexible on department
# (a real, valid answer) saw the scoreboard say "satisfied" while the shortlist never actually
# populated, since ready_to_close silently required BOTH city AND department_pref.

def test_requirement_scoreboard_city_or_department_is_satisfied_by_either_alone():
    assert LB.requirement_scoreboard({"city": "München"})["city_or_department"] == "satisfied"
    assert LB.requirement_scoreboard({"department_pref": "Intensiv/IMC"})["city_or_department"] == "satisfied"
    assert LB.requirement_scoreboard({})["city_or_department"] == "open"


_DOC = {"qualification_path": "urkunde", "documents": [_CV, _URKUNDE]}   # TASK-96: both documents in


def test_shortlist_appears_with_only_department_known_no_city():
    card = {"qualification_ok": True, "department_pref": "Intensiv/IMC", "housing_known": True, **_DOC}
    snap = LB.market_snapshot(card)
    assert snap["shortlist"], (
        "a candidate flexible on city but with a stated department must still reach a shortlist, "
        "matching requirement_scoreboard's own city_or_department == satisfied verdict")


def test_shortlist_appears_with_only_city_known_no_department():
    card = {"qualification_ok": True, "city": "München", "housing_known": True, **_DOC}
    snap = LB.market_snapshot(card)
    assert snap["shortlist"], (
        "a candidate flexible on department but with a stated city (a real, answered preference, "
        "not a missing one) must still reach a shortlist -- this is the exact regression a live "
        "e2e persona run surfaced (backlog TASK-82)")


@pytest.mark.parametrize("department_pref", ["Intensivstation", "ITS", "Intensivpflege", "intensiv", "Intensiv/IMC"])
def test_shortlist_reads_the_candidates_department_word_in_board_vocabulary(luna, department_pref):
    """TASK-96 review: the live close persona test wrote 'Intensivstation wäre ideal.', the model stored
    department_pref='Intensivstation', and the exact board filter ('Intensiv/IMC') left the shortlist empty
    with both documents in -- consent was then asked with no clinic ever named."""
    card = {"qualification_ok": True, "city": "München", "department_pref": department_pref,
            "housing_known": True, **_DOC}
    snap = LB.market_snapshot(card)
    assert [s["clinic"] for s in snap["shortlist"]] == ["Klinikum München Nord"]
    assert snap["matching_clinics_count"] == 1


def test_shortlist_department_word_still_filters_to_its_own_department(luna):
    card = {"qualification_ok": True, "department_pref": "Operationssaal", "housing_known": True, **_DOC}
    assert [s["clinic"] for s in LB.market_snapshot(card)["shortlist"]] == ["Klinikum Würzburg"]
    assert LB.market_snapshot({**card, "city": "München"})["shortlist"] == []


def test_shortlist_is_empty_with_neither_city_nor_department():
    card = {"qualification_ok": True, "housing_known": True, **_DOC}
    snap = LB.market_snapshot(card)
    assert snap["shortlist"] == []


def test_shortlist_is_empty_without_a_document_even_when_everything_else_is_satisfied():
    """TASK-91: qualification/city/housing alone are not enough -- documents must actually have
    arrived (TASK-96: the CV and the qualification document, card.documents) before the
    shortlist/close sequence exists, matching the real reference implementation's own
    document-verification gate (recon notes)."""
    card = {"qualification_ok": True, "qualification_path": "urkunde", "city": "München", "housing_known": True}
    snap = LB.market_snapshot(card)
    assert snap["shortlist"] == []


# --- session persistence: one Claude Code session per WhatsApp thread -------------------------

def test_a_brand_new_thread_has_no_session_id_and_one_comes_back_from_the_client(luna):
    assert luna["slots"].get("_session_id") is None
    d = LB.turn("Hallo", luna, client=fake_client(lambda s, u, sid: (_out(), "brand-new-session-id")))
    assert d["slots"]["_session_id"] == "brand-new-session-id"


def test_a_later_turn_passes_the_stored_session_id_back_to_the_client(luna):
    luna["slots"]["_session_id"] = "already-open-session"
    seen = []
    d = LB.turn("und jetzt Würzburg", luna,
               client=fake_client(lambda s, u, sid: seen.append(sid) or (_out(), sid)))
    assert seen == ["already-open-session"], "the existing session id must be handed to the client, not discarded"
    assert d["slots"]["_session_id"] == "already-open-session"


def test_the_stop_and_out_of_scope_gates_never_touch_the_session_id(luna):
    """These two gates return before the client is ever built -- the session id on the card,
    whatever it is, must survive untouched."""
    luna["slots"]["_session_id"] = "existing"
    d = LB.turn("STOP", luna)
    assert d["slots"]["_session_id"] == "existing"
    luna2 = {"slots": {"_session_id": "existing"}, "asked": []}
    d2 = LB.turn("ich bin in Hessen", luna2)
    assert d2["slots"]["_session_id"] == "existing"


# --- output validation: fail loudly, do not guess ---------------------------------------------

def test_missing_required_keys_raises(luna):
    bad = {"bubbles": ["hi"]}  # no action, no escalate_to_manager, no no_send, no card_patch
    with pytest.raises(RuntimeError, match="missing required keys"):
        LB.turn("Hallo", luna, client=fake_client(bad))


def test_card_patch_must_be_an_object(luna):
    bad = _out(card_patch="not an object")
    with pytest.raises(RuntimeError, match="card_patch must be an object"):
        LB.turn("Hallo", luna, client=fake_client(bad))


def test_too_many_bubbles_is_rejected(luna):
    out = _out(bubbles=["one", "two", "three"])
    with pytest.raises(AssertionError, match="style rule"):
        LB.turn("Hallo", luna, client=fake_client(out))


def test_an_empty_bubble_is_rejected(luna):
    out = _out(bubbles=[""])
    with pytest.raises(AssertionError, match="empty bubble"):
        LB.turn("Hallo", luna, client=fake_client(out))


# --- the fence-stripping and validation helpers, directly ---------------------------------------

def test_strip_fence_removes_a_markdown_json_fence():
    fenced = '```json\n{"a": 1}\n```'
    assert LB._strip_fence(fenced) == '{"a": 1}'
    assert LB._strip_fence('{"a": 1}') == '{"a": 1}'


def test_parse_reply_json_handles_plain_fenced_and_stray_prose():
    assert LB._parse_reply_json('{"a": 1}') == {"a": 1}
    assert LB._parse_reply_json('```json\n{"a": 1}\n```') == {"a": 1}
    # Observed against the live CLI even with every built-in tool disabled: a stray narration
    # ahead of the actual answer.
    prose = ('That was an erroneous file access on my part -- ignoring it. '
            'Here is my answer to the candidate:\n{"a": 1}')
    assert LB._parse_reply_json(prose) == {"a": 1}


def test_parse_reply_json_still_raises_on_genuine_garbage():
    with pytest.raises(json.JSONDecodeError):
        LB._parse_reply_json("no JSON anywhere in this text")


def test_validate_passes_through_a_well_formed_reply():
    out = _out()
    assert LB._validate(out) is out


# --- the CLI subprocess call itself: mocked at the subprocess boundary ------------------------

class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _ok_stdout(session_id="cli-assigned-session", **out_kw):
    return json.dumps({"is_error": False, "result": json.dumps(_out(**out_kw)), "session_id": session_id})


def test_live_reply_starts_a_fresh_session_with_session_id_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "sessions")
    captured = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None, cwd=None):
        captured["cmd"], captured["input"], captured["timeout"], captured["cwd"] = cmd, input, timeout, cwd
        return _FakeCompleted(stdout=_ok_stdout())

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = LB.Client()
    out, session_id = client._live_reply("SYSTEM TEXT", "USER TEXT", None)
    assert out["action"] == "reply_now_conversational"
    assert session_id == "cli-assigned-session"
    cmd = captured["cmd"]
    assert cmd[0] == C.LUNA_CLAUDE_BIN and "-p" in cmd and "--restricted" in cmd
    assert "--tools" in cmd and cmd[cmd.index("--tools") + 1] == "", (
        "every built-in tool must be off -- --restricted alone still leaves file-reading tools "
        "available, and a stray tool-use narration ahead of the JSON breaks the parse")
    assert "--system-prompt" in cmd and cmd[cmd.index("--system-prompt") + 1] == "SYSTEM TEXT"
    assert "--session-id" in cmd, "a brand-new thread must start a named session, not an anonymous one"
    assert "--resume" not in cmd
    started_id = cmd[cmd.index("--session-id") + 1]
    uuid.UUID(started_id)  # raises if this module did not generate a real UUID
    assert captured["input"] == "USER TEXT", "the user payload goes over stdin, not argv"
    assert captured["timeout"] == C.LUNA_TIMEOUT_SEC
    assert captured["cwd"] == C.LUNA_SESSION_DIR, "resume only finds this session again from the same cwd"


def test_live_reply_resumes_an_existing_session_with_resume_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "sessions")
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _FakeCompleted(stdout=_ok_stdout(session_id="existing-thread-session"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    out, session_id = LB.Client()._live_reply("s", "u", "existing-thread-session")
    cmd = captured["cmd"]
    assert "--resume" in cmd and cmd[cmd.index("--resume") + 1] == "existing-thread-session"
    assert "--session-id" not in cmd, "resuming must not also claim a fresh session id"
    assert session_id == "existing-thread-session"


def test_live_reply_strips_a_markdown_fence_around_the_result(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "sessions")
    fenced_result = "```json\n" + json.dumps(_out()) + "\n```"
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _FakeCompleted(stdout=json.dumps(
                            {"is_error": False, "result": fenced_result, "session_id": "s1"})))
    out, session_id = LB.Client()._live_reply("s", "u", None)
    assert out["action"] == "reply_now_conversational"


def test_live_reply_raises_when_the_cli_is_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "sessions")

    def raise_not_found(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", raise_not_found)
    with pytest.raises(RuntimeError, match="not on PATH"):
        LB.Client()._live_reply("s", "u", None)


def test_live_reply_raises_on_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "sessions")

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["claude"], timeout=1)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    with pytest.raises(RuntimeError, match="did not answer within"):
        LB.Client()._live_reply("s", "u", None)


def test_live_reply_raises_on_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted(returncode=1, stderr="boom"))
    with pytest.raises(RuntimeError, match="exited 1"):
        LB.Client()._live_reply("s", "u", None)


def test_live_reply_raises_when_stdout_is_not_json(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted(stdout="not json"))
    with pytest.raises(RuntimeError, match="did not return JSON"):
        LB.Client()._live_reply("s", "u", None)


def test_live_reply_raises_when_the_cli_itself_reports_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _FakeCompleted(stdout=json.dumps({"is_error": True, "result": "quota exceeded"})))
    with pytest.raises(RuntimeError, match="reported an error"):
        LB.Client()._live_reply("s", "u", None)


def test_live_reply_raises_when_result_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted(stdout=json.dumps({"is_error": False})))
    with pytest.raises(RuntimeError, match="no result text"):
        LB.Client()._live_reply("s", "u", None)


# --- wired into the webhook, end to end, brain selected by config -----------------------------

def test_webhook_uses_the_luna_brain_when_selected(luna, monkeypatch):
    monkeypatch.setattr(C, "BRAIN", "luna")
    calls = []

    class FakeMeta:
        def __init__(self):
            self.sent = []

        def send_text(self, to_e164, body):
            self.sent.append(body)
            return f"wamid.out.{len(self.sent)}"

        def send_buttons(self, to_e164, body, buttons):
            return self.send_text(to_e164, body)

    def fake_reply(system, user, session_id):
        calls.append(json.loads(user))
        return _out(bubbles=["Hallo, hier antwortet die Luna-Brain 🙂"],
                    card_patch={"region": "Bayern"}), session_id or "webhook-test-session"

    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "ACCESS_TOKEN", "t")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", "p")
    # app/wa/luna_brain.py:turn() constructs its own Client() when none is passed in (api.py
    # does not pass one) -- capture the real class before patching, so the replacement below
    # does not call itself.
    RealClient = LB.Client
    monkeypatch.setattr(LB, "Client", lambda: RealClient(reply=fake_reply))

    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "p"},
        "messages": [{"id": "wamid.1", "from": "491701234567", "type": "text",
                     "text": {"body": "Hallo"}}]}}]}]}
    wa = FakeMeta()
    out = WAPI.handle_payload(payload, client=wa)
    assert out["results"][0]["status"] == "sent"
    assert wa.sent == ["Hallo, hier antwortet die Luna-Brain 🙂"]
    assert calls, "the luna brain must have been the one consulted, not the deterministic ladder"
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        assert t["slots"]["region"] == "Bayern"
        assert t["slots"]["_session_id"] == "webhook-test-session"


def test_webhook_default_config_still_uses_the_deterministic_brain(luna):
    assert C.BRAIN == "deterministic", "the default must not have flipped for every other test"
