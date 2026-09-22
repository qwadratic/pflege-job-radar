"""TASK-144: the three dialog rules Ivan made explicit on 2026-09-21, tested where they are
enforced -- in code, not in the prompt.

1. VOLUME -- at most five positions in one message, an accurate remainder count, and the
   narrow-or-pool choice offered in the same turn (app/wa/luna/offer.py).
2. NO INVENTION -- a reply may name only a clinic this thread's tools really returned
   (app/wa/luna/grounding.py).
3. FUNNEL CONTINUITY -- the card carries the stage and the next turn resumes from it
   (app/wa/luna_brain.py:funnel_stage).

Offline: the model is the same fake ``reply`` callable tests/test_wa_luna_brain.py injects, and the
board is a fixture snapshot. No subprocess, no network.
"""
import json
import time

import pytest

from app import data as D
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa.luna import grounding as GR
from app.wa.luna import offer as OF
from app.wa.luna import prompts as P
from app.wa.luna import tools_server as TS

_CV = {"id": 1, "document_type": "lebenslauf", "certificate_level": "unknown"}
_URKUNDE = {"id": 2, "document_type": "urkunde", "certificate_level": "fachkraft"}
# A card with every gate but consent satisfied -- what market_snapshot needs before it builds an offer.
READY = {"region": "Bayern", "qualification_path": "urkunde", "qualification_ok": True,
         "housing_needed": False, "city": "München", "documents": [_CV, _URKUNDE]}

_CITIES = ["München", "Augsburg", "Würzburg", "Coburg", "Regensburg"]
_BEZIRKE = {"München": "Oberbayern", "Augsburg": "Schwaben", "Würzburg": "Unterfranken",
            "Coburg": "Oberfranken", "Regensburg": "Oberpfalz",
            # Not in _CITIES -- _job()'s own default city cycling stays untouched -- only used where a
            # test names Straubing explicitly (round-4 audit, 2026-09-22).
            "Straubing": "Niederbayern"}
_DEPARTMENTS = ["Intensiv/IMC", "OP", "Innere Medizin", "Notaufnahme"]


def _job(i, city=None, department=None, housing=None, clinic=None):
    city = city or _CITIES[i % len(_CITIES)]
    department = department or _DEPARTMENTS[i % len(_DEPARTMENTS)]
    return {"posting_id": i, "title": f"Pflegefachkraft {department}", "role_class": "pflegefachkraft",
            "department_hint": department, "city": city, "clinic_town": city,
            "regierungsbezirk": _BEZIRKE[city], "clinic_id": f"c{i}",
            "clinic_name": clinic or f"Klinikum {city} {i}", "employer": clinic or f"Klinikum {city} {i}",
            "employment_types": ["vollzeit" if i % 2 else "teilzeit"],
            "enr_housing": bool(i % 3) if housing is None else housing,
            "verify_status": "live", "status": "open", "first_published": "2026-09-01", "fresh": True,
            # external_url and nothing else, which is the shape a live snapshot row really has: the
            # board publishes no separate source_url on any posting (TASK-151, measured 2026-09-21),
            # and a fixture that invented one made app/wa/luna/source_link.py's documented
            # preference look like it fired.
            "external_url": f"https://example.org/job/{i}"}


def _board(jobs, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": jobs, "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)


