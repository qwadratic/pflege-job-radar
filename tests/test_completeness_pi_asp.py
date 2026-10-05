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
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs.sources import pi_asp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


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


class _FakeRootLocator:
    def __init__(self, page):
        self.page = page

    def nth(self, i):
        return _FakeItemLocator(self.page, i)


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

    def evaluate(self, script, arg=None):
        if arg == pi_asp.TITLE_LABEL:                    # _list_rows
            return [{"title": p["title"], "dept": p["dept"], "city": p["city"], "date": p["date"]}
                    for p in self.postings]
        return None

    def locator(self, selector):
        assert selector == pi_asp.TITLE_LABEL, "click targets must be the rows _list_rows read"
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


def test_crawl_uses_the_seeds_own_query_param_name(monkeypatch):
    # TASK-117: brkm.pi-asp.de (BRK Muenchen) renders 0 rows through "?companyEid=..." (every other
    # seed's param) -- live-verified 2026-09-23 it needs "?company=..." instead. seed["param"] is an
    # optional per-seed override; fake_save.calls[0][0] is the exact board url crawl() built and
    # passed to save(), so this pins the real query string without a live board.
    postings = [_posting(0, navigates=True)]
    page, fake_save = _wire_fake_playwright(monkeypatch, postings)
    seed = {"name": "BRK München", "host": "brkm.pi-asp.de", "companyEid": "123-FIRMA-ID", "param": "company",
            "default": {"kez": "16254", "town": "München"}}
    pi_asp.crawl(seed, set())
    assert fake_save.calls[0][0] == "https://brkm.pi-asp.de/bewerber-web/?company=123-FIRMA-ID"


def test_crawl_defaults_to_companyeid_when_no_param_override_is_given(monkeypatch):
    # The two pre-existing seeds (Helios, Regiomed) carry no "param" key at all -- must keep working
    # exactly as before.
    postings = [_posting(0, navigates=True)]
    page, fake_save = _wire_fake_playwright(monkeypatch, postings)
    seed = {"name": "Helios", "host": "helios-gesundheit.pi-asp.de", "companyEid": 1134, "default": {"kez": "16207", "town": "München"}}
    pi_asp.crawl(seed, set())
    assert fake_save.calls[0][0] == "https://helios-gesundheit.pi-asp.de/bewerber-web/?companyEid=1134"


def test_crawl_snapshots_the_list_and_every_real_detail(monkeypatch):
    postings = [_posting(0, navigates=True), _posting(1, navigates=False)]
    page, fake_save = _wire_fake_playwright(monkeypatch, postings)
    seed = {"name": "Board", "host": "x.test", "companyEid": 1, "default": {"kez": "K4", "town": "Coburg"}}
    pi_asp.crawl(seed, set())
    # one list snapshot + one detail snapshot for the row that actually navigated
    assert len(fake_save.calls) == 2
    assert any(c[2] == "text/html" and "position,id=" not in c[1] for c in fake_save.calls)
    assert any("position,id=" in c[1] for c in fake_save.calls)


# --- TASK-178: Klinikum Ingolstadt, wirkzvin.pi-asp.de/bewerber-web/?companyEid=* --------------------
# The fixture is that board's own rendered list, captured live 2026-09-29: 65 postings, one
# div.B3-Web-Responsive-Row each. The board prints no count of its own, so its list is the oracle.

FIXTURE = ROOT / "tests" / "fixtures" / "board_samples" / "pi_asp_klinikum_ingolstadt_list_sample.html"
GENDER = re.compile(r"\((?:m|w|d)/(?:m|w|d)/(?:m|w|d)\)", re.I)
HEIM = ["Pflegefachhelfer (m/w/d) Psychiatrischer Wohn- und Pflegebereich",
        "Pflegefachhelfer oder Heilerziehungspflegehelfer (m/w/d) für die psychiatrische Eingliederungshilfe"]
# Krankenhausplan 2026 rows as the registry holds them (16101 as parsed, TASK-178; ZPG 16107 and
# Eichstätt 17606 are kbo-Donau-Altmühl-Kliniken gGmbH's since 2026-01-01, their jobs are on kbo.de)
CLINICS = [{"clinic_id": "16101", "name": "Klinikum Ingolstadt", "town": "Ingolstadt", "operator": "Klinikum Ingolstadt GmbH", "beds": 798},
           {"clinic_id": "16107", "name": "Zentrum für psychische Gesundheit (ZPG) Ingolstadt", "town": "Ingolstadt",
            "operator": "kbo-Donau-Altmühl-Kliniken gGmbH", "beds": 275},
           {"clinic_id": "17606", "name": "Tagesklinik für Psychiatrie Eichstätt", "town": "Eichstätt",
            "operator": "kbo-Donau-Altmühl-Kliniken gGmbH", "beds": 0},
           {"clinic_id": "16104", "name": "kbo-Heckscher-Klinikum Ingolstadt", "town": "Ingolstadt",
            "operator": "kbo-Heckscher-Klinikum gGmbH", "beds": 0}]


@pytest.fixture(scope="module")
def ingolstadt_page():
    pw_api = pytest.importorskip("playwright.sync_api")
    with pw_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as exc:                                  # no browser binary in this env
            pytest.skip(f"chromium unavailable: {exc}")
        pg = b.new_page()
        pg.set_content(FIXTURE.read_text(encoding="utf-8"))
        yield pg
        b.close()


