"""The Claude-driven brain against synthetic personas, through the real `claude` CLI
(WA_BRAIN=luna). Marked ``llm``: excluded from the default run (``-m "not llm"``, same
convention as ``network``/``completeness``/``mutation`` in pytest.ini) because every test here
spawns a real subprocess, costs real money, and takes several seconds per turn.

Every persona below is fully fictional -- invented names, invented specifics -- but the
*patterns* they exercise (qualification-path mix, conversation shape, tone, the specific edge
cases each script triggers) come from a read of two months of the reference implementation's
real WhatsApp history on tasker-dispatcher-01, aggregated and anonymized into archetype groups
before a single line of this file was written. No real name, phone number, or verbatim message
from that history appears here -- see the task history (backlog TASK-60 follow-up notes) for the
full anonymized group report this was built from.

Run explicitly: ``pytest -q -m llm tests/test_wa_luna_personas.py``. Skipped automatically if the
`claude` CLI is not on PATH (nothing here can run without it).
"""
import json
import re
import shutil
import time
from collections import Counter

import pytest

from app import data as D
from app.wa import api as WAPI
from app.wa import config as C
from app.wa import luna_brain as LB
from tests.luna_fixture_tools_server import use_fixture_board

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not shutil.which(C.LUNA_CLAUDE_BIN),
                        reason=f"{C.LUNA_CLAUDE_BIN!r} is not on PATH -- WA_BRAIN=luna needs it installed"),
]

SALARY_RE = re.compile(r"\d[\d.,]*\s?(€|eur\b|euro)", re.I)


_PLAN = [
    ("München", "Oberbayern", "Intensiv/IMC", True), ("München", "Oberbayern", "OP", False),
    ("Augsburg", "Schwaben", "Innere Medizin", True), ("Würzburg", "Unterfranken", "Geburtshilfe", False),
    ("Regensburg", "Oberpfalz", "Notaufnahme", True), ("Bayreuth", "Oberfranken", "Chirurgie/Orthopädie", False),
]


def _clinic_id(city):
    return f"k-{city.lower()}"


def _jobs():
    """A small, varied board: three cities, three departments, both city sizes, some housing --
    enough for the market snapshot to have real examples without needing the full live board."""
    rows = []
    for i, (city, bezirk, dept, housing) in enumerate(_PLAN):
        rows.append({"posting_id": i + 1, "title": f"Pflegefachkraft {dept}", "role_class": "pflegefachkraft",
                     "department_hint": dept, "city": city, "clinic_town": city, "regierungsbezirk": bezirk,
                     "clinic_id": _clinic_id(city), "clinic_name": f"Klinikum {city}", "employer": f"Klinikum {city}",
                     "employment_types": ["vollzeit"], "enr_housing": housing, "verify_status": "live",
                     "status": "open", "first_published": "2026-09-01", "fresh": True,
                     "source_url": f"https://example.org/job/{i + 1}"})
    return rows


def _clinics():
    """One clinic per fixture city, so list_clinics answers from the same board (TASK-96 repair round 2)."""
    open_by_city = Counter((city, bezirk) for city, bezirk, _, _ in _PLAN)
    return [{"clinic_id": _clinic_id(city), "name": f"Klinikum {city}", "town": city, "regierungsbezirk": bezirk,
             "beds": 500, "jobs_open": n, "fachrichtungen": []} for (city, bezirk), n in open_by_city.items()]