@pytest.fixture()
def hundred(tmp_path, monkeypatch):
    """100 open postings in 100 distinct clinics, all in one city so the card's city filter keeps them."""
    _board([_job(i, city="München") for i in range(1, 101)], monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    return tmp_path


@pytest.fixture()
def small(tmp_path, monkeypatch):
    """Five postings in five clinics across five cities -- the board the turn() tests run against."""
    _board([_job(i) for i in range(1, 6)], monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    return tmp_path


def _out(**kw):
    base = {"action": "reply_now_conversational", "bubbles": ["Hallo 🙂"], "rationale": "",
            "escalate_to_manager": False, "escalate_reason": None, "no_send": False,
            "next_ask": None, "card_patch": {}}
    base.update(kw)
    return base


def fake_client(out_or_fn):
    fn = out_or_fn if callable(out_or_fn) else (lambda system, user, session_id: (out_or_fn, session_id))
    return LB.Client(reply=fn)


# --- 1. VOLUME ---------------------------------------------------------------------------------

def test_a_hundred_matching_clinics_produce_five_positions_and_an_accurate_remainder(hundred):
    """Ivan 2026-09-21: never dump many vacancies. Five named, the rest counted, both branches offered."""
    snap = LB.market_snapshot(READY)
    offer = snap["offer"]
    assert offer["shown"] == 5 == OF.OFFER_LIMIT
    assert len(offer["positions"]) == 5
    assert offer["clinics_total"] == 100 == snap["matching_clinics_count"]
    assert offer["remaining_clinics"] == 95, "how many more matched has to be the real remainder"
    assert offer["postings_total"] == 100 and offer["remaining_postings"] == 95
    assert [b["id"] for b in offer["branches"]] == [OF.BRANCH_NARROW, OF.BRANCH_POOL], (
        "both branches -- narrow the search, or go into the general pool -- in the same payload")
    assert snap["shortlist"] == offer["positions"], "one assembly, so the list and the count agree"


def test_the_cap_holds_however_large_the_result_set_is():
    """The cap sits where the rows are assembled, so no prompt wording can raise it."""
    for size in (6, 100, 1000):
        offer = OF.build_offer([_job(i, city="München") for i in range(1, size + 1)])
        assert offer["shown"] == OF.OFFER_LIMIT
        assert offer["remaining_clinics"] == size - OF.OFFER_LIMIT


def test_the_suggested_narrowing_criteria_come_from_the_actual_result_set(tmp_path, monkeypatch):
    """"SUGGEST the concrete criteria that would narrow it for this candidate, taken from the actual
    result set" -- every value is one the rows carry, and a dimension the whole set agrees on is left
    out because it narrows nothing."""
    rows = [_job(i) for i in range(1, 41)]        # 5 cities, 4 departments, both employment types
    offer = OF.build_offer(rows)
    by_criterion = {c["criterion"]: c for c in offer["narrow_by"]}
    assert "city" in by_criterion and "department" in by_criterion
    assert {v["value"] for v in by_criterion["city"]["values"]} <= set(_CITIES)
    assert {v["value"] for v in by_criterion["department"]["values"]} <= set(_DEPARTMENTS)
    assert sum(v["postings"] for v in by_criterion["department"]["values"]) == 40
    one_city = OF.build_offer([_job(i, city="Augsburg") for i in range(1, 41)])
    assert "city" not in {c["criterion"] for c in one_city["narrow_by"]}, (
        "a dimension every row shares discriminates nothing and must not be suggested")


def test_every_criterion_that_would_narrow_the_set_is_offered_whole():
    """Ivan's five is a cap on POSITIONS IN ONE MESSAGE. Applying it to how many values a narrowing
    criterion may list was a second ceiling nobody asked for (CLAUDE.md), and it left the model able
    to suggest 5 of the 20 departments that would actually narrow this set -- picked by a sort
    order, not by the candidate's need. narrow_by is machine-readable context, not message text; the
    VOLUME rule still governs what goes out (TASK-146)."""
    rows = [_job(i, city="München", department=f"Abteilung {i}") for i in range(1, 21)]
    by_criterion = {c["criterion"]: c for c in OF.build_offer(rows)["narrow_by"]}
    assert len(by_criterion["department"]["values"]) == 20
    assert "more_values" not in by_criterion["department"]


def test_no_position_carries_a_board_url_or_job_link(hundred):
    """Ivan's standing rule: never send a board URL or job link. The link does not reach the payload
    the model writes from, so it cannot repeat one."""
    payload = json.dumps(LB.market_snapshot(READY))
    assert "http" not in payload and "source_url" not in payload and "external_url" not in payload


def test_there_is_no_offer_before_every_gate_is_settled(small):
    """TASK-91 stands: the payload carries no per-city preview, so the offer only exists at the close."""
    assert LB.market_snapshot({"city": "München"})["offer"] is None
    assert LB.market_snapshot(READY)["offer"] is not None


def test_a_reply_naming_more_positions_than_the_cap_is_rejected_loudly(hundred):
    named = [p["clinic"] for p in LB.market_snapshot(READY)["offer"]["positions"]]
    allowed = set(named) | {"Klinikum München 6"}
    with pytest.raises(AssertionError, match="VOLUME"):
        GR.check_reply([", ".join(named + ["Klinikum München 6"])], allowed)
    assert len(GR.check_reply([", ".join(named)], allowed)) == OF.OFFER_LIMIT, "exactly five is allowed"


def test_choosing_the_pool_branch_is_recorded_on_the_card(small):
    thread = {"slots": dict(READY), "asked": []}
    out = _out(bubbles=["Alles klar, dann schlage ich Sie allen passenden Kliniken vor."],
               card_patch={"match_branch": OF.BRANCH_POOL})
    d = LB.turn("dann bitte allen", thread, client=fake_client(out))
    assert d["slots"]["match_branch"] == OF.BRANCH_POOL
    assert d["slots"]["match_branch_at"], "the branch choice is timestamped by the harness"


def test_choosing_the_narrow_branch_is_recorded_too(small):
    thread = {"slots": dict(READY), "asked": []}
    out = _out(bubbles=["Gern, in welcher Abteilung möchten Sie arbeiten?"],
               card_patch={"match_branch": OF.BRANCH_NARROW})
    d = LB.turn("lieber eingrenzen", thread, client=fake_client(out))
    assert d["slots"]["match_branch"] == OF.BRANCH_NARROW


# --- 2. NO INVENTION ---------------------------------------------------------------------------

def test_a_reply_naming_a_clinic_no_tool_returned_is_rejected_loudly(small):
    """The prompt has banned inventing a clinic since TASK-91; this is the check behind it."""
    with pytest.raises(AssertionError, match="NO INVENTION"):
        GR.check_reply(["Im Klinikum München 63 ist gerade eine Stelle frei."], set())


def test_an_invented_name_never_reaches_the_candidate_and_never_becomes_silence(small):
    """TASK-146: the check used to raise straight out of turn(), so nothing was sent at all and
    catch-up re-drove the same turn into the same wording. The model gets one corrective attempt --
    this fake keeps writing the same invented house -- and then the candidate gets the harness's own
    holding message while the thread is flagged for a colleague. An answer plus a human, never the
    invented name and never silence."""
    thread = {"slots": {"region": "Bayern"}, "asked": []}
    out = _out(bubbles=["Im Klinikum München 63 ist gerade eine Stelle frei."])
    d = LB.turn("gibt es was in München?", thread, client=fake_client(out))
    assert d["bubbles"] == [P.BLOCKED_REPLY_DE] and d["action"] == "reply_blocked_escalated"
    assert "Klinikum München 63" not in " ".join(d["bubbles"])
    assert d["slots"]["_escalated"] is True and "NO INVENTION" in d["slots"]["_escalate_reason"]


def test_the_model_is_told_which_rule_it_broke_and_its_corrected_reply_goes_out(small):
    """The corrective retry runs in the SAME session, so the model still has its own tool results in
    context, and it is told the violation in the harness's own words."""
    seen, attempts = {}, []

    def reply(system, user, session_id):
        attempts.append(user)
        if len(attempts) == 1:
            return _out(bubbles=["Im Klinikum München 63 ist gerade eine Stelle frei."]), session_id
        seen.update(json.loads(user))
        return _out(bubbles=["Dazu habe ich gerade keine passende Stelle."]), session_id

    d = LB.turn("gibt es was in München?", {"slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))
    assert d["bubbles"] == ["Dazu habe ich gerade keine passende Stelle."]
    assert d["action"] == "reply_after_correction" and "_escalated" not in d["slots"]
    assert "NO INVENTION" in seen["harness_rejected_your_reply"]
    assert "Klinikum München 63" in seen["harness_rejected_your_reply"], "name the offending text"


def test_a_second_violation_hands_the_thread_to_a_human_with_an_answer_already_sent(small):
    """Two broken replies in a row: the candidate still hears something, and the escalation reason
    says which rule died so the colleague knows what they are picking up."""
    def reply(system, user, session_id):
        return _out(bubbles=["Hier die Übersicht: https://pflege-job-radar.de/jobs"]), session_id

    d = LB.turn("schicken Sie mir mal was", {"slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))
    assert d["bubbles"] == [P.BLOCKED_REPLY_DE]
    assert "LINK" in d["slots"]["_escalate_reason"] and d["slots"]["_escalated"] is True


def test_a_reply_with_too_many_bubbles_is_a_corrective_retry_not_an_exception(small):
    """TASK-156 (F2): the bubble-count style check (MAX_BUBBLES) used to run OUTSIDE the try/except
    that protects a turn -- three bubbles raised AssertionError straight out of turn() and the
    candidate got nothing at all (F2, verification 2026-09-22, hit 1 of 7 live turns). It now shares
    the same corrective-retry-then-holding-message contract as every other checked rule here."""
    attempts = []

    def reply(system, user, session_id):
        attempts.append(user)
        if len(attempts) == 1:
            return _out(bubbles=["Eins.", "Zwei.", "Drei."]), session_id
        return _out(bubbles=["Nur noch eins."]), session_id

    d = LB.turn("wie geht es weiter?", {"slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))
    assert len(attempts) == 2, "no exception -- the model got a corrective retry"
    assert d["bubbles"] == ["Nur noch eins."]
    assert d["action"] == "reply_after_correction" and "_escalated" not in d["slots"]


def test_a_persistent_bubble_count_violation_still_ends_in_an_answer_not_silence(small):
    """Two too-many-bubble replies in a row: the candidate still gets the holding message and the
    thread is flagged, exactly like any other rule broken twice in a row -- never a raw exception."""
    def reply(system, user, session_id):
        return _out(bubbles=["Eins.", "Zwei.", "Drei."]), session_id

    d = LB.turn("wie geht es weiter?", {"slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))
    assert d["bubbles"] == [P.BLOCKED_REPLY_DE]
    assert d["slots"]["_escalated"] is True
    assert "bubbles" in d["slots"]["_escalate_reason"]


@pytest.mark.parametrize("bubble", [
    "Die Schön Klinik München sucht gerade Pflegefachkräfte.",
    "Das Rotkreuzklinikum Augsburg hat eine Stelle frei.",
    "Die Sana Kliniken Würzburg suchen Intensivpflege.",
    "Das St. Anna Stift Coburg sucht.",
])
def test_an_invented_house_is_caught_even_without_a_head_word_from_a_list(small, bubble):
    """TASK-146: the detector used to fire only on a fixed list of head words at the FRONT of the
    name, so every one of these -- the ordinary shape of a fabricated clinic -- passed NO INVENTION
    untouched, and because the volume cap counts detected mentions, VOLUME did not count them
    either. A reply could have named ten invented houses and cleared both rules."""
    with pytest.raises(AssertionError, match="NO INVENTION"):
        GR.check_reply([bubble], set())


def test_the_residual_the_shape_detector_cannot_reach_is_stated_not_hidden(small):
    """An invented house whose own words are nowhere in the board's vocabulary AND whose town sits
    outside the name is not corroborated, so detector 2 does not fire on it. Corroborating from the
    whole sentence instead of from the name would catch it -- and would bring back the false
    positive that silences a truthful turn ("Im Klinikum Nachtdienst zu arbeiten ist in München
    möglich"), which is the worse of the two. Recorded here so the limit is a decision and not a
    surprise (TASK-146)."""
    assert GR.check_reply(["Das St. Anna Stift in Coburg sucht."], set()) == []


@pytest.mark.parametrize("bubble", [
    "Im Klinikum Nachtdienst zu arbeiten ist möglich — wollen Sie das?",
    "Wir suchen im Klinikum Pflegefachkräfte für die Intensivstation.",
    "Im Krankenhaus Dienst zu tun ist anspruchsvoll.",
    "Das Krankenhaus Ihrer Wahl entscheidet darüber selbst.",
    "Im Krankenhaus Vollzeit zu arbeiten ist der Normalfall.",
])
def test_ordinary_german_after_a_head_word_is_not_a_clinic_name(small, bubble):
    """TASK-146: an ordinary German compound after "Klinikum"/"Krankenhaus" was read as an invented
    clinic, check_reply raised, the whole turn failed and the candidate got silence -- while
    catch-up re-drove it into the same phrasing. The board's own vocabulary is what tells a name
    from a noun now: 'München' is in it, 'Nachtdienst' is not."""
    assert GR.check_reply([bubble], set()) == []


def test_a_real_board_clinic_no_tool_returned_this_turn_is_rejected_too(small):
    """Not only made-up names: a clinic that exists but that nothing looked up this turn is exactly
    the "named from memory of the board" failure the rule is about."""
    with pytest.raises(AssertionError, match="NO INVENTION"):
        GR.check_reply(["Das Klinikum Augsburg 1 sucht Pflegefachkräfte."], set())


def test_a_clinic_the_tools_returned_this_turn_may_be_named(small):
    """The fake model calls a tool the way the real one does -- tools_server logs it -- and then names
    what that call returned."""
    thread = {"slots": {"region": "Bayern"}, "asked": []}

    def reply(system, user, session_id):
        TS.search_postings(city="Augsburg")          # logs the call, as it does in a live turn
        return _out(bubbles=["In Augsburg sucht das Klinikum Augsburg 1 gerade."]), session_id

    d = LB.turn("und in Augsburg?", thread, client=fake_client(reply))
    assert d["bubbles"] == ["In Augsburg sucht das Klinikum Augsburg 1 gerade."]
    assert d["slots"][LB.GROUNDED_KEY] == ["Klinikum Augsburg 1"]


def test_a_clinic_grounded_earlier_on_the_thread_stays_sayable(small):
    """A follow-up question about a house named two turns ago must not fail: the thread's own evidence
    is remembered, so only a name that was NEVER looked up is an invention."""
    thread = {"slots": {"region": "Bayern", LB.GROUNDED_KEY: ["Klinikum Augsburg 1"]}, "asked": []}
    d = LB.turn("und die Wohnung dort?", thread,
                client=fake_client(_out(bubbles=["Beim Klinikum Augsburg 1 steht dazu nichts dabei."])))
    assert d["bubbles"] and d["slots"][LB.GROUNDED_KEY] == ["Klinikum Augsburg 1"]


def test_the_close_sequence_shortlist_grounds_the_names_it_told_the_model_to_use(small):
    thread = {"slots": dict(READY), "asked": []}
    clinic = LB.market_snapshot(READY)["offer"]["positions"][0]["clinic"]
    d = LB.turn("ok", thread, client=fake_client(_out(bubbles=[f"Zum Beispiel das {clinic}."])))
    assert d["bubbles"] == [f"Zum Beispiel das {clinic}."]


def test_ordinary_german_is_not_mistaken_for_a_clinic_name(small):
    """The detector must not fail a true sentence: "die Klinik Ihrer Wahl" and "im Krankenhaus
    Vollzeit arbeiten" name no house, and rejecting them would stall a live thread."""
    assert GR.check_reply(["Gern finde ich die Klinik Ihrer Wahl.",
                           "Möchten Sie im Krankenhaus Vollzeit arbeiten?"], set()) == []


def test_a_locked_harness_text_is_never_grounding_checked(small):
    """The reject/out-of-scope/decline bubbles are ours, not the model's."""
    thread = {"slots": {"region": "Bayern", "qualification_ok": True}, "asked": []}
    d = LB.turn("ich bin Pflegehelferin", thread,
                client=fake_client(_out(bubbles=["egal"], card_patch={"qualification_ok": False})))
    assert d["bubbles"] == [P.REJECT_BODY_DE] and d["action"] == "explain_not_placeable"


def _shown(result):
    """tools_server hands a listing back as {shown, total} (TASK-145); one posting comes back bare."""
    return result["shown"] if isinstance(result, dict) and "shown" in result else [result]


@pytest.mark.parametrize("tool, args", [
    ("search_postings", {"city": "Augsburg"}),
    ("search_postings", {"department": "OP"}),
    ("search_postings", {"city": "München"}),
    ("search_postings_with_housing", {"city": "München"}),
])
def test_the_replay_covers_what_the_tool_itself_returned(small, tool, args):
    """The grounding check reconstructs tool results from tools_server's OWN query code. This pins the
    two together: every clinic a tool actually put in front of the model is one the replay grounds, so
    a change on that side fails here instead of quietly rejecting a true sentence."""
    offset = GR.log_offset()
    result = getattr(TS, tool)(**args)
    shown = {r["clinic_name"] for r in _shown(result) if r}
    assert shown, "the fixture board has to make this call return something"
    assert shown <= GR.clinics_returned(GR.calls_since(offset)), (tool, args)


def test_one_posting_looked_up_by_id_grounds_its_own_clinic(small):
    """get_posting reads the ad itself through the board API, so the call is replayed from the log
    rather than by running the tool offline."""
    assert GR.clinics_returned([{"tool": "get_posting", "args": {"posting_id": 2}}]) == {"Klinikum Würzburg 2"}
    assert GR.clinics_returned([{"tool": "get_posting", "args": {"posting_id": 9999}}]) == set()


def test_a_tool_that_returns_no_clinic_name_grounds_nothing(small):
    offset = GR.log_offset()
    TS.count_postings(city="München")
    TS.list_cities_with_postings()
    assert GR.clinics_returned(GR.calls_since(offset)) == set()


# --- 3. FUNNEL CONTINUITY -----------------------------------------------------------------------

@pytest.mark.parametrize("card, stage", [
    ({}, "contact"),
    ({"region": "Bayern"}, "qualification"),
    ({"region": "Bayern", "qualification_path": "reject"}, "qualification"),
    ({"region": "Bayern", "qualification_path": "urkunde"}, "matching"),
    ({"region": "Bayern", "qualification_path": "urkunde", "city": "München"}, "matching"),
    ({"region": "Bayern", "qualification_path": "urkunde", "city": "München", "housing_needed": False}, "cv"),
    ({"region": "Bayern", "qualification_path": "urkunde", "city": "München", "housing_needed": False,
      "documents": [_CV]}, "documents"),
    (READY, "consent"),
    ({**READY, "anonymous_send_consent": True}, "submitted"),
])
def test_the_stage_names_the_first_gate_still_open(card, stage):
    board = LB.requirement_scoreboard(card)
    assert board["stage"] == stage == LB.funnel_stage(board)
    assert stage in LB.FUNNEL_STAGES


def test_the_stage_is_persisted_and_the_next_turn_resumes_from_it(small):
    """Ivan 2026-09-21: the dialog resumes from the stage instead of restarting."""
    first = LB.turn("Hallo", {"slots": {}, "asked": []},
                    client=fake_client(_out(card_patch={"region": "Bayern",
                                                        "qualification_path": "urkunde",
                                                        "qualification_ok": True})))
    assert first["slots"]["stage"] == "matching" and first["slots"]["stage_at"]

    seen = {}

    def capture(system, user, session_id):
        seen.update(json.loads(user))
        return _out(), session_id

    second = LB.turn("bin wieder da", {"slots": first["slots"], "asked": []}, client=fake_client(capture))
    assert seen["card"]["stage"] == "matching", "the model is told where the candidate already is"
    assert seen["requirement_scoreboard"]["stage"] == "matching"
    assert seen["requirement_scoreboard"]["stage_since"] == first["slots"]["stage_at"], (
        "resuming, not restarting: the stage keeps the moment it was entered")
    assert second["slots"]["stage_at"] == first["slots"]["stage_at"]


def test_a_stage_that_moves_on_gets_a_new_timestamp(small, monkeypatch):
    """The clock is stubbed because both turns land in the same second otherwise."""
    stamps = iter(["2026-09-21T09:00:00+00:00", "2026-09-21T09:05:00+00:00"])
    monkeypatch.setattr(LB.ST, "now_iso", lambda: next(stamps))
    first = LB.turn("Hallo", {"slots": {}, "asked": []},
                    client=fake_client(_out(card_patch={"region": "Bayern"})))
    assert (first["slots"]["stage"], first["slots"]["stage_at"]) == ("qualification", "2026-09-21T09:00:00+00:00")
    second = LB.turn("ja, die Urkunde habe ich", {"slots": first["slots"], "asked": []},
                     client=fake_client(_out(card_patch={"qualification_path": "urkunde",
                                                         "qualification_ok": True})))
    assert (second["slots"]["stage"], second["slots"]["stage_at"]) == ("matching", "2026-09-21T09:05:00+00:00")


def test_the_model_cannot_write_the_stage_or_the_grounding_memory_itself(small):
    out = _out(card_patch={"region": "Bayern", "stage": "submitted", "stage_at": "2020-01-01T00:00:00Z",
                           LB.GROUNDED_KEY: ["Klinikum Erfunden"]})
    d = LB.turn("Hallo", {"slots": {}, "asked": []}, client=fake_client(out))
    assert d["slots"]["stage"] == "qualification", "the stage follows the gates, never the model"
    assert LB.GROUNDED_KEY not in d["slots"]


# --- the rules are also written down where the model reads them ----------------------------------

def test_the_system_prompt_states_all_three_rules():
    system = P.system_prompt("{}", "{}")
    assert "VOLUME" in system and "At most 5 positions in ONE message" in system
    assert "offer.remaining_clinics" in system and "offer.narrow_by" in system
    assert '"narrow" or "pool"' in system
    assert "NO INVENTION" in system and "never a guess" in system
    assert "FUNNEL CONTINUITY" in system and "Never restart the funnel" in system
    assert "never send a board URL or job link" in system


def test_the_system_prompt_states_what_the_code_now_checks():
    """The model is told the same rules the harness enforces -- a check the model cannot see is a
    trap, and each of these was prompt-silent while the code was about to start rejecting on it."""
    system = P.system_prompt("{}", "{}")
    assert "A POSITION IS ANYTHING THEY CAN ACT ON" in system, "the cap counts positions, not names"
    assert "WHAT WAS TRUE LAST TURN IS NOT EVIDENCE NOW" in system, "STALE"
    assert "Saying you do NOT have a clinic the candidate named is always allowed" in system
    assert "QUALIFICATION PATH IS A FINDING, NOT A SCRATCHPAD" in system
    assert "http" not in P.BLOCKED_REPLY_DE, "the holding reply is a reply, not a link"


def test_escalation_defaults_to_attempting_an_answer_not_calling_a_human():
    """Ivan 2026-09-22, right after 'München oder Nürnberg' broke a live dialog: escalation must stay
    a rare last resort, never an easy default for something the model could try or ask about instead."""
    rule = next(r for r in P.RULES if r.startswith("ESCALATION:"))
    assert "the default is to ATTEMPT an answer" in rule
    assert "ASK A CLARIFYING QUESTION" in rule
    assert "never as a substitute for trying" in rule
    # still names the genuine-unknown cases escalation IS for, unchanged in substance
    assert "pets, visa specifics, a policy question" in rule
    assert "unreadable" in rule and "attachment" in rule
    assert "never for a short typo, timing or weekday answer" in rule


def test_tools_rule_allows_several_calls_in_one_turn_merged_by_the_model():
    """Several tool calls in one turn, merged by the model, is allowed and expected when one call's
    parameters cannot cover every dimension the candidate named -- not a reason to escalate."""
    rule = next(r for r in P.RULES if r.startswith("TOOLS (mandatory"))
    assert "MORE THAN ONE TOOL CALL, SAME TURN" in rule
    assert "Make every call you need, IN THE SAME TURN, and combine the results yourself" in rule
    assert "never a reason to escalate or to ask permission first" in rule
    # honest about what is ALREADY solved at the tool level (_resolve_city, same-day fix) vs what
    # this guidance is actually for
    assert "Multi-city is already solved for you INSIDE one call, not an example of this" in rule
    assert "München oder Nürnberg" in rule
    assert "department, employment_type, role_class" in rule


def test_region_rule_answers_the_bavaria_half_instead_of_escalating_or_dropping_the_rest():
    """A candidate naming Bayern together with a Bundesland the board has no data for at all
    (Baden-Württemberg, Hessen) must get an honest, complete answer for the Bavaria half -- never a
    silently dropped other state, and never an escalation instead of answering."""
    rule = next(r for r in P.RULES if r.startswith("REGION:"))
    assert "this board covers Bavaria (Bayern) only" in rule
    assert "Bayern oder Baden-Württemberg" in rule
    assert "never silently drop the other Bundesland without acknowledging it was asked about" in rule
    assert "never a reason to escalate instead of answering" in rule
    # grounds the rule in the real, verified fact: the multi-town fix cannot help here
    assert "there is no board town to resolve for the other Bundesland at all" in rule


# --- wiring the CV matcher needs (TASK-145 lives in tools_server.py; this half is luna_brain's) ---

def test_the_tools_server_is_told_whose_cv_to_match(small, monkeypatch):
    """tools_server.match_cv_to_postings reads the stored CV of the thread it is answering, and can
    only find it if this side passes the number. Without one it says so and the model works around
    it, which is why the key is always present rather than conditionally added."""
    monkeypatch.setattr(LB.BV, "vocabulary_lines", lambda: {})
    env = json.loads(LB._mcp_config_path(small / "ready.json", "+4915550001234")
                     .read_text(encoding="utf-8"))["mcpServers"][LB.MCP_SERVER_NAME]["env"]
    assert env["WA_LUNA_PHONE"] == "+4915550001234"
    no_thread = json.loads(LB._mcp_config_path(small / "ready.json")
                           .read_text(encoding="utf-8"))["mcpServers"][LB.MCP_SERVER_NAME]["env"]
    assert no_thread["WA_LUNA_PHONE"] == ""
    assert f"mcp__{LB.MCP_SERVER_NAME}__match_cv_to_postings" in LB.MCP_TOOL_NAMES
    assert f"mcp__{LB.MCP_SERVER_NAME}__get_clinic_contact" not in LB.MCP_TOOL_NAMES, (
        "TASK-91: clinic contacts are for the human handoff, never the candidate-facing turn")


def test_the_turn_hands_its_own_number_to_the_client(small):
    client = fake_client(_out())
    LB.turn("Hallo", {"phone": "+4915550001234", "slots": {}, "asked": []}, client=client)
    assert client.phone == "+4915550001234"


# --- TASK-146: what the grounding evidence is, and where it comes from --------------------------
def test_evidence_is_what_the_tool_showed_and_not_what_the_query_matched(hundred):
    """A listing tool shows 5 rows and a total. Replaying the query WITHOUT that cut made every
    clinic the query matched valid evidence, so on a 100-clinic board a reply could name row 63 --
    which the model never saw -- and invent its housing, employment type and start date, and both
    rules passed. The mission's own lens is that a reply cannot name a posting the tools did not
    return, and a matched-but-unshown row was not returned."""
    offset = GR.log_offset()
    shown = TS.search_postings(city="München")
    assert len(shown["shown"]) == TS.LISTING_LIMIT and shown["total"] == 100

    evidence = GR.clinics_returned(GR.calls_since(offset))
    assert evidence == {r["clinic_name"] for r in shown["shown"]}
    with pytest.raises(AssertionError, match="NO INVENTION"):
        GR.check_reply(["Im Klinikum München 63 ist eine Intensivstelle mit Wohnung frei."], evidence)


def test_a_logging_failure_is_a_logging_failure_and_not_a_false_invention(small, monkeypatch):
    """Since TASK-144 the call log is the sole source of grounding evidence, so swallowing a write
    error left the tools returning rows while the evidence set was empty -- every truthful clinic
    name in the reply was then rejected as an invention, on every turn, with nothing anywhere
    recording why (TASK-146)."""
    monkeypatch.setattr(TS, "_session_dir", lambda: C.LUNA_SESSION_DIR / "nope")

    def refuse(*_a, **_kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(TS.Path, "mkdir", refuse)
    with pytest.raises(OSError):
        TS.search_postings(city="München")


def test_a_town_with_nothing_live_answers_zero_instead_of_unknown_town(tmp_path, monkeypatch):
    """The error text says in so many words that an unknown town is NOT the same as nothing being
    open there -- and the town set it was built from was the live-verified one, so the two states
    it exists to separate were conflated. A candidate who typed their own town correctly was asked
    to re-spell it, or offered a spelling-near DIFFERENT town (TASK-146)."""
    gone = {**_job(2, city="Coburg"), "verify_status": "gone"}
    _board([_job(1, city="München"), gone], monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")

    found = TS.search_postings(city="Coburg")
    assert (found["shown"], found["total"]) == ([], 0)
    assert TS.count_postings(city="Coburg")["postings"] == 0
    with pytest.raises(TS.ToolError, match="not a town this board has"):
        TS.search_postings(city="Musterstadt")


def test_a_clinic_whose_only_posting_is_gone_is_not_a_clinic_with_openings(tmp_path, monkeypatch):
    """The posting doors have refused withheld rows since TASK-145; the clinic doors counted
    jobs_open and handed them over anyway, and grounding then turned the name into valid evidence
    for telling a candidate that house has openings (TASK-146)."""
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    _board([{**_job(1, city="Coburg"), "verify_status": "gone"}], monkeypatch)
    D._snap["clinics"] = [{"clinic_id": "c1", "name": "Klinikum Coburg 1", "town": "Coburg",
                           "regierungsbezirk": "Oberfranken", "beds": 300,
                           "jobs_open": 1, "jobs_live": 0, "jobs_fresh": 0, "routable": False}]

    assert TS.list_clinics(city="Coburg") == []
    assert TS._api_clinics({"city": "Coburg", "has_jobs": "1"})["rows"] == []


def test_a_posting_whose_detail_cannot_be_read_is_loud_rather_than_all_nulls(small, monkeypatch):
    """This tool's own description tells the model a null field "means this ad did not say it,
    never that the answer is no". `or {}` made a failed detail read indistinguishable from an ad
    that genuinely said nothing, so the candidate was told "das steht nicht dabei" about an ad
    nobody read (TASK-146)."""
    monkeypatch.setattr(D, "job_detail", lambda _pid: None)
    with pytest.raises(TS.ToolError, match="detail row could not be read"):
        TS.get_posting(1)


def test_no_tool_row_carries_a_board_url(small):
    """offer.py keeps the link out of the payload the model writes from on purpose -- "the way to
    make that a guarantee rather than a rule is to keep the link out" -- while every tool-sourced
    row handed one over and check_reply does not reject a URL in a bubble (TASK-146)."""
    row = TS.search_postings(city="München")["shown"][0]
    assert "source_url" not in row and "external_url" not in row
    assert not any("http" in str(v) for v in row.values())


@pytest.mark.parametrize("path, query", [
    ("/api/jobs", "city=München&limit=5"),
    ("/api/clinics", "limit=5"),
    ("/api/search", "q=Klinikum München"),
    ("/api/cities", ""),
])
def test_no_board_api_result_carries_a_url_either(small, path, query):
    """TASK-150 AC#3, made true in TASK-151. The preset tools were clean; ``board_api_get`` -- which
    prompts.py tells the model to use -- was not: /api/jobs returned ``external_url`` on every row
    and /api/clinics returned ``website``/``careers_url``/``board``. Measured on the live board
    2026-09-21: row 0 of ``board_api_get('/api/jobs','city=Augsburg&limit=2')`` carried
    ``https://uk-augsburg.softgarden.io/job/66836575/...``."""
    assert "http" not in json.dumps(TS.board_api_get(path, query), default=str, ensure_ascii=False)


def test_the_test_thread_links_never_re_enter_the_model_s_payload(small):
    """The other half of AC#3: the remembered links live on the card, and the card goes into the
    payload. TEST_SOURCES_KEY is hidden from it, so the model cannot read a URL back out of its own
    thread on the next turn."""
    card = {"region": "Bayern",
            LB.TEST_SOURCES_KEY: [{"clinic": "Klinikum Augsburg 1", "posting_id": 1,
                                   "url": "https://example.org/job/1"}]}
    payload = LB._user_payload("hallo", card, LB.requirement_scoreboard(card), {})
    assert "http" not in payload and LB.TEST_SOURCES_KEY not in json.loads(payload)["card"]


# --- the audit counterexamples of 2026-09-21, each proved against the live board first ------------
# Every case below was demonstrated end to end before it was fixed: a sixth house through the cap,
# ten positions the cap never saw, an umlaut-free fabrication sent with no tool call in the turn,
# four shapes of truthful German that killed the turn, a false "nur diese 5", a URL in a bubble, a
# closed posting confirmed as open, and a five-stage funnel reset through one side field.

@pytest.fixture()
def pair_board(tmp_path, monkeypatch):
    """Six houses, two of them the containment pair the cap lost a house through: the live board
    really carries BOTH "Klinik Mindelheim" and "Kreisklinik Mindelheim" (16 such pairs among the
    255 clinics with a live posting on 2026-09-21), and the old name-containment dedup swallowed the
    shorter into the longer, so six positions counted as five."""
    names = ["Kreisklinik Mindelheim", "Klinik Mindelheim", "Bezirkskrankenhaus Günzburg",
             "Klinik Günzburg", "Sana Klinikum Coburg", "Klinikum Nürnberg"]
    _board([_job(i + 1, city="München", clinic=name) for i, name in enumerate(names)], monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    return names


def test_two_board_houses_whose_names_contain_each_other_cost_two_positions(pair_board):
    """Audit A2: six real houses went out as five because "Klinik Mindelheim" was read as part of
    "Kreisklinik Mindelheim". They are two different board entries and two different positions."""
    with pytest.raises(AssertionError, match="VOLUME"):
        GR.check_reply(["Passend sind: " + ", ".join(pair_board) + "."], set(pair_board))
    assert len(GR.check_reply(["Passend sind: " + ", ".join(pair_board[:5]) + "."],
                              set(pair_board))) == OF.OFFER_LIMIT


def test_one_house_written_once_costs_one_position_however_it_is_spelled(pair_board):
    """The other direction of the same rule: the board's shorter name found INSIDE the longer one
    the reply actually wrote is one house, not two -- otherwise every full name would cost double."""
    assert GR.check_reply(["Die Kreisklinik Mindelheim sucht gerade."], {"Kreisklinik Mindelheim"}) == [
        "Kreisklinik Mindelheim"]


def test_ten_positions_without_a_single_clinic_name_are_still_ten_positions(small):
    """Audit A1: the cap counted detected clinic names, so a numbered list of ten jobs that names no
    house at all went out untouched. Ivan's five is a cap on what the candidate can act on."""
    ten = ("Ich habe zehn Stellen für Sie: 1) OP Vollzeit; 2) OP Teilzeit; 3) Intensiv; 4) Innere; "
           "5) Notaufnahme; 6) Anästhesie; 7) Stroke Unit; 8) Dialyse; 9) Palliativ; 10) Geriatrie.")
    with pytest.raises(AssertionError, match="VOLUME"):
        GR.check_reply([ten], set())
    five = "Zur Auswahl: 1) OP Vollzeit; 2) OP Teilzeit; 3) Intensiv; 4) Innere; 5) Notaufnahme."
    assert GR.check_reply([five], set()) == [], "five is the cap, not four"


def test_a_bulleted_list_counts_the_same_way(small):
    bullets = "\n".join(["Das hätte ich:", "- OP Vollzeit", "- Intensiv", "- Innere", "- Dialyse",
                         "- Palliativ", "- Geriatrie"])
    with pytest.raises(AssertionError, match="VOLUME"):
        GR.check_reply([bullets], set())


@pytest.mark.parametrize("bubble, rule", [
    ("Das Klinikum Munchen-Waldperlach sucht gerade Pflegefachkraefte.", "NO INVENTION"),
    ("Das Klinikum München-Waldperlach sucht gerade Pflegefachkräfte.", "NO INVENTION"),
    ("Im Klinikum Wurzburg 2 ist eine Stelle frei.", "NO INVENTION"),
])
def test_writing_a_name_without_umlauts_does_not_hide_it_from_either_detector(small, bubble, rule):
    """Audit A3/B: both detectors were spelling-exact. "Klinikum Munchen-Waldperlach" -- a
    fabrication -- was SENT with zero tool calls in the turn while the identical sentence with
    umlauts was blocked, and 37 of 255 real clinics could be hidden from the cap the same way. This
    population writes German without umlauts; spelling is not identity."""
    with pytest.raises(AssertionError, match=rule):
        GR.check_reply([bubble], set())


def test_a_real_house_written_without_umlauts_is_sayable_once_a_tool_returned_it(small):
    """The same fold, in the direction that keeps a truthful turn alive."""
    assert GR.check_reply(["Im Klinikum Wurzburg 2 ist eine Stelle frei."],
                          {"Klinikum Würzburg 2"}) == ["Klinikum Würzburg 2"]


def test_a_tool_call_that_failed_contributes_no_evidence_at_all(small):
    """Audit B: one match_cv_to_postings that raised a ToolError still injected all 255 live clinics
    into the evidence set, so the model could name any house in Bavaria without looking anything up
    -- while grounding.py's own docstring claimed a call that raised contributes nothing."""
    assert GR.clinics_returned([{"tool": "match_cv_to_postings", "args": {}}], phone=None) == set()
    assert GR.clinics_returned([{"tool": "search_postings", "args": {"city": "Musterstadt"}}]) == set()


def test_a_clinic_looked_up_through_the_fuzzy_search_door_is_replayed_as_evidence(small):
    """Audit C: /api/search is the only door to a clinic BY NAME and the prompt sends the model
    through it when the candidate names a house -- but its hits were not replayed, so using that
    door and then naming what it found killed the turn."""
    calls = [{"tool": "board_api_get", "args": {"path": "/api/search", "query": "q=Klinikum Augsburg 1"}}]
    assert "Klinikum Augsburg 1" in GR.clinics_returned(calls)


def test_saying_we_do_not_have_the_house_the_candidate_named_is_not_an_invention(small):
    """Audit C, the costliest false positive: the bot honestly answering that it does NOT have the
    clinic the candidate asked about was rejected as an invention, and the candidate got silence."""
    evidence = GR.turn_evidence(LB.market_snapshot({}), [], inbound="Haben Sie das Krankenhaus Coburg-West?")
    assert GR.check_reply(["Das Krankenhaus Coburg-West habe ich leider nicht im Bestand."],
                          evidence["names"], deniable=evidence["deniable"]) == []
    with pytest.raises(AssertionError, match="NO INVENTION"):
        GR.check_reply(["Das Krankenhaus Coburg-West sucht gerade Pflegefachkräfte."],
                       evidence["names"], deniable=evidence["deniable"])


def test_a_true_general_statement_about_housing_names_no_house(small):
    assert GR.check_reply(["Manche Kliniken bieten eine Unterkunft an, viele leider nicht."], set()) == []


def test_the_correct_spelling_of_a_board_record_with_a_scrape_typo_is_accepted(tmp_path, monkeypatch):
    """Audit C: the board writes "Bezirkskranken-haus Werneck". Spelling it properly was rejected as
    an invention while reproducing the typo was accepted -- the model was being asked to copy a
    scrape error to stay inside the rules."""
    _board([_job(1, city="Würzburg", clinic="Bezirkskranken-haus Werneck")], monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    allowed = {"Bezirkskranken-haus Werneck"}
    assert GR.check_reply(["Das Bezirkskrankenhaus Werneck sucht gerade."], allowed)
    assert GR.check_reply(["Das Bezirkskranken-haus Werneck sucht gerade."], allowed)


def test_saying_these_are_all_there_are_reaches_the_candidate_and_is_flagged_not_blocked(hundred):
    """Audit D: "Es gibt nur diese 5 Kliniken in Bayern" was accepted with a true count of 224,
    because nothing checked the reply against remaining_clinics -- TASK-144 made it BLOCK. ROUND 5
    (grounding.py's module docstring) took it back off the blocking path: the reply reaches the
    candidate and the sentence is recorded in ``flagged`` instead. The remainder ("95 weitere") is
    stated so the still-BLOCKING remainder-disclosure rule does not fire and mask what this test is
    isolating."""
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]]
    counts = {offer["clinics_total"], offer["remaining_clinics"]}
    body = ("Es gibt nur diese 5 Kliniken in Bayern: " + ", ".join(named) +
            f". Es gibt {offer['remaining_clinics']} weitere.")
    flagged = []
    assert len(GR.check_reply([body], set(named), counts=counts,
                              remaining=offer["remaining_clinics"], flagged=flagged)) == OF.OFFER_LIMIT
    assert flagged, "the exhaustive-claim shape is still detected, just not blocked on"


def test_naming_several_positions_without_saying_how_many_more_is_rejected(hundred):
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]]
    counts = {offer["clinics_total"], offer["remaining_clinics"]}
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply(["Passend sind: " + ", ".join(named) + "."], set(named), counts=counts,
                       remaining=offer["remaining_clinics"])
    ok = ("Passend sind: " + ", ".join(named) + f". Es gibt {offer['remaining_clinics']} weitere.")
    assert len(GR.check_reply([ok], set(named), counts=counts,
                              remaining=offer["remaining_clinics"])) == OF.OFFER_LIMIT


def test_an_offer_turn_without_branch_wording_is_no_longer_rejected(hundred):
    """Ivan, 2026-09-22: the forced narrow/pool branch menu is gone -- BRANCHES stopped blocking, so
    a reply naming five with neither branch phrasing (audit D's old failure case) now passes."""
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]]
    counts = {offer["clinics_total"], offer["remaining_clinics"]}
    body = "Passend sind: " + ", ".join(named) + f". Es gibt {offer['remaining_clinics']} weitere."
    assert len(GR.check_reply([body], set(named), counts=counts, remaining=offer["remaining_clinics"],
                              branches=True)) == OF.OFFER_LIMIT
    both = body + " Wollen Sie eingrenzen, oder soll ich Sie allen passenden Kliniken vorschlagen?"
    assert GR.check_reply([both], set(named), counts=counts, remaining=offer["remaining_clinics"],
                          branches=True)


def test_an_open_question_offer_turn_is_not_rejected_and_count_is_still_enforced(hundred):
    """TASK (2026-09-22, Ivan): the open question replacing the forced branch menu carries no
    narrow/pool wording at all and must not be rejected for that. The remainder/COUNT check right
    above BRANCHES in the same rule is untouched: missing the number of how many more matched, or
    stating a wrong one, still fails the turn."""
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]]
    remainder = offer["remaining_clinics"]
    counts = {offer["clinics_total"], remainder}
    open_q = ("Passend sind: " + ", ".join(named) + f". Es gibt {remainder} weitere. "
              "Was ist Ihnen bei der Auswahl besonders wichtig?")
    assert len(GR.check_reply([open_q], set(named), counts=counts, remaining=remainder,
                              branches=True)) == OF.OFFER_LIMIT
    missing_remainder = "Passend sind: " + ", ".join(named) + ". Was ist Ihnen dabei wichtig?"
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply([missing_remainder], set(named), counts=counts, remaining=remainder,
                       branches=True)
    wrong_remainder = ("Passend sind: " + ", ".join(named) + ". Es gibt 999 weitere. "
                        "Was ist Ihnen dabei wichtig?")
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply([wrong_remainder], set(named), counts=counts, remaining=remainder,
                       branches=True)


def test_a_bubble_carrying_a_board_url_is_rejected(small):
    """Audit D: the payload is link-free by construction, but a URL the model wrote from its own
    memory went out untouched -- there was no check on the text."""
    with pytest.raises(AssertionError, match="LINK"):
        GR.check_reply(["Hier die Übersicht: https://pflege-job-radar.de/jobs"], set())
    with pytest.raises(AssertionError, match="LINK"):
        GR.check_reply(["Schauen Sie auf pflege-job-radar.de nach."], set())


def test_a_posting_the_verifier_marked_gone_cannot_be_confirmed_as_still_open(small, monkeypatch):
    """Audit E: _grounded_clinics was a permanent per-thread allowlist. Turn 1 named the house
    legitimately; the verifier then marked its posting gone; turn 2 said "die Stelle ist noch frei"
    and it was ACCEPTED and sent."""
    thread = {"slots": {"region": "Bayern", LB.GROUNDED_KEY: ["Klinikum Augsburg 1"]}, "asked": []}
    _board([j for j in D.jobs() if j["clinic_name"] != "Klinikum Augsburg 1"], monkeypatch)
    d = LB.turn("ist die Stelle noch frei?", thread,
                client=fake_client(_out(bubbles=["Beim Klinikum Augsburg 1 ist die Stelle noch frei."])))
    assert d["bubbles"] == [P.BLOCKED_REPLY_DE]
    assert "STALE" in d["slots"]["_escalate_reason"]


def test_the_same_house_may_still_be_named_in_the_sentence_that_says_it_is_gone(small, monkeypatch):
    """The other half of the same rule: telling the candidate the position is no longer available
    has to stay sayable, or the honest answer is the one that dies."""
    thread = {"slots": {"region": "Bayern", LB.GROUNDED_KEY: ["Klinikum Augsburg 1"]}, "asked": []}
    _board([j for j in D.jobs() if j["clinic_name"] != "Klinikum Augsburg 1"], monkeypatch)
    honest = "Die Stelle beim Klinikum Augsburg 1 ist leider nicht mehr frei."
    d = LB.turn("ist die Stelle noch frei?", thread, client=fake_client(_out(bubbles=[honest])))
    assert d["bubbles"] == [honest] and "_escalated" not in d["slots"]


def test_an_empty_board_is_a_loud_failure_not_a_silent_verdict_on_every_name(small, monkeypatch):
    """The liveness re-check has to tell "this house closed" apart from "the board did not load".
    Treating a failed snapshot as "every remembered clinic is gone" would reject every true sentence
    on the thread instead of saying so (CLAUDE.md: fail loudly)."""
    _board([], monkeypatch)
    with pytest.raises(RuntimeError, match="board snapshot holds no live posting"):
        GR.turn_evidence({}, [], ["Klinikum Augsburg 1"])


def test_the_funnel_stage_cannot_be_reset_through_the_qualification_path(small):
    """Audit F: one schema-legal card_patch {qualification_path: "unknown"} dropped the stage from
    consent back to qualification while the Urkunde was still on card.documents, and the bot then
    re-asked for the document it was holding. Five stages through a side field."""
    assert LB.requirement_scoreboard(READY)["stage"] == "consent"
    d = LB.turn("hm, ich weiß nicht", {"slots": dict(READY), "asked": []},
                client=fake_client(_out(card_patch={"qualification_path": "unknown"})))
    assert d["slots"]["qualification_path"] == "urkunde", "the document on the card outranks the patch"
    assert d["slots"]["stage"] == "consent"
    refused = d["slots"][LB.REFUSED_PATCH_KEY]
    assert refused[0]["field"] == "qualification_path" and refused[0]["to"] == "unknown"
    assert LB.requirement_scoreboard(d["slots"])["qualification_document"] == "satisfied"


def test_a_real_correction_of_the_qualification_path_still_goes_through(small):
    """The guard is about re-opening a settled gate, not about freezing the field: a different real
    path is a finding the model is supposed to record."""
    d = LB.turn("die Urkunde ist noch im Anerkennungsverfahren", {"slots": dict(READY), "asked": []},
                client=fake_client(_out(card_patch={"qualification_path": "defizit"})))
    assert d["slots"]["qualification_path"] == "defizit" and LB.REFUSED_PATCH_KEY not in d["slots"]


def test_declaring_a_candidate_unplaceable_is_said_out_loud_whichever_field_does_it(small):
    """TASK-151, audit F's second door: qualification_path:"reject" was waved through as "a verdict,
    said out loud" while nothing said it. One ordinary bubble went out, the stage dropped from
    consent to qualification, and the next turn treated a candidate holding a German Urkunde as not
    placeable -- without a word to them about it."""
    card = {k: v for k, v in READY.items() if k != "documents"}
    d = LB.turn("ich bin Pflegehelferin", {"slots": card, "asked": []},
                client=fake_client(_out(bubbles=["Alles klar, ich melde mich."],
                                        card_patch={"qualification_path": "reject"})))
    assert d["bubbles"] == [P.REJECT_BODY_DE], "the candidate hears the verdict, in the locked wording"
    assert d["action"] == "explain_not_placeable"
    assert d["slots"]["qualification_path"] == "reject"


def test_a_reject_the_card_s_own_document_contradicts_is_refused_like_any_other_reset(small):
    """The same side door, on a card that holds the Urkunde: the document outranks the patch exactly
    as it does for "unknown", and the loud gate (qualification_ok: false) stays the way to say it."""
    d = LB.turn("hm", {"slots": dict(READY), "asked": []},
                client=fake_client(_out(card_patch={"qualification_path": "reject"})))
    assert d["slots"]["qualification_path"] == "urkunde" and d["slots"]["stage"] == "consent"
    assert d["slots"][LB.REFUSED_PATCH_KEY][0]["to"] == "reject"
    assert d["bubbles"] == ["Hallo 🙂"], "the model's own turn still goes out; only the reset is refused"


def test_the_path_is_freely_writable_while_no_qualification_document_is_on_the_card(small):
    """Where the model is the only one who knows what was said, it decides -- the guard needs the
    card's own evidence to fire at all."""
    card = {**READY, "documents": [_CV]}
    d = LB.turn("ich bin unsicher", {"slots": card, "asked": []},
                client=fake_client(_out(card_patch={"qualification_path": "unknown"})))
    assert d["slots"]["qualification_path"] == "unknown" and LB.REFUSED_PATCH_KEY not in d["slots"]


def test_the_correction_may_look_the_house_up_and_then_name_it(small):
    """The corrective payload tells the model it may call a tool first, so the retry is checked
    against the evidence AFTER that call -- checking it against the evidence from before would
    reject the very sentence the lookup was made to ground."""
    attempts = []

    def reply(system, user, session_id):
        attempts.append(user)
        if len(attempts) == 1:
            return _out(bubbles=["Das Klinikum Augsburg 1 sucht gerade."]), session_id
        TS.search_postings(city="Augsburg")          # the lookup the correction asked for
        return _out(bubbles=["Das Klinikum Augsburg 1 sucht gerade."]), session_id

    d = LB.turn("was gibt es in Augsburg?", {"slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))
    assert d["bubbles"] == ["Das Klinikum Augsburg 1 sucht gerade."]
    assert d["action"] == "reply_after_correction" and "_escalated" not in d["slots"]
    assert d["slots"][LB.GROUNDED_KEY] == ["Klinikum Augsburg 1"]


def test_the_replay_covers_what_list_clinics_itself_returned(tmp_path, monkeypatch):
    """The clinic door reads the candidate's spelling of a town the way the registry writes it
    ("Wuerzburg" -> "Würzburg"). A replay that compared the raw string instead found nothing, so a
    clinic the tool really put in front of the model would have been rejected as an invention. The
    two sides are pinned to each other here rather than trusted to stay in step (TASK-146)."""
    _board([_job(1, city="Würzburg", clinic="Klinikum Würzburg 1")], monkeypatch)
    D._snap["clinics"] = [{"clinic_id": "c1", "name": "Klinikum Würzburg 1", "town": "Würzburg",
                           "regierungsbezirk": "Unterfranken", "beds": 300,
                           "jobs_open": 1, "jobs_live": 1, "jobs_fresh": 1, "routable": True}]
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")

    offset = GR.log_offset()
    shown = {row["name"] for row in TS.list_clinics(city="Wuerzburg")}
    assert shown == {"Klinikum Würzburg 1"}, "the tool answers the candidate's own spelling"
    assert shown <= GR.clinics_returned(GR.calls_since(offset)), (
        "and every clinic it showed is evidence the reply may name")


def test_a_blocked_turn_does_not_attach_consent_buttons_to_the_holding_message(small):
    """The consent buttons belong to the message that asks for consent. A blocked turn never sent
    that ask, so the offer is un-made: attaching the buttons to "a colleague will look at it" would
    take a tap as consent for a question the candidate never saw, and leaving the flag set would
    mean the NEXT ask is no longer the first one -- the buttons would never appear again."""
    out = _out(bubbles=["Im Klinikum München 63 ist eine Stelle frei, sollen wir Sie vorstellen?"],
               card_patch={"anonymous_send_offered": True})
    d = LB.turn("ja gerne", {"slots": dict(READY), "asked": []}, client=fake_client(out))
    assert d["bubbles"] == [P.BLOCKED_REPLY_DE] and d["buttons"] == []
    assert not d["slots"]["anonymous_send_offered"], "the offer was not made"


# --- the reviewer counterexamples of 2026-09-21 (TASK-151) ----------------------------------------
# Every case below was demonstrated against the LIVE board (255 clinics, 2462 live postings) before
# it was fixed: three ten-position messages the cap read as zero, a fabricated house with no head
# word from the list, a fabricated site built around a real one, four ways to say "that is all", an
# invented total riding next to a true one, a truthful denial killed for missing a nicht/kein token,
# a factual follow-up forced to recite a total, a career URL on a TLD the backstop did not know, and
# a posting confirmed as open after the verifier had removed it while the house stayed live.

@pytest.mark.parametrize("bubble", [
    # prose -- the announcement makes the separators item separators
    "Ich habe zehn Stellen für Sie: OP in Vollzeit, OP in Teilzeit, Intensiv/IMC in Vollzeit, "
    "Intensiv/IMC in Teilzeit, Notaufnahme in Vollzeit, Notaufnahme in Teilzeit, Innere Medizin in "
    "Vollzeit, Innere Medizin in Teilzeit, OP in Vollzeit und Intensiv/IMC in Teilzeit.",
    # one per line, no marker at all
    "Ich habe zehn Stellen:\nOP Vollzeit\nOP Teilzeit\nIntensiv/IMC Vollzeit\nIntensiv/IMC Teilzeit\n"
    "Notaufnahme Vollzeit\nNotaufnahme Teilzeit\nInnere Medizin Vollzeit\nInnere Medizin Teilzeit\n"
    "OP Vollzeit\nIntensiv/IMC Teilzeit",
    # letter markers
    "a) OP Vollzeit; b) OP Teilzeit; c) Intensiv/IMC Vollzeit; d) Intensiv/IMC Teilzeit; "
    "e) Notaufnahme Vollzeit; f) Notaufnahme Teilzeit; g) Innere Medizin Vollzeit; "
    "h) Innere Medizin Teilzeit; i) OP Vollzeit; j) Intensiv/IMC Teilzeit.",
])
def test_ten_positions_are_ten_positions_in_any_layout(small, bubble):
    """The cap counted a marker, not a position: the same ten jobs as prose, one per line, or under
    letter markers returned 0 and were SENT, while "1) 2) 3) ..." returned 10 and was blocked."""
    with pytest.raises(AssertionError, match="VOLUME"):
        GR.check_reply([bubble], set())


@pytest.mark.parametrize("bubble", [
    "Das Klinikum Waldkraiburg sucht Pflegefachkräfte mit Wohnung.",
    "Im Waldperlachklinikum ist eine Stelle frei.",
    "Das Rotkreuzklinikum sucht gerade Pflegefachkräfte.",
])
def test_a_fabricated_house_is_caught_whatever_town_it_claims(small, bubble):
    """Detector 2 needed a word from the board's own vocabulary to corroborate the span, so a house
    in a town with no live posting ("Waldkraiburg", "Zwiesel") was corroborated by nothing and went
    out with zero tool calls; and a one-word compound ("Waldperlachklinikum", "Rotkreuzklinikum" --
    the ordinary German form, and the one the module's own docstring named) was skipped outright
    because the span had fewer than two tokens."""
    with pytest.raises(AssertionError, match="NO INVENTION"):
        GR.check_reply([bubble], set())


@pytest.fixture()
def kinds_board(tmp_path, monkeypatch):
    """Houses whose KIND is not one of the ten seed stems. The live board has 26 such clinics
    ("Diakoneo KdöR", "Salus Gesundheitszentrum", "Thoraxzentrum Bezirk Unterfranken", "Deutsches
    Zentrum für Kinderund Jugendrheumatologie"), and a fabricated house built the same way was
    invisible to detector 2 -- which is what an invented clinic actually looks like."""
    names = ["Salus Gesundheitszentrum", "Deutsches Zentrum für Kinderrheumatologie",
             "Klinikum Coburg 1"]
    _board([_job(i + 1, city="Coburg", clinic=n) for i, n in enumerate(names)], monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    return names


def test_the_kinds_of_house_are_read_off_the_board_not_off_a_list(kinds_board):
    assert {"gesundheitszentrum", "zentrum"} <= GR.board_head_words()
    assert "coburg" not in GR.board_head_words(), "a town is what a name is built FROM, not a kind"


@pytest.mark.parametrize("bubble", [
    "Das Gesundheitszentrum Coburg Nord sucht gerade Pflegefachkräfte.",
    "Das Medizinische Zentrum Coburg Süd sucht gerade Pflegefachkräfte.",
])
def test_a_fabricated_house_whose_kind_is_not_a_seed_stem_is_caught(kinds_board, bubble):
    with pytest.raises(AssertionError, match="NO INVENTION"):
        GR.check_reply([bubble], set())


@pytest.mark.parametrize("bubble", [
    "Wir suchen im Klinikum Pflegefachkräfte für die Intensivstation.",
    "Der Standort München ist gut angebunden und die Krankenhausleitung entscheidet selbst.",
    "Wir haben eine Stelle in der Altersmedizin frei.",
    "Manche Kliniken bieten eine Unterkunft an, viele leider nicht.",
])
def test_the_wider_detector_still_lets_ordinary_german_through(small, bubble):
    """The other direction of the same widening, and the one that costs a candidate their answer:
    a head word next to the board's own job German is prose, not a house."""
    assert GR.check_reply([bubble], set()) == []


def test_a_site_invented_around_a_real_house_is_not_grounded_by_it(pair_board):
    """Containment went both ways, so a name CONTAINING an evidence name was accepted: the model
    looked Mindelheim up and wrote "Die Kreisklinik Mindelheim Nord sucht Pflegefachkräfte" -- a
    house that does not exist -- and it was sent AND written into the thread's memory."""
    evidence = {"Kreisklinik Mindelheim", "Klinik Mindelheim"}
    with pytest.raises(AssertionError, match="NO INVENTION"):
        GR.check_reply(["Die Kreisklinik Mindelheim Nord sucht Pflegefachkräfte."], evidence)
    assert GR.check_reply(["Die Kreisklinik Mindelheim sucht Pflegefachkräfte."], evidence) == [
        "Kreisklinik Mindelheim"]


def test_the_memory_gets_the_board_s_spelling_and_not_the_model_s(pair_board):
    """What goes into card[GROUNDED_KEY] has to be a name the board really has, or the next turn
    grounds on the model's own wording."""
    assert GR.check_reply(["Die Kreisklinik sucht gerade."], {"Kreisklinik Mindelheim"}) == [
        "Kreisklinik Mindelheim"]


@pytest.mark.parametrize("tail", [
    "Damit kennen Sie alle Kliniken in München.",
    "Das ist unser komplettes Angebot in München.",
    "Weitere Häuser gibt es in München nicht.",
    "In München haben wir sonst nichts.",
])
def test_ordinary_ways_of_saying_that_is_everything_are_caught_too(hundred, tail):
    """_EXHAUSTIVE_RE was five literal alternatives, and any true number riding along satisfied the
    rest of COUNT -- so a false "that is all" went out next to a correct remainder. Detection still
    catches all four (``flagged``); ROUND 5 stopped this rule from blocking the reply on it."""
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]]
    body = ("Passend sind: " + ", ".join(named) + f". Es gibt {offer['remaining_clinics']} weitere. " + tail)
    flagged = []
    assert len(GR.check_reply([body], set(named), counts={offer["clinics_total"], offer["remaining_clinics"]},
                              remaining=offer["remaining_clinics"], flagged=flagged)) == OF.OFFER_LIMIT
    assert flagged


def test_that_is_everything_is_false_even_when_the_message_names_nothing(hundred):
    """The same falsehood one message later, with no houses in it at all: "Mehr gibt es nicht." was
    accepted because the rule keyed on how many names the bubble carried. Still detected and flagged
    (ROUND 5); no longer blocks -- there is nothing else here to keep this reply from going out."""
    flagged = []
    assert GR.check_reply(["Mehr gibt es nicht."], set(), counts={100, 95}, remaining=95,
                          flagged=flagged) == []
    assert flagged


def test_the_pool_branch_is_an_offer_and_not_a_claim_that_those_are_all(hundred):
    """"soll ich Sie allen Kliniken vorschlagen?" says "all" and must stay sayable."""
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]]
    body = ("Passend sind: " + ", ".join(named) + f". Es gibt {offer['remaining_clinics']} weitere. "
            "Wollen Sie eingrenzen, oder soll ich Sie allen passenden Kliniken vorschlagen?")
    assert len(GR.check_reply([body], set(named),
                              counts={offer["clinics_total"], offer["remaining_clinics"]},
                              remaining=offer["remaining_clinics"], branches=True)) == OF.OFFER_LIMIT


def test_the_offer_turns_own_required_pool_wording_is_not_flagged_as_an_exhaustive_claim(hundred):
    """F3 (verification, 2026-09-22): the offer turn's own required pool-branch sentence -- "...oder
    darf ich Sie gleich fuer alle dort passenden Stellen vormerken?" -- says "alle ... Stellen" but is
    the harness's own required BRANCHES wording (Ivan's rule (b)), not a claim about how many exist.
    _OFFER_SENTENCE_RE's verb list missed "vormerken", so this correct, required sentence flagged on
    every offer turn phrased this way -- a flag on the happy path teaches a human to ignore flags."""
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]]
    body = ("Passend sind: " + ", ".join(named) + f". Es gibt {offer['remaining_clinics']} weitere. "
            "Wollen Sie eingrenzen, oder darf ich Sie gleich für alle dort passenden Stellen vormerken?")
    flagged = []
    assert len(GR.check_reply([body], set(named),
                              counts={offer["clinics_total"], offer["remaining_clinics"]},
                              remaining=offer["remaining_clinics"], branches=True,
                              flagged=flagged)) == OF.OFFER_LIMIT
    assert not flagged, f"the offer's own required pool wording must not self-flag: {flagged!r}"


def test_a_real_exhaustive_claim_still_flags_next_to_the_word_vormerken(hundred):
    """The "vormerken" exclusion is scoped to the sentence that carries it, not the whole reply -- a
    real exhaustive claim in a DIFFERENT sentence of the same reply still flags, so the fix for F3
    does not blind the detector to a genuine claim."""
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]]
    body = ("Es gibt nur diese 5 Kliniken in Bayern: " + ", ".join(named) +
            f". Es gibt {offer['remaining_clinics']} weitere. "
            "Wollen Sie eingrenzen, oder darf ich Sie gleich für alle dort passenden Stellen vormerken?")
    flagged = []
    assert len(GR.check_reply([body], set(named),
                              counts={offer["clinics_total"], offer["remaining_clinics"]},
                              remaining=offer["remaining_clinics"], branches=True,
                              flagged=flagged)) == OF.OFFER_LIMIT
    assert flagged, "a genuine exhaustive claim next to the pool sentence must still be caught"


def test_an_invented_figure_is_rejected_even_next_to_a_true_one(hundred):
    """COUNT was satisfied by the PRESENCE of one true number anywhere, so "über 4000 offene
    Pflegestellen, davon 95 weitere hier" passed on the 95."""
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]]
    body = ("Passend sind: " + ", ".join(named) + ". In ganz Bayern habe ich über 4000 offene "
            f"Pflegestellen, davon {offer['remaining_clinics']} weitere hier.")
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply([body], set(named), counts={offer["clinics_total"], offer["remaining_clinics"]},
                       remaining=offer["remaining_clinics"])


