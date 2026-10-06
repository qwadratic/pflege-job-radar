"""kbo.de: a posting whose page states no site of work gets NO place, never the group's head-office address (TASK-431.9).

kbo.de's pages carry the group headquarters in the JSON-LD jobLocation (Prinzregentenstrasse 18, 80538 Muenchen, 105 of 105
postings on the mirrored board) and in the page chrome (Postfach 80502 Muenchen, contact blocks). crawl_group_portal drops the
JSON-LD place (hq_location_untrusted) and reads the posting's own site of work in this order: the "Einsatzort" site block, a city
the title names, an "Einsatzort:" label. Its last rung read the first "<PLZ> <Ort>" pair anywhere in the page text -- which on this
site is always page furniture. Five stored postings (6622, 6624, 7395, 7396, 7397; hiringOrganization as employer, so no site block
had fired) carry exactly that: Muenchen 80538.

Every posting of the mirrored board names its site (site block or title), so the mirror holds no page of that shape today (the five
stored ones left the board). The case is built from the mirror's own pages: each posting page of the board with the three places it
may state its site taken out (the site block, the title, the "Einsatzort/Arbeitsort/Dienstort/Standort" labels). What is left is
what a page without a stated site looks like. The board is the oracle: the page now says nothing, so the answer is no place.

  1. the page shape is on the mirror      ->  .venv/bin/python tools/mirror.py status    (typo3_jobs__kbo.de-1)
  2. the test below, on the mirror         ->  RED:   105/105 postings carry a place read off page furniture, 48 of them 80502 Muenchen,
                                               others "Ingolstadt E-Mail" and "Tel"
  3. the fix: the whole-page PLZ-Ort rung of the hq_location_untrusted branch is gone  ->  GREEN
"""
import re

import pytest

from crawlers import vendor_adapters as VA
from tests import adapter_harness as H
from tests import mirror as M

# reads real boards from the mirror (INDEX.json, <board>.sqlite.xz): `-m "not mirror"` runs without a pulled mirror
pytestmark = pytest.mark.mirror

KBO = VA.GROUP_PORTALS[0]
_LABELS = re.compile(r"(Einsatzort|Arbeitsort|Dienstort|Standort)", re.I)
_JSONLD_TITLE = re.compile(r'("title"\s*:\s*")[^"]*(")')
_H1 = re.compile(r"(?is)<h1[^>]*>.*?</h1>")


def _without_a_stated_site(html):
    html = KBO["site_block_rx"].sub("", html)
    html = _H1.sub("<h1>Pflegekraft (m/w/d)</h1>", html)
    html = _JSONLD_TITLE.sub(r"\1Pflegekraft (m/w/d)\2", html)
    return _LABELS.sub("Ort-", html)


@pytest.mark.completeness
def test_kbo_page_that_states_no_site_of_work_gets_no_place_not_the_page_furniture(monkeypatch):
    board = H.board_with("kbo.de/karriere/jobs")
    towns = set(M.Store.load_shared(board["board_id"]).meta("towns"))
    real_get = VA.get

    def get(url, session=None, **kw):
        r = real_get(url, session=session, **kw)
        if r is not None and r.ok and "/karriere/jobs/" in url:
            r._content = _without_a_stated_site(r.text).encode("utf-8")
        return r

    monkeypatch.setattr(VA, "get", get)
    with M.mirror_board(board["board_id"], scope="adapter"):
        rows = VA.crawl_group_portal(board["clinics"][0], KBO, towns=towns)
    assert rows, "the adapter returned nothing for the board"
    placed = [(r["payload"]["loc"][0]["city"], r["payload"]["loc"][0]["plz"]) for r in rows
              if r["payload"]["loc"][0]["city"] or r["payload"]["loc"][0]["plz"]]
    assert not placed, (f"{board['board_id']} :: place :: {len(placed)}/{len(rows)} postings whose page states no site of work carry a "
                        f"place read off the page furniture, e.g. {sorted(set(placed))[:4]}")
