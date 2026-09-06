from pflege_jobs.registry import Matcher, city_key
from pflege_jobs.mechanics import get

CL = [{"clinic_id": "16101", "name": "Klinikum Ingolstadt", "town": "Ingolstadt", "operator": "Klinikum Ingolstadt GmbH"},
      {"clinic_id": "16201", "name": "München Klinik Schwabing", "town": "München", "operator": "München Klinik gGmbH"},
      {"clinic_id": "16202", "name": "München Klinik Harlaching", "town": "München", "operator": "München Klinik gGmbH"},
      {"clinic_id": "58101", "name": "Klinikum Fürth", "town": "Fürth", "operator": "Klinikum Fürth"},
      {"clinic_id": "18701", "name": "Kliniken Südostbayern Klinikum Traunstein", "town": "Traunstein", "operator": "Kliniken Südostbayern AG"},
      {"clinic_id": "18702", "name": "Kliniken Südostbayern Kreisklinik Bad Reichenhall", "town": "Bad Reichenhall", "operator": "Kliniken Südostbayern AG"}]


def test_rules_r1_r2_r6():
    m = Matcher(CL)
    assert m.match("Klinikum Ingolstadt GmbH", "Ingolstadt")[1] in ("R1_exact", "R2_operator")
    assert m.match("Klinikum Fürth", "Fürth")[0] == "58101"
    assert m.match("Kliniken Südostbayern AG", "Traunstein") == ("18701", "R2_operator_town", 0.9)
    r = m.match("München Klinik gGmbH", "München")
    assert r[1].startswith("R6_ambiguous_sites:16201,16202")


def test_never_links_ambiguous_or_non_clinic():
    m = Matcher(CL)
    assert m.match("Kliniken Südostbayern AG", "Rosenheim") is None          # ambiguous operator, unknown town
    assert m.match("AWO Seniorenzentrum Fürth", "Fürth") is None


def test_tokens_and_stopwords():
    m = Matcher(CL)
    assert m.match("Klinikum Fürth Personalabteilung", "Fürth")[0] == "58101"


def test_uni_aliases():
    m = Matcher([{"clinic_id": "56290", "name": "Klinikum der Friedrich-Alexander-Universität Erlangen-Nürnberg", "town": "Erlangen", "operator": "Freistaat Bayern", "beds": 1400},
                 {"clinic_id": "16290", "name": "Klinikum der Ludwig-Maximilians-Universität München", "town": "München", "operator": "Freistaat Bayern", "beds": 2000},
                 {"clinic_id": "16291", "name": "Klinikum der Technischen Universität München (TUM) - Klinikum rechts der Isar", "town": "München", "operator": "Freistaat Bayern", "beds": 1100}])
    assert m.match("Universitätsklinikum Erlangen AöR", "Erlangen")[0] == "56290"
    assert m.match("LMU Klinikum (Campus Großhadern)", "München")[0] == "16290"
    assert m.match("Klinikum rechts der Isar der Technischen Universität München", "München")[0] == "16291"
    assert m.match("TUM Klinikum Rechts der Isar", "München")[0] == "16291"


def test_city_key():
    assert city_key("82467 Garmisch-Partenkirchen") == "garmisch-partenkirchen"
    assert city_key("Landshut, Isar") == "landshut" and city_key("Neuburg an der Donau") == "neuburg" and city_key("Muenchen") == "münchen"


def test_mechanic_try_uses_real_registry():
    r = get("clinic_link").run({"employer": "Klinikum Fürth Personalabteilung", "city": "Fürth"})
    assert r["result"]["clinic_id"] and r["rule"].startswith("R")
    assert get("clinic_link").run({"employer": "AWO Seniorenzentrum", "city": "Fürth"})["result"]["clinic_id"] is None
