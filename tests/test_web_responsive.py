"""Every route of both built pages, at a phone width and a desktop width: no horizontal overflow, no JS errors.

Horizontal overflow on a dashboard is almost always one grid column that forgot `min-width:0`, and it is
invisible until someone opens the page on a phone — cheap to catch here instead.
"""
import http.server, functools, threading, pathlib, pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
ROUTES = [("index.html", r) for r in ["#/", "#/jobs", "#/clinic/16100", "#/job/1"]] + \
         [("pro.html", r) for r in ["#/", "#/jobs", "#/clawl", "#/billing", "#/settings", "#/plan", "#/clinic/16100"]]


@pytest.fixture(scope="module")
def browser_and_base():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium unavailable: {exc}")
        yield browser, f"http://127.0.0.1:{srv.server_address[1]}"
        browser.close()
    srv.shutdown()


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("page_file,route", ROUTES)
def test_route_fits_and_runs_clean(browser_and_base, page_file, route, width):
    browser, base = browser_and_base
    errors = []
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)[:200]))
    page.goto(f"{base}/{page_file}?mock=1&lang=en{route}")
    page.wait_for_timeout(1500)
    overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    page.close()
    assert not errors, errors[:2]
    assert not overflow, f"{page_file}{route} overflows horizontally at {width}px"
