from pflege_jobs.classify import qualification_hint
from pflege_jobs.mechanics import get


def test_from_hauptberuf():
    assert qualification_hint("x", "Gesundheits- und Kinderkrankenpfleger/in") == "GKiK"
    assert qualification_hint("x", "Pflegefachmann/-frau (Altenpflege)") == "Altenpflege"
    assert qualification_hint("x", "Gesundheits- und Krankenpfleger/in") == "GuK"


def test_from_title_only():
    assert qualification_hint("Gesundheits- und Krankenpfleger (m/w/d) Intensiv") == "GuK"
    assert qualification_hint("Pflegefachfrau / Pflegefachmann (m/w/d)") in ("generalistisch", "GuK", None)


def test_null_means_not_stated():
    assert qualification_hint("Stationsleitung (m/w/d)") is None
    assert get("qualification").run({"title": "Stationsleitung (m/w/d)", "hauptberuf": ""})["result"]["qualification_hint"] is None
