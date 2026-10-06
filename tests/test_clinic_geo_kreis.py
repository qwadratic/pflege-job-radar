"""TASK-431 step 1: the clinic's point follows its Landkreis (and PLZ when it has one), and geo_source names the rule.

pflege_jobs.geo.clinic_centroid(town, plz, landkreis) used to read the town string alone and take the first of several
Bavarian municipalities of that name: 6 clinics got a municipality 47-166 km away (57403 Altdorf, RH2229 Bernried, RH2421
Auerbach, 18710 Aschau, 18302 Haag, DK01 Bruckberg) and 17772 (München-Flughafen, Plan: Landkreis Erding) the centre of
München, 31 km off. tests/fixtures/registry_clinics_2026-10-06.json holds the 651 rows of that day as the `clinics` table
has them; the `full_registry` fixture (conftest.py) runs them through app/data.py:_build()."""
import csv
import math

from pflege_jobs import geo

_BY_ROWS = [r for r in csv.DictReader(open(geo._CSV_PATH, newline="", encoding="utf-8")) if r["land"] == "BY" and r["source"] == "destatis"]

# clinic_id -> (the Destatis municipality its registry Landkreis holds, that Kreis' ARS prefix)
KREIS_FIXED = {
    "57403": ("Altdorf b.Nürnberg, St", "09574"),          # Landkreis Nürnberger Land; the table's bare 'Altdorf' is a Markt near Landshut
    "RH2229": ("Bernried am Starnberger See", "09190"),    # Weilheim-Schongau; the first 'Bernried' is in Deggendorf
    "RH2421": ("Auerbach i.d.OPf., St", "09371"),          # Amberg-Sulzbach; the first 'Auerbach' is in Deggendorf
    "18710": ("Aschau i.Chiemgau", "09187"),               # Landkreis Rosenheim; the first 'Aschau' is Aschau a.Inn (Mühldorf)
    "18302": ("Haag i.OB, M", "09183"),                    # Mühldorf a. Inn; the first 'Haag' is Haag a.d.Amper (Freising)
    "DK01": ("Bruckberg", "09571"),                        # Landkreis Ansbach; the first 'Bruckberg' is near Landshut
    "17772": ("Oberding", "09177"),                        # München-Flughafen, Plan: Landkreis Erding -- not the centre of München
}


def _km(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p = math.pi / 180
    h = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def _kreis_centre(prefix):
    pts = [(float(r["lat"]), float(r["lon"])) for r in _BY_ROWS if r["ars"][:5] == prefix]
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def _municipality(lat, lon, name):
    rows = [r for r in _BY_ROWS if r["gemeindename"] == name and (float(r["lat"]), float(r["lon"])) == (lat, lon)]
    assert len(rows) == 1, (name, lat, lon)
    return rows[0]


def test_the_seven_clinics_off_by_town_name_sit_in_their_own_kreis(full_registry):
    by_id = full_registry["by_clinic"]
    assert {cid: by_id[cid]["geo_name"] for cid in KREIS_FIXED} == {cid: name for cid, (name, _) in KREIS_FIXED.items()}
    for cid, (name, kreis) in KREIS_FIXED.items():
        c = by_id[cid]
        assert _municipality(c["lat"], c["lon"], name)["ars"][:5] == kreis, cid
        assert _km((c["lat"], c["lon"]), _kreis_centre(kreis)) < BOUND_KM, cid


# The wrong points were 47-166 km from the right place (and 31 km for the airport); a Kreis is under 70 km across.
BOUND_KM = 30


def test_every_registry_clinic_sits_in_the_kreis_its_landkreis_names(full_registry):
    # A new registry row whose Landkreis string the Kreis table cannot read, or whose point lands in another Kreis, fails here by clinic_id.
    unread, outside = [], []
    for c in full_registry["clinics"]:
        codes = geo.kreis_codes(c["landkreis"])
        if not codes:
            unread.append((c["clinic_id"], c["landkreis"]))
        elif c["geo_source"] != "plz" and _municipality(c["lat"], c["lon"], c["geo_name"])["ars"][:5] not in codes:
            outside.append((c["clinic_id"], c["town"], c["landkreis"], c["geo_name"]))
    assert (unread, outside) == ([], [])


def test_geo_source_names_the_rule_that_placed_the_clinic(full_registry):
    by_id = full_registry["by_clinic"]
    assert by_id["16104"]["geo_source"] == "municipality_centroid"                  # 'München': one municipality, the Landkreis only agrees
    assert by_id["57403"]["geo_source"] == "municipality_centroid_by_kreis"         # 'Altdorf': two, the Landkreis decided
    assert by_id["17772"]["geo_source"] == "municipality_centroid_override"         # 'München-Flughafen': data/geo/clinic_town_overrides.json
    assert {c["geo_source"] for c in full_registry["clinics"]} == {"municipality_centroid", "municipality_centroid_by_kreis", "municipality_centroid_override"}
    g = geo.clinic_centroid("München", "80331", "Landeshauptstadt München")
    assert (g.rule, g.matched_name, g.plz) == ("plz", "München", "80331")


def test_kreis_codes_read_the_registry_spellings():
    assert geo.kreis_codes("Landkreis Ansbach") == {"09571"}
    assert geo.kreis_codes("Kreisfreie Stadt Ansbach") == {"09561"}
    assert geo.kreis_codes("Landeshauptstadt München") == {"09162"}
    assert geo.kreis_codes("Ansbach") == {"09561", "09571"}                          # the Reha rows write the bare name: Stadt or Landkreis
    assert geo.kreis_codes("Landkreis Neustadt a.d. Aisch-Bad Windsheim") == {"09575"}
    assert geo.kreis_codes("Landkreis Neustadt / Bad Windsheim") == {"09575"}        # the plan's own short form
    assert geo.kreis_codes("Landkreis Mühldorf a. Inn") == geo.kreis_codes("Mühldorf a.Inn") == {"09183"}
    assert geo.kreis_codes("Kreisfreie Stadt Kempten (Allgäu)") == {"09763"}
    assert geo.kreis_codes("Landkreis Atlantis") == set() == geo.kreis_codes(None)


def test_the_kreis_table_is_the_set_of_kreise_the_municipality_table_has():
    table = {r["code"] for r in csv.DictReader(open(geo._KREISE_PATH, newline="", encoding="utf-8"))}
    assert table == {r["ars"][:5] for r in _BY_ROWS} and len(table) == 96
