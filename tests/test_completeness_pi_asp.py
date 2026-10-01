"""Adapter-specific completeness regressions for pi_asp (TASK-39) -- the shared harness in
tests/test_adapter_completeness.py covers the five generic checks against live boards; this module
locks in fixes the live investigation found that the generic checks can't exercise offline:

  no cap        the old max_items=80/120 cap silently dropped postings past an arbitrary limit --
                confirmed live 2026-09-10 the regiomed wildcard board alone lists 90 titles, more
                than the old default. crawl() must return every listed row, whichever board it is.
  popup         TASK-184: on regiomed and wirkzvin a title click does not change the board's own URL, it
                window.open()s the position in a POPUP (".../bewerber-web?company=*-FIRMA-ID&...
                #position,id=<uuid>,popup=y"). crawl() used to watch only the main URL, concluded the click
                was "dead" after 3 rows, gave up on the rest and stored no text. It must read the popup:
                the position id (the stable ref, so two vacancies sharing a title stay two rows) and the
                ad above the application form. No stop-after-N guard: a click that opens nothing is a
                recorded failure, never a silent "dead board".
  this window   on Helios and BRK München the click navigates this window to the position and the list is
                restored afterwards. BRK's page is an ad above the application form (read like a popup's, its
                empty boxes between the ad blocks are no end of the ad); Helios's is the FORM only (the ad
                lives on the Helios site), so its seeds say "ad_on_page": false and its text is never stored.
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
SAMPLES = ROOT / "tests" / "fixtures" / "board_samples"


# --- _parse_pi_date -------------------------------------------------------------------------------

def test_parse_pi_date_zero_pads_month_and_day():
    assert pi_asp._parse_pi_date("2026/7/10") == "2026-07-10"
    assert pi_asp._parse_pi_date("2025/12/23") == "2025-12-23"


def test_parse_pi_date_none_when_absent_or_unparseable():
    assert pi_asp._parse_pi_date(None) is None
    assert pi_asp._parse_pi_date("") is None
    assert pi_asp._parse_pi_date("not a date") is None


# --- fake Playwright: enough of the API surface pi_asp.crawl() drives ------------------------------

# What the boards really show (live 2026-10-01). Ad text: the first lines of the regiomed OTA ad above its
# application form, contact line left out. Helios 1134's position page: all that AD_JS finds above its form.
REGIOMED_AD = (
    "Coburg geht in die Zukunft – und du kannst Teil davon sein!\n"
    "Mit der Neuintegration der Neurochirurgie und Gefäßchirurgie bauen wir unser OP-Portfolio gezielt weiter aus.\n"
    "Deine Vorteile:\n"
    "Strukturierte Einarbeitung und fachliche Entwicklung\n"
    "Eine attraktive tarifliche Vergütung nach Entgeltgruppe P8 TVöD-K mit zusätzlicher Altersvorsorge\n"
    "Deine Aufgaben:\n"
    "Assistenz bei operativen Eingriffen, konventionell wie auch roboterassistiert\n"
    "Dein Profil:\n"
    "Abgeschlossene Ausbildung als OTA oder Pflegefachkraft (m/w/d) mit OP-Erfahrung")
HELIOS_FORM_HEAD = (
    "\n\n\nBewerbung auf die Stellenausschreibung \"Facharzt Radiologie (m/w/d)\" 1134_000113 in München\n\n\n\n"
    "Hinweis: Mit einem * markierte Felder sind Pflichtfelder.")
# BRK München's position page: the ad of the real Pflegefachkräfte posting (contact line left out)
BRK_AD = (
    "Pflegefachkräfte (m/w/d) - Senioren- und Pflegeheim Römerschanz\n"
    "Für unser Senioren- und Pflegeheim Haus Römerschanz, eine Einrichtung mit 77 Plätzen in Grünwald, suchen wir ab sofort:\n"
    "Was bieten wir:\nFlexiblen Diensteinsatz: Früh-, Spät-, oder Nachtdienste möglich.\n"
    "Unterstützung bei der Wohnungssuche (Werksdienstwohnungen)\n"
    "Ihre Aufgaben:\nDigitale Dokumentation der Pflege\n"
    "Informationen zum Datenschutz: Datenschutzerklärung")


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


class _FakePopup:
    """The position page a popup board's title click opens. `ready` False = its form never renders."""
    def __init__(self, url, ad, ready=True):
        self.url, self.ad, self.ready, self.closed = url, ad, ready, False

    def wait_for_selector(self, selector, state=None, timeout=None):
        assert selector == pi_asp.FORM_WAIT, "a form control is the signal that the ad above it has rendered"
        if not self.ready:
            from playwright.sync_api import TimeoutError as PWTimeout
            raise PWTimeout("Timeout 15000ms exceeded.")

    def evaluate(self, script, arg=None):
        assert (script, arg) == (pi_asp.AD_JS, pi_asp.FORM_CONTROL)
        return self.ad

    def content(self):
        return "<html>position</html>"

    def close(self):
        self.closed = True


