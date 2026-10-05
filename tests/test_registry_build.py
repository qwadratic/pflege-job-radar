"""tools/registry_build.py (TASK-175, TASK-180 AC#4): every field a source states is compared with the DB, and a
difference counts as explained only by a pflege_jobs.corrections row that set exactly the value the DB holds.
No DB, no PDF: the rows below are the shapes read_db() and the parsers return."""
from tools import registry_build as RB

PLAN = "krankenhausplan_2026"
SOURCES = {PLAN: ([], "Krankenhausplan Bayern 2026 (51. Fortschreibung), StMGP", RB.PDF),
           "rhv_2024": ([], "RHV 2024", RB.XLSX)}


def _cor(i, cid, field, new, code="parse_error"):
    return {"id": i, "row_id": cid, "field": field, "new_value": new, "reason_code": code, "reason": "r", "task": "TASK-1"}


def test_the_latest_correction_for_the_field_must_have_set_the_db_value():
    cor = {("16201", "name"): _cor(9, "16201", "name", "München Klinik Schwabing")}
    assert RB.explanation(cor, "16201", "name", "München Klinik Schwabing")["id"] == 9
    assert RB.explanation(cor, "16201", "name", "Something else") is None          # the DB moved on after #9
    assert RB.explanation(cor, "16201", "town", "München") is None                  # no row for that field


def test_an_insert_row_explains_the_fields_it_set():
    cor = {("16102", "*"): _cor(3, "16102", "*", {"name": "Privatklinik Dr. Maul", "beds": 38, "operator": None})}
    assert RB.explanation(cor, "16102", "beds", 38)["id"] == 3
    assert RB.explanation(cor, "16102", "operator", None)["id"] == 3
    assert RB.explanation(cor, "16102", "beds", 40) is None
    cor[("16102", "beds")] = _cor(4, "16102", "beds", 40)                            # a later field row wins
    assert RB.explanation(cor, "16102", "beds", 40)["id"] == 4 and RB.explanation(cor, "16102", "beds", 38) is None


def test_compare_marks_each_difference_and_lists_missing_and_vanished_rows():
    rows = [{"clinic_id": "16201", "name": "München Klinik Schwabing", "town": "München", "beds": 700},
            {"clinic_id": "16205", "name": "München Klinik Bogenhausen", "town": "München", "beds": 1020},
            {"clinic_id": "16107", "name": "ZPG Ingolstadt", "town": "Ingolstadt", "beds": 0}]
    db = {"16201": {"clinic_id": "16201", "name": "München Klinik Schwabing", "town": "", "beds": 700},
          "16205": {"clinic_id": "16205", "name": "München Klinik Bogenhausen", "town": "München", "beds": None},
          "26101": {"clinic_id": "26101", "name": "Gone", "status": RB.GONE},
          "26105": {"clinic_id": "26105", "name": "Dropped", "status": "Plan-KH"},
          "RH1847": {"clinic_id": "RH1847", "name": "Reha", "status": "Reha-Einrichtung"}}
    cor = {("16205", "beds"): _cor(1, "16205", "beds", None)}
    r = RB.compare(PLAN, rows, db, cor)
    assert [(d["id"], d["field"], d["db"], d["source_value"], d["explained"]) for d in r["deviations"]] == [
        ("16201", "town", None, "München", False), ("16205", "beds", None, 1020, True)]
    assert [m["id"] for m in r["missing_in_db"]] == ["16107"]
    assert [(g["id"], g["explained"]) for g in r["not_in_source"]] == [("26101", True), ("26105", False)]


def test_proposals_take_the_source_value_for_every_unexplained_difference_only():
    db = {"16201": {"clinic_id": "16201", "source": "Krankenhausplan Bayern 2026 (51. Fortschreibung), StMGP"},
          "26105": {"clinic_id": "26105", "source": "Krankenhausplan Bayern 2025 (50. Fortschreibung), StMGP"}}
    sources = {PLAN: ([{"clinic_id": "16107", "name": "ZPG Ingolstadt", "town": "Ingolstadt", "beds": 0, "website": ""}],
                      "KHP 2026", RB.PDF),
               "rhv_2024": ([{"clinic_id": "RH9", "name": "Reha Neu", "town": "Bad Tölz", "beds": 12}], "RHV 2024", RB.XLSX)}
    result = {"deviations": [
        {"source": PLAN, "id": "16201", "field": "town", "db": None, "source_value": "München", "explained": False},
        {"source": PLAN, "id": "16201", "field": "beds", "db": 690, "source_value": 700, "explained": False},
        {"source": PLAN, "id": "16205", "field": "beds", "db": None, "source_value": 1020, "explained": True}],
        "missing_in_db": [{"source": PLAN, "id": "16107", "name": "ZPG Ingolstadt", "town": "Ingolstadt"},
                          {"source": "rhv_2024", "id": "RH9", "name": "Reha Neu", "town": "Bad Tölz"}],
        "not_in_source": [{"source": PLAN, "id": "26101", "status": RB.GONE, "explained": True},
                          {"source": PLAN, "id": "26105", "status": "Plan-KH", "explained": False}]}
    p = RB.proposals(result, sources, db, "2026-09-29")
    assert sorted(p) == ["16107", "16201", "26105", "RH9"]
    assert {k: v for k, v in p["16201"].items() if k != "_why"} == {"town": "München", "beds": 700}
    assert p["16201"]["_why"]["code"] == "parse_error" and p["16201"]["_why"]["task"] == "TASK-175"
    assert "town, beds" in p["16201"]["_why"]["reason"]
    assert p["16107"]["_insert"] is True and p["16107"]["_why"]["code"] == "parse_error"
    assert (p["16107"]["name"], p["16107"]["beds"], p["16107"]["website"]) == ("ZPG Ingolstadt", 0, None)
    assert p["RH9"]["_insert"] is True and p["RH9"]["_why"]["code"] == "not_in_source"
    assert p["26105"]["status"] == RB.GONE and p["26105"]["_why"]["code"] == "not_in_source"
    assert p["26105"]["source"] == "Krankenhausplan Bayern 2025 (50. Fortschreibung), StMGP | nicht in KHP 2026"
