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
import re
import shutil
import time

import pytest

from app import data as D
from app.wa import config as C
from app.wa import luna_brain as LB

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not shutil.which(C.LUNA_CLAUDE_BIN),
                        reason=f"{C.LUNA_CLAUDE_BIN!r} is not on PATH -- WA_BRAIN=luna needs it installed"),
]

SALARY_RE = re.compile(r"\d[\d.,]*\s?(€|eur\b|euro)", re.I)


def _jobs():
    """A small, varied board: three cities, three departments, both city sizes, some housing --
    enough for the market snapshot to have real examples without needing the full live board."""
    rows = []
    plan = [
        ("München", "Oberbayern", "Intensiv/IMC", True), ("München", "Oberbayern", "OP", False),
        ("Augsburg", "Schwaben", "Innere Medizin", True), ("Würzburg", "Unterfranken", "Geburtshilfe", False),
        ("Regensburg", "Oberpfalz", "Notaufnahme", True), ("Bayreuth", "Oberfranken", "Chirurgie/Orthopädie", False),
    ]
    for i, (city, bezirk, dept, housing) in enumerate(plan):
        rows.append({"posting_id": i + 1, "title": f"Pflegefachkraft {dept}", "role_class": "pflegefachkraft",
                     "department_hint": dept, "city": city, "clinic_town": city, "regierungsbezirk": bezirk,
                     "clinic_name": f"Klinikum {city}", "employer": f"Klinikum {city}",
                     "employment_types": ["vollzeit"], "enr_housing": housing, "verify_status": "live",
                     "status": "open", "first_published": "2026-09-01", "fresh": True,
                     "source_url": f"https://example.org/job/{i + 1}"})
    return rows


@pytest.fixture()
def board(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": _jobs(), "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")


def _run(script, thread=None):
    """Play a persona's script turn by turn through the real CLI, returning the full list of
    per-turn results (each the dict app/wa/luna_brain.py:turn() returns)."""
    thread = thread or {"slots": {}, "asked": []}
    out = []
    for text in script:
        d = LB.turn(text, thread)
        out.append(d)
        thread = {"slots": d["slots"], "asked": d["asked"]}
    return out


def _all_bubbles(results):
    return [b for d in results for b in d["bubbles"]]


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


def test_maria_salary_question_is_deferred_never_quoted(board):
    """Cross-cutting pattern: a salary question before qualification is fully settled must be
    deferred to a human, never answered with an invented number."""
    results = _run([
        "Hallo, ich bin Krankenschwester, Urkunde schon anerkannt.",
        "Wie viel verdient man da ungefähr im Monat?",
    ])
    bubbles = " ".join(_all_bubbles(results))
    assert not SALARY_RE.search(bubbles), f"a euro figure must never be quoted, got: {bubbles!r}"


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