class _FakeExpectPage:
    """ctx.expect_page(): the popup the click inside the with-block opened, else Playwright's TimeoutError."""
    def __init__(self, ctx):
        self.ctx = ctx

    def __enter__(self):
        self.ctx.opened = None
        return self

    def __exit__(self, *exc):
        if exc[0] is None and self.ctx.opened is None:
            from playwright.sync_api import TimeoutError as PWTimeout
            raise PWTimeout("Timeout 3000ms exceeded while waiting for event \"page\"")
        return False

    @property
    def value(self):
        return self.ctx.opened


class _FakePage:
    """postings[i] = {title, dept, city, date, pid, navigates | popup, ad?, ready?}. `navigates` = clicking the row
    puts a #position,id= hash on this page's URL (Helios / BRK-shaped: the position page is shown in this window,
    `ad` is what AD_JS finds above its form, `ready` False = the form never renders); `popup` = the ad text a popup
    window shows (regiomed / wirkzvin-shaped); neither = the click opens nothing."""
    def __init__(self, base_url, postings):
        self.base_url, self.postings = base_url, postings
        self.url = base_url
        self.mouse = _FakeMouse()
        self.ctx = None
        self._clicked, self.popups, self.ad_reads = [], [], 0

    def goto(self, *a, **k): pass
    def wait_for_timeout(self, *a): pass
    def wait_for_load_state(self, *a, **k): pass
    def go_back(self): self.url = self.base_url

    def _shown(self):
        return next(p for p in self.postings if p.get("pid") and p["pid"] in self.url)

    def wait_for_selector(self, selector, state=None, timeout=None):
        assert selector == pi_asp.FORM_WAIT, "a form control is the signal that the ad above it has rendered"
        if not self._shown().get("ready", True):
            from playwright.sync_api import TimeoutError as PWTimeout
            raise PWTimeout("Timeout 15000ms exceeded.")

    def evaluate(self, script, arg=None):
        if arg == pi_asp.TITLE_LABEL:                    # _list_rows
            return [{"title": p["title"], "dept": p["dept"], "city": p["city"], "date": p["date"]}
                    for p in self.postings]
        if script == pi_asp.AD_JS:                       # the position page shown in this window
            assert arg == pi_asp.FORM_CONTROL
            self.ad_reads += 1
            return self._shown().get("ad")
        return None

    def locator(self, selector):
        assert selector == pi_asp.TITLE_LABEL, "click targets must be the rows _list_rows read"
        return _FakeRootLocator(self)

    def _click(self, i):
        self._clicked.append(i)
        p = self.postings[i]           # the URL stays what it was: only go_back() takes it off a form page
        if p.get("popup") is not None:
            popup = _FakePopup(f"https://x.test/bewerber-web?company=*-FIRMA-ID#position,id={p['pid']},popup=y",
                               p["popup"], ready=p.get("ready", True))
            self.popups.append(popup)
            self.ctx.opened = popup
        elif p.get("navigates"):
            self.url = f"{self.base_url}#position,id={p['pid']}"

    def content(self):
        return "<html></html>"


class _FakeContext:
    def __init__(self, page):
        self._page = page
        self.opened = None
        page.ctx = self

    def new_page(self):
        return self._page

    def expect_page(self, timeout=None):
        return _FakeExpectPage(self)


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


