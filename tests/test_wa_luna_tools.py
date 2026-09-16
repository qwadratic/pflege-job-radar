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
    rows = []
    plan = [("München", "Oberbayern", "Intensiv/IMC", True, "c1"), ("München", "Oberbayern", "OP", False, "c1"),
            ("Augsburg", "Schwaben", "Innere Medizin", True, "c2"), ("Coburg", "Oberfranken", "Notaufnahme", False, "c3")]
    for i, (city, bezirk, dept, housing, clinic_id) in enumerate(plan):
        rows.append({"posting_id": i + 1, "title": f"Pflegefachkraft {dept}", "role_class": "pflegefachkraft",
                     "department_hint": dept, "city": city, "clinic_town": city, "regierungsbezirk": bezirk,
                     "clinic_id": clinic_id, "clinic_name": f"Klinikum {city}", "employer": f"Klinikum {city}",
                     "employment_types": ["vollzeit"], "enr_housing": housing, "verify_status": "live",
                     "status": "open", "first_published": "2026-09-01", "fresh": True,
                     "source_url": f"https://example.org/job/{i + 1}"})
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
    assert {r["city"] for r in out} == {"München"}
    assert len(out) == 2
    log = (C.LUNA_SESSION_DIR / "tool_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(log[-1])["tool"] == "search_postings"
    assert json.loads(log[-1])["args"]["city"] == "München"


def test_search_postings_filters_by_department_and_role(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    out = TS.search_postings(department="Notaufnahme")
    assert len(out) == 1 and out[0]["city"] == "Coburg"


def test_search_postings_reads_the_candidates_department_word_in_board_vocabulary(tmp_path, monkeypatch):
    """TASK-96 review: a live persona run called search_postings(city='München', department='Intensivstation'),
    got 0 rows from the exact board filter ('Intensiv/IMC') and told the candidate nothing was open."""
    board(tmp_path, monkeypatch)
    for word in ("Intensivstation", "ITS", "Intensiv/IMC"):
        out = TS.search_postings(city="München", department=word)
        assert [(r["posting_id"], r["department"]) for r in out] == [(1, "Intensiv/IMC")], word
    assert TS.search_postings(city="Augsburg", department="Intensivstation") == []
    log = (C.LUNA_SESSION_DIR / "tool_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(log[0])["args"]["department"] == "Intensivstation", "the call log keeps the model's own word"


def test_search_postings_reads_department_like_market_snapshot(tmp_path, monkeypatch):
    """TASK-104: one reading for both (slots.read_department_pref): a flexible word filters nothing, a word only the
    board's title classifier knows filters to that board department."""
    board(tmp_path, monkeypatch)
    for word in ("egal", "flexibel", "keine Präferenz"):
        assert [r["posting_id"] for r in TS.search_postings(city="München", department=word)] == [1, 2], word
    assert [r["posting_id"] for r in TS.search_postings(department="Zentrale Notaufnahme")] == [4]
    assert TS.search_postings(city="München", department="Stroke Unit") == []
    # review 2026-09-15: every department named, not only the first rule that matched
    assert sorted(r["posting_id"] for r in TS.search_postings(department="Innere oder Intensiv")) == [1, 3]
    assert [r["posting_id"] for r in TS.search_postings(city="Augsburg", department="Intensiv oder Innere")] == [3]


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


def test_search_postings_respects_limit_and_caps_it(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    assert len(TS.search_postings(limit=1)) == 1
    assert len(TS.search_postings(limit=999)) <= 50


def test_get_posting_returns_the_row_or_none(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    assert TS.get_posting(posting_id=1)["city"] == "München"
    assert TS.get_posting(posting_id=999) is None


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
    TS.search_postings(city="Nowhereville")
    TS.list_clinics(city="Nowhereville")
    TS.get_posting(posting_id=42)
    TS.search_postings_with_housing(city="Nowhereville")
    TS.list_clinics_with_housing(city="Nowhereville")
    TS.list_cities_with_postings(regierungsbezirk="Nirgendwo")
    TS.count_postings(city="Nowhereville")
    TS.board_api_get(path="/api/jobs", query="city=Nowhereville")
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
    assert sorted(r["posting_id"] for r in out) == [1, 3]
    assert all(r["housing"] is True for r in out)
    assert [r["posting_id"] for r in TS.search_postings_with_housing(city="München")] == [1]
    assert TS.search_postings_with_housing(city="Coburg") == [], "Coburg's posting carries no housing mark"
    assert [r["posting_id"] for r in TS.search_postings_with_housing(regierungsbezirk="Schwaben")] == [3]
    logged = [json.loads(l) for l in
              (C.LUNA_SESSION_DIR / "tool_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()]
    assert {l["tool"] for l in logged} == {"search_postings_with_housing"}


def test_search_postings_with_housing_reads_the_candidates_department_word(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    assert [r["posting_id"] for r in TS.search_postings_with_housing(department="Intensivstation")] == [1]
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
    assert TS.count_postings(city="München") == {"postings": 2, "clinics": 1, "cities": 1, "with_housing": 1,
                                                  "filters": {"city": "München"}}
    assert TS.count_postings(housing=True) == {"postings": 2, "clinics": 2, "cities": 2, "with_housing": 2,
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
    -- live, that was Coburg 2 vs 39 and München 369 vs 499 in one conversation."""
    board(tmp_path, monkeypatch)
    D._snap["jobs"].append({**D._snap["jobs"][3], "posting_id": 20, "verify_status": "gone"})
    assert TS.count_postings(city="Coburg")["postings"] == 1
    assert TS.board_api_get(path="/api/jobs", query="city=Coburg")["total"] == 1, (
        "the same base as the tools: a posting the verifier last found gone is not named as open")
    assert TS.board_api_get(path="/api/jobs", query="city=Coburg&verify=gone")["total"] == 1, (
        "an explicit verify= still decides -- asking past the re-verified rows stays possible on purpose")
    assert TS.board_api_get(path="/api/jobs", query="city=Coburg&verify=live,gone")["total"] == 2
    detail = TS.board_api_get(path="/api/clinics/c3")
    assert [j["posting_id"] for j in detail["jobs"]] == [4] and detail["jobs_total"] == 1, (
        "the clinic detail's own job list starts from that base too")


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
