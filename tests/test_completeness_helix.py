"""Adapter-specific red tests for helix (TASK-34), on top of the shared harness in
tests/test_adapter_completeness.py. The shared field-completeness check only asks whether
description/city/datePosted/employmentType are populated on *some* row -- it never looks at
whether a value is actually clean. helix's listing card wraps the job title AND its schedule/
contract-type badge spans in one anchor (see parse_helix's docstring), so a naive scrape of the
listing anchor text pollutes the title with badge fragments ("... OKH Vollzeit oder Teilzeit
Festanstellung") that the shared harness would never notice. These tests would have caught that
bug before the shared five checks would.

    .venv/bin/python -m pytest tests/test_completeness_helix.py -m completeness -q
"""
import os
import re
import sys
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import adapter_contract as AC  # noqa: E402
from crawlers import vendor_adapters as VA  # noqa: E402

BADGE_WORDS = ("Vollzeit", "Teilzeit", "Festanstellung", "Befristet", "Treffer")
REAL_BOARDS = [
    "https://bezirk-unterfranken.helixjobs.com/okh/joblist",
    "https://bezirk-unterfranken.helixjobs.com/tzbu/career",
]


def _offline_reason():
    if AC.offline():
        return "PFLEGE_TESTS_OFFLINE=1"
    try:
        req = urllib.request.Request(AC.PROXY + "/clinics?select=clinic_id&limit=1", headers=AC.HDR)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        return f"registry proxy unreachable: {type(e).__name__}: {e}"
    return None


_SKIP_REASON = _offline_reason()
if _SKIP_REASON:
    pytest.skip(f"helix completeness harness offline: {_SKIP_REASON}", allow_module_level=True)


@pytest.mark.network
@pytest.mark.completeness
@pytest.mark.parametrize("careers_url", REAL_BOARDS)
def test_helix_title_carries_no_listing_badge_text(careers_url):
    """The joblist card's own anchor wraps the title together with its schedule/contract-type
    badge spans -- a title scraped straight off that anchor (rather than the jobad detail's own
    JSON-LD title) pollutes every row with badge words no real job title contains."""
    rows = VA.crawl_helix({"name": "Test", "careers_url": careers_url})
    assert rows, f"no rows returned for {careers_url}"
    for r in rows:
        title = r["payload"]["title"]
        hit = [w for w in BADGE_WORDS if re.search(r"\b%s\b" % w, title, re.I)]
        assert not hit, f"{careers_url}: title {title!r} still carries listing badge text {hit}"


@pytest.mark.network
@pytest.mark.completeness
@pytest.mark.parametrize("careers_url", REAL_BOARDS)
def test_helix_detail_fetch_populates_description(careers_url):
    """jobad?prj= carries the real description/city/datePosted/employmentType the listing card
    lacks -- crawl_helix must fetch it per row, not stop at the listing scrape."""
    rows = VA.crawl_helix({"name": "Test", "careers_url": careers_url})
    assert rows, f"no rows returned for {careers_url}"
    for r in rows:
        p = r["payload"]
        assert p["url"].count("jobad?prj=") == 1, f"stored url is not a jobad detail page: {p['url']!r}"
        assert p.get("description") and len(p["description"]) > 50, (
            f"{careers_url}: {p['url']} has no real description -- detail page was not fetched")
