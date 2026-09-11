"""Custom dropdowns on the public board (web/index.html): town search (in the map header) and the role filter.

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
        pg.wait_for_selector(".mapbox .pick input.pk-t")
        yield pg
        browser.close()


def test_typing_filters_and_ignores_umlauts(page):
    page.fill(".mapbox .pick input.pk-t", "nurnberg")                         # no umlaut typed on purpose
    page.wait_for_selector(".mapbox .pick .dd .o")
    assert page.locator(".mapbox .pick .dd .o").all_inner_texts()[0].startswith("Nürnberg")


def test_keyboard_pick_selects_that_town(page):
    """The picker beside the map does what a dot does: narrow the list below, not navigate away."""
    page.fill(".mapbox .pick input.pk-t", "regens")
    page.wait_for_selector(".mapbox .pick .dd .o")
    page.press(".mapbox .pick input.pk-t", "ArrowDown")
    page.press(".mapbox .pick input.pk-t", "Enter")
    page.wait_for_selector(".mp .d.on")
    assert page.locator(".mp .d.on").get_attribute("aria-label").startswith("Regensburg")
    assert "Regensburg" in page.inner_text(".mapbox .ctl .sel")         # and shows up as a chosen chip
    # .sel moved under the hero controls (.mapbox .ctl) when the disclosure states landed; it used to sit
    # inside .filters, which on / now carries only the "with jobs" toggle.


def test_no_match_shows_empty_state_and_escape_closes(page):
    page.goto(page.url.split("#")[0])
    page.wait_for_selector(".mapbox .pick input.pk-t")
    page.fill(".mapbox .pick input.pk-t", "zzzz")
    page.wait_for_selector(".mapbox .pick .dd .o.empty")
    page.press(".mapbox .pick input.pk-t", "Escape")
    assert page.locator(".mapbox .pick .dd").is_hidden()


def test_role_filter_dropdown_replaces_native_select(page):
    """The jobs page filter must be our own listbox, not a native <select> (unstylable OS sheet on mobile)."""
    page.goto(page.url.split("#")[0] + "#/jobs")
    # Two pickers live in .filters since the town vocabulary was shared with the map: the role listbox
    # first, then the town combobox. Both are .pick, so every locator here names the first one.
    role = page.locator(".filters .pick").first
    role.locator(".pk-t").wait_for()
    assert page.locator(".filters select").count() == 0
    role.locator(".pk-t").click()
    role.locator(".dd .o").first.wait_for()
    role.locator(".pk-t").press("ArrowDown")
    role.locator(".pk-t").press("Enter")
    page.wait_for_function("location.hash.includes('role_class=') || document.querySelector('.filters .pick .v').textContent.length > 0")
    assert role.locator(".dd").is_hidden()
