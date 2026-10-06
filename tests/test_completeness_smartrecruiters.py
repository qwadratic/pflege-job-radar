"""Adapter-specific completeness test for smartrecruiters (TASK-28), on top of the shared harness in
tests/adapter_harness.py (run against the local mirror of the boards, TASK-197). The shared field-completeness check only
requires description on ANY row -- it would stay green for a bug that fetches the per-posting detail endpoint for just the
first posting and leaves the rest None. This asserts description on EVERY row with a public url, since the detail fetch here
is unconditional (no sampling, see crawl_smartrecruiters)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import adapter_harness as H  # noqa: E402

# reads real boards from the mirror (INDEX.json, <board>.sqlite.xz): `-m "not mirror"` runs without a pulled mirror
pytestmark = pytest.mark.mirror

_SR_BOARDS = [b for b in H.indexed_boards() if b["kind"] == "vendor" and b.get("adapter", "").endswith(":crawl_smartrecruiters")]
_PARAMS, _IDS = H.board_params(_SR_BOARDS, lambda b: H.board_host(b["url"]))


@pytest.mark.completeness
@pytest.mark.parametrize("board", _PARAMS, ids=_IDS)
def test_every_row_carries_its_own_description(board):
    if board is None:
        H.need_mirror()
    rows, _calls = H.run_adapter(board)
    missing = [r["payload"]["title"] for r in rows if not H.field("vendor", r, "description")]
    assert not missing, (
        f"smartrecruiters @ {board['url']} :: per-row description :: "
        f"{len(missing)}/{len(rows)} rows have no description, e.g. {missing[:3]!r}"
    )
