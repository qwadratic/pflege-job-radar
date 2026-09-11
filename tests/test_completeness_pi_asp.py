"""Adapter-specific completeness regressions for pi_asp (TASK-39) -- the shared harness in
tests/test_adapter_completeness.py covers the five generic checks against live boards; this module
locks in fixes the live investigation found that the generic checks can't exercise offline:

  no cap        the old max_items=80/120 cap silently dropped postings past an arbitrary limit --
                confirmed live 2026-09-10 the regiomed wildcard board alone lists 90 titles, more
                than the old default. crawl() must return every listed row, whichever board it is.
  dead click    the regiomed board's title click does NOTHING (verified live: zero requests, zero
                DOM change, zero URL change) -- the old code still stored whatever was already on
                screen (the *whole list*) as `body`, so every single row got the same giant
                "description". This must only treat `body` as real once the click actually produced
                a `#position,id=` hash, and must still return every row (no row dropped) once the
                click is proven dead.
  list fields   department_raw and first_published must come straight from the list's own DOM (dept
                line / calendar-icon date), not stay hardcoded None -- the source exposes them for
                every row without any click at all.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs.sources import pi_asp  # noqa: E402

GM = pi_asp.GM


# --- _parse_pi_date -------------------------------------------------------------------------------

def test_parse_pi_date_zero_pads_month_and_day():
    assert pi_asp._parse_pi_date("2026/7/10") == "2026-07-10"
    assert pi_asp._parse_pi_date("2025/12/23") == "2025-12-23"


def test_parse_pi_date_none_when_absent_or_unparseable():
    assert pi_asp._parse_pi_date(None) is None
    assert pi_asp._parse_pi_date("") is None
    assert pi_asp._parse_pi_date("not a date") is None


# --- fake Playwright: enough of the API surface pi_asp.crawl() drives ------------------------------

class _FakeSave:
    """Records tests.adapter_contract.save() calls without touching disk."""
    def __init__(self):
        self.calls = []

    def __call__(self, board_url, url, body, status=200, content_type="text/html"):
        self.calls.append((board_url, url, content_type))


class _FakeItemLocator:
    def __init__(self, page, index):
        self.page, self.index = page, index

    def evaluate(self, script):
        pass  # scrollIntoView -- no real layout to move

    def click(self, timeout=None):
        self.page._click(self.index)


class _FakeFilteredLocator:
    def __init__(self, page):
        self.page = page

    def nth(self, i):
        return _FakeItemLocator(self.page, i)


class _FakeRootLocator:
    def __init__(self, page):
        self.page = page

    def filter(self, has_text=None):
        return _FakeFilteredLocator(self.page)


class _FakeMouse:
    def wheel(self, *a): pass


class _FakePage:
    """postings[i] = {title, dept, city, date, navigates}. `navigates` decides whether clicking
    that row produces a #position,id= hash (Helios-shaped) or does nothing (regiomed-shaped)."""
    def __init__(self, base_url, postings):
        self.base_url, self.postings = base_url, postings
        self.url = base_url
        self.mouse = _FakeMouse()
        self._clicked = []

    def goto(self, *a, **k): pass
    def wait_for_timeout(self, *a): pass
    def wait_for_load_state(self, *a, **k): pass
    def go_back(self): self.url = self.base_url

    def evaluate(self, script):
        if "querySelectorAll('.LG-Label" in script:
            return [{"title": p["title"], "dept": p["dept"], "city": p["city"], "date": p["date"]}
                    for p in self.postings]
        return None

    def locator(self, selector):
        return _FakeRootLocator(self)

    def _click(self, i):
        self._clicked.append(i)
        p = self.postings[i]
        self.url = f"{self.base_url}#position,id={p['pid']}" if p.get("navigates") else self.base_url

    def inner_text(self, sel):
        return "Bewerbung auf die Stellenausschreibung" if "position,id=" in self.url else ""

    def content(self):
        return "<html></html>"


class _FakeContext:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


class _FakeBrowser:
    def __init__(self, page):
        self._page = page

    def new_context(self, **k):
        return _FakeContext(self._page)

    def close(self): pass


