"""Home-page town combobox (web/index.html): filter, keyboard, navigation.

Runs the built page against its own offline mock (`?mock=1`), so it needs no API and no network.
Skipped when Playwright or its Chromium build is not installed.
"""
import http.server, functools, threading, pathlib, pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


@pytest.fixture(scope="module")
def base_url():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def page(base_url):
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:                                  # no browser binary in this env
            pytest.skip(f"chromium unavailable: {exc}")
        pg = browser.new_page()
        pg.goto(f"{base_url}/index.html?mock=1&lang=en")
        pg.wait_for_selector(".cities .twn")
        yield pg
        browser.close()


def test_typing_filters_and_ignores_umlauts(page):
    page.fill(".cities .twn", "nurnberg")                         # no umlaut typed on purpose
    page.wait_for_selector(".twn-box .dd .o")
    assert page.locator(".twn-box .dd .o").all_inner_texts()[0].startswith("Nürnberg")


def test_keyboard_pick_navigates_to_that_town(page):
    page.fill(".cities .twn", "regens")
    page.wait_for_selector(".twn-box .dd .o")
    page.press(".cities .twn", "ArrowDown")
    page.press(".cities .twn", "Enter")
    page.wait_for_function("location.hash.includes('city=')")
    assert "Regensburg" in page.evaluate("decodeURIComponent(location.hash)")


def test_no_match_shows_empty_state_and_escape_closes(page):
    page.goto(page.url.split("#")[0])
    page.wait_for_selector(".cities .twn")
    page.fill(".cities .twn", "zzzz")
    page.wait_for_selector(".twn-box .dd .o.empty")
    page.press(".cities .twn", "Escape")
    assert page.locator(".twn-box .dd").is_hidden()
