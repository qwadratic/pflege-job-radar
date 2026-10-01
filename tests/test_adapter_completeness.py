"""Adapter-vs-board completeness (TASK-26/27), run against the local mirror of the boards (TASK-197) -- never a live site.

Read tests/adapter_contract.py in full first, then tests/adapter_harness.py (the checks and the mutations live there, shared
with the recorder tools/mirror.py). This file wires them onto the boards of the mirror index (data/mirror/INDEX.json, no
network at collection or at run time) and onto the exact code path app/crawl.py runs (app.crawl._vendor_rows /
app.crawl._seed_obs, group_portal_for first), replayed from the frozen recording of each board.

The board is the oracle, never our own parser: what the board's own client reads and declares, as recorded. Five checks per
(adapter family, board): read-path coverage, declared-total parity, field completeness, public url, round trip. Plus one
mutation meta-test per tests.adapter_contract.MUTATIONS key, run on ONE representative board per adapter family (the family's
biggest board that returns rows), asserting the matching check goes red and nothing else does.

A new page shape, a new board, a posting an adapter misreads: re-record it (.venv/bin/python tools/mirror.py record <board_id |
host | clinic_id>), write the red test on the mirror, fix, green. A request the mirror lacks fails the test and names that command.

    .venv/bin/python -m pytest tests/test_adapter_completeness.py -q                       # everything
    .venv/bin/python -m pytest tests/test_adapter_completeness.py -k smartrecruiters -q    # one family
    .venv/bin/python -m pytest tests/test_adapter_completeness.py -k "kbo.de" -q           # one board
    .venv/bin/python -m pytest tests/test_adapter_completeness.py -m mutation -q           # meta-tests only
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import adapter_contract as AC  # noqa: E402
from tests import adapter_harness as H  # noqa: E402
from tests import mirror as M  # noqa: E402

# --- the boards of the mirror, biggest first (the recorder sorts the same way) --------------------
_INDEX = M.read_index_or_none()
_ENTRIES = sorted(_INDEX["boards"].values(), key=lambda e: (-e["n_clinics"], e["board_id"])) if _INDEX else []
_BOARD_LIST = [dict(e["board"], board_id=e["board_id"]) for e in _ENTRIES]
_BOARD_IDS = [e["board_id"] for e in _ENTRIES]
_FAMILY_BOARDS = H.family_boards(_BOARD_LIST)

# No mirror at all must be a loud failure of every case that needs it, never an empty (skipped) parametrisation.
_NO_MIRROR = "no-mirror"


def _need_mirror():
    M.read_index()  # raises MirrorMiss with the command that records the boards
    raise M.MirrorMiss(f"{M.index_path()} lists no board: record them with .venv/bin/python {M.RECORD} record --all")


@pytest.fixture(scope="module", params=_BOARD_LIST or [None], ids=_BOARD_IDS or [_NO_MIRROR])
def board(request):
    if request.param is None:
        _need_mirror()
    return request.param


@pytest.fixture(scope="module")
def adapter_result(board):
    """One run per board, shared by every check function that asks for it."""
    return H.run_adapter(board)


# ---------------------------------------------------------------------------------------------
# checks -- one pytest id per (board, check)
# ---------------------------------------------------------------------------------------------
@pytest.mark.completeness
def test_read_path_coverage(board, adapter_result):
    _rows, calls = adapter_result
    ok, detail = H.check_read_path_coverage(board, calls, H.client_for(board))
    assert ok, H.msg(board, "read-path coverage", detail)


@pytest.mark.completeness
def test_declared_total_parity(board, adapter_result):
    rows, _calls = adapter_result
    ok, detail = H.check_declared_total_parity(board, rows, H.client_for(board))
    assert ok, H.msg(board, "declared-total parity", detail)


@pytest.mark.completeness
def test_field_completeness(board, adapter_result):
    rows, _calls = adapter_result
    ok, detail = H.check_field_completeness(board, rows)
    assert ok, H.msg(board, "field completeness", detail)


@pytest.mark.completeness
def test_public_url(board, adapter_result):
    rows, _calls = adapter_result
    ok, detail = H.check_public_url(board, rows)
    assert ok, H.msg(board, "public url", detail)


@pytest.mark.completeness
def test_round_trip(board, adapter_result):
    rows, _calls = adapter_result
    ok, detail = H.check_round_trip(board, rows)
    assert ok, H.msg(board, "round trip", detail)


# ---------------------------------------------------------------------------------------------
# mutations (TASK-27): break an adapter on purpose on one board per family and prove exactly the matching check goes red
# ---------------------------------------------------------------------------------------------
@pytest.mark.mutation
@pytest.mark.parametrize("mutation_name", sorted(AC.MUTATIONS))
@pytest.mark.parametrize("family", sorted(_FAMILY_BOARDS) or [_NO_MIRROR])
def test_mutation(monkeypatch, family, mutation_name):
    if family == _NO_MIRROR:
        _need_mirror()
    board, baseline_checks, client, baseline_rows, baseline_calls, rows, calls, checks = H.mutated_run(
        monkeypatch, family, mutation_name, _FAMILY_BOARDS)

    if H.no_observable_effect(mutation_name, board, client, baseline_rows, baseline_calls, rows, calls):
        pytest.skip(H.msg(board, f"mutation {mutation_name}", "adapter shape has nothing this mutation can break here"))

    target = H.TARGET_CHECK[mutation_name]
    ok, detail = checks[target]
    assert not ok, H.msg(board, f"mutation {mutation_name} -> {target}", "expected this check to go red, it stayed green")
    for name, (ok2, detail2) in checks.items():
        if name == target or not baseline_checks[name]:
            continue  # already red before the mutation -- a pre-existing finding, not collateral damage
        assert ok2, H.msg(board, f"mutation {mutation_name} -> collateral {name}", detail2)