class _FakeChromium:
    def __init__(self, page):
        self._page = page

    def launch(self, **k):
        return _FakeBrowser(self._page)


class _FakePlaywrightCtx:
    def __init__(self, page):
        self.chromium = _FakeChromium(page)

    def __enter__(self): return self

    def __exit__(self, *a): return False


def _wire_fake_playwright(monkeypatch, postings, base_url="https://x.test/bewerber-web/?companyEid=1"):
    page = _FakePage(base_url, postings)
    fake_save = _FakeSave()
    import playwright.sync_api as pw_api
    monkeypatch.setattr(pw_api, "sync_playwright", lambda: _FakePlaywrightCtx(page))
    from tests import adapter_contract as AC
    monkeypatch.setattr(AC, "save", fake_save)
    return page, fake_save


def _posting(i, navigates, dept="Pflegedienst", city="Coburg, Bayern, Deutschland", date=None):
    return {"title": f"Pflegefachkraft {i} (m/w/d)", "dept": dept, "city": city, "date": date,
            "navigates": navigates, "pid": f"{'a' * 20}-{i}" if navigates else None}


def test_crawl_returns_every_listed_row_no_cap(monkeypatch):
    n = 90  # more than the old max_items=80/120 caps
    postings = [_posting(i, navigates=True) for i in range(n)]
    page, _save = _wire_fake_playwright(monkeypatch, postings)
    seed = {"name": "Test Board", "host": "x.test", "companyEid": 1, "default": {"kez": "K1", "town": "Coburg"}}
    rows, stats = pi_asp.crawl(seed, set())
    assert len(rows) == n
    assert stats["listed"] == n
    assert stats["opened"] == n


def test_crawl_on_a_dead_click_board_still_returns_every_row_with_no_description(monkeypatch):
    # regiomed-shaped: nothing ever navigates -- every row must still come back, just without a
    # description forged from whatever was on screen when the click did nothing (the old bug).
    n = 20
    postings = [_posting(i, navigates=False) for i in range(n)]
    page, _save = _wire_fake_playwright(monkeypatch, postings)
    seed = {"name": "Dead Click Board", "host": "x.test", "companyEid": 1, "default": {"kez": "K2", "town": "Coburg"}}
    rows, stats = pi_asp.crawl(seed, set())
    assert len(rows) == n
    assert stats["opened"] == 0
    assert stats["dead_click"] is True
    assert all(r["description"] is None for r in rows)
    # gives up after 3 tries, not one click attempt per every remaining row
    assert len(page._clicked) == 3


def test_crawl_reads_department_and_date_straight_from_the_list_no_click_needed(monkeypatch):
    postings = [_posting(0, navigates=False, dept="Aerztlicher Dienst",
                          city="Lichtenfels, Bayern, Deutschland", date="2026/7/10")]
    page, _save = _wire_fake_playwright(monkeypatch, postings)
    seed = {"name": "Board", "host": "x.test", "companyEid": 1, "default": {"kez": "K3", "town": "Coburg"}}
    rows, _stats = pi_asp.crawl(seed, set())
    assert rows[0]["department_raw"] == "Aerztlicher Dienst"
    assert rows[0]["first_published"] == "2026-07-10"
    assert rows[0]["city"] == "Lichtenfels"  # split off ", Bayern, Deutschland"


def test_crawl_snapshots_the_list_and_every_real_detail(monkeypatch):
    postings = [_posting(0, navigates=True), _posting(1, navigates=False)]
    page, fake_save = _wire_fake_playwright(monkeypatch, postings)
    seed = {"name": "Board", "host": "x.test", "companyEid": 1, "default": {"kez": "K4", "town": "Coburg"}}
    pi_asp.crawl(seed, set())
    # one list snapshot + one detail snapshot for the row that actually navigated
    assert len(fake_save.calls) == 2
    assert any(c[2] == "text/html" and "position,id=" not in c[1] for c in fake_save.calls)
    assert any("position,id=" in c[1] for c in fake_save.calls)
