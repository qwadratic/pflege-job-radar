from pflege_jobs.mechanics import cv_profile, get

CV = """Lebenslauf
Gesundheits- und Krankenpflegerin
6 Jahre Berufserfahrung auf der Intensivstation, davon 2 Jahre Anästhesie / Aufwachraum
Beatmung, Stroke Unit
Sprachen: Deutsch C1, Englisch B2
"""


def test_roles_and_qualification():
    p = cv_profile(CV)
    assert "GuK" in p["qualifications"]
    assert p["roles"] and set(p["roles"]) <= {"pflegefachkraft", "fachpflege"}


def test_skills_departments_experience_languages():
    p = cv_profile(CV)
    assert {"Intensiv", "Anästhesie", "Beatmung"} <= set(p["skills"])
    assert "Intensiv/IMC" in p["departments"] and "Anästhesie" in p["departments"]
    assert p["experience_years"] == 6
    assert "Deutsch C1" in p["languages"] and "Englisch B2" in p["languages"]


def test_english_cv_and_empty():
    p = cv_profile("Registered nurse, 3 years ICU and emergency department, German B2")
    assert "Intensiv" in p["skills"] and "Notaufnahme" in p["skills"] and p["experience_years"] == 3 and "Deutsch B2" in p["languages"]
    e = cv_profile("")
    assert e["roles"] == [] and e["experience_years"] is None


def test_mechanic_try():
    assert get("cv_profile").run({"text": CV})["result"]["experience_years"] == 6
