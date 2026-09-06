"""Registry push safety (not a mechanic): discovery-owned columns survive CSV pushes."""
# --- regression: a registry push must never erase ATS-discovery columns -------------------------
# Both failure modes we actually hit in production: sending a blank value, and omitting the key
# entirely (json_to_recordset makes the omitted column NULL, so it overwrites just the same).
def test_merge_discovered_keeps_db_value_when_csv_is_blank():
    from pflege_jobs.registry import merge_discovered
    clinics = [{"clinic_id": "16201", "ats_type": "", "careers_url": "  "}]
    live = [{"clinic_id": "16201", "ats_type": "softgarden", "careers_url": "https://x.test/jobs"}]
    merge_discovered(clinics, live)
    assert clinics[0]["ats_type"] == "softgarden"
    assert clinics[0]["careers_url"] == "https://x.test/jobs"


def test_merge_discovered_fills_missing_keys():
    from pflege_jobs.registry import merge_discovered
    clinics = [{"clinic_id": "16201"}]                      # keys absent entirely
    merge_discovered(clinics, [{"clinic_id": "16201", "ats_type": "personio", "careers_url": ""}])
    assert clinics[0]["ats_type"] == "personio"             # present, so it cannot null the column
    assert clinics[0]["careers_url"] == ""


def test_merge_discovered_csv_wins_when_set():
    from pflege_jobs.registry import merge_discovered
    clinics = [{"clinic_id": "16201", "ats_type": "dvinci"}]
    merge_discovered(clinics, [{"clinic_id": "16201", "ats_type": "softgarden"}])
    assert clinics[0]["ats_type"] == "dvinci"               # a real CSV value still takes precedence


def test_merge_discovered_unknown_clinic_is_safe():
    from pflege_jobs.registry import merge_discovered
    clinics = [{"clinic_id": "99999", "ats_type": ""}]
    merge_discovered(clinics, [])
    assert clinics[0]["ats_type"] == ""                     # explicit empty, never a missing key


def test_full_clinic_rows_never_drops_columns():
    """A partial push must not null unrelated columns -- this wiped the whole registry once."""
    from pflege_jobs.registry import full_clinic_rows
    from pflege_jobs.schema import CLINIC_SPEC
    live = [{"clinic_id": "16201", "name": "München Klinik Schwabing", "town": "München",
             "beds": 660, "fachrichtungen": "INN|CHI", "ats_type": "softgarden"}]
    rows = full_clinic_rows([{"clinic_id": "16201", "name": "München Klinik Schwabing"}], live)
    assert len(rows[0]) == len(CLINIC_SPEC)          # every column present, none implicitly NULL
    assert rows[0]["beds"] == 660                    # untouched fields survive
    assert rows[0]["fachrichtungen"] == "INN|CHI"
    assert rows[0]["ats_type"] == "softgarden"       # discovery-owned value preserved
