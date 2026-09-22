"""TASK-150: the original ad behind what a TEST thread was told -- and never anywhere else.

Ivan, 2026-09-21, for the acceptance phase: his business partner must be able to check that the
vacancies this bot names are real. So on a thread ``wa_threads.is_test`` marks, a message that names
a posting carries a footnote offering the original ad, and taking that offer up returns the ad's own
URL on the clinic's site. A production thread never sees either -- the standing rule that a
candidate gets no board link stays structural, not a wording the model could forget.

Both halves are assembled in CODE (app/wa/luna/source_link.py): the model never writes a link and
never sees one in its payload (app/wa/luna/offer.py strips source_url/external_url on purpose).

Offline: fixture board, fake model callable, no subprocess and no network.
"""
import json
import time

import pytest

from app import data as D
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa.luna import source_link as SRC
from app.wa.luna import tools_server as TS

from .test_wa_luna_dialog_rules import _board, _job, _out, fake_client


@pytest.fixture()
def board(tmp_path, monkeypatch):
    """Five postings, each with the ad URL the crawler read it from."""
    _board([_job(i) for i in range(1, 6)], monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    return tmp_path


def _names_a_posting(system, user, session_id):
    """A model turn that looks a city up and then names what came back, as a live one does."""
    TS.search_postings(city="Augsburg")
    return _out(bubbles=["In Augsburg sucht das Klinikum Augsburg 1 gerade."]), session_id


def test_a_test_thread_is_offered_the_original_ad_of_the_posting_it_was_told_about(board):
    d = LB.turn("und in Augsburg?", {"phone": "+4915550001234", "is_test": True, "slots": {}, "asked": []},
                client=fake_client(_names_a_posting))
    assert d["bubbles"][-1].endswith(SRC.FOOTNOTE_DE)
    assert d["slots"][LB.TEST_SOURCES_KEY] == [
        {"clinic": "Klinikum Augsburg 1", "posting_id": 1, "url": "https://example.org/job/1"}]


def test_the_footnote_itself_carries_no_link(board):
    """The offer is words; the URL only ever goes out when the candidate asks for it."""
    assert "http" not in SRC.FOOTNOTE_DE and "www." not in SRC.FOOTNOTE_DE


def test_a_production_thread_gets_no_footnote_and_no_link(board):
    d = LB.turn("und in Augsburg?", {"phone": "+4915550001234", "slots": {}, "asked": []},
                client=fake_client(_names_a_posting))
    assert d["bubbles"] == ["In Augsburg sucht das Klinikum Augsburg 1 gerade."]
    assert LB.TEST_SOURCES_KEY not in d["slots"]


def test_asking_for_the_original_gets_the_url_appended_to_the_answer_not_instead_of_it(board):
    """Assembled in code -- the link line is built from board rows, never written by the model.

    AND IT NEVER TAKES THE TURN (TASK-151). This used to return before the model ran, so the
    candidate's actual question was replaced by a list of links. The model writes its answer and the
    links go out next to it."""
    first = LB.turn("und in Augsburg?", {"phone": "+4915550001234", "is_test": True, "slots": {},
                                         "asked": []}, client=fake_client(_names_a_posting))
    d = LB.turn("Können Sie mir die Original-Anzeige schicken?",
                {"phone": "+4915550001234", "is_test": True, "slots": first["slots"], "asked": []},
                client=fake_client(_out(bubbles=["Klar, einen Moment."])))
    assert d["bubbles"][0] == "Klar, einen Moment.", "the model's own answer is not swallowed"
    assert d["bubbles"][-1].endswith("Klinikum Augsburg 1: https://example.org/job/1")


def test_a_question_about_a_document_is_answered_by_the_model_not_with_job_links(board):
    """TASK-151: "Original" is the word this funnel uses for the Urkunde. The old detector read any
    sentence carrying it plus a question mark as a request for the ad, so the single gate the funnel
    exists to close was answered with a list of vacancies."""
    first = LB.turn("und in Augsburg?", {"phone": "+4915550001234", "is_test": True, "slots": {},
                                         "asked": []}, client=fake_client(_names_a_posting))
    for question in ["Brauchen Sie das Original meiner Urkunde?",
                     "Muss ich die Originaldokumente per Post schicken?",
                     "Soll ich Ihnen das Original oder eine Kopie schicken?",
                     "Können Sie mir die Stellen anzeigen?",
                     "Ich wohne links vom Bahnhof."]:
        assert SRC.asks_for_source(question) is False, question
        d = LB.turn(question, {"phone": "+4915550001234", "is_test": True,
                               "slots": dict(first["slots"]), "asked": []},
                    client=fake_client(_out(bubbles=["Das Original brauche ich nicht, ein Foto reicht."])))
        assert d["bubbles"] == ["Das Original brauche ich nicht, ein Foto reicht."], question


def test_a_stored_link_the_board_no_longer_confirms_is_said_instead_of_sent(board, monkeypatch):
    """TASK-151: the stored links were a frozen per-thread allowlist -- audit E's shape, for URLs.
    The point of the feature is proving a vacancy is real; a dead ad breaks exactly that."""
    first = LB.turn("und in Augsburg?", {"phone": "+4915550001234", "is_test": True, "slots": {},
                                         "asked": []}, client=fake_client(_names_a_posting))
    _board([j for j in D.jobs() if j["posting_id"] != 1], monkeypatch)
    d = LB.turn("Link", {"phone": "+4915550001234", "is_test": True, "slots": first["slots"],
                         "asked": []}, client=fake_client(_out(bubbles=["Klar."])))
    assert "https://example.org/job/1" not in " ".join(d["bubbles"])
    assert f"Klinikum Augsburg 1: {SRC.GONE_DE}" in d["bubbles"][-1]


def test_a_production_thread_asking_the_same_question_gets_no_link(board):
    """Same words, real candidate: the interception needs the test flag AND remembered sources, and
    a production card has neither."""
    called = []

    def reply(system, user, session_id):
        called.append(json.loads(user))
        return _out(bubbles=["Die Stellenanzeige selbst kann ich Ihnen hier nicht schicken."]), session_id

    d = LB.turn("Können Sie mir die Original-Anzeige schicken?",
                {"phone": "+4915550001234", "slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))
    assert d["action"] != "test_source_links" and "http" not in " ".join(d["bubbles"])
    assert "http" not in json.dumps(called[0]), "no link reaches the model's payload either"


def test_a_test_thread_merely_mentioning_the_ad_keeps_its_normal_turn(board):
    """The interception takes the whole turn, so it must fire on a request and not on any sentence
    that happens to contain the word."""
    first = LB.turn("und in Augsburg?", {"phone": "+4915550001234", "is_test": True, "slots": {},
                                         "asked": []}, client=fake_client(_names_a_posting))
    d = LB.turn("Die Anzeige klingt gut.",
                {"phone": "+4915550001234", "is_test": True, "slots": first["slots"], "asked": []},
                client=fake_client(_out(bubbles=["Das freut mich! Sollen wir weitermachen?"])))
    assert d["bubbles"] == ["Das freut mich! Sollen wir weitermachen?"]


@pytest.mark.parametrize("text, wanted", [
    ("Link", True),                                        # what the footnote itself asks for
    ("Link bitte", True),
    ("Können Sie mir die Original-Anzeige schicken?", True),
    ("Haben Sie einen Link zu der Ausschreibung?", True),
    ("Zeigen Sie mir die Anzeige", True),
    ("Die Anzeige klingt gut.", False),
    ("Ja, Intensivstation wäre gut.", False),
])
def test_what_counts_as_asking_for_the_original(text, wanted):
    assert SRC.asks_for_source(text) is wanted


def test_a_message_that_names_no_posting_gets_no_footnote(board):
    """An aggregate ("so many positions are open") names no posting, so there is no original to
    offer -- a footnote there would be a promise this code could not keep."""
    d = LB.turn("wie viele Stellen haben Sie?",
                {"phone": "+4915550001234", "is_test": True, "slots": {}, "asked": []},
                client=fake_client(_out(bubbles=["Aktuell sind 5 Stellen offen."])))
    assert d["bubbles"] == ["Aktuell sind 5 Stellen offen."]
    assert LB.TEST_SOURCES_KEY not in d["slots"]


def test_a_posting_whose_board_row_has_no_url_is_said_plainly(tmp_path, monkeypatch):
    """TASK-150 AC#5, made true in TASK-151: a posting with no recorded original used to be dropped
    from the list silently, so the answer had fewer lines than the message had houses and nobody
    could tell which one had no ad. It is named and the reason is given (CLAUDE.md)."""
    rows = [{**_job(i), "external_url": None} for i in range(1, 6)]
    _board(rows, monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    first = LB.turn("und in Augsburg?", {"phone": "+4915550001234", "is_test": True, "slots": {},
                                         "asked": []}, client=fake_client(_names_a_posting))
    assert first["slots"][LB.TEST_SOURCES_KEY] == [
        {"clinic": "Klinikum Augsburg 1", "posting_id": 1, "url": ""}]
    d = LB.turn("Link", {"phone": "+4915550001234", "is_test": True, "slots": first["slots"],
                         "asked": []}, client=fake_client(_out(bubbles=["Klar."])))
    assert f"Klinikum Augsburg 1: {SRC.NO_ORIGINAL_DE}" in d["bubbles"][-1]
    assert "http" not in " ".join(d["bubbles"])


def test_the_model_may_not_write_the_remembered_sources_itself(board):
    d = LB.turn("Hallo", {"phone": "+4915550001234", "is_test": True, "slots": {}, "asked": []},
                client=fake_client(_out(card_patch={
                    LB.TEST_SOURCES_KEY: [{"clinic": "Klinikum Erfunden", "posting_id": 1,
                                           "url": "https://example.invalid/anything"}]})))
    assert LB.TEST_SOURCES_KEY not in d["slots"]
