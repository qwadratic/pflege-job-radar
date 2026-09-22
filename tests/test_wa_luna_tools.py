"""Offline unit tests for app/wa/luna/tools_server.py's tool functions -- called directly, no
MCP protocol, no subprocess. The `@mcp.tool()` decorator does not change the underlying function's
callability, so these are ordinary function calls against a fixture board snapshot.

Proactive-tool-use (does the model actually call one, and only when it should) is covered
separately in tests/test_wa_luna_personas.py, marked ``llm`` since it needs the real CLI.
"""
import asyncio
import json
import time

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from app import data as D
from app.wa import config as C
from app.wa.luna import board_vocabulary as BV
from app.wa.luna import tools_server as TS


def _jobs():
    """enr_housing_evidence is on every snapshot row (app/data.py:_build reads it off the postings table,
    the v_postings view has no such column) -- it is what housing_kind reads, so the fixture carries the
    board's own commonest wording."""
    rows = []
    plan = [("München", "Oberbayern", "Intensiv/IMC", "personalwohnraum (soweit verfügbar)", "c1"),
            ("München", "Oberbayern", "OP", None, "c1"),
            ("Augsburg", "Schwaben", "Innere Medizin", "mitarbeiterwohn", "c2"),
            ("Coburg", "Oberfranken", "Notaufnahme", None, "c3")]
    for i, (city, bezirk, dept, evidence, clinic_id) in enumerate(plan):
        rows.append({"posting_id": i + 1, "title": f"Pflegefachkraft {dept}", "role_class": "pflegefachkraft",
                     "department_hint": dept, "city": city, "clinic_town": city, "regierungsbezirk": bezirk,
                     "clinic_id": clinic_id, "clinic_name": f"Klinikum {city}", "employer": f"Klinikum {city}",
                     "employment_types": ["vollzeit"], "enr_housing": bool(evidence),
                     "enr_housing_evidence": evidence, "enr_childcare": i == 0, "verify_status": "live",
                     "status": "open", "first_published": "2026-09-01", "fresh": True,
                     "external_url": f"https://example.org/job/{i + 1}"})
    return rows


def _clinics():
    """jobs_fresh/jobs_live are on every real clinic row (app/data.py:_build) -- GET /api/cities reads
    them, and board_api_get serves that path (TASK-110)."""
    return [{"clinic_id": "c1", "name": "Klinikum München", "town": "München", "regierungsbezirk": "Oberbayern", "beds": 800, "jobs_open": 2, "jobs_fresh": 2, "jobs_live": 2},
            {"clinic_id": "c2", "name": "Klinikum Augsburg", "town": "Augsburg", "regierungsbezirk": "Schwaben", "beds": 600, "jobs_open": 1, "jobs_fresh": 1, "jobs_live": 1},
            {"clinic_id": "c3", "name": "Klinikum Coburg", "town": "Coburg", "regierungsbezirk": "Oberfranken", "beds": 400, "jobs_open": 1, "jobs_fresh": 1, "jobs_live": 1}]


def board(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": _jobs(), "clinics": _clinics(),
                    "by_clinic": {c["clinic_id"]: c for c in _clinics()}, "facets": {}, "taxonomy": {},
                    "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")


