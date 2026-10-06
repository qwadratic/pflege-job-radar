"""TASK-431.2: tools/fill_clinic_plz.py matches the registry clinics to the sites of the Krankenhausverzeichnis 2024 and proposes
their PLZ. Offline: the real workbook (data/registry/krankenhausverzeichnis_24.xlsx), the 651 registry rows of 2026-10-06
(tests/fixtures/registry_clinics_2026-10-06.json) and a small synthetic workbook for each rule. Nothing here writes a DB."""
import collections
import csv
import json
import pathlib

import openpyxl
import pytest

from pflege_jobs import geo
from tools import fill_clinic_plz as F
from tools import ledger as L

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "registry_clinics_2026-10-06.json"


# ---- the real workbook against the 651 registry rows ---------------------------------------------------------------

@pytest.fixture(scope="module")
def real():
    clinics = json.loads(FIX.read_text(encoding="utf-8"))
    khv, rhv = F.load_sites()
    return clinics, F.propose(clinics, khv, rhv)


def test_626_of_651_clinics_get_a_plz_and_the_rule_is_recorded(real):
    # What the audit (TASK-431.2) reached: RH by exact id 229, DK from the source text 13, KeZ 384 by the KHV match.
    _, out = real
    assert collections.Counter(o["status"] for o in out) == {"fill": 626, "unresolved": 25}
    assert collections.Counter(o["rule"] for o in out if o["status"] == "fill") == {
        "rhv_id": 229, "dk_source": 13, "khv_domain": 155, "khv_only_site_in_municipality": 135, "khv_name_overlap": 72, "khv_municipality_one_plz": 22}
    assert all(o["rule"] and o["site"] and len(o["new_plz"]) == 5 for o in out if o["status"] == "fill")


def test_the_unresolved_are_the_big_city_sites_the_directory_cannot_tell_apart(real):
    _, out = real
    un = {o["clinic_id"]: o["why"] for o in out if o["status"] == "unresolved"}
    assert sorted(un) == ["16101", "16102", "16107", "16212", "16251", "16252", "16259", "16261", "16264", "16268", "16290", "16291", "16307",
                          "17706", "17772", "18302", "26108", "26205", "56413", "57707", "66204", "66205", "66310", "66312", "76107"]
    assert sum(1 for w in un.values() if w.startswith("ambiguous")) == 22
    assert sum(1 for w in un.values() if w.startswith("no KHV site")) == 3                      # 17772 (Oberding), 18302 (Haag i.OB), 57707 (Treuchtlingen)
    assert un["16290"] == "ambiguous: 78 KHV sites, 35 PLZ in the municipality"                 # the LMU: München has 78 KHV sites


def test_known_matches_agree_with_the_primary_source(real):
    _, out = real
    by = {o["clinic_id"]: o for o in out}
    assert (by["RH1847"]["new_plz"], by["RH1847"]["rule"]) == ("83646", "rhv_id")
    assert (by["DK01"]["new_plz"], by["DK01"]["rule"]) == ("91590", "dk_source")             # Bernhard-Harleß-Str. 2, 91590 Bruckberg
    assert (by["16105"]["new_plz"], by["16105"]["rule"]) == ("85049", "khv_domain")          # Danuvius Klinik Ingolstadt
    assert (by["57403"]["new_plz"], by["57403"]["rule"]) == ("90518", "khv_only_site_in_municipality")   # Altdorf b.Nürnberg, not the Markt near Landshut


def test_the_municipality_of_the_clinics_landkreis_is_used(real):
    clinics, _ = real
    khv, rhv = F.load_sites()
    blind = [{**c, "landkreis": None} for c in clinics if c["clinic_id"] in ("57403", "DK01")]
    assert {o["clinic_id"]: o["status"] for o in F.propose(blind, khv, rhv)}["57403"] == "unresolved"   # 'Altdorf' alone: Markt near Landshut, no KHV site


# ---- each rule on a small synthetic workbook -----------------------------------------------------------------------

