import json
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.apply_clinic_corrections import merged_row, diff_lines
from pflege_jobs.schema import CLINIC_SPEC


LIVE = {"clinic_id": "12345", "name": "Test Klinik", "town": "Musterstadt", "operator": "Op GmbH",
        "landkreis": "LK", "regierungsbezirk": "Oberbayern", "status": "aktiv",
        "versorgungsstufe": "Grund", "traegerart": "oeffentlich", "beds": 100, "day_places": 0,
        "fachrichtungen": "Innere", "parse_quality": "ok", "source": "krankenhausplan",
        "website": "https://x.de", "careers_url": "https://x.de/old", "ats_type": "wp_jobs"}


def test_merged_row_changes_only_named_fields_and_preserves_the_rest():
    row = merged_row(LIVE, {"careers_url": "https://x.de/new", "ats_type": "softgarden"})
    assert row["careers_url"] == "https://x.de/new"
    assert row["ats_type"] == "softgarden"
    assert row["name"] == "Test Klinik"
    assert row["beds"] == 100
    assert set(row) == {c for c, _ in CLINIC_SPEC}


def test_diff_lines_shows_old_arrow_new_for_every_changed_field():
    lines = diff_lines("12345", LIVE, {"careers_url": "https://x.de/new", "ats_type": "softgarden"})
    assert len(lines) == 2
    assert any("https://x.de/old" in l and "https://x.de/new" in l for l in lines)
    assert any("wp_jobs" in l and "softgarden" in l for l in lines)


# --- TASK-185: a careers_url this file writes is linted before anything is written. Synthetic rows; the
# network (clinics, postings) and the ledger connection are stubbed. -------------------------------------------
POSTING_URL = "https://klinik-x.example/stellenangebot/pflegefachkraft-m-w-d"


def _stub_db(monkeypatch, AC):
    live = {"66103": {**LIVE, "clinic_id": "66103", "name": "Synthetic Frauenklinik"},
            "12345": LIVE, "12346": {**LIVE, "clinic_id": "12346", "name": "Y"}}

    def rest_get(path, params=None, **kw):
        if path == "clinics":
            return [live[c] for c in params["clinic_id"][4:-1].split(",")]
        return [{"external_url": POSTING_URL}] if params["external_url"] == f"eq.{POSTING_URL}" else []
    monkeypatch.setattr(AC.A, "rest_get", rest_get)
    return live


def test_lint_careers_urls_names_aggregator_and_posting_url_corrections(monkeypatch):
    from tools import apply_clinic_corrections as AC
    live = _stub_db(monkeypatch, AC)
    corrections = {"66103": {"careers_url": "https://www.krankenpflegejobs24.de/synthetic-frauenklinik"},
                   "12345": {"careers_url": POSTING_URL},
                   "12346": {"ats_type": "softgarden"},                      # no careers_url written: not linted
                   "12347": {"careers_url": "https://klinik-z.example/karriere/"}}
    found = AC.lint_careers_urls(corrections, live)
    assert [(f.clinic_id, f.shape) for f in found] == [("66103", "aggregator-host"), ("12345", "posting-url")]
    assert "Synthetic Frauenklinik" in str(found[0])


def test_main_stops_before_the_backup_and_the_write_when_the_lint_finds_something(monkeypatch, tmp_path):
    from tools import apply_clinic_corrections as AC
    _stub_db(monkeypatch, AC)
    why = {"code": "board_location", "reason": "synthetic", "evidence": ["synthetic"], "task": "TASK-185"}
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"66103": {"careers_url": "https://www.krankenpflegejobs24.de/synthetic-frauenklinik", "_why": why}}))
    written = []
    monkeypatch.setattr(AC.L, "connect", lambda: None)
    monkeypatch.setattr(AC.L, "reason_codes", lambda table, conn: {"board_location"})
    monkeypatch.setattr(AC, "backup", lambda *a: written.append("backup"))
    monkeypatch.setattr(AC.EdgeSink, "write_clinics", lambda self, rows: written.append("write"))
    monkeypatch.setattr(sys, "argv", ["apply_clinic_corrections.py", str(path), "--push", "--by", "test"])
    with pytest.raises(SystemExit) as e:
        AC.main()
    assert "66103" in str(e.value) and "Synthetic Frauenklinik" in str(e.value) and "aggregator-host" in str(e.value)
    assert written == []
