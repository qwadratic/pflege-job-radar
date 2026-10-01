"""Adapter-specific completeness test for dvinci (TASK-29), against the local mirror of the boards (TASK-197).

list.json is a bare JSON list, not a dict -- adapter_contract.declared_total() finds no
totalFound-shaped key in it and falls back to the generic "N Stellen" page-text regex, which is
unreliable here because several dvinci careers_urls are the hospital's own CMS page embedding the
tenant via a widget, not the tenant's own listing page (see crawl_dvinci's docstring). sitemap.xml on
the tenant host lists the same posting ids the board itself considers live -- a second, source-native
oracle for the same declared-total question the shared harness's regex can miss. Uses the shared
harness's own fetch helper (adapter_contract._get), no new machinery.

The sitemap page is not part of the standard board recording; this case records it under its own scope the first time:
    MIRROR_RECORD=1 .venv/bin/python -m pytest tests/test_completeness_dvinci.py
"""
import re

import pytest
import requests

from tests import adapter_contract as AC
from tests import adapter_harness as H
from tests import mirror as M
from crawlers import vendor_adapters as VA

_DVINCI_BOARDS = [b for b in H.indexed_boards() if b.get("vendor") == "dvinci"]
_PARAMS, _IDS = H.board_params(_DVINCI_BOARDS, lambda b: b["url"])

# No trailing slash required: parse_dvinci() normalizes a crawled row's own URL to the id-only form
# (strips a trailing /<slug>, see crawl_dvinci's 2026-09-22 dedup comment) -- a stricter r"/jobs/(\d+)/"
# matched sitemap.xml's own <loc> entries (which keep the slug) but zero normalized row URLs, making
# every board look 100% incomplete when crawl_dvinci was actually missing nothing (confirmed live
# 2026-09-23 on all 6 boards: 0 real gaps once the id is matched the same way on both sides).
_ID_RX = re.compile(r"/jobs/(\d+)")


@pytest.mark.completeness
@pytest.mark.parametrize("board", _PARAMS, ids=_IDS)
def test_sitemap_ids_match_crawled_rows(board):
    if board is None:
        H.need_mirror()
    c = board["clinics"][0]
    with M.mirror_board(board["board_id"], scope="dvinci_sitemap"):
        session = requests.Session()
        host = VA.dvinci_host(c["careers_url"], session=session)
        assert host, f"dvinci @ {board['url']} :: sitemap parity :: could not resolve tenant host"

        r = AC._get(f"https://{host}/sitemap.xml", session=session)
        assert r and r.ok, f"dvinci @ {board['url']} :: sitemap parity :: sitemap.xml unavailable"
        sitemap_ids = set(_ID_RX.findall(r.text))

        rows = VA.crawl_dvinci(c, session=session)
    row_ids = {m.group(1) for m in (_ID_RX.search(row["source_url"]) for row in rows) if m}

    missing = sitemap_ids - row_ids
    assert not missing, (f"dvinci @ {board['url']} :: sitemap parity :: {len(missing)} id(s) on "
                          f"sitemap.xml never returned by crawl_dvinci: {sorted(missing)[:5]}")
