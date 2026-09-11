"""Clawl dashboard (web/pro.html): tab layout, scope toggle, run-log viewer.

Runs the built page against its own offline mock (`?mock=1`); no API, no network, no credentials.
"""
import http.server, functools, threading, pathlib, pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


@pytest.fixture(scope="module")
def page():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/pro.html?mock=1&lang=en"
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:                                  # no browser binary in this env
            pytest.skip(f"chromium unavailable: {exc}")
        pg = browser.new_page(viewport={"width": 1440, "height": 1200})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(base + "#/clawl")
        pg.wait_for_selector(".subnav .tb")
        pg.wait_for_timeout(1500)
        assert not errors, errors[:2]
        pg.base = base
        yield pg
        browser.close()
    srv.shutdown()


def test_no_native_selects_left_on_the_dashboard(page):
    assert page.locator("select").count() == 0


def test_tabs_show_one_section_at_a_time(page):
    page.click('.subnav .tb:has-text("Run")')
    page.wait_for_selector("text=New clawl")                       # the run form
    assert page.locator("text=Coverage by adapter").count() >= 1   # right-rail summary
    page.click('.subnav .tb:has-text("Automation")')
    page.wait_for_timeout(400)
    assert page.locator("text=New clawl").count() == 0             # run form is gone
    assert page.locator("text=Hunter").count() >= 1


def test_scope_toggle_can_be_cleared(page):
    page.click('.subnav .tb:has-text("Run")')
    page.wait_for_selector('.seg .sg[data-v="city"]')
    page.click('.seg .sg[data-v="city"]')
    assert page.get_attribute('.seg .sg[data-v="city"]', "aria-pressed") == "true"
    page.click('.seg .sg[data-v="city"]')                          # a radio could never do this
    assert page.get_attribute('.seg .sg[data-v="city"]', "aria-pressed") == "false"
    assert page.get_attribute('.seg .sg[data-v="all"]', "aria-pressed") == "true"


def test_run_log_viewer(page):
    page.click('.subnav .tb:has-text("History")')
    page.wait_for_selector("table.cards tbody tr")
    page.click('table.cards tbody tr:has-text("clinic")')           # the finished adapter run in the mock
    page.wait_for_selector("ol.log .ln")
    total = page.locator("ol.log .ln").count()
    assert total > 1
    assert page.locator(".prev:has-text('Mode')").count() == 1     # run parameters above the log
    assert page.inner_text("ol.log .ln:first-child .no") == "1"    # line numbers
    assert page.inner_text("ol.log .ln:nth-child(2) .ts").startswith("+")  # elapsed since start
    assert page.locator("ol.log .warn").count() >= 1               # severity colouring
    ell = page.locator("ol.log .ell").first                        # mid-truncated long line
    assert ell.count() == 1 and len(ell.get_attribute("title")) > 220
    ell.click()
    assert page.locator("ol.log .ell").count() == 0                # click expands it in place
    page.fill(".lg-q", "verify")
    page.wait_for_timeout(200)
    assert page.locator("ol.log .ln").count() == 1
    page.fill(".lg-q", "")
    page.keyboard.press("Escape")                                   # leave no dialog open for the next test
    page.wait_for_timeout(200)


def test_jobs_badge_is_compact_not_999_plus(page):
    assert page.evaluate("compact(1240)") == "1.2k"
    assert page.evaluate("compact(23400)") == "23k"
    assert page.evaluate("compact(940)") == "940"


def test_prompts_tab_shows_both_templates_with_schemas(page):
    page.click('.subnav .tb:has-text("Prompts")')
    page.wait_for_selector("pre.prompt")
    assert page.locator("pre.prompt").count() == 2                 # jobs + career
    assert page.locator("details.sch").count() == 2                # answer schema per prompt
    assert "GOAL:" in page.inner_text("pre.prompt")


def test_estimate_needs_a_hospital_target(page):
    page.click('.subnav .tb:has-text("Run")')
    page.wait_for_selector(".seg .sg")
    est = page.locator('button:has-text("Estimate")').first
    assert est.is_disabled()                                       # scope=all: estimate is per clinic
    page.click('.seg .sg[data-v="clinic"]')
    page.click(".ms .box input")
    page.locator(".ms .dd label input").first.click()
    assert not est.is_disabled()
    est.click()
    page.wait_for_selector(".estb tbody tr")
    assert page.locator(".estb tbody tr").count() == 1
    page.click('.seg .sg[data-v="all"]')                           # leave the form as we found it


def test_failed_run_is_triaged_into_causes(page):
    page.click('.subnav .tb:has-text("Run")')
    page.wait_for_selector("text=Last failed run")
    card = page.locator(".card:has-text('Last failed run')")
    assert card.locator("tbody tr").count() >= 2                   # cap + gate + zero-rows in the mock log
    assert card.locator("button:has-text('re-run at')").count() == 1


def test_running_run_has_a_live_panel(page):
    page.wait_for_selector("text=Running now")
    card = page.locator(".card:has-text('Running now')")
    assert card.locator(".prev b").count() >= 4                    # elapsed, rows, new, credits


def test_cancel_is_two_click_and_hits_the_cancel_endpoint(page):
    """Cancel asks once, then POSTs /api/crawl/runs/{id}/cancel (mock flips the run to cancelled)."""
    page.click('.subnav .tb:has-text("Run")')
    page.wait_for_selector("text=Running now")
    btn = page.locator('.card:has-text("Running now") button.danger')
    btn.click()                                                    # first click only arms it
    assert page.locator("text=Running now").count() == 1
    btn.click()
    page.wait_for_timeout(400)
    # cancel is cooperative: accepted at once, the run keeps going until the board in flight finishes,
    # so the panel must say so instead of looking like the click did nothing
    assert page.locator("text=cancel requested").count() == 1
    assert page.locator('.card:has-text("Running now") button:has-text("stopping")').is_disabled()
    page.wait_for_timeout(6000)                                    # runs list polls every 5 s
    assert page.locator("text=Running now").count() == 0           # panel is gone once nothing runs
    assert "cancelled" in page.inner_text(".tbl")                   # the runs table, not the triage one


def test_clawl_does_not_overflow_a_phone_viewport(page):
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(300)
    assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    page.set_viewport_size({"width": 1440, "height": 1200})


def test_plan_opens_as_a_drawer_not_a_modal(page):
    page.click('.subnav .tb:has-text("Run")')
    page.wait_for_selector('button:has-text("plan")')
    page.locator('button:has-text("plan")').first.click()
    page.wait_for_timeout(500)
    assert page.evaluate("document.querySelector('#plan-dlg').open")
    body = page.inner_text("#plan-dlg-body")
    assert "null" not in body.lower()                              # replaceChildren renders a null child as text
    assert page.locator('.subnav .tb:has-text("Coverage")').is_enabled()   # page stays interactive behind it
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    assert not page.evaluate("document.querySelector('#plan-dlg').open")
