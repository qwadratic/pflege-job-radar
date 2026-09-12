"""Offline unit tests for app/wa/luna/external_contacts.py -- a fake `run` stands in for
subprocess.run, so nothing here touches a real external database or spawns any subprocess. DB_PATH
is monkeypatched to a non-empty placeholder in every test that needs the module "configured";
the unset-by-default no-op path gets its own dedicated test."""
import json

import pytest

from app.wa.luna import external_contacts as EC


class _FakeProc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _fake_run(rows_by_call):
    """rows_by_call: a list of row-lists, one per expected call, returned in order."""
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        rows = rows_by_call[len(calls) - 1]
        return _FakeProc(stdout=json.dumps(rows) if rows else "")
    run.calls = calls
    return run


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    """Every test in this file exercises the "configured" path -- the dedicated unset-by-default
    test below monkeypatches DB_PATH back to "" for that one case."""
    monkeypatch.setattr(EC, "DB_PATH", "/configured/for/this/test.sqlite")


def test_contact_for_clinic_is_a_no_op_when_unconfigured(monkeypatch):
    monkeypatch.setattr(EC, "DB_PATH", "")

    def run(cmd, **kwargs):
        raise AssertionError("must not query anything when DB_PATH is unset")

    assert EC.contact_for_clinic("Irgendeine Klinik", run=run) is None


def test_query_raises_loudly_on_a_nonzero_exit():
    def run(cmd, **kwargs):
        return _FakeProc(returncode=1, stderr="Error: unable to open database file")
    with pytest.raises(RuntimeError, match="external contact CRM query failed"):
        EC._query("select 1;", run=run)


def test_query_returns_empty_list_for_empty_output():
    run = _fake_run([[]])
    assert EC._query("select 1;", run=run) == []


def test_best_clinic_match_picks_the_closest_bavarian_name_above_threshold():
    rows = [{"id": 101, "name": "Klinikum Muenchen Nord"}, {"id": 202, "name": "Klinikum Augsburg"}]
    run = _fake_run([rows])
    assert EC._best_clinic_match("Klinikum München Nord", run=run) == 101


def test_best_clinic_match_returns_none_below_threshold():
    rows = [{"id": 101, "name": "Ein ganz anderer Name AG"}]
    run = _fake_run([rows])
    assert EC._best_clinic_match("Klinikum München Nord", run=run) is None


def test_best_clinic_match_query_scopes_to_bavaria():
    run = _fake_run([[]])
    EC._best_clinic_match("Klinikum X", run=run)
    sql = run.calls[0][-1]
    assert "bundesland='Bayern'" in sql
    assert "clinic_location" in sql


def test_best_contact_prefers_a_preferred_role_category_even_if_not_first_row():
    rows = [{"email": "random.person@example.de", "role_category": "management"},
            {"email": "pflegedirektion@example.de", "role_category": "pflege_leadership"}]
    run = _fake_run([rows])
    email, role = EC._best_contact(1174, run=run)
    assert email == "pflegedirektion@example.de"
    assert role == "pflege_leadership"


def test_best_contact_falls_back_to_first_person_row_when_no_preferred_role():
    rows = [{"email": "someone@example.de", "role_category": "management"}]
    run = _fake_run([rows])
    email, role = EC._best_contact(1174, run=run)
    assert email == "someone@example.de" and role == "management"


def test_best_contact_falls_back_to_a_generic_company_mailbox():
    calls = {"n": 0}

    def run(cmd, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeProc(stdout=json.dumps([]))    # no person-level email
        return _FakeProc(stdout=json.dumps([{"email": "info@klinikum-x.example"}]))

    email, role = EC._best_contact(1174, run=run)
    assert email == "info@klinikum-x.example"
    assert role == "generic_company_mailbox"


def test_best_contact_returns_none_none_when_nothing_found():
    run = _fake_run([[], []])
    assert EC._best_contact(1174, run=run) == (None, None)


def test_contact_for_clinic_end_to_end_high_confidence():
    def run(cmd, **kwargs):
        sql = cmd[-1]
        if "companies" in sql:
            return _FakeProc(stdout=json.dumps([{"id": 1174, "name": "Klinikum Muenchen Nord"}]))
        if "contact_channels cc join people" in sql:
            return _FakeProc(stdout=json.dumps([{"email": "PD@Klinikum-Muenchen.EXAMPLE", "role_category": "pflege_leadership"}]))
        return _FakeProc(stdout="[]")

    out = EC.contact_for_clinic("Klinikum München Nord", run=run)
    assert out == {"email": "pd@klinikum-muenchen.example", "source": "external_crm", "confidence": "high"}


def test_contact_for_clinic_returns_none_when_no_clinic_matches():
    run = _fake_run([[]])
    assert EC.contact_for_clinic("Ein völlig unbekannter Name", run=run) is None


def test_sql_str_escapes_single_quotes():
    assert EC._sql_str("St. Vinzenz'sches Krankenhaus") == "'St. Vinzenz''sches Krankenhaus'"
