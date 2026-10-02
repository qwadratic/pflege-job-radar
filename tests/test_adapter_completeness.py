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
_BOARD_LIST = H.indexed_boards()
_FAMILY_BOARDS = H.family_boards(_BOARD_LIST)
_PARAMS, _IDS = H.board_params(_BOARD_LIST)  # no mirror at all: one case that fails saying how to record it, never a skip


@pytest.fixture(scope="module", params=_PARAMS, ids=_IDS)
def board(request):
    if request.param is None:
        H.need_mirror()
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
# (family, mutation) pairs whose meta-test cannot show what it is meant to show -- named gaps, found by the first full run over the mirror
MUTATION_GAPS = {
    ("crawlers.vendor_adapters:crawl_drv_bund", "cap_first_page"):
        "the adapter refuses a board read that stops short (RuntimeError 'listing page=1 failed (404) -- board read stops short') instead of "
        "returning fewer rows, so declared_total_parity never gets the chance to go red",
    ("crawlers.vendor_adapters:crawl_oracle", "skip_detail"):
        "the oracle adapter builds every row from the detail fetch: blocking it returns 0 rows, so declared_total_parity goes red as well "
        "(collateral by construction)",
}
_MUTATION_PAIRS = [pytest.param(f, m, id=f"{f}-{m}", marks=[pytest.mark.xfail(reason="named gap: " + MUTATION_GAPS[f, m], strict=False)] if (f, m) in MUTATION_GAPS else [])
                   for f in sorted(_FAMILY_BOARDS) for m in sorted(AC.MUTATIONS)] or [pytest.param("no-mirror", "no-mirror", id="no-mirror")]


@pytest.mark.mutation
@pytest.mark.parametrize("family,mutation_name", _MUTATION_PAIRS)
def test_mutation(monkeypatch, family, mutation_name):
    if family == "no-mirror":
        H.need_mirror()
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
