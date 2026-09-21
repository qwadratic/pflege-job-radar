"""Live drift guard for the one rung that has no fallback: the P&I LOGA board list.

These boards (data/registry/pi_seeds.json -- 3 Helios + the Regiomed/Sana wildcard, 91 open
postings) expose no per-posting page, so `pflege_jobs.verify.board_titles()` reading the rendered
GWT list IS the liveness check. Nothing else can answer, and the rungs below it would answer off
the board LIST -- which carries every title, removed ones included.

Deliberately not asserting markup: the selectors inside `_list_rows` will churn. What must hold is
behavioural and survives a redesign -- the rung still reads titles off every registered board, and
it still discriminates: a title the board lists reads live, one it does not reads gone. A rung that
has silently stopped working answers neither, or answers "live" to both.

Live by definition; deselected by `-m "not network"`.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs import verify as V  # noqa: E402

SEEDS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "registry", "pi_seeds.json")


def _boards():
    with open(SEEDS, encoding="utf-8") as f:
        seeds = json.load(f)
    return sorted({"https://%s/bewerber-web/?companyEid=%s" % (s["host"], s["companyEid"]) for s in seeds})


@pytest.fixture(scope="module")
def titles_per_board():
    V.reset_board_titles_cache()
    out = {b: V.board_titles(b) for b in _boards()}
    try:
        from crawlers.portals import _close_browser
        _close_browser()
    except Exception:
        pass
    return out


@pytest.mark.network
def test_every_registered_pi_loga_board_still_lists_titles(titles_per_board):
    empty = [b for b, t in titles_per_board.items() if not t]
    assert not empty, (f"board_titles() read nothing off {empty} -- either the GWT list markup "
                       f"changed or the render stopped settling. Every posting on those boards is "
                       f"now unverifiable (verify_status 'error', method 'board_list').")


@pytest.mark.network
def test_the_rung_discriminates_listed_from_unlisted(titles_per_board):
    """The fall-through bug this rung exists to prevent answered 'live' to everything, because the
    board list contains every title. A working rung must still say gone to a title that is not on
    the board."""
    import requests
    s = requests.Session()
    for board, titles in titles_per_board.items():
        listed = sorted(titles)[0]
        live = V.verify_one(s, board + "#title=x", listed, rungs=("render",))
        assert (live["verify_status"], live["method"]) == ("live", "board_list"), (board, listed, live)

        gone = V.verify_one(s, board + "#title=y", "Ballonpilot (m/w/d) für die Nachtschicht", rungs=("render",))
        assert (gone["verify_status"], gone["method"]) == ("gone", "board_list"), (board, gone)
