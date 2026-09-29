"""TASK-145 AC#1: the RHV (Reha/Vorsorge facilities) sync parses the real, checked-in
data/registry/krankenhausverzeichnis_24.xlsx and produces one clinics-registry-shaped row per Bavaria
facility, with no OCR-style ambiguity (structured columns, not the Krankenhausplan PDF)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.sync_rhv_reha import parse, _for_push  # noqa: E402
from pflege_jobs.schema import CLINIC_SPEC  # noqa: E402

ROWS = parse()


def test_for_push_turns_blank_int_columns_into_none_not_empty_string():
    # A CSV round-trip stringifies every field, including Python None -> "". json_to_recordset
    # casts beds/day_places straight to Postgres int and "" fails that cast (confirmed live against
    # the pooler: int4in("") raises "invalid input syntax for type integer"), taking down the whole
    # batch's single INSERT...SELECT -- this is what silently dropped all 229 rows the first time.
    row = {"clinic_id": "RH1885", "name": "X", "beds": "", "day_places": "", "website": ""}
    out = _for_push(row)
    assert out["beds"] is None
    assert out["day_places"] is None
    assert out["website"] == ""          # only the two known int columns are touched
    assert out["clinic_id"] == "RH1885"


def test_for_push_leaves_a_real_int_string_untouched():
    row = {"beds": "127", "day_places": "0"}
    out = _for_push(row)
    assert out["beds"] == "127"
    assert out["day_places"] == "0"


def test_only_bavaria_facilities_and_matches_the_known_live_count():
    # RHV_2024 has 1094 facilities nationwide; live-verified 2026-09-24: 229 are Land='09' (Bavaria).
    assert len(ROWS) == 229


def test_every_row_has_all_clinic_spec_fields_and_an_rh_prefixed_id():
    spec_keys = {k for k, _ in CLINIC_SPEC}
    for r in ROWS:
        assert set(r) == spec_keys
        assert r["clinic_id"].startswith("RH")
        assert r["clinic_id"][2:].isdigit()


def test_status_is_the_reha_discriminator_not_a_krankenhausplan_status():
    kh_statuses = {"Plan-KH", "Vertrags-KH", "HS-Klinik", "Bedarfsfeststellung", "nicht_mehr_im_plan"}
    for r in ROWS:
        assert r["status"] == "Reha-Einrichtung"
        assert r["status"] not in kh_statuses


def test_traegerart_values_are_the_shared_three_way_enum():
    seen = {r["traegerart"] for r in ROWS}
    assert seen <= {"oeffentlich", "freigemeinnuetzig", "privat"}
    assert seen  # not all empty


def test_regierungsbezirk_is_derived_per_row_not_a_constant():
    # All 7 Bavarian Regierungsbezirke are large enough to have at least one Reha facility.
    assert {r["regierungsbezirk"] for r in ROWS} == {
        "Oberbayern", "Niederbayern", "Oberpfalz", "Oberfranken", "Mittelfranken", "Unterfranken", "Schwaben"}


def test_a_known_facility_parses_with_the_expected_fields():
    row = next(r for r in ROWS if r["clinic_id"] == "RH1847")
    assert row["name"] == "Rehaklinik FRISIA Munkert GmbH"
    assert row["town"] == "Bad Tölz"
    assert row["beds"] == 127
    assert row["regierungsbezirk"] == "Oberbayern"
    assert row["landkreis"] == "Bad Tölz-Wolfratshausen"
    assert row["website"] == "https://www.frisia-toelz.de"
    assert row["careers_url"] == "" and row["ats_type"] == ""


def test_no_duplicate_clinic_ids():
    ids = [r["clinic_id"] for r in ROWS]
    assert len(ids) == len(set(ids))
