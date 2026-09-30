"""load_registry_source() reads the registry from the live snapshot only. It used to fall back to
data/registry/clinics.csv when the snapshot was unavailable (TASK-148/TASK-103 fixed that fallback's
id filter for "RH<n>"/"DK<nn>" rows); TASK-175 removed the CSV, so an unavailable or empty snapshot now
fails the seed instead of seeding from a stale copy -- and every snapshot row is kept, whatever its id."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import data as D  # noqa: E402
from app.autopilot import seed as ASEED  # noqa: E402


def test_an_unavailable_snapshot_fails_the_seed(monkeypatch):
    def boom(wait=60):
        raise RuntimeError("snapshot unavailable")
    monkeypatch.setattr(D, "snapshot", boom)
    with pytest.raises(RuntimeError, match="snapshot unavailable"):
        ASEED.load_registry_source()


def test_an_empty_snapshot_fails_the_seed(monkeypatch):
    monkeypatch.setattr(D, "snapshot", lambda wait=60: {"clinics": [], "jobs": []})
    with pytest.raises(RuntimeError, match="no clinics"):
        ASEED.load_registry_source()


def test_every_snapshot_clinic_is_kept_whatever_its_id_shape(monkeypatch):
    rows = [{"clinic_id": "RH1847"}, {"clinic_id": "16107"}, {"clinic_id": "DK01"}]
    monkeypatch.setattr(D, "snapshot", lambda wait=60: {"clinics": rows, "jobs": []})
    clinics, jobs = ASEED.load_registry_source()
    assert [c["clinic_id"] for c in clinics] == ["16107", "DK01", "RH1847"] and jobs == []