@pytest.fixture()
def board(tmp_path, monkeypatch):
    clinics = _clinics()
    D._snap.update({"at": time.time(), "jobs": _jobs(), "clinics": clinics,
                    "by_clinic": {c["clinic_id"]: c for c in clinics}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    use_fixture_board(monkeypatch, tmp_path)


def _run(script, thread=None):
    """Play a persona's script turn by turn through the real CLI, returning the full list of
    per-turn results (each the dict app/wa/luna_brain.py:turn() returns).

    TASK-80: the moment Valentina offers the anonymized send, she attaches real Ja/Nein buttons --
    a real WhatsApp UI renders those as taps, not free text, and consent is only ever recorded from
    an actual tap (typing "Ja" instead gets a "please tap" nudge, never silent consent). Every
    script in this file is written to be cooperative through to consent, so the moment buttons show
    up mid-script, this simulates the "yes" tap a real candidate would make and stops feeding the
    rest of the script's free-text lines (which would otherwise just collect more "please tap"
    nudges) -- callers that want to see a genuine decline still can, by inspecting each turn's own
    ``buttons`` themselves instead of relying on this helper's default."""
    thread = thread or {"slots": {}, "asked": []}
    out = []
    for text in script:
        d = LB.turn(text, thread)
        out.append(d)
        thread = {"slots": d["slots"], "asked": d["asked"]}
        if d["buttons"]:
            yes = next(b for b in d["buttons"] if b["id"] == LB.CONSENT_YES_ID)
            d = LB.turn(yes["title"], thread, button_id=yes["id"])
            out.append(d)
            thread = {"slots": d["slots"], "asked": d["asked"]}
            break
    return out


def _send_document(thread, document_type, text, certificate_level="unknown"):
    """Simulate app/wa/api.py:_ingest_media's effect on the card before the next turn runs
    (TASK-91/TASK-96) -- tests call LB.turn() directly and never go through the webhook's own media-
    download/classification path, so a script that needs 'a document just arrived' (the documents
    gate the CLOSE SEQUENCE requires) sets the same card fields _ingest_media writes: the text key
    chosen by document_type, the latest document_type/certificate_level, and one entry in both
    documents and _documents_just_received."""
    slots = dict(thread.get("slots") or {})
    summary = {"id": len(slots.get("documents", [])) + 1, "document_type": document_type,
               "certificate_level": certificate_level}
    text_key = WAPI._CARD_TEXT_KEY.get(document_type)
    if text_key:
        slots[text_key] = "\n\n".join(filter(None, (slots.get(text_key), text)))
    slots.update(document_type=document_type, certificate_level=certificate_level)
    slots["documents"] = [*slots.get("documents", []), summary]
    slots["_documents_just_received"] = [*slots.get("_documents_just_received", []), summary]
    return {"slots": slots, "asked": thread.get("asked") or []}


def _all_bubbles(results):
    return [b for d in results for b in d["bubbles"]]


# TASK-96 repair round 2: while the MCP tools read no board, Luna's own München search came back empty and she said
# "Für München habe ich aktuell leider keine offene Stelle" two turns before the shortlist named Klinikum München.
# The fixture board has open München postings, so a bubble naming München with such a denial is false.
_NO_OPENING_RE = re.compile(r"\bkeine\s+(?:\w+\s+){0,2}(?:stelle|job|angebot)", re.I)


def _assert_no_munich_opening_denied(bubbles, transcript):
    denials = [b for b in bubbles if "münchen" in b.lower() and _NO_OPENING_RE.search(b)]
    assert denials == [], f"Luna denied the fixture board's open München postings: {denials!r} {transcript!r}"


# --- Group 1: verified Urkunde, smooth self-service close -------------------------------------

def test_maria_verified_urkunde_reaches_a_city_and_department_without_a_reject(board):
    """Fictional persona: Maria, GKP with an already-recognized Urkunde, wants a smaller
    Bavarian town, lives alone. Defining pattern: fast, clean qualify -> match, no escalation."""
    results = _run([
        "Hallo, ich suche einen Pflegejob mit Wohnung.",
        "Ja, ich habe die Urkunde schon, ist anerkannt.",
        "Bayern, am liebsten eine kleinere Stadt.",
        "Innere Medizin wäre gut.",
    ])
    final = results[-1]
    assert final["slots"].get("qualification_ok") is True
    assert final["slots"].get("qualification_path") == "urkunde"
    assert final["slots"].get("_session_id"), "the whole script must run in one resumed session"
    assert not final["slots"].get("_escalated"), "a clean, qualified path should not need a human"
    for d in results:
        assert 1 <= len(d["bubbles"]) <= LB.MAX_BUBBLES


def test_the_close_sequence_states_matches_before_recap_and_consent_together(board):
    """TASK-63, tightened by TASK-90 and TASK-91: once qualification, city, department and
    housing are all settled, the harness must not jump straight to the anonymized-send question in
    the very same turn that first names a clinic -- it states the distinct clinic count and
    shortlist together as one info-only turn, THEN (a later turn) restates the matched criteria and
    asks for consent together. TASK-90 found the original four-turn spread (count, then shortlist,
    then recap, each its own turn) read as broken on a real WhatsApp test -- three turns in a row
    with no question at all, so the candidate had to guess they should send a filler reply to keep
    it moving. TASK-91 additionally requires actual documents (not just a verbal 'ja, ich habe
    die Urkunde'; TASK-96: the CV and the Urkunde) before the close sequence can start at all --
    this script simulates both arriving via _send_document (the persona scripts are pure text; a
    real document download/classification is TASK-67's own separately-tested path). The defining regression this guards is narrower than
    "every step its own turn": a shortlist and the consent ask must never land in the same turn as
    the FIRST clinic mention."""
    results = _run([
        "Hallo, ich habe die Urkunde schon, ist anerkannt.",
        "Bayern, am liebsten München.",
        "Intensivstation wäre ideal.",
        "Ich wohne allein, brauche nur ein Zimmer für mich.",
    ])
    assert results[3]["slots"].get("housing_known"), (
        "the housing turn itself must resolve housing_known before the document ask can start")
    assert LB.market_snapshot(results[3]["slots"])["shortlist"] == [], (
        "no document has arrived yet -- the close sequence must not be reachable")

    thread = _send_document({"slots": results[-1]["slots"], "asked": results[-1]["asked"]},
                            "lebenslauf", "Lebenslauf ... Gesundheits- und Krankenpflegerin, Intensivstation")
    thread = _send_document(thread, "urkunde", "Urkunde ... Gesundheits- und Krankenpflegerin ... volle Anerkennung",
                            certificate_level="fachkraft")
    for d in results:
        _no_close(d, [r["bubbles"] for r in results])
    close_start = len(results)
    results += _run(["Hier sind mein Lebenslauf und meine Urkunde 📄", "Ok", "Alles klar", "Ja", "Passt für mich"],
                    thread)
    transcript = [d["bubbles"] for d in results]
    print(json.dumps(transcript, ensure_ascii=False, indent=1))

    assert results[-1]["slots"].get("anonymous_send_consent") is True, (
        "this script answers every question positively and provides a document -- it must reach "
        f"recorded consent by the end: {transcript!r}")

    board_clinics = {"Klinikum München", "Klinikum Augsburg", "Klinikum Würzburg",
                     "Klinikum Regensburg", "Klinikum Bayreuth"}
    # The invariant that must hold regardless of exact pacing (which turn says what varies run to
    # run, per this harness's own confirmed non-determinism): the FIRST turn that reveals a
    # shortlist clinic must not also be the turn asking for anonymized-send consent -- that would
    # be the "combine steps" regression this test guards. A LATER turn naming the same
    # already-revealed clinic again while asking for consent (e.g. "send your profile to the
    # clinic I just named -- do you agree?") is normal, expected phrasing, not a violation.
    # Repair round 2: the MCP tools now see the fixture board, so a tool answer may name Klinikum München
    # before the documents arrive (live: 'z. B. am Klinikum München' in the first turn); the close starts with
    # the turn after the upload, so its first clinic mention is searched from there.
    first_clinic_turn = next((i for i, d in enumerate(results) if i >= close_start
                              and any(c in " ".join(d["bubbles"]) for c in board_clinics)), None)
    # TASK-96 review: 2/2 live runs failed here because department_pref='Intensivstation' filtered the harness
    # shortlist to nothing (market_snapshot now reads the word in board vocabulary) -- the card shows which.
    card = {k: v for k, v in results[-1]["slots"].items() if not k.endswith("_text")}
    assert first_clinic_turn is not None, (
        f"the close sequence (turns from {close_start}) never named a real board clinic: {transcript!r} "
        f"shortlist={LB.market_snapshot(results[-1]['slots'])['shortlist']!r} card={card!r}")
    first_mention_text = " ".join(results[first_clinic_turn]["bubbles"]).lower()
    assert "anonym" not in first_mention_text, (
        f"the FIRST clinic mention (turn {first_clinic_turn}) already asked for anonymized-send "
        f"consent in the same breath: {results[first_clinic_turn]['bubbles']!r}")
    _assert_no_munich_opening_denied(_all_bubbles(results), transcript)


def test_maria_salary_question_is_deferred_never_quoted(board):
    """Cross-cutting pattern: a salary question before qualification is fully settled must be
    deferred to a human, never answered with an invented number."""
    results = _run([
        "Hallo, ich bin Krankenschwester, Urkunde schon anerkannt.",
        "Wie viel verdient man da ungefähr im Monat?",
    ])
    bubbles = " ".join(_all_bubbles(results))
    assert not SALARY_RE.search(bubbles), f"a euro figure must never be quoted, got: {bubbles!r}"


# --- both documents before the close, the missing one re-asked (TASK-96, Ivan's manual test) -----

_CV_RE = re.compile(r"lebenslauf|\bcv\b", re.I)
_BOARD_CLINICS = ("Klinikum München", "Klinikum Augsburg", "Klinikum Würzburg", "Klinikum Regensburg",
                  "Klinikum Bayreuth")
# Everything but the documents is settled on the card; the opener says the same, so the session has it too.
_SVETLANA_CARD = {"region": "Bayern", "qualification_path": "urkunde", "qualification_ok": True,
                  "role_verdict": "accept", "city": "München", "housing_known": True, "people_count": 1}
# No department wish: a free-text department_pref ("egal") filters the fixture board's shortlist to nothing.
_OPENER_SVETLANA = ("Hallo, ich bin Pflegefachfrau mit deutscher Urkunde, volle Anerkennung. Ich suche eine "
                    "Stelle in München und brauche eine Wohnung nur für mich.")


def _no_close(d, transcript):
    assert d["matches"] == [] and d["buttons"] == [], f"shortlist/consent before both documents: {transcript!r}"
    assert not d["slots"].get("anonymous_send_offered"), f"consent offered before both documents: {transcript!r}"


def test_svetlana_sends_only_her_cv_and_is_asked_for_the_urkunde_until_it_arrives(board):
    """TASK-96 (Ivan's manual test 2026-09-13: a claimed Urkunde plus a sent Lebenslauf unlocked the
    close). Fictional persona: Svetlana, Urkunde, München, lives alone -- everything settled but the
    documents. The first document ask names both the CV and the Urkunde; she sends only the CV (Luna
    thanks and asks for the Urkunde, no shortlist/consent); she writes 'schicke ich später' (Luna still
    names the Urkunde); she sends the Urkunde (the close sequence starts: a shortlist clinic is named).
    A media message reaches LB.turn() with empty text, same as the webhook."""
    d = LB.turn(_OPENER_SVETLANA, {"slots": dict(_SVETLANA_CARD), "asked": []})
    transcript = [("candidate", _OPENER_SVETLANA), ("luna", d["bubbles"])]
    first_ask = " ".join(d["bubbles"])
    assert "urkunde" in first_ask.lower() and _CV_RE.search(first_ask), (
        f"the first document ask must name both the CV and the Urkunde: {transcript!r}")
    assert "und/oder" not in first_ask.lower(), transcript
    _no_close(d, transcript)

    thread = _send_document({"slots": d["slots"], "asked": d["asked"]}, "lebenslauf",
                            "Lebenslauf Svetlana K., Pflegefachfrau, 2015-2025 Intensivstation, München")
    d = LB.turn("", thread)
    transcript += [("candidate", "[Lebenslauf.pdf]"), ("luna", d["bubbles"])]
    after_cv = " ".join(d["bubbles"]).lower()
    assert "urkunde" in after_cv, f"the CV arrived, the Urkunde is still missing and must be named: {transcript!r}"
    assert re.search(r"dank|angekommen|erhalten", after_cv), f"the CV was not acknowledged: {transcript!r}"
    _no_close(d, transcript)

    d = LB.turn("schicke ich später", {"slots": d["slots"], "asked": d["asked"]})
    transcript += [("candidate", "schicke ich später"), ("luna", d["bubbles"])]
    assert "urkunde" in " ".join(d["bubbles"]).lower(), (
        f"'later' must still get the missing Urkunde named: {transcript!r}")
    _no_close(d, transcript)

    thread = _send_document({"slots": d["slots"], "asked": d["asked"]}, "urkunde",
                            "Urkunde ... Pflegefachfrau ... Erlaubnis zum Führen der Berufsbezeichnung",
                            certificate_level="fachkraft")
    d = LB.turn("", thread)
    transcript += [("candidate", "[Urkunde.jpg]"), ("luna", d["bubbles"])]
    print(json.dumps(transcript, ensure_ascii=False, indent=1))
    card = {k: v for k, v in d["slots"].items() if not k.endswith("_text")}
    assert d["matches"], f"both documents are in, the harness shortlist must exist: {transcript!r} {card!r}"
    assert any(c in " ".join(d["bubbles"]) for c in _BOARD_CLINICS), (
        f"the close sequence did not start (no shortlist clinic named): {transcript!r}")
    assert not d["slots"].get("anonymous_send_offered"), f"consent asked in the shortlist turn: {transcript!r}"
    _assert_no_munich_opening_denied(_luna_bubbles(transcript), transcript)


def _luna_bubbles(transcript):
    return [b for who, said in transcript if who == "luna" for b in said]


def test_svetlana_wrong_document_types_get_the_missing_documents_named_again(board):
    """TASK-96 AC3, the wrong-type case (verifier round 2: only an offline prompt-string test and one throwaway
    probe covered it). Same settled card and opener as above. A Dienstplan arrives first (neither document: both
    named again), then the CV (the Urkunde named), then a home-country nursing diploma (not the German Urkunde:
    the German Urkunde named again). No shortlist or consent at any step."""
    d = LB.turn(_OPENER_SVETLANA, {"slots": dict(_SVETLANA_CARD), "asked": []})
    transcript = [("candidate", _OPENER_SVETLANA), ("luna", d["bubbles"])]
    _no_close(d, transcript)

    thread = _send_document({"slots": d["slots"], "asked": d["asked"]}, "dienstplan",
                            "Dienstplan Station 4B, KW 36: Mo Frühdienst, Di Spätdienst, Mi frei, Do Nachtdienst")
    d = LB.turn("", thread)
    transcript += [("candidate", "[Dienstplan.jpg]"), ("luna", d["bubbles"])]
    after_roster = " ".join(d["bubbles"])
    assert "urkunde" in after_roster.lower() and _CV_RE.search(after_roster), (
        f"a Dienstplan is neither document, both must be named again: {transcript!r}")
    _no_close(d, transcript)

    thread = _send_document({"slots": d["slots"], "asked": d["asked"]}, "lebenslauf",
                            "Lebenslauf Svetlana K., Pflegefachfrau, 2015-2025 Intensivstation, München")
    d = LB.turn("", thread)
    transcript += [("candidate", "[Lebenslauf.pdf]"), ("luna", d["bubbles"])]
    assert "urkunde" in " ".join(d["bubbles"]).lower(), (
        f"the CV arrived, the Urkunde is still missing and must be named: {transcript!r}")
    _no_close(d, transcript)

    thread = _send_document({"slots": d["slots"], "asked": d["asked"]}, "auslaendisches_diplom",
                            "Diplom ... Medizinisches College (fiktiv) ... Qualifikation: Krankenschwester ... 2014")
    d = LB.turn("", thread)
    transcript += [("candidate", "[Diplom.jpg]"), ("luna", d["bubbles"])]
    print(json.dumps(transcript, ensure_ascii=False, indent=1))
    after_diploma = " ".join(d["bubbles"]).lower()
    assert "urkunde" in after_diploma and "deutsch" in after_diploma, (
        f"a home-country diploma is not the German Urkunde, which must be named again: {transcript!r}")
    _no_close(d, transcript)
    _assert_no_munich_opening_denied(_luna_bubbles(transcript), transcript)


# --- soft "ja" to the qualification question (TASK-97, Ivan's manual test) ----------------------

_QUALIFICATION_TERMS = ("urkunde", "anerkenn", "defizit", "kenntnisprüfung", "kenntnispruefung")
_ABBREV_DOT_RE = re.compile(r"\b(?:z\.\s?B|d\.\s?h|u\.\s?a|bzw|ggf|evtl|inkl|ca|usw)\.", re.I)
_QUESTION_RE = re.compile(r"[^.!?\n]*\?")


def _qualification_questions(bubbles):
    """Every question sentence in these bubbles that is about the qualification path. Abbreviation
    dots (z.B., bzw.) are dropped first so they do not cut a question in half (seen live: '...,
    oder läuft noch ein Anerkennungsverfahren (z.B. Defizitbescheid oder Kenntnisprüfung)?')."""
    return [q.strip() for b in bubbles
            for q in _QUESTION_RE.findall(_ABBREV_DOT_RE.sub(lambda m: m[0].replace(".", ""), b))
            if any(t in q.lower() for t in _QUALIFICATION_TERMS)]


def _is_either_or(question):
    return bool(re.search(r"\boder\b", question, re.I))


def _ja_until_qualification_path(d, transcript):
    """Answer each Luna turn's qualification question with a bare 'ja' until qualification_path
    lands. Fails the moment Luna leaves it open without asking, or needs a third ask. Returns
    (final turn, the qualification questions of every turn that got a 'ja')."""
    asks = []
    while d["slots"].get("qualification_path") in (None, "unknown"):
        questions = _qualification_questions(d["bubbles"])
        assert questions, f"qualification still open but Luna did not ask it: {transcript!r}"
        asks.append(questions)
        assert len(asks) <= 2, f"more than one re-ask after the first 'ja': {transcript!r}"
        d = LB.turn("ja", {"slots": d["slots"], "asked": d["asked"]})
        transcript += [("candidate", "ja"), ("luna", d["bubbles"])]
    print(json.dumps(transcript, ensure_ascii=False, indent=1))
    return d, asks


def _assert_urkunde_from_a_ja_to_a_plain_yes_no(d, asks, transcript):
    assert asks, f"qualification_path was set without ever asking: {transcript!r}"
    assert d["slots"]["qualification_path"] == "urkunde", transcript
    assert not any(_is_either_or(q) for q in asks[-1]), (
        f"urkunde was recorded from a 'ja' to an either/or question: {asks[-1]!r}")
    either_or = [a for a in asks if any(_is_either_or(q) for q in a)]
    assert len(either_or) <= 1, f"an either/or qualification question got 'ja' twice: {either_or!r}"


_OPENER_OLENA = "Hallo, ich bin Krankenschwester und suche eine Stelle in Bayern."


def test_olena_bare_ja_to_the_qualification_question_resolves_within_one_re_ask(board):
    """TASK-97 (Ivan's manual test 2026-09-13): Luna asked 'Urkunde schon, oder noch im
    Anerkennungsverfahren (Defizitbescheid/Kenntnisprüfung)?', the candidate answered 'ja' twice and
    only a third, plain yes/no ask resolved it. Fictional persona: Olena, nurse, region already given,
    answers every qualification question with a bare 'ja'. qualification_path=urkunde must land within
    at most one re-ask, only from a 'ja' to a plain yes/no question, and at most one either/or
    qualification question may ever be answered by that 'ja'."""
    d = LB.turn(_OPENER_OLENA, {"slots": {}, "asked": []})
    transcript = [("candidate", _OPENER_OLENA), ("luna", d["bubbles"])]
    d, asks = _ja_until_qualification_path(d, transcript)
    _assert_urkunde_from_a_ja_to_a_plain_yes_no(d, asks, transcript)


_SEEDED_EITHER_OR = ("Haben Sie schon eine deutsche Pflege-Urkunde, oder sind Sie noch im "
                     "Anerkennungsverfahren (Defizitbescheid/Kenntnisprüfung)?")


def test_olena_bare_ja_to_a_seeded_either_or_question_gets_a_strict_yes_no_re_ask(board):
    """TASK-97, the ambiguous path itself: with the current prompt Luna asks the plain yes/no first
    (test above), so the either/or ask from Ivan's thread is seeded. The first turn runs with one
    extra system-prompt line forcing that question; the CLI takes the system prompt per call and
    never stores it in the session, so every later turn runs on the real prompt with only the
    seeded question in the session history. The bare 'ja' to it must settle nothing, and the very
    next re-ask must be a plain yes/no that the second 'ja' resolves to urkunde."""
    seed = f"\n\nTEST SEED (this turn only): greet briefly, then ask exactly this question: {_SEEDED_EITHER_OR}"
    seeded = LB.Client(reply=lambda system, user, sid: LB.Client()._live_reply(system + seed, user, sid))
    d = LB.turn(_OPENER_OLENA, {"slots": {}, "asked": []}, client=seeded)
    transcript = [("candidate", _OPENER_OLENA), ("luna", d["bubbles"])]
    assert any(_is_either_or(q) for q in _qualification_questions(d["bubbles"])), (
        f"the seed did not produce the either/or question: {transcript!r}")
    d, asks = _ja_until_qualification_path(d, transcript)
    assert len(asks) == 2, f"the bare 'ja' to the either/or question settled the path by itself: {transcript!r}"
    _assert_urkunde_from_a_ja_to_a_plain_yes_no(d, asks, transcript)


# --- the city and housing questions are open questions too (TASK-97 review 2026-09-14) -----------
# Live under the first TASK-97 prompt, 3/3 runs per gate: "Gibt es eine Stadt oder Region in Bayern ..., z. B.
# München, ... oder Würzburg?", "Haben Sie schon eine Stadt im Blick (...) oder ist Ihnen der Fachbereich
# wichtiger?", "Ziehen Sie allein um, oder würden noch weitere Personen mit Ihnen wohnen?" -- a bare Ja fits all.

_W_QUESTION_RE = re.compile(r"^\W*(welche[rsmn]?|wo|wohin|wie|was|wann|wer|in welche[rmn]?|mit wie)\b", re.I)
_OLENA_CARD = {"region": "Bayern", "qualification_path": "urkunde", "qualification_ok": True,
               "role_verdict": "accept"}


_CLAUSE_SPLIT_RE = re.compile(r"[,;:–—]")


def _yes_no_frames_around_options(bubbles):
    """Question sentences whose 'oder' clause does not open with a W-word: a Ja answers them without choosing.
    Per bubble and per clause (repair round 2, live: '... 6 offene Stellen in ganz Bayern – in welcher Stadt oder
    Region würden Sie denn gerne arbeiten?' is an open question behind a statement, and a bubble with no end
    punctuation ran into the next one)."""
    questions = [q for b in bubbles for q in _QUESTION_RE.findall(_ABBREV_DOT_RE.sub(lambda m: m[0].replace(".", ""), b))]
    return [q for q in questions if _is_either_or(q)
            and not _W_QUESTION_RE.search(next(c for c in _CLAUSE_SPLIT_RE.split(q) if _is_either_or(c)).strip())]


@pytest.mark.parametrize("card, inbound, topic_re", [
    (_OLENA_CARD, "Ja, die deutsche Urkunde habe ich.", r"stadt|ort|region"),
    ({**_OLENA_CARD, "city": "München"}, "München wäre gut.", r"person|wohn|allein|familie"),
], ids=["city_gate", "housing_gate"])
def test_olena_city_and_housing_questions_are_not_yes_no_frames_around_options(board, card, inbound, topic_re):
    d = LB.turn(inbound, {"slots": dict(card), "asked": []})
    transcript = [("candidate", inbound), ("luna", d["bubbles"])]
    print(json.dumps(transcript, ensure_ascii=False, indent=1))
    assert re.search(topic_re, " ".join(d["bubbles"]), re.I), f"the gate question was not asked: {transcript!r}"
    assert _yes_no_frames_around_options(d["bubbles"]) == [], transcript
    _assert_no_munich_opening_denied(d["bubbles"], transcript)


# --- Group 3: Defizitbescheid recognition-gap path ---------------------------------------------

def test_anna_defizitbescheid_path_is_not_asked_for_an_urkunde_file(board):
    """Fictional persona: Anna, Polish nurse, Defizitbescheid already issued, no full Urkunde
    yet. Defining pattern: the harness must recognize this as an accepted path on its own gate,
    not silently fold it into 'reject', and must not demand the Urkunde file she does not have."""
    results = _run([
        "Hallo, ich bin Krankenschwester, meine Anerkennung ist noch nicht fertig.",
        "Ja, den Defizitbescheid habe ich schon bekommen.",
    ])
    final = results[-1]
    assert final["slots"].get("qualification_path") == "defizit"
    assert final["slots"].get("qualification_ok") is not False
    bubbles = " ".join(_all_bubbles(results)).lower()
    assert "urkunde" not in bubbles or "defizit" in bubbles, (
        "asking about the Urkunde file itself (rather than confirming the Defizit path) here "
        "would contradict constitution.json:qualification.urkunde_pending_rule")


# --- Group 4: Kenntnisprüfung passed, Urkunde pending at the authority --------------------------

def test_mai_passed_exam_waiting_for_urkunde_is_not_blocked(board):
    """Fictional persona: Mai, passed the Kenntnisprüfung, certificate still pending at the
    authority, flexible on region. Defining pattern: pre-qualified, must not be told to wait or
    asked to produce a document that has not been issued yet."""
    results = _run([
        "Hallo, ich habe die Kenntnisprüfung bestanden, die Urkunde ist noch beim Amt.",
        "Ich bin flexibel, Hauptsache Klinik in Bayern.",
    ])
    final = results[-1]
    assert final["slots"].get("qualification_path") == "kenntnispruefung"
    assert final["slots"].get("qualification_ok") is not False


# --- Group 5: disqualified despite sending a document ------------------------------------------

def test_luis_not_placeable_gets_the_locked_decline_not_a_model_paraphrase(board):
    """Fictional persona: Luis, self-describes as a Pflegehilfskraft (care aide) without a
    recognized nursing qualification. Defining pattern: quick, polite, code-locked decline --
    the exact regression app/wa/luna_brain.py's reject gate exists to guarantee."""
    results = _run([
        "Hallo, ich suche Pflegejob. Ich bin Pflegehilfskraft ohne Ausbildung, 6 Jahre Erfahrung.",
    ])
    final = results[-1]
    assert final["action"] == "explain_not_placeable"
    assert final["bubbles"] == [LB.P.REJECT_BODY_DE], (
        "the decline must be the locked text verbatim, not the model's own wording")
    assert final["slots"].get("qualification_ok") is False


def test_luis_reopening_with_the_identical_message_does_not_re_litigate_from_scratch(board):
    """Cross-cutting pattern: candidates who never received a clean resolution sometimes resend
    their exact opening message weeks later. The resumed session should recognize the repeat
    rather than act as if qualification were never discussed -- either staying silent (already
    said, nothing new to add) or repeating the exact locked decline is fine; a fresh, different
    explanation, or the harness crashing on an intentionally empty reply, is not (this exact
    repeat was the input that first surfaced the app/wa/luna_brain.py:turn() no_send/_check
    ordering bug this test now guards against)."""
    thread = {"slots": {}, "asked": []}
    opener = "Hallo, ich suche Pflegejob. Ich bin Pflegehilfskraft ohne Ausbildung, 6 Jahre Erfahrung."
    first = _run([opener], thread)
    thread = {"slots": first[-1]["slots"], "asked": first[-1]["asked"]}
    second = _run([opener], thread)
    assert second[-1]["action"] in ("no_send", "explain_not_placeable")
    assert second[-1]["bubbles"] in ([], [LB.P.REJECT_BODY_DE])
    assert second[-1]["slots"].get("qualification_ok") is False


# --- Group 6: family/companion housing case ----------------------------------------------------

def test_yassine_family_headcount_is_recorded_and_not_re_asked(board):
    """Fictional persona: Yassine, verified Urkunde, traveling with spouse and two children.
    Defining pattern (the group's own named regression in the source data): once a headcount is
    given, a later unrelated question must not re-trigger the same housing question."""
    results = _run([
        "Hallo, ich habe die Urkunde schon, bin anerkannt.",
        "Bayern bitte, München wäre ideal.",
        "Wir sind 4 Personen, meine Frau und 2 Kinder. Brauchen wir dafür eine größere Wohnung?",
        "Und wie ist das mit einem Kindergartenplatz?",
    ])
    assert results[2]["slots"].get("people_count") == 4 or results[2]["slots"].get("housing_known")
    last_bubbles = " ".join(results[-1]["bubbles"]).lower()
    assert "wie viele personen" not in last_bubbles and "wie viele leute" not in last_bubbles, (
        "the headcount was already given -- asking it again is the repeated-question regression "
        "this persona specifically targets")


def test_yassine_found_another_job_ends_gracefully(board):
    """A candidate who says they already found a placement elsewhere is a legitimate, resolved
    ending -- the harness must not keep pushing the funnel forward."""
    results = _run([
        "Hallo, ich habe die Urkunde schon.",
        "Ich habe inzwischen schon eine andere Stelle gefunden, danke trotzdem.",
    ])
    final_bubbles = " ".join(results[-1]["bubbles"]).lower()
    assert not any(w in final_bubbles for w in ("lebenslauf", "wohnung", "welche stadt")), (
        "must not keep chasing the funnel once the candidate has withdrawn")


# --- proactive tool use (TASK-62) ---------------------------------------------------------------
# board's fixture cities are München/Augsburg/Würzburg/Regensburg/Bayreuth -- Coburg is
# deliberately absent from it, so a question about Coburg cannot be answered from
# market_snapshot/consult alone and can only be answered honestly via a live search_postings call.

def _tool_calls(names_only=True):
    path = C.LUNA_SESSION_DIR / "tool_calls.jsonl"
    if not path.exists():
        return []
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").strip().splitlines() if l.strip()]
    return [l["tool"] for l in lines] if names_only else lines


def test_a_question_about_an_unlisted_city_actually_triggers_a_live_search(board):
    """The defining proactive-tool-use case: a city outside the default snapshot must be looked
    up for real, not answered with a guess or an honest-sounding 'I don't know' when a tool call
    would settle it directly."""
    results = _run([
        "Hallo, ich habe die Urkunde schon, bin anerkannt.",
        "Haben Sie auch etwas in Coburg?",
    ])
    calls = _tool_calls(names_only=False)
    search_calls = [c for c in calls if c["tool"] == "search_postings"]
    assert search_calls, f"expected a live search_postings call about Coburg, got calls: {calls!r}"
    assert any("coburg" in json.dumps(c["args"], ensure_ascii=False).lower() for c in search_calls), (
        f"a search_postings call happened but none mentioned Coburg: {search_calls!r}")
    final_bubbles = " ".join(results[-1]["bubbles"])
    assert final_bubbles.strip(), "the tool call must still be followed by an actual reply"
    assert "coburg" in final_bubbles.lower(), (
        f"TASK-73: the reply should name what it checked (Coburg), not just answer generically: {final_bubbles!r}")


def test_a_question_the_snapshot_already_answers_does_not_trigger_a_needless_call(board):
    """Proactive is not the same as trigger-happy: a question market_snapshot's own open_jobs
    total already answers should not burn a tool call to re-derive the same number."""
    results = _run(["Hallo, wie viele offene Stellen habt ihr insgesamt bei euch?"])
    calls = _tool_calls()
    assert "search_postings" not in calls, f"a question already answered by market_snapshot should not call search_postings, got: {calls!r}"
    assert results[-1]["bubbles"], "a plain count question should still get an actual reply"