def test_a_german_thousands_dot_is_not_read_as_a_full_stop(small):
    """Defect found live 2026-09-22: "bayernweit über 2.300 Stellen" (true count 2462) was rejected
    for stating "300" -- the old digits-only pattern broke at the dot. "2.300" now reads as 2300, and
    "über 2.300" against a true 2462 is honest German, not invention."""
    assert GR.check_reply(["Bayernweit habe ich über 2.300 Stellen im Angebot."], set(),
                          counts={2462}) == []


def test_a_bare_exact_figure_still_has_to_match_evidence_exactly(small):
    """No marker, no slack -- today's behaviour is unchanged (Ivan's rule (b), point 3)."""
    assert GR.check_reply(["Ich habe 2462 Stellen im Angebot."], set(), counts={2462}) == []
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply(["Ich habe 2300 Stellen im Angebot."], set(), counts={2462})


def test_ueber_n_holds_whenever_a_true_number_is_at_least_n(small):
    """"über N" is a floor, not a point estimate: true as long as some evidence number is >= N."""
    assert GR.check_reply(["Ich habe über 2.300 Stellen im Angebot."], set(), counts={2462}) == []
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply(["Ich habe über 3.000 Stellen im Angebot."], set(), counts={2462})


def test_rund_n_holds_within_the_figures_own_rounding_tolerance(small):
    """Pinned decision: "rund 2.500" against a true 2462 is accepted -- 2.500 carries two trailing
    zeros, so it was rounded to the nearest hundred, and 2462 sits within 50 of it (the mathematical
    definition of "rounded to the nearest hundred"). "rund 2.500" against a true 2000 is not, because
    2000 sits 500 away, well outside that same tolerance -- the marker cannot launder a figure with no
    real number near it."""
    assert GR.check_reply(["Ich habe rund 2.500 Stellen im Angebot."], set(), counts={2462}) == []
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply(["Ich habe rund 2.500 Stellen im Angebot."], set(), counts={2000})


