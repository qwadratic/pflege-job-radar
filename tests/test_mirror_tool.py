"""The recorder tool tools/mirror.py: the commands a person runs when a new page or board turns up (TASK-197).

`record` itself reads the registry and crawls live sites, so it is not run here; what is run: `add` against a local server (the one
live fetch a test may make is to loopback), `list-urls`, `show`, `sql`, the target resolver, and the comparison that decides whether a
recording replays as it was made.
"""
import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest

from tests import mirror as M
from tests.test_mirror import BOARD, site  # noqa: F401  (fixture: a local stand-in for a clinic site, MIRROR_ROOT in tmp)

_spec = importlib.util.spec_from_file_location("mirror_tool", Path(__file__).resolve().parent.parent / "tools" / "mirror.py")
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)


def _board(bid=BOARD, n_clinics=1, url="https://www.site.test/karriere"):
    return {"board_id": bid, "url": url, "vendor": "wp_jobs", "kind": "vendor", "adapter": "wp_jobs", "walled": None, "n_clinics": n_clinics,
            "board": {"url": url, "vendor": "wp_jobs", "kind": "vendor", "clinics": [{"clinic_id": "36201"}] * n_clinics},
            "recorded_at": "2026-10-01T00:00:00+00:00", "pages": 0, "bytes_raw": 0, "rows": 0, "n_exc": 0, "replay_identical": True,
            "checks": None, "error": None, "refused": []}


def _seed_board(bid=BOARD):
    s = M.Store.new(bid)
    s.save()
    M.update_index(_board(bid))


def test_add_records_one_more_page_and_list_urls_show_sql_find_it(site, capsys):
    _seed_board()
    T.cmd_add(Namespace(board_id=BOARD, url=site + "/page?v=1", method="GET", data=None, scope="adapter", via="requests"))
    T.cmd_add(Namespace(board_id=BOARD, url=site + "/p", method="POST", data=__file__, scope="adapter", via="requests"))
    capsys.readouterr()

    T.cmd_list_urls(Namespace(board_id=BOARD, scope=None))
    listed = capsys.readouterr().out
    assert site + "/page?v=1" in listed and "GET" in listed and "POST" in listed and " 200 " in listed

    T.cmd_show(Namespace(board_id=BOARD, url=site + "/page?v=1", full=False))
    shown = capsys.readouterr().out
    assert "Pflegefachkraft m/w/d /page?v=1" in shown and "Content-Type: text/html" in shown

    T.cmd_sql(Namespace(board_id=BOARD, query="select method, status from responses order by seq"))
    assert capsys.readouterr().out.split() == ["GET", "200", "POST", "200"]

    assert M.read_index()["boards"][BOARD]["pages"] == 2  # the index follows the file
    with M.mirror_board(BOARD):  # and a test can now replay what was added
        import requests
        assert "v=1" in requests.get(site + "/page?v=1", timeout=5).text


def test_show_of_a_url_the_board_does_not_hold_says_so(site):
    _seed_board()
    with pytest.raises(SystemExit, match="holds nothing for"):
        T.cmd_show(Namespace(board_id=BOARD, url=site + "/never-recorded", full=False))


def test_resolve_takes_a_board_id_a_host_a_url_or_a_clinic_id():
    boards = [{"url": "https://www.klinik-a.de/jobs", "clinics": [{"clinic_id": "11"}, {"clinic_id": "12"}]},
              {"url": "https://jobs.b.de/", "clinics": [{"clinic_id": "21"}]}]
    ids = ["typo3_jobs__klinik-a.de", "rexx__jobs.b.de"]
    for token, want in (("typo3_jobs__klinik-a.de", 0), ("klinik-a.de", 0), ("www.klinik-a.de", 0), ("https://jobs.b.de/", 1), ("21", 1), ("12", 0)):
        assert [i for _b, i in T.resolve([token], boards, ids)] == [ids[want]], token
    assert [i for _b, i in T.resolve(["11", "klinik-a.de", "21"], boards, ids)] == ids  # no board twice
    with pytest.raises(SystemExit, match="no board of the registry matches 'nope'"):
        T.resolve(["nope"], boards, ids)


def test_compare_ignores_the_clock_and_the_order_and_names_what_differs():
    row = {"title": "Pflege", "payload": json.dumps({"url": "https://x/1", "observed_at": "2026-10-01T10:00:00"}), "observed_at": "t0", "inbox_id": 7}
    a = {"rows": [row, {"title": "Arzt"}], "calls": ["u2", "u1"], "checks": {"public url": (True, "ok")}}
    same = {"rows": [{"title": "Arzt"}, dict(row, observed_at="t1", inbox_id=9, payload=json.dumps({"url": "https://x/1", "observed_at": "later"}))],
            "calls": ["u1", "u2"], "checks": {"public url": (True, "ok")}}
    assert T.compare(a, same) == (True, None)
    other = {"rows": [{"title": "Arzt"}], "calls": ["u1"], "checks": {"public url": (False, "gone")}}
    ok, why = T.compare(a, other)
    assert not ok and "only in the live run 1" in why and "adapter calls differ (2 live, 1 replay)" in why and "check public url" in why
