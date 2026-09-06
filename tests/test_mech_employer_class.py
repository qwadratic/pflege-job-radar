from pflege_jobs.classify import classify_employer, employer_norm
from pflege_jobs.mechanics import get


def test_employer_clinic():
    for n in ["Klinikum Nürnberg", "Universitätsklinikum Erlangen", "Bezirkskrankenhaus Bayreuth", "Sana Klinik München GmbH",
              "kbo-Isar-Amper-Klinikum gemeinnützige GmbH", "Thoraxzentrum Bezirk Unterfranken", "Oberberg GmbH Ost"]:
        assert classify_employer(n)[0] == "clinic", n


def test_employer_non_clinic():
    for n in ["AWO Seniorenzentrum Bamberg", "Ambulante Intensivpflege ape GmbH", "BRK KV Miesbach Personalabteilung",
              "Pflegedienst Sonnenschein GmbH", "Deutschefachpflege Holding GmbH", "Vitolus Care GmbH", "Rummelsberger Diakonie e.V. Rummelsberger Dienste gAG"]:
        assert classify_employer(n)[0] == "non_clinic", n


def test_employer_conflict_and_unknown():
    c, rule = classify_employer("Klinik-Seniorenresidenz XY")
    assert c == "unknown" and rule.startswith("conflict")
    assert classify_employer("Müller & Söhne") == ("unknown", "no_match")
    assert classify_employer("MVZ Praxisklinik Orthospine")[0] == "unknown"
    assert classify_employer("Donau-Ries Kliniken und Seniorenheime gKU Seniorenheim Monheim")[0] == "unknown"


def test_conflict_matrix_weak_groups_lose_to_clinic():
    assert classify_employer("Malteser Waldkrankenhaus Erlangen gGmbH")[0] == "clinic"
    assert classify_employer("Caritas-Krankenhaus St. Josef Träger Caritasverband e.V.")[0] == "clinic"
    assert classify_employer("Kliniken Nordoberpfalz AG Akademie für Gesundheit")[0] == "clinic"
    assert classify_employer("Sozialstiftung Bamberg")[0] == "clinic"
    assert classify_employer("Schwesternschaft München vom B Roten Kreuz e. V.")[0] == "clinic"
    assert classify_employer("Deutsche Rentenversicherung Bund Reha-Zentrum Bad Brückenau Klinik Hartwald")[0] == "clinic"


def test_employer_norm_strips_legal_forms_only():
    assert employer_norm("Klinikum Nürnberg gGmbH") == employer_norm("Klinikum Nürnberg GmbH")
    assert employer_norm("Klinikum Nürnberg") != employer_norm("Klinikum Nürnberg Personalabteilung")


def test_mechanic_try_reports_rule():
    r = get("employer_class").run({"name": "Klinikum Nürnberg gGmbH"})
    assert r["result"]["employer_class"] == "clinic" and r["rule"].startswith("clinic:")