def test_knapp_and_fast_hold_only_just_under_a_true_number(small):
    """"knapp/fast N" promises the truth is a little SHORT of N, not merely nearby: a true 2462 makes
    "knapp 2.500" honest (38 short, inside the rounding tolerance) but not "knapp 2.400" (2462 is
    already past 2400, not under it)."""
    assert GR.check_reply(["Ich habe knapp 2.500 Stellen im Angebot."], set(), counts={2462}) == []
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply(["Ich habe knapp 2.400 Stellen im Angebot."], set(), counts={2462})


def test_a_german_decimal_comma_is_never_read_as_a_thousands_group(small):
    """"2,5" is 2, never 25 -- a comma introduces a decimal in German and must not be folded away like
    the thousands dot is."""
    assert GR.check_reply(["Ich habe 2,5 Stellen im Angebot."], set(), counts={2}) == []
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply(["Ich habe 2,5 Stellen im Angebot."], set(), counts={25})


def test_an_approximation_cannot_launder_a_figure_with_no_evidence_near_it(small):
    """A marker changes what counts as supported; it does not turn off the check. None of these has
    a true number anywhere near it."""
    for bubble in ("Ich habe rund 9.000 Stellen im Angebot.",
                   "Ich habe etwa 50 Stellen im Angebot.",
                   "Ich habe ca. 1.000 Stellen im Angebot.",
                   "Ich habe fast 100 Stellen im Angebot."):
        with pytest.raises(AssertionError, match="COUNT"):
            GR.check_reply([bubble], set(), counts={2462})


