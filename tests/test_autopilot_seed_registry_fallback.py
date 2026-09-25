"""TASK-148: load_registry_source()'s CSV fallback (used when the Supabase snapshot is unavailable)
used to filter clinic_id via bare .isdigit(), which would silently drop every Reha facility minted by
data/sync_rhv_reha.py ("RH<digits>", e.g. "RH1847") -- those ids are real, just not bare-numeric KeZ.

TASK-103: same gap for "DK<digits>" (data/sync_diakoneo_social.py's Diakoneo elder/disability-care
facilities) -- the regex fix for RH did not automatically cover a second prefix."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import data as D  # noqa: E402
from app.autopilot import seed as ASEED  # noqa: E402

ROWS = [
    {"clinic_id": "16107", "name": "A", "town": "X", "beds": "10", "fachrichtungen": ""},
    {"clinic_id": "RH1847", "name": "Rehaklinik B", "town": "Y", "beds": "20", "fachrichtungen": ""},
    {"clinic_id": "DK01", "name": "Diakoneo Wohnen C", "town": "Z", "beds": "", "fachrichtungen": ""},
    {"clinic_id": "", "name": "header-junk-row", "town": "", "beds": "", "fachrichtungen": ""},
]


def test_rh_and_dk_prefixed_ids_survive_the_csv_fallback(monkeypatch):
    def boom(wait=60):
        raise RuntimeError("snapshot unavailable")
    monkeypatch.setattr(D, "snapshot", boom)
    monkeypatch.setattr(D, "registry_csv_rows", lambda: ROWS)
    clinics, jobs = ASEED.load_registry_source()
    ids = {c["clinic_id"] for c in clinics}
    assert ids == {"16107", "RH1847", "DK01"}