def _posting(i, navigates, dept="Pflegedienst", city="Coburg, Bayern, Deutschland", date=None, ad=None, ready=True):
    return {"title": f"Pflegefachkraft {i} (m/w/d)", "dept": dept, "city": city, "date": date, "ad": ad, "ready": ready,
            "navigates": navigates, "pid": f"{'a' * 20}-{i}" if navigates else None}


def _popup_posting(i, ad=REGIOMED_AD, title=None, ready=True, dept="Pflegedienst", city="Coburg, Bayern, Deutschland"):
    return {"title": title or f"Pflegefachkraft {i} (m/w/d)", "dept": dept, "city": city, "date": None,
            "popup": ad, "pid": f"{'b' * 20}-{i}", "ready": ready}


def _seed(**k):
    return {"name": "Test Board", "host": "x.test", "companyEid": 1, "default": {"kez": "K1", "town": "Coburg"}, **k}


def test_crawl_returns_every_listed_row_no_cap(monkeypatch):
    n = 90  # more than the old max_items=80/120 caps
    postings = [_posting(i, navigates=True) for i in range(n)]
    page, _save = _wire_fake_playwright(monkeypatch, postings)
    rows, stats = pi_asp.crawl(_seed(), set())
    assert len(rows) == n
    assert stats["listed"] == n
    assert stats["opened"] == n


# --- popup boards (regiomed, wirkzvin) ---------------------------------------------------------------

def test_crawl_reads_the_ad_and_the_position_id_from_the_popup_a_title_click_opens(monkeypatch):
    postings = [_popup_posting(i) for i in range(3)]
    page, fake_save = _wire_fake_playwright(monkeypatch, postings)
    rows, stats = pi_asp.crawl(_seed(), set())
    assert stats == {"listed": 3, "opened": 3, "pflege": 3}
    assert [r["source_ref"] for r in rows] == [f"https://x.test/bewerber-web/?companyEid=1#position,id={'b' * 20}-{i}" for i in range(3)]
    assert [r["external_url"] for r in rows] == [r["source_ref"] for r in rows]
    assert all(r["description"] == REGIOMED_AD for r in rows)
    assert all(r["details_fetched_at"] for r in rows)
    assert [json.loads(r["payload"])["pi"]["position_id"] for r in rows] == [f"{'b' * 20}-{i}" for i in range(3)]
    assert all(p.closed for p in page.popups)        # one popup window at a time, closed after its read
    # one snapshot of the list, one per popup
    assert [c[1] for c in fake_save.calls[1:]] == [r["source_ref"] for r in rows]


def test_two_vacancies_sharing_one_title_stay_two_rows(monkeypatch):
    # live 2026-10-01 at Coburg: two vacancies "Pflegefachkraft (m/w/d) für die Station 42 Chirurgische
    # Intensivstation" -- the old '#title=<slug>' ref merged them into one stored posting
    title = "Pflegefachkraft (m/w/d) für die Station 42 Chirurgische Intensivstation"
    postings = [_popup_posting(1, title=title), _popup_posting(2, title=title)]
    _wire_fake_playwright(monkeypatch, postings)
    rows, _stats = pi_asp.crawl(_seed(), set())
    assert len(rows) == 2
    assert len({r["source_ref"] for r in rows}) == 2
    assert {r["title"] for r in rows} == {title}


def test_a_popup_with_no_ad_text_is_a_row_without_a_description(monkeypatch):
    _wire_fake_playwright(monkeypatch, [_popup_posting(0, ad="")])
    rows, stats = pi_asp.crawl(_seed(), set())
    assert [r["description"] for r in rows] == [None]
    assert [r["details_fetched_at"] for r in rows] == [None]
    assert "error" not in stats