def test_the_count_rejection_shows_the_parsed_value_and_the_text_it_came_from(small):
    """So the next person debugging this does not have to guess how "2.300" became "300" (the bug
    found live 2026-09-22)."""
    with pytest.raises(AssertionError, match=r"300 \(read off '300' in '300 Stellen'\)"):
        GR.check_reply(["Bayernweit habe ich 300 Stellen im Angebot."], set(), counts={2462})


# --- ROUND 2 (2026-09-22): TASK-151's fix was verified only by hand-injecting ``counts``, never
# against a real turn. An Opus reviewer ran it live and found (1) the board-wide total
# market_snapshot already computes never reached the rule outside an offer turn, and (2) an
# approximation marker could anchor on OF.OFFER_LIMIT -- a constant, not a market fact -- and pass
# with zero real evidence. TASK-152.

def test_the_board_wide_total_reaches_the_count_rule_before_any_offer_exists(small):
    """Live bug, 2026-09-22: market_snapshot.open_jobs is computed every turn (it is "the one
    aggregate number always present -- safe for a first-turn greeting", per its own docstring), but
    used to enter ``counts`` only inside ``if offer:`` -- so a Bavaria-wide question asked before any
    funnel gate was settled had its true answer rejected as unsupported on 2 of 7 live runs. No tool
    call and no offer here either; turn_evidence still has to carry the number."""
    snap = LB.market_snapshot({})
    assert snap["offer"] is None, "mid-funnel: nothing settled yet"
    evidence = GR.turn_evidence(snap, [])
    assert snap["open_jobs"] in evidence["counts"]
    bubble = f"Bayernweit habe ich aktuell {snap['open_jobs']} offene Stellen im Angebot."
    assert GR.check_reply([bubble], evidence["names"], counts=evidence["counts"]) == []


