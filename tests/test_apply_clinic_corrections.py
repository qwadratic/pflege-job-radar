import sys
import os

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
