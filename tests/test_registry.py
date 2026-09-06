from pflege_jobs.registry import Matcher, city_key
CL=[{"clinic_id":"16101","name":"Klinikum Ingolstadt","town":"Ingolstadt","operator":"Klinikum Ingolstadt GmbH"},
    {"clinic_id":"16201","name":"München Klinik Schwabing","town":"München","operator":"München Klinik gGmbH"},
    {"clinic_id":"16202","name":"München Klinik Harlaching","town":"München","operator":"München Klinik gGmbH"},
    {"clinic_id":"58101","name":"Klinikum Fürth","town":"Fürth","operator":"Klinikum Fürth"},
    {"clinic_id":"18701","name":"Kliniken Südostbayern Klinikum Traunstein","town":"Traunstein","operator":"Kliniken Südostbayern AG"},
    {"clinic_id":"18702","name":"Kliniken Südostbayern Kreisklinik Bad Reichenhall","town":"Bad Reichenhall","operator":"Kliniken Südostbayern AG"}]
def test_rules():
    m=Matcher(CL)
    assert m.match("Klinikum Ingolstadt GmbH","Ingolstadt")[1] in ("R1_exact","R2_operator")
    assert m.match("Klinikum Fürth","Fürth")[0]=="58101"
    assert m.match("Kliniken Südostbayern AG","Traunstein")==("18701","R2_operator_town",0.9)
    assert m.match("Kliniken Südostbayern AG","Rosenheim") is None          # ambiguous operator, unknown town -> no link
    r=m.match("München Klinik gGmbH","München"); assert r[1].startswith("R6_ambiguous_sites:16201,16202")   # same operator, sites ambiguous -> flagged
    assert m.match("Klinikum Fürth Personalabteilung","Fürth")[0]=="58101"
    assert m.match("AWO Seniorenzentrum Fürth","Fürth") is None
def test_uni_aliases():
    m=Matcher([{"clinic_id":"56290","name":"Klinikum der Friedrich-Alexander-Universität Erlangen-Nürnberg","town":"Erlangen","operator":"Freistaat Bayern","beds":1400},
               {"clinic_id":"16290","name":"Klinikum der Ludwig-Maximilians-Universität München","town":"München","operator":"Freistaat Bayern","beds":2000},
               {"clinic_id":"16291","name":"Klinikum der Technischen Universität München (TUM) - Klinikum rechts der Isar","town":"München","operator":"Freistaat Bayern","beds":1100}])
    assert m.match("Universitätsklinikum Erlangen AöR","Erlangen")[0]=="56290"
    assert m.match("LMU Klinikum (Campus Großhadern)","München")[0]=="16290"
    assert m.match("Klinikum rechts der Isar der Technischen Universität München","München")[0]=="16291"
    assert m.match("TUM Klinikum Rechts der Isar","München")[0]=="16291"
def test_city_key():
    assert city_key("82467 Garmisch-Partenkirchen")=="garmisch-partenkirchen"
    assert city_key("Landshut, Isar")=="landshut" and city_key("Neuburg an der Donau")=="neuburg" and city_key("Muenchen")=="münchen"


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
