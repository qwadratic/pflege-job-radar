"""cli._process_rows hands the employer's class to the matcher: a non_clinic employer read off the posting is not filed by its board (TASK-431.9).

Offline, no mirror: the real jobposting_to_obs and the real Matcher, only EdgeSink is stubbed (the pattern of
tests/test_mech_clinic_link.py::test_process_rows_wires_city_inherited_through_to_the_real_matcher). The mirror-side case, the 12 Malteser postings
of wp_jobs__waldkrankenhaus.de, is tests/test_board_fallback_non_clinic.py."""
import argparse

from pflege_jobs import cli
from pflege_jobs.registry import Matcher

SEED = {"clinic_id": "S1", "name": "Seed Klinikum", "town": "Seedstadt", "operator": None}
SIBLING = {"clinic_id": "S2", "name": "Sibling Klinikum", "town": "Siblingstadt", "operator": None}


def _drain(monkeypatch, org, org_source=None):
    row = {"inbox_id": 1, "kind": "jobposting", "source_url": "https://x.example/job/1", "source_host": "x.example",
           "payload": {"title": "Pflegefachkraft (m/w/d)", "org": org, "org_source": org_source, "url": "https://x.example/job/1", "description": "",
                       "loc": [{"city": "Seedstadt", "plz": None, "region": None}], "board_clinic_ids": ["S1", "S2"]}}
    posted = []

    class _FakeSink:
        def __init__(self, *a, **kw): pass

        def write(self, obs, **kw):
            posted.extend(obs); return {"observations": len(obs)}
    monkeypatch.setattr(cli, "EdgeSink", _FakeSink)
    cli._process_rows([row], argparse.Namespace(no_ack=True), "https://db", {}, Matcher([dict(SEED), dict(SIBLING)]),
                      {"seedstadt", "siblingstadt"}, lambda acks: len(acks))
    assert len(posted) == 1
    return posted[0]


def test_a_non_clinic_employer_is_not_filed_by_its_board_and_stays_non_clinic(monkeypatch):
    o = _drain(monkeypatch, "Ambulanter Pflegedienst Sonnenschein GmbH")
    assert o["employer_class"] == "non_clinic" and o["_kez"] is None
    assert o["city"] == "Seedstadt" and o["employer_name"] == "Ambulanter Pflegedienst Sonnenschein GmbH"      # its own city and employer are kept


def test_an_unknown_employer_is_still_filed_by_its_board_and_one_that_names_the_clinic_by_name(monkeypatch):
    assert _drain(monkeypatch, "Some Real Employer GmbH")["_kez"] == "S1"            # R0_board_town: the board's clinic in the posting's town
    o = _drain(monkeypatch, "Seed Klinikum")
    assert o["_kez"] == "S1" and o["employer_class"] == "clinic"


def test_a_copied_employer_is_not_read_for_its_class(monkeypatch):
    """org_source='seed': the adapter put the seed clinic's own name there, the class of that name says nothing about the posting."""
    o = _drain(monkeypatch, "Verein fuer ambulante Pflege e.V.", org_source="seed")
    assert o["_kez"] == "S1"