@pytest.mark.parametrize("seed_extra", [{}, {"ad_on_page": False}])
def test_a_board_whose_click_opens_nothing_is_tried_row_by_row_and_reported(monkeypatch, seed_extra):
    # No "3 dead clicks, the rest is skipped" guard: every row is clicked, and since a row without a
    # position id has no stable ref, none is stored -- the failure is the board's recorded error
    n = 8
    postings = [_posting(i, navigates=False) for i in range(n)]
    page, _save = _wire_fake_playwright(monkeypatch, postings)
    rows, stats = pi_asp.crawl(_seed(**seed_extra), set())
    assert page._clicked == list(range(n))
    assert rows == []
    assert stats["listed"] == n and stats["opened"] == 0
    assert "8 of 8" in stats["error"]
    assert "dead_click" not in stats


def test_one_failed_row_is_reported_and_costs_the_other_rows_nothing(monkeypatch):
    postings = [_popup_posting(0), _posting(1, navigates=False), _popup_posting(2)]
    page, _save = _wire_fake_playwright(monkeypatch, postings)
    rows, stats = pi_asp.crawl(_seed(), set())
    assert [r["title"] for r in rows] == ["Pflegefachkraft 0 (m/w/d)", "Pflegefachkraft 2 (m/w/d)"]
    assert stats["opened"] == 2
    assert "1 of 3" in stats["error"] and "Pflegefachkraft 1 (m/w/d)" in stats["error"]


def test_a_popup_whose_form_never_renders_is_not_read_half_way(monkeypatch):
    # live 2026-10-01: a position page shows only its header line (79 chars) for the first seconds. Reading it
    # then stored that line as the description of 4 Helios rows. The ad is read only once the form below it is there.
    page, _save = _wire_fake_playwright(monkeypatch, [_popup_posting(0, ready=False)])
    rows, stats = pi_asp.crawl(_seed(), set())
    assert rows == []
    assert "1 of 1" in stats["error"]
    assert all(p.closed for p in page.popups)


# --- Helios and BRK München: the click shows the position in this window ------------------------------

def test_the_application_form_a_helios_click_opens_is_not_stored_as_a_description(monkeypatch):
    # live 2026-10-01: Helios's position page is the application form only; the ad is on Helios's own site. Its
    # seeds say so, and no text is read from the page (what AD_JS would find there is the form's title and hint)
    postings = [_posting(i, navigates=True, ad=HELIOS_FORM_HEAD) for i in range(3)]
    page, fake_save = _wire_fake_playwright(monkeypatch, postings)
    rows, stats = pi_asp.crawl(_seed(ad_on_page=False), set())
    assert page.ad_reads == 0
    assert [r["description"] for r in rows] == [None, None, None]
    assert [r["details_fetched_at"] for r in rows] == [None, None, None]
    assert [r["source_ref"] for r in rows] == [f"https://x.test/bewerber-web/?companyEid=1#position,id={'a' * 20}-{i}" for i in range(3)]
    assert stats == {"listed": 3, "opened": 3, "pflege": 3}
    assert len(fake_save.calls) == 4                              # list + the form page of each row


def test_a_position_page_shown_in_this_window_carries_its_ad_like_a_popup_does(monkeypatch):
    # live 2026-10-01: BRK München's click navigates this window, and its position page is ad then form. Reading
    # nothing from it (as for Helios) dropped the 2.6-4.2 kB ads of its stored postings; a seed without
    # "ad_on_page" reads the ad
    postings = [_posting(i, navigates=True, ad=BRK_AD) for i in range(3)]
    page, fake_save = _wire_fake_playwright(monkeypatch, postings)
    rows, stats = pi_asp.crawl(_seed(), set())
    assert [r["description"] for r in rows] == [BRK_AD] * 3
    assert all(r["details_fetched_at"] for r in rows)
    assert [r["source_ref"] for r in rows] == [f"https://x.test/bewerber-web/?companyEid=1#position,id={'a' * 20}-{i}" for i in range(3)]
    assert page.ad_reads == 3
    assert page.url == page.base_url                              # the list is restored after each position page
    assert stats == {"listed": 3, "opened": 3, "pflege": 3}


def test_a_position_page_in_this_window_whose_form_never_renders_is_not_read_half_way(monkeypatch):
    page, _save = _wire_fake_playwright(monkeypatch, [_posting(0, navigates=True, ad=BRK_AD[:60], ready=False), _posting(1, navigates=True, ad=BRK_AD)])
    rows, stats = pi_asp.crawl(_seed(), set())
    assert [r["title"] for r in rows] == ["Pflegefachkraft 1 (m/w/d)"]
    assert page.ad_reads == 1                                     # the half-rendered page was never read
    assert "1 of 2" in stats["error"] and "Pflegefachkraft 0 (m/w/d)" in stats["error"]
    assert page.url == page.base_url


