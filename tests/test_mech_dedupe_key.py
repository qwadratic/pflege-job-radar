from pflege_jobs.classify import fuzzy_key, employer_norm
from pflege_jobs.mechanics import get


def test_fuzzy_key_ignores_gender_marker_employment_and_legal_form():
    a = fuzzy_key("Pflegefachkraft (m/w/d)", "Klinikum Nürnberg gGmbH", "Nürnberg")
    b = fuzzy_key("Pflegefachkraft (w/m/d) Vollzeit", "Klinikum Nürnberg GmbH", "nürnberg")
    assert a == b


def test_fuzzy_key_separates_city_and_title():
    a = fuzzy_key("Pflegefachkraft (m/w/d)", "Klinikum Nürnberg", "Nürnberg")
    assert a != fuzzy_key("Pflegefachkraft (m/w/d)", "Klinikum Nürnberg", "Fürth")
    assert a != fuzzy_key("Pflegefachkraft Intensiv (m/w/d)", "Klinikum Nürnberg", "Nürnberg")


def test_employer_norm_is_conservative():
    assert employer_norm("Klinikum Nürnberg") != employer_norm("Klinikum Nürnberg Personalabteilung")


def test_canonical_ref_folds_url_variants():
    from pflege_jobs.cli import canonical_ref
    assert canonical_ref("https://jobs.smartrecruiters.com/ArtemedSE/744000143844579-needle-nurse-m-w-d-in-teilzeit") == canonical_ref("https://jobs.smartrecruiters.com/ArtemedSE/744000143844579")
    assert canonical_ref("https://x.de/job/1/?utm=a#top") == "https://x.de/job/1"


def test_mechanic_try_exposes_parts():
    r = get("dedupe_key").run({"title": "Pflegefachkraft (m/w/d)", "employer": "Klinikum Nürnberg gGmbH", "city": "Nürnberg"})
    assert r["result"]["employer_norm"] == "klinikum nürnberg" and len(r["result"]["fuzzy_key"]) == 40
