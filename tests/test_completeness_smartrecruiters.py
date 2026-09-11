"""Adapter-specific completeness test for smartrecruiters (TASK-28), on top of the shared harness in
tests/test_adapter_completeness.py. The shared field-completeness check only requires description on
ANY row -- it would stay green for a bug that fetches the per-posting detail endpoint for just the
first posting and leaves the rest None. This asserts description on EVERY row with a public url,
since the detail fetch here is unconditional (no sampling, see crawl_smartrecruiters)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import adapter_contract as AC  # noqa: E402
from tests.test_adapter_completeness import _SKIP_REASON, _board_host, _field, run_adapter  # noqa: E402

if _SKIP_REASON:
    pytest.skip(f"adapter-completeness harness offline: {_SKIP_REASON}", allow_module_level=True)

_SR_BOARDS = [b for b in AC.boards().values()
              if b["kind"] == "vendor" and b.get("adapter", "").endswith(":crawl_smartrecruiters")]


@pytest.mark.network
@pytest.mark.completeness
@pytest.mark.parametrize("board", _SR_BOARDS, ids=[_board_host(b["url"]) for b in _SR_BOARDS])
def test_every_row_carries_its_own_description(board):
    rows, _calls = run_adapter(board)
    missing = [r["payload"]["title"] for r in rows if not _field("vendor", r, "description")]
    assert not missing, (
        f"smartrecruiters @ {board['url']} :: per-row description :: "
        f"{len(missing)}/{len(rows)} rows have no description, e.g. {missing[:3]!r}"
    )
