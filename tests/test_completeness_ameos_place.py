"""AMEOS (karriere.ameos.eu): every posting carries the place its own page states. A case found on 2026-10-01 and the worked
example of the process in CLAUDE.md ("Tests never touch a live site", TASK-197).

The defect: on base 111e8f7 crawl_wp_jobs stores the seed clinic's town (Neuburg/Donau) on all 779 postings of this board, which spans
AMEOS sites from Osnabrueck to Simbach. The board is the oracle, never our parser: each posting's page says where it is as schema.org
microdata (`<meta itemprop="jobLocation" content="Haldensleben" />`), read here straight off the recorded page. The five shared
checks cannot see it (they pass 5/5 on this board): a city is on every row, it is just the wrong one.

The steps this file went through:
  1. the page shape is new  ->  .venv/bin/python tools/mirror.py record karriere.ameos.eu     (888 pages, 59.8 MB raw, 0.66 MB on disk)
  2. the test below, written on the mirror   ->  RED on base:  `pytest tests/test_completeness_ameos_place.py --runxfail`
       "779/779 postings carry a city their own page contradicts, e.g. ... 'Neuburg/Donau', 'Haldensleben'"
  3. the fix (TASK-185, 6456a2a, on main since 2026-10-05: the posting's own place is read before any seed stamp)  ->  GREEN, same command
     (the test carried an xfail marker until that fix was merged; it XPASSed on the first run over the merged adapter and the marker went)
"""
import re

import pytest
import requests

from tests import adapter_harness as H
from tests import mirror as M

_PLACE = re.compile(r'itemprop="jobLocation"\s+content="([^"]*)"', re.I)


def _norm(s):
    return re.sub(r"[^a-zäöüß]+", " ", (s or "").lower()).strip()


@pytest.mark.completeness
def test_every_ameos_posting_carries_the_place_its_own_page_states():
    board = H.board_with("karriere.ameos.eu")
    rows, _calls = H.run_adapter(board)
    assert rows, "the adapter returned nothing for the board"
    wrong, declared_n = [], 0
    with M.mirror_board(board["board_id"], scope="adapter"):
        for r in rows:
            url = H.url_of(r)
            m = _PLACE.search(requests.get(url, timeout=25).text)
            if not (m and m.group(1).strip()):
                continue  # a posting that states no place (one has an empty jobLocation) has nothing to compare against
            declared_n += 1
            city = H.field("vendor", r, "city")
            if _norm(city) not in _norm(m.group(1)):
                wrong.append((url, city, m.group(1)))
    assert declared_n, "no posting of the board declares a jobLocation: the oracle found nothing to compare"
    assert not wrong, (f"karriere.ameos.eu :: place :: {len(wrong)}/{declared_n} postings carry a city their own page contradicts, "
                       f"e.g. {[(u.rsplit('/', 1)[-1][:40], c, p) for u, c, p in wrong[:3]]}")