def test_a_fabricated_board_wide_total_is_still_rejected(small):
    """Threading the true total through must not turn COUNT into a rubber stamp: a figure nowhere
    near it is still an invention."""
    snap = LB.market_snapshot({})
    evidence = GR.turn_evidence(snap, [])
    fake = snap["open_jobs"] + 5000
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply([f"Bayernweit habe ich {fake} offene Stellen im Angebot."], evidence["names"],
                       counts=evidence["counts"])


def test_the_audit_counterexample_still_reaches_the_candidate_through_the_full_pipeline(hundred):
    """The board-wide total now always rides in ``counts`` too -- confirm that addition does not
    change how "nur diese 5 Kliniken" is handled (audit D, true count 224 originally; 100 here): the
    exhaustive-claim check flags it (ROUND 5), it does not key ``remaining`` into a rejection any
    more. The remainder is disclosed in the same bubble so the still-BLOCKING remainder-disclosure
    rule does not fire and mask what this test is isolating."""
    snap = LB.market_snapshot(READY)
    evidence = GR.turn_evidence(snap, [])
    named = [p["clinic"] for p in snap["offer"]["positions"]]
    body = ("Es gibt nur diese 5 Kliniken in Bayern: " + ", ".join(named) +
            f". Es gibt {evidence['remaining']} weitere.")
    flagged = []
    assert len(GR.check_reply([body], evidence["names"], counts=evidence["counts"],
                              remaining=evidence["remaining"], flagged=flagged)) == len(named)
    assert flagged


def test_the_display_cap_cannot_anchor_an_approximation(small):
    """OF.OFFER_LIMIT (5, our per-message display cap) is STRUCTURAL -- a fact about the message, not
    the market. With zero real evidence, "rund 10"/"knapp 10"/"gut 5" must not pass just because the
    cap happens to sit inside their tolerance (live defect, 2026-09-22: exactly this laundered an
    invented figure past the rule on the same turn the true Bavaria-wide total was rejected)."""
    for bubble in ("In Bayern habe ich rund 10 offene Stellen im Angebot.",
                   "In Bayern habe ich knapp 10 offene Stellen im Angebot.",
                   "In Bayern habe ich gut 5 offene Stellen im Angebot."):
        with pytest.raises(AssertionError, match="COUNT"):
            GR.check_reply([bubble], set(), counts=set())


def test_the_display_cap_still_passes_as_a_bare_exact_statement_about_what_is_shown(small):
    """The structural/evidence split only closes the MARKER door: a bare, unapproximated claim about
    how many positions THIS message actually lists is still true of the message, and OF.OFFER_LIMIT
    (or the bubble's own position count) must keep satisfying it exactly."""
    board = sorted(GR.board_clinic_names())
    assert len(board) == 5 == OF.OFFER_LIMIT
    bubble = "Diese 5 Stellen habe ich für Sie: " + ", ".join(board) + "."
    assert GR.check_reply([bubble], set(board), counts=set()) == board


@pytest.mark.parametrize("bubble", [
    "Beim Klinikum Coburg-West ist gerade alles besetzt. Soll ich in der Nähe suchen?",
    "Die Stelle beim Klinikum Coburg-West ist inzwischen weg.",
    "Das Klinikum Coburg-West habe ich leider nicht im Bestand.",
])
def test_a_house_the_candidate_named_may_be_answered_in_any_honest_wording(small, bubble):
    """A deniable name was let through only when nicht/kein/nirgend appeared in the same sentence.
    German says "we have nothing there" in many other ways, and those replies became the holding
    message plus a colleague -- the audit-C failure narrowed to a token list."""
    evidence = GR.turn_evidence(LB.market_snapshot({}), [], inbound="Was ist mit dem Klinikum Coburg-West?")
    assert GR.check_reply([bubble], evidence["names"], deniable=evidence["deniable"]) == []


def test_the_same_house_still_may_not_be_offered_as_hiring(small):
    """The other half: the burden moved to the sentence that OFFERS a position, and that sentence is
    still rule 2."""
    evidence = GR.turn_evidence(LB.market_snapshot({}), [], inbound="Was ist mit dem Klinikum Coburg-West?")
    with pytest.raises(AssertionError, match="NO INVENTION"):
        GR.check_reply(["Das Klinikum Coburg-West sucht gerade Pflegefachkräfte."],
                       evidence["names"], deniable=evidence["deniable"])


def test_a_follow_up_about_houses_already_offered_is_not_an_offer_turn(hundred):
    """COUNT and BRANCHES keyed on any two detected names, so a factual follow-up about two houses
    the candidate had already been shown had to recite a total and both branch wordings or be
    rejected -- twice in a row that is the holding message and a colleague."""
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]][:2]
    body = (f"{named[0]} liegt im Süden, {named[1]} ist etwas kleiner. Welche möchten Sie sich "
            "näher ansehen?")
    assert GR.check_reply([body], set(named), counts={100, 95}, remaining=95, branches=True,
                          shown_before=named) == named


@pytest.mark.parametrize("bubble", [
    "Schauen Sie hier: karriere.klinikum-augsburg.bayern",
    "Schauen Sie hier: klinikum-muenchen.jobs",
    "Kurzlink: bit.ly/3xYz9",
    "Mehr unter t.me/pflegeboard",
    "Schauen Sie hier: www(.)pflege-job-radar(.)de",
    "Schauen Sie hier: pflege-job-radar (Punkt) de",
])
def test_a_link_is_a_link_on_any_tld_and_through_the_obvious_obfuscations(small, bubble):
    """The backstop knew seven TLDs. The live board's own ad URLs sit on .pro, .med and .bayern, and
    German clinic career domains on .jobs and .health."""
    with pytest.raises(AssertionError, match="LINK"):
        GR.check_reply([bubble], set())


@pytest.mark.parametrize("bubble", [
    "Schicken Sie mir Ihren Lebenslauf bitte als lebenslauf.pdf.",
    "Das Foto heißt urkunde.jpg.",
])
def test_a_file_name_is_not_a_link(small, bubble):
    """The funnel asks for a PDF in almost every thread; reading "lebenslauf.pdf" as a host would
    kill the message that asks for it."""
    assert GR.check_reply([bubble], set()) == []


