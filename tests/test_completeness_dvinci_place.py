"""dvinci: a posting that names its facility carries the town of that facility, never the seed clinic's town (TASK-431.9).

The Sozialstiftung Bamberg tenant (sozialstiftung-bamberg.dvinci-easy.com) serves 121 postings to three registry boards: the
foundation's own page (4 clinics, the first one in Forchheim), the Saludis rehabilitation page and the paediatric psychosomatics
page (both Bamberg). Every posting states its facility in jobOpening.location ("Klinikum Bamberg", "Zentrum fuer rehabilitative
Medizin Bamberg", one "Aerztliches Praxiszentrum Forchheim"). parse_dvinci rejects such a label as a city (it is a facility name,
not a town) and crawl_dvinci then stamped the TRIGGERING clinic's town on the row, so the same 121 postings came out as Forchheim
on the first board and as Bamberg on the other two. The board is the oracle: the town its own label ends with.

  1. the page shape is on the mirror      ->  .venv/bin/python tools/mirror.py status    (dvinci__sozialstiftung-bamberg.de)
  2. the test below, on the mirror         ->  RED:   first board "112/113 postings state a facility in one town and carry another" (Bamberg
                                               facilities stamped Forchheim); the other two boards "1/113" (the Forchheim practice centre stamped Bamberg)
  3. the fix: a facility label that ends in a registry town gives the posting's town (crawl_dvinci takes `towns`)  ->  GREEN
"""
import json
import re

import pytest
import requests

from app import crawl as AppCrawl
from crawlers import vendor_adapters as VA
from tests import adapter_contract as AC
from tests import adapter_harness as H
from tests import mirror as M

# reads real boards from the mirror (INDEX.json, <board>.sqlite.xz): `-m "not mirror"` runs without a pulled mirror
pytestmark = pytest.mark.mirror

_ID = re.compile(r"/jobs/(\d+)")
_BOARDS = [b for b in H.indexed_boards() if b.get("vendor") == "dvinci" and "sozialstiftung-bamberg" in b["url"]]
_PARAMS, _IDS = H.board_params(_BOARDS, lambda b: b["board_id"])


def _town_the_label_ends_with(label, towns):
    """The registry town a facility label ends with ('Klinikum Bamberg' -> 'bamberg'), or None. Own code, not the adapter's."""
    first = (label or "").split(",")[0].strip().lower()
    return next((t for t in sorted(towns, key=len, reverse=True) if first.endswith(t) and (len(first) == len(t) or first[-len(t) - 1] == " ")), None)


@pytest.mark.completeness
@pytest.mark.parametrize("board", _PARAMS, ids=_IDS)
def test_dvinci_posting_that_names_its_facility_carries_that_towns_name(board):
    if board is None:
        H.need_mirror()
    c = board["clinics"][0]
    towns = set(M.Store.load_shared(board["board_id"]).meta("towns"))
    with M.mirror_board(board["board_id"], scope="adapter"):
        session = requests.Session()
        host = VA.dvinci_host(c["careers_url"], session=session)
        listing = json.loads(AC._get(f"https://{host}/jobPublication/list.json", session=session).text)
        rows = AppCrawl._vendor_rows(board, c, session, lambda *_: None, towns=towns)
    stated = {}
    for j in listing:
        m = _ID.search(j.get("jobPublicationURL") or "")
        town = _town_the_label_ends_with((j.get("jobOpening") or {}).get("location"), towns)
        if m and town:
            stated[m.group(1)] = town
    assert stated, "no posting of the board names a facility in a registry town: the oracle found nothing to compare"
    wrong = []
    for r in rows:
        m = _ID.search(r["payload"]["url"])
        city = (r["payload"]["loc"][0]["city"] or "").lower()
        if m and m.group(1) in stated and city != stated[m.group(1)]:
            wrong.append((stated[m.group(1)], r["payload"]["loc"][0]["city"], r["payload"].get("city_source")))
    assert not wrong, (f"{board['board_id']} :: place :: {len(wrong)}/{len(stated)} postings state a facility in one town and carry another, "
                       f"e.g. {sorted(set(wrong))[:3]} (stated town, row city, city_source)")
