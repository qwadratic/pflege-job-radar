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
