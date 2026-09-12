"""Offline unit tests for app/wa/luna/tools_server.py's tool functions -- called directly, no
MCP protocol, no subprocess. The `@mcp.tool()` decorator does not change the underlying function's
callability, so these are ordinary function calls against a fixture board snapshot.

Proactive-tool-use (does the model actually call one, and only when it should) is covered
separately in tests/test_wa_luna_personas.py, marked ``llm`` since it needs the real CLI.
"""
import json
import time

from app import data as D
from app.wa import config as C
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
    return [{"clinic_id": "c1", "name": "Klinikum München", "town": "München", "regierungsbezirk": "Oberbayern", "beds": 800, "jobs_open": 2},
            {"clinic_id": "c2", "name": "Klinikum Augsburg", "town": "Augsburg", "regierungsbezirk": "Schwaben", "beds": 600, "jobs_open": 1},
            {"clinic_id": "c3", "name": "Klinikum Coburg", "town": "Coburg", "regierungsbezirk": "Oberfranken", "beds": 400, "jobs_open": 1}]


def board(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": _jobs(), "clinics": _clinics(),
                    "by_clinic": {c["clinic_id"]: c for c in _clinics()}, "facets": {}, "taxonomy": {},
                    "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")


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


def test_get_clinic_contact_delegates_to_the_contacts_module_when_present(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)

    class _FakeContacts:
        def get_contact(self, clinic_id):
            return {"email": "pd@klinikum-muenchen.example", "source": "board", "confidence": "high"}

    monkeypatch.setattr(TS, "CT", _FakeContacts())
    out = TS.get_clinic_contact(clinic_id="c1")
    assert out["email"] == "pd@klinikum-muenchen.example"


def test_every_call_is_logged_even_when_the_result_is_empty(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch)
    TS.search_postings(city="Nowhereville")
    TS.list_clinics(city="Nowhereville")
    TS.get_posting(posting_id=42)
    lines = (C.LUNA_SESSION_DIR / "tool_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()
    tools_called = [json.loads(l)["tool"] for l in lines]
    assert tools_called == ["search_postings", "list_clinics", "get_posting"]