@pytest.fixture(scope="module")
def ingolstadt_rows(ingolstadt_page):
    return pi_asp._list_rows(ingolstadt_page)


def _ingolstadt_crawl(monkeypatch, rows):
    seed = next(s for s in json.loads((ROOT / "data" / "registry" / "pi_seeds.json").read_text(encoding="utf-8"))
                if s["host"] == "wirkzvin.pi-asp.de")
    _page, fake_save = _wire_fake_playwright(monkeypatch, [dict(r, navigates=False) for r in rows])
    out, stats = pi_asp.crawl(seed, set())
    return out, stats, fake_save


def test_list_rows_reads_every_posting_the_board_lists(ingolstadt_page, ingolstadt_rows):
    assert ingolstadt_page.locator("div.B3-Web-Responsive-Row").count() == 65
    assert len(ingolstadt_rows) == 65
    # a title without "(m/w/d)" is a posting too -- the old filter dropped these 38
    assert sum(not GENDER.search(r["title"]) for r in ingolstadt_rows) == 38
    assert {"title": "Flexpool", "dept": "Pflege- und Funktionsbereiche", "city": "Flexpool", "date": None} in ingolstadt_rows
    assert {"title": "Famulatur - Apotheke", "dept": "Medizin-Technischer Dienst", "city": "Klinikumsapotheke", "date": None} in ingolstadt_rows


def test_list_rows_and_click_targets_are_the_same_rows(ingolstadt_page, ingolstadt_rows):
    labels = ingolstadt_page.locator(pi_asp.TITLE_LABEL)
    assert [labels.nth(i).inner_text().strip() for i in range(labels.count())] == [r["title"] for r in ingolstadt_rows]


def test_crawl_ingolstadt_returns_every_row_with_the_unit_as_unit_not_as_town(monkeypatch, ingolstadt_rows):
    rows, stats, fake_save = _ingolstadt_crawl(monkeypatch, ingolstadt_rows)
    assert fake_save.calls[0][0] == "https://wirkzvin.pi-asp.de/bewerber-web/?companyEid=*"
    assert len(rows) == stats["listed"] == 65
    # the pin line names the org unit on this board ("Zentral OP PO40"), never a town
    assert {(r["city"], r["plz"]) for r in rows} == {("Ingolstadt", "85049")}
    units = [json.loads(r["payload"])["pi"]["unit"] for r in rows]
    assert units == [r["city"] for r in ingolstadt_rows]


def test_crawl_ingolstadt_files_the_nursing_home_under_no_site(monkeypatch, ingolstadt_rows):
    rows, _stats, _save = _ingolstadt_crawl(monkeypatch, ingolstadt_rows)
    heim = [r for r in rows if json.loads(r["payload"])["pi"]["unit"] == "Alten- und Pflegeheim"]
    assert sorted(r["title"] for r in heim) == HEIM
    assert all((r["employer_name"], r["_kez"], r["_board"]) == ("Alten- und Pflegeheim Klinikum Ingolstadt GmbH", None, [])
               for r in heim)
    hospital = [r for r in rows if r not in heim]
    assert len(hospital) == 63
    assert all((r["employer_name"], r["_kez"], "_board" in r) == ("Klinikum Ingolstadt", "16101", False) for r in hospital)


def test_ingolstadt_rows_link_to_16101_and_the_nursing_home_to_no_clinic(monkeypatch, ingolstadt_rows):
    from pflege_jobs.registry import Matcher
    rows, _stats, _save = _ingolstadt_crawl(monkeypatch, ingolstadt_rows)
    m = Matcher([dict(c) for c in CLINICS])
    got = {}
    for r in rows:   # app/crawl.py: an adapter's own pool, else the board's clinics
        mt = m.match(r["employer_name"], r["city"], board=r.get("_board", ["16101"]))
        got.setdefault(mt and mt[0], []).append(r["title"])
    assert {k: len(v) for k, v in got.items()} == {"16101": 63, None: 2}
    assert sorted(got[None]) == HEIM


def test_routing_sends_16101_and_only_16101_to_the_ingolstadt_board():
    from crawlers.routing import seed_overlays
    ov = seed_overlays()
    assert ov["16101"] == ("pi_asp", "https://wirkzvin.pi-asp.de/bewerber-web/?companyEid=*")
    assert "16107" not in ov and "17606" not in ov


def test_crawl_classifies_a_training_place_from_the_opened_detail_text(monkeypatch):
    # TASK-186: the detail text of a row that opened (real excerpt of a live posting) reaches classify_role.
    postings = [_posting(0, navigates=True)]
    page, _save = _wire_fake_playwright(monkeypatch, postings)
    page.inner_text = lambda sel: "Ihre Voraussetzungen für die Ausbildung Sie haben einen Hauptschulabschluss (oder gleichwertig)"
    seed = {"name": "Board", "host": "x.test", "companyEid": 1, "default": {"kez": "K5", "town": "Coburg"}}
    rows, _stats = pi_asp.crawl(seed, set())
    assert rows[0]["role_class"] == "ausbildung"
