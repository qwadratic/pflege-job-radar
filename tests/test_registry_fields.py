"""TASK-431 step 1, items 2-4: the registry fields the audit (TASK-431.1/.3) found wrong are cleaned where the snapshot is built
(app/data.py:_build), not in the DB. tests/fixtures/registry_clinics_2026-10-06.json holds the 651 rows of that day as the
`clinics` table has them (raw values); the `full_registry` fixture (conftest.py) runs them through _build()."""


def test_fachrichtungen_tokens_have_no_surrounding_space(full_registry):
    # 77 Reha rows write 'Innere Medizin, Kardiologie, ...': the split on ',' left ' Kardiologie' as a value of its own.
    by_id = full_registry["by_clinic"]
    assert by_id["RH1051"]["fachrichtungen"] == ["Innere Medizin", "Kardiologie", "Orthopädie", "Psychosomatik/Psychotherapie"]
    assert by_id["16101"]["fachrichtungen"][:3] == ["AUG", "CHI", "GUG"]                      # the plan's own 'AUG|CHI|...' form is unchanged
    assert [(c["clinic_id"], t) for c in full_registry["clinics"] for t in c["fachrichtungen"] if t != t.strip() or not t] == []
    assert [f["v"] for f in full_registry["facets"]["fachrichtungen"] if f["v"] != f["v"].strip()] == []


def test_versorgungsstufe_placeholders_are_none(full_registry):
    # '-' (44: Vertrags-KH, HS-Klinik, Bedarfsfeststellung) and '' (242: Reha and social rows) both mean 'no care level'.
    from collections import Counter
    assert Counter(c["versorgungsstufe"] for c in full_registry["clinics"]) == {
        None: 286, "Fachkrankenhaus": 191, "Grundversorgung (I)": 127, "Schwerpunkt (II)": 36, "Maximalversorgung (III)": 11}
    assert {f["v"]: f["n"] for f in full_registry["facets"]["versorgungsstufe"]} == {
        "Fachkrankenhaus": 191, "Grundversorgung (I)": 127, "Schwerpunkt (II)": 36, "Maximalversorgung (III)": 11}


def test_a_postings_versorgungsstufe_placeholder_is_none_too(tmp_path, monkeypatch):
    # v_postings carries the clinic's value on every posting row; the job facet and the WA board vocabulary count it.
    from app import config as A
    from app import data as D
    from app import runs as R
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    R.init()
    posting = {"posting_id": 1, "status": "open", "first_published": "2026-10-01", "department_hint": "", "clinic_id": "47570", "role_class": "fachpflege"}
    monkeypatch.setattr(A, "rest_get_all", lambda path, params=None, **kw: (
        [{**posting, "versorgungsstufe": "-"}, {**posting, "posting_id": 2, "versorgungsstufe": "Fachkrankenhaus"}] if path == "v_postings"
        else [{"clinic_id": "47570", "town": "Bad Rodach", "versorgungsstufe": "-"}] if path == "clinics" else []))
    monkeypatch.setattr(D, "_routing", lambda cs: {})
    snap = D._build()
    assert [j["versorgungsstufe"] for j in snap["jobs"]] == [None, "Fachkrankenhaus"]
    assert snap["clinics"][0]["versorgungsstufe"] is None


def test_operator_names_have_no_trailing_space(full_registry):
    # The RHV writes 'Diakonie Herzogsägmühle gGmbH\xa0' for four Reha rows: a second spelling of the same operator.
    by_id = full_registry["by_clinic"]
    assert {i: by_id[i]["operator"] for i in ("RH1017", "RH2019", "RH2063", "RH2220")} == {
        i: "Diakonie Herzogsägmühle gGmbH" for i in ("RH1017", "RH2019", "RH2063", "RH2220")}
    assert [c["clinic_id"] for c in full_registry["clinics"] if c["operator"] != c["operator"].strip()] == []


