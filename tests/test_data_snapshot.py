"""app/data.py's in-memory snapshot cache: a failed _build() must not fake freshness or hide
behind a false 404 (TASK-91). Unlike test_app_api.py's `client` fixture, these call refresh()/
snapshot() directly (no stubbing) so the actual except-branch behaviour is what runs."""
import time

import pytest
from fastapi import HTTPException

from app import data as D

EMPTY = {"jobs": [], "clinics": [], "by_clinic": {}, "facets": {}, "taxonomy": {}, "loading": False, "error": None}


@pytest.fixture(autouse=True)
def _restore_snap():
    saved = dict(D._snap)
    yield
    D._snap.clear()
    D._snap.update(saved)


def _boom():
    raise RuntimeError("v_postings 500")


def test_a_failed_build_does_not_reset_the_ttl(monkeypatch):
    D._snap.update({**EMPTY, "at": 111.0})
    monkeypatch.setattr(D, "_build", _boom)
    D.refresh()
    # Before the fix this line set `at = time.time() - D.TTL + 60`, making a failed build look
    # like it had just refreshed -- snapshot()'s own `stale` check would stay False for the next
    # ~60s and never retry. `at` must be left exactly as it was.
    assert D._snap["at"] == 111.0
    assert D._snap["loading"] is False
    assert "RuntimeError" in D._snap["error"] and "v_postings 500" in D._snap["error"]


def test_snapshot_raises_503_instead_of_silently_serving_empty_after_a_failed_build(monkeypatch):
    D._snap.update({**EMPTY, "at": 0.0})
    monkeypatch.setattr(D, "_build", _boom)
    with pytest.raises(HTTPException) as exc:
        D.snapshot()
    # Before the fix, snapshot() returned the empty _snap unchanged: D.clinic(any_id) then
    # returned None for every id, indistinguishable from a genuinely unknown clinic_id --
    # app/main.py's `if not c: raise HTTPException(404, "unknown clinic")` turned a transient
    # Supabase failure into a false 404 for every clinic on the board.
    assert exc.value.status_code == 503
    assert "v_postings 500" in exc.value.detail


def test_a_stale_but_populated_snapshot_still_serves_the_last_good_data(monkeypatch):
    old_clinics = [{"clinic_id": "1", "name": "Test Klinik"}]
    D._snap.update({**EMPTY, "at": time.time() - D.TTL - 1, "clinics": old_clinics, "by_clinic": {"1": old_clinics[0]}})
    monkeypatch.setattr(D, "_build", _boom)
    # stale-but-not-empty: served immediately, refresh happens in a background thread -- must NOT
    # raise just because a retry is also in flight (this is not the AC#2 "empty + error" case).
    snap = D.snapshot()
    assert snap["clinics"] == old_clinics


def test_an_empty_snapshot_with_no_error_yet_is_not_mistaken_for_a_failure(monkeypatch):
    # First call ever (or a legitimately empty registry): error is None, not a failure -- refresh()
    # must actually run (not be skipped) and, on success, snapshot() must return real data rather
    # than raising just because clinics started out empty.
    D._snap.update({**EMPTY, "at": 0.0})
    monkeypatch.setattr(D, "_build", lambda: {**EMPTY, "at": time.time(), "clinics": [{"clinic_id": "9"}], "by_clinic": {"9": {"clinic_id": "9"}}})
    snap = D.snapshot()
    assert snap["clinics"] == [{"clinic_id": "9"}]
    assert snap["error"] is None


def test_a_failed_refresh_of_a_stale_snapshot_still_reads_as_stale_afterwards(monkeypatch):
    # The bug's real-world trigger (TASK-89's Hygienefachkraft-class failures): a snapshot that HAD
    # good data goes stale, a transient v_postings 500 hits mid-refresh. snapshot()'s stale branch
    # backgrounds the retry and is inherently async/racy to assert on directly, but the guarantee
    # that makes retrying possible at all is deterministic and belongs to refresh() alone: a failed
    # refresh() must leave `at` exactly where a caller's own `stale` check still says True,
    # otherwise the next snapshot() call (sync or background) never even tries again.
    old_clinics = [{"clinic_id": "1", "name": "Test Klinik"}]
    old_at = time.time() - D.TTL - 100
    D._snap.update({**EMPTY, "at": old_at, "clinics": old_clinics, "by_clinic": {"1": old_clinics[0]}})
    monkeypatch.setattr(D, "_build", _boom)
    D.refresh()
    assert D._snap["at"] == old_at                                  # untouched, not bumped forward
    assert time.time() - D._snap["at"] > D.TTL                      # still reads as stale -> will retry
    assert D._snap["clinics"] == old_clinics                        # last good data still there to serve
