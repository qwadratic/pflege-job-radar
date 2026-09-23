"""crawlers.portals' shared Playwright browser has ONE owner thread (TASK-78).

Real threads, no real Chromium: sync_playwright is faked, so what is under test is portals' own
ownership/locking, not Playwright. The live crash these pin is 'greenlet.error: Cannot switch to a
different thread' + a cascading TargetClosedError, raised when a second thread calls new_context()
on the browser _get_browser() handed it (reproduced 2026-09-20).
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import portals  # noqa: E402


class _FakeBrowser:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakePlaywright:
    def __init__(self, counter, delay):
        self.chromium = self
        self._counter = counter
        self._delay = delay
        self.stopped = False

    def start(self):
        return self

    def launch(self, **_kw):
        time.sleep(self._delay)          # widen the check-then-act window the race test needs
        self._counter.append(1)
        return _FakeBrowser()

    def stop(self):
        self.stopped = True


@pytest.fixture
def launches(monkeypatch):
    """Patches sync_playwright and yields the list of launches performed during the test."""
    counter = []
    delay = 0.05
    import playwright.sync_api as pwapi
    monkeypatch.setattr(pwapi, "sync_playwright", lambda: _FakePlaywright(counter, delay))
    monkeypatch.setattr(portals, "_browser", None)
    monkeypatch.setattr(portals, "_pw", None)
    monkeypatch.setattr(portals, "_owner", None, raising=False)   # absent pre-TASK-78, so mutation runs reach the asserts
    yield counter
    portals._browser = portals._pw = portals._owner = None


def _in_thread(fn):
    """Run fn() on a fresh thread; return (result, exception)."""
    box = {}

    def run():
        try:
            box["ok"] = fn()
        except BaseException as e:      # noqa: BLE001 - the exception IS the assertion target
            box["err"] = e

    t = threading.Thread(target=run)
    t.start()
    t.join(30)
    assert not t.is_alive(), "worker thread hung"
    return box.get("ok"), box.get("err")


def test_second_thread_is_refused_before_playwright_is_touched(launches):
    mine = portals._get_browser()
    got, err = _in_thread(portals._get_browser)

    assert got is None
    assert isinstance(err, RuntimeError), f"expected a loud refusal, got {err!r}"
    assert "another thread" in str(err)
    assert portals._browser is mine
    assert launches == [1], "the refused thread must not have launched a second browser"


def test_racing_the_first_call_launches_exactly_one_browser(launches):
    n = 4
    barrier = threading.Barrier(n)
    results = []

    def racer():
        barrier.wait(10)
        try:
            results.append(("browser", portals._get_browser()))
        except RuntimeError as e:
            results.append(("refused", e))

    threads = [threading.Thread(target=racer) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
        assert not t.is_alive(), "racer thread hung"

    assert launches == [1], f"{len(launches)} browsers launched, expected exactly 1"
    kinds = [k for k, _ in results]
    assert kinds.count("browser") == 1, kinds
    assert kinds.count("refused") == n - 1, kinds
    assert all(b is portals._browser for k, b in results if k == "browser")


def test_close_from_a_foreign_thread_refuses_instead_of_dropping_the_handle(launches):
    mine = portals._get_browser()
    _, err = _in_thread(portals._close_browser)

    assert isinstance(err, RuntimeError), f"expected a loud refusal, got {err!r}"
    # The silent version swallowed greenlet.error and nulled the globals while Chromium stayed alive.
    assert portals._browser is mine
    assert mine.closed is False


def test_owner_thread_keeps_one_browser_and_close_frees_ownership(launches):
    first = portals._get_browser()
    assert portals._get_browser() is first
    assert launches == [1]

    portals._close_browser()
    assert first.closed is True
    assert portals._browser is None

    second, err = _in_thread(portals._get_browser)
    assert err is None, f"ownership was not released on close: {err!r}"
    assert second is not None and second is not first
    assert launches == [1, 1]
