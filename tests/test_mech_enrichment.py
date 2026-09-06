from pflege_jobs.classify import enrich_description
from pflege_jobs.mechanics import get


def test_housing_tariff_email_language_bonus():
    e = enrich_description("Wir bieten Personalwohnungen. Vergütung nach TVöD-K. Kontakt: pflegedirektion@klinik.de, B2 Deutsch erforderlich. Willkommensprämie 3000€")
    assert e["housing"] and e["tariff"] == "TVöD" and e["contact_emails"] == ["pflegedirektion@klinik.de"]
    assert e["bonus"] and e["language_req"]


def test_empty_text_gives_nothing():
    assert enrich_description("") == {}


def test_pay_grade_and_requirements():
    e = enrich_description("Ihr Profil: abgeschlossene Ausbildung als Pflegefachkraft, mindestens 2 Jahre Berufserfahrung auf Intensivstation. Wir bieten: Vergütung nach TVöD-K Entgeltgruppe P 8 sowie Personalwohnungen.")
    assert e["pay_grade"] == "P8" and e["housing"] and e["tariff"] == "TVöD"
    assert e["requirements"].startswith("abgeschlossene Ausbildung als Pflegefachkraft")
    assert "Berufserfahrung" in e["experience"] and "Vergütung nach TVöD-K" in e["pay_text"]
    assert enrich_description("Eingruppierung gemäß TV-L in KR 8.")["pay_grade"] == "KR8"


def test_pay_grade_needs_tariff_context():
    assert enrich_description("Bus P 8 fährt zur Klinik. Vergütung nach Tarif.")["pay_grade"] is None


def test_mechanic_try_lists_fired_fields():
    r = get("enrichment").run({"description": "Personalwohnung vorhanden, Vergütung nach AVR Caritas."})
    assert "housing" in r["rule"] and r["result"]["tariff"] == "AVR Caritas"