def test_the_pro_dashboard_does_not_special_case_the_old_placeholder():
    # web/pro.html is built from web/pro.template.html; both are committed and must say the same.
    import pathlib
    web = pathlib.Path(__file__).resolve().parent.parent / "web"
    for name in ("pro.template.html", "pro.html"):
        assert '"-":"#6C7583"' not in (web / name).read_text(encoding="utf-8"), name   # the colour of a '-' chip, which no row carries any more


# ---- size cohorts and the university tag (TASK-431.3, rule approved by Ivan 2026-10-06) ------------------------------
# size: S < 100, M 100-299, L >= 300 beds (XL folded into L). Beds 0 or NULL: no size, and size_reason says why.
# is_university: status 'HS-Klinik' (the 6 university hospitals of Art. 1 BayUniKlinG, 7 sites: DHM is part of the TUM Klinikum).

def test_size_cohorts_of_the_651_registry_clinics(full_registry):
    from collections import Counter
    clinics = full_registry["clinics"]
    assert Counter(c["size"] for c in clinics) == {"S": 259, "M": 247, "L": 82, None: 63}
    assert Counter(c["size_reason"] for c in clinics if c["size"] is None) == {"no_bed_concept": 13, "day_places_only": 42, "planned_only": 8}
    assert [c["clinic_id"] for c in clinics if c["size"] is not None and c["size_reason"] is not None] == []
    assert {f["v"]: f["n"] for f in full_registry["facets"]["size"]} == {"S": 259, "M": 247, "L": 82}
    by_id = full_registry["by_clinic"]
    assert (by_id["16290"]["beds"], by_id["16290"]["size"]) == (2062, "L")                                   # LMU Klinikum: was XL
    assert [(i, by_id[i]["size"], by_id[i]["size_reason"]) for i in ("DK01", "16104", "16106", "18501", "16101")] == [
        ("DK01", None, "no_bed_concept"), ("16104", None, "day_places_only"), ("16106", None, "planned_only"),
        ("18501", "M", None), ("16101", "L", None)]                                                          # 298 beds, 798 beds


def test_the_bed_thresholds_are_read_from_the_taxonomy_and_only_there():
    import json
    import pathlib
    from app import config as A
    from app import data as D
    tax = D.taxonomy()
    assert [b["key"] for b in tax["size_buckets"]] == ["S", "M", "L"]
    assert [D.size_bucket(b, tax) for b in (1, 99, 100, 299, 300, 799, 800, 2062)] == ["S", "S", "M", "M", "L", "L", "L", "L"]
    assert D.size_bucket(None, tax) is None
    rules = lambda t: [(b["key"], b.get("min", 0), b.get("max")) for b in t["size_buckets"]]       # noqa: E731
    assert rules(tax) == rules(json.loads((A.FALLBACK_DIR / "taxonomy.json").read_text(encoding="utf-8")))   # the two committed copies agree
    assert not hasattr(D, "_DEFAULT_SIZES")                                                          # no third copy in the code
    web = pathlib.Path(__file__).resolve().parent.parent / "web"
    for name in ("pro.template.html", "pro.html"):                                                   # none in the dashboard either: it reads `size` off the row
        text = (web / name).read_text(encoding="utf-8")
        assert [w for w in ("sizeOf", "size_XL", '"XL"', "300–799") if w in text] == [], name


def test_university_tag_is_the_hs_klinik_status(full_registry):
    clinics = full_registry["clinics"]
    assert {c["clinic_id"] for c in clinics if c["is_university"]} == {"16290", "76190", "66390", "56290", "16291", "36290", "16292"}
    assert {c["is_university"] for c in clinics} == {True, False}
    assert {c["status"] for c in clinics if c["is_university"]} == {"HS-Klinik"}
    assert sum(1 for c in clinics if c["is_university"] and c["size"] == "L") == 6                    # six become L + U, DHM (16292) stays M + U