def test_a_clinic_whose_board_name_is_a_hostname_is_still_sayable(tmp_path, monkeypatch):
    """Three live board clinics ARE hostnames ("jobs.sana.de", "karriereportal.kirinus.de",
    "vitrea-gesundheit.de", 7 live postings between them). Running the link test over the raw text
    rejected a truthful, fully grounded reply and handed the candidate the holding message."""
    _board([_job(1, city="München", clinic="jobs.sana.de")], monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    assert GR.check_reply(["In München sucht jobs.sana.de gerade Pflegefachkräfte."],
                          {"jobs.sana.de"}) == ["jobs.sana.de"]
    with pytest.raises(AssertionError, match="LINK"):
        GR.check_reply(["In München sucht jobs.sana.de, siehe klinikum-x.de/jobs."], {"jobs.sana.de"})


def test_a_posting_that_is_gone_is_stale_even_while_the_house_keeps_other_openings(tmp_path, monkeypatch):
    """The memory held clinic NAMES, so the re-check asked "does this house still have anything?"
    -- and a house with 32 other openings answered yes. Ivan's rule (a) is about the opening."""
    jobs = [{**_job(i, city="Coburg", clinic="Klinikum Bamberg"),
             "department_hint": "Onkologie" if i == 1 else "OP"} for i in (1, 2, 3)]
    _board(jobs, monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")

    def names_the_posting(system, user, session_id):
        TS.search_postings(city="Coburg", department="Onkologie")
        return _out(bubbles=["Beim Klinikum Bamberg ist eine Stelle in Onkologie frei."]), session_id

    thread = {"phone": "+4915550001234", "slots": {"region": "Bayern"}, "asked": []}
    first = LB.turn("was gibt es in Coburg?", thread, client=fake_client(names_the_posting))
    assert first["slots"][LB.GROUNDED_KEY] == ["Klinikum Bamberg"]
    assert first["slots"][LB.GROUNDED_POSTINGS_KEY] == [1]

    # The verifier removes that one posting; the house keeps the other two.
    _board([j for j in jobs if j["posting_id"] != 1], monkeypatch)
    d = LB.turn("ist die Onkologie-Stelle noch frei?",
                {"phone": "+4915550001234", "slots": dict(first["slots"]), "asked": []},
                client=fake_client(_out(bubbles=["Ja, die Stelle in Onkologie beim Klinikum Bamberg "
                                                 "ist noch frei."])))
    assert d["bubbles"] == [P.BLOCKED_REPLY_DE] and "STALE" in d["slots"]["_escalate_reason"]


def test_a_fresh_look_up_makes_the_same_house_confirmable_again(tmp_path, monkeypatch):
    """The other half, and the one that keeps the thread alive: once the turn has looked the house
    up again, saying a position there is open is evidence from THIS turn, not memory."""
    jobs = [_job(i, city="Coburg", clinic="Klinikum Bamberg") for i in (1, 2, 3)]
    _board(jobs, monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    card = {"region": "Bayern", LB.GROUNDED_KEY: ["Klinikum Bamberg"], LB.GROUNDED_POSTINGS_KEY: [99]}

    def looks_again(system, user, session_id):
        TS.search_postings(city="Coburg")
        return _out(bubbles=["Beim Klinikum Bamberg ist noch eine Stelle frei."]), session_id

    d = LB.turn("und jetzt?", {"phone": "+4915550001234", "slots": card, "asked": []},
                client=fake_client(looks_again))
    assert d["bubbles"] == ["Beim Klinikum Bamberg ist noch eine Stelle frei."]


# --- ROUND 3 (2026-09-22): 14 more live turns, Opus reviewer. Every figure actually SENT in those
# runs was board-supported -- no invention went out. The failure was one-directional: the guard
# silenced three TRUE shapes, plus an open question round 2 left unanswered. See grounding.py's
# module docstring for the four write-ups; these tests pin each one down.

# 1. count_postings -- the tool whose own docstring says to call it for "wie viele Stellen haben Sie
#    in X" -- hit replay()'s "else: continue" and contributed nothing; no other tool contributes a
#    filtered CLINIC count at all. Live rejection: 18 postings in 4 clinics, the true Augsburg
#    Intensiv/IMC figure looked up that same turn.

def test_count_postings_own_numbers_reach_the_count_rule(small):
    """The mechanism: count_postings' scalar result now lands in turn_evidence's ``counts``."""
    offset = GR.log_offset()
    result = TS.count_postings(city="Augsburg")
    evidence = GR.turn_evidence(LB.market_snapshot({}), GR.calls_since(offset))
    assert result["postings"] == result["clinics"] == 1
    assert {result["postings"], result["clinics"]} <= evidence["counts"]
    bubble = "In Augsburg habe ich aktuell 1 offene Stelle bei 1 Klinik für Sie."
    assert GR.check_reply([bubble], evidence["names"], counts=evidence["counts"]) == []


def test_a_filtered_clinic_total_has_no_source_but_count_postings(tmp_path, monkeypatch):
    """search_postings only ever contributes POSTING totals -- no distinct clinic count. Three
    postings filed at the SAME Augsburg clinic (clinics=1, postings=3, so the two numbers cannot
    coincidentally match) makes the true "1 Klinik" unsayable off search_postings alone, and sayable
    the moment count_postings has actually been called."""
    jobs = [{**_job(i, city="Augsburg", clinic="Klinikum Augsburg Mitte"), "clinic_id": "c-augsburg-mitte"}
            for i in (1, 2, 3)]
    jobs += [_job(i, city="München", housing=False) for i in (4, 5)]   # a second city, so the
    _board(jobs, monkeypatch)                                         # board-wide clinic count (3) is
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")     # not itself the Augsburg answer
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")

    offset = GR.log_offset()
    TS.search_postings(city="Augsburg")
    evidence = GR.turn_evidence(LB.market_snapshot({}), GR.calls_since(offset))
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply(["In Augsburg arbeite ich aktuell mit 1 Klinik zusammen."],
                       evidence["names"], counts=evidence["counts"])

    offset2 = GR.log_offset()
    result = TS.count_postings(city="Augsburg")
    assert (result["postings"], result["clinics"]) == (3, 1)
    evidence2 = GR.turn_evidence(LB.market_snapshot({}), GR.calls_since(offset2))
    assert GR.check_reply(["In Augsburg arbeite ich aktuell mit 1 Klinik zusammen."],
                          evidence2["names"], counts=evidence2["counts"]) == []


def test_a_refused_count_postings_call_contributes_nothing(small):
    """count_postings refuses an unfiltered call; the refusal is logged too (marked, not silent), and
    the replay must fail on it the same way the live tool did -- never hand the model the whole live
    board as evidence for a number tools_server itself never returned (ONLY A CALL THAT SUCCEEDED
    CONTRIBUTES, audit B)."""
    offset = GR.log_offset()
    with pytest.raises(TS.ToolError, match="at least one filter"):
        TS.count_postings()
    assert GR.replay(GR.calls_since(offset)) == []


# 2. mention_spans/_shaped read "278 Kliniken" as a NAME, because "Kliniken" is a board head word and
#    an unrelated offering verb elsewhere in the sentence satisfied the "offers a position" half of
#    the detector regardless of what the span itself said -- so NO INVENTION fired on a count before
#    COUNT was ever consulted, twice in a row (runs 7/8), for the honest answer to "mit wie vielen
#    Kliniken arbeiten Sie".

def test_a_bare_count_of_clinics_is_not_read_as_a_clinic_name(small):
    board = sorted(GR.board_clinic_names())
    bubble = f"Wir bieten Ihnen aktuell {len(board)} Kliniken auf dem Board an."
    assert GR.mention_spans(bubble, board=board) == []
    assert GR.check_reply([bubble], set(), board=board, counts={len(board)}) == []


def test_an_approximate_bare_count_is_not_read_as_a_clinic_name_either(small):
    """The marker itself ("Über") is a lead word too, so the NUMBER-THEN-HEAD shape still applies
    once it is stripped."""
    bubble = "Über 278 Kliniken sind aktuell bei uns gelistet, das bieten wir Ihnen gerne an."
    assert GR.mention_spans(bubble) == []


def test_a_real_clinic_is_still_named_even_with_a_digit_before_the_head_word(small):
    """The fix reads the NUMBER-THEN-HEAD SHAPE, not the word "Kliniken" itself -- a real board
    clinic whose own name happens to start with a digit is still found by the exact-match detector
    (detector 1), which the fix never touches."""
    board = ["2 Kliniken Verbund München"]
    spans = GR.mention_spans("Das ist beim 2 Kliniken Verbund München gemeldet.", board=board)
    assert [s["text"] for s in spans] == ["2 Kliniken Verbund München"]


def test_a_bare_count_bubble_still_counts_its_own_listed_positions_for_volume(small):
    """The fix is about NAME detection only -- "278 Kliniken" costs nothing as a NAMED house, but a
    numbered list of positions right next to it is still counted the way VOLUME always has been
    (regression guard: the two rules must stay independent)."""
    board = sorted(GR.board_clinic_names())
    ten = (f"Wir haben {len(board)} Kliniken im Bestand. Davon zehn Stellen für Sie: 1) OP Vollzeit; "
          "2) OP Teilzeit; 3) Intensiv; 4) Innere; 5) Notaufnahme; 6) Anästhesie; 7) Stroke Unit; "
          "8) Dialyse; 9) Palliativ; 10) Geriatrie.")
    with pytest.raises(AssertionError, match="VOLUME"):
        GR.check_reply([ten], set(), board=board, counts={len(board)})


# 3. The exhaustive-claim rule's "nur diese/die" alternative required no object at all, so it fired
#    on "nur diese Klinik [in Straubing]" -- true, singular, narrow German -- exactly as it would on
#    "nur diese 5 Kliniken", killing a true reply and its corrective rewrite both (run 11).

def test_a_true_singular_clinic_statement_does_not_trip_the_exhaustive_claim_rule(hundred):
    """Round-3 audit (Opus reviewer, run 11, 2026-09-22): "nur diese Klinik in Straubing" is a true,
    NARROW statement about one house that answered, not a claim that this is everything Bavaria has.
    Detection precision still matters after ROUND 5 (fewer threads flagged for no reason), even
    though this shape was never blocking -- confirmed via ``flagged`` staying empty."""
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]]
    body = ("Nur diese Klinik in Straubing hat mir eben eine Rückmeldung geschickt. Passend sind: "
            + ", ".join(named) + f". Es gibt {offer['remaining_clinics']} weitere. "
            "Wollen Sie eingrenzen, oder soll ich Sie allen passenden Kliniken vorschlagen?")
    flagged = []
    assert len(GR.check_reply([body], set(named),
                              counts={offer["clinics_total"], offer["remaining_clinics"]},
                              remaining=offer["remaining_clinics"], branches=True,
                              flagged=flagged)) == OF.OFFER_LIMIT
    assert flagged == [], "a true, narrow singular statement must not be flagged either"


def test_the_plural_exhaustive_claim_still_flags_next_to_the_same_singular_wording(hundred):
    """The fix narrows one alternative; it must not blunt the detection it was written for -- and
    (ROUND 5) detection is all this rule does now: the reply still reaches the candidate, the
    sentence is recorded in ``flagged``. The remainder is disclosed so the still-BLOCKING
    remainder-disclosure rule does not fire and mask what this test is isolating."""
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]]
    body = ("Nur diese Klinik in Straubing hat geantwortet. Es gibt nur diese 5 Kliniken in Bayern: "
            + ", ".join(named) + f". Es gibt {offer['remaining_clinics']} weitere.")
    flagged = []
    assert len(GR.check_reply([body], set(named), counts={offer["clinics_total"], offer["remaining_clinics"]},
                              remaining=offer["remaining_clinics"], flagged=flagged)) == OF.OFFER_LIMIT
    assert flagged


# 4. THE OPEN QUESTION ROUND 2 LEFT UNTOUCHED: "über N" is a floor with no ceiling, and a large
#    board-wide number is in ``counts`` on almost every turn now (round 2) -- so "über 40 Kliniken"
#    about München specifically was accepted on the true, unrelated Bavaria-wide total. DECISION: an
#    approximation marker anchors only on evidence about the SAME SUBJECT (see grounding.py's
#    _false_counts docstring for the reasoning).

