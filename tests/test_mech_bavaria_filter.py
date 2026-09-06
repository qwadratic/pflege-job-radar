from pflege_jobs.sources.career_crawl import in_bavaria
from pflege_jobs.mechanics import get


def test_plz_ranges():
    assert in_bavaria(None, "88709", None, set()) is False      # Meersburg (BW)
    assert in_bavaria(None, "89073", None, set()) is False      # Ulm (BW)
    assert in_bavaria(None, "89231", None, set()) is True       # Neu-Ulm (BY)
    assert in_bavaria(None, "88131", None, set()) is True       # Lindau (BY)
    assert in_bavaria(None, "63739", None, set()) is True and in_bavaria(None, "97080", None, set()) is True


def test_region_codes_beat_everything():
    assert in_bavaria("Bad Oeynhausen", None, "NW", {"bad kissingen"}) is False
    assert in_bavaria("Tutzing", None, "BY", set()) is True
    assert in_bavaria("Tutzing", "88709", "Bayern", set()) is True


def test_town_list_and_generic_prefix():
    assert in_bavaria("Bad Oeynhausen", None, None, {"bad kissingen", "bad"}) is None
    assert in_bavaria("Tutzing", None, None, {"tutzing"}) is True
    assert in_bavaria("Berlin", None, None, {"tutzing"}) is False
    assert in_bavaria("", None, None, set()) is None


def test_mechanic_try_reports_why():
    assert get("bavaria_filter").run({"city": "Neu-Ulm", "plz": "89231", "region": ""}) == {"result": {"in_bavaria": True}, "rule": "plz"}
