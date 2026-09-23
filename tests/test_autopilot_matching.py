"""TASK-105: score()'s "German 10" block used to score candidate.german_level in isolation, so a posting
silent on language and one stating "Deutsch mindestens B2" scored a candidate identically -- the
requirement the posting actually states was never read. enr_language_req (pflege_jobs.classify.
enrich_description) is a regex SNIPPET around the level marker, not a clean enum, e.g.
"Deutschkenntnisse mindestens B2 vorausgesetzt" -- _stated_level() pulls the level back out of it."""
from app.autopilot.matching import W_GERMAN, score

CLINIC = {"clinic_id": "1", "name": "Klinikum X", "town": "München", "regierungsbezirk": "Oberbayern", "beds": 300, "fachrichtungen": []}


def _candidate(level):
    return {"role_class": "pflegefachkraft", "city": "", "region": "", "qualification": "", "departments": [],
            "german_level": level, "anerkennung_status": "none"}


def _job(enr_language_req=None):
    return {"role_class": "pflegefachkraft", "qualification_hint": None, "department_hint": None, "enr_language_req": enr_language_req}


def test_a_posting_silent_on_language_still_uses_the_fixed_b2_bar():
    s, reasons = score(_candidate("B1"), CLINIC, [_job()])
    assert any("Deutsch B1 (Nachweis B2 fehlt)" in r for r in reasons)


def test_b1_candidate_gets_full_credit_against_a_posting_stating_b1():
    s_silent, _ = score(_candidate("B1"), CLINIC, [_job()])
    s_stated, reasons = score(_candidate("B1"), CLINIC, [_job("Deutschkenntnisse mindestens B1 vorausgesetzt")])
    assert any("erfüllt Anforderung" in r and "B1" in r for r in reasons)
    assert s_stated > s_silent          # half credit (fixed bar) -> full credit (meets the real bar)


def test_b2_candidate_gets_half_credit_one_level_under_a_stated_c1_requirement():
    s, reasons = score(_candidate("B2"), CLINIC, [_job("Deutsch auf C1-Niveau")])
    assert any("knapp unter Anforderung" in r for r in reasons)


def test_a2_candidate_gets_no_credit_two_levels_under_a_stated_b2_requirement():
    s, reasons = score(_candidate("A2"), CLINIC, [_job("Sprachniveau B2")])
    assert any("unter Anforderung" in r and "knapp" not in r for r in reasons)


def test_the_max_requirement_across_the_clinics_open_postings_wins():
    """score() is per-clinic, not per-posting (see rank()) -- a candidate qualifies for the clinic if
    ANY open posting would take them, so the toughest stated requirement is the one that must be met."""
    s, reasons = score(_candidate("B2"), CLINIC, [_job("B1 genügt"), _job("mindestens C1")])
    assert any("knapp unter Anforderung" in r and "C1" in r for r in reasons)
