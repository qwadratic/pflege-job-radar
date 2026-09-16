"""Offline tests for app/wa/luna/export_known_phones.py -- a small, real (not mocked) fixture
sqlite file only, no real data, no real external system named or touched."""
import sqlite3

import pytest

from app.wa import config as C
from app.wa import routing as R
from app.wa.luna import export_known_phones as EXP


@pytest.fixture()
def source_db(tmp_path):
    """A tiny sqlite file shaped like *some* operator's own database -- table/column names chosen
    for this test only, not a description of any real system (same discipline the module under
    test itself follows)."""
    path = tmp_path / "source.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("create table source_candidates (id integer primary key, contact_phone text)")
    conn.executemany("insert into source_candidates (contact_phone) values (?)", [
        ("0170 1234567",),        # -> +491701234567
        ("+491701234567",),       # same human, different formatting -> dedup with the row above
        ("0049 170 7654321",),    # -> +491707654321
        ("not-a-phone-number",),  # garbage -- must be skipped, not silently pass through
    ])
    conn.commit()
    conn.close()
    return path


QUERY = "select contact_phone from source_candidates order by id"


# --- a working export ----------------------------------------------------------------------

def test_export_writes_canonicalized_deduplicated_sorted_phones(source_db, tmp_path):
    out = tmp_path / "known_phones.txt"
    result = EXP.export(source_db, QUERY, out)

    assert result["exported"] == 2
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines == sorted(["+491701234567", "+491707654321"])


def test_export_returns_zero_and_writes_a_valid_empty_file_for_an_empty_result_set(source_db, tmp_path):
    out = tmp_path / "known_phones.txt"
    result = EXP.export(source_db, "select contact_phone from source_candidates where 1=0", out)

    assert result == {"exported": 0, "skipped": []}
    assert out.exists()
    assert out.read_text(encoding="utf-8") == ""


# --- skipped rows: reported, never silently dropped -----------------------------------------

def test_garbage_phone_is_skipped_not_silently_treated_as_a_bogus_country_code_only_number(source_db, tmp_path):
    """canonicalize_phone("not-a-phone-number") -> "+49" (every character stripped as non-digit,
    leaving only the default country code) -- truthy but not a real number; the MIN_PHONE_DIGITS
    floor must catch this, exactly like migrate_candidates.py's own regression test for the same
    footgun. Without the floor this row would silently land in the output as a fake "known" phone."""
    out = tmp_path / "known_phones.txt"
    result = EXP.export(source_db, QUERY, out)

    assert "+49" not in out.read_text(encoding="utf-8").splitlines()
    reasons = [s["reason"] for s in result["skipped"]]
    assert any("did not canonicalize" in r for r in reasons)


def test_a_malformed_phone_row_is_reported_with_a_reason_not_dropped(source_db, tmp_path):
    out = tmp_path / "known_phones.txt"
    result = EXP.export(source_db, QUERY, out)

    assert result["skipped"] == [
        {"row": "not-a-phone-number", "reason": "phone did not canonicalize to a real number: "
                                                  "'not-a-phone-number' -> '+49'"}
    ]


# --- read-only source ------------------------------------------------------------------------

def test_export_never_writes_to_the_source_database_even_if_the_query_tries_to(source_db, tmp_path):
    """The source connection is opened with SQLite URI ``mode=ro`` -- even a caller-supplied query
    that attempts a write must fail against it, proving this is real read-only enforcement and not
    just a convention this function happens to follow."""
    out = tmp_path / "known_phones.txt"
    with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
        EXP.export(source_db, "insert into source_candidates (contact_phone) values ('+491700000000')",
                   out)

    # the insert really did not happen
    conn = sqlite3.connect(source_db)
    count = conn.execute("select count(*) from source_candidates").fetchone()[0]
    conn.close()
    assert count == 4
    assert not out.exists()


def test_export_works_even_when_the_source_file_is_read_only_on_disk(source_db, tmp_path):
    source_db.chmod(0o444)
    out = tmp_path / "known_phones.txt"
    result = EXP.export(source_db, QUERY, out)
    assert result["exported"] == 2


# --- atomic output write ----------------------------------------------------------------------

def test_a_failed_export_leaves_prior_output_file_content_completely_untouched(source_db, tmp_path, monkeypatch):
    out = tmp_path / "known_phones.txt"
    out.write_text("+491700000000\n", encoding="utf-8")

    def boom(*a, **kw):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(EXP.os, "replace", boom)

    with pytest.raises(OSError):
        EXP.export(source_db, QUERY, out)

    assert out.read_text(encoding="utf-8") == "+491700000000\n", \
        "a failed export must never corrupt or truncate whatever was already at out_path"
    assert list(tmp_path.glob(".export_known_phones-*.tmp")) == [], \
        "a failed export must not leave a stray temp file behind either"


def test_a_failed_export_does_not_create_out_path_when_it_did_not_exist_before(source_db, tmp_path, monkeypatch):
    out = tmp_path / "known_phones.txt"

    def boom(*a, **kw):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(EXP.os, "replace", boom)

    with pytest.raises(OSError):
        EXP.export(source_db, QUERY, out)

    assert not out.exists()


# --- integration: the file this writes is exactly what routing.py reads -----------------------

def test_the_output_file_is_plain_newline_delimited_and_routing_py_can_read_it_back(source_db, tmp_path,
                                                                                      monkeypatch):
    out = tmp_path / "known_phones.txt"
    EXP.export(source_db, QUERY, out)
    monkeypatch.setattr(C, "REAL_SYSTEM_PHONES_FILE", str(out))

    assert R._is_known_to_real_system("+491701234567") is True
    assert R._is_known_to_real_system("+491707654321") is True
    assert R._is_known_to_real_system("+49999999999") is False
