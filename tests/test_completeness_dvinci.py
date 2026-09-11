"""Adapter-specific completeness test for dvinci (TASK-29).

list.json is a bare JSON list, not a dict -- adapter_contract.declared_total() finds no
totalFound-shaped key in it and falls back to the generic "N Stellen" page-text regex, which is
unreliable here because several dvinci careers_urls are the hospital's own CMS page embedding the
tenant via a widget, not the tenant's own listing page (see crawl_dvinci's docstring). sitemap.xml on
the tenant host lists the same posting ids the board itself considers live -- a second, source-native
oracle for the same declared-total question the shared harness's regex can miss. Uses the shared
harness's own fetch/snapshot helpers (adapter_contract._get/save), no new machinery.
"""
import re

import pytest
import requests

from tests import adapter_contract as AC
from crawlers import vendor_adapters as VA

pytestmark = [pytest.mark.network, pytest.mark.completeness]

if AC.offline():
    pytest.skip("adapter-completeness harness offline: PFLEGE_TESTS_OFFLINE=1", allow_module_level=True)

_DVINCI_BOARDS = [b for b in AC.boards().values() if b.get("vendor") == "dvinci"]

_ID_RX = re.compile(r"/jobs/(\d+)/")


@pytest.mark.parametrize("board", _DVINCI_BOARDS, ids=[b["url"] for b in _DVINCI_BOARDS])
def test_sitemap_ids_match_crawled_rows(board):
    c = board["clinics"][0]
    session = requests.Session()
    host = VA.dvinci_host(c["careers_url"], session=session)
    assert host, f"dvinci @ {board['url']} :: sitemap parity :: could not resolve tenant host"

    r = AC._get(f"https://{host}/sitemap.xml", session=session)
    assert r and r.ok, f"dvinci @ {board['url']} :: sitemap parity :: sitemap.xml unavailable"
    AC.save(board["url"], r.url, r.text, r.status_code, "application/xml")
    sitemap_ids = set(_ID_RX.findall(r.text))

    rows = VA.crawl_dvinci(c, session=session)
    row_ids = {m.group(1) for m in (_ID_RX.search(row["source_url"]) for row in rows) if m}

    missing = sitemap_ids - row_ids
    assert not missing, (f"dvinci @ {board['url']} :: sitemap parity :: {len(missing)} id(s) on "
                          f"sitemap.xml never returned by crawl_dvinci: {sorted(missing)[:5]}")
