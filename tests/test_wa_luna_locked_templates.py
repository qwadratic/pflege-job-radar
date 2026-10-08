"""The A/B switch WA_LUNA_LOCKED_TEMPLATES (Ivan, 2026-10-08), the city-check prompt rule, the region
shortcut standing down when a Bavarian town is named too, and the run records carrying the arm.
Fakes only: the model is a ``reply`` callable on ``luna_brain.Client``; no network, no ``claude``."""
import json
import time

import pytest

from app import data as D
from app.wa import config as C
from app.wa import luna_brain as LB
from tests.test_wa_luna_brain import _jobs, _out, _rule, fake_client
from tests.test_wa_transport import _import_config


@pytest.fixture()
def luna(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": _jobs(), "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    return {"slots": {}, "asked": []}


# --- the switch itself ---------------------------------------------------------------------------

def test_the_switch_defaults_to_all():
    assert C.LUNA_LOCKED_TEMPLATES == "all"


@pytest.mark.parametrize("value", ["all", "exceptions", " Exceptions "])
def test_the_switch_parses_both_arms(value):
    proc = _import_config(WA_LUNA_LOCKED_TEMPLATES=value)
    assert proc.returncode == 0, proc.stderr


def test_any_other_value_stops_the_process_at_import():
    proc = _import_config(WA_LUNA_LOCKED_TEMPLATES="some")
    assert proc.returncode != 0
    assert "WA_LUNA_LOCKED_TEMPLATES='some' is not 'all' or 'exceptions'" in proc.stderr


def test_system_prompt_rejects_an_unknown_arm():
    with pytest.raises(ValueError, match="not 'all' or 'exceptions'"):
        LB.P.system_prompt("{}", "{}", locked_templates="some")


# --- the prompt ----------------------------------------------------------------------------------

def test_the_city_check_rule_sends_every_named_city_to_the_board_tool_first():
    rule = _rule("CITY CHECK")
    assert "any city, several in one message, a non-Bavarian one too" in rule
    assert "BEFORE you say anything about vacancies" in rule
    assert "count_postings" in rule and "search_postings" in rule and "list_cities_with_postings" in rule
    assert "never name cities from memory or from market_snapshot alone" in rule
    for tool in ("count_postings", "search_postings", "list_cities_with_postings"):
        assert tool in LB.TS._BASE_DESCRIPTIONS, f"the rule names {tool}, which must be a registered tool"


def test_no_prompt_line_forbids_the_city_lookup_any_more():
    off_region = _rule("OFF REGION")
    assert "no tool lookup for the other region" not in off_region
    assert "a named CITY is still checked, CITY CHECK" in off_region
    assert "a city: CITY CHECK" in _rule("TOOLS (mandatory")


def test_the_exceptions_paragraph_is_only_in_the_exceptions_prompt():
    base = LB.P.system_prompt("{}", "{}")
    assert LB.P.system_prompt("{}", "{}", locked_templates="all") == base
    arm = LB.P.system_prompt("{}", "{}", locked_templates="exceptions")
    assert arm == base + "\n\n" + LB.P.EXCEPTIONS_ARM
    assert "NO LOCKED REFUSAL OR REGION TEXT" in arm and "NO LOCKED REFUSAL OR REGION TEXT" not in base
    assert "no hand-off promise, no apology" in LB.P.EXCEPTIONS_ARM


def test_the_turn_sends_the_arm_s_prompt_to_the_model(luna, monkeypatch):
    seen = []

    def reply(system, user, session_id):
        seen.append(system)
        return _out(), session_id

    LB.turn("Hallo", luna, client=fake_client(reply))
    monkeypatch.setattr(C, "LUNA_LOCKED_TEMPLATES", "exceptions")
    LB.turn("Hallo", {"slots": {}, "asked": []}, client=fake_client(reply))
    assert "NO LOCKED REFUSAL OR REGION TEXT" not in seen[0]
    assert "NO LOCKED REFUSAL OR REGION TEXT" in seen[1]


# --- region shortcut: `all` ------------------------------------------------------------------------

def test_berlin_und_muenchen_does_not_trigger_the_locked_region_text(luna):
    """Eval finding 2026-10-08: München is in Bavaria, so the mixed message is the REGION rule's case and
    the model answers. The town is recognised by the board's own town list, not a phrase list."""
    assert LB.named_non_bavaria_land("Ich möchte in Berlin und München arbeiten") is None
    calls = []
    d = LB.turn("Berlin und München", luna,
                client=fake_client(lambda s, u, sid: calls.append(1) or (_out(bubbles=["Antwort"]), sid)))
    assert calls == [1] and d["action"] != "out_of_scope_region" and "region" not in d["slots"]


def test_a_plain_land_still_triggers_it_and_berlin_counts_as_a_land(luna):
    assert LB.named_non_bavaria_land("Hessen") == "hessen"
    assert LB.named_non_bavaria_land("ich suche in Berlin") == "berlin"
    assert LB.named_non_bavaria_land("Berlin oder Hamburg") == "berlin"
    d = LB.turn("Hessen", luna, client=fake_client(lambda s, u, sid: pytest.fail("model must not run")))
    assert d["bubbles"] == [LB.P.OUT_OF_SCOPE_REGION_DE] and d["slots"]["region"] == "hessen"


# --- region shortcut: `exceptions` -----------------------------------------------------------------

def test_in_exceptions_mode_the_model_answers_a_named_land_and_no_region_is_written(luna, monkeypatch):
    monkeypatch.setattr(C, "LUNA_LOCKED_TEMPLATES", "exceptions")
    own = "Eigene Antwort des Modells."
    d = LB.turn("Hessen", luna, client=fake_client(_out(bubbles=[own], card_patch={})))
    assert d["bubbles"] == [own] and d["action"] != "out_of_scope_region"
    assert "region" not in d["slots"], "the harness writes no card.region for a Land in this arm"


# --- reject substitution -----------------------------------------------------------------------------

def _reject_out(own):
    return _out(bubbles=[own], card_patch={"qualification_ok": False, "qualification_path": "reject"})


def test_in_all_mode_a_not_placeable_verdict_still_sends_the_locked_refusal(luna):
    d = LB.turn("ich bin Pflegehelferin", luna, client=fake_client(_reject_out("Eigener Text.")))
    assert d["bubbles"] == [LB.P.REJECT_BODY_DE] and d["action"] == "explain_not_placeable"


def test_in_exceptions_mode_the_model_s_own_refusal_goes_out(luna, monkeypatch):
    monkeypatch.setattr(C, "LUNA_LOCKED_TEMPLATES", "exceptions")
    own = "Leider können wir hier nichts anbieten. Wenn sich etwas ändert, schreiben Sie gern."
    d = LB.turn("ich bin Pflegehelferin", luna, client=fake_client(_reject_out(own)))
    assert d["bubbles"] == [own] and LB.P.REJECT_BODY_DE not in d["bubbles"]
    assert d["slots"]["qualification_ok"] is False, "the verdict itself is still recorded on the card"


def test_in_exceptions_mode_a_reopened_not_placeable_thread_gets_the_model_s_own_words(luna, monkeypatch):
    monkeypatch.setattr(C, "LUNA_LOCKED_TEMPLATES", "exceptions")
    luna["slots"]["qualification_ok"] = False
    own = "Wie besprochen, aktuell passt es leider nicht."
    d = LB.turn("Hallo nochmal", luna, client=fake_client(_out(bubbles=[own], action="explain_not_placeable")))
    assert d["bubbles"] == [own]


# --- what stays locked in the hypothesis arm ---------------------------------------------------------

def test_in_exceptions_mode_stop_and_decline_stay_locked(luna, monkeypatch):
    monkeypatch.setattr(C, "LUNA_LOCKED_TEMPLATES", "exceptions")
    stop = LB.turn("STOP", luna, client=fake_client(lambda s, u, sid: pytest.fail("model must not run")))
    assert stop["stopped"] is True and stop["bubbles"] == []
    from app.wa.luna import refusal as RF
    monkeypatch.setattr(RF, "is_unambiguous_refusal", lambda text, our_last_message=None: RF.Verdict(True, "test"))
    d = LB.turn("kein Interesse", {"slots": {}, "asked": []},
                client=fake_client(_out(bubbles=["x"], decline=True, decline_reason="no interest")))
    assert d["bubbles"] == [LB.P.DECLINE_ACK_DE] and d["action"] == "decline_ack"