def test_a_click_that_opens_nothing_after_a_form_page_does_not_inherit_its_position_id(monkeypatch):
    # the list is restored (history back) after each form page; without that the next row, whose click opens
    # nothing, would read the previous row's '#position,id=' off this window's URL and be stored under its ref
    postings = [_posting(0, navigates=True), _posting(1, navigates=False)]
    _wire_fake_playwright(monkeypatch, postings)
    rows, stats = pi_asp.crawl(_seed(), set())
    assert [r["title"] for r in rows] == ["Pflegefachkraft 0 (m/w/d)"]
    assert "1 of 2" in stats["error"] and "Pflegefachkraft 1 (m/w/d)" in stats["error"]


def test_crawl_reads_department_and_date_straight_from_the_list_no_click_needed(monkeypatch):
    postings = [dict(_popup_posting(0, dept="Aerztlicher Dienst", city="Lichtenfels, Bayern, Deutschland"), date="2026/7/10")]
    page, _save = _wire_fake_playwright(monkeypatch, postings)
    rows, _stats = pi_asp.crawl(_seed(), set())
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


# --- the ad a position page shows, read off the real DOM (live 2026-10-01) --------------------------
# The fixtures are the position screen of one regiomed popup, one wirkzvin popup and one BRK München page
# (shown in the main window) as rendered by headless Chromium: share bar, buttons, the ad's rich-text blocks,
# the form's lead-in, then the first rows of the application form. Contact lines are redacted.

@pytest.fixture(scope="module")
def chromium_browser():
    pw_api = pytest.importorskip("playwright.sync_api")
    with pw_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as exc:                                  # no browser binary in this env
            pytest.skip(f"chromium unavailable: {exc}")
        yield b
        b.close()


def _ad_of(browser, sample):
    pg = browser.new_page()
    try:
        pg.set_content((SAMPLES / sample).read_text(encoding="utf-8"))
        return pg.evaluate(pi_asp.AD_JS, pi_asp.FORM_CONTROL)
    finally:
        pg.close()


FORM_LABELS = ("Anrede", "Titel / akad. Grad", "Vorname", "Bewerbungsquelle")


def test_the_ad_is_what_a_regiomed_popup_shows_above_its_application_form(chromium_browser):
    ad = pi_asp._strip(_ad_of(chromium_browser, "pi_asp_regiomed_position_popup_sample.html"))
    assert ad.startswith("Coburg geht in die Zukunft – und du kannst Teil davon sein!")
    for line in ("Deine Vorteile:", "Entlastung durch Versorgungsassistenten",
                 "Eine attraktive tarifliche Vergütung nach Entgeltgruppe P8 TVöD-K mit zusätzlicher Altersvorsorge",
                 "Deine Aufgaben:", "Dein Profil:", "Arbeitgeber Sana Kliniken Oberfranken Coburg GmbH",
                 "Standort Coburg, Ketschendorfer Straße 33"):
        assert line in ad
    assert not any(label in ad for label in FORM_LABELS)
    assert "HERUNTERLADEN" not in ad and "Sie haben bereits ein Profil" not in ad     # the page chrome above the ad


def test_the_ad_is_what_a_wirkzvin_popup_shows_above_its_application_form(chromium_browser):
    ad = pi_asp._strip(_ad_of(chromium_browser, "pi_asp_wirkzvin_position_popup_sample.html"))
    assert ad.startswith("OPERATIONSTECHNISCHER ASSISTENT, GESUNDHEITS- UND KRANKENPFLEGER (M/W/D)")
    for line in ("DAS ERWARTET SIE", "Einsatz in unserem modernen Operationstrakt", "DAS ÜBERZEUGT UNS",
                 "DAS BIETEN WIR", "Vergütung und Urlaub nach TVöD, Zusatzversorgung der bayerischen Gemeindekassen"):
        assert line in ad
    assert not any(label in ad for label in FORM_LABELS)
    assert "WEITERLEITEN" not in ad