def test_search_postings_filters_by_city_and_logs_the_call(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    out = TS.search_postings(city="München")
    assert {r["city"] for r in out["shown"]} == {"München"}
    assert (len(out["shown"]), out["total"]) == (2, 2)
    log = (C.LUNA_SESSION_DIR / "tool_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(log[-1])["tool"] == "search_postings"
    assert json.loads(log[-1])["args"]["city"] == "München"


def test_search_postings_filters_by_department_and_role(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    out = TS.search_postings(department="Notaufnahme")
    assert out["total"] == 1 and out["shown"][0]["city"] == "Coburg"


def test_search_postings_reads_the_candidates_department_word_in_board_vocabulary(tmp_path, monkeypatch):
    """TASK-96 review: a live persona run called search_postings(city='München', department='Intensivstation'),
    got 0 rows from the exact board filter ('Intensiv/IMC') and told the candidate nothing was open."""
    board(tmp_path, monkeypatch)
    for word in ("Intensivstation", "ITS", "Intensiv/IMC"):
        out = TS.search_postings(city="München", department=word)
        assert [(r["posting_id"], r["department"]) for r in out["shown"]] == [(1, "Intensiv/IMC")], word
    empty = TS.search_postings(city="Augsburg", department="Intensivstation")
    assert (empty["shown"], empty["total"]) == ([], 0)
    log = (C.LUNA_SESSION_DIR / "tool_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(log[0])["args"]["department"] == "Intensivstation", "the call log keeps the model's own word"


def test_search_postings_reads_department_like_market_snapshot(tmp_path, monkeypatch):
    """TASK-104: one reading for both (slots.read_department_pref): a flexible word filters nothing, a word only the
    board's title classifier knows filters to that board department."""
    board(tmp_path, monkeypatch)
    for word in ("egal", "flexibel", "keine Präferenz"):
        assert [r["posting_id"] for r in TS.search_postings(city="München", department=word)["shown"]] == [1, 2], word
    assert [r["posting_id"] for r in TS.search_postings(department="Zentrale Notaufnahme")["shown"]] == [4]
    none_here = TS.search_postings(city="München", department="Stroke Unit")
    assert (none_here["shown"], none_here["total"]) == ([], 0)
    # review 2026-09-15: every department named, not only the first rule that matched
    assert sorted(r["posting_id"] for r in TS.search_postings(department="Innere oder Intensiv")["shown"]) == [1, 3]
    assert [r["posting_id"] for r in TS.search_postings(city="Augsburg", department="Intensiv oder Innere")["shown"]] == [3]


@pytest.mark.parametrize("word", ["alles außer OP", "kein OP", "Intensiv, sonst egal"])
def test_search_postings_with_a_negated_or_flexible_department_is_an_error_the_model_reads(tmp_path, monkeypatch,
                                                                                         word):
    """Review 2026-09-15: 'alles außer OP' and 'kein OP' filtered to OP."""
    board(tmp_path, monkeypatch)
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("search_postings", {"department": word}))
    assert not isinstance(raised.value, UnexpectedToolError)
    assert f"department {word!r} names a department together with a flexible word or a negation" in str(raised.value)


def test_search_postings_with_an_unknown_department_is_an_error_the_model_reads(tmp_path, monkeypatch):
    """TASK-104: a word the board has no department for returned [] and read as 'nothing open there'."""
    board(tmp_path, monkeypatch)
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("search_postings", {"city": "München", "department": "Urologie"}))
    assert not isinstance(raised.value, UnexpectedToolError), "any other exception reaches the model without its text"
    assert "department 'Urologie' is not a board department" in str(raised.value)
    assert "Intensiv/IMC" in str(raised.value)
    log = (C.LUNA_SESSION_DIR / "tool_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(log[-1])["args"]["department"] == "Urologie"


# --- TASK-145: at most five positions per listing turn, and the true number next to them ---------
# Ivan 2026-09-21, from the first real phone-rail conversation: a candidate must never get a wall of
# vacancies. The cap is in the result set, not in the prompt, and the count that did match travels with
# the short list so "und 95 weitere" is sayable.

def _many(n, **row):
    D._snap["jobs"].extend({**D._snap["jobs"][0], "posting_id": 1000 + i, **row} for i in range(n))


def test_a_hundred_matching_postings_hand_over_five_rows_and_the_full_count(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    _many(98, city="München", clinic_town="München")               # 2 München rows already on the board
    out = TS.search_postings(city="München")
    assert len(out["shown"]) == TS.LISTING_LIMIT == 5
    assert out["total"] == 100, "the count is what matched, never what was handed over"
    assert {r["city"] for r in out["shown"]} == {"München"}
    with_flat = TS.search_postings_with_housing(city="München")
    assert len(with_flat["shown"]) == 5 and with_flat["total"] == 99


def test_no_argument_can_raise_the_five(tmp_path, monkeypatch):
    """The cap is not a default the model can talk past: there is no limit parameter to pass any more, and
    asking for one anyway (the CLI drops an argument the schema does not name) still yields five."""
    board(tmp_path, monkeypatch)
    _many(98, city="München", clinic_town="München")
    for tool in ("search_postings", "search_postings_with_housing"):
        assert "limit" not in TS.mcp._tool_manager.get_tool(tool).parameters["properties"], tool
        result = asyncio.run(TS.mcp.call_tool(tool, {"city": "München", "limit": 50}))
        assert not result.is_error, result
        answered = json.loads(result.content[0].text)
        assert len(answered["shown"]) == 5 and answered["total"] >= 99, answered["total"]


def test_the_listing_tools_say_how_many_more_there_are_in_their_own_description(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    TS.apply_board_vocabulary()
    said = TS.mcp._tool_manager.get_tool("search_postings").description
    assert "at most 5 postings" in said and "how many matched in all" in said, said
    assert "never a board link" in said, said


# --- TASK-145: one posting, in full -------------------------------------------------------------
# The model could read a posting's clinic, city and department and nothing else, so everything a
# candidate actually asks ("was muss ich mitbringen", "welcher Tarif", "wie ist die Wohnung") was either
# unanswerable or invented. app/data.py:JOB_COLS does not carry the ad text at all; job_detail does.

def _detail_row(posting_id):
    """What app/data.py:job_detail reads off the postings row itself (select=*), the columns
    sql/001_schema.sql + pflege_jobs/schema.py define and JOB_COLS leaves out."""
    return {"posting_id": posting_id, "description": "Wir suchen ... Bewerbung an pd@klinikum.example",
            "enr_requirements": "Examen, Berufserlaubnis", "enr_experience": "2 Jahre Intensiv",
            "enr_language_req": "B2", "enr_tariff": "TVöD", "enr_pay_grade": "P8",
            "enr_housing_evidence": "Personalwohnung nach Verfügbarkeit", "shift_night_weekend": None,
            "start_date": "2026-11-01", "contract": "UNBEFRISTET", "qualification_hint": "examiniert",
            "enr_contact_emails": ["pd@klinikum.example"]}


def test_get_posting_returns_the_whole_ad_not_a_search_row(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    monkeypatch.setattr(D, "job_detail", _detail_row)
    out = TS.get_posting(posting_id=1)
    assert out["city"] == "München" and out["clinic_name"] == "Klinikum München"
    assert all(k in out for k in TS.POSTING_DETAIL_FIELDS), TS.POSTING_DETAIL_FIELDS
    assert (out["enr_requirements"], out["enr_language_req"], out["enr_tariff"], out["enr_pay_grade"]) == \
        ("Examen, Berufserlaubnis", "B2", "TVöD", "P8")
    assert out["enr_housing_evidence"] == "Personalwohnung nach Verfügbarkeit"
    assert out["start_date"] == "2026-11-01"
    # the fields the description promises are the fields it returns, and nothing personal comes with them
    for field in ("description", "enr_requirements", "enr_experience", "enr_language_req",
                  "enr_tariff", "enr_pay_grade", "enr_housing_evidence"):
        assert field in TS.mcp._tool_manager.get_tool("get_posting").description, field
    assert "pd@klinikum.example" not in json.dumps(out, ensure_ascii=False)
    assert D.EMAIL_MASK in out["description"], "the ad text is scrubbed like every other door here"
    assert "enr_contact_emails" not in out


def test_get_posting_is_null_only_for_a_posting_that_does_not_exist(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    monkeypatch.setattr(D, "job_detail", _detail_row)
    assert TS.get_posting(posting_id=4242) is None


def test_list_clinics_filters_by_region(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    out = TS.list_clinics(regierungsbezirk="Schwaben")
    assert [c["clinic_id"] for c in out] == ["c2"]


def test_get_clinic_contact_returns_none_when_the_contacts_module_is_unavailable(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    monkeypatch.setattr(TS, "CT", None)
    assert TS.get_clinic_contact(clinic_id="c1") is None


def test_get_clinic_contact_reads_a_real_saved_contact_end_to_end(tmp_path, monkeypatch):
    """No mocking of TASK-64's contacts module: a real save through app.wa.luna.contacts, read
    back through the tool function's own app.wa.store.db() connection -- the actual round trip an
    MCP tool call makes, not just the fake-delegate path below."""
    board(tmp_path, monkeypatch)
    from app.wa.luna import contacts as CT

    conn = CT.db()
    CT.save_contact(conn, "c1", "pd@klinikum-muenchen.example", "board", "high")
    conn.close()

    out = TS.get_clinic_contact(clinic_id="c1")
    assert out["email"] == "pd@klinikum-muenchen.example"
    assert out["source"] == "board"
    assert TS.get_clinic_contact(clinic_id="c-unknown") is None


def test_get_clinic_contact_delegates_to_the_contacts_module_when_present(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)

    class _FakeConn:
        def close(self):
            pass

    class _FakeContacts:
        def db(self):
            return _FakeConn()

        def get_contact(self, conn, clinic_id):
            return {"email": "pd@klinikum-muenchen.example", "source": "board", "confidence": "high"}

    monkeypatch.setattr(TS, "CT", _FakeContacts())
    out = TS.get_clinic_contact(clinic_id="c1")
    assert out["email"] == "pd@klinikum-muenchen.example"


def test_every_call_is_logged_even_when_the_result_is_empty(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    TS.search_postings(city="Coburg", department="Intensiv")   # a town the board has, nothing matching in it
    TS.list_clinics(city="Coburg", regierungsbezirk="Schwaben")
    TS.get_posting(posting_id=42)
    TS.search_postings_with_housing(city="Coburg")
    TS.list_clinics_with_housing(city="Coburg")
    TS.list_cities_with_postings(regierungsbezirk="Nirgendwo")
    TS.count_postings(city="Coburg", housing=True)
    TS.board_api_get(path="/api/jobs", query="city=Coburg&housing=1")
    TS.read_board_docs(topic="skill")
    lines = (C.LUNA_SESSION_DIR / "tool_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()
    tools_called = [json.loads(l)["tool"] for l in lines]
    assert tools_called == ["search_postings", "list_clinics", "get_posting", "search_postings_with_housing",
                            "list_clinics_with_housing", "list_cities_with_postings", "count_postings",
                            "board_api_get", "read_board_docs"]


# --- TASK-110: the board's own vocabulary, in the tool descriptions the model reads -------------
# Ivan 2026-09-16: the housing filter existed for a whole task and the model never used it -- the schema
# named the parameters and nothing said what values they take or how much of the board each covers.

def _description(name):
    return TS.mcp._tool_manager.get_tool(name).description


def test_the_tool_descriptions_carry_the_boards_own_values_and_counts(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    TS.apply_board_vocabulary()
    said = _description("search_postings")
    for value in ("Intensiv/IMC 1", "Notaufnahme 1", "Oberbayern 2", "Schwaben 1", "pflegefachkraft 4", "vollzeit 4"):
        assert value in said, f"{value!r} missing from the search_postings description: {said!r}"
    assert "enr_housing" in said and "2 of 4 postings (50%)" in said, said
    assert "4 live-verified of 4 open postings at 3 clinics in 3 cities" in said, said


def test_each_tool_carries_only_the_vocabulary_of_its_own_filters(tmp_path, monkeypatch):
    """These descriptions go to the model on every single turn -- a housing tool has no role_class
    parameter and must not spend tokens listing role classes."""
    board(tmp_path, monkeypatch)
    TS.apply_board_vocabulary()
    housing = _description("search_postings_with_housing")
    assert "2 of 4 postings (50%)" in housing and "Intensiv/IMC 1" in housing and "Oberbayern 2" in housing
    assert "role_class:" not in housing and "employment_type:" not in housing, housing
    assert "role_class:" in _description("count_postings")
    assert _description("read_board_docs").count("\n") == 0, "a tool with no board filters carries no vocabulary"


def test_the_vocabulary_follows_the_board_and_is_not_a_list_in_the_file(tmp_path, monkeypatch):
    """The point of generating it: a board that changes changes the description, with no edit here."""
    board(tmp_path, monkeypatch)
    TS.apply_board_vocabulary()
    assert "OP 1" in _description("search_postings")
    D._snap["jobs"] = [j for j in D._snap["jobs"] if j["department_hint"] != "OP"]
    for job in D._snap["jobs"]:
        job["enr_housing"] = True
    TS.apply_board_vocabulary()
    said = _description("search_postings")
    assert "OP 1" not in said and "Intensiv/IMC 1" in said
    assert "3 of 3 postings (100%)" in said, said


def test_a_department_value_no_filter_can_apply_is_counted_not_advertised(tmp_path, monkeypatch):
    """The board carries a handful of department_hint values the filter has no word for ('Pflege',
    'Berufsfachschule für Pflege' live 2026-09-16). Offering them as filter values would be a dead end;
    hiding them silently would misstate what a department filter drops."""
    board(tmp_path, monkeypatch)
    D._snap["jobs"].append({**D._snap["jobs"][0], "posting_id": 99,
                            "department_hint": "Berufsfachschule für Pflege"})
    TS.apply_board_vocabulary()
    said = _description("search_postings")
    assert "Berufsfachschule" not in said, said
    assert "1 of 5 postings carry no filterable department" in said, said


@pytest.mark.parametrize("break_it, expected", [
    (lambda: D._snap.update(jobs=[]), "no live-verified open posting"),
    (lambda: D._snap.update(jobs=[], error="HTTPError: 401 Client Error: Unauthorized"), "401"),
], ids=["empty_board", "failed_board"])
def test_a_board_that_did_not_load_stops_the_server_instead_of_serving_an_empty_vocabulary(
        tmp_path, monkeypatch, break_it, expected):
    """CLAUDE.md: fail loudly. A tools server whose board is empty answers every question with
    'nothing found' -- the exact silent wrong answer this task exists to remove. The message names the
    snapshot's own error when it has one."""
    board(tmp_path, monkeypatch)
    break_it()
    with pytest.raises(RuntimeError) as raised:
        TS.apply_board_vocabulary()
    assert expected in str(raised.value)


def test_a_failed_refresh_over_a_cached_board_still_serves_that_boards_vocabulary(tmp_path, monkeypatch):
    """TASK-110 review: app/data.py:refresh keeps serving the cached board when a refresh fails (it only
    records the error and retries in a minute), and market_snapshot answers this same turn from those
    cached rows. Raising on the error flag alone would fail turns over a board that is right there."""
    board(tmp_path, monkeypatch)
    D._snap.update(error="HTTPError: 401 Client Error: Unauthorized")
    TS.apply_board_vocabulary()
    assert "4 live-verified of 4 open postings" in _description("search_postings")


# --- TASK-110: purpose-built tools, the filter already preset -----------------------------------

def test_search_postings_with_housing_returns_only_postings_the_board_marks(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    out = TS.search_postings_with_housing()
    assert sorted(r["posting_id"] for r in out["shown"]) == [1, 3] and out["total"] == 2
    assert all(r["housing"] is True for r in out["shown"])
    assert [r["posting_id"] for r in TS.search_postings_with_housing(city="München")["shown"]] == [1]
    coburg = TS.search_postings_with_housing(city="Coburg")
    assert (coburg["shown"], coburg["total"]) == ([], 0), "Coburg's posting carries no housing mark"
    assert [r["posting_id"] for r in TS.search_postings_with_housing(regierungsbezirk="Schwaben")["shown"]] == [3]
    logged = [json.loads(l) for l in
              (C.LUNA_SESSION_DIR / "tool_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()]
    assert {l["tool"] for l in logged} == {"search_postings_with_housing"}


def test_search_postings_with_housing_reads_the_candidates_department_word(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    assert [r["posting_id"] for r in TS.search_postings_with_housing(department="Intensivstation")["shown"]] == [1]
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("search_postings_with_housing", {"department": "Urologie"}))
    assert not isinstance(raised.value, UnexpectedToolError)
    assert "department 'Urologie' is not a board department" in str(raised.value)


def test_list_clinics_with_housing_counts_the_marked_postings_per_clinic(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    D._snap["jobs"].append({**D._snap["jobs"][0], "posting_id": 5})      # a second housing posting at c1
    out = TS.list_clinics_with_housing()
    assert [(c["clinic_id"], c["postings_with_housing"]) for c in out] == [("c1", 2), ("c2", 1)]
    assert out[0]["city"] == "München" and out[0]["regierungsbezirk"] == "Oberbayern"
    assert [c["clinic_id"] for c in TS.list_clinics_with_housing(regierungsbezirk="Schwaben")] == ["c2"]
    assert TS.list_clinics_with_housing(city="Coburg") == []


def test_list_cities_with_postings_ranks_cities_and_takes_the_presets(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    assert [(c["city"], c["postings"], c["clinics"]) for c in TS.list_cities_with_postings()] == [
        ("München", 2, 1), ("Augsburg", 1, 1), ("Coburg", 1, 1)]
    assert [c["city"] for c in TS.list_cities_with_postings(housing=True)] == ["Augsburg", "München"]
    assert [c["city"] for c in TS.list_cities_with_postings(department="Notaufnahme")] == ["Coburg"]
    assert [c["city"] for c in TS.list_cities_with_postings(regierungsbezirk="Oberbayern")] == ["München"]
    assert TS.list_cities_with_postings(department="ITS", housing=True)[0] == {
        "city": "München", "regierungsbezirk": "Oberbayern", "postings": 1, "clinics": 1}


def test_count_postings_counts_rows_clinics_cities_and_the_ones_with_a_flat(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    assert TS.count_postings(city="München") == {
        "postings": 2, "clinics": 1, "cities": 1, "with_housing": 1, "with_accommodation": 1,
        "with_relocation_support": 0, "filters": {},
        "town": {"asked": "München", "board_spellings": ["München"], "matched_as": "town"}}
    assert TS.count_postings(housing=True) == {"postings": 2, "clinics": 2, "cities": 2, "with_housing": 2,
                                               "with_accommodation": 2, "with_relocation_support": 0,
                                               "filters": {"housing": "1"}}
    counted = TS.count_postings(city="Coburg")
    assert (counted["postings"], counted["with_housing"]) == (1, 0), "open in Coburg, none of it with a flat"
    assert TS.count_postings(housing=True)["filters"] == {"housing": "1"}
    assert TS.count_postings(department="ITS")["filters"] == {"department_hint": "Intensiv/IMC"}


def test_count_postings_without_a_single_filter_says_where_that_number_already_is(tmp_path, monkeypatch):
    """TASK-110 review: live runs spent ~2s of a turn calling this with every parameter empty to re-derive
    market_snapshot.open_jobs, and saying so in the prompt did not hold across runs (3 llm runs
    2026-09-16: two of them did it anyway). The refusal names where the number is, so the turn answers."""
    board(tmp_path, monkeypatch)
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("count_postings", {}))
    assert not isinstance(raised.value, UnexpectedToolError)
    assert "market_snapshot.open_jobs" in str(raised.value) and "needs at least one filter" in str(raised.value)
    logged = [json.loads(l) for l in
              (C.LUNA_SESSION_DIR / "tool_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()]
    assert [l["tool"] for l in logged] == ["count_postings"], "the refused call is still in the log"


# --- TASK-110: the fallback -- the board's own docs, and an allowlist of public GET paths -------

def test_board_api_get_answers_an_allowlisted_path_with_the_apis_own_envelope(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    out = TS.board_api_get(path="/api/jobs", query="city=München&limit=1")
    assert (out["total"], out["limit"], out["offset"], out["next_offset"]) == (2, 1, 0, 1)
    assert [r["posting_id"] for r in out["rows"]] == [1]
    assert TS.board_api_get(path="/api/jobs", query="housing=1")["total"] == 2
    assert json.loads((C.LUNA_SESSION_DIR / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()[0]) \
        ["args"] == {"path": "/api/jobs", "query": "city=München&limit=1"}


def test_board_api_get_serves_the_rest_of_the_allowlist(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    assert [c["clinic_id"] for c in TS.board_api_get(path="/api/clinics", query="city=Augsburg")["rows"]] == ["c2"]
    detail = TS.board_api_get(path="/api/clinics/c1")
    assert detail["name"] == "Klinikum München" and [j["posting_id"] for j in detail["jobs"]] == [1, 2]
    assert detail["runs"] == [], "crawl runs are owner-only on the app API and stay that way here"
    assert [c["city"] for c in TS.board_api_get(path="/api/cities")] == ["München", "Augsburg", "Coburg"]
    fuzzy = TS.board_api_get(path="/api/search", query="q=Klinkum Münchn")     # typo, on purpose
    assert fuzzy["clinics"][0]["clinic_id"] == "c1", fuzzy
    assert TS.board_api_get(path="/api/facets") == {}
    assert isinstance(TS.board_api_get(path="/api/taxonomy"), dict)
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("board_api_get", {"path": "/api/clinics/nope"}))
    assert "unknown clinic" in str(raised.value)


@pytest.mark.parametrize("path", ["/api/settings", "/api/wa/threads", "/api/crawl/runs", "/api/jobs/1",
                                  "/api/clinics/c1/jobs", "../../../etc/passwd", ""])
def test_board_api_get_refuses_every_path_off_the_allowlist(tmp_path, monkeypatch, path):
    """The whole point of the allowlist: this tool reaches the public board and nothing else -- no ops
    route, no WhatsApp route, no write, no file."""
    board(tmp_path, monkeypatch)
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("board_api_get", {"path": path}))
    assert not isinstance(raised.value, UnexpectedToolError), "the model must read why it was refused"
    assert "is not a board API path you may call" in str(raised.value)
    assert "/api/jobs" in str(raised.value), "the refusal names what is allowed"


def test_board_api_get_hands_over_no_personal_data(tmp_path, monkeypatch):
    """The app API nulls enr_contact_emails and masks addresses in the text below a member session
    (app/data.py:redact). This door is the same door -- Luna is no member."""
    board(tmp_path, monkeypatch)
    D._snap["jobs"][3].update({"enr_contact_emails": ["pd@klinikum-coburg.example"],
                               "description": "Bewerbung an pd@klinikum-coburg.example"})
    out = TS.board_api_get(path="/api/jobs", query="city=Coburg")
    assert out["rows"][0]["enr_contact_emails"] is None
    assert "pd@klinikum-coburg.example" not in json.dumps(out, ensure_ascii=False)
    assert D.EMAIL_MASK in out["rows"][0]["description"]


def test_board_api_get_turns_the_apis_own_400_into_an_error_the_model_reads(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("board_api_get", {"path": "/api/jobs", "query": "limit=abc"}))
    assert not isinstance(raised.value, UnexpectedToolError)
    assert "limit must be an integer" in str(raised.value)


def test_read_board_docs_serves_this_repos_own_agent_documentation(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    for topic, path in TS.BOARD_DOCS.items():
        out = TS.read_board_docs(topic=topic)
        assert out["path"] == path
        assert out["text"] == TS._strip_secrets((TS._REPO_ROOT / path).read_text(encoding="utf-8"))
    assert "department_hint" in TS.read_board_docs(topic="api")["text"]
    assert "enr_housing" in TS.read_board_docs(topic="skill")["text"]
    assert TS.read_board_docs()["path"] == "skill/SKILL.md"


def test_read_board_docs_refuses_an_unknown_topic(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("read_board_docs", {"topic": "../../.env"}))
    assert not isinstance(raised.value, UnexpectedToolError)
    assert "unknown docs topic" in str(raised.value) and "data-model" in str(raised.value)


def test_read_board_docs_never_hands_the_published_anon_key_to_a_candidate_facing_model(tmp_path, monkeypatch):
    """skill/SKILL.md publishes the anon Supabase key on purpose. Luna has no network and no shell, so the
    key buys her nothing -- and a model holding a credential can put it in a WhatsApp bubble."""
    board(tmp_path, monkeypatch)
    raw = (TS._REPO_ROOT / "skill" / "SKILL.md").read_text(encoding="utf-8")
    key = TS._JWT_RE.search(raw)
    assert key, "skill/SKILL.md no longer carries the key this test is about"
    served = TS.read_board_docs(topic="skill")["text"]
    assert key.group(0) not in served and "eyJ" not in served
    assert TS.SECRET_MASK in TS._strip_secrets("token eyJabcdefgh.ijklmnopq.rstuvwxyz1 here")


def test_read_board_docs_hands_over_no_host_project_or_key_audit(tmp_path, monkeypatch):
    """TASK-110 review: masking the key alone left the board host (5x), the Supabase project ref (4x) and
    the whole audit of what that key opens in a candidate-facing model's context -- the same threat model
    that strips the key (prompts.py IDENTITY forbids naming any brand, site or product; TASK-100: asked
    who we are, the model named the repo). None of it answers a board question."""
    board(tmp_path, monkeypatch)
    raw = (TS._REPO_ROOT / "skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "pflege-board.exe.xyz" in raw and "klkxfvieaxpjlplloljn" in raw, "the doc no longer has what this is about"
    for topic in TS.BOARD_DOCS:
        served = TS.read_board_docs(topic=topic)["text"]
        for leak in ("pflege-board.exe.xyz", "klkxfvieaxpjlplloljn", "https://", "http://", "supabase.co"):
            assert leak not in served, f"{leak!r} reached the model through read_board_docs({topic!r})"
    skill = TS.read_board_docs(topic="skill")["text"]
    assert not skill.startswith("---"), "the YAML front matter names the host and the product"
    for audit in ("anon key (published on purpose)", "CAN read every table in schema",
                  "INSERT into `pflege_jobs.inbox` is not ruled out", "No quota protection"):
        assert audit not in skill, audit
    assert TS.SECTION_MASK in skill and TS.URL_MASK in skill, "what was dropped is said, not silently cut"
    # still the reference the fallback exists for
    assert "enr_housing" in skill and "department_hint" in TS.read_board_docs(topic="api")["text"]


def test_every_tool_luna_may_call_is_a_real_read_only_tool_and_contacts_are_not_among_them(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    from app.wa import luna_brain as LB

    allowed = {name.split("__")[-1] for name in LB.MCP_TOOL_NAMES}
    assert allowed <= set(TS.mcp._tool_manager._tools), "the CLI allowlist names a tool the server does not serve"
    assert {"search_postings_with_housing", "list_clinics_with_housing", "list_cities_with_postings",
            "count_postings", "read_board_docs", "board_api_get"} <= allowed
    # TASK-91: contact details belong to the human handoff after consent, never to the conversation.
    assert "get_clinic_contact" in TS.mcp._tool_manager._tools and "get_clinic_contact" not in allowed
    # TASK-145: this one IS for the conversation. It is registered here; the CLI reaches it only once
    # luna_brain.MCP_TOOL_NAMES names it and _mcp_config_path passes WA_LUNA_PHONE (that file is another
    # lane's -- until it does, the tool is served and never called).
    assert "match_cv_to_postings" in TS.mcp._tool_manager._tools


# --- TASK-110 review: the vocabulary is counted in the parent, the server only applies it --------
# Ivan's fix round 2026-09-16: counting the board inside this server meant a cold Supabase build
# (8-17s measured) between the CLI spawning it and the MCP handshake, on every turn, under the CLI's
# 30s connect deadline -- and nothing upstream could see that deadline being missed.

def test_the_server_applies_the_vocabulary_the_parent_counted_without_touching_the_board(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    lines = BV.vocabulary_lines()                       # what luna_brain writes, from its warm snapshot
    path = tmp_path / "board_vocabulary.json"
    path.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("WA_LUNA_BOARD_VOCABULARY", str(path))
    monkeypatch.setattr(D, "snapshot", lambda *a, **k: pytest.fail("the server must not build a board at start"))

    TS.apply_board_vocabulary()
    assert "4 live-verified of 4 open postings at 3 clinics in 3 cities" in _description("search_postings")
    assert "2 of 4 postings (50%)" in _description("search_postings_with_housing")


def test_a_vocabulary_file_missing_a_line_is_a_loud_failure_not_a_tool_without_values(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    path = tmp_path / "board_vocabulary.json"
    path.write_text(json.dumps({"board": "BOARD NOW: ..."}), encoding="utf-8")
    monkeypatch.setenv("WA_LUNA_BOARD_VOCABULARY", str(path))
    with pytest.raises(RuntimeError) as raised:
        TS.apply_board_vocabulary()
    assert "carries no vocabulary line for" in str(raised.value) and "housing" in str(raised.value)


def test_serving_stamps_the_readiness_file_the_parent_checks(tmp_path, monkeypatch):
    """A tools server that dies at start, or that the CLI drops, leaves claude -p exiting 0 with a
    normal-looking reply and no MCP status anywhere in its output -- this stamp is the only signal
    luna_brain._live_reply has that the turn actually had the board tools."""
    board(tmp_path, monkeypatch)
    ready = tmp_path / "tools_ready" / "turn.json"
    monkeypatch.setenv("WA_LUNA_TOOLS_READY", str(ready))
    ran = []
    monkeypatch.setattr(TS.mcp, "run", lambda **kw: ran.append(kw))

    TS.serve()
    assert ran == [{"transport": "stdio"}]
    stamped = json.loads(ready.read_text(encoding="utf-8"))
    assert "search_postings_with_housing" in stamped["tools"] and stamped["pid"] > 0
    assert stamped["at"] <= time.time()


def test_a_board_that_did_not_load_leaves_no_readiness_stamp(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    D._snap.update(jobs=[])
    ready = tmp_path / "tools_ready" / "turn.json"
    monkeypatch.setenv("WA_LUNA_TOOLS_READY", str(ready))
    monkeypatch.setattr(TS.mcp, "run", lambda **kw: pytest.fail("a server with no vocabulary must not serve"))
    with pytest.raises(RuntimeError):
        TS.serve()
    assert not ready.exists(), "the parent must be able to see that this turn had no tools"


# --- TASK-110 review: one clinic identity, and the fallback door bounded and on the same base ----

def _housing_board(tmp_path, monkeypatch):
    """The live board's two awkward shapes at once (2026-09-16): 26 employer names carry no clinic_id,
    and one name spans two clinic_ids. Counting by name and counting by clinic_id then disagree --
    live: 48 housing clinics in the generated line vs 51 from the tools."""
    board(tmp_path, monkeypatch)
    D._snap["jobs"].extend([
        {**D._snap["jobs"][0], "posting_id": 10, "clinic_id": "c9", "clinic_name": "Klinikum München",
         "employer": "Klinikum München", "city": "Dachau", "clinic_town": "Dachau"},
        {**D._snap["jobs"][0], "posting_id": 11, "clinic_id": None, "clinic_name": None,
         "employer": "Pflegezentrum Ohne Register", "city": "Passau", "clinic_town": "Passau"},
    ])


def test_clinics_with_a_flat_are_counted_the_same_way_in_the_schema_and_in_every_tool(tmp_path, monkeypatch):
    _housing_board(tmp_path, monkeypatch)
    TS.apply_board_vocabulary()
    counted = TS.count_postings(housing=True)["clinics"]
    listed = len(TS.list_clinics_with_housing(limit=50))
    assert counted == listed == 4, (
        "c1 + c2 + the second site sharing the München name + the employer with no clinic_id")
    assert f"at {counted} clinics" in _description("search_postings_with_housing")
    assert sum(c["postings_with_housing"] for c in TS.list_clinics_with_housing(limit=50)) == \
        TS.count_postings(housing=True)["postings"]
    entries = {c["clinic_name"]: c for c in TS.list_clinics_with_housing(limit=50)}
    assert entries["Pflegezentrum Ohne Register"]["clinic_id"] is None, "an unlinked employer is its own clinic"
    assert {c["city"] for c in TS.list_clinics_with_housing(limit=50) if c["clinic_name"] == "Klinikum München"} == \
        {"München", "Dachau"}, "two sites of one name are two clinics, each with its own city"


def test_board_api_get_serves_the_same_re_verified_rows_as_every_other_tool(tmp_path, monkeypatch):
    """TASK-110 review: this door served every open posting while the tools served the re-verified ones
    -- live, that was Coburg 2 vs 39 and München 369 vs 499 in one conversation. TASK-145 (Ivan,
    2026-09-21) removed the deliberate way past that base: a posting whose liveness is not confirmed may
    not reach a candidate-facing model at all, and one escape hatch is the whole guarantee gone."""
    board(tmp_path, monkeypatch)
    D._snap["jobs"].append({**D._snap["jobs"][3], "posting_id": 20, "verify_status": "gone"})
    assert TS.count_postings(city="Coburg")["postings"] == 1
    out = TS.board_api_get(path="/api/jobs", query="city=Coburg")
    assert out["total"] == 1, "a posting the verifier last found gone is not named as open"
    assert out["withheld_not_live"] == 1, "and the one it kept back is said, not silently dropped"
    assert [j["posting_id"] for j in out["rows"]] == [4]
    for query in ("city=Coburg&verify=gone", "city=Coburg&verify=live,gone", "verify=live"):
        with pytest.raises(ToolError) as raised:
            asyncio.run(TS.mcp.call_tool("board_api_get", {"path": "/api/jobs", "query": query}))
        assert not isinstance(raised.value, UnexpectedToolError)
        assert "is not yours to set" in str(raised.value), query
    detail = TS.board_api_get(path="/api/clinics/c3")
    assert [j["posting_id"] for j in detail["jobs"]] == [4] and detail["jobs_total"] == 1, (
        "the clinic detail's own job list starts from that base too")
    assert detail["jobs_withheld_not_live"] == 1


def test_board_api_get_asks_for_a_page_it_can_actually_hand_over(tmp_path, monkeypatch):
    """The CLI truncates an MCP result over MAX_MCP_OUTPUT_TOKENS (25000 by default): the app API's own
    default page (200 jobs = 408,851 chars live) came back to the model cut mid-row or replaced by a file
    path it cannot read. A limit above the maximum is a loud error naming it, never a quietly cut page."""
    board(tmp_path, monkeypatch)
    for _ in range(60):
        D._snap["jobs"].append({**D._snap["jobs"][0], "posting_id": 100 + _})
    out = TS.board_api_get(path="/api/jobs")
    assert len(out["rows"]) == TS.BOARD_API_MAX_ROWS == 25
    assert out["total"] == 64 and out["limit"] == 25 and out["next_offset"] == 25, (
        "the full number is still in the envelope, so a bounded page can never read as the whole board")
    assert len(TS.board_api_get(path="/api/jobs", query="limit=5&offset=60")["rows"]) == 4
    assert len(TS.board_api_get(path="/api/clinics")["rows"]) == 3
    for path, query in [("/api/jobs", "limit=999999"), ("/api/clinics", "limit=200"),
                        ("/api/search", "q=Klinikum&limit=26")]:
        with pytest.raises(ToolError) as raised:
            asyncio.run(TS.mcp.call_tool("board_api_get", {"path": path, "query": query}))
        assert not isinstance(raised.value, UnexpectedToolError)
        assert "is more than this tool serves: 25 rows per call" in str(raised.value), (path, query)
        assert "count_postings" in str(raised.value), "the refusal names the way to get the number"


def test_one_clinics_postings_are_bounded_too_with_the_real_number_next_to_them(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    for _ in range(60):
        D._snap["jobs"].append({**D._snap["jobs"][0], "posting_id": 200 + _})
    detail = TS.board_api_get(path="/api/clinics/c1")
    assert detail["jobs_total"] == 62 and len(detail["jobs"]) == TS.BOARD_API_MAX_ROWS == 25


def test_the_fallback_door_says_which_columns_the_board_barely_fills(tmp_path, monkeypatch):
    """TASK-110 review: board_api_get reaches filters no tool presets (contract, enr_tariff, …) with no
    values anywhere. Live, `contract` is set on 16 of 2624 live-verified postings and UNBEFRISTET does
    not exist at all, so `contract=UNBEFRISTET` answers total=0 and reads as "we have no permanent
    positions" -- a false statement from the fallback added to prevent false statements."""
    board(tmp_path, monkeypatch)
    for i in range(16):                                    # a board where contract is the rarity it is live
        D._snap["jobs"].append({**D._snap["jobs"][0], "posting_id": 300 + i})
    D._snap["jobs"][0]["contract"] = "BEFRISTET"
    for job in D._snap["jobs"]:
        job["enr_tariff"] = "TVöD"
    TS.apply_board_vocabulary()
    said = _description("board_api_get")
    assert "contract 1 (BEFRISTET 1)" in said, said         # rare enough that the values are named
    assert "enr_tariff 20" in said and "TVöD" not in said, said
    assert "never turn such a 0 into 'we have none'" in said, said
    assert TS.board_api_get(path="/api/jobs", query="contract=UNBEFRISTET")["total"] == 0, (
        "the value the docs list is a legal one the board simply does not carry")


# --- TASK-145: the candidate's own word for a town ----------------------------------------------
# Ivan 2026-09-21: app/data.py:filter_jobs compares the city lowercased and exactly, so 'Nuernberg',
# 'Wuerzburg' and a Landkreis each matched nothing and the turn said there was nothing open there --
# the failure TASK-104 removed for the department word, on the slot candidates lead with.

@pytest.mark.parametrize("word", ["Nuernberg", "Nurnberg", "NÜRNBERG", "Landkreis Nürnberg", "nürnberg"])
def test_a_city_spelt_the_candidates_way_reaches_the_boards_own_town(tmp_path, monkeypatch, word):
    board(tmp_path, monkeypatch)
    D._snap["jobs"].append({**D._snap["jobs"][0], "posting_id": 30, "city": "Nürnberg",
                            "clinic_town": "Nürnberg", "regierungsbezirk": "Mittelfranken"})
    assert [r["posting_id"] for r in TS.search_postings(city=word)["shown"]] == [30], word
    assert TS.count_postings(city=word)["postings"] == 1


def test_a_town_the_board_does_not_know_is_an_error_naming_the_near_ones_not_an_empty_list(tmp_path, monkeypatch):
    """The whole point: [] and "never heard of that town" are the same value to the model, and only one
    of them may be told to the candidate."""
    board(tmp_path, monkeypatch)
    D._snap["jobs"].append({**D._snap["jobs"][0], "posting_id": 30, "city": "Nürnberg",
                            "clinic_town": "Nürnberg", "regierungsbezirk": "Mittelfranken"})
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("search_postings", {"city": "Nuremberg"}))
    assert not isinstance(raised.value, UnexpectedToolError), "the model must read why it got nothing"
    said = str(raised.value)
    assert "'Nuremberg' is not a town this board has open postings in" in said
    assert "Nürnberg" in said and "a DIFFERENT town" in said, said
    assert "list_cities_with_postings" in said
    assert json.loads((C.LUNA_SESSION_DIR / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()[-1]) \
        ["args"]["city"] == "Nuremberg", "the refused call keeps the model's own word"


def test_a_town_nothing_on_the_board_resembles_says_so_instead_of_offering_another(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("search_postings_with_housing", {"city": "Kyiv"}))
    assert "No board town resembles it" in str(raised.value) and "Bavaria only" in str(raised.value)


def test_every_door_that_takes_a_town_reads_it_the_same_way(tmp_path, monkeypatch):
    """Including the raw API door: it would otherwise be the way around the reading, with 'city=Nuernberg'
    answering total=0 again."""
    board(tmp_path, monkeypatch)
    assert [c["clinic_id"] for c in TS.list_clinics(city="Muenchen")] == ["c1"]
    assert [c["clinic_id"] for c in TS.list_clinics_with_housing(city="Muenchen")] == ["c1"]
    assert TS.board_api_get(path="/api/jobs", query="city=Muenchen")["total"] == 2
    assert [c["clinic_id"] for c in TS.board_api_get(path="/api/clinics", query="city=Muenchen")["rows"]] == ["c1"]
    for tool, args in (("list_clinics", {"city": "Nuremberg"}),
                       ("list_clinics_with_housing", {"city": "Nuremberg"}),
                       ("count_postings", {"city": "Nuremberg"}),
                       ("board_api_get", {"path": "/api/jobs", "query": "city=Nuremberg"}),
                       ("board_api_get", {"path": "/api/clinics", "query": "city=Nuremberg"})):
        with pytest.raises(ToolError) as raised:
            asyncio.run(TS.mcp.call_tool(tool, args))
        assert not isinstance(raised.value, UnexpectedToolError)
        assert "is not a town this board has" in str(raised.value), (tool, args)


def test_the_tool_descriptions_say_how_a_town_word_is_read(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    TS.apply_board_vocabulary()
    said = TS.mcp._tool_manager.get_tool("search_postings").description
    assert "city: pass the candidate's own word" in said and "3 cities that carry postings" in said, said
    assert "never means an unrecognised city" in said, said
    assert "city:" not in TS.mcp._tool_manager.get_tool("list_cities_with_postings").description, \
        "a tool with no city parameter does not spend a line on one"


# --- TASK-145: a posting whose liveness is not confirmed reaches nothing -------------------------

def test_no_tool_hands_over_a_posting_the_verifier_no_longer_confirms(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    monkeypatch.setattr(D, "job_detail", _detail_row)
    gone = {**D._snap["jobs"][0], "posting_id": 50, "city": "Coburg", "clinic_town": "Coburg",
            "clinic_id": "c3", "clinic_name": "Klinikum Coburg", "employer": "Klinikum Coburg",
            "regierungsbezirk": "Oberfranken", "verify_status": "gone"}
    D._snap["jobs"].append(gone)
    found = [TS.search_postings(city="Coburg")["shown"], TS.search_postings_with_housing(city="Coburg")["shown"],
             TS.list_clinics_with_housing(city="Coburg"), TS.board_api_get(path="/api/jobs", query="city=Coburg")["rows"]]
    assert all(50 not in [r.get("posting_id") for r in rows] for rows in found), found
    assert TS.count_postings(city="Coburg")["postings"] == 1
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("get_posting", {"posting_id": 50}))
    assert not isinstance(raised.value, UnexpectedToolError)
    assert "is withheld" in str(raised.value) and "no longer confirms it is live" in str(raised.value)
    assert TS.get_posting(posting_id=4242) is None, "withheld and non-existent are different answers"


# --- TASK-145: the candidate's own CV, ranked against what is open right now ---------------------
# app/cv.py:match() has ranked postings against a CV since TASK-65, but only after consent, on the
# handover path -- so the conversation itself could never answer "welche davon passt zu meinem Lebenslauf".

_CV_PHONE = "491700000000"        # a test number, never a candidate's
_CV_TEXT = ("Lebenslauf\nGesundheits- und Krankenpflegerin\n2018-2026 Intensivstation, München\n"
            "Deutsch B2\nFachweiterbildung Intensivpflege")


def _with_stored_cv(monkeypatch, text=_CV_TEXT, phone=_CV_PHONE):
    from app.wa import store as ST

    conn = ST.db()
    conn.execute("insert into wa_threads (phone, opened_at, slots) values (?,?,?)",
                 (phone, "2026-09-21T08:00:00Z", json.dumps({"cv_text": text}, ensure_ascii=False)))
    conn.commit()
    conn.close()
    monkeypatch.setenv("WA_LUNA_PHONE", phone)


def test_match_cv_to_postings_ranks_the_stored_cv_against_the_live_board(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    _with_stored_cv(monkeypatch)
    D._snap["jobs"].append({**D._snap["jobs"][0], "posting_id": 60, "verify_status": "gone"})

    out = TS.match_cv_to_postings()
    assert [r["posting_id"] for r in out["shown"]][0] == 1, "Intensiv in München, the CV's own ward and town"
    assert all(r["score"] > 0 and r["why"] for r in out["shown"]), out["shown"]
    assert out["total"] == len(out["shown"]) <= TS.LISTING_LIMIT
    assert out["withheld_not_live"] == 1, "the gone posting ranked and was kept back, and that is said"
    assert out["profile"]["qualifications"] == ["GuK", "Fachweiterbildung"], out["profile"]
    assert out["profile"]["departments"] == ["Intensiv/IMC"]
    assert out["profile"]["cities"] == ["München"] and out["profile"]["languages"] == ["Deutsch B2"]


def test_match_cv_to_postings_sends_nothing_and_never_logs_the_number(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    _with_stored_cv(monkeypatch)
    TS.match_cv_to_postings()

    from app.wa import store as ST
    conn = ST.db()
    try:
        assert conn.execute("select count(*) from wa_messages").fetchone()[0] == 0, "a read-only tool sends nothing"
        assert json.loads(conn.execute("select slots from wa_threads where phone=?",
                                       (_CV_PHONE,)).fetchone()["slots"])["cv_text"] == _CV_TEXT
    finally:
        conn.close()
    logged = (C.LUNA_SESSION_DIR / "tool_calls.jsonl").read_text(encoding="utf-8")
    assert json.loads(logged.splitlines()[-1]) == {"tool": "match_cv_to_postings", "args": {},
                                                   "at": pytest.approx(time.time(), abs=60)}
    assert _CV_PHONE not in logged, "PII: the number is the turn's own context, never an argument or a log line"


def test_match_cv_to_postings_caps_the_listing_at_five_with_the_true_count(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    _with_stored_cv(monkeypatch)
    _many(40, city="München", clinic_town="München", department_hint="Intensiv/IMC")
    out = TS.match_cv_to_postings()
    # every live row on this board scores over app/cv.py:match's own threshold for this CV: 41 Intensiv
    # rows in München plus the three other fixture postings -- and five of them may be named.
    assert out["total"] == 44 == len(D.filter_jobs(dict(TS.LIVE_BASE))), out["total"]
    assert len(out["shown"]) == 5


def test_match_cv_to_postings_without_a_stored_cv_says_so_instead_of_ranking_nothing(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    _with_stored_cv(monkeypatch, text="")
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("match_cv_to_postings", {}))
    assert not isinstance(raised.value, UnexpectedToolError)
    assert "no CV text stored yet" in str(raised.value) and "Ask them for the Lebenslauf" in str(raised.value)


def test_match_cv_to_postings_on_a_server_started_without_the_turns_number_fails_loudly(tmp_path, monkeypatch):
    """The number is passed to this subprocess the way the database path is (luna_brain._mcp_config_path).
    Without it there is no way to tell whose CV this is -- which must not read as "no CV"."""
    board(tmp_path, monkeypatch)
    monkeypatch.delenv("WA_LUNA_PHONE", raising=False)
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("match_cv_to_postings", {}))
    assert "WA_LUNA_PHONE" in str(raised.value)


# --- requirements audit 2026-09-21: a real Bavarian town with live postings is never refused -----
# The audit broke TASK-145's city resolution with four correctly spelled towns that have live
# postings -- 'Weißenburg' (3), 'Lohr am Main' (12), 'Neumarkt' (11), 'Landkreis Miesbach' (8) all
# answered "not a town this board has open postings in" -- and with 'Neuburg an der Donau', which
# found 1 posting while 50 sat in the same town under the board's other spelling 'Neuburg/Donau'.
# One class behind all five: the board's town names carry an administrative qualifier the candidate
# does not type, abbreviated or spelled out, and the resolver only asked whether the BOARD's spelling
# occurs inside the CANDIDATE's word.

_AUDIT_TOWNS = [
    # board spelling(s), regierungsbezirk, clinic_id
    (["Weißenburg i.Bay."], "Mittelfranken", "c4"),
    (["Lohr a. Main"], "Unterfranken", "c5"),
    (["Neumarkt i.d.OPf.", "Neumarkt in der Oberpfalz"], "Oberpfalz", "c6"),
    (["Neuburg an der Donau", "Neuburg/Donau"], "Oberbayern", "c7"),
    (["Neustadt an der Aisch", "Neustadt bei Coburg"], "Mittelfranken", "c8"),
    (["Hausham"], "Oberbayern", "c9"),
]


def _audit_board(tmp_path, monkeypatch):
    """The board's own awkward spellings, one live posting per spelling, plus the registry row that
    makes 'Landkreis Miesbach' answerable at all (the board files a clinic under a Landkreis, never a
    posting, and it has no town of that name)."""
    board(tmp_path, monkeypatch)
    posting_id = 200
    for spellings, bezirk, clinic_id in _AUDIT_TOWNS:
        for spelling in spellings:
            posting_id += 1
            D._snap["jobs"].append({**D._snap["jobs"][0], "posting_id": posting_id, "city": spelling,
                                    "clinic_town": spellings[0], "regierungsbezirk": bezirk,
                                    "clinic_id": clinic_id, "clinic_name": f"Klinik {spellings[0]}",
                                    "employer": f"Klinik {spellings[0]}"})
    D._snap["clinics"].append({"clinic_id": "c9", "name": "Klinik Hausham", "town": "Hausham",
                               "landkreis": "Landkreis Miesbach", "regierungsbezirk": "Oberbayern",
                               "beds": 300, "jobs_open": 1, "jobs_fresh": 1, "jobs_live": 1})
    D._snap["by_clinic"] = {c["clinic_id"]: c for c in D._snap["clinics"]}


@pytest.mark.parametrize("asked, board_spellings", [
    ("Weissenburg", ["Weißenburg i.Bay."]),                      # the umlaut written away entirely
    ("Weißenburg", ["Weißenburg i.Bay."]),                       # correct German, no board qualifier
    ("Weissenburg i. Bay.", ["Weißenburg i.Bay."]),              # the qualifier, spaced differently
    ("Lohr am Main", ["Lohr a. Main"]),                          # spelled out vs the board's 'a.'
    ("Lohr a. Main", ["Lohr a. Main"]),
    ("Lohr", ["Lohr a. Main"]),                                  # the bare town name
    ("Neumarkt", ["Neumarkt i.d.OPf.", "Neumarkt in der Oberpfalz"]),
    ("Neumarkt in der Oberpfalz", ["Neumarkt i.d.OPf.", "Neumarkt in der Oberpfalz"]),
    ("Neumarkt i.d.OPf.", ["Neumarkt i.d.OPf.", "Neumarkt in der Oberpfalz"]),
    ("Neuburg an der Donau", ["Neuburg an der Donau", "Neuburg/Donau"]),
    ("Neuburg/Donau", ["Neuburg an der Donau", "Neuburg/Donau"]),
    ("Neuburg a.d. Donau", ["Neuburg an der Donau", "Neuburg/Donau"]),
    ("Neuburg", ["Neuburg an der Donau", "Neuburg/Donau"]),
])
def test_a_correctly_spelt_town_with_live_postings_is_never_refused(tmp_path, monkeypatch, asked,
                                                                    board_spellings):
    _audit_board(tmp_path, monkeypatch)
    out = TS.search_postings(city=asked)
    assert out["town"] == {"asked": asked, "board_spellings": board_spellings, "matched_as": "town"}
    assert out["total"] == len(board_spellings), "every board spelling of the town, not just the one typed"
    assert {r["city"] for r in out["shown"]} == set(board_spellings)
    assert TS.count_postings(city=asked)["postings"] == len(board_spellings)


def test_a_district_the_board_has_no_town_for_reaches_the_town_its_clinic_is_in(tmp_path, monkeypatch):
    """'Landkreis Miesbach' has live postings and no board town of that name -- the audit's fourth
    refusal. The registry files the clinic under the Landkreis; the posting is in Hausham."""
    _audit_board(tmp_path, monkeypatch)
    for asked in ("Landkreis Miesbach", "Miesbach", "Lkr. Miesbach"):
        out = TS.search_postings(city=asked)
        assert out["town"] == {"asked": asked, "board_spellings": ["Hausham"], "matched_as": "landkreis"}, asked
        assert [r["city"] for r in out["shown"]] == ["Hausham"], asked


def test_a_word_that_names_several_real_towns_asks_which_instead_of_picking_one(tmp_path, monkeypatch):
    """A bare 'Neustadt' is two different towns on this board. Answering about either would be a
    position in a town the candidate did not ask about, so the tool refuses and names both."""
    _audit_board(tmp_path, monkeypatch)
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("search_postings", {"city": "Neustadt"}))
    assert not isinstance(raised.value, UnexpectedToolError)
    said = str(raised.value)
    assert "'Neustadt' names 2 different towns" in said, said
    assert "Neustadt an der Aisch" in said and "Neustadt bei Coburg" in said, said
    assert "do not pick one" in said, said
    # naming which one is meant answers normally
    assert [r["city"] for r in TS.search_postings(city="Neustadt an der Aisch")["shown"]] == \
        ["Neustadt an der Aisch"]


# --- live UAT finding 2026-09-22: a candidate naming two towns in one breath must reach both -------
# 'München oder Nürnberg' broke the dialog live: _resolve_city resolved (or silently dropped to) only
# ONE of the two towns, so a truthful combined count had no evidence behind it and the grounding
# checker's NO INVENTION rule rejected the reply as if it named an unknown clinic.
@pytest.mark.parametrize("asked", [
    "München oder Augsburg", "München, Augsburg", "München und Augsburg",
    "München or Augsburg", "München/Augsburg", "München; Augsburg",
])
def test_a_candidate_naming_two_towns_in_one_breath_reaches_both(tmp_path, monkeypatch, asked):
    board(tmp_path, monkeypatch)
    out = TS.search_postings(city=asked)
    assert out["town"] == {"asked": asked, "board_spellings": ["Augsburg", "München"], "matched_as": "multi"}
    assert out["total"] == 3, "München's 2 postings plus Augsburg's 1 -- neither silently dropped"
    assert {r["city"] for r in out["shown"]} == {"München", "Augsburg"}


def test_count_postings_sums_across_two_named_towns(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    counted = TS.count_postings(city="München oder Augsburg")
    assert counted["postings"] == 3 and counted["clinics"] == 2 and counted["cities"] == 2
    assert counted["town"] == {"asked": "München oder Augsburg",
                               "board_spellings": ["Augsburg", "München"], "matched_as": "multi"}


def test_a_single_town_whose_own_board_spelling_contains_a_split_separator_is_not_split(tmp_path,
                                                                                        monkeypatch):
    """'Neuburg/Donau' is one town's own board spelling (the _audit_board fixture, TASK-145) -- it must
    resolve whole, on the first attempt, never reach the multi-city split fallback."""
    _audit_board(tmp_path, monkeypatch)
    out = TS.search_postings(city="Neuburg/Donau")
    assert out["town"]["matched_as"] == "town", "resolved as ONE town, not split on its own '/'"
    assert out["total"] == 2


def test_one_bad_town_in_a_pair_names_itself_rather_than_silently_answering_for_the_other(tmp_path,
                                                                                          monkeypatch):
    board(tmp_path, monkeypatch)
    with pytest.raises(ToolError) as raised:
        asyncio.run(TS.mcp.call_tool("search_postings", {"city": "München oder Nichtstadt"}))
    said = str(raised.value)
    assert "'Nichtstadt' is not a town" in said, said


@pytest.mark.parametrize("word", ["Landshut", "Fundament", "Nordbayern"])
def test_the_split_pattern_is_word_bounded_not_a_bare_substring_match(word):
    """Each of these carries a separator word mid-string ('Landshut' has 'and', 'Fundament' has 'und',
    'Nordbayern' has 'or') without being one -- none of these may be torn apart looking for a second
    town that was never named. A direct regex check: no board or ToolError machinery needed to prove
    this, and 'Landshut' is a real Bavarian town this exact bug would otherwise have broken."""
    assert TS._MULTI_CITY_SPLIT_RE.split(word) == [word]


def test_a_posting_is_never_offered_for_a_town_other_than_the_one_its_own_ad_names(tmp_path, monkeypatch):
    """Live 2026-09-21: 53 of the 116 postings a search for Ansbach returned were in Bruckberg,
    Himmelkron, Obernzenn or Erlangen -- filed under a clinic whose registry town is Ansbach. Rule (a):
    positions are in the place the bot states."""
    board(tmp_path, monkeypatch)
    D._snap["jobs"].append({**D._snap["jobs"][0], "posting_id": 40, "city": "Bruckberg",
                            "clinic_town": "Ansbach", "regierungsbezirk": "Mittelfranken",
                            "clinic_id": "c4", "clinic_name": "Rangauklinik Ansbach",
                            "employer": "Rangauklinik Ansbach"})
    D._snap["clinics"].append({"clinic_id": "c4", "name": "Rangauklinik Ansbach", "town": "Ansbach",
                               "regierungsbezirk": "Mittelfranken", "beds": 200, "jobs_open": 1,
                               "jobs_fresh": 1, "jobs_live": 1})
    D._snap["by_clinic"] = {c["clinic_id"]: c for c in D._snap["clinics"]}
    ansbach = TS.search_postings(city="Ansbach")
    assert (ansbach["shown"], ansbach["total"]) == ([], 0), "the ad says Bruckberg, so it is not an Ansbach job"
    assert TS.count_postings(city="Ansbach")["postings"] == 0
    assert TS.board_api_get(path="/api/jobs", query="city=Ansbach")["total"] == 0
    bruckberg = TS.search_postings(city="Bruckberg")
    assert [r["posting_id"] for r in bruckberg["shown"]] == [40]
    assert bruckberg["shown"][0]["city"] == "Bruckberg"


def test_a_posting_with_no_city_of_its_own_still_counts_for_its_clinics_town(tmp_path, monkeypatch):
    """4 of 2462 live rows carry no city at all. The clinic's registry town is then the only thing
    that says where the job is, and dropping the row would hide a real position."""
    board(tmp_path, monkeypatch)
    D._snap["jobs"].append({**D._snap["jobs"][0], "posting_id": 41, "city": "", "clinic_town": "Coburg",
                            "clinic_id": "c3", "regierungsbezirk": "Oberfranken"})
    assert 41 in [r["posting_id"] for r in TS.search_postings(city="Coburg")["shown"]]


def test_every_door_says_which_town_it_answered_about(tmp_path, monkeypatch):
    _audit_board(tmp_path, monkeypatch)
    assert TS.search_postings_with_housing(city="Lohr am Main")["town"]["board_spellings"] == ["Lohr a. Main"]
    assert TS.board_api_get(path="/api/jobs", query="city=Lohr am Main")["town"]["board_spellings"] == \
        ["Lohr a. Main"]
    assert TS.board_api_get(path="/api/clinics", query="city=Muenchen")["town"] == {
        "asked": "Muenchen", "board_spellings": ["München"], "matched_as": "town"}


def test_the_city_line_tells_the_model_the_board_spelling_comes_back(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    TS.apply_board_vocabulary()
    said = _description("search_postings")
    assert "town.board_spellings" in said and "Name the town the way the board does" in said, said
    assert "its OWN ad names, never for the town its clinic's head office is registered in" in said, said
    assert "never pick" in said, said


# --- requirements audit 2026-09-21: the housing mark is not a flat -------------------------------
# 61 of the 497 marked open postings promise only help with the search or money towards the move
# ('unterstützung bei der wohnungssuche' 37, 'umzugskosten' 13, 'wohnungssuche' 7, ...), and every
# tool presented all 497 as one thing to somebody who is moving country for the job.

@pytest.mark.parametrize("evidence, kind", [
    ("personalwohn", "accommodation"),
    ("mitarbeiterwohn", "accommodation"),
    ("wohnheim", "accommodation"),
    ("klinikeigener wohnraum (je nach verfügbarkeit)", "accommodation"),
    ("Möglichkeit auf eine Unterkunft in unseren Mitarbeiterappartements", "accommodation"),
    ("Personalwohnungen sowie Hilfe bei der Wohnungssuche", "accommodation"),   # both: there IS a flat
    ("unterstützung bei der wohnungssuche", "relocation_support"),
    ("Unterstuetzung bei der Wohnungssuche", "relocation_support"),
    ("umzugskosten", "relocation_support"),
    ("Wir beteiligen uns an den Umzugskosten", "relocation_support"),
    ("wohnungssuche", "relocation_support"),
    ("Mitarbeitervergünstigungen: Möglichkeit zur Wohnungsvermittlung", "relocation_support"),
    ("betriebliche Altersvorsorge", "unspecified"),
    ("", "unspecified"),
    (None, "unspecified"),
])
def test_a_marked_posting_says_whether_it_is_a_flat_or_only_help_looking(tmp_path, monkeypatch,
                                                                         evidence, kind):
    board(tmp_path, monkeypatch)
    D._snap["jobs"] = [{**D._snap["jobs"][0], "posting_id": 70, "enr_housing": True,
                        "enr_housing_evidence": evidence}]
    row = TS.search_postings_with_housing()["shown"][0]
    assert (row["housing"], row["housing_kind"]) == (True, kind)


def test_an_unmarked_posting_has_no_housing_kind_at_all(tmp_path, monkeypatch):
    """None, not 'unspecified': the ad was never marked, so there is nothing to classify -- and a
    posting without the mark is still not a flat."""
    board(tmp_path, monkeypatch)
    unmarked = [r for r in TS.search_postings()["shown"] if not r["housing"]]
    assert unmarked and all(r["housing_kind"] is None for r in unmarked), unmarked


def test_relocation_support_is_still_a_housing_hit_and_is_counted_apart(tmp_path, monkeypatch):
    """The filter keeps returning it -- "wir helfen bei der Wohnungssuche" is a real answer to a real
    question. What changes is that the model can no longer call it a flat."""
    board(tmp_path, monkeypatch)
    D._snap["jobs"].append({**D._snap["jobs"][0], "posting_id": 71, "city": "Coburg",
                            "clinic_town": "Coburg", "clinic_id": "c3", "clinic_name": "Klinikum Coburg",
                            "employer": "Klinikum Coburg", "regierungsbezirk": "Oberfranken",
                            "enr_housing": True, "enr_housing_evidence": "umzugskosten"})
    found = TS.search_postings_with_housing(city="Coburg")
    assert [r["posting_id"] for r in found["shown"]] == [71] and found["total"] == 1
    assert found["shown"][0]["housing_kind"] == "relocation_support"
    counted = TS.count_postings(housing=True)
    assert (counted["with_housing"], counted["with_accommodation"], counted["with_relocation_support"]) \
        == (3, 2, 1)
    listed = {c["clinic_name"]: c for c in TS.list_clinics_with_housing(limit=50)}
    assert listed["Klinikum Coburg"]["postings_with_housing"] == 1
    assert listed["Klinikum Coburg"]["postings_with_accommodation"] == 0
    assert listed["Klinikum Coburg"]["postings_with_relocation_support"] == 1


def test_the_housing_tools_say_the_mark_is_not_a_flat(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    D._snap["jobs"][2]["enr_housing_evidence"] = "unterstützung bei der wohnungssuche"
    TS.apply_board_vocabulary()
    line = _description("search_postings_with_housing")
    assert "1 accommodation" in line and "1 relocation_support" in line and "0 unspecified" in line, line
    assert "never 'mit Wohnung'" in line, line
    assert "READ housing_kind ON EVERY ROW BEFORE YOU PROMISE A FLAT" in line, line
    assert "never quote it as \"Stellen mit Wohnung\"" in _description("count_postings")


# --- requirements audit 2026-09-21: enr_childcare exists on 2170 of 2560 postings and no tool showed it

def test_every_posting_row_carries_the_ads_own_childcare_answer(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    monkeypatch.setattr(D, "job_detail", _detail_row)
    D._snap["jobs"][1]["enr_childcare"] = None            # the ad was never read for it
    by_id = {r["posting_id"]: r for r in TS.search_postings()["shown"]}
    assert by_id[1]["childcare"] is True, "the ad names a Kita"
    assert by_id[2]["childcare"] is None, "not read -- never the same as 'no Kita'"
    assert by_id[3]["childcare"] is False, "the ad was read and says nothing of the kind"
    assert TS.get_posting(posting_id=1)["childcare"] is True


def test_the_childcare_line_separates_no_from_not_recorded(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    D._snap["jobs"][1]["enr_childcare"] = None
    TS.apply_board_vocabulary()
    said = _description("search_postings")
    assert "childcare on every posting row" in said, said
    assert "1 of 4 live postings" in said and "1 postings" in said, said
    assert "never 'there is no Kita'" in said, said


# --- requirements audit 2026-09-21: stop advertising a column the board never fills --------------

def test_get_posting_no_longer_advertises_a_field_the_board_never_fills(tmp_path, monkeypatch):
    """shift_night_weekend is populated on 0 of 2462 live postings, while this tool's description
    named it and its own rule says a null field "means this ad did not say it" -- so every candidate
    asking about nights would have been told this particular ad is silent about them, 2462 times."""
    board(tmp_path, monkeypatch)
    monkeypatch.setattr(D, "job_detail", _detail_row)
    TS.apply_board_vocabulary()
    said = _description("get_posting")
    assert "shift_night_weekend" not in said, said
    assert "The board records nothing at all about shifts" in said, said
    assert "shift_night_weekend" not in TS.POSTING_DETAIL_FIELDS
    assert "shift_night_weekend" not in TS.get_posting(posting_id=1)