def _ags8(town, landkreis):
    ars = geo.clinic_centroid(town, None, landkreis).ars
    return ars[:5] + ars[9:12]


def _workbook(path, khv, rhv):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = " KHV_2024"
    ws.append(["Zurück zum Inhalt"])
    ws.append(["Verzeichnis der Krankenhäuser"])
    ws.append(["KH_ID_Pseudo", "Land", "Gemeinde", "Kreis", "KH_Name", "Standortname", "PLZ", "Ort", "Internet"])
    for i, (ags8, name, site, plz, web) in enumerate(khv):
        ws.append([str(1000 + i), ags8[:2], ags8[5:8], ags8[2:5], name, site, plz, "x", web])
    ws.append(["9999", "01", "000", "001", "Ein Haus in Flensburg", "", "24939", "Flensburg", ""])                # another Land: ignored
    ws = wb.create_sheet("RHV_2024")
    ws.append(["Zurück zum Inhalt"])
    ws.append(["Verzeichnis der Vorsorge- oder Rehabilitationseinrichtungen"])
    ws.append(["RH_ID_Pseudo", "Land", "Gemeinde", "Kreis", "RH_Name", "PLZ", "Ort"])
    for rid, name, plz in rhv:
        ws.append([rid, "09", "112", "173", name, plz, "x"])
    wb.save(path)
    return path


def _clinic(cid, town="", landkreis="", **kw):
    return {"clinic_id": cid, "town": town, "landkreis": landkreis, "plz": None, "name": "", "operator": "", "website": "", "source": "", **kw}


@pytest.fixture()
def synthetic(tmp_path):
    bruck = _ags8("Bruckberg", "Landkreis Ansbach")
    ro = _ags8("Rosenheim", "Kreisfreie Stadt Rosenheim")
    la = _ags8("Landshut", "Kreisfreie Stadt Landshut")
    rg = _ags8("Regensburg", "Kreisfreie Stadt Regensburg")
    wu = _ags8("Würzburg", "Kreisfreie Stadt Würzburg")
    xlsx = _workbook(tmp_path / "khv.xlsx", [
        (bruck, "Diakonie Bruckberg", "", "91590", ""),
        (ro, "RoMed Klinikum Rosenheim", "", "83022", "www.romed-kliniken.de"), (ro, "Sana Klinik Rosenheim", "", "83024", "www.sana.de"),
        (la, "Klinikum Landshut", "", "84034", ""), (la, "Kinderklinik St. Marien", "Kinderklinik St. Marien", "84028", ""),
        (rg, "Haus Nord", "", "93049", ""), (rg, "Haus Süd", "", "93049", ""),
        (wu, "Haus Alpha", "", "97070", ""), (wu, "Haus Beta", "", "97080", "")], [("1847", "Rehaklinik FRISIA", "83646")])
    khv, rhv = F.load_sites(xlsx)
    clinics = [
        _clinic("11111", "Bruckberg", "Landkreis Ansbach"),
        _clinic("22222", "Rosenheim", "Kreisfreie Stadt Rosenheim", website="https://www.romed-kliniken.de/"),
        _clinic("33333", "Landshut", "Kreisfreie Stadt Landshut", name="Kinderklinik St. Marien Landshut"),
        _clinic("44444", "Regensburg", "Kreisfreie Stadt Regensburg", name="Zentrum X"),
        _clinic("55555", "Würzburg", "Kreisfreie Stadt Würzburg", name="Zentrum Y"),
        _clinic("66666", "Kempten", "Kreisfreie Stadt Kempten (Allgäu)"),
        _clinic("RH1847"), _clinic("RH9999"),
        _clinic("DK01", source="Diakoneo (Adresse: Weiherstraße 9, 96450 Coburg, erhoben 2026-09-24)"), _clinic("DK02", source="Diakoneo (Adresse: Sonnenstraße, Dinkelsbühl)"),
        _clinic("X1"),
        _clinic("77777", "Bruckberg", "Landkreis Ansbach", plz="91590"), _clinic("88888", "Bruckberg", "Landkreis Ansbach", plz="99999")]
    return F.propose(clinics, khv, rhv)


