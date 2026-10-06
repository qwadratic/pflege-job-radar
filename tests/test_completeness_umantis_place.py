"""umantis: a posting whose page names its facility carries that facility's town, never the seed clinic's town (TASK-431.9, case 1).

The ANregiomed board (recruitingapp-5511.de.umantis.com) serves the postings of Ansbach, Dinkelsbuehl, Rothenburg and the MVZ to the
board of the Rothenburg clinic. A detail page states its site only in a facility label, `<i class="home-icon"></i><div>Klinikum Ansbach</div>`
("Klinik Rothenburg", "MVZ Dinkelsbuehl", "Standortuebergreifend"). career_crawl read no place from it and stamped the seed clinic's town
(Rothenburg, city_source 'seed'), so 18 of the 22 postings without another place statement were filed under the Rothenburg clinic although
the page says Ansbach or Dinkelsbuehl. The board is the oracle: the town the label ends with.

  1. the page shape is on the mirror  ->  .venv/bin/python tools/mirror.py status    (umantis__anregiomed.de)
  2. the test below, on the mirror     ->  RED:   "18 postings state a site in one town and carry another"
  3. the fix: the label is read as the posting's place when it ends in a registry town  ->  GREEN
"""
import json
import re

import pytest
import requests

from pflege_jobs.registry import _town_match, city_key
from tests import adapter_contract as AC
from tests import adapter_harness as H
from tests import mirror as M

# reads real boards from the mirror (INDEX.json, <board>.sqlite.xz): `-m "not mirror"` runs without a pulled mirror
pytestmark = pytest.mark.mirror

_LABEL = re.compile(r'<i class="home-icon"></i>\s*<div>\s*(.*?)\s*</div>', re.S)
_BOARDS = [b for b in H.indexed_boards() if b["board_id"] == "umantis__anregiomed.de"]
_PARAMS, _IDS = H.board_params(_BOARDS, lambda b: b["board_id"])


def _payload(row):
    p = row.get("payload")
    return json.loads(p) if isinstance(p, str) else (p or {})


def _town_the_label_ends_with(label, towns):
    """The registry town the label ends with ('Klinikum Ansbach' -> 'ansbach'), or None. Own code, not the adapter's."""
    words = label.lower().split()
    return next((" ".join(words[i:]) for i in range(len(words)) if " ".join(words[i:]) in towns), None)


@pytest.mark.completeness
@pytest.mark.parametrize("board", _PARAMS, ids=_IDS)
def test_umantis_posting_that_names_its_site_carries_that_towns_name(board):
    if board is None:
        H.need_mirror()
    towns = set(M.Store.load_shared(board["board_id"]).meta("towns"))
    rows, _ = H.run_adapter(board)
    session = requests.Session()
    checked, wrong = 0, []
    with M.mirror_board(board["board_id"], scope="adapter"):
        for r in rows:
            page = AC._get(r["source_url"], session=session)
            m = _LABEL.search(page.text) if page is not None else None
            stated = _town_the_label_ends_with(m.group(1), towns) if m else None
            if not stated:
                continue
            checked += 1
            if not _town_match(city_key(stated), city_key(r["city"])):
                wrong.append((m.group(1), r["city"], _payload(r).get("city_source")))
    assert checked, "no posting of the board names a site in a registry town: the oracle found nothing to compare"
    assert not wrong, (f"{board['board_id']} :: place :: {len(wrong)}/{checked} postings state a site in one town and carry another, "
                       f"e.g. {sorted(set(wrong))[:3]} (page label, row city, city_source)")
