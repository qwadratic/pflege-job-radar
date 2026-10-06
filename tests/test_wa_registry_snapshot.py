"""A real consumer for tests/conftest.py's opt-in `registry_snapshot` fixture (2026-10-05 fix pass,
M1): runs app/data.py's REAL loader path -- _build() via refresh(), exactly what production calls on
every snapshot rebuild -- against the committed registry snapshot, and asserts the result has the
shape that fixture promises. Nothing here stubs app.data._build itself (unlike tests/test_data_snapshot
.py, which deliberately does, to test the cache's own failure handling); this is the test that proves
the fixture is actually wired to something, which is the whole reason M1 flagged the old silent seam
as having zero real consumers.

Never touches Supabase (registry_snapshot stubs app.config.rest_get_all) and never touches
data/app.sqlite or any other repo file: app/data.py._build() also calls app/runs.py's
last_run_per_clinic()/career_profiles()/clinic_photos_map()/clinic_blurbs_map(), all local-SQLite
readers, so A.SQLITE_PATH/A.DATA_DIR are redirected into pytest's own tmp_path first (same pattern
tests/test_app_api.py's `client` fixture uses) and R.init() creates that throwaway database's schema
-- never the committed data/app.sqlite in the repo."""
import pytest

from app import config as A
from app import data as D
from app import runs as R
from pflege_jobs import config as C
from tools import wa_registry_fixture as GEN


@pytest.fixture(autouse=True)
def _restore_snap():
    """D._build()/D.refresh() mutate the module-global D._snap -- restore whatever was there before
    this test ran, so a snapshot built from the fixture here never leaks into a later test."""
    saved = dict(D._snap)
    yield
    D._snap.clear()
    D._snap.update(saved)


def _served(fixture):
    return [r for r in fixture["v_postings"] if r.get("role_class") not in C.EXCLUDED_ROLE_CLASSES]


@pytest.fixture()
def built_snapshot(tmp_path, monkeypatch, registry_snapshot):
    """-> (the snapshot D._build() actually produced, the registry_snapshot fixture dict it was
    built from). Real loader path, real-shaped (scrubbed) data, no live system, no repo file."""
    monkeypatch.setattr(A, "SQLITE_PATH", tmp_path / "app.sqlite")
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    R.init()
    snap = D.refresh()
    assert snap["error"] is None, f"the real loader path failed against registry_snapshot: {snap['error']}"
    return snap, registry_snapshot


def test_build_produces_every_clinic_and_posting_the_fixture_has(built_snapshot):
    snap, fixture = built_snapshot
    # 7 clinics: one per Bavarian Regierungsbezirk, the TRIM POLICY tools/wa_registry_fixture.py's
    # own module docstring describes.
    assert len(snap["clinics"]) == 7 == len(fixture["clinics"])
    served = _served(fixture)
    # The fixture also carries rows of excluded role classes (a trimmed live read without the filter);
    # production's role_class=not.in.(...) drops them, and so must the fake.
    assert 0 < len(served) < len(fixture["v_postings"])
    assert len(snap["jobs"]) == len(served)
    assert {c["clinic_id"] for c in snap["clinics"]} == {c["clinic_id"] for c in fixture["clinics"]}
    assert {j["posting_id"] for j in snap["jobs"]} == {r["posting_id"] for r in served}


def test_housing_evidence_is_joined_onto_every_job(built_snapshot):
    snap, fixture = built_snapshot
    # _housing_evidence() reads postings_housing_evidence separately from v_postings (the view does
    # not carry the column at all, app/data.py:238-246) and _build() merges it in by posting_id --
    # this is the one join the committed fixture is specifically shaped to exercise.
    evidence_by_posting = {r["posting_id"]: r.get("enr_housing_evidence") for r in fixture["postings_housing_evidence"]}
    for job in snap["jobs"]:
        assert job["enr_housing_evidence"] == evidence_by_posting.get(job["posting_id"]), (
            f"posting {job['posting_id']}'s enr_housing_evidence did not come through the real join"
        )


