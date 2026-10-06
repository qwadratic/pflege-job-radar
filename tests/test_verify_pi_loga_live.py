"""Drift guard for the one rung that has no fallback: the P&I LOGA board list, run against the local mirror of the boards.

These boards (data/registry/pi_seeds.json -- 3 Helios + the Regiomed/Sana wildcard, 91 open
postings) expose no per-posting page, so `pflege_jobs.verify.board_titles()` reading the rendered
GWT list IS the liveness check. Nothing else can answer, and the rungs below it would answer off
the board LIST -- which carries every title, removed ones included.

Deliberately not asserting markup: the selectors inside `_list_rows` will churn. What must hold is
behavioural and survives a redesign -- the rung still reads titles off every registered board, and
it still discriminates: a title the board lists reads live, one it does not reads gone. A rung that
has silently stopped working answers neither, or answers "live" to both.

The boards are replayed from their recordings (tests/mirror.py, TASK-197): a markup change on a live board shows up when that
board is re-recorded (.venv/bin/python tools/mirror.py record <host>) and these cases are run on it. The two verify cases make
the same requests as the shared harness's run of the board; what they add is recorded under their own scope the first time:
    MIRROR_RECORD=1 .venv/bin/python -m pytest tests/test_verify_pi_loga_live.py
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs import verify as V  # noqa: E402
from tests import adapter_harness as H  # noqa: E402
from tests import mirror as M  # noqa: E402

# reads real boards from the mirror (INDEX.json, <board>.sqlite.xz): `-m "not mirror"` runs without a pulled mirror
pytestmark = pytest.mark.mirror

SCOPE = "verify_board_list"

SEEDS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "registry", "pi_seeds.json")


# seeds whose host/companyEid is a wildcard no registry board lists as its url: the registry board that is read through them
CARRIER = {"https://logaallin.regiomed-kliniken.de/bewerber-web/?companyEid=%2a": "pi_asp__sana.de",
           "https://brkm.pi-asp.de/bewerber-web/?companyEid=123-FIRMA-ID": "pi_asp__pflegejobs.brk-muenchen.de"}


# named gap: the BRK München seed's placeholder companyEid reads no titles off the board (found while recording it, 2026-10-02; the original
# live test read the same URL and had the same answer, nobody had run it)
GAPS = {"https://brkm.pi-asp.de/bewerber-web/?companyEid=123-FIRMA-ID":
        "named gap: board_titles() reads nothing off the BRK seed's placeholder companyEid (123-FIRMA-ID); BRK is read through its position pages"}


def _case(b):
    return pytest.param(b, id=b, marks=[pytest.mark.xfail(reason=GAPS[b], strict=False)] if b in GAPS else [])


def _board_id(url):
    return CARRIER.get(url) or H.board_for_url(url)["board_id"]


def _boards():
    with open(SEEDS, encoding="utf-8") as f:
        seeds = json.load(f)
    return sorted({"https://%s/bewerber-web/?companyEid=%s" % (s["host"], s["companyEid"]) for s in seeds})


@pytest.fixture(scope="module")
def titles_per_board():
    V.reset_board_titles_cache()
    out = {}
    for b in _boards():
        with M.mirror_board(_board_id(b), SCOPE):
            out[b] = V.board_titles(b)
    try:
        from crawlers.portals import _close_browser
        _close_browser()
    except Exception:
        pass
    return out


@pytest.fixture(params=[_case(b) for b in _boards()])
def board(request):
    return request.param


def test_every_registered_pi_loga_board_still_lists_titles(titles_per_board, board):
    assert titles_per_board[board], (f"board_titles() read nothing off {board} -- either the GWT list markup "
                                     f"changed or the render stopped settling. Every posting on that board is "
                                     f"now unverifiable (verify_status 'error', method 'board_list').")


def test_the_rung_discriminates_listed_from_unlisted(titles_per_board, board):
    """The fall-through bug this rung exists to prevent answered 'live' to everything, because the
    board list contains every title. A working rung must still say gone to a title that is not on
    the board."""
    import requests
    s = requests.Session()
    listed = sorted(titles_per_board[board])[0]
    with M.mirror_board(_board_id(board), SCOPE):
        live = V.verify_one(s, board + "#title=x", listed, rungs=("render",))
        assert (live["verify_status"], live["method"]) == ("live", "board_list"), (board, listed, live)

        gone = V.verify_one(s, board + "#title=y", "Ballonpilot (m/w/d) für die Nachtschicht", rungs=("render",))
        assert (gone["verify_status"], gone["method"]) == ("gone", "board_list"), (board, gone)
