"""Tests for app/wa/luna/status_message.py and tools/wa_status_message.py (TASK-439). Offline; the status
JSON here is synthetic, the public base is pinned to a test domain."""
import json

import pytest

from app.wa.luna import prompts as P
from app.wa.luna import status_message as SM
from tools import wa_status_message as TOOL

BASE = "https://test.example/s/"
URL = BASE + "A" * 22 + "/"


@pytest.fixture(autouse=True)
def base(monkeypatch):
    monkeypatch.setenv("WA_STATUS_DOCS_PUBLIC_BASE", BASE)


def _clinic(name, best=False, **over):
    e = {"name": name, "town": "Teststadt", "travel": "ab Teststadt 20 Min.", "sent_at": "2026-10-01",
         "jobs": ["Pflegefachkraft Intensivstation", "Pflegefachkraft IMC"], "housing": "ja: Personalwohnung",
         "fit": {"station": "yes", "near": "yes", "housing": "yes"}}
    if best:
        e["best"] = True
    e.update(over)
    return e


def _status(*sent):
    return {"as_of": "2026-10-06", "profile": [], "wishes": [], "more": [], "chat_url": "https://wa.me/490",
            "sent": list(sent)}


def test_approved_wording_is_locked():
    # Ivan 2026-10-06, verbatim. A change here is a change of candidate-facing text and needs his OK.
    assert P.STATUS_BEST_INTRO_DE == ("Wir haben Ihr Profil an diese Klinik geschickt, sie passt besonders gut "
                                      "zu Ihren Wünschen:")
    assert (P.STATUS_JOBS_LABEL_DE, P.STATUS_HOUSING_LABEL_DE, P.STATUS_TRAVEL_LABEL_DE) == (
        "Stellen:", "Wohnung:", "Weg:")
    assert P.STATUS_COUNT_DE == ("Insgesamt ist Ihr anonymisiertes Profil an {n} Kliniken gegangen. "
                                 "Wir warten jetzt auf deren Antworten.")
    assert P.STATUS_LINK_LEAD_DE == "Alle Kliniken und den vollständigen Bericht finden Sie hier:"


def test_two_bubbles_best_clinic_then_count_and_link():
    status = _status(_clinic("Klinik Eins"), _clinic("Klinik Zwei", best=True, town="Zweistadt"),
                     _clinic("Klinik Drei"))
    assert SM.bubbles(status, URL) == [
        "Wir haben Ihr Profil an diese Klinik geschickt, sie passt besonders gut zu Ihren Wünschen:\n"
        "Klinik Zwei, Zweistadt\n"
        "Stellen: Pflegefachkraft Intensivstation; Pflegefachkraft IMC\n"
        "Wohnung: ja: Personalwohnung\n"
        "Weg: ab Teststadt 20 Min.",
        "Insgesamt ist Ihr anonymisiertes Profil an 3 Kliniken gegangen. Wir warten jetzt auf deren Antworten.\n"
        "Alle Kliniken und den vollständigen Bericht finden Sie hier:\n"
        + URL,
    ]


def test_every_job_of_the_best_clinic_is_listed():
    jobs = [f"Stelle {i}" for i in range(5)]
    first, _ = SM.bubbles(_status(_clinic("A", best=True, jobs=jobs), _clinic("B")), URL)
    assert "Stellen: Stelle 0; Stelle 1; Stelle 2; Stelle 3; Stelle 4" in first


@pytest.mark.parametrize("sent", [
    [_clinic("A"), _clinic("B")],                        # nobody marked best
    [_clinic("A", best=True), _clinic("B", best=True)],  # two marked best
    [{**_clinic("A"), "best": "yes"}, _clinic("B")],    # only the JSON value true counts
])
def test_exactly_one_best_or_loud(sent):
    with pytest.raises(ValueError, match="exactly one sent clinic best"):
        SM.bubbles(_status(*sent), URL)


@pytest.mark.parametrize("field", SM.BEST_FIELDS)
def test_best_clinic_missing_a_field_is_loud(field):
    best = _clinic("A", best=True)
    del best[field]
    with pytest.raises(ValueError, match=field):
        SM.bubbles(_status(best, _clinic("B")), URL)


def test_jobs_must_be_strings():
    with pytest.raises(ValueError, match="jobs must be a list"):
        SM.bubbles(_status(_clinic("A", best=True, jobs=["ok", ""]), _clinic("B")), URL)


def test_one_clinic_is_refused_wording_is_plural_only():
    with pytest.raises(ValueError, match="plural only"):
        SM.bubbles(_status(_clinic("A", best=True)), URL)


@pytest.mark.parametrize("url", [
    BASE + "A" * 22,                       # no trailing slash
    BASE + "A" * 21 + "/",                 # token too short
    BASE + "A" * 22 + "/index.html",       # a file, not the page
    "https://evil.example/s/" + "A" * 22 + "/",
    "",
])
def test_link_must_be_a_status_page_link(url):
    with pytest.raises(ValueError, match="not a status-page link"):
        SM.bubbles(_status(_clinic("A", best=True), _clinic("B")), url)


def test_tool_prints_both_bubbles(tmp_path, capsys):
    path = tmp_path / "status.json"
    path.write_text(json.dumps(_status(_clinic("A", best=True), _clinic("B"))), encoding="utf-8")
    assert TOOL.main([str(path), URL]) == 0
    out = capsys.readouterr().out
    assert out.startswith("--- bubble 1\nWir haben Ihr Profil an diese Klinik geschickt")
    assert "--- bubble 2\nInsgesamt ist Ihr anonymisiertes Profil an 2 Kliniken gegangen." in out
    assert out.rstrip().endswith(URL)


def test_tool_bad_input_exits_1(tmp_path, capsys):
    path = tmp_path / "status.json"
    path.write_text(json.dumps(_status(_clinic("A"), _clinic("B"))), encoding="utf-8")
    assert TOOL.main([str(path), URL]) == 1
    assert "exactly one sent clinic best" in capsys.readouterr().err
