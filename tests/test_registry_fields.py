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
