"""The named gaps of the completeness cases (tests/conftest.py pytest_collection_modifyitems, tests.adapter_harness.recorded_gap).

A check that was already red when its board was recorded is an explicit xfail carrying that reason; the same check going red on a board
where it was green is a regression and fails; a board with no recorded verdict gets no mark. Run in a tiny inner pytest session, like
tests/test_network_guard.py, against an index written to a temp MIRROR_ROOT.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from tests import adapter_harness as H
from tests import mirror as M

ROOT = Path(__file__).resolve().parent.parent
INNER = '''
import pytest

@pytest.fixture(scope="module", params=[{"board_id": "fake__red"}, {"board_id": "fake__green"}, {"board_id": "fake__unrecorded"}],
                ids=["red", "green", "unrecorded"])
def board(request):
    return request.param

def test_round_trip(board):
    assert False, "round trip is red on every board here"

def test_read_path_coverage(board):
    pass    # green now: where it was a recorded gap that is an XPASS (a fix landed), never a failure

def test_public_url(board):
    assert False, "public url is red on every board here"

def test_something_else(board):
    assert False, "not a check: never marked"
'''


def _index(boards):
    return {"format": M.FORMAT, "boards": {bid: {"board_id": bid, "recorded_at": "2026-10-01T10:00:00+00:00", "checks": checks}
                                           for bid, checks in boards.items()}}


def test_a_check_red_at_recording_is_an_explicit_xfail_and_nothing_else_is(tmp_path):
    root = tmp_path / "mirror"
    root.mkdir()
    (root / "INDEX.json").write_text(json.dumps(_index({"fake__red": {"round_trip": False, "public_url": True, "read_path_coverage": False},
                                                        "fake__green": {"round_trip": True, "public_url": True}})))
    (tmp_path / "conftest.py").write_text((ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "test_inner.py").write_text(INNER, encoding="utf-8")
    env = {**os.environ, "MIRROR_ROOT": str(root), "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rxf", "-p", "no:cacheprovider", "--color=no", "--rootdir", str(tmp_path)],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert "XFAIL test_inner.py::test_round_trip[red]" in out and "named gap: round_trip was already red when fake__red was recorded (2026-10-01)" in out
    assert "FAILED test_inner.py::test_public_url[red]" in out          # that check was green when recorded: red now is a regression
    assert "FAILED test_inner.py::test_round_trip[green]" in out and "FAILED test_inner.py::test_round_trip[unrecorded]" in out
    assert "FAILED test_inner.py::test_something_else[red]" in out       # only the five checks of the completeness module carry gaps
    assert "8 failed, 2 passed, 1 xfailed, 1 xpassed" in out and r.returncode == 1


def test_recorded_gap_names_the_check_and_the_board(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRROR_ROOT", str(tmp_path))
    (tmp_path / "INDEX.json").write_text(json.dumps(_index({"b1": {"public_url": False, "round_trip": True}, "b2": None})))
    assert "public_url was already red when b1 was recorded (2026-10-01)" in H.recorded_gap("test_public_url", "b1")
    assert H.recorded_gap("test_round_trip", "b1") is None            # green when recorded
    assert H.recorded_gap("test_public_url", "b2") is None            # no verdict stored (the recording itself failed)
    idx = json.loads((tmp_path / "INDEX.json").read_text())
    idx["boards"]["b1"].update(replay_identical=False, replay_diff="rows only in the live run 2, only in the replay 0")
    (tmp_path / "INDEX.json").write_text(json.dumps(idx))
    for t in ("test_round_trip", "test_public_url"):                   # a recording that does not replay as recorded: every check of it is a named gap
        assert "does not replay as it was recorded (rows only in the live run 2" in H.recorded_gap(t, "b1")
    assert H.recorded_gap("test_public_url", "never-recorded") is None
    assert H.recorded_gap("test_mutation", "b1") is None              # not a check
