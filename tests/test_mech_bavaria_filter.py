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


def test_one_item_list_instead_of_scalar_does_not_crash():
    # Live 2026-09-09, inbox_id 13576: a malformed upstream row carried {"plz": ["97318"],
    # "city": ["Kitzingen"]} -- crashed the whole drain (TypeError in BAV_PLZ.match) for every
    # other pending row behind it, not just this one.
    assert in_bavaria(["Kitzingen"], ["97318"], None, set()) is True     # 97318 is a Bavarian PLZ
    assert in_bavaria(["Berlin"], None, ["NW"], set()) is False
    assert in_bavaria([], [], [], set()) is None


def test_mechanic_try_reports_why():
    assert get("bavaria_filter").run({"city": "Neu-Ulm", "plz": "89231", "region": ""}) == {"result": {"in_bavaria": True}, "rule": "plz"}