def test_filter_jobs_runs_over_the_built_snapshot(built_snapshot):
    """Cheap smoke test for the one other loader-path piece Ivan's ask names ("if cheap"): an
    in-memory filter over a snapshot that is actually D._snap (filter_jobs() reads jobs() ->
    snapshot()["jobs"], not the `snap` dict this fixture also returns for convenience)."""
    snap, fixture = built_snapshot
    rows = D.filter_jobs({})
    assert len(rows) == len(_served(fixture))
    assert {j["posting_id"] for j in rows} == {j["posting_id"] for j in snap["jobs"]}


def test_synthetic_pii_round_trips_through_the_loader_unchanged(built_snapshot):
    """The fixture's own PII scrub (tools/wa_registry_fixture.py, B1) replaced real names/phones with
    synthetic ones before this file was ever committed; this asserts _build() carries those synthetic
    values through byte-for-byte -- not that the loader itself does any scrubbing (it does not), and
    not that the committed fixture still has real PII in it (tools/wa_registry_fixture.py's own
    _verify_scrubbed already guarantees that at generation time)."""
    snap, fixture = built_snapshot
    jobs_by_id = {j["posting_id"]: j for j in snap["jobs"]}
    found_name = found_phone = False

    for row in _served(fixture):
        job = jobs_by_id[row["posting_id"]]
        for key, value in row.items():
            # department_hint is turned from a "|"-joined string into a list by _build() itself
            # (TASK-97) -- a real transform unrelated to the PII scrub, so it is not a byte-for-byte
            # round trip and is skipped here on purpose.
            if not isinstance(value, str) or key == "department_hint":
                continue
            if "Muster" in value:
                found_name = True
                assert job[key] == value, f"v_postings:{row['posting_id']}.{key} lost its synthetic name in _build()"
            for m in GEN._phone_matches(value):
                assert GEN._SYNTHETIC_PHONE_RE.match(m.group(0)), (
                    f"v_postings:{row['posting_id']}.{key} has a non-synthetic phone-shaped substring "
                    f"-- the committed fixture itself is not scrubbed"
                )
                found_phone = True

    for row in fixture["postings_housing_evidence"]:
        value = row.get("enr_housing_evidence")
        job = jobs_by_id.get(row["posting_id"])
        if not isinstance(value, str) or job is None:
            continue
        if "Muster" in value:
            found_name = True
            assert job["enr_housing_evidence"] == value
        for m in GEN._phone_matches(value):
            assert GEN._SYNTHETIC_PHONE_RE.match(m.group(0)), (
                f"postings_housing_evidence:{row['posting_id']} has a non-synthetic phone-shaped "
                f"substring -- the committed fixture itself is not scrubbed"
            )
            found_phone = True

    assert found_name, "no synthetic name ('Muster...') anywhere in the committed fixture -- nothing here to verify round-tripped"
    assert found_phone, "no synthetic phone number anywhere in the committed fixture -- nothing here to verify round-tripped"


@pytest.mark.parametrize("text", [
    # All-zero digits: every format a real ad uses, none of them a number anyone could dial.
    "+49 100 00000000", "(0100) 0000000", "+49-1000-00-0", "+49 (1000) 00 0000", "0100.000.000.00",
    "+49 (0)1000 00 0000", "01000/00-0", "0100 000-0 Durchwahl 00", "0049 100 0000",
])
def test_phone_scrub_finds_every_common_german_format(text):
    assert [m.group(0) for m in GEN._phone_matches(f"Tel. {text}, Mo-Fr")] == [text]


@pytest.mark.parametrize("text", [
    "ab 01.10.2026", "seit 2026-09-05", "Start 04/2026", "0,5 Stellen", "PLZ 91054",
    "2026-09-05T10:22:02.652769+00:00", "stellenangebot-189552729.html", "jobDbPVId=279023813",
])
def test_phone_scrub_leaves_dates_timestamps_and_ids_alone(text):
    assert GEN._phone_matches(text) == []


def test_committed_fixture_has_only_synthetic_phone_numbers(registry_snapshot):
    for table in ("v_postings", "postings_housing_evidence"):
        for row in registry_snapshot[table]:
            for key, value in row.items():
                if isinstance(value, str):
                    for m in GEN._phone_matches(value):
                        assert GEN._SYNTHETIC_PHONE_RE.match(m.group(0)), (row.get("posting_id"), key)