def test_the_ad_is_what_a_brk_position_page_shows_between_its_empty_boxes_and_its_form(chromium_browser):
    # BRK München puts an empty .LG-BoxPanel in front of its ad blocks and between them: "the text above the first
    # box" is nothing at all there (0 chars), the ad ends where the first form control starts
    ad = pi_asp._strip(_ad_of(chromium_browser, "pi_asp_brkm_position_sample.html"))
    assert ad.startswith("Pflegefachkräfte (m/w/d) - Senioren- und Pflegeheim Römerschanz")
    for line in ("Was bieten wir:", "Unterstützung bei der Wohnungssuche (Werksdienstwohnungen)", "Ihre Aufgaben:",
                 "Was wir uns wünschen:", "Informationen zum Datenschutz: Datenschutzerklärung"):
        assert line in ad
    assert not any(label in ad for label in FORM_LABELS)
    assert "HERUNTERLADEN" not in ad


@pytest.mark.parametrize("sample", ["pi_asp_regiomed_position_popup_sample.html", "pi_asp_wirkzvin_position_popup_sample.html",
                                    "pi_asp_brkm_position_sample.html"])
def test_the_wait_for_the_form_finds_the_form_of_each_real_position_page(chromium_browser, sample):
    pg = chromium_browser.new_page()
    try:
        pg.set_content((SAMPLES / sample).read_text(encoding="utf-8"))
        assert pg.locator(pi_asp.FORM_WAIT).count() > 0
    finally:
        pg.close()


def test_the_helios_seeds_say_their_position_page_carries_no_ad_and_no_other_seed_does():
    seeds = json.loads((ROOT / "data" / "registry" / "pi_seeds.json").read_text(encoding="utf-8"))
    flagged = {(s["host"], s["companyEid"]) for s in seeds if s.get("ad_on_page") is False}
    assert flagged == {("helios-gesundheit.pi-asp.de", 1134), ("helios-gesundheit.pi-asp.de", 1135),
                       ("helios-gesundheit.pi-asp.de", 1130)}
    assert all("ad_on_page" not in s for s in seeds if (s["host"], s["companyEid"]) not in flagged)


# --- TASK-178: Klinikum Ingolstadt, wirkzvin.pi-asp.de/bewerber-web/?companyEid=* --------------------
# The fixture is that board's own rendered list, captured live 2026-09-29: 65 postings, one
# div.B3-Web-Responsive-Row each. The board prints no count of its own, so its list is the oracle.

FIXTURE = SAMPLES / "pi_asp_klinikum_ingolstadt_list_sample.html"
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
def ingolstadt_page(chromium_browser):
    pg = chromium_browser.new_page()
    pg.set_content(FIXTURE.read_text(encoding="utf-8"))
    yield pg
    pg.close()


@pytest.fixture(scope="module")
def ingolstadt_rows(ingolstadt_page):
    return pi_asp._list_rows(ingolstadt_page)


def _ingolstadt_crawl(monkeypatch, rows):
    seed = next(s for s in json.loads((ROOT / "data" / "registry" / "pi_seeds.json").read_text(encoding="utf-8"))
                if s["host"] == "wirkzvin.pi-asp.de")
    # wirkzvin is a popup board: every row opens its position page
    _page, fake_save = _wire_fake_playwright(monkeypatch, [dict(r, popup="", pid=f"{'c' * 20}-{i}") for i, r in enumerate(rows)])
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
    # TASK-186: the ad text of a row that opened (real excerpt of a live posting) reaches classify_role.
    postings = [_posting(0, navigates=True, ad="Ihre Voraussetzungen für die Ausbildung Sie haben einen Hauptschulabschluss (oder gleichwertig)")]
    _page, _save = _wire_fake_playwright(monkeypatch, postings)
    seed = {"name": "Board", "host": "x.test", "companyEid": 1, "default": {"kez": "K5", "town": "Coburg"}}
    rows, _stats = pi_asp.crawl(seed, set())
    assert rows[0]["role_class"] == "ausbildung"
