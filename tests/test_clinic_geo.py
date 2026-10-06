"""TASK-200: every registry clinic carries a point for a map (lat, lon, geo_source, geo_name on the /api/clinics row).
The point is the centre of the clinic's municipality, no address: pflege_jobs.geo.clinic_centroid.

tests/fixtures/clinic_towns_2026-10-02.json is the list of the 287 distinct `town` strings of the 651 registry
clinics of that day (public names of Bavarian towns, no contacts); each must name exactly one point."""
import json
import pathlib

import pytest

from app import config as A
from app import data as D
from app import runs as R
from pflege_jobs import geo

TOWNS = json.loads((pathlib.Path(__file__).parent / "fixtures" / "clinic_towns_2026-10-02.json").read_text(encoding="utf-8"))
BAVARIA = (47.2, 50.6, 8.9, 13.9)   # lat min/max, lon min/max


def test_every_registry_town_names_exactly_one_point_inside_bavaria():
    # A new registry town that the table cannot place fails here by name: add it to data/geo/clinic_town_overrides.json.
    pts = {t: geo.clinic_centroid(t) for t in TOWNS}
    assert [t for t, g in pts.items() if g is None] == []
    assert [t for t, g in pts.items() if not (BAVARIA[0] <= g.lat <= BAVARIA[1] and BAVARIA[2] <= g.lon <= BAVARIA[3])] == []


@pytest.mark.parametrize("town,plz,name,lat,lon", [
    ("München", None, "München, Landeshauptstadt", 48.1377, 11.576),                  # 58 clinics share this point
    ("Rosenheim", None, "Rosenheim", 47.8564, 12.1289),                                 # Destatis row, not one of the GeoNames PLZ rows
    ("Neustadt a. d. Aisch", None, "Neustadt a.d.Aisch, St", 49.5798, 10.6127),         # the registry's spacing, Destatis' spelling
    ("Rothenburg o.d. Tauber", None, "Rothenburg ob der Tauber, GKSt", 49.3771, 10.1793),
    ("Weiden", None, "Weiden i.d.OPf.", 49.6761, 12.1602),                              # bare name, one Bavarian municipality starts with it
    ("Augsburg-Göggingen", None, "Augsburg", 48.3684, 10.8976),                         # a district: the override file maps it to its town
    ("Berg", None, "Berg", 47.9666, 11.3562),                                           # two Bavarian Gemeinden are called Berg: Starnberg's, not Hof's
])
def test_known_towns(town, plz, name, lat, lon):
    g = geo.clinic_centroid(town, plz)
    assert (g.matched_name.split(",")[0], round(g.lat, 4), round(g.lon, 4)) == (name.split(",")[0], lat, lon)


def test_a_name_shared_by_several_bavarian_municipalities_is_no_point():
    # 'Neustadt' alone is Neustadt a.d.Donau, a.d.Aisch, a.d.Waldnaab, am Kulm, b.Coburg, a.Main: six points, none preferred.
    assert geo.clinic_centroid("Neustadt") is None
    assert geo.clinic_centroid("Atlantis") is None
    assert geo.clinic_centroid(None) is None


def test_an_override_naming_a_municipality_the_table_lacks_stops_the_import(monkeypatch, tmp_path):
    p = tmp_path / "o.json"
    p.write_text(json.dumps({"X": "Nirgendwo, St"}), encoding="utf-8")
    monkeypatch.setattr(geo, "_TOWN_OVERRIDES_PATH", p)
    monkeypatch.setattr(geo, "_TOWN_OVERRIDES", {})
    with pytest.raises(RuntimeError, match="Nirgendwo"):
        geo._load_towns()


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    R.init()
    return tmp_path


def test_the_clinic_row_carries_the_point(fresh, monkeypatch):
    clinics = [{"clinic_id": "16104", "name": "A", "town": "München", "plz": None, "beds": 0, "fachrichtungen": "", "ats_type": "", "website": "", "careers_url": ""},
               {"clinic_id": "99999", "name": "B", "town": "Atlantis", "plz": None, "beds": 0, "fachrichtungen": "", "ats_type": "", "website": "", "careers_url": ""}]
    # _build() also reads the "postings" table for housing evidence (app/data.py:_housing_evidence) --
    # answer that with no rows too, not with these clinic-shaped rows (they have no posting_id).
    monkeypatch.setattr(A, "rest_get_all",
                        lambda table, params: clinics if table == "clinics" else [])
    monkeypatch.setattr(D, "taxonomy", lambda: {})
    monkeypatch.setattr(D, "_routing", lambda cs: {})
    by_id = D._build()["by_clinic"]
    assert {k: by_id["16104"][k] for k in ("lat", "lon", "geo_source", "geo_name")} == {
        "lat": 48.137683, "lon": 11.575997, "geo_source": "municipality_centroid", "geo_name": "München, Landeshauptstadt"}
    assert {k: by_id["99999"][k] for k in ("lat", "lon", "geo_source", "geo_name")} == {"lat": None, "lon": None, "geo_source": None, "geo_name": None}
