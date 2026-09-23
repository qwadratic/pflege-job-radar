"""TASK-120 partial pass: one cached Maps photo per clinic, exposed on the clinic snapshot row
(app.data._build -> photo_url) and served by GET /photos/{clinic_id} (app/main.py)."""
import time

import pytest
from fastapi.testclient import TestClient

from app import config as A
from app import data as D
from app import runs as R


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    R.init()
    return tmp_path


def test_record_and_lookup_round_trip(fresh):
    assert R.clinic_photo_url("16104") is None
    assert R.clinic_photo_path("16104") is None
    R.record_clinic_photo("16104", "/data/clinic_photos/16104/maps.jpg", "maps")
    assert R.clinic_photo_url("16104") == "/photos/16104"
    assert R.clinic_photo_path("16104") == "/data/clinic_photos/16104/maps.jpg"
    assert R.clinic_photos_map() == {"16104": "/photos/16104"}


def test_build_merges_photo_url_onto_the_clinic_row(fresh, monkeypatch):
    R.record_clinic_photo("16104", "/data/clinic_photos/16104/maps.jpg", "maps")
    clinics = [{"clinic_id": "16104", "name": "kbo-Heckscher-Klinikum", "town": "Ingolstadt", "beds": 0,
                "fachrichtungen": "", "ats_type": "", "website": "", "careers_url": ""},
               {"clinic_id": "36201", "name": "No Photo Clinic", "town": "Regensburg", "beds": 0,
                "fachrichtungen": "", "ats_type": "", "website": "", "careers_url": ""}]

    def fake_rest_get_all(table, params):
        return [] if table == "v_postings" else clinics

    monkeypatch.setattr(A, "rest_get_all", fake_rest_get_all)
    monkeypatch.setattr(D, "taxonomy", lambda: {})
    monkeypatch.setattr(D, "_routing", lambda cs: {})
    snap = D._build()
    by_id = snap["by_clinic"]
    assert by_id["16104"]["photo_url"] == "/photos/16104"
    assert by_id["36201"]["photo_url"] is None


def test_photos_route_404_when_no_photo(fresh, monkeypatch):
    monkeypatch.setattr(R, "enqueue", lambda rid: None)
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/photos/99999")
    assert r.status_code == 404


def test_photos_route_serves_bytes_when_present(fresh, tmp_path, monkeypatch):
    img_path = tmp_path / "16104.jpg"
    img_path.write_bytes(b"\xff\xd8\xff\xe0fakejpegbytes")
    R.record_clinic_photo("16104", str(img_path), "maps")
    monkeypatch.setattr(R, "enqueue", lambda rid: None)
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/photos/16104")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == b"\xff\xd8\xff\xe0fakejpegbytes"


def test_clinic_id_regex_rejects_non_alnum_tokens():
    from app.main import CLINIC_ID_RX
    assert CLINIC_ID_RX.match("16104")
    assert not CLINIC_ID_RX.match("../../etc/passwd")
    assert not CLINIC_ID_RX.match("16104\x00.jpg")
    assert not CLINIC_ID_RX.match("a/b")


# --- clinic_blurbs (TASK-120 AC#7): the researched presentation paragraph, same shape as photos ---

def test_save_and_read_blurb_round_trip(fresh):
    assert R.clinic_blurbs_map() == {}
    R.save_clinic_blurb("16104", {"text_de": "Ein Fachkrankenhaus fuer KJP.", "sources": ["https://x.example"], "confidence": "high"})
    m = R.clinic_blurbs_map()
    assert set(m) == {"16104"}
    assert m["16104"]["text_de"] == "Ein Fachkrankenhaus fuer KJP."
    assert m["16104"]["confidence"] == "high"
    assert m["16104"]["sources"] == ["https://x.example"]
    assert m["16104"]["fetched_at"]  # stamped, not left out


def test_save_clinic_blurb_upserts_not_duplicates(fresh):
    R.save_clinic_blurb("16104", {"text_de": "first draft", "sources": [], "confidence": "registry_only"})
    R.save_clinic_blurb("16104", {"text_de": "researched version", "sources": ["https://x.example"], "confidence": "high"})
    m = R.clinic_blurbs_map()
    assert len(m) == 1
    assert m["16104"]["text_de"] == "researched version"


def test_build_merges_presentation_onto_the_clinic_row(fresh, monkeypatch):
    R.save_clinic_blurb("16104", {"text_de": "Ein Fachkrankenhaus.", "sources": [], "confidence": "high"})
    clinics = [{"clinic_id": "16104", "name": "kbo-Heckscher-Klinikum", "town": "Ingolstadt", "beds": 0,
                "fachrichtungen": "", "ats_type": "", "website": "", "careers_url": ""},
               {"clinic_id": "36201", "name": "No Blurb Clinic", "town": "Regensburg", "beds": 0,
                "fachrichtungen": "", "ats_type": "", "website": "", "careers_url": ""}]

    def fake_rest_get_all(table, params):
        return [] if table == "v_postings" else clinics

    monkeypatch.setattr(A, "rest_get_all", fake_rest_get_all)
    monkeypatch.setattr(D, "taxonomy", lambda: {})
    monkeypatch.setattr(D, "_routing", lambda cs: {})
    snap = D._build()
    by_id = snap["by_clinic"]
    assert by_id["16104"]["presentation"]["text_de"] == "Ein Fachkrankenhaus."
    assert by_id["36201"]["presentation"] is None


def test_expose_route_404s_for_an_unknown_clinic(fresh, monkeypatch):
    monkeypatch.setattr(R, "enqueue", lambda rid: None)
    monkeypatch.setattr(D, "clinic", lambda cid: None)
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/api/clinics/99999999/expose")
    assert r.status_code == 404


def test_expose_route_returns_photo_and_presentation_together(fresh, monkeypatch):
    monkeypatch.setattr(R, "enqueue", lambda rid: None)
    monkeypatch.setattr(D, "clinic", lambda cid: {
        "clinic_id": "16104", "name": "kbo-Heckscher-Klinikum", "town": "Ingolstadt",
        "photo_url": "/photos/16104",
        "presentation": {"text_de": "Ein Fachkrankenhaus.", "confidence": "high", "sources": ["https://x.example"], "fetched_at": "2026-09-23"}})
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/api/clinics/16104/expose")
    assert r.status_code == 200
    body = r.json()
    assert body == {"clinic_id": "16104", "name": "kbo-Heckscher-Klinikum", "town": "Ingolstadt",
                     "photo_url": "/photos/16104",
                     "presentation": {"text_de": "Ein Fachkrankenhaus.", "confidence": "high", "sources": ["https://x.example"]}}


def test_expose_route_presentation_is_null_not_missing_when_clinic_has_none(fresh, monkeypatch):
    monkeypatch.setattr(R, "enqueue", lambda rid: None)
    monkeypatch.setattr(D, "clinic", lambda cid: {
        "clinic_id": "36201", "name": "No Blurb Clinic", "town": "Regensburg", "photo_url": None, "presentation": None})
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/api/clinics/36201/expose")
    assert r.status_code == 200
    assert r.json()["presentation"] is None