def _munich_and_augsburg_board(monkeypatch, tmp_path):
    jobs = ([_job(i, city="München", housing=False) for i in range(1, 31)]
           + [_job(i, city="Augsburg", housing=False) for i in range(31, 36)])
    _board(jobs, monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")


def test_an_approximation_about_a_named_city_cannot_anchor_on_the_board_wide_total(tmp_path, monkeypatch):
    """Live defect (Opus reviewer, 2026-09-22): "über 40 Kliniken" about München was accepted because
    the true board-wide 278 (here: 35) satisfied the floor -- with zero München-specific evidence."""
    _munich_and_augsburg_board(monkeypatch, tmp_path)
    evidence = GR.turn_evidence(LB.market_snapshot({}), [])
    assert 35 in evidence["counts"] and evidence["counts_by_city"] == {}
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply(["München hat aktuell über 40 Kliniken bei uns gelistet."], evidence["names"],
                       counts=evidence["counts"], counts_by_city=evidence["counts_by_city"])


def test_an_approximation_about_a_named_city_may_anchor_on_that_city_s_own_evidence(tmp_path, monkeypatch):
    """Once the model has actually looked München up THIS turn, an honest approximation about it may
    rest on that number -- and a figure the same-scoped evidence does not support is still rejected."""
    _munich_and_augsburg_board(monkeypatch, tmp_path)
    offset = GR.log_offset()
    TS.count_postings(city="München")
    evidence = GR.turn_evidence(LB.market_snapshot({}), GR.calls_since(offset))
    assert evidence["counts_by_city"]["munchen"] == {0, 1, 30}

    assert GR.check_reply(["München hat aktuell über 25 Kliniken bei uns gelistet."], evidence["names"],
                          counts=evidence["counts"], counts_by_city=evidence["counts_by_city"]) == []
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply(["München hat aktuell über 40 Kliniken bei uns gelistet."], evidence["names"],
                       counts=evidence["counts"], counts_by_city=evidence["counts_by_city"])


def test_an_approximation_naming_no_city_still_anchors_on_the_unscoped_total(tmp_path, monkeypatch):
    """The scoping is additional, not a narrowing of what already worked: a bayernweit approximation
    that names no specific city keeps drawing on the board-wide figure, exactly as round 2 left it."""
    _munich_and_augsburg_board(monkeypatch, tmp_path)
    evidence = GR.turn_evidence(LB.market_snapshot({}), [])
    assert GR.check_reply(["Bayernweit habe ich über 30 Kliniken im Angebot."], evidence["names"],
                          counts=evidence["counts"], counts_by_city=evidence["counts_by_city"]) == []


def test_an_approximation_about_a_different_city_cannot_borrow_another_city_s_evidence(tmp_path, monkeypatch):
    """München's own 30 must not answer for Augsburg either -- the anchor is the SAME subject, not
    merely "some" subject."""
    _munich_and_augsburg_board(monkeypatch, tmp_path)
    offset = GR.log_offset()
    TS.count_postings(city="München")
    evidence = GR.turn_evidence(LB.market_snapshot({}), GR.calls_since(offset))
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply(["Augsburg hat aktuell über 20 Kliniken bei uns gelistet."], evidence["names"],
                       counts=evidence["counts"], counts_by_city=evidence["counts_by_city"])


# --- ROUND 4 (2026-09-22): reviewer run 15, the same true reply rejected twice in the same turn (the
# reply and its corrective rewrite). "In Straubing gibt es aktuell 6 offene Pflegestellen, alle beim
# Klinikum St. Elisabeth der Barmherzigen Brueder ([Straubing's own four departments])." is TRUE --
# reproduced against the real board 2026-09-22 (2469 postings, 278 clinics that day; Straubing itself
# has exactly 6 postings, all at that one house) -- and fully discloses its own true total, but the
# old rule read the noun AFTER "alle" ("Klinikum") as an exhaustive CLINICS claim and checked it
# against ``remaining``, a scalar this same search_postings call had already set to 1 for an unrelated
# reason: the LISTING_LIMIT cutoff (5 of the 6 postings shown). A POSTINGS listing's own display cap,
# read as "one more clinic exists" -- unlike quantities. Round 4 fixed this by scoping "alle
# bei/beim/im/in KLINIK" to (city, subject) evidence -- and ROUND 5 (below) deleted that machinery
# again, because the SAME live session then hit the SAME bug through one more preposition ("alle AM
# Klinikum") round 4 never enumerated. See grounding.py's module docstring for the full history.

def _straubing_board(monkeypatch, tmp_path):
    """The real board's own Straubing shape, measured 2026-09-22: 6 postings, 1 clinic, 1 city."""
    depts = ["Intensiv/IMC", "Anästhesie", "OP", "Springerpool", "Onkologie", "Intensiv/IMC"]
    jobs = [_job(i, city="Straubing", clinic="Klinikum St. Elisabeth", department=d)
            for i, d in enumerate(depts, 1)]
    _board(jobs, monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")


# --- ROUND 5 (2026-09-22, Ivan's decision): the exhaustive-claim check stops blocking; grounding.py's
# module docstring has the full argument, and TASK-154 the plan. Round 4's scoped (city, subject)
# evidence and the preposition split it needed are deleted along with the blocking decision they
# served -- what is pinned below is that detection is unaffected (both the direct "alle Kliniken" and
# the distribution "alle bei/beim/im/in KLINIK" shape are still caught, merged back into one regex),
# the reply now reaches the candidate either way, and the four other guards are untouched.

def test_the_straubing_sentence_reaches_the_candidate_and_leaves_a_flag(tmp_path, monkeypatch):
    """The sentence that died three more times live even after round 4's fix (module docstring):
    "In Straubing gibt es aktuell 6 offene Stellen, alle am Klinikum St. Elisabeth (Barmherzige
    Brueder) - u. a. Intensiv/IMC, Anaesthesie/ATA, OP und Springerpool" is TRUE (Straubing genuinely
    has 6 postings, all at that one house) and fully discloses its own total. "alle AM Klinikum" uses
    a preposition round 4 never enumerated, so it fell through to the DIRECT path and was checked
    against the unscoped ``remaining`` -- rejected, again. ROUND 5 stops checking the claim's truth at
    all: the reply reaches the candidate, and the sentence lands in ``flagged`` for a human."""
    _straubing_board(monkeypatch, tmp_path)
    offset = GR.log_offset()
    TS.search_postings(city="Straubing")
    evidence = GR.turn_evidence(LB.market_snapshot({}), GR.calls_since(offset))
    # The scalar the old, deleted rule used is exactly the LISTING_LIMIT cutoff, not a second clinic.
    assert evidence["remaining"] == 1

    body = ("In Straubing gibt es aktuell 6 offene Stellen, alle am Klinikum St. Elisabeth "
            "(Barmherzige Brueder) - u. a. Intensiv/IMC, Anaesthesie/ATA, OP und Springerpool.")
    flagged = []
    assert GR.check_reply([body], evidence["names"], counts=evidence["counts"],
                          counts_by_city=evidence["counts_by_city"], remaining=evidence["remaining"],
                          flagged=flagged) == ["Klinikum St. Elisabeth"]
    assert flagged, "the shape is still detected -- it just no longer blocks the reply"


def test_nur_diese_5_kliniken_in_bayern_against_a_true_278_reaches_the_candidate_but_is_flagged(
        tmp_path, monkeypatch):
    """The audit-D counterexample the rule was built for (module docstring, rule 3): "Es gibt nur
    diese 5 Kliniken in Bayern" against a true 278 is no longer rejected -- it is NOT BLOCKED any
    more, ROUND 5 took this check off the blocking path entirely. It is still DETECTED (``flagged``)
    so a human sees it; the candidate gets the reply. The remainder is disclosed in the same bubble
    so the still-BLOCKING remainder-disclosure rule does not fire and mask what this isolates."""
    _board([_job(i, city="München") for i in range(1, 279)], monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    offer = LB.market_snapshot(READY)["offer"]
    assert offer["clinics_total"] == 278
    named = [p["clinic"] for p in offer["positions"]]
    body = ("Es gibt nur diese 5 Kliniken in Bayern: " + ", ".join(named) +
            f". Es gibt {offer['remaining_clinics']} weitere.")
    flagged = []
    assert len(GR.check_reply([body], set(named), counts={offer["clinics_total"], offer["remaining_clinics"]},
                              remaining=offer["remaining_clinics"], flagged=flagged)) == OF.OFFER_LIMIT
    assert flagged


def test_alle_bei_x_is_flagged_not_blocked_when_a_second_clinic_genuinely_matched(tmp_path, monkeypatch):
    """Detection stays useful signal even for a genuinely FALSE distribution claim (grounding.py's
    module docstring: "detection stays"). Regensburg has TWO clinics with open postings, six between
    them -- LISTING_LIMIT (5) cuts the search_postings reply short, so ``remaining`` (the flag's own
    trigger heuristic) is genuinely > 0 here, same as a real turn. A reply naming only Klinikum A and
    calling that "alle" is false -- round 4 would have blocked this on scoped evidence. ROUND 5 flags
    it instead: the reply still reaches the candidate, but a human sees the same false claim the
    deleted rule used to catch."""
    jobs = ([{**_job(i, city="Regensburg", clinic="Klinikum A"), "clinic_id": "c-regensburg-a"}
             for i in range(1, 6)]
           + [{**_job(6, city="Regensburg", clinic="Klinikum B"), "clinic_id": "c-regensburg-b"}])
    _board(jobs, monkeypatch)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")

    offset = GR.log_offset()
    TS.search_postings(city="Regensburg")
    evidence = GR.turn_evidence(LB.market_snapshot({}), GR.calls_since(offset))
    assert evidence["remaining"] == 1

    body = "In Regensburg arbeiten wir mit mehreren Kliniken zusammen, alle beim Klinikum A vertreten."
    flagged = []
    assert GR.check_reply([body], evidence["names"], counts=evidence["counts"],
                          counts_by_city=evidence["counts_by_city"], remaining=evidence["remaining"],
                          flagged=flagged) == ["Klinikum A"]
    assert flagged


def test_the_flagged_reply_reaches_the_candidate_and_marks_the_card_for_review(hundred):
    """The app/wa/luna_brain.py wiring (TASK-154): a flagged (not blocked) exhaustive claim still
    reaches the candidate exactly as the model wrote it -- no corrective retry, no holding message --
    and the thread is marked the same way an escalation is recorded today (card._escalated /
    card._escalate_reason), so a human sees the thread and the suspected sentence."""
    thread = {"slots": dict(READY), "asked": []}
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]]
    body = ("Es gibt nur diese 5 Kliniken in Bayern: " + ", ".join(named) +
            f". Es gibt {offer['remaining_clinics']} weitere. "
            "Wollen Sie eingrenzen, oder soll ich Sie allen passenden Kliniken vorschlagen?")
    d = LB.turn("was gibt es?", thread, client=fake_client(_out(bubbles=[body])))
    assert d["bubbles"] == [body], "the reply is sent as written -- flagging never touches the text"
    assert d["action"] != "reply_blocked_escalated"
    assert d["slots"]["_escalated"] is True
    assert "exhaustive-claim" in d["slots"]["_escalate_reason"]


def test_a_flagged_reply_that_is_also_model_escalated_keeps_both_reasons(hundred):
    """TASK-156 (F1): the demoted exhaustive-claim flag and the model's own escalate_to_manager
    describe two different real facts about the SAME turn, and card._escalate_reason is one string
    field (app/wa/api.py's own use for unread media is the same field) -- an unconditional overwrite
    used to drop whichever fact was recorded first. Both must survive."""
    thread = {"slots": dict(READY), "asked": []}
    offer = LB.market_snapshot(READY)["offer"]
    named = [p["clinic"] for p in offer["positions"]]
    body = ("Es gibt nur diese 5 Kliniken in Bayern: " + ", ".join(named) +
            f". Es gibt {offer['remaining_clinics']} weitere. "
            "Wollen Sie eingrenzen, oder soll ich Sie allen passenden Kliniken vorschlagen?")
    out = _out(bubbles=[body], escalate_to_manager=True, escalate_reason="candidate asked about a visa")
    d = LB.turn("was gibt es?", thread, client=fake_client(out))
    assert d["slots"]["_escalated"] is True
    reason = d["slots"]["_escalate_reason"]
    assert "exhaustive-claim" in reason, "the demoted flag's own reason must survive"
    assert "candidate asked about a visa" in reason, "the model's own escalation reason must survive too"


# --- the four guards this round leaves BLOCKING and untouched (TASK-154): each already has broader
# coverage earlier in this file (NO INVENTION/COUNT/VOLUME above, STALE around the Coburg/Bamberg
# tests) -- pinned here once more, narrowly, as this round's own audit record.

def test_an_invented_clinic_name_is_still_blocked_round_5(small):
    """NO INVENTION: untouched by this round."""
    with pytest.raises(AssertionError, match="NO INVENTION"):
        GR.check_reply(["Im Klinikum München 63 ist gerade eine Stelle frei."], set())


def test_an_unsupported_figure_is_still_blocked_round_5(small):
    """COUNT's figure check: untouched by this round."""
    with pytest.raises(AssertionError, match="COUNT"):
        GR.check_reply(["Ich habe 9999 Stellen im Angebot."], set(), counts={2462})


def test_a_sixth_position_is_still_blocked_round_5(hundred):
    """VOLUME, the five-position cap: untouched by this round."""
    named = [p["clinic"] for p in LB.market_snapshot(READY)["offer"]["positions"]]
    allowed = set(named) | {"Klinikum München 6"}
    with pytest.raises(AssertionError, match="VOLUME"):
        GR.check_reply([", ".join(named + ["Klinikum München 6"])], allowed)


def test_a_non_live_posting_never_reaches_the_model_round_5(small, monkeypatch):
    """STALE: untouched by this round -- a posting the verifier marked gone is not evidence it is
    still open; the corrected reply is the harness's own holding message, never sent to the model
    again as if it had been accepted."""
    thread = {"slots": {"region": "Bayern", LB.GROUNDED_KEY: ["Klinikum Augsburg 1"]}, "asked": []}
    _board([j for j in D.jobs() if j["clinic_name"] != "Klinikum Augsburg 1"], monkeypatch)
    d = LB.turn("ist die Stelle noch frei?", thread,
                client=fake_client(_out(bubbles=["Beim Klinikum Augsburg 1 ist die Stelle noch frei."])))
    assert d["bubbles"] == [P.BLOCKED_REPLY_DE]
    assert "STALE" in d["slots"]["_escalate_reason"]