def test_each_rule_on_a_small_workbook(synthetic):
    got = {o["clinic_id"]: (o["status"], o["rule"], o["new_plz"]) for o in synthetic}
    assert got == {
        "11111": ("fill", "khv_only_site_in_municipality", "91590"),
        "22222": ("fill", "khv_domain", "83022"),
        "33333": ("fill", "khv_name_overlap", "84028"),
        "44444": ("fill", "khv_municipality_one_plz", "93049"),
        "55555": ("unresolved", "", ""),
        "66666": ("unresolved", "", ""),
        "RH1847": ("fill", "rhv_id", "83646"),
        "RH9999": ("unresolved", "", ""),
        "DK01": ("fill", "dk_source", "96450"),
        "DK02": ("unresolved", "", ""),
        "X1": ("unresolved", "", ""),
        "77777": ("unchanged", "khv_only_site_in_municipality", "91590"),
        "88888": ("conflict", "khv_only_site_in_municipality", "91590"),                    # the stored PLZ differs: listed, never overwritten
    }
    why = {o["clinic_id"]: o["why"] for o in synthetic if o["status"] == "unresolved"}
    assert why["55555"] == "ambiguous: 2 KHV sites, 2 PLZ in the municipality"
    assert why["66666"].startswith("no KHV site in the municipality")
    assert why["RH9999"] == "no RHV row with this id" and why["X1"].startswith("clinic_id is neither")


def test_corrections_are_a_complete_file_for_the_generic_applier(synthetic):
    todo = F.corrections(synthetic, "TASK-431")
    assert sorted(todo) == ["11111", "22222", "33333", "44444", "DK01", "RH1847"]            # fill only: not unchanged, not conflict, not unresolved
    assert todo["22222"]["plz"] == "83022" and set(todo["22222"]) == {"plz", "_why"}
    why = L.require_why(todo["22222"]["_why"], "clinic 22222", {F.REASON_CODE})                # tools/apply_clinic_corrections.py stops on anything less
    assert why["task"] == "TASK-431" and "khv_domain" in why["reason"] and "KHV" in why["evidence"][0]


# ---- the command line ----------------------------------------------------------------------------------------------

def test_dry_run_prints_the_counts_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    F.main(["--clinics", str(FIX)])
    out = capsys.readouterr().out
    assert "651 clinics: fill 626, unchanged 0, conflict 0, unresolved 25" in out and "khv_domain" in out
    assert "dry run: nothing written; --apply --by <who> would set 626 PLZ" in out
    assert list(tmp_path.iterdir()) == []


def test_csv_holds_every_clinic_and_apply_needs_a_name(tmp_path, capsys):
    path = tmp_path / "p.csv"
    F.main(["--clinics", str(FIX), "--csv", str(path)])
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    assert len(rows) == 651 and list(rows[0]) == F.CSV_FIELDS
    assert collections.Counter(r["status"] for r in rows) == {"fill": 626, "unresolved": 25}
    with pytest.raises(SystemExit, match="--apply needs --by"):
        F.main(["--clinics", str(FIX), "--apply"])


def test_apply_hands_the_fills_to_the_generic_applier_and_does_not_write_itself(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(F, "ROOT", str(tmp_path))
    monkeypatch.setattr(F.subprocess, "run", lambda cmd, check: calls.append(cmd))
    F.main(["--clinics", str(FIX), "--apply", "--by", "test"])
    (cmd,) = calls
    assert cmd[1:2] == [str(tmp_path / "tools" / "apply_clinic_corrections.py")] and cmd[3:] == ["--push", "--by", "test"]
    assert len(json.loads(pathlib.Path(cmd[2]).read_text(encoding="utf-8"))) == 626
