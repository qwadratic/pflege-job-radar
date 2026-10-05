"""Sana (jobs.sana.de, Oracle HCM Candidate Experience): every requisition the site lists is read with its own ad text. The second
worked example of the process in CLAUDE.md ("Tests never touch a live site", TASK-197; the first is test_completeness_ameos_place.py).

The defect (TASK-184, found 2026-10-01): jobs.sana.de is a client-rendered SPA, every page under it is the same shell whose <title> is
"Sana". crawl_oracle fell back to crawl_wp_jobs, which stored that title as the text of a posting (4 chars), under the seed clinic's
town, and read 0 rows from a board URL on jobs.sana.de itself. The five shared checks could not tell: this board's recording of
2026-10-02 (old adapter) carries only a red read-path coverage, the rest was "green" because there was nothing to compare.

The board is the oracle: the SPA's own REST API names the total (`TotalJobsCount` of the first page of `recruitingCEJobRequisitions`),
read here straight off the recorded answer.

The steps this file went through:
  1. the page shape is new  ->  .venv/bin/python tools/mirror.py record oracle__jobs.sana.de-1   (the recorder runs the production path:
     with the fixed adapter it also records the REST list and one detail per requisition: ~1,100 pages, 19 MB raw, 1.8 MB on disk)
  2. this test, on a mirror recorded by the adapter of base 111e8f7 (9bf74cd reverted)  ->  RED: "the adapter returned nothing for the board"
  3. the fix (9bf74cd on main: crawl_oracle reads the REST API, list then detail)  ->  GREEN on the mirror recorded with it, same command
"""
import json
import re
from urllib.parse import urlparse

import pytest

from tests import adapter_harness as H
from tests import mirror as M

_LIST = re.compile(r"/recruitingCEJobRequisitions\?")
BOARD_URL = "https://jobs.sana.de/de/sites/CX_4025/jobs"


def _declared_total(board_id):
    """TotalJobsCount of the first list page the recorded run read: what the site itself says it lists."""
    store = M.Store.load_shared(board_id)
    for r in store.rows():
        if r.method == "GET" and r.body_sha and _LIST.search(r.url) and re.search(r"offset=0(&|$)", r.url):
            return json.loads(store.body(r.body_sha))["items"][0]["TotalJobsCount"]
    return None


def _shell_title(board_id):
    """The <title> every page of the site (/sites/CX_...) shows: the SPA shell's, as recorded. (Not the host's 404 page, which the jobs.feed.json probe lands on.)"""
    store = M.Store.load_shared(board_id)
    for r in store.rows():
        if r.status == 200 and r.body_sha and urlparse(r.url).hostname == "jobs.sana.de" and "/sites/CX_" in r.url:
            m = re.search(r"<title>([^<]*)</title>", store.body(r.body_sha).decode("utf-8", "replace"))
            if m:
                return m.group(1).strip()
    return None


@pytest.mark.completeness
def test_every_sana_requisition_is_read_with_its_own_ad_text():
    board = H.board_for_url(BOARD_URL)
    rows, _calls = H.run_adapter(board)
    assert rows, f"{board['board_id']} :: oracle cx :: the adapter returned nothing for the board"
    declared = _declared_total(board["board_id"])
    assert declared, f"{board['board_id']} :: oracle cx :: the mirror holds no REST list answer: the oracle found nothing to compare"
    assert len(rows) == declared, (f"{board['board_id']} :: oracle cx :: the site's own list declares {declared} requisitions, "
                                   f"the adapter returned {len(rows)}")
    shell = _shell_title(board["board_id"])
    assert shell, f"{board['board_id']} :: oracle cx :: the mirror holds no answer of jobs.sana.de with a <title>"
    no_ad = [H.url_of(r) for r in rows if (H.field("vendor", r, "description") or "").strip().casefold() in ("", shell.casefold())]
    assert not no_ad, (f"{board['board_id']} :: oracle cx :: {len(no_ad)}/{len(rows)} postings carry no ad text of their own "
                       f"(nothing, or the SPA shell's title {shell!r} that every page under the site shows), e.g. {no_ad[:3]}")
